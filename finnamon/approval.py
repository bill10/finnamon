"""Permission prompts on the phone: the intercom's PermissionRequest hook (`finnamon hook permission`, the bundle's
.claude/settings.json) and the daemon's half, the Allow / Deny press.

The intercom runs in ask mode: anything off the allow list shows Claude Code's dialog in the dashboard's terminal. When
the turn in progress came from Telegram (session mode: the daemon typed it in as `[telegram · <owner>] ...`), nobody may
be at the dashboard, so the hook also puts the request in the household chat with Allow / Deny buttons and waits.
Claude Code shows its dialog while the hook runs, and dismisses it when the hook prints a decision, so the first answer
wins either way:

- a household member presses a button: the daemon (the bot's one reader) records it in `state`, the hook prints it;
- someone answers at the dashboard: the tool's result lands in the session's transcript, and the hook clears the buttons;
- nobody answers within WAIT_S: denied, and the chat is told.

One `state` row per request (`permission:<id>`), settled by compare-and-set, so a press, the timeout and the dashboard
cannot both win. The hook prints nothing (no decision: the dialog stays for the dashboard) whenever it is unsure: not a
Telegram turn, no chat, Telegram unreachable, an unattended `claude -p` (nobody can answer there; its web ban stands)."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from . import store, telegram
from .telegram import esc

WAIT_S = 600   # the hook's own "timeout" in settings.json sits above this, so the deny is ours and the chat hears it
POLL_S = 1.0
KEY = "permission:"
TAG = "[telegram · "   # web/talk.js telegramPrompt: a phone message typed into the intercom
DETAIL_MAX = 3000   # escaped characters shown in full; a Telegram message holds 4096. Longer: no Allow on the phone
CALLBACK = re.compile(r"^perm:([0-9a-f]{16}):(allow|deny)$")


def _hidden(s: str) -> str:
    """Controls, format characters (bidi marks, zero-width) and separators shown escaped: what the phone shows is what runs."""
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in s)


def describe(tool: str, inp: dict) -> tuple[str, bool]:
    """What the phone shows, HTML: the tool and what it would touch (the command, the whole address, the path), and
    whether that is all of it. Never a cut-down version with an Allow under it: a memo-led command can hide its real
    work after a harmless start, and a fetch can carry the rows out in its query string, so the person sees every
    character that would run, or the phone only offers Deny."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "WebFetch":
        detail = str(inp.get("url") or "")
        host = urlsplit(detail).hostname or ""
        if host and not host.isascii():   # a look-alike name (Cyrillic і for i) shows as the bytes DNS will resolve
            try:
                detail += f" (host {host.encode('idna').decode()})"
            except UnicodeError:
                detail += " (host not a valid name)"
    elif tool == "WebSearch":
        detail = str(inp.get("query") or "")
    else:
        detail = next((str(inp[k]) for k in ("command", "file_path", "notebook_path", "path", "url", "pattern") if inp.get(k)), "")
        if not detail:
            detail = next((str(v) for v in inp.values() if isinstance(v, str) and v), "")
    shown = esc(_hidden(detail))
    whole = len(shown) <= DETAIL_MAX
    if not whole:
        shown = f"{shown[:300].rsplit('&', 1)[0] if '&' in shown[290:300] else shown[:300]}… ({len(detail)} characters: too long to check on a phone)"
    what = {"WebFetch": "open", "WebSearch": "search the web for", "Bash": "run"}.get(tool, f"use {tool} on")
    return f"🔐 The assistant asks to {esc(what)}: <code>{shown}</code>", whole


def _entries(path: str, offset: int = 0) -> tuple[list[dict], int]:
    """Whole lines of the transcript from offset; a line still being written waits for the next read."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    out = []
    for line in data[:end].splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out, offset + end


def _user_text(e: dict) -> str:
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text") if isinstance(c, list) else ""


def telegram_turn(entries: list[dict]) -> bool:
    """Whether the newest prompt (not a tool result, not Claude Code's own tags) is a phone message."""
    last = ""
    for e in entries:
        if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
            continue
        t = _user_text(e).strip()
        if t and not t.startswith("<"):
            last = t
    return last.startswith(TAG)


def _blocks(e: dict, kind: str) -> list[dict]:
    c = (e.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict) and b.get("type") == kind] if isinstance(c, list) else []


def results(entries: list[dict]) -> set:
    """The ids of every tool call that has its result (run, refused, or answered No at the dashboard)."""
    return {b.get("tool_use_id") for e in entries if e.get("type") == "user" for b in _blocks(e, "tool_result")}


def tool_use_id(entries: list[dict], tool: str, inp, done: set) -> str | None:
    """The newest call of this tool with this input that has no result yet: the one the dialog is about. An input that
    differs (Claude Code adding a field) falls back to the newest open call of the same tool."""
    open_calls = [b for e in entries if e.get("type") == "assistant" for b in _blocks(e, "tool_use") if b.get("name") == tool and b.get("id") not in done]
    exact = [b for b in open_calls if b.get("input") == inp]
    return (exact or open_calls or [{}])[-1].get("id")


def denied_by(tool: str, inp: dict, deny: list[str]) -> str | None:
    """The deny rule this request falls under, if any. Claude Code checks its deny list before it ever asks, so this
    never fires in practice; it is here so a phone can never be the one to approve something the list refuses.
    ponytail: an approximation of Claude Code's matcher (fnmatch's * crosses /, no splitting of compound commands); it can
    only ever deny more, and Claude Code's own check is the real one."""
    inp = inp if isinstance(inp, dict) else {}
    arg = str(inp.get("command") or inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    home = str(Path.home())
    for rule in deny:
        m = re.fullmatch(r"([\w.:-]+)(?:\((.*)\))?", rule)
        if not m or m.group(1) != tool:
            continue
        pat = m.group(2)
        if pat is None:
            return rule
        pat = pat.replace("~", home, 1) if pat.startswith("~") else pat[1:] if pat.startswith("//") else pat
        target = arg if tool == "Bash" else os.path.expanduser(arg)
        if fnmatch.fnmatchcase(target, pat.replace("**", "*")) or (pat.endswith(" *") and target == pat[:-2]):
            return rule
    return None


def _decision(behavior: str, message: str = "") -> dict:
    d = {"behavior": behavior, **({"message": message} if behavior == "deny" and message else {})}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


def _settle(conn: sqlite3.Connection, rid: str, waiting: str, outcome: dict) -> bool:
    """Compare-and-set: only one of a press, the timeout and the dashboard moves a request off waiting."""
    return conn.execute("UPDATE state SET value=? WHERE key=? AND value=?", (json.dumps(outcome), KEY + rid, waiting)).rowcount == 1


def _settings_deny(cwd: str) -> list[str]:
    try:
        return list(json.loads((Path(cwd) / ".claude" / "settings.json").read_text())["permissions"]["deny"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def ask(event: dict, conn: sqlite3.Connection, *, deny: list[str] | None = None, wait_s: float = WAIT_S, poll_s: float = POLL_S,
        sleep=time.sleep, clock=time.monotonic, env=os.environ) -> dict | None:
    """The hook: a decision to print, or None (no decision; the dashboard's dialog stays)."""
    tool, inp = str(event.get("tool_name") or ""), event.get("tool_input") or {}
    rule = denied_by(tool, inp, _settings_deny(str(event.get("cwd") or ".")) if deny is None else deny)
    if rule:
        return _decision("deny", f"{rule} is on the deny list")
    if env.get("FINNAMON_FROM_CLAUDE") or env.get("FINNAMON_IMPORT_SESSION") or env.get("FINNAMON_TRIAGE"):
        return None   # an unattended run or the import session: nobody on the phone answers for those
    path = str(event.get("transcript_path") or "")
    entries, offset = _entries(path) if path else ([], 0)
    if not telegram_turn(entries):
        return None
    chat = store.get_state(conn, "chat_id")
    if not chat:
        return None
    # A hook killed before its finally (claude restarted under it) leaves its row; nothing can answer it now.
    conn.execute("DELETE FROM state WHERE key LIKE ? AND json_extract(value, '$.deadline') < ?", (KEY + "%", time.time()))
    done = results(entries)
    tid = tool_use_id(entries, tool, inp, done)
    entries = []   # from here on only what is new is read: the household's transcript only grows
    rid = secrets.token_hex(8)
    text, whole = describe(tool, inp)
    buttons = [{"text": "✅ Allow", "callback_data": f"perm:{rid}:allow"}] if whole else []
    buttons.append({"text": "❌ Deny", "callback_data": f"perm:{rid}:deny"})
    ask_line = "Allow it? (or answer at the dashboard)" if whole else "Too long to approve from here: answer at the dashboard, or deny it."
    try:
        mid = telegram.send_message(chat, f"{text}\n{ask_line}", reply_markup={"inline_keyboard": [buttons]})
    except telegram.TelegramError as e:
        print(f"finnamon: permission prompt not sent to Telegram: {e}", file=sys.stderr)
        return None
    waiting = json.dumps({"status": "waiting", "chat": str(chat), "message_id": mid, "text": text, "whole": whole, "deadline": time.time() + wait_s})
    store.set_state(conn, KEY + rid, waiting)
    deadline = clock() + wait_s

    def finish(note: str) -> None:
        try:
            telegram.edit_message(chat, mid, f"{text}\n{note}")
        except telegram.TelegramError as e:
            print(f"finnamon: could not update the permission prompt: {e}", file=sys.stderr)

    def dashboard_answered() -> bool:
        nonlocal offset, tid
        if not path:
            return False
        new, offset = _entries(path, offset)
        done.update(results(new))
        tid = tid or tool_use_id(new, tool, inp, set())   # the call's own line may land a moment after the dialog (with its result, even)
        return bool(tid) and tid in done

    def stop(signum, _frame):   # claude killed with the dialog up: the finally below still clears the row and the buttons
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):   # not the main thread (a test runner): the expired-row sweep above covers it
            pass
    settled = False
    try:
        while True:
            row = json.loads(store.get_state(conn, KEY + rid) or "{}")
            if row.get("status") in ("allow", "deny"):   # a press; the daemon already updated the message
                settled = True
                if dashboard_answered():   # the dashboard got there first; the tap changed nothing
                    finish(f"🖥 Answered at the dashboard first; {esc(row.get('by') or 'the household')}'s tap came too late.")
                    return None
                return _decision(row["status"], f"Denied on Telegram by {row.get('by') or 'the household'}.")
            if dashboard_answered() and _settle(conn, rid, waiting, {"status": "dashboard"}):
                settled = True
                finish("🖥 Answered at the dashboard.")
                return None
            if clock() >= deadline:
                if _settle(conn, rid, waiting, {"status": "timeout"}):
                    settled = True
                    finish(f"⏱ No answer within {round(wait_s / 60)} minutes, so it was denied.")
                    return _decision("deny", f"Nobody answered on Telegram or at the dashboard within {round(wait_s / 60)} minutes.")
                continue   # a press landed in the same instant: read it on the next pass
            sleep(poll_s)
    finally:
        if not settled and _settle(conn, rid, waiting, {"status": "gone"}):
            finish("The assistant stopped before anyone answered.")
        conn.execute("DELETE FROM state WHERE key=?", (KEY + rid,))


def press(conn: sqlite3.Connection, cq: dict) -> str:
    """The daemon's half: a button press (a callback_query). Returns what the presser is told (answerCallbackQuery)."""
    m = CALLBACK.match(str(cq.get("data") or ""))
    if not m:
        return ""
    rid, behavior = m.groups()
    msg = cq.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id"))
    if chat != str(store.get_state(conn, "chat_id")):
        return "This is not the household chat."
    owner = conn.execute("SELECT COALESCE(NULLIF(display_name, ''), owner) FROM owners WHERE telegram_user_id=?", ((cq.get("from") or {}).get("id"),)).fetchone()
    if not owner:
        return "Only the household can answer this."
    waiting = store.get_state(conn, KEY + rid)
    row = json.loads(waiting or "{}")
    if row.get("status") != "waiting" or row.get("deadline", 0) < time.time() or str(row.get("message_id")) != str(msg.get("message_id")):
        try:
            telegram.edit_message(chat, msg.get("message_id"), f"{msg.get('text') and esc(msg['text']) or 'Permission request'}\n(no longer waiting)")
        except telegram.TelegramError:
            pass
        return "That request is no longer waiting."
    if behavior == "allow" and not row.get("whole", True):
        return "Too long to approve from the phone; answer it at the dashboard."
    if not _settle(conn, rid, waiting, {"status": behavior, "by": owner[0]}):
        return "That request was just answered."
    note = f"✅ Allowed by {esc(owner[0])}." if behavior == "allow" else f"❌ Denied by {esc(owner[0])}."
    try:
        telegram.edit_message(chat, msg.get("message_id"), f"{row.get('text', '')}\n{note}")
    except telegram.TelegramError:
        pass
    return "Allowed." if behavior == "allow" else "Denied."
