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

from . import config, store, telegram
from .telegram import esc

WAIT_S = 600   # the hook's own "timeout" in settings.json sits above this, so the deny is ours and the chat hears it
POLL_S = 1.0
KEY = "permission:"
TAG = "[telegram · "   # web/talk.js telegramPrompt: a phone message typed into the intercom
DETAIL_MAX = 3000   # escaped characters shown in full; a Telegram message holds 4096. Longer: no Allow on the phone
CALLBACK = re.compile(r"^perm:([0-9a-f]{16}):(allow|deny)$")


def _hidden(s: str) -> str:
    """Controls, format characters (bidi marks, zero-width), separators, private-use and unassigned code points and
    variation selectors shown escaped: what the phone shows is what runs. A newline stays a line break (in Bash it
    separates commands, so it must look like one, not like the six characters \\u000a a command could also contain)."""
    return "".join(c if c == "\n" else f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cn")
                   or 0xFE00 <= ord(c) <= 0xFE0F or 0xE0100 <= ord(c) <= 0xE01EF else c for c in s)


def describe(tool: str, inp: dict) -> tuple[str, bool]:
    """What the phone shows, HTML: the tool and what it would touch (the command, the whole address, the path), and
    whether that is all of it. Never a cut-down version with an Allow under it: a memo-led command can hide its real
    work after a harmless start, and a fetch can carry the rows out in its query string, so the person sees every
    character that would run, or the phone only offers Deny."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "WebFetch" and set(inp) <= {"url", "prompt"}:
        detail = str(inp.get("url") or "")
        host = urlsplit(detail).hostname or ""
        if host and not host.isascii():   # a look-alike name (Cyrillic і for i) shows as the bytes DNS will resolve
            try:
                detail += f" (host {host.encode('idna').decode()})"
            except UnicodeError:
                detail += " (host not a valid name)"
        if inp.get("prompt"):
            detail += f"\nasking it: {inp['prompt']}"
    elif tool == "WebSearch" and set(inp) <= {"query"}:
        detail = str(inp.get("query") or "")
    elif tool == "Bash" and set(inp) <= {"command", "description", "timeout"}:
        detail = str(inp.get("command") or "")
    elif tool == "Read" and set(inp) <= {"file_path", "offset", "limit"}:
        detail = str(inp.get("file_path") or "")
    else:   # Write's content, Edit's old and new text, an agent's prompt: the whole input, or no Allow
        detail = json.dumps(inp, ensure_ascii=False, indent=1)
    shown = esc(_hidden(detail))
    whole = len(shown.encode("utf-16-le")) // 2 <= DETAIL_MAX   # Telegram counts UTF-16 units
    if not whole:
        shown = f"{shown[:300].rsplit('&', 1)[0] if '&' in shown[290:300] else shown[:300]}… ({len(detail)} characters: too long to check on a phone)"
    what = {"WebFetch": "open", "WebSearch": "search the web for", "Bash": "run"}.get(tool, f"use {tool} on")
    return f"🔐 The assistant asks to {esc(what)}: <code>{shown}</code>", whole


TAIL_BYTES = 2 << 20   # the first read: the newest prompt and its tool call sit at the end of a transcript that only grows


def _entries(path: str, offset: int | None = None) -> tuple[list[dict], int]:
    """Whole lines of the transcript from offset (None: its last TAIL_BYTES); a line still being written waits for the
    next read. A turn whose prompt lies further back than the tail is not recognised as a phone turn: the dashboard's
    dialog still asks, the phone just is not told."""
    try:
        with open(path, "rb") as f:
            if offset is None:
                offset = max(0, f.seek(0, os.SEEK_END) - TAIL_BYTES)
                skip = offset > 0
            else:
                skip = False
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset or 0
    if skip:   # started mid-line
        cut = data.find(b"\n") + 1
        data, offset = data[cut:], offset + cut
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


# A Codex session's transcript is its rollout (`transcript_path` in every Codex hook payload): JSONL of {type, payload}.
# Codex 0.157 runs tools in code mode, so its response_items do not say which tool ran; its event_msg item_completed
# items do (docs/designs/codex-spike.md, finding 5). A tool call has no item until it completes, so the call a dialog is
# about is found once its result lands, which is all dashboard_answered() needs.
CODEX_CALLS = ("CommandExecution", "FileChange", "McpToolCall")


def _codex(entries: list[dict]) -> bool:
    return any(e.get("type") in ("event_msg", "response_item", "session_meta") for e in entries)


def _codex_items(entries: list[dict]) -> list[dict]:
    return [p["item"] for e in entries if e.get("type") == "event_msg" and isinstance(p := e.get("payload"), dict)
            and p.get("type") == "item_completed" and isinstance(p.get("item"), dict)]


def _codex_call(item: dict, tool: str, inp) -> bool:
    """Whether a completed rollout item is this hook's call. apply_patch's FileChange keeps no patch text: never a match."""
    inp = inp if isinstance(inp, dict) else {}
    if item.get("type") == "CommandExecution":
        return tool == "Bash" and (item.get("command") or [None])[-1] == inp.get("command")
    if item.get("type") == "McpToolCall":
        return tool == f"mcp__{item.get('server')}__{item.get('tool')}" and item.get("arguments") == inp
    return False


def telegram_turn(entries: list[dict]) -> bool:
    """Whether the newest prompt (not a tool result, not Claude Code's own tags) is a phone message."""
    if _codex(entries):   # a Codex prompt is a UserMessage item; its environment context is a response_item, never one
        texts = ["\n".join(c.get("text", "") for c in it.get("content") or [] if isinstance(c, dict) and c.get("type") == "text").strip()
                 for it in _codex_items(entries) if it.get("type") == "UserMessage"]
        return bool(texts) and texts[-1].startswith(TAG)
    last = ""
    for e in entries:
        if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
            continue
        t = _user_text(e).strip()
        if t and (not t.startswith("<") or t.startswith(("<command-name>", "<bash-input>"))):   # a dashboard /command or ! line is a prompt too
            last = t
    return last.startswith(TAG)


def _blocks(e: dict, kind: str) -> list[dict]:
    c = (e.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict) and b.get("type") == kind] if isinstance(c, list) else []


def results(entries: list[dict]) -> set:
    """The ids of every tool call that has its result (run, refused, or answered No at the dashboard)."""
    if _codex(entries):
        return {it.get("id") for it in _codex_items(entries) if it.get("type") in CODEX_CALLS}
    return {b.get("tool_use_id") for e in entries if e.get("type") == "user" for b in _blocks(e, "tool_result")}


def tool_use_id(entries: list[dict], tool: str, inp, done: set) -> str | None:
    """The newest call of this tool with exactly this input that has no result yet: the one the dialog is about. No
    guessing on a near match: a parallel call of the same tool finishing would read as the dashboard's answer and take
    the buttons away from a dialog still up. Without a match the phone keeps its buttons until a tap or the timeout."""
    if _codex(entries):
        calls = [it for it in _codex_items(entries) if _codex_call(it, tool, inp) and it.get("id") not in done]
        return calls[-1].get("id") if calls else None
    calls = [b for e in entries if e.get("type") == "assistant" for b in _blocks(e, "tool_use")
             if b.get("name") == tool and b.get("input") == inp and b.get("id") not in done]
    return calls[-1].get("id") if calls else None


def denied_by(tool: str, inp: dict, deny: list[str]) -> str | None:
    """The deny rule this request falls under, if any. Claude Code checks its deny list before it ever asks, so this
    never fires in practice; it is here so a phone can never be the one to approve something the list refuses.
    ponytail: an approximation of Claude Code's matcher (fnmatch's * crosses /, no splitting of compound commands); it can
    only ever deny more, and Claude Code's own check is the real one."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == FINNAMON_TOOL:   # Codex's finnamon(argv): the Bash(finnamon …) rules are its rules
        tool, inp = "Bash", {"command": _argv_command(inp)}
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


# What no tool may touch, whoever approves it: the secrets, the database, the dashboard's key and session id, imported
# statements, the bot's token and the two browser profiles holding bank and personal cookies. Read/Edit deny rules only
# cover those tools; this covers Bash, Grep, Glob, an agent's prompt, anything. The bundle's PreToolUse hook (`finnamon
# hook secret-guard`) runs it before any permission check, so it is never offered on the phone or the dashboard; the
# Bash(*...*) deny rules in settings.json are the second layer.
# ponytail: a string guard over the call's input, case-insensitive, for the forms a path is written in (~, $HOME, ${HOME},
# /Users/<name>, /home/<name>, this box's own home); a path assembled at run time (variables, globs, base64) gets past it,
# and the person approving still sees the whole command.
PROTECTED = (r"\.finnamon/secrets\.toml", r"\.finnamon/finnamon\.db", r"\.finnamon/web-token", r"\.finnamon/intercom\.json",
             r"\.finnamon/imports(?![\w.-])", r"\.finnamon/chrome(?![\w.-])",
             r"\.finnamon/chrome-extension-test(?![\w.-])", r"\.finnamon/claude-import(?![\w.-])", r"\.finnamon/downloads(?![\w.-])", r"\.claude/channels/telegram/\.env",
             r"\.agent-browser(?![\w.-])")
_HOMES = r"(?:~|\$home|\$\{home\}|/users/[^/\s'\"]+|/home/[^/\s'\"]+)"
_PROTECTED_RE = re.compile(rf"{_HOMES}/(?:{'|'.join(PROTECTED)})", re.IGNORECASE)
# A Bash command that names the folder and, anywhere, one of its protected files (`cd ~/.finnamon && cat secrets.toml`).
_IN_FOLDER_RE = re.compile(r"\.finnamon\b.*\b(?:secrets\.toml|finnamon\.db|web-token|intercom\.json|imports|chrome|claude-import|downloads)\b"
                           r"|\.claude/channels\b.*\.env\b", re.IGNORECASE | re.DOTALL)
SAYS_ONLY = ("mcp__plugin_telegram_telegram__reply", "mcp__plugin_telegram_telegram__react")   # words to people, not file access
FINNAMON_TOOL = "mcp__finnamon__finnamon"   # Codex's finnamon(argv) tool (finnamon/mcp_server.py)
_PATCH_PATHS = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): *(.+?) *$", re.MULTILINE)


def _argv_command(inp) -> str:
    argv = inp.get("argv") if isinstance(inp, dict) else None
    return " ".join(map(str, argv)) if isinstance(argv, list) else ""


def _codex_paths(tool: str, inp, cwd: str | None) -> list[str]:
    """The files a Codex apply_patch or view_image call names, made absolute against the call's cwd: a patch names them
    relative to it (`*** Update File: ../secrets.toml` from the assistant directory is the household's secrets)."""
    if not isinstance(inp, dict):
        return []
    found = _PATCH_PATHS.findall(str(inp.get("command") or "")) if tool == "apply_patch" else [str(inp.get("path") or "")] if tool == "view_image" else []
    return [os.path.normpath(os.path.join(cwd or "", os.path.expanduser(p))) for p in found if p]


def protected_path(tool: str, inp, home: str | None = None, cwd: str | None = None) -> str | None:
    """The protected path this call's input names, or None. Claude Code's and Codex's tool names and input shapes alike
    (tests/fixtures/codex/hooks): Bash with a command string, apply_patch, view_image, any MCP tool's arguments."""
    if tool in SAYS_ONLY:
        return None
    try:
        text = json.dumps(inp, ensure_ascii=False) if not isinstance(inp, str) else inp
    except (TypeError, ValueError):
        text = str(inp)
    text = "\n".join([text.replace("\\/", "/"), *_codex_paths(tool, inp, cwd)])
    fhome = os.environ.get("FINNAMON_HOME", "").rstrip("/")
    if fhome:   # a household kept somewhere else (FINNAMON_HOME) is the same folder
        text = re.sub(re.escape(fhome) + r"(?=/|\b)", "~/.finnamon", text, flags=re.IGNORECASE)
    home = (home or str(Path.home())).rstrip("/")
    if home:   # this box's own home, whatever its root (/var/root, /private/...)
        text = re.sub(re.escape(home) + r"(?=/)", "~", text, flags=re.IGNORECASE)
    cmd = _argv_command(inp) if tool == FINNAMON_TOOL else str(inp.get("command") or "") if tool == "Bash" and isinstance(inp, dict) else ""
    m = _PROTECTED_RE.search(text) or (cmd and _IN_FOLDER_RE.search(cmd))
    return m.group(0) if m else None


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
    if (hit := protected_path(tool, inp, cwd=str(event.get("cwd") or "") or None)):   # the PreToolUse guard should have stopped it already; never ask about it
        return _decision("deny", f"{hit} is off limits to every tool")
    rule = denied_by(tool, inp, _settings_deny(str(event.get("cwd") or ".")) if deny is None else deny)
    if rule:
        return _decision("deny", f"{rule} is on the deny list")
    if tool in ("AskUserQuestion", "ExitPlanMode"):
        return None   # a question or a plan for the person at the screen: an Allow from the phone would answer it with nothing
    if any(env.get(k) for k in config.AGENT_MARKERS) or env.get("FINNAMON_IMPORT_SESSION") or env.get("FINNAMON_TRIAGE"):
        return None   # an unattended run or the import session: nobody on the phone answers for those
    path = str(event.get("transcript_path") or "")
    entries, offset = _entries(path) if path else ([], 0)
    if not telegram_turn(entries):
        return None
    chat = store.get_state(conn, "chat_id")
    if not chat:
        return None
    # A hook killed before its finally (claude restarted under it) leaves its row; nothing can answer it now.
    # A wall-clock margin of a whole wait: a clock stepped forward (a wake from sleep) must not take a live row.
    conn.execute("DELETE FROM state WHERE key LIKE ? AND json_extract(value, '$.deadline') < ?", (KEY + "%", time.time() - wait_s))
    done = results(entries)
    tid = tool_use_id(entries, tool, inp, done)
    entries = []   # from here on only what is new is read: the household's transcript only grows
    rid = secrets.token_hex(8)
    text, whole = describe(tool, inp)
    buttons = [{"text": "✅ Allow", "callback_data": f"perm:{rid}:allow"}] if whole else []
    buttons.append({"text": "❌ Deny", "callback_data": f"perm:{rid}:deny"})
    ask_line = "Allow it? (or answer at the dashboard)" if whole else "Too long to approve from here: answer at the dashboard, or deny it."
    # The row goes in before the buttons go out: a tap can reach the daemon before send_message returns here.
    row = {"status": "waiting", "chat": str(chat), "message_id": None, "text": text, "whole": whole, "deadline": time.time() + wait_s}
    waiting = json.dumps(row)
    store.set_state(conn, KEY + rid, waiting)
    try:
        mid = telegram.send_message(chat, f"{text}\n{ask_line}", reply_markup={"inline_keyboard": [buttons]})
    except telegram.TelegramError as e:
        print(f"finnamon: permission prompt not sent to Telegram: {e}", file=sys.stderr)
        conn.execute("DELETE FROM state WHERE key=?", (KEY + rid,))
        return None
    deadline, wall_deadline = clock() + wait_s, row["deadline"]   # wall: what press() checks, and it runs on through a sleep

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
    parent = os.getppid()
    try:
        with_mid = json.dumps({**row, "message_id": mid})
        if _settle(conn, rid, waiting, json.loads(with_mid)):   # else a tap already settled it; the loop reads it
            waiting = with_mid
        while True:
            if os.getppid() != parent:   # claude died under us (a SIGKILL forwards nothing): nobody would read a decision
                raise SystemExit(1)
            row = json.loads(store.get_state(conn, KEY + rid) or "{}")
            if not row:   # swept from under us; the request can no longer be answered from the phone
                settled = True
                finish("⏱ This request expired before anyone answered.")
                return _decision("deny", "The phone request expired before anyone answered.")
            if row.get("status") in ("allow", "deny"):   # a press; the daemon already updated the message
                settled = True
                if dashboard_answered():   # the dashboard got there first; the tap changed nothing
                    finish(f"🖥 Answered at the dashboard first; {esc(row.get('by') or 'the household')}'s tap came too late.")
                    return None
                return _decision(row["status"], f"Denied on Telegram by {row.get('by') or 'the household'}.")
            if dashboard_answered() and _settle(conn, rid, waiting, {"status": "dashboard", "deadline": wall_deadline}):
                settled = True
                finish("🖥 Answered at the dashboard.")
                return None
            if clock() >= deadline or time.time() >= wall_deadline:
                if _settle(conn, rid, waiting, {"status": "timeout", "deadline": wall_deadline}):
                    settled = True
                    finish(f"⏱ No answer within {round(wait_s / 60)} minutes, so it was denied.")
                    return _decision("deny", f"Nobody answered on Telegram or at the dashboard within {round(wait_s / 60)} minutes.")
                continue
            sleep(poll_s)   # a lost settle is a press landing in the same instant: read on the next pass
    finally:
        try:
            if not settled and _settle(conn, rid, waiting, {"status": "gone", "deadline": wall_deadline}):
                finish("The assistant stopped before anyone answered.")
            conn.execute("DELETE FROM state WHERE key=?", (KEY + rid,))
        except sqlite3.Error as e:   # a busy database: the sweep takes the row later, and press() refuses it past its deadline
            print(f"finnamon: could not clear the permission request: {e}", file=sys.stderr)
            if not settled:
                finish("The assistant stopped before anyone answered.")


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
    # message_id is None only between the hook storing the row and Telegram returning the id; the id in the button
    # (64 random bits, only ever in that message) is what ties a tap to its request.
    if row.get("status") != "waiting" or row.get("deadline", 0) < time.time() or row.get("message_id") not in (None, msg.get("message_id")):
        try:
            telegram.edit_message(chat, msg.get("message_id"), f"{esc(str(msg.get('text') or 'Permission request')[:3000])}\n(no longer waiting)")
        except telegram.TelegramError:
            pass
        return "That request is no longer waiting."
    if behavior == "allow" and not row.get("whole", True):
        return "Too long to approve from the phone; answer it at the dashboard."
    if not _settle(conn, rid, waiting, {"status": behavior, "by": owner[0], "deadline": row.get("deadline")}):
        return "That request was just answered."
    note = f"✅ Allowed by {esc(owner[0])}." if behavior == "allow" else f"❌ Denied by {esc(owner[0])}."
    try:
        telegram.edit_message(chat, msg.get("message_id"), f"{row.get('text', '')}\n{note}")
    except telegram.TelegramError:
        pass
    return "Allowed." if behavior == "allow" else "Denied."
