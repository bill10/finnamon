"""Separate process, hourly: if the daemon hasn't completed a run in 8h, say so, once a day.
Runs outside the daemon so it can notice a dead daemon.

A laptop that slept overnight wakes with a stale last_run and a daemon about to catch up, and launchd fires this job
on wake too. So a stale sync is only reported when the daemon is dead (after giving it WAKE_WAIT_S to stamp
daemon_alive_at), or when it is alive but the sync is still stale DEFER_MINUTES into its grace. A deferred check never uses the
day's one alert. Separately, a daemon the supervisor keeps restarting is reported once per crash loop."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from . import owners, scheduler, store, telegram

log = logging.getLogger("finnamon.heartbeat")
MAX_AGE_HOURS = 8
WAKE_WAIT_S = 120    # on wake the daemon's poll loop may take a long-poll to stamp daemon_alive_at again
WAKE_POLL_S = 10
CRASH_STARTS = 3     # this many starts without STABLE_S of uptime between them (daemon.py) is a crash loop
DEFER_MINUTES = 50   # a deferred stale sync is reported by the next hourly heartbeat, not a quick re-run of this one


def _send(conn: sqlite3.Connection, msg: str) -> None:
    chat = store.get_state(conn, "chat_id")
    if chat:
        telegram.send_message(chat, msg)


def crash_loop(conn: sqlite3.Connection) -> str | None:
    starts = json.loads(store.get_state(conn, "daemon_starts") or "[]")
    if len(starts) < CRASH_STARTS or store.get_state(conn, "crash_loop_reported"):
        return None
    err = store.get_state(conn, "daemon_last_error") or "no error recorded"
    msg = (f"🩺 <b>The daemon keeps crashing</b> ({len(starts)} starts since {telegram.esc(starts[0])}): {telegram.esc(err)}. "
           f"The full error is in the daemon's log on the Finnamon box (<code>{telegram.esc(str(scheduler.log_dir() / 'daemon.err'))}</code> on a Mac, "
           f"<code>journalctl --user -u {scheduler.unit('daemon')}</code> on Linux); fix it, then run <code>finnamon install</code>.")
    _send(conn, msg)
    store.set_state(conn, "crash_loop_reported", starts[-1])
    return msg


def check(conn: sqlite3.Connection, now: str | None = None, sleep=time.sleep) -> str | None:
    """Returns the message sent, or None."""
    now = now or store.now_local()
    if msg := crash_loop(conn):
        return msg
    last = store.get_state(conn, "last_run")
    interval = store.setting_num(conn, "sync_interval_hours", 6)
    max_age = max(MAX_AGE_HOURS, interval + 2)
    stale = conn.execute("SELECT ? IS NULL OR ? < datetime(?, ?)", (last, last, now, f"-{max_age} hours")).fetchone()[0]
    if not stale:
        return None   # read-only: a sync holding the write lock must not make a healthy heartbeat fail
    today = now[:10]
    if store.get_state(conn, "last_heartbeat_text") == today:
        return None
    woke = not owners.daemon_alive(conn, now)   # the daemon's stamp is old: this machine (or the daemon) was just asleep
    waited = 0
    while store.get_state(conn, "daemon_alive_at") and not owners.daemon_alive(conn, now) and waited < WAKE_WAIT_S:
        sleep(WAKE_POLL_S)
        waited += WAKE_POLL_S
    alive = owners.daemon_alive(conn, now)
    if alive:
        # The daemon is up and will catch up within a minute (sync_due); only a sync still stale DEFER_MINUTES on is
        # news. A successful run clears the grace (run.cycle). On a wake it starts over, unless a sync attempt has
        # finished and failed since it began: the laptop slept mid catch-up is not an outage, a failing sync is.
        deferred = store.get_state(conn, "heartbeat_deferred_at")
        attempt = store.get_state(conn, "last_attempt")
        if not deferred or (woke and not (attempt and attempt > deferred)):
            deferred = now
            store.set_state(conn, "heartbeat_deferred_at", now)
        if conn.execute("SELECT ? > datetime(?, ?)", (deferred, now, f"-{DEFER_MINUTES} minutes")).fetchone()[0]:
            log.info("last sync %s is stale but the daemon is alive; re-checking next heartbeat", last)
            return None
    hint = ("The daemon is running but its syncs aren't finishing: check <code>finnamon status</code>" if alive
            else "Check that the daemon is running: <code>finnamon status</code>")
    msg = (f"🩺 <b>Last sync was {telegram.esc(last or 'never')}</b> (expected every {interval:g}h). "
           f"{hint} in a terminal on the Finnamon box.")
    _send(conn, msg)
    store.set_state(conn, "last_heartbeat_text", today)
    store.set_state(conn, "heartbeat_deferred_at", None)
    return msg
