"""The Codex side of the assistant: what `finnamon install`, `init`, `update` and `doctor` need for a household whose
`assistant` setting is codex (docs/designs/codex-spike.md). Nothing here is maintained by hand:

- the bundle Codex reads in the assistant directory, AGENTS.md and .agents/skills/*, is generated from the Claude files
  (assistant.codex_bundle(); assistant.files() ships both, so the manifest, the .bak rule and the removal of a dropped
  skill hold for both);
- FINNAMON_HOME/codex is Codex's own home for these sessions (CODEX_HOME), so the person's ~/.codex config, MCP servers
  and skills never reach the household's assistant. Its config.toml is generated from .claude/settings.json: a permission
  profile (never `sandbox_mode`: under it an allow rule escapes the sandbox, spike finding 1), the hooks with their trust
  pinned by hash, no web search, no ChatGPT-apps server. Pinned like settings.json: a release always wins, an edited
  copy goes to .bak;
- the login is shared, not repeated: CODEX_HOME/auth.json is a symlink to ~/.codex/auth.json, and a token refresh writes
  through it (spike finding 6). Only when there is no such file does `init` run `codex login` under Finnamon's home.
  Never `codex logout` there: it revokes the tokens the link shares, logging the person out everywhere.

Codex 0.157 has no switch for the user skill root (~/.agents/skills, read whatever CODEX_HOME says; the feature
`skip_host_skill_discovery` is unfinished and changes nothing, and `[[skills.config]]` disables only paths it lists), so
every Codex session Finnamon starts gets HOME = FINNAMON_HOME/codex/home, an empty directory (env()). Tool commands and
hooks get the real HOME back through shell_environment_policy, and FINNAMON_HOME explicitly."""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

from . import assistant, config

MIN_VERSION = (0, 157, 0)
TESTED_VERSION = (0, 157, 0)   # what the rollout parser and the hook payloads were recorded on (tests/fixtures/codex)
PROFILE = "finnamon"
CONFIG = "config.toml"
TIMEOUT_S = 30
INSTALL_HINT = "npm i -g @openai/codex"


def home() -> Path:
    """CODEX_HOME for every Codex session Finnamon starts."""
    return config.home() / "codex"


def seal_home() -> Path:
    """The HOME those sessions see: empty, so Codex's user skill root (~/.agents/skills) holds nothing of the person's."""
    return home() / "home"


def user_auth() -> Path:
    """The person's own Codex login. Read only to link it; nothing here ever writes ~/.codex."""
    return Path.home() / ".codex" / "auth.json"


def binary() -> str | None:
    return shutil.which(os.environ.get("FINNAMON_CODEX_BIN") or "codex")   # an override that is not an executable is no codex


def env() -> dict:
    return {**os.environ, "CODEX_HOME": str(home()), "HOME": str(seal_home()), "FINNAMON_HOME": str(config.home())}


# --- config.toml from settings.json -----------------------------------------------------------------------------------

def _q(s: str) -> str:
    return json.dumps(s)   # a JSON string is a TOML basic string


def _inline(d: dict) -> str:
    return "{ " + ", ".join(f"{k} = {_q(v)}" for k, v in d.items()) + " }"


def denied() -> list[str]:
    """What the profile keeps every tool (shell, apply_patch, view_image) from reading or writing: the household's secrets
    and data, which the assistant reaches only through `finnamon`, and the login the sealed home shares."""
    h, real = config.home(), Path.home()
    paths = [h / "secrets.toml", h / "finnamon.db", h / "finnamon.db-wal", h / "finnamon.db-shm", h / "finnamon.db-journal",
             h / "backups", config.web_token_path(), h / "web-hosts", h / config.INTERCOM_FILE, h / "imports", h / "chrome",
             home() / "sessions", home() / "auth.json", user_auth(), real / ".agent-browser", real / ".claude" / "channels"]
    return list(dict.fromkeys(str(p) for p in paths))


def _hooks(settings: dict) -> list[tuple[str, str, dict]]:
    """(event, matcher, handler) from settings.json, minus the Telegram channel plugin's own (Claude Code only)."""
    out = []
    for event in ("PreToolUse", "PermissionRequest"):
        for group in (settings.get("hooks") or {}).get(event) or []:
            if str(group.get("matcher", "")).startswith("mcp__plugin_telegram"):
                continue
            out.append((event, group.get("matcher", "*"), [h for h in group.get("hooks") or [] if h.get("type") == "command"]))
    return out


def render_config(d: Path | None = None, state: dict[str, str] | None = None) -> str:
    """config.toml for CODEX_HOME. `state`: {hook key: "sha256:…"} from pin(); without it the hooks are untrusted and Codex
    skips them, which doctor and harness_problems() refuse."""
    d = d or assistant.dir()
    settings = json.loads(assistant.files()[".claude/settings.json"])
    sealed_env = {"FINNAMON_FROM_AGENT": "1", "HOME": str(Path.home()), "FINNAMON_HOME": str(config.home())}
    lines = ["# Generated by finnamon from the assistant bundle (.claude/settings.json). `finnamon install` and `update`",
             "# rewrite it; an edited copy is kept as config.toml.bak.",
             f"default_permissions = {_q(PROFILE)}",
             'approval_policy = "on-request"',
             'web_search = "disabled"',
             'cli_auth_credentials_store = "file"',
             "",
             "[features]",
             "hooks = true",
             "apps = false   # codex_apps, the built-in ChatGPT-apps MCP server",
             "browser_use = false", "browser_use_external = false", "in_app_browser = false", "computer_use = false",
             "",
             "[shell_environment_policy]",
             f"set = {_inline(sealed_env)}",
             "",
             f"[permissions.{PROFILE}]",
             'extends = ":workspace"',
             "",
             f"[permissions.{PROFILE}.filesystem]",
             '":workspace_roots" = "read"',
             f"{_q(str(d))} = \"read\"",
             *(f"{_q(p)} = \"deny\"" for p in denied()),
             "",
             "[mcp_servers.finnamon]   # finnamon(argv): the CLI outside the sandbox, on the bundle's allow list (mcp_server.py)",
             'command = "finnamon"',
             'args = ["serve"]',
             f"env = {_inline(sealed_env)}",
             'env_vars = ["FINNAMON_TRIAGE", "FINNAMON_IMPORT_SESSION"]   # the read-only runs stay read-only through the tool',
             'enabled_tools = ["finnamon"]',
             "",
             "[mcp_servers.finnamon.tools.finnamon]",
             'approval_mode = "approve"   # the tool refuses what the allow list does not cover, and never asks',
             "",
             f"[projects.{_q(str(d))}]",
             'trust_level = "trusted"']
    for event, matcher, handlers in _hooks(settings):
        lines += ["", f"[[hooks.{event}]]", f"matcher = {_q(matcher)}"]
        for h in handlers:
            lines += [f"[[hooks.{event}.hooks]]", 'type = "command"', f"command = {_q(h['command'])}"]
            if h.get("timeout"):
                lines.append(f"timeout = {int(h['timeout'])}")
    if state:
        lines += ["", "[hooks.state]", *(f"{_q(k)} = {{ trusted_hash = {_q(v)} }}" for k, v in sorted(state.items()))]
    return "\n".join(lines) + "\n"


# --- talking to codex -------------------------------------------------------------------------------------------------

def app_server(exe: str, method: str, params: dict, timeout: float = TIMEOUT_S) -> dict:
    """One request to `codex app-server` (stdio JSON-RPC) under Finnamon's CODEX_HOME. No model call, no login needed."""
    p = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, bufsize=1, env=env(), cwd=assistant.dir() if assistant.dir().is_dir() else None)
    try:
        for msg in ({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "finnamon", "version": "1"}}},
                    {"method": "initialized"}, {"id": 1, "method": method, "params": params}):
            p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()   # stdin stays open: the server exits on EOF before it answers
        deadline, buf, fd = time.monotonic() + timeout, b"", p.stdout.fileno()
        while True:
            while b"\n" in buf:   # every whole line read so far, before waiting on the pipe again
                line, buf = buf.split(b"\n", 1)
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if isinstance(m, dict) and m.get("id") == 1:
                    if "error" in m:
                        err = m["error"]
                        raise RuntimeError(f"codex app-server {method}: {err.get('message', err) if isinstance(err, dict) else err}")
                    return m.get("result") if isinstance(m.get("result"), dict) else {}
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise RuntimeError(f"codex app-server gave no answer to {method} within {timeout:.0f}s")
            chunk = os.read(fd, 65536)   # raw reads: a buffered readline would hide a second reply from select
            if not chunk:
                raise RuntimeError(f"codex app-server exited before answering {method}")
            buf += chunk
    finally:
        p.kill()
        p.wait()


def hooks(exe: str) -> list[dict]:
    """The hooks Codex sees for the assistant directory, from our config.toml only."""
    cfg = str(home() / CONFIG)
    res = app_server(exe, "hooks/list", {"cwds": [str(assistant.dir())]})
    return [h for e in res.get("data") or [] if isinstance(e, dict) for h in e.get("hooks") or []
            if isinstance(h, dict) and h.get("sourcePath") == cfg and h.get("key") and h.get("currentHash")]


def leaked_skills(exe: str) -> list[str]:
    """Skills a sealed session would load from outside the bundle and Codex's own system set: the person's, if the seal leaks."""
    res = app_server(exe, "skills/list", {"cwds": [str(assistant.dir())], "forceReload": True})
    return sorted(str(s.get("path")) for e in res.get("data") or [] if isinstance(e, dict) for s in e.get("skills") or []
                  if isinstance(s, dict) and s.get("scope") in ("user", "admin"))


def version(exe: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=TIMEOUT_S, env=env()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def logged_in(exe: str) -> tuple[bool, str]:
    try:
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=TIMEOUT_S, env=env())
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run `codex login status`: {e}"
    msg = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0 and any("logged in" in l.lower() for l in msg), (msg[-1] if msg else f"exit {r.returncode}")


def features(exe: str) -> dict[str, bool]:
    try:
        out = subprocess.run([exe, "features", "list"], capture_output=True, text=True, timeout=TIMEOUT_S, env=env()).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return {p[0]: p[-1] == "true" for p in (l.split() for l in out.splitlines()) if len(p) >= 2}


def login(exe: str) -> bool:
    """The fallback when there is no ~/.codex/auth.json to share: a login of Finnamon's own, in the terminal."""
    try:
        return subprocess.run([exe, "login"], env=env()).returncode == 0
    except OSError:
        return False


# --- install ----------------------------------------------------------------------------------------------------------

def link_auth() -> str:
    """Share the person's Codex login. Returns linked | relinked | own (a login of Finnamon's own: a real file) | none."""
    src, dst = user_auth(), home() / "auth.json"
    if dst.is_symlink():
        if os.readlink(dst) == str(src) and src.is_file():
            return "linked"
        if not src.is_file():
            return "none"   # a dangling link: the person logged out, or moved to the keychain
        dst.unlink()
        os.symlink(src, dst)
        return "relinked"
    if dst.exists():
        return "own"
    if not src.is_file():
        return "none"
    dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.symlink(src, dst)
    return "linked"


def pin(exe: str | None) -> dict[str, str]:
    """{hook key: hash} for our hooks, as Codex computes them (app-server hooks/list). Needs the hooks already in config.toml."""
    try:
        return {h["key"]: h["currentHash"] for h in hooks(exe)} if exe else {}
    except (OSError, RuntimeError):
        return {}


def pinned_state() -> dict[str, str]:
    """The [hooks.state] pins in config.toml now, as {key: hash}."""
    try:
        state = tomllib.loads((home() / CONFIG).read_text()).get("hooks", {}).get("state", {})
        return {k: v["trusted_hash"] for k, v in state.items() if isinstance(v, dict) and isinstance(v.get("trusted_hash"), str)}
    except (OSError, ValueError, AttributeError):
        return {}


def install(exe: str | None = None) -> dict:
    """Write CODEX_HOME's config.toml (the household's edited copy to .bak), pin the hooks' trust, link the login.
    Returns assistant.install()'s report plus {"auth": link_auth(), "pinned": n}."""
    exe = exe or binary()
    h = home()
    h.mkdir(mode=0o700, parents=True, exist_ok=True)
    seal_home().mkdir(mode=0o700, exist_ok=True)

    def write(state):
        return assistant.install(h, {CONFIG: render_config(state=state).encode()}, pinned=(CONFIG,))

    def trusted() -> dict[str, str] | None:   # Codex keys a hook by config path and position, and hashes its definition
        try:
            hs = hooks(exe)
        except (OSError, RuntimeError):
            return None
        return {x["key"]: x["currentHash"] for x in hs} if hs and all(x.get("trustStatus") == "trusted" for x in hs) else None

    # The hashes of what is on disk are the release's when the hook definitions are unchanged: then a steady state writes nothing.
    # When Codex cannot answer (not on this shell's PATH, an app-server failure) the pins on file stay: dropping them would
    # leave every hook untrusted and the household's sessions refused until the next update.
    res = write((pin(exe) if exe else {}) or pinned_state())
    if exe and trusted() is None and (fresh := pin(exe)):   # new or changed hooks: their hashes, now that they are written
        again = write(fresh)
        res = again | {k: res[k] + again[k] for k in ("written", "backed_up", "kept", "orphaned", "removed")} | {"changed": res["changed"] or again["changed"], "first": res["first"]}
    state = trusted() if exe else None
    return res | {"auth": link_auth(), "pinned": len(state or {})}


def configured() -> bool:
    """Has init (or an update since) set Codex up here? A Claude household's FINNAMON_HOME has no codex/ at all."""
    return (home() / CONFIG).is_file()


def _strings(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield f"{path}.{k}".lstrip("."), str(k)
            yield from _strings(v, f"{path}.{k}".lstrip("."))
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v, path)
    else:
        yield path, str(x)


def config_problems() -> list[str]:
    """What in config.toml would unseal the household's session, whoever wrote it there."""
    try:
        cfg = tomllib.loads((home() / CONFIG).read_text())
    except (OSError, ValueError) as e:
        return [f"cannot read {home() / CONFIG}: {e}"]
    out = [f"{where} = {v}: full access, no sandbox" for where, v in _strings(cfg) if "danger" in v.lower() or "bypass" in v.lower()]
    if "sandbox_mode" in cfg:
        out.append("sandbox_mode is set (under it an allow rule runs outside the sandbox); the permission profile is the only seal")
    if cfg.get("default_permissions") != PROFILE or (cfg.get("permissions") or {}).get(PROFILE, {}).get("extends") != ":workspace":
        out.append(f"the {PROFILE} permission profile is not the default")
    if cfg.get("web_search") != "disabled" or (cfg.get("features") or {}).get("apps") is not False:
        out.append("web search or the ChatGPT-apps server is on")
    if ((cfg.get("projects") or {}).get(str(assistant.dir())) or {}).get("trust_level") != "trusted":
        out.append(f"{assistant.dir()} is not a trusted project, so Codex ignores its config")
    return out


def problems(exe: str | None = None) -> list[str]:
    """What a sealed Codex session would be missing; the harness check refuses to start one while any is true."""
    exe = exe or binary()
    if not exe:
        return [f"`codex` is not on PATH ({INSTALL_HINT})"]
    if not configured():
        return [f"no {home() / CONFIG} (run `finnamon install` or `finnamon update`)"]
    try:
        hs = hooks(exe)
    except (OSError, RuntimeError) as e:
        return [f"could not list Codex's hooks: {e}"]
    if not hs:
        return [f"Codex sees none of the hooks in {home() / CONFIG}"]
    bad = [h["key"].split(":")[-3] if h["key"].count(":") >= 3 else h["key"] for h in hs if h.get("trustStatus") != "trusted"]
    return [f"Codex does not trust the {', '.join(sorted(set(bad)))} hook(s) (their pinned hash is out of date): run `finnamon update`"] if bad else []
