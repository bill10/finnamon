"""Owners and the household chat. One chat for everyone: a DM while there is one owner, a group
once a second is added. Enrolment needs a one-time code so the first stranger to speak never
becomes an owner. If the daemon is already long-polling Telegram (only one consumer is allowed),
the CLI hands enrolment to it through state.pending_owner and waits for the result."""
from __future__ import annotations

import json
import secrets as _secrets
import sqlite3
import time

from . import store, telegram

DAEMON_ALIVE_S = 90  # daemon.poll_loop stamps state.daemon_alive_at every poll
JOINT = "joint"      # an account owner value, never a member row


def resolve(conn: sqlite3.Connection, name: str | None, joint: bool = False) -> str:
    """The member a typed name means, spelt as stored (case-insensitive), or `joint` where that is allowed. A typo is
    refused rather than quietly becoming a new member: only `owner add` makes one."""
    n = (name or "").strip()
    if joint and n.lower() == JOINT:
        return JOINT
    r = conn.execute("SELECT owner FROM owners WHERE owner=? COLLATE NOCASE", (n,)).fetchone()
    if r:
        return r[0]
    names = [x[0] for x in conn.execute("SELECT owner FROM owners ORDER BY rowid")] + ([JOINT] if joint else [])
    raise ValueError(f"{n or 'an empty name'} is not a household member (members: {', '.join(names) or 'none'}); "
                     "`finnamon owner add <name>` makes one, for a person at a terminal")


def filing(conn: sqlite3.Connection, owner: str) -> str:
    """The member an Item is filed under. A joint account hangs off the first member: items.owner must be a real row."""
    if owner != JOINT:
        return owner
    r = conn.execute("SELECT owner FROM owners ORDER BY rowid LIMIT 1").fetchone()
    return r[0] if r else "me"


def rename(conn: sqlite3.Connection, old: str, new: str) -> dict:
    """Every account, bank and feedback row follows the new name; the Telegram id and display name stay with the person."""
    old = resolve(conn, old)
    new = " ".join((new or "").split())
    if not new or new.lower() == JOINT:
        raise ValueError("a member's name is a word of its own, and `joint` is reserved for shared accounts")
    if conn.execute("SELECT 1 FROM owners WHERE owner=? COLLATE NOCASE AND owner<>?", (new, old)).fetchone():
        raise ValueError(f"{new} is already a household member")
    if new == old:
        raise ValueError(f"{old} is already called that")
    with store.tx(conn):
        conn.execute("INSERT INTO owners (owner, telegram_user_id, display_name) SELECT ?, telegram_user_id, "
                     "CASE WHEN display_name IS NULL OR display_name=? THEN ? ELSE display_name END FROM owners WHERE owner=?", (new, old.title(), new.title(), old))
        for table, col in (("items", "owner"), ("accounts", "owner"), ("accounts", "owner_before_merge"), ("feedback", "owner")):
            conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
        conn.execute("DELETE FROM owners WHERE owner=?", (old,))
    return {"renamed": old, "to": new}


def remove(conn: sqlite3.Connection, name: str) -> dict:
    """Refused while the member still owns an account (reassign it first: `finnamon account owner`) and for the last member."""
    name = resolve(conn, name)
    n = conn.execute("SELECT count(*) FROM accounts WHERE owner=?", (name,)).fetchone()[0]
    if n:
        raise ValueError(f"{name} still owns {n} account{'s' if n != 1 else ''}; give them to someone else (or joint) with `finnamon account owner <account_id> <member>` first")
    other = conn.execute("SELECT owner FROM owners WHERE owner<>? ORDER BY rowid LIMIT 1", (name,)).fetchone()
    if not other:
        raise ValueError(f"{name} is the only household member")
    with store.tx(conn):
        conn.execute("UPDATE items SET owner=? WHERE owner=?", (other[0], name))   # a bank whose accounts are all joint or moved on
        conn.execute("UPDATE accounts SET owner_before_merge=NULL WHERE owner_before_merge=?", (name,))
        conn.execute("DELETE FROM owners WHERE owner=?", (name,))
    return {"removed": name}


def new_code() -> str:
    return _secrets.token_hex(3).upper()  # 6 hex chars, spoken over the shoulder


def daemon_alive(conn: sqlite3.Connection, now: str | None = None) -> bool:
    t = store.get_state(conn, "daemon_alive_at")
    return bool(t) and conn.execute("SELECT ? >= datetime(?, ?)", (t, now or store.now_local(), f"-{DAEMON_ALIVE_S} seconds")).fetchone()[0] == 1


def matches(text: str | None, code: str) -> bool:
    t = (text or "").strip()
    return t.upper() == code or t.upper() == f"/START {code}"


def may_enrol_from(conn: sqlite3.Connection, chat, switch: bool) -> bool:
    """Where an enrolment code counts. A household that is still a DM moves to the group the code comes from (that is
    what `owner add` asks for); a household group takes the code only there, unless `owner add --new-group` asked to move."""
    return switch or not household_is_group(conn) or str(store.get_state(conn, "chat_id")) == str(chat)


def household_is_group(conn: sqlite3.Connection) -> bool:
    return str(store.get_state(conn, "chat_id") or "").startswith("-")   # Telegram group ids are negative


def enroll(conn: sqlite3.Connection, owner: str, m: dict, switch_chat: bool) -> dict:
    """Record the speaker as `owner`; optionally make their chat the household chat. Used by the CLI and the daemon."""
    uid, chat = m["from"]["id"], m["chat"]["id"]
    with store.tx(conn):
        conn.execute("INSERT OR IGNORE INTO owners (owner, display_name) VALUES (?,?)", (owner, owner.title()))
        conn.execute("UPDATE owners SET telegram_user_id=?, display_name=COALESCE(display_name, ?) WHERE owner=?", (uid, m["from"].get("first_name"), owner))
        old = store.get_state(conn, "chat_id")
        if switch_chat and str(old) != str(chat):
            store.set_state(conn, "chat_id", chat)
            # message ids are per chat: DM-era alerts can no longer be replied to, and the conversation starts fresh
            conn.execute("UPDATE alerts SET telegram_message_id=NULL WHERE telegram_chat_id IS NOT ?", (str(chat),))
            store.set_state(conn, "session", None)
        store.set_state(conn, "pending_owner", None)
    if switch_chat and old and str(old) != str(chat):
        names = [r[0] for r in conn.execute("SELECT COALESCE(display_name, owner) FROM owners")]
        telegram.send_message(chat, f"👋 {telegram.esc(owner.title())} is in. This group is now the household chat for {telegram.esc(', '.join(names))}. Everything I notice goes here.")
        telegram.send_message(old, "Moving to the household group. This chat goes quiet.")
    return {"owner": owner, "telegram_user_id": uid, "chat_id": chat}


def _poll_direct(conn: sqlite3.Connection, owner: str, code: str, want_group: bool, wait_s: int, sleep, switch: bool = False) -> dict:
    deadline = time.time() + wait_s
    offset = store.get_state(conn, "telegram_offset")
    offset = int(offset) if offset else None
    while time.time() < deadline:
        for u in telegram.get_updates(offset, timeout=20):
            offset = u["update_id"] + 1
            store.set_state(conn, "telegram_offset", offset)
            m = u.get("message") or {}
            chat_type = m.get("chat", {}).get("type")
            is_group = chat_type in ("group", "supergroup")
            if (is_group if want_group else chat_type == "private") and m.get("from") and not m["from"].get("is_bot") and matches(m.get("text"), code) \
                    and may_enrol_from(conn, m["chat"]["id"], switch):
                return enroll(conn, owner, m, switch_chat=True)
        sleep(1)
    raise TimeoutError(f"no message with the code {code} received; run the command again"
                       + (" (a code sent in another group counts only with --new-group)" if want_group and not switch else ""))


def add_first(conn: sqlite3.Connection, owner: str, code: str, wait_s: int = 600, sleep=time.sleep) -> dict:
    """First owner (init): a private /start <code> or the bare code. The daemon isn't installed yet."""
    _no_polling_in_channel_mode(conn)
    return _poll_direct(conn, owner, code, want_group=False, wait_s=wait_s, sleep=sleep)


def _no_polling_in_channel_mode(conn: sqlite3.Connection) -> None:
    if store.get_state(conn, "inbound") == "channel":
        raise RuntimeError("inbound is the Claude Code channel, which owns the bot's updates; use `finnamon owner add <name> --user-id <id>`")


def add_by_id(conn: sqlite3.Connection, owner: str, telegram_user_id: int) -> dict:
    """Channel mode: the Claude Code Telegram plugin already gates who may talk (pairing); Finnamon only needs the
    id to attribute what they say. `/telegram:access` in the channel session lists the ids."""
    other = conn.execute("SELECT owner FROM owners WHERE telegram_user_id=? AND owner<>?", (telegram_user_id, owner)).fetchone()
    if other:
        raise ValueError(f"Telegram user {telegram_user_id} is already {other[0]}")
    before = conn.execute("SELECT telegram_user_id FROM owners WHERE owner=?", (owner,)).fetchone()
    conn.execute("INSERT INTO owners (owner, display_name, telegram_user_id) VALUES (?,?,?) "
                 "ON CONFLICT(owner) DO UPDATE SET telegram_user_id=excluded.telegram_user_id",
                 (owner, owner.title(), telegram_user_id))   # new rows are named like the code dance names them
    out = {"owner": owner, "telegram_user_id": telegram_user_id}
    if before and before[0] not in (None, telegram_user_id):
        out["replaced"] = before[0]   # a typo here makes the real person a stranger; make the change visible
    return out


def add_member(conn: sqlite3.Connection, owner: str, code: str, wait_s: int = 600, sleep=time.sleep, switch: bool = False) -> dict:
    """Second+ owner: the code must be sent in a group the bot is in. Via the daemon if it is polling, else directly."""
    _no_polling_in_channel_mode(conn)
    me = telegram.get_me()
    if not me.get("can_read_all_group_messages"):
        raise RuntimeError("privacy mode is on: in @BotFather run /setprivacy → Disable, then remove and re-add the bot to the group")
    if not daemon_alive(conn):
        return _poll_direct(conn, owner, code, want_group=True, wait_s=wait_s, sleep=sleep, switch=switch)
    deadline = conn.execute("SELECT datetime('now','localtime', ?)", (f"+{wait_s} seconds",)).fetchone()[0]
    store.set_state(conn, "pending_owner", json.dumps({"owner": owner, "code": code, "deadline": deadline, "switch": switch}))
    deadline = time.time() + wait_s
    while time.time() < deadline:
        r = conn.execute("SELECT telegram_user_id FROM owners WHERE owner=? AND telegram_user_id IS NOT NULL", (owner,)).fetchone()
        if r and store.get_state(conn, "pending_owner") is None:
            return {"owner": owner, "telegram_user_id": r[0], "chat_id": store.get_state(conn, "chat_id")}
        sleep(2)
    store.set_state(conn, "pending_owner", None)
    raise TimeoutError(f"no message with the code {code} received in a group; run the command again"
                       + ("" if switch else " (a code sent in another group counts only with --new-group)"))
