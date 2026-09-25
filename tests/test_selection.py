import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rating_app.db import Database
from rating_app.qt_app import MainWindow


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_next_level_and_academic_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.sqlite3")
            db.initialize(); db.create_user("admin", "secret1", "admin")
            org_id = db.connection.execute("INSERT INTO organizations(name) VALUES(?)", ("Компания",)).lastrowid
            director_id = db.connection.execute("INSERT INTO positions(organization_id,name,sort_order) VALUES(?,?,?)", (org_id, "Директор компании", 1)).lastrowid
            deputy_id = db.connection.execute("INSERT INTO positions(organization_id,name,sort_order) VALUES(?,?,?)", (org_id, "Заместитель директора", 2)).lastrowid
            db.connection.execute(
                "INSERT INTO personnel(organization_id,full_name,position,position_id,position_level,academic_education) VALUES(?,?,?,?,?,?)",
                (org_id, "Иванов И.И.", "Заместитель директора", deputy_id, 2, 1),
            )
            db.connection.execute(
                "INSERT INTO vacancies(organization_id,title,position_id,position_level) VALUES(?,?,?,?)",
                (org_id, "Директор компании", director_id, 1),
            )
            db.connection.commit()
            window = MainWindow(db, db.authenticate("admin", "secret1"))
            window.selection.refresh()
            self.assertEqual(window.selection.table.rowCount(), 1)
            window.selection.academic.setChecked(True)
            self.assertEqual(window.selection.table.rowCount(), 1)
            self.assertEqual(window.selection.table.item(0, 1).text(), "Иванов И.И.")
            window.close(); db.close()


if __name__ == "__main__":
    unittest.main()
