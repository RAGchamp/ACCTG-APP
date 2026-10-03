"""Open and initialise a business's books (one SQLite file, plan §8)."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from books import coa

SCHEMA = Path(__file__).with_name("schema.sql")

DEFAULT_PROFILE = {
    # Confirmed decisions, plan §18.
    "name": "Hartford Slice Pizza LLC",
    "address": "Hartford, CT",
    "entity": "SMLLC",                    # D1: single-member LLC, disregarded -> Schedule C/SE
    "ein": "",
    "ct_tax_registration": "",
    "tax_year": 2026,
    "books_start": "2026-01-01",          # D7
    "basis": "accrual",                   # D2 (reports offer a cash-basis view)
    "sales_tax_frequency": "monthly",     # D6
    "capitalization_threshold": "2500.00",  # D9
    "pos": "Square",                      # D3
    "platforms": ["DoorDash", "Uber Eats", "Grubhub"],   # D3b
    "payroll_provider": "Payroll provider",               # D5
    "delivery_fee_taxable": True,         # D8
    "cpa_review": True,                   # D10
    "card_last4": [],
    "bank_last4": [],
    "owner_name": "",
}


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path):
    """A connection that commits on success and rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path, profile=None):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with session(db_path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        coa.load_starter(conn)
        if not conn.execute("SELECT 1 FROM settings WHERE key='profile'").fetchone():
            merged = dict(DEFAULT_PROFILE, **(profile or {}))
            conn.execute("INSERT INTO settings(key, value) VALUES ('profile', ?)", (json.dumps(merged),))


_READY = set()


def ensure_schema(db_path):
    """Bring books made by an older version up to date (new tables, e.g. the ledger trail) and
    back-fill the trail once. Runs once per database per process."""
    key = str(db_path)
    if key in _READY:
        return
    from books import trail
    with session(db_path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        trail.backfill(conn)
    _READY.add(key)


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def profile(conn):
    return dict(DEFAULT_PROFILE, **(get_setting(conn, "profile", {}) or {}))


def audit(conn, action, **detail):
    conn.execute("INSERT INTO audit_log(at, action, detail) VALUES (?,?,?)",
                 (now(), action, json.dumps(detail, default=str)))
