from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path

from .scoring import CRITERIA


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS organizations (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS personnel (
  id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  full_name TEXT NOT NULL, birth_date TEXT, birth_place TEXT, department TEXT, position TEXT,
  service_number TEXT, notes TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS criteria (
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, section TEXT NOT NULL, name TEXT NOT NULL,
  rate REAL NOT NULL, cap REAL, unit TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS scores (
  person_id INTEGER NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
  criterion_id INTEGER NOT NULL REFERENCES criteria(id) ON DELETE RESTRICT,
  quantity REAL NOT NULL DEFAULT 0, points REAL NOT NULL DEFAULT 0, evidence TEXT, assessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(person_id, criterion_id)
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL, action TEXT NOT NULL, details TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, sort_order INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, name)
);
CREATE TABLE IF NOT EXISTS departments (
  id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(organization_id, name)
);
CREATE TABLE IF NOT EXISTS countries (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS regions (
  id INTEGER PRIMARY KEY, country_id INTEGER NOT NULL REFERENCES countries(id) ON DELETE CASCADE,
  name TEXT NOT NULL, UNIQUE(country_id, name)
);
CREATE TABLE IF NOT EXISTS cities (
  id INTEGER PRIMARY KEY, region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  name TEXT NOT NULL, UNIQUE(region_id, name)
);
CREATE TABLE IF NOT EXISTS vacancies (
  id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  title TEXT NOT NULL, position_level INTEGER NOT NULL CHECK(position_level BETWEEN 1 AND 99),
  department TEXT, requirements TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    def initialize(self) -> bool:
        self.connection.executescript(SCHEMA)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(personnel)")}
        if "position_level" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN position_level INTEGER NOT NULL DEFAULT 99")
        if "academic_education" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN academic_education INTEGER NOT NULL DEFAULT 0")
        if "position_id" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN position_id INTEGER")
        if "birth_country_id" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN birth_country_id INTEGER")
        if "birth_region_id" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN birth_region_id INTEGER")
        if "birth_city_id" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN birth_city_id INTEGER")
        if "department_id" not in columns:
            self.connection.execute("ALTER TABLE personnel ADD COLUMN department_id INTEGER")
        vacancy_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(vacancies)")}
        if "position_id" not in vacancy_columns:
            self.connection.execute("ALTER TABLE vacancies ADD COLUMN position_id INTEGER")
        # Move legacy free-text positions into the ordered directory without losing data.
        legacy_people = self.connection.execute("""SELECT id,organization_id,position,position_level FROM personnel
            WHERE position_id IS NULL AND TRIM(COALESCE(position,''))<>''""").fetchall()
        for row in legacy_people:
            order = row["position_level"] if row["position_level"] and row["position_level"] < 99 else 1000
            self.connection.execute("INSERT OR IGNORE INTO positions(organization_id,name,sort_order) VALUES(?,?,?)", (row["organization_id"], row["position"], order))
            position_id = self.connection.execute("SELECT id FROM positions WHERE organization_id=? AND name=?", (row["organization_id"], row["position"])).fetchone()[0]
            self.connection.execute("UPDATE personnel SET position_id=? WHERE id=?", (position_id, row["id"]))
        legacy_departments = self.connection.execute("""SELECT id,organization_id,department FROM personnel
            WHERE department_id IS NULL AND TRIM(COALESCE(department,''))<>''""").fetchall()
        for row in legacy_departments:
            self.connection.execute("INSERT OR IGNORE INTO departments(organization_id,name) VALUES(?,?)", (row["organization_id"], row["department"]))
            department_id = self.connection.execute("SELECT id FROM departments WHERE organization_id=? AND name=?", (row["organization_id"], row["department"])).fetchone()[0]
            self.connection.execute("UPDATE personnel SET department_id=? WHERE id=?", (department_id, row["id"]))
        legacy_vacancies = self.connection.execute("""SELECT id,organization_id,title,position_level FROM vacancies
            WHERE position_id IS NULL AND TRIM(COALESCE(title,''))<>''""").fetchall()
        for row in legacy_vacancies:
            self.connection.execute("INSERT OR IGNORE INTO positions(organization_id,name,sort_order) VALUES(?,?,?)", (row["organization_id"], row["title"], row["position_level"]))
            position_id = self.connection.execute("SELECT id FROM positions WHERE organization_id=? AND name=?", (row["organization_id"], row["title"])).fetchone()[0]
            self.connection.execute("UPDATE vacancies SET position_id=? WHERE id=?", (position_id, row["id"]))
        for item in CRITERIA:
            self.connection.execute(
                """INSERT INTO criteria(code,section,name,rate,cap,unit) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET section=excluded.section,name=excluded.name,
                   rate=excluded.rate,cap=excluded.cap,unit=excluded.unit""",
                (item.code, item.section, item.name, item.rate, item.cap, item.unit),
            )
        is_new = self.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        self.connection.commit()
        return is_new

    def create_user(self, username: str, password: str, role: str = "admin") -> None:
        self.connection.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            (username.strip(), password_hash(password), role),
        )
        self.connection.commit()

    def authenticate(self, username: str, password: str):
        row = self.connection.execute(
            "SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)
        ).fetchone()
        return row if row and verify_password(password, row["password_hash"]) else None

    def audit(self, username: str, action: str, details: str = "") -> None:
        self.connection.execute(
            "INSERT INTO audit_log(username,action,details) VALUES(?,?,?)", (username, action, details)
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
