"""Install the daemon (kept alive), the hourly heartbeat and, when web/ is npm-installed, the dashboard for the detected OS.
macOS: LaunchAgents (user session). Linux: systemd --user units with linger.
The daemon is the only sync scheduler; the OS keeps it and the dashboard alive and runs the heartbeat, nothing else.

Why not cron: cron drops a job the machine slept through. A long-running daemon just resumes."""
from __future__ import annotations

import hashlib
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import assistant, config

LABEL = "com.finnamon"   # the household's (default FINNAMON_HOME) labels; any other home gets suffix()
LOG_MAX_BYTES = 5 * 1024 * 1024   # macOS: a log past this is rotated by the next heartbeat or daemon start
LOG_KEEP = 3                      # name.log.1 .. name.log.3
JOBS = ("daemon", "heartbeat", "web")


def real_home() -> Path:
    """The login's home from the password database, not $HOME: launchd labels and systemd user units are per user, so a
    shell with HOME pointed at a scratch directory is still installing into the one user's job namespace."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:   # a uid with no passwd entry (a container's --user N)
        return Path.home()


def _unit_home(text: str, darwin: bool) -> str | None:
    """The FINNAMON_HOME a rendered plist or unit was written for."""
    m = re.search(r"<key>FINNAMON_HOME</key><string>([^<]*)</string>" if darwin else r"^Environment=FINNAMON_HOME=(.*)$", text, re.M)
    return m.group(1) if m else None


def _same_home(a, b) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()   # however either side spells it


def suffix(os_name: str | None = None) -> str:
    """'' for the household's home (~/.finnamon of the real user, see real_home), else '-<hash of the home>': a test or
    second install under another FINNAMON_HOME must never list, replace or remove the household's jobs, even one whose
    FINNAMON_HOME is $HOME/.finnamon under a scratch HOME. A home already installed under the plain labels (a box that
    set FINNAMON_HOME before this existed) keeps them, or its next install would start a second daemon beside the first."""
    h = config.home()
    if _same_home(h, real_home() / ".finnamon"):
        return ""
    darwin = (os_name or platform.system()) == "Darwin"
    try:
        text = (unit_dir(os_name) / (f"{LABEL}.daemon.plist" if darwin else "finnamon-daemon.service")).read_text()
    except OSError:
        text = ""
    if (theirs := _unit_home(text, darwin)) and _same_home(theirs, h):
        return ""
    return "-" + hashlib.sha256(str(h.expanduser().resolve()).encode()).hexdigest()[:8]


def foreign_home(os_name: str | None = None) -> str | None:
    """Why this home must not touch its job names: one of them is installed for a different FINNAMON_HOME. None if not."""
    os_name = os_name or platform.system()
    darwin = os_name == "Darwin"
    if _same_home(config.home(), real_home() / ".finnamon") and not _same_home(Path.home(), real_home()):
        return (f"this is the household's home ({config.home()}), but HOME is {Path.home()}, not your login's {real_home()}: "
                "its jobs would run with the wrong HOME and replace the real ones. Run it from your normal shell, or re-run with --force")
    for name in (f"{label(n, os_name)}.plist" for n in JOBS) if darwin else (unit(n, os_name=os_name) for n in JOBS):
        try:
            theirs = _unit_home((unit_dir(os_name) / name).read_text(), darwin)
        except OSError:
            continue
        if theirs and not _same_home(theirs, config.home()):
            return (f"{name} belongs to another Finnamon home (FINNAMON_HOME={theirs}), not this one ({config.home()}); "
                    "leaving it alone. If that home is gone and these jobs should be this one's, re-run with --force")
    return None


def label(name: str, os_name: str | None = None) -> str:
    """launchd label: com.finnamon.daemon for the household, com.finnamon-1a2b3c4d.daemon for any other home."""
    return f"{LABEL}{suffix(os_name)}.{name}"


def unit(name: str, kind: str = "service", os_name: str | None = None) -> str:
    """systemd unit: finnamon-daemon.service for the household, finnamon-1a2b3c4d-daemon.service for any other home."""
    return f"finnamon{suffix(os_name)}-{name}.{kind}"


def env_path() -> str:
    """PATH for the units: wherever finnamon, claude, node and bun live, plus the usual. Bun runs the Telegram channel
    plugin inside the intercom session; without it the channel banner shows and nothing polls the bot. codex only once
    init has set it up: a Claude household's units must not drift (and so refuse `finnamon update`) over a CLI it never runs."""
    from . import codex
    parts = []
    for exe in ("finnamon", "claude", "uv", "node", "bun", *(("codex",) if codex.configured() else ())):
        p = codex.binary() if exe == "codex" else shutil.which(exe)
        if p and str(Path(p).parent) not in parts:
            parts.append(str(Path(p).parent))
    for d in ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", f"{Path.home()}/.local/bin", f"{Path.home()}/.bun/bin"):
        if d not in parts:
            parts.append(d)
    return ":".join(parts)


def plist(name: str, args: list[str], keepalive: bool, interval: int | None, log_dir: Path) -> str:
    argv = "\n".join(f"      <string>{a}</string>" for a in args)
    extra = "    <key>KeepAlive</key><true/>\n" if keepalive else f"    <key>StartInterval</key><integer>{interval}</integer>\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label(name, "Darwin")}</string>
    <key>ProgramArguments</key>
    <array>
{argv}
    </array>
{extra}    <key>RunAtLoad</key><true/>
    <key>WorkingDirectory</key><string>{repo_dir()}</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>PATH</key><string>{env_path()}</string>
      <key>HOME</key><string>{Path.home()}</string>
      <key>FINNAMON_HOME</key><string>{config.home()}</string>{web_hosts_env("plist") if name == "web" else ""}
    </dict>
    <key>StandardOutPath</key><string>{log_dir}/{name}.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/{name}.err</string>
</dict>
</plist>
"""


def systemd_units(exe: list[str], web: list[str] | None = None) -> dict[str, str]:
    cmd = " ".join(exe)
    env = f"Environment=PATH={env_path()}\nEnvironment=HOME={Path.home()}\nEnvironment=FINNAMON_HOME={config.home()}\nWorkingDirectory={repo_dir()}\n"
    u = lambda n, k="service": unit(n, k, "Linux")
    units = {
        u("daemon"): f"[Unit]\nDescription=Finnamon daemon\nAfter=network-online.target\n\n[Service]\n{env}ExecStart={cmd} daemon\nRestart=always\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n",
        u("heartbeat"): f"[Unit]\nDescription=Finnamon heartbeat\n\n[Service]\nType=oneshot\n{env}ExecStart={cmd} heartbeat\n",
        u("heartbeat", "timer"): "[Unit]\nDescription=Finnamon heartbeat hourly\n\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec=1h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n",
    }
    if web:
        units[u("web")] = f"[Unit]\nDescription=Finnamon web dashboard\nAfter=network-online.target\n\n[Service]\n{env}{web_hosts_env('systemd')}ExecStart={' '.join(web)}\nRestart=always\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n"
    return units


def web_hosts_env(kind: str) -> str:
    """FINNAMON_WEB_HOSTS (the Tailscale name the dashboard answers to, web/server.js) from the installing shell, baked
    into the web job so it survives a reboot, which `launchctl setenv` does not. Keep it exported in your shell profile:
    `update` compares the units against this shell's render, and `install` from a shell without it drops it."""
    hosts = os.environ.get("FINNAMON_WEB_HOSTS", "").strip()
    if not hosts:
        return ""
    return f"\n      <key>FINNAMON_WEB_HOSTS</key><string>{xml_escape(hosts)}</string>" if kind == "plist" else f"Environment=FINNAMON_WEB_HOSTS={hosts}\n"


def repo_dir() -> str:
    return str(Path(__file__).resolve().parent.parent)


def web_args() -> list[str] | None:
    """The dashboard job, when `node` is installed and web/ has been `npm install`ed; None means skip it quietly."""
    node = shutil.which("node")
    server = Path(repo_dir()) / "web" / "server.js"
    if not node or not server.exists() or not (server.parent / "node_modules").exists():
        return None
    return [node, str(server)]


def exe_args() -> list[str]:
    b = shutil.which("finnamon")
    return [b] if b else [sys.executable, "-m", "finnamon.cli"]


def render(os_name: str | None = None) -> dict[str, str]:
    os_name = os_name or platform.system()
    exe = exe_args()
    if os_name == "Darwin":
        logs = log_dir()
        files = {
            f"{label('daemon', os_name)}.plist": plist("daemon", exe + ["daemon"], True, None, logs),
            f"{label('heartbeat', os_name)}.plist": plist("heartbeat", exe + ["heartbeat"], False, 3600, logs),
        }
        web = web_args()
        if web:
            files[f"{label('web', os_name)}.plist"] = plist("web", web, True, None, logs)
        return files
    return systemd_units(exe, web_args())


# Paths a running service has already finished with. The rule is a denylist because a whitelist fails open to "nothing
# to restart", and the paths that would slip through are the ones deciding how the assistant behaves: the assistant
# bundle (finnamon/assistant_bundle/: its permission set, skills and CLAUDE.md), read once at each session's startup.
# VERSION, CHANGELOG.md and changelog.d/ are all a release commit touches (scripts/release.py): it changes no code, so it
# restarts nothing. Only the running daemon's `finnamon/<version>` Plaid User-Agent keeps the old one until the next
# restart, which the next code change brings (a `finnamon` command reads VERSION fresh).
INERT = ("web/public/", "docs/", "tests/", "web/test/", ".github/", ".gitignore", "CHANGELOG.md", "README.md", "TODOS.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
         "VERSION", "changelog.d/", "scripts/release.py")


def services_for(paths) -> list[str]:
    """Which services a set of changed files leaves stale.

    web/public/* is read off disk on every request, so a page reload picks it up and restarting the dashboard would only
    cost the household its assistant's process for nothing. Anything not provably inert restarts both: a release whose
    effect is silently missed is worse than a restart that was not needed."""
    svc = set()
    for p in paths:
        p = str(p).strip().removeprefix("./")   # a prefix, not lstrip's character set: that ate the dot of .github/
        if not p or p.startswith(INERT):
            continue
        if p.startswith("web/"):
            svc.add("web")
        elif p.startswith(assistant.BUNDLE_REL):
            svc.update(("daemon", "web"))   # both jobs start claude sessions in the installed copy of it
        elif p.startswith("finnamon/") or p in ("pyproject.toml", "uv.lock"):
            svc.add("daemon")
        else:
            svc.update(("daemon", "web"))   # scripts/, a new top-level package: unknown means adopt it everywhere
    return [s for s in ("daemon", "web") if s in svc]


def unit_dir(os_name: str | None = None) -> Path:
    os_name = os_name or platform.system()
    # $HOME's, not real_home(): a scratch HOME's jobs must not land in the login's LaunchAgents and load at every login.
    # The names are what keeps them apart (suffix), and the files under a person's own HOME are what foreign_home reads.
    return Path.home() / "Library" / "LaunchAgents" if os_name == "Darwin" else Path.home() / ".config" / "systemd" / "user"


def installed(name: str, os_name: str | None = None) -> bool:
    """Whether `finnamon install` put this job's unit on the machine (restart() of one that is not there fails)."""
    os_name = os_name or platform.system()
    return (unit_dir(os_name) / (f"{label(name, os_name)}.plist" if os_name == "Darwin" else unit(name, os_name=os_name))).exists()


def drifted_units(os_name: str | None = None) -> list[str]:
    """Units whose installed text is no longer what render() produces, so `finnamon update` has to send you to `install`.

    A release that changes a unit (a new service, different arguments, a moved log path) cannot be adopted by restarting
    what is already loaded: launchd would keep running the old definition against the new code, silently."""
    os_name = os_name or platform.system()
    dest = unit_dir(os_name)
    out = []
    for name, body in render(os_name).items():
        p = dest / name
        try:
            same = p.read_text() == body
        except OSError:
            same = False
        if not same:
            out.append(name)
    return sorted(out)


def restart(names: list[str], os_name: str | None = None) -> list[str]:
    """Restart loaded services in place, without rewriting a unit. `install` is the one that reprovisions."""
    os_name = os_name or platform.system()
    done = []
    uid = os.getuid()
    for n in names:
        argv = (["launchctl", "kickstart", "-k", f"gui/{uid}/{label(n, os_name)}"] if os_name == "Darwin"
                else ["systemctl", "--user", "restart", unit(n, os_name=os_name)])
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise RuntimeError(_partial(f"{n}: {argv[0]} did not return within {LAUNCHCTL_TIMEOUT_S}s", done)) from None
        if r.returncode:
            raise RuntimeError(_partial(f"{n}: {(r.stderr or '').strip() or f'{argv[0]} exited {r.returncode}'}", done))
        if (why := _not_running(n, os_name, uid)):
            # kickstart returns when the job is respawned, not when it stays up: a release needing `npm install` or a
            # reinstall leaves it crash-looping under KeepAlive, and in channel mode that job is the whole assistant.
            raise RuntimeError(_partial(f"{n} restarted but is not running ({why})", done))
        done.append(n)
    return done


def _partial(msg: str, done: list[str]) -> str:
    """A half-finished restart has to say which half: those services are already running the new code."""
    return msg + (f"; already restarted and now on the new code: {', '.join(done)}" if done else "; nothing was restarted")


def _not_running(name: str, os_name: str, uid: int, settle_s: float = 2.0) -> str | None:
    """None when the job is up after settling, else why not. Best effort: a manager we cannot read is not a failure."""
    time.sleep(settle_s)
    try:
        if os_name == "Darwin":
            r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label(name, os_name)}"], capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_S)
            if r.returncode:
                return "launchctl no longer lists it"
            m = re.search(r"^\s*state = (\S+)", r.stdout, re.M)
            return None if m and m.group(1) == "running" else f"state = {m.group(1) if m else 'unknown'}"
        r = subprocess.run(["systemctl", "--user", "is-active", unit(name, os_name=os_name)], capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_S)
        return None if r.stdout.strip() == "active" else (r.stdout.strip() or "inactive")
    except (OSError, subprocess.SubprocessError):
        return None


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / f"finnamon{suffix('Darwin')}"


def rotate_logs(d: Path | None = None, os_name: str | None = None) -> list[str]:
    """macOS only (Linux logs to journald): launchd appends to ~/Library/Logs/finnamon/*.log|*.err forever, and newsyslog
    needs root. Copy-then-truncate, not rename: launchd's children keep writing to the open fd, which would follow a
    renamed file. Every file is made 0600 on the way. Returns the rotated names."""
    if (os_name or platform.system()) != "Darwin":
        return []
    d = d or log_dir()
    rotated = []
    for p in sorted([*d.glob("*.log"), *d.glob("*.err")]) if d.is_dir() else []:
        try:
            os.chmod(p, 0o600)
            if p.stat().st_size <= LOG_MAX_BYTES:
                continue
            for i in range(LOG_KEEP - 1, 0, -1):
                old = p.with_name(f"{p.name}.{i}")
                if old.exists():
                    old.replace(p.with_name(f"{p.name}.{i + 1}"))
            # ponytail: lines written between the copy and the truncate are lost; a few lines once per 5 MB
            shutil.copyfile(p, p.with_name(f"{p.name}.1"))
            os.chmod(p.with_name(f"{p.name}.1"), 0o600)
            os.truncate(p, 0)
            rotated.append(p.name)
        except OSError as e:
            print(f"could not rotate {p}: {e}", file=sys.stderr)
    return rotated


def install(os_name: str | None = None, dry_run: bool = False, force: bool = False) -> list[str]:
    os_name = os_name or platform.system()
    if not force and (why := foreign_home(os_name)):
        raise RuntimeError(why)
    files = render(os_name)
    written = []
    dest = unit_dir(os_name)
    if os_name == "Darwin":
        log_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(log_dir(), 0o700)
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        p = dest / name
        written.append(str(p))
        if dry_run:
            continue
        p.write_text(body)
    if dry_run:
        return written
    if os_name == "Darwin":
        uid = os.getuid()
        for name in ("daemon", "heartbeat", "web"):
            lb = label(name, os_name)
            if f"{lb}.plist" in files:
                _launchd_reload(f"gui/{uid}", lb, dest / f"{lb}.plist")
            else:
                subprocess.run(["launchctl", "bootout", f"gui/{uid}/{lb}"], capture_output=True)   # e.g. web removed
    else:
        daemon, web = unit("daemon", os_name=os_name), unit("web", os_name=os_name)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        units = [daemon, unit("heartbeat", "timer", os_name)] + ([web] if web in files else [])
        subprocess.run(["systemctl", "--user", "enable", "--now", *units], check=True)
        subprocess.run(["systemctl", "--user", "restart", daemon], check=True)   # enable --now leaves a running daemon on the old code
        if web in files:
            subprocess.run(["systemctl", "--user", "restart", web], check=True)
        r = subprocess.run(["loginctl", "enable-linger", login_user()], capture_output=True, text=True)
        if (why := linger_problem()):
            print(f"! {why}{f' (loginctl enable-linger: {r.stderr.strip()})' if r.returncode and r.stderr.strip() else ''}. "
                  f"Fix: sudo loginctl enable-linger {login_user()}", file=sys.stderr)
    return written


def linger_problem(run=None) -> str | None:
    """Linux: None when systemd keeps this user's services running after logout, else why not. Without linger the
    daemon, heartbeat and dashboard all stop at logout, silently."""
    user, run = login_user(), run or subprocess.run
    try:
        r = run(["loginctl", "show-user", user, "-p", "Linger", "--value"], capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return f"cannot check linger for {user} ({e}); without it the services stop when you log out"
    if r.stdout.strip() == "yes":
        return None
    if r.returncode and r.stdout.strip() != "no":
        return f"cannot check linger for {user} ({(r.stderr or '').strip() or f'loginctl exited {r.returncode}'}); without it the services stop when you log out"
    return f"linger is off for {user}, so the services stop when you log out"


def login_user() -> str:
    """$USER is unset under sudo -u, cron and some ssh setups; the uid is not."""
    return os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name


TEARDOWN_WAIT_S = 15      # how long to wait for launchd to drop the old service's label
BOOTSTRAP_RETRIES = 5     # one-second retries on EIO only; any other launchctl error is reported at once
LAUNCHCTL_TIMEOUT_S = 15  # a hung launchctl must not hang the installer


def _launchd_reload(domain: str, label: str, plist: Path, run=subprocess.run, sleep=time.sleep) -> None:
    """bootout returns before launchd has torn down a KeepAlive service; bootstrapping into that gap fails with
    'Input/output error' (exit 5). Wait for the label to disappear (`launchctl print` exits non-zero once it is gone),
    then bootstrap, retrying briefly on EIO only."""
    run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, timeout=LAUNCHCTL_TIMEOUT_S)
    for _ in range(int(TEARDOWN_WAIT_S / 0.5)):
        if run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, timeout=LAUNCHCTL_TIMEOUT_S).returncode != 0:
            break
        sleep(0.5)
    last = None
    for attempt in range(BOOTSTRAP_RETRIES):
        last = run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT_S)
        if last.returncode == 0:
            return
        if last.returncode != 5 or attempt == BOOTSTRAP_RETRIES - 1:
            break   # a bad plist is not going to get better; report it
        sleep(1)
    raise RuntimeError(f"launchctl bootstrap {label} failed ({last.returncode}): {(last.stderr or last.stdout).strip()}")


def uninstall(os_name: str | None = None, dry_run: bool = False, force: bool = False) -> list[str]:
    """Stop and remove this home's jobs; returns the unit files it removed (or, with dry_run, would remove)."""
    os_name = os_name or platform.system()
    if not force and (why := foreign_home(os_name)):
        raise RuntimeError(why)
    dest = unit_dir(os_name)
    if os_name == "Darwin":
        labels = [label(n, os_name) for n in JOBS]   # before any file goes: suffix() reads the daemon's
        paths = [dest / f"{lb}.plist" for lb in labels]
    else:
        names = [unit("daemon", os_name=os_name), unit("heartbeat", os_name=os_name), unit("heartbeat", "timer", os_name), unit("web", os_name=os_name)]
        paths = [dest / n for n in names]
    gone = [str(p) for p in paths if p.exists()]
    if dry_run:
        return gone
    if os_name == "Darwin":
        uid = os.getuid()
        for lb, p in zip(labels, paths):
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{lb}"], capture_output=True)
            p.unlink(missing_ok=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", names[0], names[2], names[3]], capture_output=True)
        for p in paths:
            p.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return gone


def status(os_name: str | None = None) -> str:
    os_name = os_name or platform.system()
    if os_name == "Darwin":
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        mine = {label(n, os_name) for n in JOBS}   # this home's labels only, never another home's
        return "\n".join(l for l in r.stdout.splitlines() if l.split("\t")[-1] in mine) or "not loaded"
    r = subprocess.run(["systemctl", "--user", "--no-pager", "status", unit("daemon", os_name=os_name), unit("heartbeat", "timer", os_name), unit("web", os_name=os_name)], capture_output=True, text=True)
    return r.stdout or r.stderr
