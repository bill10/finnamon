"""Weekly backup: VACUUM INTO FINNAMON_HOME/backups/finnamon-YYYY-MM-DD.db (0600, directory 0700), newest KEEP kept.

    backup.maybe(conn)   from run.cycle: backs up when a week has passed, never raises
    backup.run(conn)     one backup now, then prune

A failure is logged and kept in state.backup_error (status and doctor show it); it never blocks a sync."""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from . import config, store

log = logging.getLogger("finnamon.backup")
KEEP = 8
EVERY_DAYS = 7
RETRY_HOURS = 6   # after a failure (a full disk, say), not a full-size VACUUM INTO every sync


def backup_dir() -> Path:
    return config.home() / "backups"


def backups() -> list[Path]:
    return sorted(backup_dir().glob("finnamon-????-??-??.db"))


def run(conn: sqlite3.Connection, keep: int = KEEP) -> Path:
    d = backup_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    dest = d / f"finnamon-{store.now_local()[:10]}.db"
    tmp = dest.with_name(dest.name + ".tmp")   # not a backup until it is whole: a kill mid-copy leaves only a .tmp
    tmp.unlink(missing_ok=True)
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))   # VACUUM INTO accepts an empty file: it is 0600 from the start
    try:
        conn.execute("VACUUM INTO ?", (str(tmp),))
        fd = os.open(tmp, os.O_RDONLY)   # SQLite does not sync a VACUUM INTO target
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, dest)   # a second run the same day replaces that day's copy, only once the new one is whole
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for old in backups()[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def maybe(conn: sqlite3.Connection) -> Path | None:
    try:
        last, err = store.get_state(conn, "last_backup_at"), store.get_state(conn, "backup_error")
        if err and conn.execute("SELECT ? > datetime('now','localtime', ?)", (err[:19], f"-{RETRY_HOURS} hours")).fetchone()[0]:
            return None
        if last and conn.execute("SELECT ? > datetime('now','localtime', ?)", (last, f"-{EVERY_DAYS} days")).fetchone()[0]:
            return None
        dest = run(conn)
        store.set_state(conn, "last_backup_at", store.now_local())
        store.set_state(conn, "backup_error", None)
        log.info("backed up to %s", dest)
        return dest
    except Exception as e:  # noqa: BLE001 - a backup must never cost a sync
        log.exception("backup failed")
        try:
            store.set_state(conn, "backup_error", f"{store.now_local()} {type(e).__name__}: {e}"[:300])
        except sqlite3.Error:
            pass
        return None
