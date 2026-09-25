import tempfile
import unittest
from pathlib import Path

from rating_app.db import Database
from rating_app.importer import find_conflicts, import_database


class ImportTests(unittest.TestCase):
    def make_db(self, path, name):
        db = Database(path); db.initialize()
        org = db.connection.execute("INSERT INTO organizations(name) VALUES(?)", (name,)).lastrowid
        db.connection.execute("INSERT INTO personnel(organization_id,full_name,birth_date,service_number) VALUES(?,?,?,?)", (org,"Иванов Иван","1990-01-01","A-1"))
        db.connection.commit(); return db

    def test_conflict_and_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.make_db(Path(folder)/"target.sqlite3", "Орг 1")
            source = self.make_db(Path(folder)/"source.sqlite3", "Орг 2")
            conflicts, count = find_conflicts(target.connection, source.path)
            self.assertEqual(count, 1); self.assertEqual(len(conflicts), 1)
            result = import_database(target.connection, source.path, {conflicts[0].source_id: "overwrite"})
            self.assertEqual(result["overwritten"], 1)
            organization = target.connection.execute("""SELECT o.name FROM personnel p JOIN organizations o ON o.id=p.organization_id
                WHERE p.id=?""", (conflicts[0].existing_id,)).fetchone()[0]
            self.assertEqual(organization, "Орг 2")
            source.close(); target.close()


if __name__ == "__main__": unittest.main()
