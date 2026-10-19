"""SQLite-Speicher: Mapping, „übertragen“-Häkchen, Dolibarr-IDs.

Schema-Version über PRAGMA user_version; jede Migration ist ein Eintrag in
MIGRATIONS und läuft genau einmal.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS = [
    # 1
    """
    CREATE TABLE yt_project (
        short_name TEXT PRIMARY KEY,
        dolibarr_project_id INTEGER
    );
    -- Snapshot einer als übertragen markierten Zeile (für „geändert“ alt → neu)
    CREATE TABLE transfer (
        row_key TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        target TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        ext_key TEXT,
        label TEXT NOT NULL,
        description TEXT NOT NULL,
        marked_at TEXT NOT NULL
    );
    -- Von zeitbuch angelegte Dolibarr-Aufgaben
    CREATE TABLE dolibarr_task (
        yt_issue TEXT PRIMARY KEY,
        task_id INTEGER NOT NULL,
        dolibarr_project_id INTEGER NOT NULL,
        parent_yt_issue TEXT,
        label TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    -- Von zeitbuch angelegte Dolibarr-Zeitbuchungen, je Datum × Ziel
    CREATE TABLE dolibarr_time (
        row_key TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        target TEXT NOT NULL,
        task_id INTEGER NOT NULL,
        time_id INTEGER NOT NULL,
        seconds INTEGER NOT NULL,
        note TEXT NOT NULL,
        synced_at TEXT NOT NULL
    );
    """,
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._migrate()

    @contextmanager
    def conn(self):
        with self._lock:
            con = sqlite3.connect(self.path)
            con.row_factory = sqlite3.Row
            try:
                with con:
                    yield con
            finally:
                con.close()

    def _migrate(self):
        with self.conn() as con:
            con.execute("PRAGMA journal_mode=WAL")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                con.executescript(sql)
                con.execute(f"PRAGMA user_version = {i}")

    @property
    def version(self):
        with self.conn() as con:
            return con.execute("PRAGMA user_version").fetchone()[0]

    # ------------------------------------------------------------ Mapping
    def projects(self):
        with self.conn() as con:
            rows = con.execute("SELECT * FROM yt_project ORDER BY short_name").fetchall()
        return [dict(r) for r in rows]

    def set_project(self, short_name, dolibarr_project_id):
        with self.conn() as con:
            con.execute(
                "INSERT INTO yt_project (short_name, dolibarr_project_id) VALUES (?, ?) "
                "ON CONFLICT(short_name) DO UPDATE SET dolibarr_project_id = excluded.dolibarr_project_id",
                (short_name, dolibarr_project_id))

    def delete_project(self, short_name):
        with self.conn() as con:
            con.execute("DELETE FROM yt_project WHERE short_name = ?", (short_name,))

    # ------------------------------------------------------------ übertragen
    def transfers(self, month):
        with self.conn() as con:
            rows = con.execute("SELECT * FROM transfer WHERE date LIKE ? ORDER BY date, target",
                               (f"{month}-%",)).fetchall()
        return {r["row_key"]: dict(r) for r in rows}

    def mark_transfer(self, row_key, date, target, minutes, ext_key, label, description):
        with self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO transfer (row_key, date, target, minutes, ext_key, label, "
                "description, marked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_key, date, target, minutes, ext_key, label, description, now()))

    def unmark_transfer(self, row_key):
        with self.conn() as con:
            con.execute("DELETE FROM transfer WHERE row_key = ?", (row_key,))

    # ------------------------------------------------------------ Dolibarr
    def tasks(self):
        with self.conn() as con:
            rows = con.execute("SELECT * FROM dolibarr_task").fetchall()
        return {r["yt_issue"]: dict(r) for r in rows}

    def save_task(self, yt_issue, task_id, dolibarr_project_id, parent_yt_issue, label):
        with self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO dolibarr_task (yt_issue, task_id, dolibarr_project_id, "
                "parent_yt_issue, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (yt_issue, task_id, dolibarr_project_id, parent_yt_issue, label, now()))

    def set_task_label(self, yt_issue, label):
        with self.conn() as con:
            con.execute("UPDATE dolibarr_task SET label = ? WHERE yt_issue = ?", (label, yt_issue))

    def forget_task(self, yt_issue):
        """Nur die lokale Zuordnung, wenn die Aufgabe in Dolibarr nicht mehr existiert."""
        with self.conn() as con:
            con.execute("DELETE FROM dolibarr_task WHERE yt_issue = ?", (yt_issue,))

    def times(self, month):
        with self.conn() as con:
            rows = con.execute("SELECT * FROM dolibarr_time WHERE date LIKE ? ORDER BY date, target",
                               (f"{month}-%",)).fetchall()
        return {r["row_key"]: dict(r) for r in rows}

    def save_time(self, row_key, date, target, task_id, time_id, seconds, note):
        with self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO dolibarr_time (row_key, date, target, task_id, time_id, "
                "seconds, note, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_key, date, target, task_id, time_id, seconds, note, now()))

    def forget_time(self, row_key):
        """Nur die lokale Zuordnung, wenn die Buchung in Dolibarr nicht mehr existiert."""
        with self.conn() as con:
            con.execute("DELETE FROM dolibarr_time WHERE row_key = ?", (row_key,))
