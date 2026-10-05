"""The assistant bundle: the household assistant's CLAUDE.md, permission set and skills, shipped inside the package
(finnamon/assistant_bundle/) and installed to FINNAMON_HOME/assistant/. Every `claude` Finnamon starts for the household
runs with that directory as cwd, so the checkout is developer-only and a wheel install has an assistant at all.

`install()` writes the bundle there and nowhere else in FINNAMON_HOME. A manifest of what it last wrote tells a file the
household edited (a tuned skill) from one an older release wrote: the first is kept as `<file>.bak` and reported, the
second is overwritten quietly."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import config

BUNDLE = Path(__file__).resolve().parent / "assistant_bundle"
BUNDLE_REL = "finnamon/assistant_bundle/"   # the same directory as git names it, for `finnamon update`'s changed-file list
MANIFEST = ".finnamon-bundle.json"   # {relative path: sha256} of the last install, inside the assistant directory
PINNED = (".claude/settings.json",)   # the permission set that seals unattended runs over bank memos: a release always wins, and the daemon restores it


def default_dir() -> Path:
    return config.home() / "assistant"


def dir() -> Path:   # noqa: A001 - `assistant.dir()` reads right at every call site
    # resolved: claude keys a directory's trust by the real path of its cwd, so a symlinked HOME must map to the same key
    return Path(os.environ.get("FINNAMON_ASSISTANT") or default_dir()).resolve()


def files() -> dict[str, bytes]:
    """The bundle's files by path relative to its root: an allowlist, so nothing a claude session, an install pointed at the
    source or an editor leaves there (settings.local.json, a manifest, .DS_Store, a swap file) is ever bundle content."""
    paths = [BUNDLE / "CLAUDE.md", BUNDLE / ".claude/settings.json", *sorted((BUNDLE / ".claude/skills").glob("*/SKILL.md"))]
    claude = {p.relative_to(BUNDLE).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    return claude | codex_bundle(claude)   # AGENTS.md and .agents/skills, generated from these: the same files for a Codex household


# Codex reads the same instructions as AGENTS.md and the skills from .agents/skills, with its own per-CLI snippets.
AGENTS_MD_MAX = 32 * 1024   # Codex's project_doc_max_bytes: past it the instructions are cut off

# Per-CLI snippets. Skill invocations are `$name` in Codex; the skill names come from the bundle itself.
SWAPS = ((".claude/skills/", ".agents/skills/"), ("claude -p", "codex exec"))
CHANNEL_ONLY = re.compile(r"Claude Code's (own )?Telegram channel")   # the channel plugin is Claude Code's; a Codex household never has it
# Codex's sandbox keeps the shell away from the database, so on Codex every `finnamon …` is the finnamon(argv) MCP tool
# (finnamon/mcp_server.py); the skills keep one wording for both CLIs and this line, after AGENTS.md's title, says so.
CODEX_TOOL_NOTE = ("**On Codex, every `finnamon …` command in these instructions and the skills is a call to the `finnamon` tool**"
                   " (MCP server `finnamon`), not a shell command: `argv` is the command line as a list, one string per"
                   ' argument (`finnamon query "SELECT 1"` is `["finnamon", "query", "SELECT 1"]`). Your shell cannot reach'
                   " the household's data; the tool can. There is no shell and no stdin: where a skill pipes text in through"
                   " `-` (a heredoc), pass the text itself as that argument, `$` signs and all. A command the tool refuses is"
                   " one a person runs in a terminal.\n")


def _items(text: str) -> list[list[str]]:
    """Lines grouped so that a list item or table row keeps its indented continuation lines."""
    out: list[list[str]] = []
    for line in text.split("\n"):
        if out and line.startswith("  ") and out[-1][0].lstrip().startswith("- "):
            out[-1].append(line)
        else:
            out.append([line])
    return out


def to_codex(text: str, skills: list[str]) -> str:
    text = "\n".join(l for item in _items(text) if not CHANNEL_ONLY.search(" ".join(item)) for l in item)
    for a, b in SWAPS:
        text = text.replace(a, b)
    if skills:
        text = re.sub(rf"(?<![\w/.~$-])/({'|'.join(map(re.escape, skills))})\b", r"$\1", text)
    return text


def codex_bundle(claude: dict[str, bytes]) -> dict[str, bytes]:
    """AGENTS.md and .agents/skills for Codex (finnamon/codex.py), from the Claude files: never maintained by hand."""
    names = sorted(rel.split("/")[2] for rel in claude if rel.startswith(".claude/skills/"))
    out = {}
    if "CLAUDE.md" in claude:
        title, _, rest = to_codex(claude["CLAUDE.md"].decode(), names).partition("\n\n")
        agents = f"{title}\n\n{CODEX_TOOL_NOTE}\n{rest}".encode()
        if len(agents) > AGENTS_MD_MAX:   # a release that outgrows it must say so, not ship cut-off instructions
            raise ValueError(f"AGENTS.md is {len(agents)} bytes; Codex reads only {AGENTS_MD_MAX}")
        out["AGENTS.md"] = agents
    for rel, data in claude.items():
        if rel.startswith(".claude/skills/"):
            out[".agents/" + rel[len(".claude/"):]] = to_codex(data.decode(), names).encode()
    return out


# The harness check demands every file the bundle ships, and never less than this floor: a wheel that lost the bundle
# would otherwise pass the check with an empty directory and start every household session with no allow list.
FLOOR = ("CLAUDE.md", ".claude/settings.json", ".claude/skills/finnamon/SKILL.md", ".claude/skills/triage/SKILL.md", ".claude/skills/import-browser/SKILL.md",
         "AGENTS.md", ".agents/skills/finnamon/SKILL.md", ".agents/skills/triage/SKILL.md", ".agents/skills/import-browser/SKILL.md")
REQUIRED = tuple(dict.fromkeys(FLOOR + tuple(files())))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_atomic(p: Path, data: bytes, mode: int | None = None, follow_symlinks: bool = False) -> None:
    """Write a private temp file next to the target and rename it over the target, with `mode` when given, else the
    target's mode. mkstemp: a name of its own, so two writers cannot truncate each other's file, created 0600 and given its
    final mode before any byte lands, so ~/.claude.json is never readable by others on the way. A symlink in the assistant
    directory is replaced by a real file, never followed: a link planted there must not turn an install into a write
    elsewhere; the one file written through its link is a dotfiles-managed ~/.claude.json (follow_symlinks)."""
    if follow_symlinks:
        p = Path(os.path.realpath(p))
    elif p.is_symlink():
        p.unlink()
    if mode is not None:
        final = mode
    else:
        try:
            final = p.stat().st_mode & 0o777
        except OSError:
            final = None
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:   # writes every byte (a bare os.write may return short), then closes the fd whatever happens
            if final is not None:
                os.fchmod(f.fileno(), final)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)   # never a half-written file left beside the target
        raise


def install(dest: Path | None = None, bundle: dict[str, bytes] | None = None, pinned: tuple[str, ...] = PINNED) -> dict:
    """Write the bundle. Returns {"dir", "written", "backed_up", "kept", "orphaned", "removed", "changed": bool, "first": bool}.

    A file the household edited (not what the manifest says we wrote) is left alone while the release ships the same
    content we wrote last time (`kept`), and replaced with a `.bak` beside it once the release changes it (`backed_up`).
    With no manifest (a directory we never wrote) every differing file counts as theirs and gets its .bak. A file the
    release dropped goes (`removed`) unless they edited it (`orphaned`: theirs now, no release will touch it again)."""
    dest = dest or dir()
    bundle = files() if bundle is None else bundle   # another bundle: Codex's config.toml in FINNAMON_HOME/codex (codex.install)
    if not bundle:   # a package that lost its bundle: never "installed" an empty directory and retired the sessions for it
        raise FileNotFoundError(f"no assistant bundle at {BUNDLE}; reinstall finnamon")
    first = not (dest / MANIFEST).is_file()   # never written by us before: an existing but empty directory counts
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)   # a fresh box: ~/.finnamon is 0700 whoever creates it first
    dest.mkdir(parents=True, exist_ok=True)
    try:
        last = json.loads((dest / MANIFEST).read_text())
    except (OSError, ValueError):
        last = {}
    if not isinstance(last, dict):   # household-writable: a shape we did not write is no manifest at all
        last = {}
    written, backed_up, kept, orphaned, removed, manifest = [], [], [], [], [], {}
    for rel, data in bundle.items():
        p = dest / rel
        manifest[rel] = _sha(data)
        try:
            current = p.read_bytes()
        except OSError:
            current = None
        if current == data:
            continue
        if current is not None and last.get(rel) != _sha(current):   # not what we wrote last time: the household's edit
            if last.get(rel) == manifest[rel] and rel not in pinned:   # and this release did not touch the file: theirs stands
                kept.append(str(p))
                continue
            bak = p.with_name(p.name + ".bak")
            n = 0
            while bak.exists() or bak.is_symlink():   # an earlier edit already saved there: never one edit over another
                n += 1
                bak = p.with_name(f"{p.name}.{time.strftime('%Y%m%dT%H%M%S')}{'' if n == 1 else f'.{n}'}.bak")
            _write_atomic(bak, current)
            backed_up.append(str(bak))
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(p, data)
        written.append(str(p))
    for rel, sha in last.items():   # a file this release dropped (a retired skill): gone if it is still ours, theirs if they changed it
        if rel in manifest or not isinstance(rel, str) or not isinstance(sha, str) or "\0" in rel or rel.startswith("/") or ".." in rel.split("/"):
            continue   # the manifest is household-writable: only a plausible path inside dest is ever touched
        p = dest / rel
        try:
            current = p.read_bytes()
        except (OSError, ValueError):   # ValueError: a name the filesystem refuses
            continue
        if _sha(current) == sha:
            p.unlink()
            removed.append(str(p))
        else:
            orphaned.append(str(p))
    if first or manifest != last:
        _write_atomic(dest / MANIFEST, json.dumps(manifest, indent=1).encode())
    return {"dir": str(dest), "written": written, "backed_up": backed_up, "kept": kept, "orphaned": orphaned, "removed": removed, "changed": bool(written or removed), "first": first}


def ensure() -> dict | None:
    """Write the bundle when nothing has written it before, a required file is missing (an interrupted install), or the
    pinned permission set is not the release's (the household's copy goes to .bak): the daemon at startup, so the first
    release with a bundle comes up on a box whose `finnamon update` ran the previous release's code, and the seal is
    always the release's. Otherwise never rewrites a complete one: that is install()."""
    d = dir()
    if not (d / MANIFEST).is_file() or any(not (d / rel).is_file() for rel in REQUIRED):
        return install()
    try:   # the seal is the one file the household does not get to keep edited: put the release's back (theirs to .bak)
        if any((d / rel).read_bytes() != data for rel, data in files().items() if rel in PINNED):
            return install()
    except OSError:
        return install()
    return None


CHANNEL_PLUGIN = "telegram@claude-plugins-official"
# A management subcommand: it edits the plugin registry and the directory's .claude/settings.local.json and starts no
# session, so no MCP server and none of the second-copy hazard --strict-mcp-config exists for (`claude plugin --help`).
CHANNEL_PLUGIN_ARGS = ("plugin", "install", CHANNEL_PLUGIN, "--scope", "local")
CHANNEL_PLUGIN_INSTALL = "claude " + " ".join(CHANNEL_PLUGIN_ARGS)   # the by-hand form of the argv below, minus --yes (`-y, --yes` per `claude plugin install --help`: a person answers the prompt)
PLUGIN_INSTALL_TIMEOUT_S = 120


def channel_plugin_registered(d: Path | None = None) -> bool:
    try:
        return json.loads(((d or dir()) / ".claude/settings.local.json").read_text())["enabledPlugins"][CHANNEL_PLUGIN] is True
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def plugin_dir_problem(d: Path) -> str | None:
    """Why the Telegram plugin must not be registered for `d`, or None when `d` is the household's own assistant directory.
    Any `claude` started in a folder the plugin is registered for loads it and polls the bot, stealing the household's
    updates (2026-09-26: a board worker in a worktree did). So only the default ~/.finnamon/assistant qualifies: not a
    scratch FINNAMON_HOME or a FINNAMON_ASSISTANT override, not under a temp dir, not inside a git checkout or worktree."""
    d = Path(d).resolve()
    if os.environ.get("FINNAMON_ASSISTANT") or config.home().resolve() != (Path.home() / ".finnamon").resolve():
        return f"{d} is not the household's ~/.finnamon/assistant (FINNAMON_HOME or FINNAMON_ASSISTANT is overridden)"
    if any(d.is_relative_to(t) for t in {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}):
        return f"{d} is under a temp directory"
    home = Path.home().resolve()   # home and above are not "a checkout": a dotfiles repo at ~/.git must not refuse the household
    if any((p / ".git").exists() for p in (d, *d.parents) if not home.is_relative_to(p)):
        return f"{d} is inside a git checkout or worktree"
    return None


def register_channel_plugin(exe: str | None, d: Path | None = None, force: bool = False) -> tuple[bool | None, str]:
    """Channel mode runs Claude Code's Telegram plugin inside the dashboard's session, and a local-scope plugin belongs to
    the directory the session runs in: this one. (True, msg) registered, (False, msg) already was, (None, msg) could not
    or would not (plugin_dir_problem; `force` skips that check), the message naming the by-hand step."""
    d = d or dir()
    how = f"cd {d} && {CHANNEL_PLUGIN_INSTALL}"
    if channel_plugin_registered(d):
        return False, f"the Telegram channel plugin is already registered for {d}"
    why = None if force else plugin_dir_problem(d)
    if why:
        return None, f"not registering the Telegram channel plugin: {why}; a claude started there would steal the bot's updates (--force to do it anyway)"
    if not exe:
        return None, f"`claude` is not on PATH, so the Telegram channel plugin is not registered for {d}; when it is: {how}"
    try:
        r = subprocess.run([exe, *CHANNEL_PLUGIN_ARGS, "--yes"], cwd=d, capture_output=True, text=True, timeout=PLUGIN_INSTALL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"could not register the Telegram channel plugin for {d} ({e}); by hand: {how}"
    if r.returncode:
        return None, f"registering the Telegram channel plugin for {d} failed: {(r.stderr or r.stdout).strip()[-300:]}\n  by hand: {how}"
    return True, f"registered the Telegram channel plugin for {d}"


def plugins_file() -> Path:
    """Claude Code's plugin registry (CLAUDE_CONFIG_DIR moves it, as it does for claude)."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "plugins" / "installed_plugins.json"


def stray_channel_plugins() -> list[str]:
    """Where the Telegram plugin is registered other than the assistant directory. Read only: fixing it is the person's."""
    home = dir()
    try:   # a registry that is missing or unparseable is not a finding
        regs = json.loads(plugins_file().read_text())["plugins"].get(CHANNEL_PLUGIN) or []
        return sorted({"(user scope: every folder)" if r.get("scope") == "user" else str(r.get("projectPath"))
                       for r in regs if isinstance(r, dict)
                       and (r.get("scope") == "user" or (r.get("projectPath") and Path(r["projectPath"]).resolve() != home))})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return []


def claude_config_path() -> Path:
    """Claude Code's own state file, where a directory's trust lives (CLAUDE_CONFIG_DIR moves it, as it does for claude)."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"


def trust(d: Path | None = None) -> bool | None:
    """Mark the assistant directory trusted in ~/.claude.json: Claude Code ignores a workspace's .claude/settings.json (the
    allow list, the reply-guard hook) until the directory has been trusted, interactively once or by this key, which is
    the fix its own warning names. True = written, False = already trusted, None = could not (the caller says how to do
    it by hand). Read-modify-replace of the one key; everything else in the file is left as it was."""
    d = str(d or dir())
    p = claude_config_path()
    try:
        for _ in range(3):   # claude writes this file too: a change between our read and our write means read again
            before = _stamp(p)
            cfg = json.loads(p.read_text()) if p.exists() else {}
            if not isinstance(cfg, dict):
                return None
            projects = cfg.setdefault("projects", {})
            if not isinstance(projects, dict):
                return None
            if isinstance(projects.get(d), dict) and projects[d].get("hasTrustDialogAccepted") is True:   # d is resolved by dir(); an explicit path is the caller's to resolve
                return False
            projects[d] = {**(projects.get(d) if isinstance(projects.get(d), dict) else {}), "hasTrustDialogAccepted": True}
            data = json.dumps(cfg, indent=2).encode()
            if _stamp(p) != before:
                continue
            _write_atomic(p, data, mode=0o600, follow_symlinks=True)   # claude keeps it 0600, it holds its own state; a looser mode is tightened
            return True
        return None
    except (OSError, ValueError, RecursionError):   # RecursionError: json on a pathologically nested file
        return None


def _stamp(p: Path) -> tuple | None:
    try:
        st = p.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def untrust(d: Path) -> None:
    """The reverse, for the eval lane's scratch directories: never leave a temp path in someone's ~/.claude.json."""
    p = claude_config_path()
    try:
        cfg = json.loads(p.read_text())
        if cfg.get("projects", {}).pop(str(d), None) is not None:
            _write_atomic(p, json.dumps(cfg, indent=2).encode(), mode=0o600, follow_symlinks=True)
    except (OSError, ValueError, AttributeError, RecursionError):
        pass


def trusted(d: Path | None = None) -> bool:
    try:
        return json.loads(claude_config_path().read_text())["projects"][str(d or dir())]["hasTrustDialogAccepted"] is True
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


TRUST_FIX = "run `finnamon install` or `finnamon update`, or `claude` once in that directory and accept the trust dialog"


def problems(claude_trust: bool = True) -> list[str]:
    """What a sealed `claude -p` would be missing in the assistant directory: a file, or Claude Code's trust in the
    directory, without which it ignores the settings there (the allow list, the reply-guard hook) and every command is
    "requires approval" with nobody to approve it."""
    d = dir()
    out = [f"missing {d / rel} (run `finnamon install`, or `finnamon update`, to write the assistant bundle)" for rel in REQUIRED if not (d / rel).is_file()]
    if claude_trust and not trusted(d):   # Codex keeps its trust in its own config.toml (codex.problems)
        out.append(f"{d} is not trusted by Claude Code, so it would ignore the settings there ({TRUST_FIX})")
    return out
