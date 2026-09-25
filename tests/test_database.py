import tempfile
import unittest
from pathlib import Path

from rating_app.db import Database


class DatabaseTests(unittest.TestCase):
    def test_initialize_and_authenticate(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.sqlite3")
            self.assertTrue(db.initialize())
            self.assertGreater(db.connection.execute("SELECT COUNT(*) FROM criteria").fetchone()[0], 30)
            personnel_columns = {row["name"] for row in db.connection.execute("PRAGMA table_info(personnel)")}
            self.assertIn("position_id", personnel_columns)
            self.assertIn("birth_city_id", personnel_columns)
            self.assertIn("department_id", personnel_columns)
            self.assertIsNotNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='positions'").fetchone())
            self.assertIsNotNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='cities'").fetchone())
            self.assertIsNotNone(db.connection.execute("SELECT name FROM sqlite_master WHERE name='departments'").fetchone())
            db.create_user("admin", "secure-pass")
            self.assertIsNotNone(db.authenticate("admin", "secure-pass"))
            self.assertIsNone(db.authenticate("admin", "wrong"))
            db.close()


if __name__ == "__main__":
    unittest.main()
