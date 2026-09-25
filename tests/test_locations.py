import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rating_app.db import Database
from rating_app.qt_app import MainWindow


class LocationFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_values_filter_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.sqlite3")
            db.initialize(); db.create_user("admin", "secret1", "admin")
            country_a = db.connection.execute("INSERT INTO countries(name) VALUES(?)", ("Страна А",)).lastrowid
            country_b = db.connection.execute("INSERT INTO countries(name) VALUES(?)", ("Страна Б",)).lastrowid
            region_a = db.connection.execute("INSERT INTO regions(country_id,name) VALUES(?,?)", (country_a, "Область А")).lastrowid
            region_b = db.connection.execute("INSERT INTO regions(country_id,name) VALUES(?,?)", (country_b, "Область Б")).lastrowid
            db.connection.execute("INSERT INTO cities(region_id,name) VALUES(?,?)", (region_a, "Город А"))
            db.connection.execute("INSERT INTO cities(region_id,name) VALUES(?,?)", (region_b, "Город Б")); db.connection.commit()
            window = MainWindow(db, db.authenticate("admin", "secret1"))
            window.locations.clear_filter()
            self.assertEqual(window.locations.table.rowCount(), 2)
            window.locations.country_filter.setCurrentIndex(window.locations.country_filter.findData(country_a))
            window.locations.apply_filter()
            self.assertEqual(window.locations.table.rowCount(), 1)
            self.assertEqual(window.locations.table.item(0, 1).text(), "Страна А")
            window.locations.clear_filter()
            self.assertEqual(window.locations.table.rowCount(), 2)
            window.close(); db.close()


if __name__ == "__main__":
    unittest.main()
