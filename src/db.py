"""Small thread-safe SQLite wrapper."""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS chambers(
  id INTEGER PRIMARY KEY, name TEXT, crop TEXT, setpoint REAL, temp_max REAL, temp_min REAL, call_list TEXT);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, ts REAL, temp REAL, humidity REAL,
  door INTEGER, comp INTEGER, level TEXT, score REAL, prev_hash TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS idx_readings ON readings(chamber_id, ts);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, ts REAL, level TEXT, code TEXT,
  message_hi TEXT, message_en TEXT, temp REAL, resolved_ts REAL, acked_by TEXT, acked_ts REAL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, alert_id INTEGER, chamber_id INTEGER, channel TEXT,
  to_name TEXT, to_phone TEXT, message TEXT, status TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS sites(
  id INTEGER PRIMARY KEY, name TEXT, location TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, salt TEXT, pw_hash TEXT, role TEXT, created_ts REAL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER, expires_ts REAL);
CREATE TABLE IF NOT EXISTS maintenance(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, task TEXT, interval_run_hours REAL, interval_days REAL,
  last_done_ts REAL, last_done_run_hours REAL, notified_ts REAL);
CREATE TABLE IF NOT EXISTS lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chamber_id INTEGER, farmer_name TEXT, farmer_phone TEXT,
  bags INTEGER, loaded_ts REAL, withdrawn_ts REAL);
CREATE TABLE IF NOT EXISTS applications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, job_title TEXT, department TEXT, name TEXT, phone TEXT, email TEXT,
  org TEXT, link TEXT, message TEXT, status TEXT DEFAULT 'new', note TEXT, updated_by TEXT, updated_ts REAL);
CREATE TABLE IF NOT EXISTS suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT DEFAULT 'suggestion', name TEXT, contact TEXT, topic TEXT,
  message TEXT, status TEXT DEFAULT 'new', note TEXT, updated_by TEXT, updated_ts REAL);
"""


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            # additive migrations so databases created by older versions keep working
            self.ensure_column("readings", "current_a", "REAL")
            self.ensure_column("chambers", "site_id", "INTEGER DEFAULT 1")
            self.ensure_column("chambers", "run_hours", "REAL DEFAULT 0")
            self.ensure_column("lots", "token", "TEXT")
            self.ensure_column("users", "site_id", "INTEGER")      # site_manager: the one site this user may see
            self.ensure_column("users", "phone", "TEXT")           # farmer: matches lots.farmer_phone
            self.conn.commit()

    def ensure_column(self, table: str, column: str, decl: str):
        cols = [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def execute(self, sql: str, params=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None
