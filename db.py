"""
db.py — Database abstraction layer for AuraXL Uptime Monitor.

Supports:
  - PostgreSQL (via DATABASE_URL env var, production / Render)
  - SQLite     (fallback for local development)

The schema is initialised automatically on first use.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Detect backend
_USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")))

# SQLite file path (only used when PostgreSQL is not configured)
_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "monitor.db")
_sqlite_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS checks (
    id               SERIAL PRIMARY KEY,
    target_url       TEXT         NOT NULL,
    checked_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    http_status      INTEGER,
    response_time_ms INTEGER,
    is_up            BOOLEAN      NOT NULL DEFAULT FALSE,
    status           TEXT         NOT NULL DEFAULT 'down',
    error_type       TEXT,
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_checks_status     ON checks (status);
CREATE INDEX IF NOT EXISTS idx_checks_target_url ON checks (target_url);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS checks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    target_url       TEXT    NOT NULL,
    checked_at       TEXT    NOT NULL,
    http_status      INTEGER,
    response_time_ms INTEGER,
    is_up            INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'down',
    error_type       TEXT,
    error_message    TEXT,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_checks_status     ON checks (status);
CREATE INDEX IF NOT EXISTS idx_checks_target_url ON checks (target_url);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------
def _pg_conn():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


@contextmanager
def _pg_cursor():
    import psycopg2.extras
    conn = _pg_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------
def _sqlite_conn():
    conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _sqlite_cursor():
    with _sqlite_lock:
        conn = _sqlite_conn()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Unified cursor context manager
# ---------------------------------------------------------------------------
@contextmanager
def get_cursor():
    if _USE_POSTGRES:
        with _pg_cursor() as cur:
            yield cur
    else:
        with _sqlite_cursor() as cur:
            yield cur


def _row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def init_db():
    """Create tables and indexes if they do not exist."""
    schema = _SCHEMA_POSTGRES if _USE_POSTGRES else _SCHEMA_SQLITE
    # Split on ';' and execute each statement individually (required for SQLite)
    statements = [s.strip() for s in schema.split(';') if s.strip()]
    with get_cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    mode = "PostgreSQL" if _USE_POSTGRES else f"SQLite ({_SQLITE_PATH})"
    print(f"[DB] Initialised — backend: {mode}")


def insert_check(target_url, checked_at, http_status, response_time_ms,
                 is_up, status, error_type=None, error_message=None):
    """Insert one monitoring result."""
    now = datetime.now(timezone.utc).isoformat()
    if isinstance(checked_at, datetime):
        checked_at_str = checked_at.isoformat()
    else:
        checked_at_str = str(checked_at)

    # Truncate error messages to avoid storing large payloads
    if error_message and len(error_message) > 500:
        error_message = error_message[:497] + "..."

    if _USE_POSTGRES:
        sql = """
            INSERT INTO checks
              (target_url, checked_at, http_status, response_time_ms,
               is_up, status, error_type, error_message, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (target_url, checked_at_str, http_status, response_time_ms,
                  is_up, status, error_type, error_message, now)
    else:
        sql = """
            INSERT INTO checks
              (target_url, checked_at, http_status, response_time_ms,
               is_up, status, error_type, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (target_url, checked_at_str, http_status, response_time_ms,
                  1 if is_up else 0, status, error_type, error_message, now)

    with get_cursor() as cur:
        cur.execute(sql, params)


def get_latest_check(target_url=None):
    """Return the most recent check row as a dict."""
    if _USE_POSTGRES:
        sql = "SELECT * FROM checks ORDER BY checked_at DESC LIMIT 1"
        params = ()
        if target_url:
            sql = "SELECT * FROM checks WHERE target_url=%s ORDER BY checked_at DESC LIMIT 1"
            params = (target_url,)
    else:
        sql = "SELECT * FROM checks ORDER BY checked_at DESC LIMIT 1"
        params = ()
        if target_url:
            sql = "SELECT * FROM checks WHERE target_url=? ORDER BY checked_at DESC LIMIT 1"
            params = (target_url,)

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return _row_to_dict(row)


def get_checks(limit=50, page=1, status_filter=None, from_ts=None, to_ts=None):
    """Return paginated check rows."""
    offset = (page - 1) * limit
    conditions = []
    params = []

    if status_filter:
        conditions.append("status = %s" if _USE_POSTGRES else "status = ?")
        params.append(status_filter)
    if from_ts:
        conditions.append("checked_at >= %s" if _USE_POSTGRES else "checked_at >= ?")
        params.append(from_ts)
    if to_ts:
        conditions.append("checked_at <= %s" if _USE_POSTGRES else "checked_at <= ?")
        params.append(to_ts)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    placeholder = "%s" if _USE_POSTGRES else "?"

    sql = f"""
        SELECT * FROM checks
        {where_clause}
        ORDER BY checked_at DESC
        LIMIT {placeholder} OFFSET {placeholder}
    """
    params.extend([limit, offset])

    with get_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_stats(hours=None):
    """
    Return aggregate stats for the given time window (hours).
    If hours is None, returns all-time stats.
    """
    if _USE_POSTGRES:
        if hours:
            time_filter = f"WHERE checked_at >= NOW() - INTERVAL '{hours} hours'"
        else:
            time_filter = ""
        sql = f"""
            SELECT
                COUNT(*)                                       AS total_checks,
                SUM(CASE WHEN is_up THEN 1 ELSE 0 END)        AS successful_checks,
                SUM(CASE WHEN NOT is_up THEN 1 ELSE 0 END)    AS failed_checks,
                ROUND(AVG(response_time_ms))                   AS avg_response_ms,
                MIN(response_time_ms)                          AS min_response_ms,
                MAX(response_time_ms)                          AS max_response_ms,
                MAX(CASE WHEN is_up THEN checked_at END)       AS last_success,
                MAX(CASE WHEN NOT is_up THEN checked_at END)   AS last_failure
            FROM checks {time_filter}
        """
    else:
        if hours:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            time_filter = f"WHERE checked_at >= '{cutoff}'"
        else:
            time_filter = ""
        sql = f"""
            SELECT
                COUNT(*)                                              AS total_checks,
                SUM(CASE WHEN is_up=1 THEN 1 ELSE 0 END)             AS successful_checks,
                SUM(CASE WHEN is_up=0 THEN 1 ELSE 0 END)             AS failed_checks,
                ROUND(AVG(response_time_ms))                          AS avg_response_ms,
                MIN(response_time_ms)                                 AS min_response_ms,
                MAX(response_time_ms)                                 AS max_response_ms,
                MAX(CASE WHEN is_up=1 THEN checked_at ELSE NULL END)  AS last_success,
                MAX(CASE WHEN is_up=0 THEN checked_at ELSE NULL END)  AS last_failure
            FROM checks {time_filter}
        """

    with get_cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    result = _row_to_dict(row) or {}
    total = result.get("total_checks") or 0
    successful = result.get("successful_checks") or 0
    result["uptime_pct"] = round(100.0 * successful / total, 2) if total > 0 else None
    return result


def get_consecutive_failures():
    """Count consecutive failures from the most recent check backwards."""
    if _USE_POSTGRES:
        sql = "SELECT is_up FROM checks ORDER BY checked_at DESC LIMIT 100"
    else:
        sql = "SELECT is_up FROM checks ORDER BY checked_at DESC LIMIT 100"

    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    count = 0
    for row in rows:
        r = _row_to_dict(row)
        is_up_val = r.get("is_up")
        # SQLite stores booleans as 0/1
        if is_up_val in (False, 0):
            count += 1
        else:
            break
    return count


def get_setting(key: str, default: str = "") -> str:
    """Get a single setting value."""
    if _USE_POSTGRES:
        sql = "SELECT value FROM settings WHERE key = %s"
        params = (key,)
    else:
        sql = "SELECT value FROM settings WHERE key = ?"
        params = (key,)
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if row:
        r = _row_to_dict(row)
        return r.get("value", default)
    return default


def get_all_settings() -> dict:
    """Return all settings as a dict."""
    defaults = {
        "target_url": os.environ.get("TARGET_URL", "https://auraxl.com"),
        "alert_email": os.environ.get("ALERT_EMAIL", "31pranav104@gmail.com"),
        "monitor_interval_minutes": os.environ.get("MONITOR_INTERVAL_MINUTES", "5"),
        "email_alerts_enabled": os.environ.get("EMAIL_ALERTS_ENABLED", "true"),
        "push_alerts_enabled": "true",
        "smtp_server": os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
        "smtp_port": os.environ.get("SMTP_PORT", "587"),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", "")
    }
    sql = "SELECT key, value FROM settings"
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    for row in rows:
        r = _row_to_dict(row)
        defaults[r["key"]] = r["value"]
    return defaults


def set_setting(key: str, value: str):
    """Set a setting value."""
    if _USE_POSTGRES:
        sql = """
            INSERT INTO settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """
        params = (key, str(value))
    else:
        sql = """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """
        params = (key, str(value))
    with get_cursor() as cur:
        cur.execute(sql, params)

