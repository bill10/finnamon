"""The always-on process. Three threads, one DB (a connection each), one household Claude Code session.

    ┌─ sync thread ───────────────────────┐  ┌─ telegram thread ─────────────────┐  ┌─ converse thread ──────────────────────┐
    │ every sync_interval_hours           │  │ getUpdates long-poll (offset in   │  │ inbox queue, strictly one at a time:    │
    │   (state.last_run); 15 min retry    │  │   state); stamps daemon_alive_at  │  │   claude -p --resume <household session>│
    │   after a failed cycle              │  │ drop: non-owner, bot, service,    │  │   hard timeout + kill → failure message │
    │                                     │  │ (inbound=channel: ack last batch, │  │                                         │
    │                                     │  │  then stamp alive only; the       │  │                                         │
    │                                     │  │  Claude plugin polls)             │  │                                         │
    │                                     │  │ 409 ×3 → one notice; status shows │  │                                         │
    │                                     │  │   inbound_conflict_at             │  │                                         │
    │ run.cycle(): sync→detect→triage→    │  │   wrong-chat messages             │  │   2 timeouts in a row → fresh session   │
    │   notify, under the run lock        │  │ pending_owner + code → enroll     │  │   NO_REPLY → send nothing               │
    │ each tick: link.check_pending       │  │ owner messages → inbox            │  │   result → sendMessage; charts → photo  │
    │   (finish a `link --start` add) and │  │                                   │  │                                         │
    │   link.check_updates (a re-login)   │  │                                   │  │                                         │
    └─────────────────────────────────────┘  └───────────────────────────────────┘  └─────────────────────────────────────────┘

inbound=session (`finnamon channel session`): the converse thread hands each message to the dashboard instead
(POST /api/telegram/turn), which types it into its live intercom session and answers with the reply off that session's
transcript; Telegram and the dashboard then share one conversation, with no `claude -p` and no channel plugin.

Why three threads: a stuck `claude -p` must never delay a due sync or the Telegram poll (eng review 1A).
Why one at a time: two concurrent --resume calls on one session corrupt it.
If any thread dies, the process exits non-zero so launchd/systemd restart it; a dead thread is never silent.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sqlite3
import sys
import signal
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import assistant, claude_runner, config, link, notify, owners, run, store, telegram
from .telegram import esc

log = logging.getLogger("finnamon.daemon")

CHART_PATH_RE = re.compile(r"(?P<p>(?:~|/)[^\s'\"`]*?/charts/[^\s'\"`]+\.png)")
RETRY_AFTER_ERROR_MIN = 15
TICK_S = 60   # the sync thread's loop period; the "within a minute" the assistant promises for a pending link
TIMEOUTS_BEFORE_FRESH_SESSION = 2
CHANNEL_IDLE_S = 30      # channel mode: re-read state.inbound this often; must stay under owners.DAEMON_ALIVE_S (90)
CONFLICT_FIRST_BACKOFF_S = 10  # first getUpdates 409: an `install` restart overlaps its predecessor's long-poll for a few seconds
CONFLICT_BACKOFF_S = 60  # 409 twice in a row: another consumer owns the bot's updates
CONFLICTS_BEFORE_NOTICE = 3  # consecutive 409s before the household is told (once per episode) and status shows inbound_conflict_at
ACK_TRIES = 3  # hand-off acks retried on the idle tick; each failed try kicks the plugin's long-poll, so not forever
DEAF_CHECK_S = 60        # channel mode: how often to ask Telegram whether anything is collecting the chat
DEAF_AFTER_S = 300       # ... and how long a backlog may sit before the household is told nobody is reading it. A live
                         # reader that is mid-answer may not be collecting either, and a Claude turn can be slow, so this
                         # is well past the slowest turn: a late telling beats telling a working system it is broken.
DEAF_REPEAT_H = 6        # ... and the floor between two tellings, however many outages there were
STABLE_S = 600           # up this long and the start is not part of a crash loop: state.daemon_starts is cleared
INTERCOM_MAX_S = 300     # session mode: the longest turn the dashboard waits for (web/talk.js RELAY_TIMEOUT_MS)
INTERCOM_SLACK_S = 30    # session mode: past the turn's own timeout, how long the dashboard has to say so before we give up on it
STARTS_KEPT = 10         # state.daemon_starts keeps this many; heartbeat.CRASH_STARTS of them is a crash loop


def dashboard_base() -> str:
    """The dashboard on this machine: the PORT it honours (web/server.js), loopback, which its Host check always lets in."""
    return f"http://127.0.0.1:{os.environ.get('PORT') or config.DASHBOARD_PORT}"


def bot_configured() -> bool:
    return bool(config.telegram_token())


class Daemon:
    def __init__(self, conn_factory=store.connect):
        self.conn_factory = conn_factory
        self.stop = threading.Event()
        self.inbox: queue.Queue = queue.Queue()
        self.consecutive_timeouts = 0
        self.died = False   # _start ended by a failure (harness check, dead thread), not a clean stop

    def _connect(self, name: str) -> sqlite3.Connection | None:
        """Open this thread's connection, retrying; never let a bad open kill a thread silently."""
        while not self.stop.is_set():
            try:
                return self.conn_factory()
            except Exception:  # noqa: BLE001
                log.exception("%s: cannot open the database; retrying in 30s", name)
                self.stop.wait(30)
        return None

    # --- sync thread ------------------------------------------------------------------------

    def sync_due(self, conn: sqlite3.Connection) -> bool:
        hours = store.setting_num(conn, "sync_interval_hours", 6)
        last_run = store.get_state(conn, "last_run")
        last_attempt = store.get_state(conn, "last_attempt")
        run_due = conn.execute("SELECT ? IS NULL OR ? <= datetime('now','localtime', ?)", (last_run, last_run, f"-{hours} hours")).fetchone()[0]
        attempt_ok = conn.execute("SELECT ? IS NULL OR ? <= datetime('now','localtime', ?)",
                                  (last_attempt, last_attempt, f"-{RETRY_AFTER_ERROR_MIN} minutes")).fetchone()[0]
        return bool(run_due and attempt_ok)

    def check_pending_link(self, conn: sqlite3.Connection) -> None:
        """A bank the assistant started adding; the person finished in the browser. Its failures never delay a sync."""
        try:
            r = link.check_pending(conn)
            if r and r["state"] != "waiting":
                log.info("pending link: %s", r["state"])
                if r["state"] in ("closed", "expired"):
                    chat = store.get_state(conn, "chat_id")
                    if chat:
                        telegram.send_message(chat, "The bank link was closed without adding a bank; it still works if you open it again."
                                              if r["state"] == "closed" else "The bank link expired. Ask me to add the bank again when you're ready.")
        except Exception:  # noqa: BLE001
            log.exception("pending link check failed")

    def check_relogins(self, conn: sqlite3.Connection) -> None:
        """A re-login link (the chat's `fix X` or the dashboard's Reconnect) the person finished: sync that bank now."""
        try:
            for r in link.check_updates(conn):
                log.info("re-login %s: %s", r["item_id"], r["sync"]["error"] or "reconnected")
        except Exception:  # noqa: BLE001
            log.exception("re-login check failed")

    def sync_loop(self) -> None:
        conn = self._connect("sync")
        while conn and not self.stop.is_set():
            try:
                self.check_pending_link(conn)
                self.check_relogins(conn)
                if self.sync_due(conn):
                    log.info("sync due (last_run=%s)", store.get_state(conn, "last_run"))
                    try:
                        res = run.cycle(conn)
                        log.info("cycle: %d new alerts, %d sent, error=%s", len(res["alerts"]), res["sent"], res["error"])
                    except run.Locked:
                        log.info("run lock held; skipping this tick")
            except Exception:  # noqa: BLE001
                log.exception("sync loop error")
            self.stop.wait(TICK_S)

    # --- telegram thread --------------------------------------------------------------------

    def poll_loop(self) -> None:
        conn = self._connect("telegram")
        if conn:
            self.verify_privacy(conn)
        ack_tries = 0       # < ACK_TRIES = we still owe Telegram an ack for the last batch (state on disk decides, not this process's history)
        last_deaf_check = None   # the first idle tick after the ack hand-off asks; not 0.0: monotonic counts from boot, so a box up under a minute would skip it
        conflicts = 0       # consecutive 409s
        while conn and not self.stop.is_set():
            try:
                store.set_state(conn, "daemon_alive_at", store.now_local())
                if not bot_configured():   # init skipped the bot (#111): nothing to poll until `finnamon init` adds one
                    self.stop.wait(CHANNEL_IDLE_S)
                    continue
                if store.get_state(conn, "inbound") == "channel":
                    # Telegram allows one getUpdates consumer per bot; in channel mode the Claude Code Telegram
                    # channel plugin is that consumer and handles conversation. We only send.
                    if ack_tries < ACK_TRIES:
                        ack_tries = ACK_TRIES if self.ack_updates(conn) else ack_tries + 1   # else the plugin's first poll replays what we already handled
                        conflicts = 0; store.set_state(conn, "inbound_conflict_at", None)   # `channel on` is the resolution the notice asked for
                    elif last_deaf_check is None or time.monotonic() - last_deaf_check >= DEAF_CHECK_S:
                        last_deaf_check = time.monotonic()
                        self.check_channel_heard(conn)
                    self.stop.wait(CHANNEL_IDLE_S)
                    continue
                offset = store.get_state(conn, "telegram_offset")
                updates = telegram.get_updates(int(offset) if offset else None)
                ack_tries = 0
                if store.get_state(conn, "inbound_off_at") and not updates:
                    store.set_state(conn, "inbound_off_at", None)   # backlog drained: a wrong clock can hurt at most one backlog
                if conflicts:
                    conflicts = 0; store.set_state(conn, "inbound_conflict_at", None)
                for u in updates:
                    try:
                        self.handle_update(conn, u)
                    except sqlite3.OperationalError:
                        raise  # busy/locked: retry the same update next poll
                    except Exception:  # noqa: BLE001 - a poison update is logged and skipped, never retried forever
                        log.exception("dropping update %s", u.get("update_id"))
                    store.set_state(conn, "telegram_offset", u["update_id"] + 1)
            except telegram.TelegramError as e:
                if e.code == 409:   # another getUpdates consumer on this bot: the Claude Code channel session, most likely
                    conflicts += 1
                    log.warning("getUpdates: 409 Conflict (%s in a row); if the Claude channel session is running, run `finnamon channel on`", conflicts)
                    if conflicts == CONFLICTS_BEFORE_NOTICE:
                        self.conflict_notice(conn)
                    self.stop.wait(CONFLICT_FIRST_BACKOFF_S if conflicts == 1 else CONFLICT_BACKOFF_S)
                    continue
                log.warning("getUpdates: %s", e)
                self.stop.wait(e.retry_after or 10)
            except Exception:  # noqa: BLE001
                log.exception("poll loop error")
                self.stop.wait(10)

    def check_channel_heard(self, conn) -> str | None:
        """In channel mode the plugin inside the dashboard's Claude session is the bot's only reader. When it dies the
        household sees nothing at all: the session stays up and answers the dashboard's own terminal, sending still
        works, and every status surface says fine. Telegram is the one place that knows, so ask it.

        getWebhookInfo reads without consuming, so this cannot take the slot from a plugin that is alive. Returns the
        state stamped, or None while the chat is being read."""
        try:
            pending = telegram.pending_updates(timeout=10)   # a metadata read, not a long-poll: stay well inside owners.DAEMON_ALIVE_S
        except Exception as e:   # noqa: BLE001 - a Telegram hiccup is not evidence of a dead reader
            log.warning("could not ask Telegram whether the chat is being read: %s", e)
            return None
        since = store.get_state(conn, "channel_deaf_since")
        if not pending:
            if since:
                store.set_state(conn, "channel_deaf_since", None)
                log.info("the chat is being read again")
            return None
        if not since:
            store.set_state(conn, "channel_deaf_since", store.now_local())   # a message just arrived; the plugin gets a moment
            return None
        # Compared in SQL as everywhere else (owners.daemon_alive), so a clock that steps back cannot make the elapsed
        # time negative in Python and skip the telling. It does delay it: `since` lands in the future and the grace
        # holds until the wall clock catches up. A delayed telling beats a missed one.
        if conn.execute("SELECT ? > datetime('now','localtime', ?)", (since, f"-{DEAF_AFTER_S} seconds")).fetchone()[0] == 1:
            return since   # a reader still has a moment to collect it
        # The key carries the whole `since`, which is stamped once per outage and does not move, so a continuous outage
        # is told once. That alone is not enough: a reader that crash-loops collects the backlog, dies, and begins a
        # fresh outage every few minutes, each with a new `since` and each a new message. DEAF_REPEAT_H is the floor
        # between two tellings however many outages there were; the advice is the same every time anyway.
        if conn.execute("SELECT 1 FROM alerts WHERE kind='channel_deaf' AND as_of > datetime('now','localtime',?)",
                        (f"-{DEAF_REPEAT_H} hours",)).fetchone():
            return since
        chat = store.get_state(conn, "chat_id")
        key = f"health:channel_deaf:{since}"
        try:
            # send_pending runs on the sync thread against its own connection, and store.connect is autocommit: without
            # this the row is visible to its SELECT the instant it is inserted, and a cycle landing inside the send below
            # sends the same message twice. The lock is non-blocking; a cycle holding it just defers us a check.
            held = run.lock()
        except run.Locked:
            return since
        try:
            cur = conn.execute("INSERT OR IGNORE INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','channel_deaf',?,?,?)",
                               (key, json.dumps({"pending": pending, "since": since, "chat": chat}), store.now_local()))
            if cur.rowcount:   # newly raised, not the dedupe swallowing a repeat
                # Sending still works while receiving is broken, which is the only reason this is reportable at all. It
                # goes now rather than waiting for run.cycle, which the sync interval can put six hours away.
                row = conn.execute("SELECT * FROM alerts WHERE key=?", (key,)).fetchone()
                try:
                    if not notify.send_one(conn, row, chat):
                        log.warning("channel_deaf raised, but there is no chat_id to send it to")
                except Exception as e:   # noqa: BLE001 - the row stays pending and the next cycle retries it
                    log.warning("channel_deaf raised but not sent: %s", e)
        finally:
            held.close()
        return since

    def ack_updates(self, conn: sqlite3.Connection) -> bool:
        """Confirm our last batch to Telegram before the channel plugin takes over. Anything new this returns is
        left unconfirmed, so the plugin receives it. False = try again next tick."""
        offset = store.get_state(conn, "telegram_offset")
        if not offset:
            return True
        try:
            telegram.get_updates(int(offset), timeout=0)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("could not acknowledge updates before handing off: %s", e)
            return False

    def conflict_notice(self, conn: sqlite3.Connection) -> None:
        """Two programs are reading the bot: messages get answered by whichever polls first, some twice, some never.
        Say so once in the chat and leave a mark for `finnamon status`."""
        store.set_state(conn, "inbound_conflict_at", store.now_local())
        chat_id = store.get_state(conn, "chat_id")
        if not chat_id:
            return
        try:
            telegram.send_message(chat_id, "Another program is reading this bot's messages (a Claude Code channel session?), so replies "
                                           "may be missed or doubled. In a terminal on the Finnamon box: finnamon channel on, or stop that program.")
        except Exception as e:  # noqa: BLE001
            log.warning("conflict notice not sent: %s", e)

    def verify_privacy(self, conn: sqlite3.Connection) -> bool:
        try:
            me = telegram.get_me()
        except Exception as e:  # noqa: BLE001
            log.warning("getMe failed: %s", e)
            return False
        ok = bool(me.get("can_read_all_group_messages"))
        store.set_state(conn, "bot_username", me.get("username"))
        store.set_state(conn, "bot_privacy_off", "1" if ok else "0")
        chat = store.get_state(conn, "chat_id")
        if not ok and chat and str(chat).startswith("-"):
            telegram.send_message(chat, "⚠️ I can't read plain messages in this group: privacy mode is on. In @BotFather run /setprivacy → Disable, then remove and re-add me.")
        return ok

    def handle_update(self, conn: sqlite3.Connection, u: dict) -> None:
        m = u.get("message")
        if not m or not m.get("text"):
            return  # service messages, photos, etc.
        sender = m.get("from") or {}
        if sender.get("is_bot"):
            return
        off_at = store.get_state(conn, "inbound_off_at")
        if off_at and m.get("date", 0) < int(off_at):
            log.info("dropping replayed message %s: sent at %s, before channel off at %s (the plugin answered it)", m.get("message_id"), m.get("date"), off_at)
            return
        chat_id = str(m["chat"]["id"])
        pending = store.get_state(conn, "pending_owner")
        if pending and m["chat"].get("type") in ("group", "supergroup"):
            p = json.loads(pending)
            if p.get("deadline") and p["deadline"] < store.now_local():
                store.set_state(conn, "pending_owner", None)  # a Ctrl-C'd `owner add` must not leave a code live
            elif owners.matches(m.get("text"), p["code"]):
                if not owners.may_enrol_from(conn, chat_id, p.get("switch", False)):
                    log.info("ignoring the enrolment code from chat %s: not the household chat, and no --new-group", chat_id)
                    return
                owners.enroll(conn, p["owner"], m, switch_chat=True)
                log.info("enrolled %s from chat %s", p["owner"], chat_id)
                return
        if chat_id != str(store.get_state(conn, "chat_id")):
            log.info("ignoring message from chat %s", chat_id)
            return
        owner = conn.execute("SELECT owner FROM owners WHERE telegram_user_id=?", (sender.get("id"),)).fetchone()
        if not owner:
            log.info("ignoring message from unknown user %s", sender.get("id"))
            return
        reply_to = (m.get("reply_to_message") or {}).get("message_id")
        self.inbox.put({"owner": owner[0], "text": m["text"], "reply_to": reply_to, "message_id": m["message_id"], "chat_id": chat_id})

    # --- conversation worker (serial) ---------------------------------------------------------

    def converse_loop(self) -> None:
        conn = self._connect("converse")
        while conn and not self.stop.is_set():
            try:
                msg = self.inbox.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.converse(conn, msg)
            except Exception:  # noqa: BLE001
                log.exception("conversation error")

    def note_for(self, conn: sqlite3.Connection, msg: dict) -> tuple[str, int | None]:
        """What the message replied to, for its prefix ('' for nothing of ours), and the alert id when it was one alert."""
        if msg.get("reply_to"):
            rows = conn.execute(f"SELECT id, {store.ALERT_GROUP} FROM alerts WHERE telegram_chat_id=? AND telegram_message_id=? ORDER BY id",
                                (str(msg.get("chat_id")), msg["reply_to"])).fetchall()
            items = len({r[1] for r in rows})   # one transaction's alerts share one message (and one roundup item)
            if items == 1:
                return f"replying to alert {rows[0][0]}", rows[0][0]
            if rows:
                return f"replying to roundup message {msg['reply_to']} (items 1..{items}; use finnamon normal --roundup-item)", None
        return "", None

    def converse(self, conn: sqlite3.Connection, msg: dict) -> None:
        chat = msg.get("chat_id") or store.get_state(conn, "chat_id")
        note, alert_id = self.note_for(conn, msg)
        timeout = int(store.setting_num(conn, "claude_timeout_seconds", 120))
        if store.get_state(conn, "inbound") == "session":
            text, trouble = self.ask_intercom(msg, note, timeout)
            if trouble:
                conn.execute("INSERT INTO feedback (alert_id, owner, text, parsed_action) VALUES (?,?,?,?)", (alert_id, msg["owner"], msg["text"], "intercom_error"))
                telegram.send_message(chat, trouble)
                return
            return self.reply(conn, chat, msg, alert_id, text)
        prompt = f"[Telegram, {msg['owner']}{'; ' + note if note else ''}] {msg['text']}"
        session = store.get_state(conn, "session")
        res = claude_runner.run(prompt, resume=session, timeout=timeout)
        kind = claude_runner.classify_error(res.error) if not res.ok else None
        if kind == "session lost" and session:
            store.set_state(conn, "session", None)
            res = claude_runner.run(prompt, resume=None, timeout=timeout)
            kind = claude_runner.classify_error(res.error) if not res.ok else None
        if kind == "timeout":
            self.consecutive_timeouts += 1
            if self.consecutive_timeouts >= TIMEOUTS_BEFORE_FRESH_SESSION and session:
                log.warning("%d timeouts in a row; starting a fresh session", self.consecutive_timeouts)
                store.set_state(conn, "session", None)
                self.consecutive_timeouts = 0
                res = claude_runner.run(prompt, resume=None, timeout=timeout)
                kind = claude_runner.classify_error(res.error) if not res.ok else None
        else:
            self.consecutive_timeouts = 0
        if not res.ok:
            conn.execute("INSERT INTO feedback (alert_id, owner, text, parsed_action) VALUES (?,?,?,?)",
                         (alert_id, msg["owner"], msg["text"], f"claude_error:{kind}"))
            hint = ("it may have partially completed; check <code>finnamon alerts</code> and <code>finnamon normal --list</code> in a terminal on the Finnamon box" if kind == "timeout"
                    else f"run <code>claude --strict-mcp-config</code> in <code>{esc(str(assistant.dir()))}</code> on the Finnamon box to check the login")
            telegram.send_message(chat, f"🩺 Claude is unavailable ({esc(kind)}); {hint}.")
            return
        if res.session_id:
            store.set_state(conn, "session", res.session_id)
        self.reply(conn, chat, msg, alert_id, res.text)

    def ask_intercom(self, msg: dict, note: str, timeout: int) -> tuple[str, str | None]:
        """inbound=session: the dashboard types the message into its live session and answers with what the session said
        (web/server.js /api/telegram/turn). (reply, None), or ('', the line the chat gets instead)."""
        timeout = min(max(timeout, 1), INTERCOM_MAX_S)   # what the dashboard will actually wait, so the chat is told the truth
        body = json.dumps({"from": msg["owner"], "text": msg["text"], "note": note, "timeout": timeout}).encode()
        try:
            req = urllib.request.Request(f"{dashboard_base()}/api/telegram/turn", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {config.web_token()}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout + INTERCOM_SLACK_S) as r:
                reply = str(json.load(r).get("reply") or "")
            if not reply.strip():   # the turn ended on a tool call, or someone typed at the dashboard mid-turn and cut it short
                return "", "🩺 The assistant finished that without an answer for the chat (it may have been interrupted at the dashboard); ask again."
            return reply, None
        except urllib.error.HTTPError as e:
            try:
                why = str(json.load(e).get("error") or e.reason)
            except Exception:  # noqa: BLE001 - not our server's JSON: its status says enough
                why = str(e.reason)
            log.warning("intercom refused a Telegram turn: %s %s", e.code, why)
            if e.code == 504:
                return "", (f"⏳ That is taking longer than {timeout}s; the answer will be in the dashboard's intercom. "
                            "Ask again here in a minute for the short version.")
            if e.code == 503:
                return "", "🩺 The assistant's session is starting up; send that again in a minute."
            return "", f"🩺 The dashboard would not take the message ({esc(why)}); in a terminal on the Finnamon box: <code>finnamon doctor</code>."
        except (urllib.error.URLError, OSError, ValueError) as e:   # refused, reset mid-turn (a restart), timed out, garbled
            log.warning("intercom unreachable for a Telegram turn: %s", e)
            return "", ("🩺 The dashboard isn't answering, so the assistant can't reply here right now. Send that again in a "
                        "minute; if this repeats, run <code>finnamon doctor</code> on the Finnamon box.")

    def reply(self, conn: sqlite3.Connection, chat, msg: dict, alert_id: int | None, text: str) -> None:
        text = text.strip()
        if not text or text == "NO_REPLY":
            return  # not for us; not recorded either
        conn.execute("INSERT INTO feedback (alert_id, owner, text, parsed_action) VALUES (?,?,?,?)", (alert_id, msg["owner"], msg["text"], "claude"))
        charts_dir = config.charts_dir().resolve()
        for path in CHART_PATH_RE.findall(text):
            p = Path(path).expanduser().resolve()
            if not p.is_relative_to(charts_dir) or not p.is_file():
                log.warning("ignoring chart path outside %s: %s", charts_dir, path)
                continue
            try:
                telegram.send_photo(chat, str(p))
                text = text.replace(path, "").strip()
            except Exception:  # noqa: BLE001
                log.exception("send_photo failed for %s", p)
        if text:
            telegram.send_message(chat, esc(text), reply_to=msg.get("message_id"))

    # --- lifecycle -------------------------------------------------------------------------------

    def _note(self, fn) -> None:
        """Startup bookkeeping for the heartbeat's crash-loop check; never what stops the daemon starting."""
        try:
            fn(self.conn_factory())
        except Exception:  # noqa: BLE001
            log.exception("could not record daemon start state")

    def _note_error(self, err: str) -> None:
        self._note(lambda c: store.set_state(c, "daemon_last_error", err))

    def _edit_starts(self, fn) -> None:
        def edit(c):
            starts = fn(json.loads(store.get_state(c, "daemon_starts") or "[]"))
            store.set_state(c, "daemon_starts", json.dumps(starts) if starts else None)
        self._note(edit)

    def start(self, exit=sys.exit) -> None:
        # Every start is appended; STABLE_S of uptime clears the list and a clean stop (SIGTERM from update/install,
        # Ctrl-C) takes its own entry back. So the heartbeat reading several means the supervisor keeps restarting a
        # daemon that keeps dying (heartbeat.CRASH_STARTS).
        me = store.now_local()
        self._edit_starts(lambda s: s[-(STARTS_KEPT - 1):] + [me])
        try:
            self._start(exit)
        except Exception as e:
            self._note_error(f"{type(e).__name__}: {e}")
            raise
        if self.stop.is_set() and not self.died:
            self._edit_starts(lambda s: [t for t in s if t != me])

    def _start(self, exit) -> None:
        # A box whose `finnamon update` ran the release before the bundle existed restarts into this code with no
        # assistant directory at all; writing it here (only when it is missing: never over the household's edits) is what
        # keeps that update from crash-looping. `finnamon install` and `update` own every later write.
        try:
            # Trust first, files second: the dashboard restarts beside us and spawns its session the moment the directory
            # exists, and a session that starts trusted but a moment before the files is refused nothing it should have.
            t = assistant.trust()
            if t:
                log.info("marked %s trusted for Claude Code", assistant.dir())
            elif t is None:
                log.warning("could not mark %s trusted in %s (%s)", assistant.dir(), assistant.claude_config_path(), assistant.TRUST_FIX)
            wrote = assistant.ensure()
            if wrote:
                log.info("wrote the assistant bundle to %s (%d file(s); backed up: %s; removed: %s)", assistant.dir(), len(wrote["written"]),
                         ", ".join(wrote["backed_up"]) or "none", ", ".join(wrote["removed"]) or "none")
        except OSError as e:
            log.error("could not write the assistant bundle: %s", e)
        problems = claude_runner.harness_problems()
        if problems:   # the dashboard's claude restarts beside us under `finnamon update` and writes ~/.claude.json: read it once more
            time.sleep(1)
            problems = claude_runner.harness_problems()
        if problems:
            log.error("harness check failed: %s", "; ".join(problems))
            self._note_error("harness check failed: " + "; ".join(problems))
            self.died = True
            exit(2)
            return
        threads = [threading.Thread(target=self.sync_loop, name="sync", daemon=True),
                   threading.Thread(target=self.poll_loop, name="telegram", daemon=True),
                   threading.Thread(target=self.converse_loop, name="converse", daemon=True)]
        for t in threads:
            t.start()
        # `finnamon update` and `install` stop this process with SIGTERM. Without this the claude children outlive us in
        # their own process groups, and the daemon that replaces us --resumes a session an orphan is still mid-turn on.
        def _bye(signum, _frame):
            self.stop.set()
            n = claude_runner.terminate_all()
            log.info("signal %s: stopped %d claude child(ren)", signum, n)
        for _sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(_sig, _bye)
            except ValueError:   # not the main thread (a test, an embedded run): the handler is a nicety, not a requirement
                pass
        # In channel mode the dashboard's session, which restarts into the new directory as we do, is Telegram's only reader,
        # and a local-scope plugin belongs to a directory: register it here too (whenever it is missing, not only on the
        # start that wrote the bundle: a start that died in between must not leave it unregistered), or the first update
        # across the bundle release leaves the chat with nobody reading it until someone runs `finnamon update` again.
        # After the threads and the signal handlers: it fetches a marketplace, up to PLUGIN_INSTALL_TIMEOUT_S, syncing need not
        # wait for that, and a SIGTERM meanwhile must still reach _bye, or a triage child started by the first sync is orphaned.
        # In a thread of its own: a stop must not wait behind the marketplace fetch (launchd would SIGKILL a daemon that did).
        def _register():
            try:
                if store.get_state(self.conn_factory(), "inbound") == "channel" and not assistant.channel_plugin_registered():
                    ok, msg = assistant.register_channel_plugin(claude_runner.binary())
                    (log.info if ok is not None else log.warning)("%s", msg)
            except (OSError, sqlite3.Error) as e:
                log.warning("could not register the Telegram channel plugin: %s", e)
        threading.Thread(target=_register, name="plugin", daemon=True).start()
        log.info("finnamon daemon up")
        up_at, stable = time.monotonic(), False
        try:
            while not self.stop.is_set():
                self.stop.wait(30)
                if self.stop.is_set():
                    break   # a signal: the threads are leaving their loops, which is not dying
                dead = [t.name for t in threads if not t.is_alive()]
                if dead:
                    log.error("thread(s) died: %s; exiting so the supervisor restarts us", dead)
                    self._note_error(f"thread(s) died: {', '.join(dead)}")
                    self.died = True
                    self.stop.set()
                    exit(1)
                    return
                if not stable and time.monotonic() - up_at >= STABLE_S:
                    stable = True
                    self._note(lambda c: [store.set_state(c, k, None) for k in ("daemon_starts", "daemon_last_error", "crash_loop_reported")])
        except KeyboardInterrupt:
            self.stop.set()
        for t in threads:
            t.join(timeout=5)
