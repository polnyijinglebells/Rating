import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rating_app.db import Database
from rating_app.qt_app import MainWindow, bundled_resource


class ScoringReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_reference_contains_document_criteria(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/"test.sqlite3"); db.initialize(); db.create_user("admin","secret1","admin")
            window=MainWindow(db,db.authenticate("admin","secret1"))
            self.assertGreater(window.scoring_reference.table.rowCount(),30)
            self.assertEqual(window.directories.tabs.count(),4)
            self.assertEqual([window.directories.tabs.tabText(i) for i in range(4)],["Должности","Места рождения","Баллы","Подразделения"])
            self.assertTrue(bundled_resource("rules.docx").exists())
            window.close(); db.close()


if __name__ == "__main__": unittest.main()
