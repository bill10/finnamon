"""SQLite access: WAL, busy_timeout, numbered migrations, settings and state helpers.

    conn = store.connect()          # opens (creating + migrating) the household DB
    store.setting(conn, 'dup_min_amount')            # global
    store.setting(conn, 'low_balance_threshold', account_id=...)  # per-account override
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import logging

from . import config

log = logging.getLogger("finnamon.store")

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))   # an empty file is a new database; never 0644, even from `status` before `init`
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we use explicit BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):   # a database made 0644 by an older version
        try:
            if f.stat().st_mode & 0o077:
                os.chmod(f, 0o600)
        except OSError:
            pass
    return conn


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = True):
    """BEGIN [IMMEDIATE] / COMMIT, ROLLBACK on any exception. The one way to write."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    pending = [f for f in sorted(MIGRATIONS.glob("*.sql")) if f.name not in done]
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if pending and done and db_file:
        # Real data exists: one snapshot before touching it. Forward-only migrations have no down step.
        # Several connections may race here (the daemon opens three); the loser's VACUUM INTO fails on the existing file.
        db = Path(db_file)
        snap = db.with_name(f"{db.stem}.pre-{pending[0].stem}.db")
        if not snap.exists():
            try:
                conn.execute("VACUUM INTO ?", (str(snap),))
            except sqlite3.OperationalError as e:
                if not snap.exists():
                    raise
                log.info("snapshot already taken by another connection: %s", e)
    applied = []
    for f in pending:
        # One migration = one IMMEDIATE transaction: concurrent connections (the daemon opens three)
        # serialize here, and the loser re-checks and skips. A crash mid-way leaves it unapplied.
        with tx(conn):
            if conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (f.name,)).fetchone():
                continue
            for stmt in _split(f.read_text()):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (f.name,))
        applied.append(f.name)
    return applied


def _split(sql: str) -> list[str]:
    """Split into statements with sqlite3.complete_statement, which understands quotes, comments, and triggers."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt.strip(";").strip():
                out.append(stmt)
            buf = ""
    tail = buf.strip()
    if tail and any(l.strip() and not l.strip().startswith("--") for l in tail.splitlines()):
        out.append(tail)  # an unterminated last statement
    return out


# --- settings -------------------------------------------------------------------------------

def parse_amount(text, what: str = "amount") -> float:
    """Dollars as a person types them: 1200, 1,200.50, $900, -$5, "$ 1 200". The one parser every typed amount goes through."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        v = float(text)
    else:
        t = re.sub(r"[\s,]", "", str(text or ""))
        neg = t.startswith("-")
        t = t.lstrip("-+").lstrip("$")
        try:
            v = -float(t) if neg else float(t)
        except ValueError:
            raise ValueError(f"{what} must be a number of dollars, like 1200 or $1,200") from None
    return v   # nan and inf parse; each caller's range check refuses them


def setting_num(conn: sqlite3.Connection, key: str, default: float, account_id: str | None = None) -> float:
    """A numeric setting, clamped to its spec; a bad row can never break the daemon."""
    v = setting(conn, key, account_id)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    spec = SETTINGS.get(key) or OPS_SETTINGS.get(key)
    return min(max(f, spec[0]), spec[1]) if spec else f


def setting(conn: sqlite3.Connection, key: str, account_id: str | None = None, default=None):
    if account_id:
        r = conn.execute("SELECT value FROM settings WHERE account_id=? AND key=?", (account_id, key)).fetchone()
        if r:
            return r[0]
    r = conn.execute("SELECT value FROM settings WHERE account_id='*' AND key=?", (key,)).fetchone()
    return r[0] if r else default


# key → (min, max). Operational keys change how the daemon behaves and are not on Claude's allowlist (cli --ops).
SETTINGS: dict[str, tuple[float, float]] = {
    "dup_min_amount": (0, 1e6), "dup_window_days": (0, 30), "budget_min_day": (1, 28), "outlier_multiplier": (1, 100),
    "outlier_min_amount": (0, 1e6), "large_amount": (0, 1e7), "xfer_window_days": (0, 30), "low_balance_threshold": (0, 1e8),
    "import_max_age_days": (1, 365),   # how long a manual account may go without an import before sync_health says so
}
OPS_SETTINGS: dict[str, tuple[float, float]] = {
    "sync_interval_hours": (1, 48), "claude_timeout_seconds": (30, 900), "health_max_age_hours": (1, 168), "lookback_days": (1, 90),
}


SETTING_HELP = {   # what each key does, in the words `finnamon settings` prints
    "dup_min_amount": "smallest charge ($) the duplicate-charge check looks at",
    "dup_window_days": "days apart two identical charges can be and still count as a possible duplicate",
    "budget_min_day": "day of the month before budget pace alerts start (early in the month a projection is noise)",
    "outlier_multiplier": "how many times a merchant's usual amount a charge must be to be flagged as unusual",
    "outlier_min_amount": "smallest charge ($) the unusual-amount check looks at",
    "large_amount": "smallest one-way transfer ($) the unmatched-transfer check looks at; it does not watch big purchases",
    "xfer_window_days": "days within which a transfer's other half must show up on one of your accounts",
    "low_balance_threshold": "balance ($) a checking or savings account may fall to before a low-balance alert (set per account with `finnamon threshold`)",
    "import_max_age_days": "days a manual account may go without a CSV import before it is reported as stale",
    "sync_interval_hours": "hours between bank syncs (1 to 12; the daemon's timing)",
    "claude_timeout_seconds": "seconds the assistant gets to answer one message or triage run",
    "health_max_age_hours": "hours without a sync before a bank is reported as not syncing",
    "lookback_days": "days back the detectors look for new charges, subscriptions and price changes",
}


def validate_setting(key: str, value, ops: bool = False) -> str:
    """Numeric, in range, and known. Raises ValueError otherwise. Returns the canonical string."""
    spec = SETTINGS.get(key) or (OPS_SETTINGS.get(key) if ops else None)
    if key in OPS_SETTINGS and not ops:
        raise ValueError(f"{key} is an operational setting; use `finnamon settings set {key} <value> --ops` at a terminal")
    if not spec:
        raise ValueError(f"unknown setting {key}; one of {', '.join([*SETTINGS, *OPS_SETTINGS])}")
    try:
        v = parse_amount(value, key)
    except ValueError:
        raise ValueError(f"{key} must be a number") from None
    lo, hi = spec
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
    if key == "sync_interval_hours" and v > 12:
        raise ValueError("sync_interval_hours is capped at 12 so a bank that stops syncing is noticed: the health check "
                         "(health_max_age_hours, 12 by default) reports a bank with no sync in that long")
    return f"{v:.2f}".rstrip("0").rstrip(".")  # money-safe: cents kept, no exponent form


def set_setting(conn: sqlite3.Connection, key: str, value, account_id: str = "*", ops: bool = False) -> None:
    if key in OPS_SETTINGS and not ops:
        raise ValueError(f"{key} is an operational setting; use `finnamon settings set {key} <value> --ops` at a terminal")
    if value is None:
        # None means "unset the per-account override": the global row is the default and must always exist.
        if account_id == "*":
            raise ValueError(f"global {key} cannot be unset; set a value instead")
        conn.execute("DELETE FROM settings WHERE account_id=? AND key=?", (account_id, key))
        return
    value = validate_setting(key, value, ops)
    conn.execute(
        "INSERT INTO settings(account_id, key, value, updated_at) VALUES (?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(account_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (account_id, key, None if value is None else str(value)),
    )


def settings_list(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT account_id, key, value, updated_at FROM settings ORDER BY account_id, key").fetchall()


# --- state ----------------------------------------------------------------------------------

def get_state(conn: sqlite3.Connection, key: str, default=None):
    r = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_state(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO state(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, None if value is None else str(value)))


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# One transaction, one alert: every alert on the same transaction shares this group, whichever detectors raised it, so
# delivery, triage and resolution treat them as one. An anomaly with no transaction_id falls back to account + date +
# amount + name; anything else (a stream, a bank, a budget) is its own group.
ALERT_GROUP = ("COALESCE(transaction_id, CASE WHEN tier='anomaly' AND json_extract(payload_json,'$.date') IS NOT NULL "
               "AND json_extract(payload_json,'$.amount') IS NOT NULL THEN 'tx:' || COALESCE(account_id,'') || '|' || json_extract(payload_json,'$.date') "
               "|| '|' || json_extract(payload_json,'$.amount') || '|' || lower(trim(COALESCE(json_extract(payload_json,'$.merchant'), "
               "json_extract(payload_json,'$.name'), ''))) END, key)")


def alert_group(conn: sqlite3.Connection, alert_id: int) -> list[int]:
    """Ids of every alert on the same transaction as this one, itself included."""
    return [r[0] for r in conn.execute(f"SELECT id FROM alerts WHERE {ALERT_GROUP} = (SELECT {ALERT_GROUP} FROM alerts WHERE id=?) ORDER BY id",
                                       (alert_id,))] or [alert_id]
