from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QPieSeries, QValueAxis
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .db import Database, password_hash
from .importer import ImportError, find_conflicts, import_database
from .scoring import calculate


APP_NAME = "Цифровой рейтинг"


def data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DigitalRating"


def bundled_resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "resources" / name


def points(value) -> str:
    return f"{float(value or 0):.2f}".rstrip("0").rstrip(".")


def table_item(value, align=None):
    item = QTableWidgetItem("" if value is None else str(value))
    if align is not None:
        item.setTextAlignment(align)
    return item


class ServiceNumberEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("А-123456")
        self.setMaxLength(24)
        self.textEdited.connect(self.format_value)

    def format_value(self, value):
        raw = value.replace("-", "").replace(" ", "")
        letter, digits = "", ""
        for index, char in enumerate(raw):
            if char.isalpha():
                letter = char.upper()
                digits = "".join(item for item in raw[index + 1:] if item.isdigit())
                break
        formatted = f"{letter}-{digits}" if letter else ""
        if formatted != value:
            self.blockSignals(True)
            self.setText(formatted)
            self.setCursorPosition(len(formatted))
            self.blockSignals(False)

    def is_valid(self):
        value = self.text().strip()
        return not value or (len(value) >= 3 and value[0].isalpha() and value[1] == "-" and value[2:].isdigit())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Backspace and self.cursorPosition() <= 2 and self.text().endswith("-"):
            self.clear(); event.accept(); return
        super().keyPressEvent(event)


class SearchableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        completer = QCompleter(self.model(), self)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.setCompleter(completer)
        completer.activated[str].connect(self.select_completion)
        self.lineEdit().installEventFilter(self)

    def select_completion(self, text):
        for index in range(self.count()):
            if self.itemText(index).casefold() == text.casefold():
                self.setCurrentIndex(index)
                return

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.complete_first_match()
            self.focusNextPrevChild(event.key() == Qt.Key_Tab)
            event.accept(); return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.complete_first_match()
            self.focusNextPrevChild(event.key() == Qt.Key_Tab)
            return True
        return super().eventFilter(watched, event)

    def complete_first_match(self):
        typed=self.currentText().strip().casefold()
        if not typed:return
        for index in range(self.count()):
            if self.itemData(index) is not None and typed in self.itemText(index).casefold():
                self.setCurrentIndex(index)
                return


class CredentialsDialog(QDialog):
    def __init__(self, parent=None, setup=False, title=None):
        super().__init__(parent)
        self.setup = setup
        self.setWindowTitle(title or ("Первый запуск" if setup else "Вход"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        heading = QLabel("Создание администратора" if setup else APP_NAME)
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        if setup:
            text = QLabel("Создайте первую локальную учётную запись. Она получит все права администратора.")
            text.setWordWrap(True); text.setObjectName("muted"); layout.addWidget(text)
        form = QFormLayout()
        self.username = QLineEdit("admin" if setup else "")
        self.username.setPlaceholderText("Имя пользователя")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Не менее 6 символов")
        form.addRow("Пользователь", self.username); form.addRow("Пароль", self.password)
        self.confirm = None
        if setup:
            self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password)
            form.addRow("Повтор пароля", self.confirm)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Создать" if setup else "Войти")
        buttons.accepted.connect(self.validate); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        if not self.username.text().strip() or len(self.password.text()) < 6:
            QMessageBox.warning(self, APP_NAME, "Укажите имя и пароль не короче 6 символов."); return
        if self.confirm is not None and self.password.text() != self.confirm.text():
            QMessageBox.warning(self, APP_NAME, "Пароли не совпадают."); return
        self.accept()

    def values(self):
        return self.username.text().strip(), self.password.text()


class PersonDialog(QDialog):
    def __init__(self, connection, organizations, positions, departments, locations, record=None, parent=None):
        super().__init__(parent)
        self.connection = connection
        self.setWindowTitle("Карточка сотрудника")
        self.setMinimumWidth(600)
        self.positions_data = positions
        self.departments_data = departments
        self.locations = locations
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.organization = QComboBox()
        for org_id, name in organizations:
            self.organization.addItem(name, org_id)
        self.full_name = QLineEdit()
        self.birth_date = QLineEdit()
        self.birth_date.setInputMask("00.00.0000;_")
        self.birth_date.setPlaceholderText("ДД.ММ.ГГГГ")
        self.birth_country = SearchableComboBox()
        self.birth_region = SearchableComboBox()
        self.birth_city = SearchableComboBox()
        self.department = SearchableComboBox()
        self.position = SearchableComboBox()
        self.academic_education = QCheckBox("Имеется академическое образование")
        self.service_number = ServiceNumberEdit()
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(90)
        fields = (
            ("Организация *", self.organization), ("Ф.И.О. *", self.full_name),
            ("Дата рождения", self.birth_date), ("Страна рождения *", self.birth_country),
            ("Область рождения *", self.birth_region), ("Город рождения *", self.birth_city),
            ("Подразделение *", self.department), ("Должность *", self.position),
            ("Академическое образование", self.academic_education),
            ("Личный номер", self.service_number), ("Примечание", self.notes),
        )
        for label, widget in fields:
            form.addRow(label, widget)
        layout.addLayout(form)
        self.organization.currentIndexChanged.connect(self.load_positions)
        self.organization.currentIndexChanged.connect(self.load_departments)
        self.birth_country.currentIndexChanged.connect(self.load_regions)
        self.birth_region.currentIndexChanged.connect(self.load_cities)
        self.load_countries()
        self.load_positions()
        self.load_departments()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if record:
            self.organization.setCurrentIndex(max(0, self.organization.findData(record["organization_id"])))
            self.load_positions()
            self.load_departments()
            self.full_name.setText(record["full_name"] or "")
            self.service_number.setText(record["service_number"] or "")
            if record["birth_date"]:
                try:
                    self.birth_date.setText(date.fromisoformat(record["birth_date"]).strftime("%d.%m.%Y"))
                except ValueError:
                    self.birth_date.setText(record["birth_date"])
            self.birth_country.setCurrentIndex(self.birth_country.findData(record["birth_country_id"]))
            self.load_regions()
            self.birth_region.setCurrentIndex(self.birth_region.findData(record["birth_region_id"]))
            self.load_cities()
            self.birth_city.setCurrentIndex(self.birth_city.findData(record["birth_city_id"]))
            self.position.setCurrentIndex(self.position.findData(record["position_id"]))
            self.department.setCurrentIndex(self.department.findData(record["department_id"]))
            self.academic_education.setChecked(bool(record["academic_education"]))
            self.notes.setPlainText(record["notes"] or "")

    def load_positions(self):
        current = self.position.currentData()
        current_text = self.position.currentText().strip()
        self.position.blockSignals(True)
        self.position.clear()
        org_id = self.organization.currentData()
        for position_id, position_org, name, sort_order in self.positions_data:
            if position_org == org_id:
                self.position.addItem(name, position_id)
        if current is not None:
            self.position.setCurrentIndex(self.position.findData(current))
        else:
            self.position.setCurrentIndex(-1)
            if current_text:
                self.position.setEditText(current_text)
        self.position.lineEdit().setPlaceholderText("Выберите должность или начните вводить")
        self.position.blockSignals(False)

    def load_departments(self):
        current=self.department.currentData(); self.department.blockSignals(True); self.department.clear(); org_id=self.organization.currentData()
        for department_id,department_org,name in self.departments_data:
            if department_org==org_id:self.department.addItem(name,department_id)
        if current is not None:self.department.setCurrentIndex(self.department.findData(current))
        else:self.department.setCurrentIndex(-1)
        self.department.lineEdit().setPlaceholderText("Выберите подразделение или начните вводить")
        self.department.blockSignals(False)

    def load_countries(self):
        self.birth_country.blockSignals(True)
        self.birth_country.clear()
        for country_id, name in self.locations["countries"]:
            self.birth_country.addItem(name, country_id)
        self.birth_country.setCurrentIndex(-1)
        self.birth_country.lineEdit().clear()
        self.birth_country.lineEdit().setPlaceholderText("Выберите страну или начните вводить")
        self.birth_country.blockSignals(False)

    def load_regions(self):
        current = self.birth_region.currentData()
        self.birth_region.blockSignals(True)
        self.birth_region.clear()
        country_id = self.birth_country.currentData()
        for region_id, region_country_id, name in self.locations["regions"]:
            if region_country_id == country_id:
                self.birth_region.addItem(name, region_id)
        if current is not None:
            self.birth_region.setCurrentIndex(self.birth_region.findData(current))
        else:
            self.birth_region.setCurrentIndex(-1)
        self.birth_region.lineEdit().setPlaceholderText("Выберите область или начните вводить")
        self.birth_region.blockSignals(False)
        self.load_cities()

    def load_cities(self):
        current = self.birth_city.currentData()
        self.birth_city.blockSignals(True)
        self.birth_city.clear()
        region_id = self.birth_region.currentData()
        for city_id, city_region_id, name in self.locations["cities"]:
            if city_region_id == region_id:
                self.birth_city.addItem(name, city_id)
        if current is not None:
            self.birth_city.setCurrentIndex(self.birth_city.findData(current))
        else:
            self.birth_city.setCurrentIndex(-1)
        self.birth_city.lineEdit().setPlaceholderText("Выберите город или начните вводить")
        self.birth_city.blockSignals(False)

    def validate(self):
        if not self.full_name.text().strip() or self.organization.currentIndex() < 0:
            QMessageBox.warning(self, APP_NAME, "Заполните Ф.И.О. и организацию.")
            return
        raw_date = self.birth_date.text().replace("_", "").strip()
        if raw_date and raw_date != "..":
            try:
                datetime.strptime(raw_date, "%d.%m.%Y")
            except ValueError:
                QMessageBox.warning(self, APP_NAME, "Введите корректную дату в формате ДД.ММ.ГГГГ.")
                return
        required_values=(self.birth_country.currentText().strip(),self.birth_region.currentText().strip(),self.birth_city.currentText().strip(),self.department.currentText().strip(),self.position.currentText().strip())
        if not all(required_values):
            QMessageBox.warning(self, APP_NAME, "Заполните страну, область, город рождения, подразделение и должность.")
            return
        if not self.service_number.is_valid():
            QMessageBox.warning(self, APP_NAME, "Личный номер должен иметь формат: буква, тире и цифры. Например: А-123456.")
            return
        try:self.resolve_reference_values()
        except sqlite3.DatabaseError as exc:
            QMessageBox.warning(self,APP_NAME,f"Не удалось записать новое значение в справочник:\n{exc}"); return
        self.accept()

    def resolve_reference_values(self):
        org_id=self.organization.currentData(); country_name=self.birth_country.currentText().strip(); region_name=self.birth_region.currentText().strip(); city_name=self.birth_city.currentText().strip(); department_name=self.department.currentText().strip(); position_name=self.position.currentText().strip()
        def selected_id(combo,name):
            index=combo.currentIndex()
            return combo.itemData(index) if index>=0 and combo.itemText(index).strip().casefold()==name.casefold() else None
        with self.connection:
            country_id=selected_id(self.birth_country,country_name)
            if country_id is None:
                self.connection.execute("INSERT OR IGNORE INTO countries(name) VALUES(?)",(country_name,)); country_id=self.connection.execute("SELECT id FROM countries WHERE name=?",(country_name,)).fetchone()[0]
            region_id=selected_id(self.birth_region,region_name)
            if region_id is None:
                self.connection.execute("INSERT OR IGNORE INTO regions(country_id,name) VALUES(?,?)",(country_id,region_name)); region_id=self.connection.execute("SELECT id FROM regions WHERE country_id=? AND name=?",(country_id,region_name)).fetchone()[0]
            city_id=selected_id(self.birth_city,city_name)
            if city_id is None:
                self.connection.execute("INSERT OR IGNORE INTO cities(region_id,name) VALUES(?,?)",(region_id,city_name)); city_id=self.connection.execute("SELECT id FROM cities WHERE region_id=? AND name=?",(region_id,city_name)).fetchone()[0]
            department_id=selected_id(self.department,department_name)
            if department_id is None:
                self.connection.execute("INSERT OR IGNORE INTO departments(organization_id,name) VALUES(?,?)",(org_id,department_name)); department_id=self.connection.execute("SELECT id FROM departments WHERE organization_id=? AND name=?",(org_id,department_name)).fetchone()[0]
            position_id=selected_id(self.position,position_name)
            if position_id is None:
                position_order=self.connection.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM positions WHERE organization_id=?",(org_id,)).fetchone()[0]
                self.connection.execute("INSERT OR IGNORE INTO positions(organization_id,name,sort_order) VALUES(?,?,?)",(org_id,position_name,position_order)); position_row=self.connection.execute("SELECT id,sort_order FROM positions WHERE organization_id=? AND name=?",(org_id,position_name)).fetchone(); position_id,position_order=position_row["id"],position_row["sort_order"]
            else:position_order=self.connection.execute("SELECT sort_order FROM positions WHERE id=?",(position_id,)).fetchone()[0]
        self.resolved={"country_id":country_id,"region_id":region_id,"city_id":city_id,"department_id":department_id,"position_id":position_id,"position_order":position_order,"country":country_name,"region":region_name,"city":city_name,"department":department_name,"position":position_name}

    def values(self):
        if not hasattr(self,"resolved"):self.resolve_reference_values()
        resolved=self.resolved
        raw_date = self.birth_date.text().replace("_", "").strip()
        stored_date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat() if raw_date and raw_date != ".." else ""
        return {
            "organization_id": self.organization.currentData(), "full_name": self.full_name.text().strip(),
            "birth_date": stored_date, "birth_place": f"{resolved['country']}, {resolved['region']}, {resolved['city']}",
            "birth_country_id": resolved["country_id"], "birth_region_id": resolved["region_id"], "birth_city_id": resolved["city_id"],
            "department": resolved["department"], "department_id": resolved["department_id"], "position": resolved["position"], "position_id": resolved["position_id"],
            "position_level": resolved["position_order"], "academic_education": int(self.academic_education.isChecked()),
            "service_number": self.service_number.text().strip(), "notes": self.notes.toPlainText().strip(),
        }


class ScoreDialog(QDialog):
    changed = Signal()

    def __init__(self, connection, person_id, person_name, readonly=False, parent=None):
        super().__init__(parent)
        self.connection=connection; self.person_id=person_id; self.readonly=readonly
        self.setWindowTitle(f"Оценка — {person_name}"); self.resize(1100,680)
        layout=QVBoxLayout(self)
        heading=QLabel(person_name); heading.setObjectName("pageTitle"); layout.addWidget(heading)
        self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(("Раздел","Критерий","Единица","За единицу","Максимум","Количество","Итого"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.select_row); layout.addWidget(self.table)
        edit=QHBoxLayout(); self.quantity=QDoubleSpinBox(); self.quantity.setRange(0,9999); self.quantity.setDecimals(2)
        self.evidence=QLineEdit(); self.evidence.setPlaceholderText("Документ, номер, дата или примечание")
        self.save=QPushButton("Применить критерий"); self.save.setObjectName("primary"); self.save.clicked.connect(self.save_score)
        edit.addWidget(QLabel("Количество / годы")); edit.addWidget(self.quantity); edit.addWidget(QLabel("Подтверждение")); edit.addWidget(self.evidence,1); edit.addWidget(self.save)
        layout.addLayout(edit)
        bottom=QHBoxLayout(); self.total=QLabel(); self.total.setObjectName("total"); bottom.addWidget(self.total); bottom.addStretch()
        close=QPushButton("Закрыть"); close.clicked.connect(self.accept); bottom.addWidget(close); layout.addLayout(bottom)
        if readonly: self.quantity.setEnabled(False); self.evidence.setEnabled(False); self.save.setEnabled(False)
        self.load()

    def load(self):
        rows=self.connection.execute("""SELECT c.*,COALESCE(s.quantity,0) quantity,COALESCE(s.points,0) points,COALESCE(s.evidence,'') evidence
          FROM criteria c LEFT JOIN scores s ON s.criterion_id=c.id AND s.person_id=? WHERE c.enabled=1 ORDER BY c.section DESC,c.id""",(self.person_id,)).fetchall()
        self.table.setRowCount(len(rows)); total=0
        for i,row in enumerate(rows):
            total+=row["points"]
            vals=(row["section"],row["name"],row["unit"],points(row["rate"]),"—" if row["cap"] is None else points(row["cap"]),points(row["quantity"]),points(row["points"]))
            for col,val in enumerate(vals): self.table.setItem(i,col,table_item(val))
            self.table.item(i,0).setData(Qt.UserRole,row["id"]); self.table.item(i,0).setData(Qt.UserRole+1,row["evidence"])
            if row["points"]<0:
                for col in range(7): self.table.item(i,col).setForeground(QColor("#b42318"))
        self.total.setText(f"Итоговый рейтинг: {points(total)}")

    def select_row(self):
        row=self.table.currentRow()
        if row<0:return
        self.quantity.setValue(float(self.table.item(row,5).text().replace(",",".")))
        self.evidence.setText(self.table.item(row,0).data(Qt.UserRole+1) or "")

    def save_score(self):
        row=self.table.currentRow()
        if row<0: QMessageBox.information(self,APP_NAME,"Выберите критерий."); return
        criterion_id=self.table.item(row,0).data(Qt.UserRole)
        criterion=self.connection.execute("SELECT rate,cap FROM criteria WHERE id=?",(criterion_id,)).fetchone()
        quantity=self.quantity.value(); value=calculate(criterion["rate"],quantity,criterion["cap"])
        self.connection.execute("""INSERT INTO scores(person_id,criterion_id,quantity,points,evidence,assessed_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
          ON CONFLICT(person_id,criterion_id) DO UPDATE SET quantity=excluded.quantity,points=excluded.points,evidence=excluded.evidence,assessed_at=CURRENT_TIMESTAMP""",
          (self.person_id,criterion_id,quantity,value,self.evidence.text().strip()))
        self.connection.commit(); self.load(); self.changed.emit()


class ConflictDialog(QDialog):
    def __init__(self, conflicts, parent=None):
        super().__init__(parent); self.conflicts=conflicts
        self.setWindowTitle("Обнаружены совпадения"); self.resize(980,520)
        layout=QVBoxLayout(self)
        title=QLabel(f"Найдено совпадений: {len(conflicts)}"); title.setObjectName("pageTitle"); layout.addWidget(title)
        note=QLabel("Для каждой записи выберите действие. «Перезаписать» заменит карточку и её оценки данными из загружаемой базы.")
        note.setWordWrap(True); note.setObjectName("muted"); layout.addWidget(note)
        self.table=QTableWidget(len(conflicts),6); self.table.setHorizontalHeaderLabels(("Из загружаемой базы","Организация","В текущей базе","Организация","Причина","Действие"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for row,item in enumerate(conflicts):
            values=(item.source_name,item.source_org,item.existing_name,item.existing_org,item.reason)
            for col,value in enumerate(values): self.table.setItem(row,col,table_item(value))
            combo=QComboBox(); combo.addItem("Оставить текущую","skip"); combo.addItem("Перезаписать","overwrite"); combo.addItem("Добавить как новую","copy")
            self.table.setCellWidget(row,5,combo)
        layout.addWidget(self.table)
        actions=QHBoxLayout()
        for label,code in (("Все: оставить","skip"),("Все: перезаписать","overwrite"),("Все: добавить","copy")):
            button=QPushButton(label); button.clicked.connect(lambda _checked=False,c=code:self.set_all(c)); actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText("Продолжить импорт")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def set_all(self,code):
        for row in range(self.table.rowCount()): self.table.cellWidget(row,5).setCurrentIndex(self.table.cellWidget(row,5).findData(code))

    def decisions(self):
        return {item.source_id:self.table.cellWidget(row,5).currentData() for row,item in enumerate(self.conflicts)}


class UserDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowTitle("Новая учётная запись"); self.setMinimumWidth(430)
        layout=QVBoxLayout(self); form=QFormLayout()
        self.username=QLineEdit(); self.password=QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.role=QComboBox(); self.role.addItem("Администратор","admin"); self.role.addItem("Оператор","operator"); self.role.addItem("Только просмотр","viewer")
        form.addRow("Пользователь",self.username); form.addRow("Пароль",self.password); form.addRow("Роль",self.role); layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Save).setText("Создать")
        buttons.accepted.connect(self.validate); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def validate(self):
        if not self.username.text().strip() or len(self.password.text())<6:
            QMessageBox.warning(self,APP_NAME,"Укажите имя и пароль не короче 6 символов."); return
        self.accept()


class DashboardPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self)
        header=QHBoxLayout(); heading=QLabel("Обзор"); heading.setObjectName("pageTitle"); header.addWidget(heading); header.addStretch()
        refresh=QPushButton("↻  Обновить"); refresh.setObjectName("primary"); refresh.clicked.connect(self.window.refresh_all); header.addWidget(refresh); layout.addLayout(header)
        cards=QHBoxLayout(); self.people=self.card("Действующие сотрудники","0"); self.archived=self.card("В архиве","0"); self.organizations=self.card("Организаций","0"); self.average=self.card("Средний рейтинг","0"); self.best=self.card("Лучший рейтинг","0")
        for card in (self.people,self.archived,self.organizations,self.average,self.best): cards.addWidget(card)
        layout.addLayout(cards)
        self.winner=QFrame(); self.winner.setObjectName("winnerCard"); winner_box=QVBoxLayout(self.winner)
        winner_title=QLabel("★  Лучший сотрудник"); winner_title.setObjectName("winnerTitle"); self.winner_name=QLabel("Нет данных"); self.winner_name.setObjectName("winnerName"); self.winner_details=QLabel(""); self.winner_details.setObjectName("muted")
        winner_box.addWidget(winner_title); winner_box.addWidget(self.winner_name); winner_box.addWidget(self.winner_details); layout.addWidget(self.winner)
        charts=QHBoxLayout(); self.rating_chart=QChartView(); self.organization_chart=QChartView(); self.rating_chart.setRenderHint(QPainter.Antialiasing); self.organization_chart.setRenderHint(QPainter.Antialiasing)
        self.rating_chart.setMinimumHeight(300); self.organization_chart.setMinimumHeight(300); charts.addWidget(self.rating_chart,3); charts.addWidget(self.organization_chart,2); layout.addLayout(charts,1)

    def card(self,title,value):
        frame=QFrame(); frame.setObjectName("card"); box=QVBoxLayout(frame); label=QLabel(title); label.setObjectName("muted"); number=QLabel(value); number.setObjectName("cardValue"); box.addWidget(label); box.addWidget(number); frame.value_label=number; return frame

    def refresh(self):
        c=self.window.db.connection
        self.people.value_label.setText(str(c.execute("SELECT COUNT(*) FROM personnel WHERE active=1").fetchone()[0]))
        self.archived.value_label.setText(str(c.execute("SELECT COUNT(*) FROM personnel WHERE active=0").fetchone()[0]))
        self.organizations.value_label.setText(str(c.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]))
        self.average.value_label.setText(points(c.execute("SELECT AVG(total) FROM (SELECT COALESCE(SUM(s.points),0) total FROM personnel p LEFT JOIN scores s ON s.person_id=p.id WHERE p.active=1 GROUP BY p.id)").fetchone()[0]))
        rows=c.execute("""SELECT p.full_name,o.name organization,p.position,COALESCE(SUM(s.points),0) total FROM personnel p JOIN organizations o ON o.id=p.organization_id
          LEFT JOIN scores s ON s.person_id=p.id WHERE p.active=1 GROUP BY p.id ORDER BY total DESC,p.full_name LIMIT 8""").fetchall()
        best_value=rows[0]["total"] if rows else 0; self.best.value_label.setText(points(best_value))
        if rows:
            self.winner_name.setText(rows[0]["full_name"]); self.winner_details.setText(f"{rows[0]['organization']} · {rows[0]['position'] or 'Должность не указана'} · рейтинг {points(rows[0]['total'])}")
        else: self.winner_name.setText("Нет данных"); self.winner_details.setText("Добавьте сотрудников и заполните оценки")
        bar_set=QBarSet("Рейтинг"); bar_set.setColor(QColor("#2f6fed")); bar_set.append([float(row["total"]) for row in rows])
        series=QBarSeries(); series.append(bar_set); series.setLabelsVisible(True); series.setLabelsFormat("@valuePoint")
        chart=QChart(); chart.addSeries(series); chart.setTitle("Топ сотрудников по рейтингу"); chart.setAnimationOptions(QChart.SeriesAnimations); chart.legend().setVisible(False)
        axis_x=QBarCategoryAxis(); axis_x.append([row["full_name"][:18] for row in rows]); chart.addAxis(axis_x,Qt.AlignBottom); series.attachAxis(axis_x)
        axis_y=QValueAxis(); axis_y.setLabelFormat("%.1f"); minimum=min([0.0]+[float(row["total"]) for row in rows]); maximum=max([1.0]+[float(row["total"]) for row in rows]); axis_y.setRange(minimum,maximum*1.15 if maximum>0 else 1); chart.addAxis(axis_y,Qt.AlignLeft); series.attachAxis(axis_y)
        chart.setBackgroundVisible(False); self.rating_chart.setChart(chart)
        org_rows=c.execute("""SELECT o.name,COUNT(p.id) count FROM organizations o LEFT JOIN personnel p ON p.organization_id=o.id AND p.active=1
          GROUP BY o.id HAVING count>0 ORDER BY count DESC""").fetchall()
        pie=QPieSeries()
        for row in org_rows: pie.append(row["name"],row["count"])
        for slice_ in pie.slices(): slice_.setLabel(f"{slice_.label()} — {int(slice_.value())}"); slice_.setLabelVisible(True)
        pie_chart=QChart(); pie_chart.addSeries(pie); pie_chart.setTitle("Сотрудники по организациям"); pie_chart.setAnimationOptions(QChart.SeriesAnimations); pie_chart.legend().setVisible(False); pie_chart.setBackgroundVisible(False); self.organization_chart.setChart(pie_chart)


class EmployeesPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); top=QHBoxLayout(); title=QLabel("Сотрудники и рейтинг"); title.setObjectName("pageTitle"); top.addWidget(title); top.addStretch()
        self.search=QLineEdit(); self.search.setPlaceholderText("Поиск по Ф.И.О., должности, подразделению"); self.search.setMaximumWidth(340); self.search.textChanged.connect(self.refresh)
        self.status_filter=QComboBox(); self.status_filter.addItem("Действующие",1); self.status_filter.addItem("Архив",0)
        self.org=QComboBox(); self.org.currentIndexChanged.connect(self.refresh); self.refresh_button=QPushButton("↻ Обновить"); self.refresh_button.clicked.connect(self.window.refresh_all)
        top.addWidget(self.search); top.addWidget(self.status_filter); top.addWidget(self.org); top.addWidget(self.refresh_button); layout.addLayout(top)
        actions=QHBoxLayout()
        self.add=QPushButton("+ Добавить"); self.add.setObjectName("primary"); self.edit=QPushButton("Изменить"); self.rate=QPushButton("Открыть оценку"); self.archive=QPushButton("В архив"); self.add_org=QPushButton("+ Организация")
        self.add.clicked.connect(self.add_person); self.edit.clicked.connect(self.edit_person); self.rate.clicked.connect(self.open_scores); self.archive.clicked.connect(self.archive_person)
        self.add_org.clicked.connect(self.add_organization)
        self.status_filter.currentIndexChanged.connect(self.status_changed)
        for button in (self.add,self.edit,self.rate,self.archive,self.add_org): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.table=QTableWidget(0,8); self.table.setHorizontalHeaderLabels(("№","Ф.И.О.","Организация","Подразделение","Должность","Плюс","Минус","Рейтинг"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_scores); layout.addWidget(self.table)
        self.enter_shortcut=QShortcut(QKeySequence(Qt.Key_Return),self.table); self.enter_shortcut.activated.connect(self.open_scores)
        self.delete_shortcut=QShortcut(QKeySequence(Qt.Key_Delete),self.table); self.delete_shortcut.activated.connect(self.archive_person)
        self.edit_shortcut=QShortcut(QKeySequence(Qt.Key_F2),self); self.edit_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.edit_shortcut.activated.connect(self.edit_person)
        self.rate_shortcut=QShortcut(QKeySequence(Qt.Key_F3),self); self.rate_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.rate_shortcut.activated.connect(self.open_scores)
        self.archive_shortcut=QShortcut(QKeySequence(Qt.Key_F4),self); self.archive_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.archive_shortcut.activated.connect(self.archive_person)
        self.refresh_shortcut=QShortcut(QKeySequence(Qt.Key_F5),self); self.refresh_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.refresh_shortcut.activated.connect(self.window.refresh_all)
        hint=QLabel("F2 — изменить    ·    F3 / Enter — оценка    ·    F4 / Delete — архив    ·    F5 — обновить"); hint.setObjectName("muted"); layout.addWidget(hint)
        if window.user["role"]=="viewer":
            for button in (self.add,self.edit,self.archive): button.setEnabled(False)
        if window.user["role"]!="admin": self.add_org.setEnabled(False)

    def load_orgs(self):
        current=self.org.currentData(); self.org.blockSignals(True); self.org.clear(); self.org.addItem("Все организации",None)
        for row in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name"): self.org.addItem(row["name"],row["id"])
        index=self.org.findData(current); self.org.setCurrentIndex(max(0,index)); self.org.blockSignals(False)

    def refresh(self):
        c=self.window.db.connection; where=["p.active=?"]; params=[self.status_filter.currentData()]
        if self.org.currentData(): where.append("p.organization_id=?"); params.append(self.org.currentData())
        query=self.search.text().strip()
        if query: where.append("(p.full_name LIKE ? OR p.department LIKE ? OR p.position LIKE ?)"); params += [f"%{query}%"]*3
        rows=c.execute(f"""SELECT p.id,p.full_name,o.name organization,p.department,p.position,
          COALESCE(SUM(CASE WHEN s.points>0 THEN s.points ELSE 0 END),0) positive,
          COALESCE(SUM(CASE WHEN s.points<0 THEN s.points ELSE 0 END),0) negative,COALESCE(SUM(s.points),0) total
          FROM personnel p JOIN organizations o ON o.id=p.organization_id LEFT JOIN scores s ON s.person_id=p.id
          WHERE {' AND '.join(where)} GROUP BY p.id ORDER BY total DESC,p.full_name""",params).fetchall()
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            vals=(i+1,row["full_name"],row["organization"],row["department"],row["position"],points(row["positive"]),points(row["negative"]),points(row["total"]))
            for col,value in enumerate(vals): self.table.setItem(i,col,table_item(value))
            self.table.item(i,0).setData(Qt.UserRole,row["id"])

    def status_changed(self):
        archived=self.status_filter.currentData()==0
        self.archive.setText("Восстановить" if archived else "В архив")
        self.add.setEnabled(not archived and self.window.user["role"]!="viewer")
        self.edit.setEnabled(not archived and self.window.user["role"]!="viewer")
        self.refresh()

    def selected_id(self):
        row=self.table.currentRow()
        if row<0: QMessageBox.information(self,APP_NAME,"Выберите сотрудника."); return None
        return self.table.item(row,0).data(Qt.UserRole)

    def organizations(self): return [(r["id"],r["name"]) for r in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name")]
    def positions(self): return [(r["id"],r["organization_id"],r["name"],r["sort_order"]) for r in self.window.db.connection.execute("SELECT id,organization_id,name,sort_order FROM positions WHERE active=1 ORDER BY organization_id,sort_order,id")]
    def departments(self): return [(r["id"],r["organization_id"],r["name"]) for r in self.window.db.connection.execute("SELECT id,organization_id,name FROM departments WHERE active=1 ORDER BY organization_id,name")]
    def locations(self):
        c=self.window.db.connection
        return {"countries":[(r["id"],r["name"]) for r in c.execute("SELECT id,name FROM countries ORDER BY name")],
                "regions":[(r["id"],r["country_id"],r["name"]) for r in c.execute("SELECT id,country_id,name FROM regions ORDER BY name")],
                "cities":[(r["id"],r["region_id"],r["name"]) for r in c.execute("SELECT id,region_id,name FROM cities ORDER BY name")]}

    def add_organization(self):
        name,ok=QInputDialog.getText(self,"Новая организация","Название организации")
        if not ok or not name.strip():return
        try:
            self.window.db.connection.execute("INSERT INTO organizations(name) VALUES(?)",(name.strip(),)); self.window.db.connection.commit(); self.window.audit("Создана организация",name.strip()); self.window.refresh_all()
        except sqlite3.IntegrityError: QMessageBox.warning(self,APP_NAME,"Организация с таким названием уже существует.")

    def add_person(self):
        orgs=self.organizations()
        if not orgs: QMessageBox.information(self,APP_NAME,"Сначала создайте организацию."); return
        positions=self.positions()
        if not positions: QMessageBox.information(self,APP_NAME,"Сначала добавьте должности в справочник."); return
        departments=self.departments()
        if not departments: QMessageBox.information(self,APP_NAME,"Сначала добавьте подразделения в справочник."); return
        locations=self.locations()
        if not locations["countries"] or not locations["regions"] or not locations["cities"]: QMessageBox.information(self,APP_NAME,"Сначала заполните справочник мест рождения."); return
        dialog=PersonDialog(self.window.db.connection,orgs,positions,departments,locations,parent=self)
        if dialog.exec()!=QDialog.Accepted:return
        d=dialog.values(); self.window.db.connection.execute("""INSERT INTO personnel(organization_id,full_name,birth_date,birth_place,birth_country_id,birth_region_id,birth_city_id,department,department_id,position,position_id,position_level,academic_education,service_number,notes)
          VALUES(:organization_id,:full_name,:birth_date,:birth_place,:birth_country_id,:birth_region_id,:birth_city_id,:department,:department_id,:position,:position_id,:position_level,:academic_education,:service_number,:notes)""",d); self.window.db.connection.commit()
        self.window.audit("Добавлена карточка",d["full_name"]); self.window.refresh_all()

    def edit_person(self):
        if self.window.user["role"]=="viewer" or self.status_filter.currentData()==0:return
        person_id=self.selected_id()
        if not person_id:return
        record=self.window.person(person_id); dialog=PersonDialog(self.window.db.connection,self.organizations(),self.positions(),self.departments(),self.locations(),record,self)
        if dialog.exec()!=QDialog.Accepted:return
        d=dialog.values(); d["id"]=person_id
        self.window.db.connection.execute("""UPDATE personnel SET organization_id=:organization_id,full_name=:full_name,birth_date=:birth_date,birth_place=:birth_place,
          birth_country_id=:birth_country_id,birth_region_id=:birth_region_id,birth_city_id=:birth_city_id,
          department=:department,department_id=:department_id,position=:position,position_id=:position_id,position_level=:position_level,academic_education=:academic_education,
          service_number=:service_number,notes=:notes,updated_at=CURRENT_TIMESTAMP WHERE id=:id""",d); self.window.db.connection.commit()
        self.window.audit("Изменена карточка",d["full_name"]); self.window.refresh_all()

    def open_scores(self,*_args):
        person_id=self.selected_id()
        if not person_id:return
        person=self.window.person(person_id); readonly=self.window.user["role"]=="viewer" or self.status_filter.currentData()==0
        dialog=ScoreDialog(self.window.db.connection,person_id,person["full_name"],readonly,self)
        dialog.changed.connect(self.window.refresh_all); dialog.exec(); self.window.refresh_all()

    def archive_person(self):
        if self.window.user["role"]=="viewer":return
        person_id=self.selected_id()
        if not person_id:return
        person=self.window.person(person_id); archived=self.status_filter.currentData()==0
        question=f"Восстановить «{person['full_name']}» из архива?" if archived else f"Переместить «{person['full_name']}» в архив?"
        if QMessageBox.question(self,APP_NAME,question)==QMessageBox.Yes:
            new_state=1 if archived else 0
            self.window.db.connection.execute("UPDATE personnel SET active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_state,person_id)); self.window.db.connection.commit()
            self.window.audit("Карточка восстановлена из архива" if archived else "Карточка помещена в архив",person["full_name"]); self.window.refresh_all()


class ReportsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); title=QLabel("Отчёты"); title.setObjectName("pageTitle"); layout.addWidget(title)
        controls=QHBoxLayout(); self.org=QComboBox(); self.kind=QComboBox(); self.kind.addItem("Сводный рейтинг","summary"); self.kind.addItem("Положительные и отрицательные баллы","balance")
        generate=QPushButton("Сформировать"); generate.setObjectName("primary"); generate.clicked.connect(self.refresh)
        export=QPushButton("Экспорт CSV"); export.clicked.connect(self.export_csv)
        card=QPushButton("Отчёт выбранного в PDF"); card.clicked.connect(self.export_pdf)
        controls.addWidget(QLabel("Организация")); controls.addWidget(self.org); controls.addWidget(QLabel("Вид отчёта")); controls.addWidget(self.kind); controls.addWidget(generate); controls.addWidget(export); controls.addWidget(card); controls.addStretch(); layout.addLayout(controls)
        self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(("№","Ф.И.О.","Организация","Подразделение","Положительные","Отрицательные","Итого")); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); layout.addWidget(self.table)

    def load_orgs(self):
        current=self.org.currentData(); self.org.clear(); self.org.addItem("Все организации",None)
        for row in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name"): self.org.addItem(row["name"],row["id"])
        self.org.setCurrentIndex(max(0,self.org.findData(current)))

    def report_rows(self):
        where="p.active=1"; params=[]
        if self.org.currentData(): where+=" AND p.organization_id=?"; params.append(self.org.currentData())
        return self.window.db.connection.execute(f"""SELECT p.id,p.full_name,o.name organization,p.department,
          COALESCE(SUM(CASE WHEN s.points>0 THEN s.points ELSE 0 END),0) positive,
          COALESCE(SUM(CASE WHEN s.points<0 THEN s.points ELSE 0 END),0) negative,COALESCE(SUM(s.points),0) total
          FROM personnel p JOIN organizations o ON o.id=p.organization_id LEFT JOIN scores s ON s.person_id=p.id
          WHERE {where} GROUP BY p.id ORDER BY total DESC,p.full_name""",params).fetchall()

    def refresh(self):
        rows=self.report_rows(); self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            vals=(i+1,row["full_name"],row["organization"],row["department"],points(row["positive"]),points(row["negative"]),points(row["total"]))
            for col,value in enumerate(vals): self.table.setItem(i,col,table_item(value))
            self.table.item(i,0).setData(Qt.UserRole,row["id"])

    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,"Экспорт отчёта",f"rating_{date.today().isoformat()}.csv","CSV (*.csv)")
        if not path:return
        with open(path,"w",newline="",encoding="utf-8-sig") as stream:
            writer=csv.writer(stream,delimiter=";"); writer.writerow(("Место","Ф.И.О.","Организация","Подразделение","Положительные","Отрицательные","Рейтинг"))
            for i,row in enumerate(self.report_rows(),1): writer.writerow((i,row["full_name"],row["organization"],row["department"],points(row["positive"]),points(row["negative"]),points(row["total"])))
        self.window.audit("Экспорт сводного отчёта",path); QMessageBox.information(self,APP_NAME,"Отчёт сохранён.")

    def export_pdf(self):
        row=self.table.currentRow()
        if row<0: QMessageBox.information(self,APP_NAME,"Выберите сотрудника в таблице."); return
        person_id=self.table.item(row,0).data(Qt.UserRole); person=self.window.person(person_id)
        path,_=QFileDialog.getSaveFileName(self,"Сохранить персональный отчёт",f"{person['full_name']}.pdf","PDF (*.pdf)")
        if not path:return
        scores=self.window.db.connection.execute("""SELECT c.section,c.name,s.quantity,c.unit,s.points,s.evidence FROM scores s JOIN criteria c ON c.id=s.criterion_id
          WHERE s.person_id=? AND s.quantity>0 ORDER BY c.section DESC,c.id""",(person_id,)).fetchall()
        total=sum(x["points"] for x in scores)
        def esc(value):
            import html
            return html.escape(str(value or ""))
        score_rows="".join(f"<tr><td>{esc(x['section'])}</td><td>{esc(x['name'])}</td><td>{points(x['quantity'])} {esc(x['unit'])}</td><td>{points(x['points'])}</td><td>{esc(x['evidence'])}</td></tr>" for x in scores)
        html_text=f"""<html><body><h1>Рейтинг сотрудника</h1><table cellpadding='5'><tr><td><b>Ф.И.О.</b></td><td>{esc(person['full_name'])}</td></tr><tr><td><b>Организация</b></td><td>{esc(person['organization_name'])}</td></tr><tr><td><b>Подразделение</b></td><td>{esc(person['department'])}</td></tr><tr><td><b>Должность</b></td><td>{esc(person['position'])}</td></tr><tr><td><b>Итоговый рейтинг</b></td><td>{points(total)}</td></tr></table><h2>Критерии</h2><table border='1' cellspacing='0' cellpadding='4'><tr><th>Раздел</th><th>Критерий</th><th>Количество</th><th>Балл</th><th>Подтверждение</th></tr>{score_rows}</table><p>Сформировано: {datetime.now():%d.%m.%Y %H:%M}</p></body></html>"""
        from PySide6.QtGui import QTextDocument
        document=QTextDocument(); document.setHtml(html_text)
        printer=QPrinter(QPrinter.HighResolution); printer.setOutputFormat(QPrinter.PdfFormat); printer.setOutputFileName(path); document.print_(printer)
        self.window.audit("Экспорт персонального отчёта",person["full_name"]); QMessageBox.information(self,APP_NAME,"PDF-отчёт сохранён.")


class UsersPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); title=QLabel("Учётные записи"); title.setObjectName("pageTitle"); layout.addWidget(title)
        actions=QHBoxLayout(); add=QPushButton("+ Создать пользователя"); add.setObjectName("primary"); add.clicked.connect(self.add_user)
        reset=QPushButton("Сменить пароль"); reset.clicked.connect(self.reset_password); toggle=QPushButton("Включить / отключить"); toggle.clicked.connect(self.toggle_user)
        actions.addWidget(add); actions.addWidget(reset); actions.addWidget(toggle); actions.addStretch(); layout.addLayout(actions)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(("ID","Пользователь","Роль","Состояние")); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); layout.addWidget(self.table)

    def refresh(self):
        rows=self.window.db.connection.execute("SELECT id,username,role,active FROM users ORDER BY username").fetchall(); self.table.setRowCount(len(rows))
        names={"admin":"Администратор","operator":"Оператор","viewer":"Только просмотр"}
        for i,row in enumerate(rows):
            for col,value in enumerate((row["id"],row["username"],names[row["role"]],"Активен" if row["active"] else "Отключён")): self.table.setItem(i,col,table_item(value))

    def selected(self):
        row=self.table.currentRow()
        if row<0: QMessageBox.information(self,APP_NAME,"Выберите пользователя."); return None
        return int(self.table.item(row,0).text()),self.table.item(row,1).text()

    def add_user(self):
        dialog=UserDialog(self)
        if dialog.exec()!=QDialog.Accepted:return
        try: self.window.db.create_user(dialog.username.text().strip(),dialog.password.text(),dialog.role.currentData()); self.window.audit("Создан пользователь",dialog.username.text().strip()); self.refresh()
        except sqlite3.IntegrityError: QMessageBox.warning(self,APP_NAME,"Такой пользователь уже существует.")

    def reset_password(self):
        selected=self.selected()
        if not selected:return
        dialog=CredentialsDialog(self,title=f"Новый пароль — {selected[1]}"); dialog.username.setText(selected[1]); dialog.username.setEnabled(False)
        if dialog.exec()!=QDialog.Accepted:return
        self.window.db.connection.execute("UPDATE users SET password_hash=? WHERE id=?",(password_hash(dialog.password.text()),selected[0])); self.window.db.connection.commit(); self.window.audit("Сменён пароль",selected[1]); QMessageBox.information(self,APP_NAME,"Пароль изменён.")

    def toggle_user(self):
        selected=self.selected()
        if not selected:return
        if selected[1]==self.window.user["username"]: QMessageBox.warning(self,APP_NAME,"Нельзя отключить текущую учётную запись."); return
        self.window.db.connection.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(selected[0],)); self.window.db.connection.commit(); self.window.audit("Изменено состояние пользователя",selected[1]); self.refresh()


class DepartmentsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); header=QHBoxLayout(); title=QLabel("Справочник подразделений"); title.setObjectName("pageTitle"); header.addWidget(title); header.addStretch(); self.organization=QComboBox(); self.organization.currentIndexChanged.connect(self.refresh_table); header.addWidget(QLabel("Организация")); header.addWidget(self.organization); layout.addLayout(header)
        actions=QHBoxLayout(); self.add=QPushButton("+ Добавить подразделение"); self.add.setObjectName("primary"); self.rename=QPushButton("Переименовать"); self.remove=QPushButton("Удалить"); self.add.clicked.connect(self.add_department); self.rename.clicked.connect(self.rename_department); self.remove.clicked.connect(self.remove_department)
        actions.addWidget(self.add); actions.addWidget(self.rename); actions.addWidget(self.remove); actions.addStretch(); layout.addLayout(actions)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(("№","Организация","Подразделение","Сотрудников")); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.table.doubleClicked.connect(self.rename_department); layout.addWidget(self.table)
        if window.user["role"]!="admin":
            for button in (self.add,self.rename,self.remove):button.setEnabled(False)

    def refresh(self):
        current=self.organization.currentData(); self.organization.blockSignals(True); self.organization.clear(); self.organization.addItem("Все организации",None)
        for row in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name"):self.organization.addItem(row["name"],row["id"])
        self.organization.setCurrentIndex(max(0,self.organization.findData(current))); self.organization.blockSignals(False); self.refresh_table()

    def refresh_table(self):
        org_id=self.organization.currentData(); params=[]; clause=""
        if org_id is not None:clause=" AND d.organization_id=?"; params.append(org_id)
        rows=self.window.db.connection.execute(f"""SELECT d.id,d.name,o.name organization,COUNT(p.id) people_count FROM departments d JOIN organizations o ON o.id=d.organization_id LEFT JOIN personnel p ON p.department_id=d.id WHERE d.active=1{clause} GROUP BY d.id ORDER BY o.name,d.name""",params).fetchall(); self.table.setRowCount(len(rows)); self.table.setColumnHidden(1,org_id is not None)
        for index,row in enumerate(rows):
            for column,value in enumerate((index+1,row["organization"],row["name"],row["people_count"])):self.table.setItem(index,column,table_item(value))
            self.table.item(index,0).setData(Qt.UserRole,row["id"])

    def selected(self):
        row=self.table.currentRow()
        if row<0:QMessageBox.information(self,APP_NAME,"Выберите подразделение."); return None
        return self.table.item(row,0).data(Qt.UserRole),self.table.item(row,2).text()

    def add_department(self):
        org_id=self.organization.currentData()
        if org_id is None:QMessageBox.information(self,APP_NAME,"Выберите конкретную организацию."); return
        name,ok=QInputDialog.getText(self,"Новое подразделение","Название подразделения")
        if not ok or not name.strip():return
        try:self.window.db.connection.execute("INSERT INTO departments(organization_id,name) VALUES(?,?)",(org_id,name.strip())); self.window.db.connection.commit(); self.window.audit("Добавлено подразделение",name.strip()); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,"Такое подразделение уже существует.")

    def rename_department(self,*_args):
        selected=self.selected()
        if not selected:return
        department_id,old=selected; name,ok=QInputDialog.getText(self,"Переименовать подразделение","Новое название",text=old)
        if not ok or not name.strip() or name.strip()==old:return
        try:
            with self.window.db.connection:
                self.window.db.connection.execute("UPDATE departments SET name=? WHERE id=?",(name.strip(),department_id)); self.window.db.connection.execute("UPDATE personnel SET department=? WHERE department_id=?",(name.strip(),department_id))
            self.window.audit("Переименовано подразделение",f"{old} → {name.strip()}"); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,"Такое подразделение уже существует.")

    def remove_department(self):
        selected=self.selected()
        if not selected:return
        department_id,name=selected; used=self.window.db.connection.execute("SELECT COUNT(*) FROM personnel WHERE department_id=?",(department_id,)).fetchone()[0]
        if used:QMessageBox.warning(self,APP_NAME,"Подразделение используется в карточках сотрудников."); return
        if QMessageBox.question(self,APP_NAME,f"Удалить подразделение «{name}»?")==QMessageBox.Yes:self.window.db.connection.execute("DELETE FROM departments WHERE id=?",(department_id,)); self.window.db.connection.commit(); self.window.refresh_all()


class LocationsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); title=QLabel("Справочник мест рождения"); title.setObjectName("pageTitle"); layout.addWidget(title)
        note=QLabel("Фильтры влияют только на отображение таблицы. Если страна или область не выбрана, показываются все доступные значения."); note.setObjectName("muted"); note.setWordWrap(True); layout.addWidget(note)
        panel=QFrame(); panel.setObjectName("filterPanel"); filters=QHBoxLayout(panel)
        self.country_filter=QComboBox(); self.region_filter=QComboBox(); self.country_filter.currentIndexChanged.connect(lambda _index:self.load_region_filter())
        apply_button=QPushButton("Применить"); apply_button.setObjectName("primary"); clear_button=QPushButton("Очистить фильтр")
        apply_button.clicked.connect(self.apply_filter); clear_button.clicked.connect(self.clear_filter)
        filters.addWidget(QLabel("Страна")); filters.addWidget(self.country_filter,1); filters.addWidget(QLabel("Область")); filters.addWidget(self.region_filter,1); filters.addWidget(apply_button); filters.addWidget(clear_button); layout.addWidget(panel)
        actions=QHBoxLayout(); self.add_country_button=QPushButton("+ Страна"); self.add_region_button=QPushButton("+ Область"); self.add_city_button=QPushButton("+ Город"); self.rename_country_button=QPushButton("Переименовать страну"); self.rename_region_button=QPushButton("Переименовать область"); self.rename_city_button=QPushButton("Переименовать город"); self.remove_city_button=QPushButton("Удалить город")
        self.add_country_button.clicked.connect(self.add_country); self.add_region_button.clicked.connect(self.add_region); self.add_city_button.clicked.connect(self.add_city); self.rename_country_button.clicked.connect(lambda:self.rename_selected("countries","country_id","country","страну")); self.rename_region_button.clicked.connect(lambda:self.rename_selected("regions","region_id","region","область")); self.rename_city_button.clicked.connect(lambda:self.rename_selected("cities","city_id","city","город")); self.remove_city_button.clicked.connect(self.remove_city)
        for button in (self.add_country_button,self.add_region_button,self.add_city_button,self.rename_country_button,self.rename_region_button,self.rename_city_button,self.remove_city_button):actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(("№","Страна","Область","Город")); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for column in (1,2,3):self.table.horizontalHeader().setSectionResizeMode(column,QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda:self.rename_selected("cities","city_id","city","город")); layout.addWidget(self.table)
        if window.user["role"]!="admin":
            for button in (self.add_country_button,self.add_region_button,self.add_city_button,self.rename_country_button,self.rename_region_button,self.rename_city_button,self.remove_city_button):button.setEnabled(False)

    def refresh(self):
        country_id=self.country_filter.currentData(); region_id=self.region_filter.currentData(); self.country_filter.blockSignals(True); self.country_filter.clear(); self.country_filter.addItem("Все страны",None)
        for row in self.window.db.connection.execute("SELECT id,name FROM countries ORDER BY name"):self.country_filter.addItem(row["name"],row["id"])
        self.country_filter.setCurrentIndex(max(0,self.country_filter.findData(country_id))); self.country_filter.blockSignals(False); self.load_region_filter(region_id); self.apply_filter()

    def load_region_filter(self,preferred_id=None):
        current=preferred_id if preferred_id is not None else self.region_filter.currentData(); self.region_filter.clear(); self.region_filter.addItem("Все области",None); country_id=self.country_filter.currentData()
        sql="SELECT id,name FROM regions"; params=[]
        if country_id is not None:sql+=" WHERE country_id=?"; params.append(country_id)
        sql+=" ORDER BY name"
        for row in self.window.db.connection.execute(sql,params):self.region_filter.addItem(row["name"],row["id"])
        self.region_filter.setCurrentIndex(max(0,self.region_filter.findData(current)))

    def apply_filter(self):
        where=[]; params=[]
        if self.country_filter.currentData() is not None:where.append("co.id=?"); params.append(self.country_filter.currentData())
        if self.region_filter.currentData() is not None:where.append("r.id=?"); params.append(self.region_filter.currentData())
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        rows=self.window.db.connection.execute(f"""SELECT co.id country_id,co.name country,r.id region_id,r.name region,c.id city_id,c.name city
          FROM cities c JOIN regions r ON r.id=c.region_id JOIN countries co ON co.id=r.country_id{clause} ORDER BY co.name,r.name,c.name""",params).fetchall()
        self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            for column,value in enumerate((index+1,row["country"],row["region"],row["city"])):self.table.setItem(index,column,table_item(value))
            self.table.item(index,0).setData(Qt.UserRole,dict(row))

    def clear_filter(self):
        self.country_filter.setCurrentIndex(0); self.load_region_filter(); self.region_filter.setCurrentIndex(0); self.apply_filter()

    def all_countries(self):return self.window.db.connection.execute("SELECT id,name FROM countries ORDER BY name").fetchall()

    def add_country(self):
        name,ok=QInputDialog.getText(self,"Новая страна","Название страны")
        if ok and name.strip():self.insert("INSERT INTO countries(name) VALUES(?)",(name.strip(),),"Такая страна уже существует.")

    def add_region(self):
        countries=self.all_countries()
        if not countries:QMessageBox.information(self,APP_NAME,"Сначала добавьте страну."); return
        names=[row["name"] for row in countries]; selected,ok=QInputDialog.getItem(self,"Новая область","Страна",names,0,False)
        if not ok:return
        name,ok=QInputDialog.getText(self,"Новая область","Название области")
        if ok and name.strip():self.insert("INSERT INTO regions(country_id,name) VALUES(?,?)",(countries[names.index(selected)]["id"],name.strip()),"Такая область уже существует.")

    def add_city(self):
        regions=self.window.db.connection.execute("""SELECT r.id,r.name,co.name country FROM regions r JOIN countries co ON co.id=r.country_id ORDER BY co.name,r.name""").fetchall()
        if not regions:QMessageBox.information(self,APP_NAME,"Сначала добавьте область."); return
        labels=[f"{row['country']} — {row['name']}" for row in regions]; selected,ok=QInputDialog.getItem(self,"Новый город","Страна и область",labels,0,False)
        if not ok:return
        name,ok=QInputDialog.getText(self,"Новый город","Название города")
        if ok and name.strip():self.insert("INSERT INTO cities(region_id,name) VALUES(?,?)",(regions[labels.index(selected)]["id"],name.strip()),"Такой город уже существует.")

    def insert(self,sql,params,error):
        try:self.window.db.connection.execute(sql,params); self.window.db.connection.commit(); self.window.audit("Изменён справочник мест рождения",params[-1]); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,error)

    def selected(self):
        row=self.table.currentRow()
        if row<0:QMessageBox.information(self,APP_NAME,"Выберите строку в таблице."); return None
        return self.table.item(row,0).data(Qt.UserRole)

    def rename_selected(self,table,id_key,name_key,label):
        selected=self.selected()
        if not selected:return
        old=selected[name_key]; name,ok=QInputDialog.getText(self,f"Переименовать {label}","Новое название",text=old)
        if not ok or not name.strip() or name.strip()==old:return
        try:
            self.window.db.connection.execute(f"UPDATE {table} SET name=? WHERE id=?",(name.strip(),selected[id_key])); self.window.db.connection.commit(); self.window.audit("Изменён справочник мест рождения",f"{old} → {name.strip()}"); self.update_person_places(); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,"Такое название уже существует.")

    def update_person_places(self):
        self.window.db.connection.execute("""UPDATE personnel SET birth_place=(SELECT co.name||', '||r.name||', '||c.name FROM cities c JOIN regions r ON r.id=c.region_id JOIN countries co ON co.id=r.country_id WHERE c.id=personnel.birth_city_id) WHERE birth_city_id IS NOT NULL"""); self.window.db.connection.commit()

    def remove_city(self):
        selected=self.selected()
        if not selected:return
        city_id,name=selected["city_id"],selected["city"]; used=self.window.db.connection.execute("SELECT COUNT(*) FROM personnel WHERE birth_city_id=?",(city_id,)).fetchone()[0]
        if used:QMessageBox.warning(self,APP_NAME,"Город используется в карточках сотрудников."); return
        if QMessageBox.question(self,APP_NAME,f"Удалить город «{name}»?")==QMessageBox.Yes:self.window.db.connection.execute("DELETE FROM cities WHERE id=?",(city_id,)); self.window.db.connection.commit(); self.window.refresh_all()


class PositionsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); header=QHBoxLayout(); title=QLabel("Справочник должностей"); title.setObjectName("pageTitle"); header.addWidget(title); header.addStretch()
        self.organization=QComboBox(); self.organization.currentIndexChanged.connect(self.refresh_table); header.addWidget(QLabel("Организация")); header.addWidget(self.organization); layout.addLayout(header)
        note=QLabel("Расположите должности сверху вниз: первая строка — высшая должность. При подборе система использует следующую строку как нижестоящую должность."); note.setWordWrap(True); note.setObjectName("muted"); layout.addWidget(note)
        actions=QHBoxLayout(); self.add=QPushButton("+ Добавить должность"); self.add.setObjectName("primary"); self.rename=QPushButton("Переименовать"); self.up=QPushButton("↑ Выше"); self.down=QPushButton("↓ Ниже"); self.remove=QPushButton("Удалить")
        self.add.clicked.connect(self.add_position); self.rename.clicked.connect(self.rename_position); self.up.clicked.connect(lambda:self.move(-1)); self.down.clicked.connect(lambda:self.move(1)); self.remove.clicked.connect(self.remove_position)
        for button in (self.add,self.rename,self.up,self.down,self.remove):actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(("Порядок","Организация","Должность","Сотрудников")); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.table.doubleClicked.connect(self.rename_position); layout.addWidget(self.table)
        self.delete_shortcut=QShortcut(QKeySequence(Qt.Key_Delete),self.table); self.delete_shortcut.activated.connect(self.remove_position)
        if window.user["role"]!="admin":
            for button in (self.add,self.rename,self.up,self.down,self.remove):button.setEnabled(False)

    def refresh(self):
        current=self.organization.currentData(); self.organization.blockSignals(True); self.organization.clear(); self.organization.addItem("Все организации",None)
        for row in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name"):self.organization.addItem(row["name"],row["id"])
        self.organization.setCurrentIndex(max(0,self.organization.findData(current))); self.organization.blockSignals(False); self.refresh_table()

    def refresh_table(self):
        org_id=self.organization.currentData(); params=[]; clause=""
        if org_id is not None:clause=" AND pos.organization_id=?"; params.append(org_id)
        rows=self.window.db.connection.execute(f"""SELECT pos.id,pos.name,pos.sort_order,o.name organization,COUNT(p.id) people_count FROM positions pos JOIN organizations o ON o.id=pos.organization_id
          LEFT JOIN personnel p ON p.position_id=pos.id WHERE pos.active=1{clause} GROUP BY pos.id ORDER BY o.name,pos.sort_order,pos.id""",params).fetchall()
        self.table.setRowCount(len(rows)); self.table.setColumnHidden(1,org_id is not None)
        for index,row in enumerate(rows):
            for col,value in enumerate((index+1,row["organization"],row["name"],row["people_count"])):self.table.setItem(index,col,table_item(value))
            self.table.item(index,0).setData(Qt.UserRole,row["id"]); self.table.item(index,0).setData(Qt.UserRole+1,row["sort_order"])
        movable=org_id is not None and self.window.user["role"]=="admin"; self.up.setEnabled(movable); self.down.setEnabled(movable)

    def selected_row(self):
        row=self.table.currentRow()
        if row<0: QMessageBox.information(self,APP_NAME,"Выберите должность."); return None
        return row,self.table.item(row,0).data(Qt.UserRole)

    def add_position(self):
        org_id=self.organization.currentData()
        if org_id is None: QMessageBox.information(self,APP_NAME,"Выберите конкретную организацию."); return
        name,ok=QInputDialog.getText(self,"Новая должность","Название должности")
        if not ok or not name.strip():return
        order=self.window.db.connection.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM positions WHERE organization_id=?",(org_id,)).fetchone()[0]
        try:self.window.db.connection.execute("INSERT INTO positions(organization_id,name,sort_order) VALUES(?,?,?)",(org_id,name.strip(),order)); self.window.db.connection.commit(); self.window.audit("Добавлена должность",name.strip()); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,"Такая должность уже есть в справочнике.")

    def rename_position(self,*_args):
        selected=self.selected_row()
        if not selected:return
        row,position_id=selected; old=self.table.item(row,2).text(); name,ok=QInputDialog.getText(self,"Переименовать должность","Новое название",text=old)
        if not ok or not name.strip() or name.strip()==old:return
        try:
            with self.window.db.connection:
                self.window.db.connection.execute("UPDATE positions SET name=? WHERE id=?",(name.strip(),position_id))
                self.window.db.connection.execute("UPDATE personnel SET position=? WHERE position_id=?",(name.strip(),position_id))
                self.window.db.connection.execute("UPDATE vacancies SET title=? WHERE position_id=?",(name.strip(),position_id))
            self.window.audit("Переименована должность",f"{old} → {name.strip()}"); self.window.refresh_all()
        except sqlite3.IntegrityError:QMessageBox.warning(self,APP_NAME,"Такая должность уже есть в справочнике.")

    def move(self,direction):
        if self.organization.currentData() is None:return
        selected=self.selected_row()
        if not selected:return
        row,position_id=selected; target_row=row+direction
        if target_row<0 or target_row>=self.table.rowCount():return
        target_id=self.table.item(target_row,0).data(Qt.UserRole); current_order=self.table.item(row,0).data(Qt.UserRole+1); target_order=self.table.item(target_row,0).data(Qt.UserRole+1)
        with self.window.db.connection:
            self.window.db.connection.execute("UPDATE positions SET sort_order=? WHERE id=?",(target_order,position_id)); self.window.db.connection.execute("UPDATE positions SET sort_order=? WHERE id=?",(current_order,target_id))
        self.window.audit("Изменён порядок должностей",self.table.item(row,2).text()); self.window.refresh_all(); self.table.selectRow(target_row)

    def remove_position(self):
        selected=self.selected_row()
        if not selected:return
        row,position_id=selected; name=self.table.item(row,2).text()
        used=self.window.db.connection.execute("SELECT (SELECT COUNT(*) FROM personnel WHERE position_id=?)+(SELECT COUNT(*) FROM vacancies WHERE position_id=? AND active=1)",(position_id,position_id)).fetchone()[0]
        if used:QMessageBox.warning(self,APP_NAME,"Должность используется сотрудниками или открытыми вакансиями. Сначала измените эти записи."); return
        if QMessageBox.question(self,APP_NAME,f"Удалить должность «{name}» из справочника?")==QMessageBox.Yes:
            self.window.db.connection.execute("DELETE FROM positions WHERE id=?",(position_id,)); self.window.db.connection.commit(); self.window.audit("Удалена должность",name); self.window.refresh_all()


class VacancyDialog(QDialog):
    def __init__(self,organizations,positions,parent=None):
        super().__init__(parent); self.setWindowTitle("Новая вакантная должность"); self.setMinimumWidth(500)
        layout=QVBoxLayout(self); form=QFormLayout(); self.organization=QComboBox()
        for org_id,name in organizations:self.organization.addItem(name,org_id)
        self.positions_data=positions; self.position=QComboBox(); self.organization.currentIndexChanged.connect(self.load_positions); self.load_positions()
        self.department=QLineEdit()
        help_text=QLabel("Кандидаты будут выбраны среди сотрудников, занимающих следующую должность ниже в справочнике."); help_text.setWordWrap(True); help_text.setObjectName("muted")
        self.requirements=QTextEdit(); self.requirements.setMaximumHeight(80)
        form.addRow("Организация *",self.organization); form.addRow("Вакантная должность *",self.position); form.addRow("Подразделение",self.department); form.addRow("Требования",self.requirements)
        layout.addLayout(form); layout.addWidget(help_text)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Save).setText("Создать вакансию"); buttons.accepted.connect(self.validate); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def validate(self):
        if self.organization.currentIndex()<0 or self.position.currentIndex()<0: QMessageBox.warning(self,APP_NAME,"Выберите организацию и должность из справочника."); return
        self.accept()

    def load_positions(self):
        self.position.clear(); org_id=self.organization.currentData()
        for position_id,position_org,name,sort_order in self.positions_data:
            if position_org==org_id:self.position.addItem(name,{"id":position_id,"name":name,"sort_order":sort_order})


class SelectionPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); header=QHBoxLayout(); title=QLabel("Подбор на вакантную должность"); title.setObjectName("pageTitle"); header.addWidget(title); header.addStretch()
        refresh=QPushButton("↻ Обновить"); refresh.clicked.connect(self.refresh); header.addWidget(refresh); layout.addLayout(header)
        panel=QFrame(); panel.setObjectName("filterPanel"); controls=QGridLayout(panel)
        self.vacancy=QComboBox(); self.vacancy.currentIndexChanged.connect(self.refresh_candidates)
        self.academic=QCheckBox("Только с академическим образованием"); self.academic.stateChanged.connect(self.refresh_candidates)
        self.add=QPushButton("+ Создать вакансию"); self.add.setObjectName("primary"); self.add.clicked.connect(self.add_vacancy)
        self.close_button=QPushButton("Закрыть вакансию"); self.close_button.clicked.connect(self.close_vacancy)
        controls.addWidget(QLabel("Вакантная должность"),0,0); controls.addWidget(self.vacancy,0,1,1,3); controls.addWidget(self.academic,1,0,1,2); controls.addWidget(self.add,1,2); controls.addWidget(self.close_button,1,3)
        layout.addWidget(panel)
        self.description=QLabel("Создайте вакансию, чтобы увидеть подходящих кандидатов."); self.description.setObjectName("muted"); layout.addWidget(self.description)
        self.summary=QLabel("Кандидатов: 0"); self.summary.setObjectName("total"); layout.addWidget(self.summary)
        self.table=QTableWidget(0,7); self.table.setHorizontalHeaderLabels(("№","Ф.И.О.","Текущая должность","Организация","Академическое образование","Положительные","Рейтинг"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.table.doubleClicked.connect(self.open_candidate); layout.addWidget(self.table)
        self.enter_shortcut=QShortcut(QKeySequence(Qt.Key_Return),self.table); self.enter_shortcut.activated.connect(self.open_candidate)
        hint=QLabel("Двойной щелчок или Enter — открыть рейтинг кандидата"); hint.setObjectName("muted"); layout.addWidget(hint)
        if window.user["role"]!="admin": self.add.setEnabled(False); self.close_button.setEnabled(False)

    def refresh(self):
        current=self.vacancy.currentData(); current_id=current.get("id") if current else None; self.vacancy.blockSignals(True); self.vacancy.clear()
        rows=self.window.db.connection.execute("""SELECT v.id,v.position_id,pos.name title,pos.sort_order,v.department,o.name organization_name,o.id organization_id
          FROM vacancies v JOIN organizations o ON o.id=v.organization_id JOIN positions pos ON pos.id=v.position_id
          WHERE v.active=1 ORDER BY o.name,pos.sort_order,pos.name""").fetchall()
        selected_index=0
        for index,row in enumerate(rows):
            self.vacancy.addItem(f"{row['title']} — {row['organization_name']}",dict(row))
            if row["id"]==current_id:selected_index=index
        self.vacancy.setCurrentIndex(selected_index if rows else -1); self.vacancy.blockSignals(False); self.refresh_candidates()

    def refresh_candidates(self):
        vacancy=self.vacancy.currentData(); self.table.setRowCount(0)
        if not vacancy:
            self.description.setText("Создайте вакансию, чтобы увидеть подходящих кандидатов."); self.summary.setText("Кандидатов: 0"); return
        next_order_row=self.window.db.connection.execute("SELECT MIN(sort_order) FROM positions WHERE organization_id=? AND active=1 AND sort_order>?",(vacancy["organization_id"],vacancy["sort_order"])).fetchone()
        next_order=next_order_row[0] if next_order_row else None
        if next_order is None:
            self.description.setText(f"Для должности «{vacancy['title']}» в справочнике нет следующей нижестоящей должности."); self.summary.setText("Подходящих кандидатов: 0"); return
        next_positions=self.window.db.connection.execute("SELECT id,name FROM positions WHERE organization_id=? AND active=1 AND sort_order=? ORDER BY id",(vacancy["organization_id"],next_order)).fetchall()
        position_ids=[row["id"] for row in next_positions]; placeholders=",".join("?" for _ in position_ids)
        where=["p.active=1","p.organization_id=?",f"p.position_id IN ({placeholders})"]; params=[vacancy["organization_id"],*position_ids]
        if self.academic.isChecked():where.append("p.academic_education=1")
        rows=self.window.db.connection.execute(f"""SELECT p.id,p.full_name,pos.name position,p.academic_education,o.name organization,
          COALESCE(SUM(CASE WHEN s.points>0 THEN s.points ELSE 0 END),0) positive,COALESCE(SUM(s.points),0) total
          FROM personnel p JOIN organizations o ON o.id=p.organization_id JOIN positions pos ON pos.id=p.position_id LEFT JOIN scores s ON s.person_id=p.id
          WHERE {' AND '.join(where)} GROUP BY p.id ORDER BY total DESC,p.full_name""",params).fetchall()
        next_names=", ".join(row["name"] for row in next_positions)
        self.description.setText(f"Вакансия: {vacancy['title']}. Следующая должность ниже по справочнику: {next_names}.")
        self.summary.setText(f"Подходящих кандидатов: {len(rows)}")
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            vals=(i+1,row["full_name"],row["position"] or "—",row["organization"],"Да ✓" if row["academic_education"] else "Нет",points(row["positive"]),points(row["total"]))
            for col,value in enumerate(vals):self.table.setItem(i,col,table_item(value))
            self.table.item(i,0).setData(Qt.UserRole,row["id"])
            if row["academic_education"]: self.table.item(i,4).setForeground(QColor("#16803c"))

    def add_vacancy(self):
        organizations=[(r["id"],r["name"]) for r in self.window.db.connection.execute("SELECT id,name FROM organizations ORDER BY name")]
        if not organizations: QMessageBox.information(self,APP_NAME,"Сначала создайте организацию."); return
        positions=[(r["id"],r["organization_id"],r["name"],r["sort_order"]) for r in self.window.db.connection.execute("SELECT id,organization_id,name,sort_order FROM positions WHERE active=1 ORDER BY organization_id,sort_order,id")]
        if not positions: QMessageBox.information(self,APP_NAME,"Сначала заполните справочник должностей."); return
        dialog=VacancyDialog(organizations,positions,self)
        if dialog.exec()!=QDialog.Accepted:return
        selected=dialog.position.currentData()
        self.window.db.connection.execute("INSERT INTO vacancies(organization_id,title,position_level,position_id,department,requirements) VALUES(?,?,?,?,?,?)",(dialog.organization.currentData(),selected["name"],selected["sort_order"],selected["id"],dialog.department.text().strip(),dialog.requirements.toPlainText().strip())); self.window.db.connection.commit(); self.window.audit("Создана вакансия",selected["name"]); self.refresh()

    def close_vacancy(self):
        vacancy=self.vacancy.currentData()
        if not vacancy:return
        if QMessageBox.question(self,APP_NAME,f"Закрыть вакансию «{vacancy['title']}»?")==QMessageBox.Yes:
            self.window.db.connection.execute("UPDATE vacancies SET active=0 WHERE id=?",(vacancy["id"],)); self.window.db.connection.commit(); self.window.audit("Закрыта вакансия",vacancy["title"]); self.refresh()

    def open_candidate(self,*_args):
        row=self.table.currentRow()
        if row<0:return
        person_id=self.table.item(row,0).data(Qt.UserRole); person=self.window.person(person_id); ScoreDialog(self.window.db.connection,person_id,person["full_name"],True,self).exec()


class ScoringReferencePage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window
        layout=QVBoxLayout(self); header=QHBoxLayout(); title=QLabel("Справочник начисления баллов"); title.setObjectName("pageTitle"); header.addWidget(title); header.addStretch()
        open_doc=QPushButton("Открыть исходный документ"); open_doc.clicked.connect(self.open_document); header.addWidget(open_doc); layout.addLayout(header)
        info=QLabel("Цифровой рейтинг равен сумме положительных и отрицательных баллов. В таблице приведены критерии, стоимость единицы и максимальное ограничение согласно приложениям 1 и 2 Правил."); info.setWordWrap(True); info.setObjectName("muted"); layout.addWidget(info)
        controls=QHBoxLayout(); self.section=QComboBox(); self.section.addItem("Все критерии",None); self.section.addItem("Положительные","Положительные"); self.section.addItem("Отрицательные","Отрицательные"); self.section.currentIndexChanged.connect(self.refresh)
        self.search=QLineEdit(); self.search.setPlaceholderText("Поиск критерия"); self.search.textChanged.connect(self.refresh); controls.addWidget(QLabel("Раздел")); controls.addWidget(self.section); controls.addWidget(self.search,1); layout.addLayout(controls)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(("Раздел","Критерий","Балл за единицу","Максимум","Единица","Расчёт")); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Stretch); layout.addWidget(self.table)
        self.refresh()

    def refresh(self):
        where=["enabled=1"]; params=[]
        if self.section.currentData():where.append("section=?"); params.append(self.section.currentData())
        if self.search.text().strip():where.append("name LIKE ?"); params.append(f"%{self.search.text().strip()}%")
        rows=self.window.db.connection.execute(f"SELECT section,name,rate,cap,unit FROM criteria WHERE {' AND '.join(where)} ORDER BY section DESC,id",params).fetchall(); self.table.setRowCount(len(rows))
        for row_index,row in enumerate(rows):
            maximum="Без ограничения" if row["cap"] is None else points(row["cap"])
            formula=f"Количество × {points(row['rate'])}"
            if row["cap"] is not None:formula+=f", предел {maximum}"
            values=(row["section"],row["name"],points(row["rate"]),maximum,row["unit"],formula)
            for column,value in enumerate(values):self.table.setItem(row_index,column,table_item(value))
            if row["rate"]<0:
                for column in range(6):self.table.item(row_index,column).setForeground(QColor("#b42318"))

    def open_document(self):
        path=bundled_resource("rules.docx")
        if not path.exists():QMessageBox.warning(self,APP_NAME,"Исходный документ не найден в составе приложения."); return
        try:os.startfile(str(path))
        except OSError as exc:QMessageBox.warning(self,APP_NAME,f"Не удалось открыть документ:\n{exc}")


class DirectoriesPage(QWidget):
    def __init__(self,positions,locations,scoring,departments,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self); title=QLabel("Справочники"); title.setObjectName("pageTitle"); layout.addWidget(title)
        self.tabs=QTabWidget(); self.tabs.setDocumentMode(True); self.tabs.addTab(positions,"Должности"); self.tabs.addTab(locations,"Места рождения"); self.tabs.addTab(scoring,"Баллы"); self.tabs.addTab(departments,"Подразделения"); layout.addWidget(self.tabs)


class MainWindow(QMainWindow):
    def __init__(self,db,user):
        super().__init__(); self.db=db; self.user=user
        self.setWindowTitle(f"{APP_NAME} {__version__}"); self.resize(1360,820); self.setMinimumSize(1050,650)
        central=QWidget(); self.setCentralWidget(central); root=QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(240); side=QVBoxLayout(sidebar); side.setContentsMargins(18,24,18,18)
        brand=QLabel("ЦИФРОВОЙ\nРЕЙТИНГ"); brand.setObjectName("brand"); brand.setAlignment(Qt.AlignCenter); brand.setMinimumHeight(82); side.addWidget(brand); side.addSpacing(26)
        self.stack=QStackedWidget(); self.dashboard=DashboardPage(self); self.employees=EmployeesPage(self); self.positions=PositionsPage(self); self.locations=LocationsPage(self); self.scoring_reference=ScoringReferencePage(self); self.departments=DepartmentsPage(self); self.directories=DirectoriesPage(self.positions,self.locations,self.scoring_reference,self.departments); self.selection=SelectionPage(self); self.reports=ReportsPage(self); self.users=UsersPage(self)
        pages=(("Обзор",self.dashboard),("Сотрудники",self.employees),("Справочники",self.directories),("Подбор кандидатов",self.selection),("Отчёты",self.reports))
        if user["role"]=="admin": pages += (("Учётные записи",self.users),)
        self.nav=[]
        for label,page in pages:
            self.stack.addWidget(page); button=QPushButton(label); button.setObjectName("nav"); button.setCheckable(True); button.clicked.connect(lambda _checked=False,p=page:self.show_page(p)); side.addWidget(button); self.nav.append((button,page))
        side.addStretch()
        import_button=QPushButton("Загрузить базу данных"); import_button.setObjectName("secondaryNav"); import_button.clicked.connect(self.load_database); side.addWidget(import_button)
        backup_button=QPushButton("Резервная копия"); backup_button.setObjectName("secondaryNav"); backup_button.clicked.connect(self.backup); side.addWidget(backup_button)
        user_label=QLabel(user["username"]); user_label.setObjectName("sidebarUser"); user_label.setAlignment(Qt.AlignCenter); user_label.setToolTip(f"Роль: {user['role']}"); side.addWidget(user_label)
        root.addWidget(sidebar); root.addWidget(self.stack,1)
        self.nav[0][0].setChecked(True); self.show_page(self.dashboard); self.refresh_all()

    def show_page(self,page):
        self.stack.setCurrentWidget(page)
        for button,target in self.nav: button.setChecked(target is page)
        if hasattr(page,"refresh"): page.refresh()

    def person(self,person_id):
        return self.db.connection.execute("SELECT p.*,o.name organization_name FROM personnel p JOIN organizations o ON o.id=p.organization_id WHERE p.id=?",(person_id,)).fetchone()

    def audit(self,action,details=""): self.db.audit(self.user["username"],action,details)

    def refresh_all(self):
        self.positions.refresh(); self.locations.refresh(); self.departments.refresh(); self.employees.load_orgs(); self.employees.refresh(); self.selection.refresh(); self.scoring_reference.refresh(); self.reports.load_orgs(); self.reports.refresh(); self.dashboard.refresh()
        if self.user["role"]=="admin": self.users.refresh()

    def confirm_admin(self, action):
        dialog=CredentialsDialog(self,title=f"Подтверждение: {action}")
        if dialog.exec()!=QDialog.Accepted:return False
        user=self.db.authenticate(*dialog.values())
        if not user or user["role"]!="admin":
            QMessageBox.warning(self,APP_NAME,"Нужны имя и пароль действующего администратора.")
            return False
        return True

    def load_database(self):
        if not self.confirm_admin("загрузка базы данных"):return
        path,_=QFileDialog.getOpenFileName(self,"Загрузить базу данных","","SQLite (*.sqlite3 *.sqlite *.db);;Все файлы (*)")
        if not path:return
        if Path(path).resolve()==self.db.path.resolve(): QMessageBox.warning(self,APP_NAME,"Это текущая рабочая база."); return
        try: conflicts,total=find_conflicts(self.db.connection,Path(path))
        except ImportError as exc: QMessageBox.critical(self,APP_NAME,str(exc)); return
        decisions={}
        if conflicts:
            dialog=ConflictDialog(conflicts,self)
            if dialog.exec()!=QDialog.Accepted:return
            decisions=dialog.decisions()
        answer=QMessageBox.question(self,APP_NAME,f"В базе найдено записей: {total}. Начать импорт?",QMessageBox.Yes|QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        try: result=import_database(self.db.connection,Path(path),decisions)
        except (ImportError,sqlite3.DatabaseError) as exc: QMessageBox.critical(self,APP_NAME,f"Импорт не выполнен:\n{exc}"); return
        self.audit("Импорт базы",f"{path}; {result}"); self.refresh_all()
        QMessageBox.information(self,APP_NAME,f"Импорт завершён.\nДобавлено: {result['added']}\nПерезаписано: {result['overwritten']}\nПропущено: {result['skipped']}")

    def backup(self):
        if not self.confirm_admin("резервная копия"):return
        path,_=QFileDialog.getSaveFileName(self,"Резервная копия",f"digital_rating_{datetime.now():%Y%m%d_%H%M}.sqlite3","SQLite (*.sqlite3)")
        if not path:return
        target=sqlite3.connect(path)
        try:self.db.connection.backup(target)
        finally:target.close()
        self.audit("Резервная копия",path); QMessageBox.information(self,APP_NAME,"Резервная копия создана.")


STYLE="""
* { font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, QDialog, QWidget { background: #f4f6f9; color: #17202a; }
#sidebar { background: #14213d; }
#brand { color: white; background-color:#2f6fed; border:1px solid #5d8df2; border-radius:10px; padding:10px; font-size:16pt; font-weight:700; letter-spacing:1px; }
#sidebarUser { color:white; background-color:#203459; border:1px solid #465675; border-radius:7px; padding:11px 8px; font-size:11pt; font-weight:600; }
QPushButton#nav { text-align: left; padding: 12px 14px; border: none; border-radius: 7px; color: #dce4f3; background: transparent; }
QPushButton#nav:hover { background: #243556; } QPushButton#nav:checked { background: #2f6fed; color: white; font-weight: 600; }
QPushButton#secondaryNav { text-align:left; padding:9px; color:#cbd5e5; background:transparent; border:1px solid #465675; border-radius:6px; }
#pageTitle, #dialogTitle { font-size: 19pt; font-weight: 700; color: #14213d; padding: 4px 0 10px 0; }
#muted { color: #687386; } #total { font-size: 14pt; font-weight: 700; color: #14213d; }
#card { background:white; border:1px solid #e0e5ed; border-radius:10px; min-height:90px; } #cardValue { font-size:22pt; font-weight:700; color:#2f6fed; }
#winnerCard { background:#eaf1ff; border:1px solid #bdd0ff; border-radius:10px; min-height:92px; }
#winnerTitle { color:#2f6fed; font-weight:700; } #winnerName { color:#14213d; font-size:16pt; font-weight:700; }
#filterPanel { background:white; border:1px solid #e0e5ed; border-radius:10px; padding:8px; }
QChartView { background:white; border:1px solid #e0e5ed; border-radius:10px; }
QPushButton { padding:8px 14px; border:1px solid #c7cfdb; border-radius:6px; background:white; }
QPushButton:hover { background:#eef3fb; } QPushButton#primary { background:#2f6fed; color:white; border:none; font-weight:600; } QPushButton#primary:hover { background:#255dcc; }
QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit { background:white; border:1px solid #c7cfdb; border-radius:5px; padding:7px; }
QCheckBox { spacing:10px; padding:5px; font-weight:600; color:#14213d; }
QCheckBox::indicator { width:20px; height:20px; border:2px solid #7c8ba1; border-radius:4px; background:white; }
QCheckBox::indicator:hover { border-color:#2f6fed; }
QCheckBox::indicator:checked { background:white; border-color:#2f6fed; image:url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-32.png); }
QTableWidget { background:white; alternate-background-color:#f7f9fc; gridline-color:#e6eaf0; border:1px solid #d9dfe8; border-radius:6px; selection-background-color:#dce8ff; selection-color:#17202a; }
QHeaderView::section { background:#e9edf4; padding:8px; border:none; border-right:1px solid #d6dce6; font-weight:600; }
"""


def run():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    db=Database(data_directory()/"digital_rating.sqlite3")
    try:
        first=db.initialize()
        if first:
            setup=CredentialsDialog(setup=True)
            if setup.exec()!=QDialog.Accepted:return
            db.create_user(*setup.values(),"admin")
        while True:
            login=CredentialsDialog()
            if login.exec()!=QDialog.Accepted:return
            user=db.authenticate(*login.values())
            if user:break
            QMessageBox.warning(None,APP_NAME,"Неверное имя пользователя или пароль.")
        db.audit(user["username"],"Вход")
        window=MainWindow(db,user); window.show(); app.exec()
    finally:
        db.close()
