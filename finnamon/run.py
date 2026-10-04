"""One watchdog cycle: flock → sync → detect → triage → notify → roundup → weekly backup. Zero tokens unless
anomaly candidates exist. Any unhandled exception becomes a deduped health:run alert.

    run.cycle(conn)   used by the daemon's sync thread and by `finnamon run`
"""
from __future__ import annotations

import fcntl
import json
import logging
import sqlite3
import traceback

from . import backup, charts, config, detect, notify, store, sync, triage

log = logging.getLogger("finnamon.run")


class Locked(Exception):
    pass


def lock():
    """The run lock: one sync-shaped writer at a time (cycle, link --remove). Raises Locked."""
    config.lock_path().parent.mkdir(parents=True, exist_ok=True)
    f = open(config.lock_path(), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise Locked("another run is in progress")
    return f


def cycle(conn: sqlite3.Connection, do_sync: bool = True, do_triage: bool = True, do_notify: bool = True) -> dict:
    held = lock()
    as_of = store.now_local()
    result = {"as_of": as_of, "sync": [], "alerts": [], "sent": 0, "roundup": None, "error": None}
    try:
        chat_id = store.get_state(conn, "chat_id")
    except sqlite3.Error:
        chat_id = None
    try:
        if do_sync:
            result["sync"] = sync.sync_all(conn)
            charts.follow(conn)   # the board follows the new rows (#76)
        result["alerts"] = detect.run(conn, as_of)
        if do_triage:
            triage.run_if_needed(conn, timeout=int(store.setting_num(conn, "claude_timeout_seconds", 120)) * 3)
        if do_notify:
            result["sent"] = notify.send_pending(conn)
            result["roundup"] = notify.send_roundup(conn, as_of)
        store.set_state(conn, "last_run", as_of)
        store.set_state(conn, "heartbeat_deferred_at", None)   # the stale episode the heartbeat was waiting out is over
    except Exception as e:  # noqa: BLE001 - the whole point is to never die silently
        log.error("run crashed: %s\n%s", e, traceback.format_exc())
        result["error"] = f"{type(e).__name__}: {e}"
        record_crash(conn, e, as_of, chat_id)
        if do_notify:
            try:
                notify.send_pending(conn)
            except Exception:  # noqa: BLE001
                log.exception("could not send the crash alert either")
    finally:
        try:
            store.set_state(conn, "last_attempt", as_of)  # a failed cycle waits RETRY_AFTER_ERROR_MIN, never 60s
        except sqlite3.Error:
            pass
        backup.maybe(conn)   # under the run lock, after a crash too (that is when a copy matters); it never raises
        held.close()
    return result


_last_direct_send: dict[str, str] = {}


def record_crash(conn: sqlite3.Connection, e: BaseException, as_of: str, chat_id=None) -> None:
    key = f"health:run:{type(e).__name__}:{as_of[:10]}"
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass
    try:
        conn.execute(
            "INSERT OR IGNORE INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','run_error',?,?,?)",
            (key, json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}", "as_of": as_of}), as_of),
        )
    except sqlite3.Error:
        # SQLite itself is the problem: last resort, one direct message. chat_id was read before the DB broke.
        from . import telegram
        chat = chat_id
        if not chat:
            try:
                chat = store.get_state(conn, "chat_id")
            except sqlite3.Error:
                chat = None
        if chat and _last_direct_send.get("day") != as_of[:10]:  # at most once a day while the DB is dead
            try:
                telegram.send_message(chat, f"🩺 <b>Finnamon crashed and couldn't record it:</b> {telegram.esc(e)}")
                _last_direct_send["day"] = as_of[:10]
            except Exception:  # noqa: BLE001
                log.exception("direct crash send failed")
