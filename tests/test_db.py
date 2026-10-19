import sqlite3

from app.db import MIGRATIONS, Database


def test_schema_version(tmp_path):
    db = Database(tmp_path / "x" / "z.db")
    assert db.version == len(MIGRATIONS)
    # Zweites Öffnen migriert nicht erneut
    Database(tmp_path / "x" / "z.db").set_project("CON", 3)
    assert Database(tmp_path / "x" / "z.db").projects() == [
        {"short_name": "CON", "dolibarr_project_id": 3}]


def test_projekt_mapping_upsert(tmp_path):
    db = Database(tmp_path / "z.db")
    db.set_project("CON", None)
    db.set_project("CON", 7)
    db.set_project("ABC", None)
    assert db.projects() == [{"short_name": "ABC", "dolibarr_project_id": None},
                             {"short_name": "CON", "dolibarr_project_id": 7}]
    db.delete_project("ABC")
    assert len(db.projects()) == 1


def test_transfers_je_monat(tmp_path):
    db = Database(tmp_path / "z.db")
    db.mark_transfer("2026-09-01|CON-2", "2026-09-01", "CON-2", 30, "E|1", "Pos", "Text")
    db.mark_transfer("2026-10-01|CON-2", "2026-10-01", "CON-2", 15, "E|1", "Pos", "Text")
    assert list(db.transfers("2026-09")) == ["2026-09-01|CON-2"]
    db.unmark_transfer("2026-09-01|CON-2")
    assert db.transfers("2026-09") == {}


def test_datei_ist_sqlite(tmp_path):
    Database(tmp_path / "z.db")
    con = sqlite3.connect(tmp_path / "z.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"yt_project", "transfer", "dolibarr_task", "dolibarr_time"} <= tables
