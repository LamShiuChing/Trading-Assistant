"""SQLite journal: one row per bracket sent, plus the per-day loss lockout. Never stores credentials."""
import sqlite3
from datetime import datetime

from .config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,            -- ET trading date
    created TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,           -- BUY (long) / SELL (short)
    qty INTEGER NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL,                  -- NULL when trailing
    tp_method TEXT NOT NULL,
    risk REAL NOT NULL,           -- $ at stop
    parent_id INTEGER,            -- IBKR orderId of the entry
    status TEXT NOT NULL          -- submitted / filled / cancelled
);
CREATE TABLE IF NOT EXISTS lockouts (day TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL);
"""

conn = sqlite3.connect(DATA / "journal.db", isolation_level=None, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")
