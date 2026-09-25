import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rating_app.qt_app import PersonDialog, ServiceNumberEdit
from rating_app.db import Database


class PersonDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_date_mask_location_and_service_number(self):
        folder=tempfile.TemporaryDirectory(); db=Database(Path(folder.name)/"test.sqlite3"); db.initialize()
        db.connection.execute("INSERT INTO organizations(id,name) VALUES(1,'Организация')")
        db.connection.execute("INSERT INTO positions(id,organization_id,name,sort_order) VALUES(10,1,'Директор',1)")
        db.connection.execute("INSERT INTO departments(id,organization_id,name) VALUES(20,1,'Администрация')")
        db.connection.execute("INSERT INTO countries(id,name) VALUES(100,'Казахстан')")
        db.connection.execute("INSERT INTO regions(id,country_id,name) VALUES(200,100,'Кызылординская область')")
        db.connection.execute("INSERT INTO cities(id,region_id,name) VALUES(300,200,'Кызылорда')"); db.connection.commit()
        dialog = PersonDialog(
            db.connection,
            [(1, "Организация")],
            [(10, 1, "Директор", 1)],
            [(20, 1, "Администрация")],
            {
                "countries": [(100, "Казахстан")],
                "regions": [(200, 100, "Кызылординская область")],
                "cities": [(300, 200, "Кызылорда")],
            },
        )
        dialog.full_name.setText("Иванов Иван")
        dialog.department.setCurrentIndex(0)
        dialog.position.setCurrentIndex(0)
        dialog.birth_date.setText("01021990")
        self.assertEqual(dialog.birth_country.currentText(), "")
        self.assertIn("начните вводить", dialog.birth_country.lineEdit().placeholderText())
        dialog.birth_country.lineEdit().setText("Казахс")
        dialog.birth_country.completer().setCompletionPrefix("Казахс")
        QTest.keyClick(dialog.birth_country.lineEdit(), Qt.Key_Tab)
        self.assertEqual(dialog.birth_country.currentData(), 100)
        dialog.birth_region.setCurrentIndex(0)
        dialog.birth_city.setCurrentIndex(0)
        dialog.service_number.format_value("а123456")
        self.assertEqual(dialog.birth_date.text(), "01.02.1990")
        self.assertEqual(dialog.service_number.text(), "А-123456")
        values = dialog.values()
        self.assertEqual(values["birth_date"], "1990-02-01")
        self.assertEqual(values["department_id"], 20)
        self.assertEqual(values["birth_place"], "Казахстан, Кызылординская область, Кызылорда")
        dialog.service_number.setFocus(); dialog.service_number.setCursorPosition(len(dialog.service_number.text()))
        for _ in range(6): QTest.keyClick(dialog.service_number, Qt.Key_Backspace)
        self.assertEqual(dialog.service_number.text(), "А-")
        QTest.keyClick(dialog.service_number, Qt.Key_Backspace)
        self.assertEqual(dialog.service_number.text(), "")
        dialog.close()
        db.close(); folder.cleanup()

    def test_new_typed_values_are_added_to_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"test.sqlite3"); db.initialize()
            db.connection.execute("INSERT INTO organizations(id,name) VALUES(1,'Организация')"); db.connection.commit()
            dialog=PersonDialog(db.connection,[(1,"Организация")],[],[],{"countries":[],"regions":[],"cities":[]})
            dialog.full_name.setText("Новый сотрудник")
            dialog.birth_country.setEditText("Новая страна"); dialog.birth_region.setEditText("Новая область"); dialog.birth_city.setEditText("Новый город")
            dialog.department.setEditText("Новое подразделение"); dialog.position.setEditText("Новая должность")
            values=dialog.values()
            self.assertIsNotNone(values["position_id"]); self.assertIsNotNone(values["department_id"]); self.assertIsNotNone(values["birth_city_id"])
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0],1)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM departments").fetchone()[0],1)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM cities").fetchone()[0],1)
            dialog.close(); db.close()


if __name__ == "__main__":
    unittest.main()
