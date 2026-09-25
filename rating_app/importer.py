from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportConflict:
    source_id: int
    existing_id: int
    source_name: str
    existing_name: str
    source_org: str
    existing_org: str
    reason: str


class ImportError(Exception):
    pass


REQUIRED_TABLES = {"organizations", "personnel", "criteria", "scores"}


def _open_source(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise ImportError("Выбранный файл не найден.")
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES.issubset(tables):
            source.close()
            raise ImportError("Это не база программы «Цифровой рейтинг» или её структура устарела.")
        return source
    except sqlite3.DatabaseError as exc:
        raise ImportError(f"Не удалось открыть SQLite-базу: {exc}") from exc


def find_conflicts(target: sqlite3.Connection, source_path: Path) -> tuple[list[ImportConflict], int]:
    source = _open_source(source_path)
    try:
        source_rows = source.execute("""SELECT p.*,o.name organization_name FROM personnel p
            JOIN organizations o ON o.id=p.organization_id WHERE p.active=1""").fetchall()
        conflicts: list[ImportConflict] = []
        for row in source_rows:
            existing = None
            reason = ""
            if row["service_number"]:
                existing = target.execute("""SELECT p.*,o.name organization_name FROM personnel p
                    JOIN organizations o ON o.id=p.organization_id WHERE p.service_number=? AND p.service_number<>''
                    LIMIT 1""", (row["service_number"],)).fetchone()
                reason = "совпадает личный номер"
            if existing is None:
                existing = target.execute("""SELECT p.*,o.name organization_name FROM personnel p
                    JOIN organizations o ON o.id=p.organization_id
                    WHERE lower(p.full_name)=lower(?) AND COALESCE(p.birth_date,'')=COALESCE(?,'') LIMIT 1""",
                    (row["full_name"], row["birth_date"])).fetchone()
                reason = "совпадают Ф.И.О. и дата рождения"
            if existing:
                conflicts.append(ImportConflict(row["id"], existing["id"], row["full_name"], existing["full_name"], row["organization_name"], existing["organization_name"], reason))
        return conflicts, len(source_rows)
    finally:
        source.close()


def import_database(target: sqlite3.Connection, source_path: Path, decisions: dict[int, str]) -> dict[str, int]:
    """Import people and scores. Decisions are skip, overwrite, or copy."""
    source = _open_source(source_path)
    stats = {"added": 0, "overwritten": 0, "skipped": 0}
    try:
        conflicts, _ = find_conflicts(target, source_path)
        conflict_map = {item.source_id: item for item in conflicts}
        org_cache: dict[str, int] = {}
        criterion_map = {r["code"]: r["id"] for r in target.execute("SELECT id,code FROM criteria")}
        source_criteria = {r["id"]: r["code"] for r in source.execute("SELECT id,code FROM criteria")}
        source_rows = source.execute("""SELECT p.*,o.name organization_name FROM personnel p
            JOIN organizations o ON o.id=p.organization_id WHERE p.active=1 ORDER BY p.id""").fetchall()
        source_personnel_columns = {row["name"] for row in source.execute("PRAGMA table_info(personnel)")}
        source_tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with target:
            for row in source_rows:
                conflict = conflict_map.get(row["id"])
                action = decisions.get(row["id"], "skip" if conflict else "copy")
                if action == "skip":
                    stats["skipped"] += 1
                    continue
                org_name = row["organization_name"]
                if org_name not in org_cache:
                    target.execute("INSERT OR IGNORE INTO organizations(name) VALUES(?)", (org_name,))
                    org_cache[org_name] = target.execute("SELECT id FROM organizations WHERE name=?", (org_name,)).fetchone()[0]
                position_level = row["position_level"] if "position_level" in source_personnel_columns else 99
                academic_education = row["academic_education"] if "academic_education" in source_personnel_columns else 0
                position_name = row["position"] or "Должность не указана"
                position_order = position_level
                if "positions" in source_tables and "position_id" in source_personnel_columns and row["position_id"]:
                    source_position = source.execute("SELECT name,sort_order FROM positions WHERE id=?", (row["position_id"],)).fetchone()
                    if source_position:
                        position_name, position_order = source_position["name"], source_position["sort_order"]
                target.execute("INSERT OR IGNORE INTO positions(organization_id,name,sort_order) VALUES(?,?,?)", (org_cache[org_name], position_name, position_order))
                target_position_id = target.execute("SELECT id FROM positions WHERE organization_id=? AND name=?", (org_cache[org_name], position_name)).fetchone()[0]
                department_name = row["department"] or "Без подразделения"
                target.execute("INSERT OR IGNORE INTO departments(organization_id,name) VALUES(?,?)", (org_cache[org_name], department_name))
                target_department_id = target.execute("SELECT id FROM departments WHERE organization_id=? AND name=?", (org_cache[org_name], department_name)).fetchone()[0]
                birth_country_id = birth_region_id = birth_city_id = None
                if {"countries", "regions", "cities"}.issubset(source_tables) and "birth_city_id" in source_personnel_columns and row["birth_city_id"]:
                    source_location = source.execute("""SELECT co.name country,r.name region,c.name city FROM cities c
                        JOIN regions r ON r.id=c.region_id JOIN countries co ON co.id=r.country_id WHERE c.id=?""", (row["birth_city_id"],)).fetchone()
                    if source_location:
                        target.execute("INSERT OR IGNORE INTO countries(name) VALUES(?)", (source_location["country"],))
                        birth_country_id = target.execute("SELECT id FROM countries WHERE name=?", (source_location["country"],)).fetchone()[0]
                        target.execute("INSERT OR IGNORE INTO regions(country_id,name) VALUES(?,?)", (birth_country_id, source_location["region"]))
                        birth_region_id = target.execute("SELECT id FROM regions WHERE country_id=? AND name=?", (birth_country_id, source_location["region"])).fetchone()[0]
                        target.execute("INSERT OR IGNORE INTO cities(region_id,name) VALUES(?,?)", (birth_region_id, source_location["city"]))
                        birth_city_id = target.execute("SELECT id FROM cities WHERE region_id=? AND name=?", (birth_region_id, source_location["city"])).fetchone()[0]
                values = (org_cache[org_name], row["full_name"], row["birth_date"], row["birth_place"], birth_country_id, birth_region_id, birth_city_id, department_name, target_department_id, position_name, target_position_id, position_order, academic_education, row["service_number"], row["notes"], row["active"])
                if action == "overwrite" and conflict:
                    person_id = conflict.existing_id
                    target.execute("""UPDATE personnel SET organization_id=?,full_name=?,birth_date=?,birth_place=?,birth_country_id=?,birth_region_id=?,birth_city_id=?,department=?,department_id=?,position=?,position_id=?,position_level=?,academic_education=?,
                        service_number=?,notes=?,active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""", values + (person_id,))
                    target.execute("DELETE FROM scores WHERE person_id=?", (person_id,))
                    stats["overwritten"] += 1
                else:
                    cursor = target.execute("""INSERT INTO personnel(organization_id,full_name,birth_date,birth_place,birth_country_id,birth_region_id,birth_city_id,department,department_id,position,position_id,position_level,academic_education,
                        service_number,notes,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                    person_id = cursor.lastrowid
                    stats["added"] += 1
                for score in source.execute("SELECT * FROM scores WHERE person_id=?", (row["id"],)):
                    code = source_criteria.get(score["criterion_id"])
                    criterion_id = criterion_map.get(code)
                    if criterion_id:
                        target.execute("""INSERT INTO scores(person_id,criterion_id,quantity,points,evidence,assessed_at)
                            VALUES(?,?,?,?,?,?)""", (person_id, criterion_id, score["quantity"], score["points"], score["evidence"], score["assessed_at"]))
        return stats
    finally:
        source.close()
