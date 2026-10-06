"""The one place Finnamon invokes the household's assistant CLI headless: `claude -p`, or `codex exec --json` for a Codex
household. The `assistant` setting (store.assistant_kind) picks which; run(), classify_error(), harness_problems() and
terminate_all() dispatch on it, and the per-CLI parts are the _claude_*/_codex_* functions below. cwd = the assistant
directory (FINNAMON_HOME/assistant, where `finnamon install` writes the bundle: finnamon/assistant.py), hard timeout with
kill. Used by triage (fresh session per run) and the daemon's conversation thread (`--resume` the household session).
`claude_runner` is this module under its old name.

FINNAMON_CLAUDE_BIN overrides the binary (tests point it at a fake); CLAUDE_BIN (the dashboard's
own override, web/server.js) is honoured too, so both point at the same binary. FINNAMON_CODEX_BIN does the same for codex."""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from . import assistant, codex, config, store

log = logging.getLogger("finnamon.claude")

@dataclass
class Result:
    ok: bool
    text: str
    session_id: str | None
    error: str | None = None
    returncode: int | None = None
    usage: dict | None = None   # Codex: turn.completed's token counts, kept for a later doctor/usage line


def kind() -> str:
    """claude | codex, from the `assistant` setting. A database that cannot be read is a Claude household (every household
    before the setting existed) unless init set Codex up here: a busy database must not send a Codex household's turn to an
    unchecked claude, nor wipe its thread. Read-only and without store.connect()'s migrate: this runs on every triage run
    and every Telegram turn."""
    try:
        with closing(sqlite3.connect(f"{config.db_path().as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
            return store.assistant_kind(conn)
    except sqlite3.Error:
        return "codex" if codex.configured() else "claude"


def binary(which: str | None = None) -> str | None:
    if which == "codex":
        return codex.binary()
    return os.environ.get("FINNAMON_CLAUDE_BIN") or os.environ.get("CLAUDE_BIN") or shutil.which("claude")


def cwd() -> Path:
    """Where every household `claude` runs: the installed assistant bundle, never the checkout."""
    return assistant.dir()


def harness_problems() -> list[str]:
    """The permission boundary and the skills only exist if the headless CLI (`claude -p`, `codex exec`) runs in a
    directory that has them. An assistant directory nothing has written yet has none: refuse to run headless rather than
    run unguarded. A lapsed login is not a seal problem (the daemon refuses to start on these, which would stop syncing
    too): a run fails with "login expired" and doctor says how to log in."""
    if kind() == "codex":   # never run a Codex household under Claude's checks: the bundle, Codex's own home, its pinned hooks and a config that keeps the seal
        probs = assistant.problems(claude_trust=False) + codex.problems()
        return probs + codex.config_problems() if not probs else probs
    return assistant.problems()


# What the headless /triage run may do: read, and write verdicts. Nothing else, whatever settings.json allows,
# because its input is bank memos and a memo is attacker-controlled text.
TRIAGE_DISALLOWED = ["Bash(finnamon link *)", "Bash(finnamon normal *)", "Bash(finnamon budget set *)", "Bash(finnamon budget remove *)", "Bash(finnamon threshold *)",
                     "Bash(finnamon settings set *)", "Bash(finnamon category *)", "Bash(finnamon alias *)", "Bash(finnamon detect --draft *)",
                     "Bash(finnamon import *)", "Bash(finnamon account add *)", "Edit", "Write", "NotebookEdit"]

# What no `-p` run may do, triage or conversation: reach the web. At the dashboard or a terminal each fetch asks a person
# (settings.json no longer allows them; in session mode a Telegram turn's prompt goes to the chat, finnamon/approval.py),
# but every run() is unattended and reads bank memos, and a memo can say "post this to https://...". With `finnamon query`
# on the allow list that is the rows out the door with nobody asked. run() builds the one --disallowedTools tail itself,
# so no caller can leave them off, whatever a later settings.json allows.
UNATTENDED_DISALLOWED = ["WebSearch", "WebFetch"]


# Every claude child runs in its own process group (start_new_session below), so a supervisor killing the daemon leaves
# it alive: it finishes its turn, writes nowhere, and the restarted daemon then --resumes a session the orphan still
# holds. daemon.py calls terminate_all() on SIGTERM so `finnamon update` cannot produce that pair.
_LIVE: set[int] = set()
_LIVE_LOCK = threading.Lock()


def terminate_all() -> int:
    """Kill every assistant child (claude or codex) still running. Returns how many groups were signalled."""
    with _LIVE_LOCK:
        pgids = list(_LIVE)
        _LIVE.clear()
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return len(pgids)


def run(prompt: str, resume: str | None = None, timeout: int = 120, env_extra: dict | None = None, disallowed: list[str] | tuple[str, ...] = ()) -> Result:
    which = kind()
    exe = binary(which)
    if not exe:
        return Result(False, "", None, error=f"{which} not on PATH")
    build, parse = (_codex_cmd, _codex_result) if which == "codex" else (_claude_cmd, _claude_result)
    cmd = build(exe, prompt, resume, disallowed)
    # FINNAMON_FROM_AGENT: the CLI's human-only gates (cli._from_agent). The variable is the marker, not its value.
    # Codex: its sealed CODEX_HOME and empty HOME (codex.env()); FINNAMON_TRIAGE reaches the finnamon tool through env_vars.
    env = {**(codex.env() if which == "codex" else os.environ), "CLAUDECODE": "", "FINNAMON_FROM_AGENT": "1", **(env_extra or {})}
    try:
        # Own process group so a timeout kills the Bash tool's grandchildren (a slow `finnamon query`) too.
        proc = subprocess.Popen(cmd, cwd=cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return Result(False, "", None, error=str(e))
    with _LIVE_LOCK:
        _LIVE.add(proc.pid)   # start_new_session makes the child its own group leader, so pid == pgid
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)  # never block on a pipe a runaway grandchild kept open
        except subprocess.TimeoutExpired:
            log.warning("%s process group did not exit after SIGKILL", which)
        for f in (proc.stdout, proc.stderr):
            if f:
                f.close()
        with _LIVE_LOCK:
            _LIVE.discard(proc.pid)
        return Result(False, "", None, error=f"timeout after {timeout}s")
    with _LIVE_LOCK:
        _LIVE.discard(proc.pid)
    if which == "codex":   # the JSONL decides: a completed turn is the reply whatever the exit, a failed one says why
        res = parse(out)
        if res.ok or res.error != CODEX_INCOMPLETE or proc.returncode == 0:
            res.returncode = proc.returncode or None
            return res
    if proc.returncode != 0:   # no turn on stdout: stderr says why (a gone thread, a bad config, no login)
        err = (err or out or "").strip()[-400:]
        return Result(False, "", None, error=err or f"exit {proc.returncode}", returncode=proc.returncode)
    return parse(out)


def _claude_cmd(exe: str, prompt: str, resume: str | None, disallowed) -> list[str]:
    # The prompt goes right after -p: variadic options like --disallowedTools would otherwise swallow it.
    # --strict-mcp-config with no --mcp-config means no MCP servers at all. Without it a headless run loads whatever the
    # directory registers, and the Telegram channel plugin is registered local to the assistant directory: its server kills the
    # household session's copy about a second after starting, and that session never reconnects. So every triage run
    # ended the household's Telegram until something restarted the dashboard. These runs drive `finnamon` over Bash and
    # have never needed an MCP server.
    # --setting-sources project: the same seal `import --browser` puts on its session, for the same reason. The person's
    # own ~/.claude/settings.json may allow Bash outright, and this session is -p: it reads bank memos, which are text
    # whoever made the transaction chose, with nobody at the keyboard to refuse a command. The project allow list has
    # every finnamon verb triage needs and no bare Bash. It also drops the local source, so the channel plugin is not
    # enabled here either, which is the durable half of the line above.
    cmd = [exe, "-p", prompt, "--output-format", "json", "--setting-sources", "project", "--strict-mcp-config"]
    if resume:
        cmd += ["--resume", resume]
    # One --disallowedTools tail, built here and last: it is variadic, so anything after it is read as a tool name.
    # A caller's extra bans come in through `disallowed`; there is no way to hand this function raw argv.
    return cmd + ["--disallowedTools", *disallowed, *UNATTENDED_DISALLOWED]


def _claude_result(stdout: str) -> Result:
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError:
        return Result(True, stdout.strip(), None)
    if isinstance(body, list):  # some versions stream a list of events; the last has the result
        body = body[-1] if body else {}
    text = body.get("result") or ""
    if body.get("is_error"):
        return Result(False, text, body.get("session_id"), error=text[-400:] or "is_error")
    return Result(True, text, body.get("session_id"))


# Every Codex run is unattended, so its lock is on the command line, over whatever config.toml says: no approvals (nobody
# to ask), no web, no ChatGPT-apps server, and the finnamon profile as the default, on a read-only base (`:read-only`: nothing writable, not even /tmp; the
# denies still hold, and the finnamon tool runs outside the sandbox). --ignore-rules: no execpolicy allow rule from
# anywhere skips a check. Not --ignore-user-config: it skips CODEX_HOME/config.toml, which is the generated config with
# the profile, the hooks and the tool (spike, card 3 notes); the sealed CODEX_HOME already keeps ~/.codex/config.toml out.
CODEX_LOCK = ["-c", 'approval_policy="never"', "-c", 'web_search="disabled"', "-c", "features.apps=false",
              "-c", f'default_permissions="{codex.PROFILE}"', "-c", f'permissions.{codex.PROFILE}.extends=":read-only"']


def _codex_cmd(exe: str, prompt: str, resume: str | None, disallowed) -> list[str]:
    # `disallowed` has no Codex form: the triage write ban is the finnamon tool's own (FINNAMON_TRIAGE, mcp_server.py).
    # `--` before the positionals: a chat message starting with "-" is a prompt, not a flag.
    common = ["--json", "--skip-git-repo-check", "--ignore-rules", *CODEX_LOCK]
    if resume:   # `exec resume` has no -C; run() starts it in the assistant directory
        return [exe, "exec", "resume", *common, "--", resume, prompt]
    return [exe, "exec", *common, "-C", str(cwd()), "--", prompt]


CODEX_INCOMPLETE = "the Codex turn did not complete"


def _codex_result(stdout: str) -> Result:
    """`codex exec --json` prints one event per line (tests/fixtures/codex/exec.jsonl): thread.started carries the session
    id, the last agent_message item is the reply, and turn.failed is a failure; an error event counts only if the turn
    never completed after it (a stream that reconnected is not a failed turn). No turn.completed at all is not a success:
    an empty stream would otherwise read as a turn with nothing to say, and the household's message would vanish."""
    sid, text, error, usage, done = None, "", None, None, False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "thread.started":
            sid = ev.get("thread_id")
        elif t == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            text = ev["item"].get("text") or ""
        elif t in ("turn.failed", "error"):
            error = str((ev.get("error") or {}).get("message") or ev.get("message") or t)[-400:]
        elif t == "turn.completed":
            usage, error, done = ev.get("usage"), None, True
    error = error or (None if done else CODEX_INCOMPLETE)
    return Result(not error, text, sid, error=error, usage=usage)


def classify_error(err: str | None) -> str:
    """Claude's wording and Codex's. "busy" (a usage limit, a model at capacity) passes on its own and must never read
    as "login expired": Codex's usage-limit text mentions plans and tokens."""
    e = (err or "").lower()
    if "timeout" in e:
        return "timeout"
    if "not on path" in e:
        return "not installed"
    if ("session" in e and ("not found" in e or "no conversation" in e)) or "no rollout found" in e:   # Codex: `exec resume` of a gone thread
        return "session lost"
    if "context window" in e or "context_length_exceeded" in e:   # a thread too long to continue: the daemon starts a fresh one
        return "session lost"
    if any(w in e for w in ("at capacity", "usage limit", "rate limit", "too many requests", "overloaded")):
        return "busy"
    if any(w in e for w in ("config.toml", "error loading config", "invalid configuration", "unknown variant", "unknown field")):
        return "bad config"
    if any(w in e for w in ("login", "logged in", "log in", "sign in", "auth", "unauthorized", "token", "credential")):
        return "login expired"
    return "error"
