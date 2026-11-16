"""SQLite persistence layer.

Uses only the standard library so the demo needs no ORM. All SQL is parameterised and isolated in
this module, so moving to PostgreSQL later means swapping this file (and the `?` placeholders), not the
service layer, which only sees plain dicts / DataFrames.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS clinics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, catchment_pop INTEGER NOT NULL,
    lead_time_days INTEGER NOT NULL, road_speed_kmh REAL NOT NULL,
    health_worker TEXT NOT NULL, phone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weather (
    clinic_id TEXT NOT NULL REFERENCES clinics(id), week_start TEXT NOT NULL,
    rain_mm REAL NOT NULL, temp_c REAL NOT NULL,
    PRIMARY KEY (clinic_id, week_start)
);

CREATE TABLE IF NOT EXISTS cases (
    clinic_id TEXT NOT NULL REFERENCES clinics(id), disease TEXT NOT NULL,
    week_start TEXT NOT NULL, cases INTEGER NOT NULL,
    PRIMARY KEY (clinic_id, disease, week_start)
);

CREATE TABLE IF NOT EXISTS consumption (
    clinic_id TEXT NOT NULL REFERENCES clinics(id), medicine_id TEXT NOT NULL,
    week_start TEXT NOT NULL, units INTEGER NOT NULL,
    PRIMARY KEY (clinic_id, medicine_id, week_start)
);

CREATE TABLE IF NOT EXISTS inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id TEXT NOT NULL REFERENCES clinics(id), medicine_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    expiry_date TEXT NOT NULL, received_date TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'initial'
);
CREATE INDEX IF NOT EXISTS idx_batches_pair ON inventory_batches (clinic_id, medicine_id);

CREATE TABLE IF NOT EXISTS inventory_policy (
    clinic_id TEXT NOT NULL REFERENCES clinics(id), medicine_id TEXT NOT NULL,
    min_safety_stock INTEGER NOT NULL CHECK (min_safety_stock >= 0),
    PRIMARY KEY (clinic_id, medicine_id)
);

CREATE TABLE IF NOT EXISTS alert_state (
    alert_key TEXT PRIMARY KEY, acknowledged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sms_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, alert_key TEXT NOT NULL,
    clinic_id TEXT NOT NULL, to_number TEXT NOT NULL, body TEXT NOT NULL,
    mode TEXT NOT NULL, status TEXT NOT NULL, provider_sid TEXT, error TEXT
);

CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_key TEXT NOT NULL,
    medicine_id TEXT NOT NULL, donor_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
    quantity INTEGER NOT NULL, batches_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved','rejected')),
    decided_at TEXT NOT NULL, note TEXT
);
"""

DATA_TABLES = [
    "alert_state", "sms_log", "transfers", "inventory_policy", "inventory_batches",
    "consumption", "cases", "weather", "clinics", "meta",
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def drop_all(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in DATA_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute("PRAGMA foreign_keys = ON")


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    r = conn.execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def frame(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=tuple(params))


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
