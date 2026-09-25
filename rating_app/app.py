from __future__ import annotations

import csv
import os
import sqlite3
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from .db import Database
from .scoring import calculate


APP_NAME = "Цифровой рейтинг"


def data_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "DigitalRating"


def as_number(value: str) -> float:
    return float((value or "0").strip().replace(",", "."))


def format_points(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class CredentialsDialog(tk.Toplevel):
    def __init__(self, parent, title: str, setup: bool = False):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.transient(parent)
        self.grab_set()
        frame = ttk.Frame(self, padding=18)
        frame.grid()
        if setup:
            ttk.Label(frame, text="Создайте локальную учётную запись администратора.", wraplength=360).grid(row=0, column=0, columnspan=2, pady=(0, 14))
        ttk.Label(frame, text="Пользователь").grid(row=1, column=0, sticky="w", pady=5)
        self.username = ttk.Entry(frame, width=34)
        self.username.grid(row=1, column=1, pady=5)
        self.username.insert(0, "admin" if setup else "")
        ttk.Label(frame, text="Пароль").grid(row=2, column=0, sticky="w", pady=5)
        self.password = ttk.Entry(frame, width=34, show="•")
        self.password.grid(row=2, column=1, pady=5)
        self.confirm = None
        if setup:
            ttk.Label(frame, text="Повторите пароль").grid(row=3, column=0, sticky="w", pady=5)
            self.confirm = ttk.Entry(frame, width=34, show="•")
            self.confirm.grid(row=3, column=1, pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Продолжить", command=self.submit).pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda _e: self.submit())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.username.focus_set()

    def submit(self):
        username, password = self.username.get().strip(), self.password.get()
        if not username or len(password) < 6:
            messagebox.showwarning(APP_NAME, "Укажите имя и пароль не короче 6 символов.", parent=self)
            return
        if self.confirm is not None and password != self.confirm.get():
            messagebox.showwarning(APP_NAME, "Пароли не совпадают.", parent=self)
            return
        self.result = (username, password)
        self.destroy()


class PersonDialog(tk.Toplevel):
    fields = (
        ("full_name", "Ф.И.О. *"), ("birth_date", "Дата рождения (ГГГГ-ММ-ДД)"),
        ("birth_place", "Место рождения"), ("department", "Подразделение"),
        ("position", "Должность"), ("service_number", "Личный номер"),
    )

    def __init__(self, parent, organizations, record=None):
        super().__init__(parent)
        self.title("Карточка военнослужащего")
        self.result = None
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        self.values = {}
        ttk.Label(body, text="Организация *").grid(row=0, column=0, sticky="w", pady=4)
        self.org = ttk.Combobox(body, state="readonly", width=43, values=[x[1] for x in organizations])
        self.org.grid(row=0, column=1, sticky="ew", pady=4)
        self.org_ids = {x[1]: x[0] for x in organizations}
        for row, (key, label) in enumerate(self.fields, 1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(body, width=46)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self.values[key] = entry
        ttk.Label(body, text="Примечание").grid(row=7, column=0, sticky="nw", pady=4)
        self.notes = tk.Text(body, width=46, height=4)
        self.notes.grid(row=7, column=1, sticky="ew", pady=4)
        if record:
            self.org.set(record["organization_name"])
            for key, _ in self.fields:
                self.values[key].insert(0, record[key] or "")
            self.notes.insert("1.0", record["notes"] or "")
        elif organizations:
            self.org.current(0)
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Сохранить", command=self.submit).pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left")

    def submit(self):
        name, org_name = self.values["full_name"].get().strip(), self.org.get()
        birth = self.values["birth_date"].get().strip()
        if not name or not org_name:
            messagebox.showwarning(APP_NAME, "Заполните Ф.И.О. и организацию.", parent=self)
            return
        if birth:
            try:
                date.fromisoformat(birth)
            except ValueError:
                messagebox.showwarning(APP_NAME, "Дата должна быть в формате ГГГГ-ММ-ДД.", parent=self)
                return
        self.result = {key: widget.get().strip() for key, widget in self.values.items()}
        self.result["organization_id"] = self.org_ids[org_name]
        self.result["notes"] = self.notes.get("1.0", "end").strip()
        self.destroy()


class ScoreDialog(tk.Toplevel):
    def __init__(self, parent, connection, person_id: int, person_name: str, readonly=False):
        super().__init__(parent)
        self.connection, self.person_id, self.readonly = connection, person_id, readonly
        self.title(f"Оценка — {person_name}")
        self.geometry("1060x650")
        self.transient(parent)
        self.grab_set()
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=person_name, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 8))
        columns = ("section", "criterion", "unit", "rate", "cap", "quantity", "points", "evidence")
        self.tree = ttk.Treeview(outer, columns=columns, show="headings")
        widths = {"section":110,"criterion":350,"unit":80,"rate":60,"cap":70,"quantity":80,"points":70,"evidence":210}
        labels = {"section":"Раздел","criterion":"Критерий","unit":"Единица","rate":"Балл","cap":"Макс.","quantity":"Кол-во","points":"Итого","evidence":"Подтверждение"}
        for col in columns:
            self.tree.heading(col, text=labels[col]); self.tree.column(col, width=widths[col], anchor="w")
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        panel = ttk.Frame(outer, padding=(10, 0, 0, 0))
        panel.pack(side="right", fill="y")
        ttk.Label(panel, text="Количество / годы").pack(anchor="w")
        self.quantity = ttk.Entry(panel, width=24)
        self.quantity.pack(anchor="w", pady=(2, 10))
        ttk.Label(panel, text="Документ-подтверждение").pack(anchor="w")
        self.evidence = tk.Text(panel, width=28, height=8)
        self.evidence.pack(anchor="w", pady=(2, 10))
        self.save_button = ttk.Button(panel, text="Применить критерий", command=self.save_score)
        self.save_button.pack(fill="x")
        ttk.Button(panel, text="Закрыть", command=self.destroy).pack(fill="x", pady=6)
        self.total_label = ttk.Label(panel, text="Итог: 0", font=("Segoe UI", 12, "bold"))
        self.total_label.pack(anchor="w", pady=(18, 0))
        if readonly:
            self.quantity.configure(state="disabled"); self.evidence.configure(state="disabled"); self.save_button.configure(state="disabled")
        self.tree.bind("<<TreeviewSelect>>", self.select_score)
        self.load()

    def load(self):
        selected = self.tree.selection()
        for item in self.tree.get_children(): self.tree.delete(item)
        rows = self.connection.execute("""SELECT c.*, COALESCE(s.quantity,0) quantity, COALESCE(s.points,0) points,
            COALESCE(s.evidence,'') evidence FROM criteria c LEFT JOIN scores s
            ON s.criterion_id=c.id AND s.person_id=? WHERE c.enabled=1 ORDER BY c.section DESC,c.id""", (self.person_id,)).fetchall()
        total = 0.0
        for row in rows:
            total += row["points"]
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["section"],row["name"],row["unit"],format_points(row["rate"]),"—" if row["cap"] is None else format_points(row["cap"]),format_points(row["quantity"]),format_points(row["points"]),row["evidence"]))
        self.total_label.configure(text=f"Итог: {format_points(total)}")
        if selected and self.tree.exists(selected[0]): self.tree.selection_set(selected[0])

    def select_score(self, _event=None):
        selection = self.tree.selection()
        if not selection: return
        values = self.tree.item(selection[0], "values")
        self.quantity.configure(state="normal"); self.quantity.delete(0, "end"); self.quantity.insert(0, values[5])
        self.evidence.configure(state="normal"); self.evidence.delete("1.0", "end"); self.evidence.insert("1.0", values[7])
        if self.readonly:
            self.quantity.configure(state="disabled"); self.evidence.configure(state="disabled")

    def save_score(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Выберите критерий.", parent=self); return
        try:
            quantity = as_number(self.quantity.get())
            if quantity < 0: raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Количество должно быть неотрицательным числом.", parent=self); return
        criterion_id = int(selection[0])
        criterion = self.connection.execute("SELECT rate,cap FROM criteria WHERE id=?", (criterion_id,)).fetchone()
        points = calculate(criterion["rate"], quantity, criterion["cap"])
        self.connection.execute("""INSERT INTO scores(person_id,criterion_id,quantity,points,evidence,assessed_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(person_id,criterion_id) DO UPDATE SET
            quantity=excluded.quantity,points=excluded.points,evidence=excluded.evidence,assessed_at=CURRENT_TIMESTAMP""",
            (self.person_id, criterion_id, quantity, points, self.evidence.get("1.0", "end").strip()))
        self.connection.commit(); self.load()


class MainWindow:
    def __init__(self, root: tk.Tk, db: Database, user):
        self.root, self.db, self.user = root, db, user
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("1200x700")
        root.minsize(900, 520)
        self.build_menu()
        top = ttk.Frame(root, padding=10); top.pack(fill="x")
        ttk.Label(top, text="Организация").pack(side="left")
        self.org_filter = ttk.Combobox(top, state="readonly", width=32); self.org_filter.pack(side="left", padx=(6, 16))
        self.org_filter.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(top, text="Поиск").pack(side="left")
        self.search = ttk.Entry(top, width=30); self.search.pack(side="left", padx=6)
        self.search.bind("<KeyRelease>", lambda _e: self.refresh())
        ttk.Button(top, text="Обновить", command=self.refresh).pack(side="left", padx=4)
        actions = ttk.Frame(root, padding=(10, 0, 10, 8)); actions.pack(fill="x")
        self.add_btn = ttk.Button(actions, text="Добавить", command=self.add_person); self.add_btn.pack(side="left")
        self.edit_btn = ttk.Button(actions, text="Изменить", command=self.edit_person); self.edit_btn.pack(side="left", padx=5)
        self.score_btn = ttk.Button(actions, text="Оценка", command=self.open_scores); self.score_btn.pack(side="left")
        self.archive_btn = ttk.Button(actions, text="В архив", command=self.archive_person); self.archive_btn.pack(side="left", padx=5)
        columns = ("rank","name","organization","department","position","positive","negative","total")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        labels = ("№","Ф.И.О.","Организация","Подразделение","Должность","Плюс","Минус","Рейтинг")
        widths = (45,245,180,150,190,70,70,80)
        for col,label,width in zip(columns,labels,widths):
            self.tree.heading(col,text=label); self.tree.column(col,width=width,anchor="center" if col in {"rank","positive","negative","total"} else "w")
        scroll = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10,0), pady=(0,10)); scroll.pack(side="left", fill="y", pady=(0,10))
        self.tree.bind("<Double-1>", lambda _e: self.open_scores())
        self.status = ttk.Label(root, relief="sunken", anchor="w"); self.status.pack(side="bottom", fill="x")
        if user["role"] == "viewer":
            for button in (self.add_btn,self.edit_btn,self.archive_btn): button.configure(state="disabled")
        self.load_organizations(); self.refresh()

    def build_menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Экспорт рейтинга в CSV…", command=self.export_csv)
        file_menu.add_command(label="Карточка выбранного в CSV…", command=self.export_person_card)
        file_menu.add_command(label="Резервная копия…", command=self.backup)
        file_menu.add_separator(); file_menu.add_command(label="Выход", command=self.root.destroy)
        menu.add_cascade(label="Файл", menu=file_menu)
        refs = tk.Menu(menu, tearoff=False)
        refs.add_command(label="Организации…", command=self.organizations)
        refs.add_command(label="Пользователи…", command=self.users)
        menu.add_cascade(label="Справочники", menu=refs)
        menu.add_command(label="О программе", command=lambda: messagebox.showinfo(APP_NAME, f"{APP_NAME} {__version__}\nЛокальная база: {self.db.path}"))
        self.root.config(menu=menu)

    def load_organizations(self):
        rows = self.db.connection.execute("SELECT id,name FROM organizations ORDER BY name").fetchall()
        self.organizations_data = [(r["id"],r["name"]) for r in rows]
        current = self.org_filter.get()
        self.org_filter["values"] = ["Все организации"] + [x[1] for x in self.organizations_data]
        self.org_filter.set(current if current in self.org_filter["values"] else "Все организации")

    def refresh(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        params, where = [], ["p.active=1"]
        if self.org_filter.get() and self.org_filter.get() != "Все организации":
            where.append("o.name=?"); params.append(self.org_filter.get())
        query = self.search.get().strip()
        if query:
            where.append("(p.full_name LIKE ? OR p.department LIKE ? OR p.position LIKE ?)"); params += [f"%{query}%"]*3
        sql = f"""SELECT p.id,p.full_name,o.name organization,p.department,p.position,
          COALESCE(SUM(CASE WHEN s.points>0 THEN s.points ELSE 0 END),0) positive,
          COALESCE(SUM(CASE WHEN s.points<0 THEN s.points ELSE 0 END),0) negative,
          COALESCE(SUM(s.points),0) total FROM personnel p JOIN organizations o ON o.id=p.organization_id
          LEFT JOIN scores s ON s.person_id=p.id WHERE {' AND '.join(where)} GROUP BY p.id ORDER BY total DESC,p.full_name"""
        rows = self.db.connection.execute(sql, params).fetchall()
        for rank,row in enumerate(rows,1):
            self.tree.insert("","end",iid=str(row["id"]),values=(rank,row["full_name"],row["organization"],row["department"],row["position"],format_points(row["positive"]),format_points(row["negative"]),format_points(row["total"])))
        self.status.configure(text=f"  Записей: {len(rows)}    Пользователь: {self.user['username']} ({self.user['role']})")

    def selected_id(self):
        selection = self.tree.selection()
        if not selection: messagebox.showinfo(APP_NAME,"Выберите запись."); return None
        return int(selection[0])

    def require_org(self):
        if self.organizations_data: return True
        messagebox.showinfo(APP_NAME,"Сначала добавьте организацию в меню «Справочники»."); return False

    def add_person(self):
        if not self.require_org(): return
        dialog = PersonDialog(self.root,self.organizations_data); self.root.wait_window(dialog)
        if not dialog.result: return
        d=dialog.result
        self.db.connection.execute("""INSERT INTO personnel(organization_id,full_name,birth_date,birth_place,department,position,service_number,notes)
            VALUES(:organization_id,:full_name,:birth_date,:birth_place,:department,:position,:service_number,:notes)""",d)
        self.db.connection.commit(); self.db.audit(self.user["username"],"Добавлена карточка",d["full_name"]); self.refresh()

    def person_record(self, person_id):
        return self.db.connection.execute("SELECT p.*,o.name organization_name FROM personnel p JOIN organizations o ON o.id=p.organization_id WHERE p.id=?",(person_id,)).fetchone()

    def edit_person(self):
        person_id=self.selected_id()
        if not person_id: return
        dialog=PersonDialog(self.root,self.organizations_data,self.person_record(person_id)); self.root.wait_window(dialog)
        if not dialog.result: return
        d=dialog.result; d["id"]=person_id
        self.db.connection.execute("""UPDATE personnel SET organization_id=:organization_id,full_name=:full_name,birth_date=:birth_date,
          birth_place=:birth_place,department=:department,position=:position,service_number=:service_number,notes=:notes,
          updated_at=CURRENT_TIMESTAMP WHERE id=:id""",d)
        self.db.connection.commit(); self.db.audit(self.user["username"],"Изменена карточка",d["full_name"]); self.refresh()

    def archive_person(self):
        person_id=self.selected_id()
        if not person_id: return
        record=self.person_record(person_id)
        if messagebox.askyesno(APP_NAME,f"Переместить «{record['full_name']}» в архив?"):
            self.db.connection.execute("UPDATE personnel SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",(person_id,)); self.db.connection.commit()
            self.db.audit(self.user["username"],"Карточка помещена в архив",record["full_name"]); self.refresh()

    def open_scores(self):
        person_id=self.selected_id()
        if not person_id: return
        record=self.person_record(person_id)
        dialog=ScoreDialog(self.root,self.db.connection,person_id,record["full_name"],self.user["role"]=="viewer")
        self.root.wait_window(dialog); self.db.audit(self.user["username"],"Просмотр/оценка",record["full_name"]); self.refresh()

    def organizations(self):
        if self.user["role"] != "admin": messagebox.showwarning(APP_NAME,"Доступно только администратору."); return
        name=simpledialog.askstring(APP_NAME,"Название новой организации:",parent=self.root)
        if not name: return
        try:
            self.db.connection.execute("INSERT INTO organizations(name) VALUES(?)",(name.strip(),)); self.db.connection.commit(); self.load_organizations(); self.refresh()
        except sqlite3.IntegrityError: messagebox.showwarning(APP_NAME,"Такая организация уже существует.")

    def users(self):
        if self.user["role"] != "admin": messagebox.showwarning(APP_NAME,"Доступно только администратору."); return
        dialog=CredentialsDialog(self.root,"Новый пользователь"); self.root.wait_window(dialog)
        if not dialog.result: return
        role=simpledialog.askstring(APP_NAME,"Роль: admin, operator или viewer",initialvalue="operator",parent=self.root)
        if role not in {"admin","operator","viewer"}: messagebox.showwarning(APP_NAME,"Некорректная роль."); return
        try: self.db.create_user(*dialog.result,role); messagebox.showinfo(APP_NAME,"Пользователь создан.")
        except sqlite3.IntegrityError: messagebox.showwarning(APP_NAME,"Такой пользователь уже существует.")

    def export_csv(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".csv",filetypes=[("CSV","*.csv")],initialfile=f"rating_{date.today().isoformat()}.csv")
        if not path: return
        rows=[self.tree.item(i,"values") for i in self.tree.get_children()]
        with open(path,"w",newline="",encoding="utf-8-sig") as stream:
            writer=csv.writer(stream,delimiter=";"); writer.writerow(("Место","Ф.И.О.","Организация","Подразделение","Должность","Положительные","Отрицательные","Рейтинг")); writer.writerows(rows)
        self.db.audit(self.user["username"],"Экспорт рейтинга",str(path)); messagebox.showinfo(APP_NAME,"Экспорт завершён.")

    def export_person_card(self):
        person_id=self.selected_id()
        if not person_id: return
        person=self.person_record(person_id)
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".csv",filetypes=[("CSV","*.csv")],initialfile=f"card_{person['full_name'].replace(' ','_')}.csv")
        if not path: return
        birth=person["birth_date"] or ""
        age=""
        if birth:
            born=date.fromisoformat(birth); today=date.today(); age=today.year-born.year-((today.month,today.day)<(born.month,born.day))
        rows=self.db.connection.execute("""SELECT c.section,c.name,s.quantity,c.unit,s.points,s.evidence
          FROM criteria c JOIN scores s ON s.criterion_id=c.id WHERE s.person_id=? AND s.quantity>0
          ORDER BY c.section DESC,c.id""",(person_id,)).fetchall()
        total=sum(row["points"] for row in rows)
        with open(path,"w",newline="",encoding="utf-8-sig") as stream:
            writer=csv.writer(stream,delimiter=";")
            writer.writerows((("Рейтинг военнослужащего",format_points(total)),("Ф.И.О.",person["full_name"]),("Возраст",age),("Дата рождения",birth),("Место рождения",person["birth_place"] or ""),("Организация",person["organization_name"]),("Подразделение",person["department"] or ""),("Должность",person["position"] or ""),()))
            writer.writerow(("Раздел","Критерий","Количество","Единица","Балл","Подтверждение"))
            for row in rows: writer.writerow((row["section"],row["name"],format_points(row["quantity"]),row["unit"],format_points(row["points"]),row["evidence"]))
        self.db.audit(self.user["username"],"Экспорт карточки",person["full_name"]); messagebox.showinfo(APP_NAME,"Карточка экспортирована.")

    def backup(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".sqlite3",filetypes=[("SQLite","*.sqlite3")],initialfile=f"digital_rating_backup_{datetime.now():%Y%m%d_%H%M}.sqlite3")
        if not path: return
        target=sqlite3.connect(path)
        try: self.db.connection.backup(target)
        finally: target.close()
        self.db.audit(self.user["username"],"Резервная копия",str(path)); messagebox.showinfo(APP_NAME,"Резервная копия создана.")


def run():
    root=tk.Tk(); root.withdraw()
    try:
        style=ttk.Style(root)
        if "vista" in style.theme_names(): style.theme_use("vista")
        db=Database(data_directory()/"digital_rating.sqlite3")
        first_run=db.initialize()
        if first_run:
            setup=CredentialsDialog(root,"Первый запуск",setup=True); root.wait_window(setup)
            if not setup.result: db.close(); root.destroy(); return
            db.create_user(*setup.result,"admin")
        login=CredentialsDialog(root,"Вход — Цифровой рейтинг"); root.wait_window(login)
        if not login.result: db.close(); root.destroy(); return
        user=db.authenticate(*login.result)
        if not user:
            messagebox.showerror(APP_NAME,"Неверное имя пользователя или пароль."); db.close(); root.destroy(); return
        db.audit(user["username"],"Вход")
        root.deiconify(); MainWindow(root,db,user); root.mainloop(); db.close()
    except Exception as exc:
        messagebox.showerror(APP_NAME,f"Ошибка запуска:\n{exc}")
        root.destroy()
