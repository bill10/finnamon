"""finnamon: every read and write as a command. Claude Code calls these through Bash; so do you.
Which ones Claude may run is decided by the assistant bundle's .claude/settings.json (finnamon/assistant_bundle/), not by this file."""
from __future__ import annotations

import argparse
import functools
import getpass
import http.client
import select
import sqlite3
import subprocess
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, assistant, backup, budgets, claude_runner, config, demo, detect, heartbeat, imports, investments, link, notify, owners, plaid_api, properties, query, remote, run as runmod, scheduler, secrets, store, sync, taxonomy, telegram, triage, voice
from .plaid_api import PlaidError


def out(obj) -> None:
    print(json.dumps(obj, indent=1, default=str) if not isinstance(obj, str) else obj)


def die(msg: str, code: int = 1) -> None:
    sys.stdout.flush()   # what was printed before the error shows before it, not after (stdout is buffered when piped)
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# --- init --------------------------------------------------------------------------------------

def cmd_init(a) -> None:
    _human_only("init")
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    cur = config.load_secrets()
    plaid, tg = dict(cur.get("plaid", {})), dict(cur.get("telegram", {}))
    fresh_bot = not tg.get("bot_token")   # an existing bot keeps its inbound mode: no silent switch
    skipped = []   # steps left for later: init runs again any time and keeps what is already set up
    replace = bool(getattr(a, "plaid", False))   # `init --plaid`: change the keys on file (Sandbox → Production) without the other steps
    print("Plaid keys (Enter on a blank line keeps what is on file)" if replace else "Step 1 of 5: Plaid keys (Enter on a blank line skips this for now)")
    for attempt in range(3):
        if attempt == 0 and not replace and plaid.get("client_id") and (plaid.get("production_secret") or plaid.get("sandbox_secret")):
            print("  found in secrets.toml (`finnamon init --plaid` replaces them, e.g. to move from Sandbox to Production)")
        elif replace:
            print("  Plaid dashboard → Team Settings → Keys. Secrets are not echoed; Enter keeps the one on file.")
            plaid["client_id"] = input(f"  client_id [{plaid.get('client_id') or 'none'}]: ").strip() or plaid.get("client_id", "")
            plaid["production_secret"] = getpass.getpass("  Production secret: ").strip() or plaid.get("production_secret", "")
            plaid["sandbox_secret"] = getpass.getpass("  Sandbox secret (optional): ").strip() or plaid.get("sandbox_secret", "")
            if not (plaid["client_id"] and (plaid["production_secret"] or plaid["sandbox_secret"])):
                die("Plaid keys need a client_id and at least one secret")
            secrets.write(plaid, tg, dict(cur.get("items", {})))
        else:
            print("  Open https://dashboard.plaid.com/signup and create an account (the Trial plan is free).")
            print("  Then Team Settings → Keys and copy:")
            plaid["client_id"] = input("  client_id: ").strip()
            if plaid["client_id"]:
                plaid["production_secret"] = input("  Production secret (blank if Plaid has not approved Production yet): ").strip()
                plaid["sandbox_secret"] = input("  Sandbox secret (optional, for tests): ").strip()
            if not (plaid["client_id"] and (plaid["production_secret"] or plaid["sandbox_secret"])):   # nothing to verify: a skip, not a rejection
                skipped.append("Plaid keys")
                print("  skipped: the dashboard and the assistant come up without a bank; add the keys later with `finnamon init`")
                print("  (Production access can take days; meanwhile `finnamon demo` shows Finnamon on made-up data)")
                break
            secrets.write(plaid, tg, dict(cur.get("items", {})))  # written, then verified; a bad key re-prompts rather than wedging
        env = "production" if plaid.get("production_secret") else "sandbox"  # verify whichever secret was actually given
        ok, msg = _plaid_probe(env)
        print(f"  {'✓' if ok else '!'} {msg}")
        if ok:
            break
    else:
        die("Plaid rejected the keys three times; check them at dashboard.plaid.com, then run `finnamon init --plaid` again")
    if replace:
        if plaid.get("production_secret"):
            print("Linking uses Production: add banks with `finnamon link` or the dashboard's Link account. Banks linked in Sandbox are test data; unlink them (`finnamon link --remove <item_id>`).")
        return

    print("Step 2 of 5: Telegram bot (Enter on a blank line skips this for now)")
    me = None
    for attempt in range(3):
        if attempt == 0 and tg.get("bot_token"):
            print("  found in secrets.toml")
        else:
            print("  In Telegram, message @BotFather: /newbot, then /setprivacy → Disable.")
            tg["bot_token"] = input("  Paste the token: ").strip()
            if not tg["bot_token"]:
                secrets.update(telegram=tg)   # a saved token that was just rejected goes too, so the daemon stops polling it
                skipped.append("Telegram")
                print("  skipped: alerts show on the dashboard; add a bot later with `finnamon init`")
                break
            secrets.update(telegram=tg)
        try:
            me = telegram.get_me()
            break
        except Exception as e:  # noqa: BLE001
            print(f"  ! Telegram rejected the token: {e}")
    else:
        die("Telegram rejected the token three times; check it with @BotFather, then run `finnamon init` again")
    conn = store.connect()
    owner = a.owner or os.environ.get("USER", "me")
    if me is None or store.get_state(conn, "chat_id"):
        conn.execute("INSERT OR IGNORE INTO owners (owner, display_name) VALUES (?,?)", (owner, owner.title()))
    if me is not None:
        print(f"  ✓ token valid (bot: @{me.get('username')})")
        if not me.get("can_read_all_group_messages"):
            print("  ! privacy mode is on. In @BotFather run /setprivacy → Disable. Needed before adding a second person.")
        if store.get_state(conn, "chat_id"):
            print(f"  household chat already recorded ({store.get_state(conn, 'chat_id')})")
        else:
            code = owners.new_code()
            print(f"  Now open https://t.me/{me.get('username')}?start={code} and tap Start (or send: {code}).")
            print("  waiting...", end="", flush=True)
            r = owners.add_first(conn, owner, code)
            print(f" ✓ got it, chat id {r['chat_id']} recorded for {owner}")

    print("Step 3 of 5: Claude Code")
    _check_claude_code()
    _install_bundle()

    print("Step 4 of 5: Dashboard")
    dashboard = _setup_dashboard(a)
    if me is not None and fresh_bot and dashboard and store.get_state(conn, "inbound") is None:   # session mode types into the dashboard, so it needs one
        store.set_state(conn, "inbound", "session")
        store.set_state(conn, "session_tip_shown", 1)   # nothing to advertise to a household already on it
        print("  Telegram: session mode (the default), the chat shares the dashboard's conversation. `finnamon channel off` for the legacy separate session.")

    print("Step 5 of 5: Schedule")
    if not shutil.which("finnamon"):
        print("  ! `finnamon` is not on PATH. Run: uv tool install -e .   (then re-run init for the scheduler)")
    osn = platform.system()
    what = "LaunchAgents" if osn == "Darwin" else "systemd user units"
    jobs = "the always-on daemon, hourly heartbeat" + (" and the dashboard" if dashboard else "")
    ans = "y" if a.yes else input(f"  Detected {osn}. Install {jobs} as {what}? [Y/n] ").strip().lower()
    scheduled = ans in ("", "y", "yes")
    if scheduled:
        try:
            for f in scheduler.install(osn):
                print(f"  ✓ {f}")
        except (RuntimeError, subprocess.CalledProcessError) as e:
            die(f"{e}\nFix that, then run: finnamon install")
    print()
    dashboard = dashboard and scheduled   # the page only exists once its job runs
    if dashboard:
        print(f"Done. In a few seconds the dashboard is up: open {dashboard_url()} on this machine.")
        print("  That address carries the page's key once (the page keeps it as a cookie); later, `finnamon open` opens it again.")
        print("  Link account adds a bank in the page." if "Plaid keys" not in skipped else "  Link account adds a bank in the page once the Plaid keys are in (`finnamon init`).")
        print('  The intercom button in the corner is the household\'s Claude Code session: ask it to "set up my budgets".')
    elif scheduled:
        print(f"Done. Next: finnamon link (per bank), then ask the assistant to \"set up my budgets\": in the dashboard's intercom, or {_ASK_CLAUDE}.")
    else:
        print("Done, nothing scheduled: `finnamon install` when you're ready, then finnamon link (per bank).")
    if skipped:
        print(f"Skipped for now: {', '.join(skipped)}. Run `finnamon init` again to add them; what is set up stays.")
    chat = store.get_state(conn, "chat_id")
    if chat and me is not None:   # a bot skipped on a re-run (one recorded before, now rejected) cannot send this
        how = ("run <code>finnamon open</code> on the Finnamon box and click Link account, or run <code>finnamon link</code> in a terminal there"
               if dashboard else "run <code>finnamon link</code> in a terminal on the Finnamon box")
        telegram.send_message(chat, f"Finnamon is set up. Nothing is linked yet: {how} to add a bank. Here in the chat you can ask me anything about the accounts once they're in.")
        print("Sent you a message on Telegram too.")


# The household's assistant lives in its own directory; a `claude` anywhere else (this checkout, ~) is a different, unsealed session.
_ASK_CLAUDE = "`cd ~/.finnamon/assistant && claude --strict-mcp-config`"


def _plaid_probe(env: str) -> tuple[bool, str]:
    """Do the `env` keys authenticate? One institution lookup, no Item created; init and doctor both ask this."""
    try:
        plaid_api.institution_get("ins_3", env=env)  # Chase: exists in both sandbox and production
        return True, "Plaid credentials verified" + (" (sandbox only: you'll see test banks, not yours)" if env == "sandbox" else "")
    except PlaidError as e:
        if e.code == "INVALID_INSTITUTION":  # a business error after authentication still means the keys work
            return True, f"Plaid credentials verified (institution probe said {e.code}; the keys authenticated)"
        if e.code == "NETWORK_ERROR":  # a Plaid outage or a local blip, not a bad key: don't blame the keys
            return False, f"could not reach Plaid: {e}"
        return False, f"Plaid rejected the keys: {e}"


DASHBOARD_URL = f"http://localhost:{os.environ.get('PORT') or config.DASHBOARD_PORT}"   # the PORT the dashboard itself honours
NPM_TIMEOUT_S = 900
CLAUDE_AUTH_TIMEOUT_S = 15


def _announce_dashboard_port() -> None:
    """One line, once, when the dashboard's address moved (7077 → 8888): the cookie is per port and a tailscale serve names one."""
    p = config.home() / config.WEB_PORT_FILE
    if p.exists() and p.read_text().strip() == str(config.DASHBOARD_PORT):
        return
    n = config.DASHBOARD_PORT
    print(f"dashboard address is now {DASHBOARD_URL} (was 7077; a PORT you set stays): open it once with `finnamon open` (browser cookies are per port, and it carries the key); "
          f"a `tailscale serve --bg 7077` must be redone as `tailscale serve --bg {n}`")
    try:
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(f"{n}\n")
    except OSError:
        pass


def _dashboard_port_problem() -> str:
    """Something other than Finnamon answering on the dashboard's port ('' = free, or ours)."""
    port = int(os.environ.get("PORT") or config.DASHBOARD_PORT)
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/"); body = c.getresponse().read(2000).decode("utf-8", "replace")
    except (OSError, http.client.HTTPException):
        return ""
    return "" if "finnamon open" in body else f"something that is not the dashboard answers on port {port} (8888 is also Jupyter's)"


def dashboard_url(host: str | None = None) -> str:
    """The page's address with its key, the one way in (web/server.js swaps the key for a cookie and redirects to the clean
    address). `host` is a name a proxy serves the page on (tailscale serve: mac.tailnet.ts.net, HTTPS), or a whole origin."""
    base = host if host and host.startswith(("http://", "https://")) else f"https://{host}" if host else DASHBOARD_URL
    try:
        return f"{base.rstrip('/')}/?token={config.web_token()}"
    except (OSError, ValueError) as e:   # unreadable, unwritable or hand-edited: say so instead of a traceback (the server answers 503 for the same reason)
        die(f"cannot read the dashboard's key ({e}); fix the file's permissions or remove it and run `finnamon open` again")


def _browser_open(url: str) -> bool:
    """webbrowser.open, except that on WSL it finds no browser on the Linux side: there the Windows one opens the page
    (wslview from wslu, else explorer.exe, which exits 1 even when it worked, so the address is printed as well). A
    BROWSER the person set wins."""
    if "microsoft" in platform.uname().release.lower() and not os.environ.get("BROWSER"):
        if cmd := shutil.which("wslview") or shutil.which("explorer.exe"):
            webbrowser.register("wsl", None, webbrowser.GenericBrowser(cmd), preferred=True)
    return webbrowser.open(url)


def cmd_open(a) -> None:
    """Open the dashboard in a browser (or print its address): human-only, since the address is the key to the intercom's shell."""
    if _from_claude():
        die("the dashboard's key is for a person at a terminal: run `finnamon open` there")
    url = dashboard_url(a.host)
    # Printed, not opened: a phone's address (typed or sent there; never through the household chat, which every member and
    # the assistant read), or an SSH session, where a browser would open on the box's own screen with nobody in front of it.
    if a.print or a.host or os.environ.get("SSH_CONNECTION") or not _browser_open(url):
        print(url)


def cmd_web(a) -> None:
    """`finnamon web token [--rotate]`: the key itself (for curl's Authorization: Bearer, or FINNAMON_WEB_TOKEN on another
    computer's `--to` uploads); --rotate mints a new one and every open page has to be opened again with `finnamon open`."""
    if _from_claude():
        die("the dashboard's key is for a person at a terminal")
    try:
        print(config.web_token(rotate=a.rotate))
    except (OSError, ValueError) as e:
        die(f"cannot {'replace' if a.rotate else 'read'} the dashboard's key ({e}); fix the file's permissions")
    if a.rotate:
        print("rotated: every open page and copied key is locked out; `finnamon open` lets this browser back in", file=sys.stderr)


def cmd_demo(a) -> None:
    """`finnamon demo`: a made-up household in its own FINNAMON_HOME and the dashboard on a spare port over it, to try
    Finnamon with no Plaid keys or Telegram bot (finnamon/demo.py). --stop stops that dashboard; --reset starts over."""
    h = demo.home()
    try:
        demo.check_home(h)   # before FINNAMON_HOME moves: it compares against the shell's
    except ValueError as e:
        die(str(e))
    os.environ["FINNAMON_HOME"] = str(h)   # everything below, and the dashboard it starts, reads the demo's directory
    os.environ.pop("FINNAMON_ASSISTANT", None)
    if a.stop or a.reset:
        stopped = h.exists() and demo.stop(h)
        if a.stop:
            return print("stopped the demo dashboard" if stopped else "the demo dashboard was not running")
    try:
        if (a.reset or not (h / demo.READY).exists()) and (h / demo.MARKER).exists():
            shutil.rmtree(h)   # --reset, or a build that died halfway: made again rather than reused
        if not (h / demo.READY).exists():
            demo.build(h)
            print(f"made a demo household in {h} (made-up banks, six months of transactions, budgets and alerts)")
        url = demo.start(h, a.port)
    except (ValueError, RuntimeError) as e:
        die(str(e))
    print(f"the demo dashboard: {url}")
    print("  The intercom in the corner is a Claude Code session over the demo data: ask it \"how did we do on budgets last month?\"")
    print("  `finnamon demo --stop` stops it, `finnamon demo --reset` starts the household over.")
    if not (a.print or os.environ.get("SSH_CONNECTION")):
        _browser_open(url)


def cmd_voice(a) -> None:
    """`finnamon voice setup`: whisper.cpp and a speech model for the dashboard's Talk button (finnamon/voice.py);
    `finnamon voice status`: whether they are there."""
    if a.action == "status":
        ready, detail = voice.status()
        return out({"ready": ready, "detail": detail})
    _human_only("voice setup")   # it installs software and downloads a model
    sys.exit(voice.setup(yes=a.yes))


def cmd_remote(a) -> None:
    """`finnamon remote [--off] [--dry-run]`: the dashboard over Tailscale Serve (finnamon/remote.py); a person's, like `open`."""
    _human_only("remote")
    code = remote.off(dry_run=a.dry_run) if a.off else remote.setup(dry_run=a.dry_run)
    if code:
        sys.exit(code)


def _check_claude_code() -> None:
    """Every chat reply and /triage run go through `claude -p` (claude_runner.py); warn, don't block init,
    since a household can finish setup and log in afterward."""
    ok, msg = _claude_code_status()
    print(f"  {'✓' if ok else '!'} {msg}")


def _claude_code_status() -> tuple[bool, str]:
    """Is Claude Code installed and logged in? `auth status --json` is a local config read, not a model call, so this
    spends no tokens."""
    exe = claude_runner.binary()
    if not exe:
        return False, ("`claude` is not on PATH. Install Claude Code (https://claude.com/claude-code), or set "
                       "FINNAMON_CLAUDE_BIN/CLAUDE_BIN to its path.")
    try:
        # --strict-mcp-config: the same seal every other claude child Finnamon spawns carries (claude_runner.run,
        # the eval lane); a plain `claude` in this checkout loads the project-local Telegram channel plugin, whose
        # second MCP server copy kills the household session's copy (CLAUDE.md, docs/DEVELOPMENT.md).
        r = subprocess.run([exe, "--strict-mcp-config", "auth", "status", "--json"],
                           capture_output=True, text=True, timeout=CLAUDE_AUTH_TIMEOUT_S)
        status = json.loads(r.stdout)
        if not isinstance(status, dict):
            raise ValueError(f"unexpected output: {r.stdout[:200]!r}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
        return False, f"could not check whether Claude Code is logged in: {e}"
    if status.get("loggedIn"):
        return True, f"Claude Code is logged in ({status.get('email') or status.get('authMethod')})"
    return False, "Claude Code is not logged in. Run `claude` once and sign in (a Pro or Max plan)."


def _setup_dashboard(a) -> bool:
    """Step 4 of init: `npm install` in web/ when Node is here, so the scheduler step adds the dashboard job.
    Returns whether the dashboard will be installed."""
    web = Path(scheduler.repo_dir()) / "web"
    if not (web / "package.json").exists():   # a non-editable install has no web/ next to the package
        print(f"  ! no web/ in {web.parent}; the dashboard needs the repo checkout (uv tool install -e .). Skipped.")
        return False
    node = shutil.which("node")
    if not node:
        print("  ! Node 20+ is not on PATH, so the dashboard is skipped. Install it (macOS: brew install node), then:")
        print("      cd web && npm install && finnamon install")
        return False
    if (web / "node_modules" / "node-pty").exists():   # a whole install, not one that was interrupted
        print("  found web/node_modules")
        return True
    ans = "y" if a.yes else input("  Install the web dashboard (npm install in web/, about a minute)? [Y/n] ").strip().lower()
    if ans not in ("", "y", "yes"):
        print("  skipped; later: cd web && npm install && finnamon install")
        return False
    npm = shutil.which("npm") or str(Path(node).parent / "npm")
    try:   # output streams to the terminal so a slow registry looks slow, not stuck
        r = subprocess.run([npm, "install"], cwd=web, timeout=NPM_TIMEOUT_S)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  ! npm install did not finish: {e}")
        ok = False
    if not ok:
        print("  ! npm install failed. Fix that, then: cd web && npm install && finnamon install")
        return False
    print("  ✓ dashboard installed")
    return True


# --- link / owners ----------------------------------------------------------------------------

def cmd_link(a) -> None:
    _triage_read_only()
    conn = store.connect()
    if a.update:
        a.update = _resolve_item(conn, a.update)
    if _from_claude():
        if not (a.start or a.finish or (a.update and a.telegram)) or a.token is not None or a.public_token or a.web:   # a bare --token is the empty string; --web is a person's click
            die("from a Claude session, `finnamon link --start` adds a bank and `link --update <item_id> --telegram` sends a re-login link to the chat; "
                "--remove and the blocking form are for a person at a terminal")
    if a.owner:   # attribute to a member (or joint), never invent one from a typo
        try:
            a.owner = owners.resolve(conn, a.owner, joint=True)
        except ValueError as e:
            die(str(e))
    if a.token is not None:   # the web dashboard: a plain link token for Plaid Link JS
        try:
            out({"link_token": link.start(a.owner or _default_owner(conn), redirect_uri=a.token or None, hosted=False)["link_token"]})
        except PlaidError as e:
            die(str(e))
        return
    if a.public_token:
        try:
            out(link.finish_in_page(conn, a.owner or _default_owner(conn), a.public_token))
        except (PlaidError, runmod.Locked) as e:
            die(str(e))
        return
    if a.web and not a.update:
        die("--web goes with --update <item_id>")
    if a.update and a.web:   # the dashboard's Reconnect: a person's click, so the URL comes back to the page, not to the chat
        try:
            out(link.start_update(conn, a.update, to_chat=False))
        except (ValueError, PlaidError) as e:
            die(str(e))
        return
    if a.start:
        try:
            s = link.start_pending(conn, a.owner or _default_owner(conn), to_telegram=a.telegram, limited=_from_claude())
        except (ValueError, PlaidError, telegram.TelegramError) as e:
            die(str(e))
        if s.get("state") == "linked":   # the earlier session completed in the meantime
            _print_linked(s); print("Run finnamon link --start again to add another bank."); return
        print(("Still open from earlier; open this" if s.get("reused") else "Open this") + f" on any device (valid for about {link.LINK_WAIT_S // 3600}h"
              + ("; also sent to Telegram" if a.telegram else "") + f"):\n  {s['url']}")
        print("When you have logged into the bank there, the daemon links it within a minute, or run: finnamon link --finish")
        return
    if a.finish:
        try:
            r = link.check_pending(conn)
        except (PlaidError, telegram.TelegramError) as e:
            die(str(e))
        if r is None:
            print("nothing pending; start with finnamon link --start"); return
        if r["state"] == "waiting":
            print("still waiting for the bank login"); return
        if r["state"] != "linked":
            die(f"link session {r['state']}; start again with finnamon link --start")
        _print_linked(r)
        return
    if a.remove:
        row = conn.execute("SELECT institution, (SELECT count(*) FROM accounts WHERE item_id=items.item_id) FROM items WHERE item_id=?", (a.remove,)).fetchone()
        if row and not a.yes:
            ans = input(f"Unlink {row[0] or a.remove} ({row[1]} accounts, their transactions, thresholds and suppressions)? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                die("not removed")
        try:
            out(link.remove(conn, a.remove))
        except (ValueError, PlaidError, runmod.Locked, sqlite3.OperationalError) as e:
            die(str(e))
        return
    owner = a.owner or _default_owner(conn)
    if a.update and _from_claude():   # the assistant can't sit in a 4h wait: send the link and return; the next sync clears the error
        try:
            out(link.start_update(conn, a.update))
        except (ValueError, PlaidError, telegram.TelegramError) as e:
            die(str(e))
        return
    try:
        s = link.start(owner, a.update)
    except (ValueError, PlaidError) as e:
        die(str(e))
    print("Open this on any device:\n  " + s["url"])
    if a.telegram and (link.send_url(conn, s["url"], "Log back into the bank", link.UPDATE_LINK_VALID) if a.update else link.send_url(conn, s["url"])):   # opt in: the household chat is not your phone
        print("(also sent to your Telegram)")
    print(f"Waiting for you to finish in Plaid (up to {link.LINK_WAIT_S // 3600}h)...", flush=True)
    pt = link.wait_for_public_token(s["link_token"])
    if not pt and not a.update:
        die("Link session ended without adding a bank. Run `finnamon link` again.")
    r = link.complete(conn, owner, pt or "", a.update)
    if a.update:
        out(r); return
    sy = r["sync"]
    print(f"✓ Linked {r['institution']} ({r['item_id']})")
    print(f"  {sy['accounts']} accounts, {sy['transactions']} transactions, {sy['recurring']} recurring streams, {sy.get('holdings', 0)} holdings" + (f", error {sy['error']}" if sy["error"] else ""))
    for m in r["mirror_candidates"]:   # a terminal asks; the dashboard and Telegram mark the same pairs without asking (link.apply_mirrors)
        print(f"  {link.mirror_line(m)}.")
        old = conn.execute("SELECT i.item_id, i.status FROM accounts a JOIN items i ON i.item_id=a.item_id WHERE a.account_id=?", (m["existing"],)).fetchone()
        if old and old[1] not in (None, "good"):
            print(f"  The existing one is on a broken connection ({old[1]}). If this is a re-link, answer n and run: finnamon link --remove {old[0]}")
        ans = "y" if a.yes else input("  Mark as joint (same account, both logins)? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            link.mark_mirror(conn, m["new"], m["existing"])
            print("  ✓ marked joint")
    print("Baseline set: nothing before today counts as new.")
    link.announce(conn, r["item_id"])
    print(f"Summary sent to Telegram. Next: finnamon link (another bank), or ask the assistant to 'set up my budgets': in the dashboard's intercom, or {_ASK_CLAUDE}")


def _resolve_item(conn, ref: str) -> str:
    """`link --update` takes an item_id or, when only one bank has that name, the institution's name."""
    rows = conn.execute("SELECT item_id, institution FROM items WHERE source='plaid'").fetchall()
    if any(r[0] == ref for r in rows):
        return ref
    hits = [r for r in rows if (r[1] or "").lower() == ref.lower()] or [r for r in rows if ref.lower() in (r[1] or "").lower()]
    if len(hits) == 1:
        return hits[0][0]
    if hits:
        die(f"more than one bank matches {ref!r}: " + ", ".join(f"{r[1]} ({r[0]})" for r in hits) + "; pass the item_id")
    die(f"no linked bank {ref!r}; `finnamon status` lists them")


def _telegram_die(e: Exception) -> None:
    """One line and the next step, not a traceback, for a bot Telegram will not talk to."""
    if isinstance(e, telegram.TelegramError) and e.code in (401, 404):
        die(f"Telegram refused the bot token ({e.code}); there is no working bot yet: `finnamon init` adds one (in Telegram: @BotFather → /newbot)")
    die(f"Telegram: {e}" if isinstance(e, telegram.TelegramError) else str(e))


def _print_linked(r: dict) -> None:
    sy = r["sync"]
    print(f"✓ Linked {r['institution']} ({r['item_id']}): {sy['accounts']} accounts, {sy['transactions']} transactions, {sy.get('holdings', 0)} holdings"
          + (f", error {sy['error']}" if sy["error"] else ""))
    if r["mirror_candidates"] or r.get("marked_joint"):
        print(link.mirror_note(r["mirror_candidates"], r.get("marked_joint")))


def _default_owner(conn) -> str:
    r = conn.execute("SELECT owner FROM owners ORDER BY rowid LIMIT 1").fetchone()
    return r[0] if r else os.environ.get("USER", "me")


def cmd_owner(a) -> None:
    if _from_claude() and a.action != "list":
        die("adding, renaming and removing household members is for a person at a terminal")
    conn = store.connect()
    if a.action == "list":
        out([dict(r) for r in conn.execute("SELECT owner, display_name, telegram_user_id FROM owners")]); return
    if a.action in ("rename", "remove"):
        if not a.name or bool(a.new_name) == (a.action == "remove"):
            die("usage: finnamon owner rename <name> <new name>   |   finnamon owner remove <name>")
        try:
            out(owners.rename(conn, a.name, a.new_name) if a.action == "rename" else owners.remove(conn, a.name))
        except ValueError as e:
            die(str(e))
        return
    if a.action == "add":
        if not a.name:
            die("owner add needs a name: finnamon owner add <name> [--user-id <id> | --no-telegram]")
        if a.name.strip().lower() == owners.JOINT:
            die("`joint` is reserved for shared accounts; pick a person's name")
        same = conn.execute("SELECT owner FROM owners WHERE owner=? COLLATE NOCASE", (a.name.strip(),)).fetchone()   # Jane and jane are one person
        a.name = same[0] if same else a.name.strip()
        if a.no_telegram:
            if a.user_id is not None or a.new_group:
                die("--no-telegram can't be combined with --user-id or --new-group")
            if conn.execute("SELECT 1 FROM owners WHERE owner=?", (a.name,)).fetchone():
                die(f"{a.name} is already a household member; nothing to do (to give them Telegram later: finnamon owner add {a.name} --user-id <id>)")
            conn.execute("INSERT INTO owners (owner, display_name) VALUES (?,?)", (a.name, a.name.title()))
            out({"owner": a.name, "telegram_user_id": None}); return
        if a.user_id is not None:
            if a.user_id <= 0:
                die("a Telegram user id is a positive integer")
            try:
                out(owners.add_by_id(conn, a.name, a.user_id))
            except ValueError as e:
                die(str(e))
            return
        if store.get_state(conn, "inbound") == "channel":
            die("inbound is the Claude Code channel, which owns the bot's updates; add the person with their Telegram user id instead:\n"
                f"  finnamon owner add {a.name} --user-id <id>   (the id is in /telegram:access in the channel session)")
        code = owners.new_code()
        if a.new_group or not owners.household_is_group(conn):
            print(f"Create a Telegram group with you, {a.name}, and the bot; make the bot an admin. It becomes the household chat.")
        else:
            print(f"Add {a.name} to the household group (for a new group instead: finnamon owner add {a.name} --new-group).")
        print(f"Then have {a.name} send exactly this in the group:  {code}")
        print("waiting...", flush=True)
        try:
            out(owners.add_member(conn, a.name, code, switch=a.new_group))
        except (telegram.TelegramError, RuntimeError, TimeoutError) as e:
            _telegram_die(e)


def cmd_channel(a) -> None:
    """Who answers Telegram: the daemon's own `claude -p` (off), the dashboard's intercom session through the daemon (session),
    or Claude Code's own Telegram channel plugin (on, an experiment). The daemon keeps sending alerts in every mode."""
    if a.action != "status" and _from_claude():
        die("switching the Telegram inbound is for a person at a terminal")
    conn = store.connect()
    if a.action == "on":
        store.set_state(conn, "inbound", "channel")
        print("inbound = channel. The daemon stops polling the bot within a minute (no restart needed). Then, once")
        print("(slash commands go in the Claude session; finnamon commands in another terminal):")
        print(f"  1. Register the plugin for the assistant directory ({assistant.dir()}): `finnamon install` (or `finnamon update`)")
        print(f"     does it now, or by hand:  cd {assistant.dir()} && {assistant.CHANNEL_PLUGIN_INSTALL}")
        print("  2. Write the bot token (from ~/.finnamon/secrets.toml) to ~/.claude/channels/telegram/.env as")
        print("       TELEGRAM_BOT_TOKEN=...            (by hand, mode 0600; never paste it into a Claude session)")
        print("  3. With the dashboard installed: finnamon install (its intercom session attaches the channel).")
        print(f"     Otherwise keep this running in a persistent terminal (tmux/screen), in {assistant.dir()}:")
        print("       claude --permission-mode dontAsk --channels plugin:telegram@claude-plugins-official --disallowedTools WebSearch WebFetch")
        print("     dontAsk = allow-listed commands only, no Allow/Deny buttons on anyone's phone; no web = it reads bank memos unattended.")
        print("  4. DM the bot, then in that session: /telegram:access pair <code>; for the household group (the same group the")
        print(f"     daemon sends alerts to, chat id {store.get_state(conn, 'chat_id') or '<see finnamon status>'}):")
        print("       /telegram:access group add <group chat id> --no-mention --allow <user ids>")
        print("       /telegram:access policy allowlist")
        print("  5. Tell Finnamon who is who: finnamon owner add <name> --user-id <telegram user id>")
        print("  Note: the token now lives in two places; after `finnamon init` rotates it, update the .env too.")
        _warn_no_bun_for_channel(conn)
    elif a.action in ("off", "session"):
        was = store.get_state(conn, "inbound") or "daemon"
        if was == "channel":
            store.set_state(conn, "inbound_off_at", int(time.time()))   # the daemon drops messages older than this: the plugin answered them
        store.set_state(conn, "inbound", "session" if a.action == "session" else None)
        store.set_state(conn, "channel_deaf_since", None)   # only the channel branch of the poll loop clears this; leaving the mode would strand it
        if a.action == "session":
            print("inbound = session. The daemon keeps reading Telegram (resumes polling within a minute) and types each household message into")
            print("the dashboard's intercom session, so the chat and the dashboard share one conversation. No channel plugin is used.")
        else:
            print("inbound = daemon. The daemon reads Telegram and answers each message with its own `claude -p` session (resumes polling within a minute).")
        if was == "channel":
            print("Stop the channel session first (two pollers on one bot collide): the dashboard's session reads the bot until it restarts.")
        # The intercom's argv (--channels, the MCP seal, the web tools) is fixed when the dashboard starts it, and a session
        # still carrying the channel plugin keeps reading the bot under the daemon (409s, messages the relay never sees).
        if was != "daemon" or a.action == "session":
            if not scheduler.installed("web"):
                print("The dashboard isn't installed: finnamon install sets it up with this mode.")
            else:
                try:
                    scheduler.restart(["web"])
                    print("restarted the dashboard, so its intercom session runs in this mode")
                except (RuntimeError, subprocess.CalledProcessError, OSError) as e:
                    print(f"warning: could not restart the dashboard ({e}): finnamon update --no-pull", file=sys.stderr)
    else:
        out({"inbound": store.get_state(conn, "inbound") or "daemon"})


def _register_channel_plugin(conn, force: bool = False) -> None:
    """install/update, once inbound is channel: register the Telegram plugin for the assistant directory (assistant.py)."""
    try:
        if store.get_state(conn, "inbound") != "channel":
            return
    except sqlite3.Error:   # a locked or mid-migration database: nothing to register for (before init, callers do not get here: cmd_install checks db_path() first, cmd_update runs after the migration)
        return
    ok, msg = assistant.register_channel_plugin(claude_runner.binary(), force=force)
    if ok:
        print(msg)
    elif ok is None:
        print(f"warning: {msg}", file=sys.stderr)


def cmd_hook(a) -> None:
    """Claude Code PreToolUse hooks (see the assistant bundle's .claude/settings.json). reply-guard: the channel plugin's `reply` tool takes any chat
    id and any file path, and the session runs with dontAsk. browser-guard: in the browser-import session every Bash
    command must fit browser_command_ok; outside it the hook is a no-op. Exit 2 blocks the call; the message goes back
    to the assistant. permission: the intercom's PermissionRequest hook (finnamon/approval.py); it prints a decision or
    nothing, and nothing (also on any error) leaves the dialog to the person at the dashboard."""
    if a.name == "secret-guard":   # every tool call, before any permission check: the protected paths are no one's to approve
        from . import approval
        try:
            event = json.load(sys.stdin)
            hit = approval.protected_path(str(event.get("tool_name") or ""), event.get("tool_input") or {})
        except Exception as e:  # noqa: BLE001 - a guard that cannot read the call blocks it
            die(f"blocked: secret-guard could not run ({e})", 2)
        if hit:
            die(f"blocked: {hit} is off limits to every tool (the household's secrets, database, keys and browser profiles); "
                "nobody can approve it here or on the phone. If a person needs it, they do it themselves in a terminal.", 2)
        return
    if a.name == "permission":
        from . import approval
        try:
            decision = approval.ask(json.load(sys.stdin), store.connect())
        except Exception as e:  # noqa: BLE001 - never a decision by accident
            print(f"finnamon: permission hook: {e}", file=sys.stderr)
            return
        if decision:
            print(json.dumps(decision))
        return
    try:   # a guard that crashes must still block (only exit 2 blocks; settings.json also maps any other failure to 2)
        event = json.load(sys.stdin)
        tool_input = event.get("tool_input") or {}
        if a.name == "browser-guard":
            if not os.environ.get("FINNAMON_IMPORT_SESSION"):
                return   # the household session: its allow list is the rule
            import shlex
            cmd = str(tool_input.get("command") or "")
            if any(c in cmd for c in ";|&`$<>()#{}\n"):   # one plain command: no lists, pipes, redirects, groups, substitutions or comments
                die("blocked: one command at a time, no pipes, redirects or substitutions in the import session", 2)
            why = browser_command_ok(shlex.split(cmd), os.environ.get("FINNAMON_IMPORT_CDP"))
            if why:
                die(f"blocked: {why}", 2)
            return
        conn = store.connect()
        chat = store.get_state(conn, "chat_id")
        chats = ({str(chat)} if chat is not None else set()) | {str(r[0]) for r in conn.execute("SELECT telegram_user_id FROM owners WHERE telegram_user_id IS NOT NULL")}
        target = tool_input.get("chat_id")
        if target is None or str(target) not in chats:
            die(f"reply blocked: chat {target} is not the household group or a member's private chat", 2)
        charts = config.charts_dir().resolve()
        for f in tool_input.get("files") or []:
            p = Path(str(f)).expanduser().resolve()
            if not p.is_relative_to(charts) or p.suffix.lower() != ".png":
                die(f"reply blocked: only charts from `finnamon chart` (under {charts}) may be attached, not {f}", 2)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        die(f"reply blocked: reply-guard could not run ({e})", 2)


def cmd_property(a) -> None:
    """Stated assets (a house, a car): `property set "<name>" <value>`, `property remove "<name>"`, `property list`."""
    conn = store.connect()
    try:
        if a.action == "set":
            _triage_read_only()
            if not a.name or a.value is None:
                die('usage: finnamon property set "<name>" <value>')
            out(properties.set_value(conn, a.name, a.value))
        elif a.action == "remove":
            _triage_read_only()
            if not a.name:
                die('usage: finnamon property remove "<name>"')
            out({"removed": properties.remove(conn, a.name)})
        else:
            out(properties.listing(conn))
    except ValueError as e:
        die(str(e))


def cmd_account(a) -> None:
    if a.action == "list" and a.to:   # the accounts on another box, through its dashboard
        out(_http(_box_url(a.to), "/api/summary").get("accounts", [])); return
    if a.action in ("remove", "merge", "unmerge", "failover", "owner"):
        _human_only(f"account {a.action}")
    conn = store.connect()
    if a.action == "list":
        out([dict(r) for r in conn.execute("SELECT a.account_id, a.name, a.mask, a.type, a.subtype, a.type_override, COALESCE(a.source_type, a.type) AS bank_type, COALESCE(a.source_subtype, a.subtype) AS bank_subtype, a.owner, a.mirror_of, i.institution, i.source, i.last_synced_at, "
                                           "(SELECT count(*) FROM transactions t WHERE t.account_id=a.account_id) AS transactions, "
                                           "(SELECT current FROM balances b WHERE b.account_id=a.account_id AND current IS NOT NULL ORDER BY as_of DESC LIMIT 1) AS balance "   # the last one synced or imported that had a figure
                                           "FROM accounts a JOIN items i ON i.item_id=a.item_id ORDER BY i.institution, a.name")])
    elif a.action == "add":   # a manual account: a bank Plaid can't reach, fed by `finnamon import`
        _triage_read_only()
        if not a.new:
            die('usage: finnamon account add "<name>" --institution <bank> [--type checking|savings|credit|loan|investment] [--owner <member>] [--mask 1234]')
        try:
            owner = owners.resolve(conn, a.owner, joint=True) if a.owner else _default_owner(conn)
            out(imports.add_account(conn, a.new, a.institution or a.new.split()[0], a.type, owner, a.mask))
        except (ValueError, sqlite3.IntegrityError) as e:
            die(str(e))
    elif a.action == "remove":
        if not a.new:
            die('usage: finnamon account remove "<name>" [--yes]')
        row = imports.lookup_account(conn, a.new)
        if row and row["source"] == "manual" and not a.yes:
            try:
                ans = input(f"Remove {row['institution']} · {row['name']} and its {row['transactions']} transactions? [y/N] ")
            except EOFError:
                ans = ""   # no terminal to ask: --yes says it
            if ans.strip().lower() not in ("y", "yes"):
                die("not removed (--yes when there is no terminal to ask)")
        try:
            out(link.remove_account(conn, a.new))
        except (ValueError, runmod.Locked, sqlite3.OperationalError, sqlite3.IntegrityError) as e:
            die(str(e))
    elif a.action == "balance":   # a manual account whose file had no running balance
        _triage_read_only()
        amt = imports.parse_money(a.existing) if a.existing else None
        if not a.new or amt is None:
            die('usage: finnamon account balance "<name>" <amount>   (what a card or loan owes is a positive number)')
        try:
            out(imports.set_balance(conn, a.new, amt))
        except ValueError as e:
            die(str(e))
    elif a.action in ("type", "kind"):   # the household's type for an account, kept through every sync; reversible, so the assistant may
        _triage_read_only()
        if not a.new or bool(a.existing) == a.clear:
            die(f'usage: finnamon account type <account_id> {"|".join(imports.KINDS)}   |   finnamon account type <account_id> --clear')
        try:
            out(imports.set_type(conn, a.new, None if a.clear else a.existing))
        except ValueError as e:
            die(str(e))
    elif not a.new or (a.action in ("merge", "owner") and not a.existing):
        die({"merge": "usage: finnamon account merge <new account_id> <existing account_id>", "owner": "usage: finnamon account owner <account_id> <member>"}
            .get(a.action, f"usage: finnamon account {a.action} <account_id>"))
    elif a.action == "merge":
        try:
            why = link.merge_problem(conn, a.new, a.existing)
        except ValueError as e:
            die(str(e))
        if why and not a.force:
            die(f"{why}. If they really are one account, run it again with --force.")
        link.mark_mirror(conn, a.new, a.existing); out({"merged": a.new, "into": a.existing})
    elif a.action == "unmerge":
        try:
            out({"unmerged": link.unmerge(conn, a.new)})
        except ValueError as e:
            die(str(e))
    elif a.action == "failover":
        out({"promoted": link.failover(conn, a.new)})
    elif a.action == "owner":
        try:
            owner = owners.resolve(conn, a.existing, joint=True)
        except ValueError as e:
            die(str(e))
        if not conn.execute("UPDATE accounts SET owner=?, owner_before_merge=NULL WHERE account_id=?", (owner, a.new)).rowcount:
            die(f"no account {a.new!r}; `finnamon account list` shows the ids")
        out({"account": a.new, "owner": owner})


BROWSER_SESSION = "finnamon-import"   # the agent-browser session the skill drives; every allowed command names it
# What the browser-import session may run without asking. Its input is bank pages and transaction memos, which are
# attacker-controlled text, so the grammar is exact: a verb and the few flags the skill uses, checked argument by argument by
# `finnamon hook browser-guard` (a permission pattern cannot say "wait, but not wait --fn", and --fn is eval by another name).
# Typing (fill, type, keyboard, find ... fill), eval, cookies, storage, upload, download, tab new, and any launch option
# (--profile, --proxy, --cdp, --init-script ...) are not in it and prompt the person.
BROWSER_GRAMMAR: dict[str, tuple[set[str], int]] = {   # verb -> (flags it may carry, how many positional arguments)
    "snapshot": ({"-i", "-c", "--interactive", "--compact"}, 0), "get": (set(), 1), "click": (set(), 1), "select": (set(), 2),
    "scroll": (set(), 2), "scrollintoview": (set(), 1), "press": (set(), 1), "back": (set(), 0), "reload": (set(), 0),
    "is": (set(), 2), "session": (set(), 0), "close": (set(), 0), "connect": (set(), 1),
}
BROWSER_GET = {"url", "title"}   # `get <what>`: the page's address or title; never html/attr/cdp-url. Not `text`: agent-browser
# wants a selector after it, which is a second positional this grammar does not allow, so `get text` only ever errors -- `snapshot` reads the page.
BROWSER_PRESS = {"Enter", "Tab", "Escape", "PageDown", "PageUp", "End", "Home", "ArrowDown", "ArrowUp"}
BANK_LOGIN = {"hsbc": "https://www.us.hsbc.com/"}   # the page chrome_launch opens for a bank; any other bank opens blank and the person navigates.
# Not a permission: the session can never `open` anything, listed bank or not, so this only picks where the window starts.
CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable", "/opt/google/chrome/chrome"]
PORT_WAIT_SECONDS, POLL_SECONDS = 20.0, 0.2   # how long chrome_launch waits for Chrome to write DevToolsActivePort, and how often it looks


def chrome_launch(chrome: str, profile: Path, url: str) -> str:
    """Open the bank's login page in real Chrome and return the CDP address of that window, or die().

    The window is Finnamon's, not agent-browser's, and nothing is attached to it while the person logs in: a bank's risk
    engine (HSBC US runs Transmit Security) fails the login outright when a DevTools client is driving, but passes a
    browser that merely *has* the port open, and does not look again once the person is through. So Chrome is launched
    here with the port and no automation switch of any kind -- `navigator.webdriver` is false and Chrome shows no
    "unsupported command-line flag" banner -- and the session attaches afterwards (`connect`, in the grammar above)."""
    port_file = profile / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)   # stale from the last run; we wait for Chrome to write a fresh one
    try:   # --remote-debugging-port=0: the kernel picks a free one, so a Chrome the person already debugs on 9222 cannot collide
        subprocess.Popen([chrome, f"--user-data-dir={profile}", "--remote-debugging-port=0", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        die(f"could not start Chrome: {e}")
    for _ in range(int(PORT_WAIT_SECONDS / POLL_SECONDS)):   # Chrome writes the port on startup; a profile another Chrome holds never gets there
        if port_file.exists() and (text := port_file.read_text().split("\n")[0].strip()).isdigit():
            for host in ("127.0.0.1", "[::1]"):   # which family it binds varies; take the one that answers
                cdp = f"http://{host}:{text}"
                try:
                    urllib.request.urlopen(cdp + "/json/version", timeout=2).read()
                    return cdp
                except OSError:
                    continue
        time.sleep(POLL_SECONDS)
    die("Chrome did not open a debugging port. A Finnamon browser may already be open on that profile: close that window, "
        "or quit it, then start the import again.")
    return ""   # noqa: RET503 - die() exits; unreachable


def chrome_path() -> str | None:
    """Real Google Chrome, or FINNAMON_CHROME. Never agent-browser's bundled Chrome for Testing: banks fingerprint that
    build and it cannot keep a device registration, so the caller dies rather than fall back to it."""
    override = os.environ.get("FINNAMON_CHROME")
    for c in [override] if override else CHROME:
        found = shutil.which(c)   # a bare name is looked up on PATH; a path is checked as an executable file
        if found:
            return found
    return None


def browser_command_ok(argv: list[str], cdp: str | None = None) -> str | None:
    """None when argv is a command the import session may run without asking; otherwise why not. Pure, so tests pin it."""
    if not argv:
        return "empty command"
    if argv[0] == "sleep":
        return None if len(argv) == 2 and argv[1].isdigit() and int(argv[1]) <= 60 else "sleep takes one number of seconds, up to 60"
    if argv[0] == "finnamon":
        return None if argv[1:2] == ["import"] or argv[1:3] == ["account", "list"] else "only finnamon import and finnamon account list"
    if argv[0] != "agent-browser":
        return f"{argv[0]} is not a command of this session"
    if argv[1:3] != ["--session", BROWSER_SESSION]:
        return f"every agent-browser call starts with --session {BROWSER_SESSION}"
    rest = argv[3:]
    if rest[:1] == ["--headed"] or rest[:1] == ["open"]:
        return "the window is already open; attach to it with connect, never open one of your own"
    if not rest:
        return "no verb"
    verb, args = rest[0], rest[1:]
    if verb not in BROWSER_GRAMMAR:
        return f"{verb} is not a verb of this session"
    flags, npos = BROWSER_GRAMMAR[verb]
    pos = [a for a in args if not a.startswith("-")]
    bad = [a for a in args if a.startswith("-") and a not in flags]
    if bad:
        return f"{verb} does not take {bad[0]}"
    if len(pos) != npos:
        return f"{verb} takes {npos} argument(s)"
    if verb == "connect":   # only the window Finnamon opened, whose address it passed in the environment
        return None if pos[0] == (cdp or os.environ.get("FINNAMON_IMPORT_CDP")) else "connect takes the address Finnamon opened the window on"
    if verb == "get" and pos[0] not in BROWSER_GET:
        return f"get {pos[0]} is not allowed"
    if verb == "press" and pos[0] not in BROWSER_PRESS:
        return f"press {pos[0]} is not allowed"
    if verb == "scroll" and (pos[0] not in ("up", "down") or not pos[1].isdigit()):
        return "scroll takes up|down and a number"
    if verb in ("click", "scrollintoview", "select") and not pos[0].startswith("@"):
        return f"{verb} takes a snapshot ref (@e12), not a selector"
    return None


def browser_guard_settings() -> dict:
    """The PreToolUse hook for the import session, passed on the launcher's command line. It runs this checkout's code by
    the interpreter that launched the session, so it never depends on which `finnamon` is on PATH, and a crash blocks."""
    guard = f"{shlex_quote(sys.executable)} -m finnamon.cli hook browser-guard || exit 2"
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]}}


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def browser_session_tools(to: str | None) -> tuple[list[str], list[str]]:
    """(allowed, disallowed) for the import session. The allow list is the grammar's prefixes only -- `connect *` among them,
    so the one address it may attach to is pinned by the guard hook (browser_command_ok against FINNAMON_IMPORT_CDP) and never
    written into this list: an allow-list entry rides the exec'd argv, which every local user can read out of `ps`, and the
    address it would carry is a live unauthenticated CDP endpoint into a window logged in to the bank. The hook is what
    decides; the disallow list is derived from the assistant's .claude/settings.json so the household's own allow list (normal,
    budget, alias, query, the Telegram tools) can never leak into this session."""
    allowed = [f"Bash(agent-browser --session {BROWSER_SESSION} {v} *)" if BROWSER_GRAMMAR[v][0] or BROWSER_GRAMMAR[v][1] else f"Bash(agent-browser --session {BROWSER_SESSION} {v})" for v in BROWSER_GRAMMAR]
    allowed += ["Bash(finnamon import *)", "Bash(finnamon account list *)", "Bash(finnamon account list)", "Bash(sleep *)"]
    try:
        household = json.loads((assistant.dir() / ".claude/settings.json").read_text())["permissions"]["allow"]
    except (OSError, ValueError, KeyError):
        household = []
    disallowed = [t for t in household if t not in allowed] + ["Read", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
    return allowed, disallowed


def cmd_import(a) -> None:
    """`finnamon import "<account>" <file.csv>`: a bank's export into a manual account, here or (`--to <url>`) on the Finnamon
    box through its dashboard. `--browser <bank>` starts the Claude session that fetches the file through a visible browser
    (the person logs in; see the import-browser skill in the assistant bundle)."""
    _triage_read_only()
    to = _box_url(a.to)
    if a.browser:
        if _from_claude():
            die("the browser import is its own Claude session; run `finnamon import --browser <bank>` in a terminal")
        if not a.account:
            die("usage: finnamon import --browser <bank> [--to <url>]")
        exe = claude_runner.binary()
        if not exe:
            die("claude is not on PATH")
        chrome = chrome_path()
        if not chrome:
            die("Google Chrome not found; install it, or set FINNAMON_CHROME to its executable" + (f" (not an executable: {os.environ['FINNAMON_CHROME']})" if os.environ.get("FINNAMON_CHROME") else ""))
        if (problems := assistant.problems()):   # before the window opens: a die() after chrome_launch leaves the bank browser up with nothing driving it
            die("\n".join(problems))
        profile = config.home() / "chrome"
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile.chmod(0o700)   # mkdir's mode does not apply to a directory that already exists; it holds the bank's live cookies
        if to and not _box_key(to):   # the sealed session could only meet the 401, and cannot set an env var, so refuse before a bank login is spent
            die(f"--to {KEY_HINT}")
        cdp = chrome_launch(chrome, profile, BANK_LOGIN.get(a.account.lower(), "about:blank"))
        prompt = f"/import-browser {a.account}" + (f" --to {to}" if to else "") + f" --cdp {cdp}"
        allowed, disallowed = browser_session_tools(to)
        os.chdir(assistant.dir())   # the skill lives in the assistant directory
        # FINNAMON_IMPORT_SESSION: the CLI itself refuses every command but import and account list (main()), whatever the pattern
        # lists say, and pins --to to the box named here, so a page cannot talk the session into uploading a file elsewhere.
        # --setting-sources project: the person's own ~/.claude/settings.json (which may allow Bash outright) does not reach this
        # session. --settings: the Bash guard hook rides this command line, so it exists in this session and nowhere else
        # (a checked-in hook would run in every session in the checkout, against whatever `finnamon` is on PATH).
        # --strict-mcp-config: this session runs in the assistant directory too, and a second copy of the Telegram channel plugin's
        # server kills the household's. `--setting-sources project` should already keep it out (the plugin is enabled in
        # .claude/settings.local.json, a local source), but that is a guard held for another reason, and the skill drives
        # agent-browser over Bash and wants no MCP server of its own.
        # The window is already open (chrome_launch) on a Chrome profile of Finnamon's own, so the bank's known-device state
        # survives between runs; FINNAMON_IMPORT_CDP is its address, and the guard hook lets `connect` name that one and no other.
        # Every AGENT_BROWSER_* the person's shell carries is still dropped: auto-connect or a CDP URL of their own would attach
        # the session to their everyday Chrome, which holds every cookie they own.
        os.execve(exe, [exe, prompt, "--setting-sources", "project", "--strict-mcp-config",
                        "--settings", json.dumps(browser_guard_settings()),
                        "--allowedTools", *allowed, "--disallowedTools", *disallowed],
                  {**{k: v for k, v in os.environ.items() if not k.startswith("AGENT_BROWSER_")},   # no inherited auto-connect/cdp/args
                   "FINNAMON_IMPORT_SESSION": to or "local", "FINNAMON_IMPORT_CDP": cdp})
        return   # noqa: RET503 - execve does not return; tests stub it
    if not a.account or not a.file:
        die('usage: finnamon import "<account>" <file.csv> [--balance N] [--flip] [--dry-run] [--to <url>]   |   finnamon import --browser <bank> [--to <url>]')
    path = Path(a.file).expanduser()
    if _from_claude() and path.suffix.lower() != ".csv":
        die("from a Claude session, import reads .csv files only")   # never a way to print secrets.toml or a .env through an error message
    try:
        text = sys.stdin.read() if a.file == "-" else path.read_text(encoding="utf-8-sig", errors="replace")
        if to:   # the box does the parsing and the writing; this side only carries the file
            out(_upload(to, a.account, text, flip=a.flip, dry_run=a.dry_run, balance=a.balance)); return
        conn = store.connect()
        acct = imports.find_account(conn, a.account)
        out(imports.apply(conn, acct["account_id"], imports.parse(text), flip=a.flip, balance=a.balance, dry_run=a.dry_run))
    except (ValueError, OSError) as e:
        die(str(e))
    except sqlite3.OperationalError as e:   # busy_timeout ran out: the daemon is mid-cycle
        die(f"the database is busy ({e}); the file is untouched, try again in a minute")


def _box_url(to: str | None) -> str:
    """`--to`: the Finnamon box's dashboard, an http(s) origin. Inside the browser-import session it must be the box the
    launcher was pointed at (FINNAMON_IMPORT_SESSION holds it, or "local"): a bank page cannot redirect the upload. The
    household assistant has no box to upload to at all: a memo cannot talk it into sending a statement anywhere."""
    to = (to or "").rstrip("/")
    if to and not to.startswith(("http://", "https://")):
        die("--to is the Finnamon box's dashboard URL, e.g. https://mac.tailnet.ts.net")
    import urllib.parse
    if to.startswith("http://") and urllib.parse.urlsplit(to).hostname not in ("localhost", "127.0.0.1", "::1"):
        die("--to over plain http would send the box's key and the statement in the clear; use the https address `tailscale serve` gives the page")
    pinned = os.environ.get("FINNAMON_IMPORT_SESSION")
    if to and _from_claude() and not pinned:
        die("--to is for the browser-import session or a person at a terminal")
    if pinned and to != ("" if pinned == "local" else pinned):
        die(f"this import session {'imports locally' if pinned == 'local' else 'uploads to ' + pinned} and nowhere else")
    return to


KEY_HINT = "that dashboard needs its key: on the box run `finnamon web token`, then here: read -s FINNAMON_WEB_TOKEN && export FINNAMON_WEB_TOKEN   (typed, so the key stays out of this shell's history)"


def _box_key(to: str) -> str:
    """The key a `--to` request carries: FINNAMON_WEB_TOKEN (copied from `finnamon web token` on the box), or this computer's
    own key file only when `to` is this computer. A local file is never sent to another host: it is not that box's key, and a
    typo'd `--to` must not receive it. Only ever read here, never made: a missing key is the box's 401 to explain."""
    import urllib.parse
    tok = os.environ.get("FINNAMON_WEB_TOKEN", "").strip()
    if not tok and urllib.parse.urlsplit(to).hostname in ("localhost", "127.0.0.1", "::1"):
        try:
            tok = config.web_token_path().read_text().strip()
        except OSError:
            tok = ""
    return tok


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 30x from the box (or a typo'd host) is answered as an error, not followed: urllib's default handler copies every header,
    the key's Authorization included, onto the redirected request, to another host if Location names one."""
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_open = _OPENER.open   # `_http`'s one way out; tests replace it


def _http(to: str, path: str, data: bytes | None = None, content_type: str = "text/csv") -> dict:
    """One request to the box's dashboard; its JSON back, or a one-line error. The only network egress the CLI has."""
    import urllib.error
    import urllib.request
    tok = _box_key(to)
    headers = {**({"Authorization": f"Bearer {tok}"} if tok else {}), **({"Content-Type": content_type} if data else {})}
    req = urllib.request.Request(f"{to}{path}", data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with _open(req, timeout=180) as r:   # never follows a 30x: urllib would carry the bearer to whatever host Location names
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        msg = (parsed.get("error") if isinstance(parsed, dict) else None) or body or e.reason   # an HTML 502, a JSON list, an empty body: one line each
        if e.code == 401:   # the box's own message says `finnamon open`, which is for a browser on the box; from here the key travels as an env var
            die(f"{to} answered 401: {KEY_HINT}")
        die(f"{to} answered {e.code}: {str(msg)[:300]}")
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:   # HTTPException: http.client's own parse of a bad host or port, before anything is sent
        die(f"could not reach {to}: {e}")


def _upload(to: str, account: str, text: str, flip: bool = False, dry_run: bool = False, balance=None) -> dict:
    import urllib.parse
    q = {"account": account, **({"flip": "1"} if flip else {}), **({"dry_run": "1"} if dry_run else {}), **({"balance": str(balance)} if balance is not None else {})}
    return _http(to, f"/api/import?{urllib.parse.urlencode(q)}", text.encode("utf-8"))


# --- run / sync / detect / notify / daemon / heartbeat -----------------------------------------

def cmd_sync(a) -> None:
    _human_only("sync")
    conn = store.connect()
    try:
        held = runmod.lock()
    except runmod.Locked as e:
        die(str(e))
    try:
        out(sync.sync_all(conn) if not a.item else sync.sync_item(conn, a.item))
    finally:
        held.close()


def cmd_detect(a) -> None:
    if a.prelude:   # the assistant drafts detectors from the installed bundle, where there is no source to read
        print(detect.PRELUDE.read_text()); return
    conn = store.connect()
    if a.sql:
        f = next((p for p in detect.detectors() if p.stem == a.sql), None)
        if not f:
            die(f"no detector {a.sql}")
        print(detect.assemble(f, detect.kind_of(f)[1])); return
    if a.review:
        if a.draft or a.sql or a.only or a.as_of:
            die("--review takes no other options")
        if _from_claude():
            die("detectors are reviewed by a person at a terminal, not from a Claude session")
        _review_pending(a); return
    if a.draft:
        _draft(a); return
    as_of = a.as_of or store.now_local()
    ids = detect.run(conn, as_of, only=a.only)
    out({"as_of": as_of, "new_alerts": ids})


DRAFT_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _from_claude() -> bool:
    # presence, not truthiness: `CLAUDECODE="" finnamon ...` is still a Claude session
    return "FINNAMON_FROM_CLAUDE" in os.environ or "CLAUDECODE" in os.environ


def _human_only(cmd: str) -> None:
    if _from_claude():
        die(f"`finnamon {cmd}` is for a person at a terminal")


def _triage_read_only() -> None:
    """The /triage run reads bank memos, which are untrusted; the only write it may make is `triage set`."""
    if os.environ.get("FINNAMON_TRIAGE"):
        die("triage runs are read-only apart from `finnamon triage set`")


def _draft(a) -> None:
    _triage_read_only()
    # Claude may run this. SQL comes only from stdin; the name is confined to the household's detector-drafts/; never overwrite.
    if a.draft != "-":
        die("drafts are read from stdin only: finnamon detect --draft - --name <snake_name> < file.sql")
    name = a.name or "draft"
    if not DRAFT_NAME.fullmatch(name):
        die("--name must be snake_case letters, digits, underscores")
    detect.migrate_drafts()
    pend = detect.drafts_dir().resolve()
    p = (pend / f"{name}.sql").resolve()
    if p.parent != pend:
        die("invalid name")
    if p.exists() or any((detect.DETECTORS / tier / f"{name}.sql").exists() for tier in ("rules", "candidates")):
        die(f"a detector named {name} already exists; pick another name")
    body = sys.stdin.read()
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("--")).strip().lower()
    if not (code.startswith("select") or code.startswith("with")) or re.search(r"\b(insert|update|delete|drop|alter|create|replace|attach|pragma)\b", code):
        die("a detector is a single SELECT (or WITH ... SELECT); no DML")
    p.write_text(body)
    print(f"Drafted {p}. Not live until reviewed: finnamon detect --review")


def _review_pending(a) -> None:
    detect.migrate_drafts()
    pend = sorted(detect.drafts_dir().glob("*.sql"))
    if not pend:
        print("no pending detectors"); return
    store.connect()  # make sure the DB exists and is migrated
    ro = query.connect_ro()  # previews run read-only: a draft is untrusted until the human says yes
    for p in pend:
        print(f"\n=== {p.name} ===\n{p.read_text()}")
        try:
            _, kind = detect.kind_of(detect.DETECTORS / a.tier / p.name)
            rows = ro.execute(detect.assemble(p, kind), {"as_of": store.now_local()}).fetchall()
            print(f"(runs; {len(rows)} rows right now)")
        except Exception as e:  # noqa: BLE001
            print(f"(does not run: {e})")
        ans = input(f"Move to {a.tier}/? [y/N/d(elete)] ").strip().lower()
        if ans == "y":
            target = detect.DETECTORS / a.tier / p.name
            if target.exists() or (detect.DETECTORS / ("candidates" if a.tier == "rules" else "rules") / p.name).exists():
                print(f"a detector named {p.stem} already exists; rename the draft first"); continue
            p.rename(target); print("live")
        elif ans == "d":
            p.unlink(); print("deleted")


def cmd_notify(a) -> None:
    conn = store.connect()
    if a.list:
        out([{"id": r["id"], "kind": r["kind"], "text": notify.render(r)} for r in notify.pending(conn)]); return
    _human_only("notify")
    if a.restarted:   # the dashboard, after a restart cut a channel-mode turn short (web/server.js restartNotice)
        chat = store.get_state(conn, "chat_id")
        out({"sent": telegram.send_message(chat, notify.RESTARTED) if chat else None}); return
    n = notify.send_pending(conn)
    r = notify.send_roundup(conn, store.now_local(), force=a.roundup)
    out({"sent": n, "roundup_message_id": r})


def cmd_run(a) -> None:
    _human_only("run")
    conn = store.connect()
    try:
        out(runmod.cycle(conn, do_sync=not a.no_sync, do_triage=not a.no_triage, do_notify=not a.no_notify))
    except runmod.Locked as e:
        die(str(e))


def cmd_daemon(a) -> None:
    _human_only("daemon")
    from .daemon import Daemon
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    scheduler.rotate_logs()
    Daemon().start()


def cmd_heartbeat(a) -> None:
    _human_only("heartbeat")
    scheduler.rotate_logs()
    out({"sent": heartbeat.check(store.connect())})


def cmd_status(a) -> None:
    conn = store.connect()
    out({"version": __version__, "home": str(config.home()), "chat_id": store.get_state(conn, "chat_id"),
         "last_run": store.get_state(conn, "last_run"), "last_backup_at": store.get_state(conn, "last_backup_at"), "backup_error": store.get_state(conn, "backup_error"), "session": store.get_state(conn, "session"), "inbound": store.get_state(conn, "inbound") or "daemon", "inbound_conflict_at": store.get_state(conn, "inbound_conflict_at"), "channel_deaf_since": store.get_state(conn, "channel_deaf_since"), "daemon_alive": owners.daemon_alive(conn), "demo": bool(store.get_state(conn, "demo")), "plaid_keys": all(config.plaid_auth().values()),
         "items": [dict(r) for r in conn.execute("SELECT item_id, institution, owner, status, last_synced_at, last_error, "
                                                     "(SELECT group_concat(COALESCE(a.name, '') || COALESCE(' …' || a.mask, ''), ', ') FROM accounts a WHERE a.item_id=items.item_id) AS accounts FROM items")],
         "pending_alerts": conn.execute(f"SELECT count(*) FROM alerts WHERE {notify.SENDABLE}").fetchone()[0],
         "untriaged": len(triage.untriaged(conn)), "assistant": str(assistant.dir()), "assistant_problems": assistant.problems(),
         "scheduler": scheduler.status(), "claude": shutil.which("claude"), "finnamon": shutil.which("finnamon")}
        | _stray_plugin_report())


def cmd_doctor(a) -> None:
    """Every prerequisite and piece of setup, each with its fix: what `status` reports, explained. Reads only: no file,
    database or key is created (a fresh box has none of them yet, and that is what it reports)."""
    checks: list[tuple[bool | None, str, str, str]] = []   # (ok, what, detail, fix); None = worth knowing, not broken

    def check(ok, what, detail, fix=""):
        checks.append((ok, what, detail, fix))

    ok, msg = _claude_code_status()
    check(ok, "Claude Code", msg)   # the message carries its own fix
    node = shutil.which("node")
    try:
        ver = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip() if node else ""
    except (OSError, subprocess.TimeoutExpired):
        ver = ""
    major_minor = tuple(int(x) for x in re.findall(r"\d+", ver)[:2]) if ver else ()
    check(major_minor >= (20, 12), "Node", ver or (f"`{node} --version` failed" if node else "not on PATH"), "the dashboard needs Node 20.12+: brew install node")
    web = Path(scheduler.repo_dir()) / "web"
    npm_ok = (web / "node_modules" / "node-pty").exists()
    check(npm_ok, "Dashboard packages", f"{web}/node_modules" + ("" if npm_ok else " is missing"), "cd web && npm install && finnamon install")
    check(bool(shutil.which("finnamon")), "finnamon on PATH", shutil.which("finnamon") or "not found", "uv tool install -e .   (in the checkout)")

    sp = config.secrets_path()
    plaid, tg_token = {}, ""
    try:   # a hand-edited or unreadable file is exactly when someone runs doctor: report it, keep checking
        sec = config.load_secrets()
        plaid, tg_token = dict(sec.get("plaid") or {}), str((sec.get("telegram") or {}).get("bot_token") or "")
        if not sp.exists():
            check(False, "Secrets", f"{sp} does not exist", "finnamon init")
        elif sp.stat().st_mode & 0o077:
            check(False, "Secrets", f"{sp} is readable by others", f"chmod 600 {sp}")
    except (OSError, ValueError, TypeError, AttributeError) as e:   # tomllib.TOMLDecodeError is a ValueError
        check(False, "Secrets", f"cannot read {sp}: {e}", f"fix the file by hand (chmod 600 {sp}; it is TOML), or move it aside and run finnamon init")
    env = config.plaid_env()
    if not sp.exists():
        pass   # said once, under Secrets
    elif not plaid.get("client_id") or not plaid.get(f"{env}_secret"):
        have = [e for e in ("production", "sandbox") if plaid.get(f"{e}_secret")]
        check(False, "Plaid keys", f"no {env} secret" + (f" (only {', '.join(have)})" if have else ""),
              "finnamon init --plaid (dashboard.plaid.com → Team Settings → Keys); a sandbox-only key sees test banks: "
              "enter the Production secret there. docs/INSTALL.md covers production access")
    else:
        ok, msg = _plaid_probe(env)
        check(ok, f"Plaid keys ({env})", msg, "" if ok else "finnamon init --plaid (check the keys against dashboard.plaid.com → Team Settings → Keys)")
    if not tg_token:
        if sp.exists():
            check(False, "Telegram bot", "no bot token", "finnamon init (in Telegram: @BotFather → /newbot)")
    else:
        try:
            me = telegram.get_me(tg_token)
            privacy = not me.get("can_read_all_group_messages")
            check(None if privacy else True, "Telegram bot", f"@{me.get('username')}" + (": privacy mode is on, so it cannot read a group" if privacy else ""),
                  "in @BotFather: /setprivacy → Disable" if privacy else "")
        except Exception as e:  # noqa: BLE001 - a bad token, a network blip: both are this line's answer
            check(False, "Telegram bot", f"Telegram rejected the token or could not be reached: {e}", "check the token with @BotFather, then finnamon init")

    # Read-only: store.connect() would create a missing database and migrate an existing one under a daemon that may
    # still run the old code (a `git pull` before `finnamon update`).
    conn = None
    if not config.db_path().exists():
        check(False, "Database", f"{config.db_path()} does not exist", "finnamon init")
    else:
        try:
            conn = sqlite3.connect(f"file:{config.db_path()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            chat = store.get_state(conn, "chat_id")
            check(bool(chat), "Household chat", f"chat {chat}" if chat else "none recorded", "finnamon init (it gives you a code to send the bot)")
            items = [dict(r) for r in conn.execute("SELECT item_id, institution, status, last_error FROM items")]
            check(bool(items), "Banks", f"{len(items)} linked" if items else "none linked, so nothing is watched",
                  "finnamon open → Add account → Link account, or finnamon link")
            for it in items:
                if it["status"] in (None, "good"):
                    continue
                relogin = "LOGIN" in it["status"] or it["status"] in ("PENDING_EXPIRATION", "PENDING_DISCONNECT")
                check(False, f"Bank {it['institution']}", f"{it['status']}" + (f": {it['last_error']}" if it["last_error"] and not relogin else ""),
                      f"finnamon link --update {it['item_id']}, or reply 'fix {it['item_id'] if not it['institution'] or [i['institution'] for i in items].count(it['institution']) > 1 else it['institution']}' in the household chat" if relogin else "retried on every sync; if it persists, see the daemon's log")
            last, err = store.get_state(conn, "last_backup_at"), store.get_state(conn, "backup_error")
            if err or last:
                check(not err, "Backups", f"last failed: {err}" if err else f"last {last}, in {backup.backup_dir()}",
                      "see the daemon's log; the next sync retries" if err else "")
            elif items:
                check(None, "Backups", "none yet; the daemon makes one each week")
        except sqlite3.Error as e:
            conn = None
            check(False, "Database", f"cannot read {config.db_path()}: {e}", "finnamon update (applies migrations); a corrupt file needs restoring")

    missing = [n for n in scheduler.render() if not (scheduler.unit_dir() / n).exists()]
    drift = [n for n in scheduler.drifted_units() if n not in missing]
    if missing or drift:
        check(False, "Scheduler", ("not installed: " + ", ".join(missing)) if missing else ("out of date: " + ", ".join(drift)), "finnamon install")
    elif conn is not None:
        alive = owners.daemon_alive(conn)
        check(alive, "Scheduler", "jobs installed, daemon " + ("running" if alive else "not checking in"),
              "" if alive else f"finnamon install; logs in {scheduler.log_dir() if platform.system() == 'Darwin' else 'journalctl --user -u ' + scheduler.unit('daemon')}")
    else:
        check(True, "Scheduler", "jobs installed")
    if platform.system() == "Linux" and not missing and (why := scheduler.linger_problem()):
        check(False, "Linger", why, f"sudo loginctl enable-linger {scheduler.login_user()}")

    tp = config.web_token_path()
    try:
        tok = tp.read_text().strip()
        tok_ok = bool(config.WEB_TOKEN.fullmatch(tok)) and not tp.stat().st_mode & 0o077
        detail = str(tp) if tok_ok else f"{tp} does not exist yet" if not tok else f"{tp} is not a 0600 hex key"   # blank counts as missing, as in config.web_token
    except FileNotFoundError:
        tok_ok, detail = False, f"{tp} does not exist yet"
    except OSError as e:
        tok_ok, detail = False, f"cannot read {tp}: {e}"
    if scheduler.web_args() or tok_ok:   # no dashboard installed: the Node / packages lines already say so
        check(tok_ok, "Dashboard key", detail, "finnamon open (makes it; a bad one: remove the file first)")
        if scheduler.web_args() and (why := _dashboard_port_problem()):
            check(False, "Dashboard port", why, "stop that program, or set PORT=<free port> for the dashboard and run finnamon install")

    r_ok, r_detail, r_fix = remote.doctor_line()   # every run, never silent: off is a line too
    check("optional" if r_ok is None else r_ok, "Remote access", r_detail, r_fix)   # off or unknown is not a problem
    probs = assistant.problems()
    check(not probs, "Assistant directory", (probs[0] + (f" (and {len(probs) - 1} more)" if len(probs) > 1 else "")) if probs
          else f"{assistant.dir()} installed and trusted", "finnamon install")
    ready, detail = voice.status()
    check(True if ready else "optional", "Voice", detail, "" if ready else "optional: finnamon voice setup")   # Talk still works through the browser
    if conn is not None:
        mode = store.get_state(conn, "inbound") or "daemon"
        web = bool(scheduler.web_args())
        check(web or mode != "session", "Telegram inbound",
              {"daemon": "daemon (legacy): it answers the chat with its own claude -p session",
               "session": "session (default): the daemon types the chat into the dashboard's intercom session" + ("" if web else ", but the dashboard is not installed"),
               "channel": "channel: Claude Code's Telegram channel plugin in the dashboard's session reads the chat"}.get(mode, mode),
              "cd web && npm install && finnamon install, or finnamon channel off")
    strays = _stray_plugin_report()
    if strays:
        check(False, "Telegram plugin", "registered outside the assistant: " + ", ".join(strays["stray_telegram_plugins"]), strays["stray_telegram_plugins_fix"])

    for ok, what, detail, fix in checks:
        print(f"{'✓' if ok is True else '·' if ok == 'optional' else '!' if ok is None else '✗'} {what}: {detail}")
        if ok is not True and fix:
            print(f"    fix: {fix}")
    failed = sum(ok is False for ok, *_ in checks)
    print(f"\n{failed} problem(s)" if failed else "\nall good")
    if failed:
        sys.exit(1)


def _stray_plugin_report() -> dict:
    strays = assistant.stray_channel_plugins()
    if not strays:
        return {}
    # A registered folder whose .claude/settings.local.json also enables the plugin seeds more: Claude Code gives every
    # worktree of that repo and every subfolder a claude session starts in a registration of its own (reproduced 2026-09-27).
    seeding = [p for p in strays if p.startswith("/") and assistant.channel_plugin_registered(Path(p))]
    return ({"stray_telegram_plugins": strays,
            "stray_telegram_plugins_fix": f"a claude started in any of these steals the bot's updates; only {assistant.dir()} should carry the plugin. "
                                          f"For each (seeding ones first): cd <folder> && claude plugin uninstall {assistant.CHANNEL_PLUGIN} --scope local"}
            | ({"stray_telegram_plugins_seeding": seeding} if seeding else {}))


def _git(repo, *args, check: bool = True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def _session_tip(conn) -> None:
    """update: once, tell a household with a bot on `channel` or `daemon` that `session` exists (never switch it for them)."""
    try:
        if store.get_state(conn, "session_tip_shown") or not config.load_secrets().get("telegram", {}).get("bot_token"):
            return
        store.set_state(conn, "session_tip_shown", 1)
        mode = store.get_state(conn, "inbound") or "daemon"
    except sqlite3.Error:   # a locked or half-migrated database: the tip can wait for the next update
        return
    if mode in ("channel", "daemon"):
        print("New: `finnamon channel session` puts Telegram in the same conversation as the dashboard — no plugin needed.")


def cmd_update(a) -> None:
    """Adopt new code without reprovisioning: pull, migrate, restart what went stale.

    `install` rewrites the unit files and reloads every service, which costs the household its assistant: the dashboard's
    Claude session is the household's (in channel mode it is also the one Telegram talks to). `update` leaves the units
    alone and restarts in place, so the session comes back by id instead of starting a stranger."""
    if _from_claude():
        die("`finnamon update` restarts the dashboard, and in channel mode that is the session you are talking to; run it in a terminal on the Finnamon box")
    repo = Path(scheduler.repo_dir())
    if not (repo / ".git").exists():   # a file, not a directory, in a git worktree
        die(f"{repo} is not a git checkout, so there is nothing to pull; `finnamon install` reprovisions from whatever is there")
    if a.check:   # the dashboard's "Update available": what the pull below would bring, looked at the way --dry-run looks
        _git(repo, "fetch", "--quiet", check=False)
        r = _git(repo, "rev-list", "--count", "HEAD..@{u}", check=False)
        if r.returncode:
            die(f"{repo} has no upstream branch to update from: {r.stderr.strip()}")
        ahead = int(r.stdout.strip() or 0)
        latest = _git(repo, "show", "@{u}:VERSION", check=False).stdout.strip() if ahead else ""
        log = _git(repo, "show", "@{u}:CHANGELOG.md", check=False).stdout if ahead else ""
        m = re.search(r"^- \*\*(.+?)\*\*", log.split("\n## [", 2)[1] if log.count("\n## [") else "", re.M | re.S)   # the newest release's first headline, wrapped or not
        out({"current": __version__, "latest": latest or __version__, "ahead": ahead, "notes": " ".join(m.group(1).split()) if m else None})
        return

    # The units are compared before anything is pulled or migrated: it reads only files, and a release whose unit files
    # moved cannot be adopted by restarting into them, so there is no reason to have touched the database first.
    scheduled = any(scheduler.installed(n) for n in ("daemon", "web"))   # init's schedule step can be declined: no units, so none to drift or restart
    if scheduled and (drift := scheduler.drifted_units()):
        die("these service files are no longer what this version generates, so restarting would keep running the old "
            f"definition: {', '.join(drift)}. Run `finnamon install` instead (it rewrites them, and the assistant starts fresh).\n"
            "If the only difference is a PATH, you are running update from a shell whose PATH differs from the daemon's "
            "(a venv, nvm); install would bake that one in. Compare: finnamon install --dry-run")

    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if not a.no_pull:
        # Tracked changes only: `detect --review` leaves an approved detector untracked under finnamon/detectors/, and a
        # household that has ever added one would otherwise never be able to update again.
        dirty = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            die(f"{repo} has uncommitted changes to tracked files; commit, stash, or use --no-pull to adopt the tree as it stands:\n{dirty}")
        if a.dry_run:   # `install --dry-run` writes nothing either: looking must not move HEAD or the schema
            _git(repo, "fetch", "--quiet", check=False)
            ahead = _git(repo, "rev-list", "--count", "HEAD..@{u}", check=False).stdout.strip() or "0"
            remote = _git(repo, "config", "--get", "remote.origin.url", check=False).stdout.strip()
            names = [l for l in _git(repo, "diff", "--name-only", "HEAD..@{u}", check=False).stdout.splitlines() if l]
            print(f"{ahead} commit(s) to pull from {remote or 'the tracked remote'}")
            _install_bundle(dry_run=True)
            print("would restart: " + (", ".join(scheduler.services_for(names)) or "nothing") + " (nothing was pulled or migrated)")
            if _npm_needed(names):
                print("would run `npm ci` in web/ (its package files changed)")
            return
        remote = _git(repo, "config", "--get", "remote.origin.url", check=False).stdout.strip()
        pull = _git(repo, "pull", "--ff-only", check=False)
        if pull.returncode:
            die(f"git pull --ff-only failed, so nothing was restarted:\n{(pull.stderr or pull.stdout).strip()}")
        print((pull.stdout or "").strip() or "already up to date")
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if not a.no_pull and before != after:
        # What was adopted, and from where: --ff-only says the history did not fork, never who wrote it.
        print(f"adopted {before[:9]}..{after[:9]} from {remote or 'the tracked remote'}")

    changed = [l for l in _git(repo, "diff", "--name-only", f"{before}..{after}").stdout.splitlines() if l] if before != after else []
    # Without a pull there is no range to compare, and the tree may have moved under the running services by any amount
    # (a pull by hand, a checkout): restart both rather than reporting "nothing to do" over code nobody is running yet.
    services = ["daemon", "web"] if (a.all or a.no_pull) else scheduler.services_for(changed)
    if any(c.startswith(assistant.BUNDLE_REL) for c in changed):
        print("note: this release changes the assistant bundle (its instructions and permission set)")
    if not scheduled:
        services = []

    if a.dry_run:   # every --dry-run path ends here: looking must not take the lock or move the schema
        _install_bundle(dry_run=True)
        print("would restart: " + (", ".join(services) or "nothing") + " (nothing was pulled or migrated)")
        return

    # One writer at a time, and never mid-sync: the lock the daemon's cycle holds also keeps migrate off a live schema
    # and stops a restart landing in the middle of a Plaid page.
    try:
        held = runmod.lock()
    except runmod.Locked:
        die("a sync is running right now; nothing was migrated or restarted. Try again in a minute.")
    try:
        try:
            for m in store.migrate(store.connect()):
                print(f"migration {m}")
        except sqlite3.Error as e:
            die(f"migration failed, so nothing was restarted: {e}")
        # The bundle is what the sessions read at startup: a changed one restarts both jobs whatever git says changed.
        try:
            if _install_bundle()["changed"]:
                services = ["daemon", "web"]
        except OSError as e:
            die(f"could not write the assistant bundle to {assistant.dir()} ({e}), so nothing was restarted; the schema is migrated")
        if (moved := detect.migrate_drafts()):
            print(f"moved detector drafts out of the checkout into {detect.drafts_dir()}: {', '.join(moved)}")
        _register_channel_plugin(store.connect(), force=a.force)
        _session_tip(store.connect())
        if _npm_needed(changed):
            _npm_ci()

        if not scheduled:
            print("no scheduled services (init's schedule step was skipped): nothing to restart; `finnamon install` adds them, or restart a daemon you run by hand")
            return
        if not services:
            page = any(c.startswith("web/public/") for c in changed)
            print("nothing to restart" + (" (web/public is read per request: reload the page)" if page else ""))
            return
        try:
            for name in scheduler.restart(services):
                print(f"restarted {name}")
                if name == "web":
                    _announce_dashboard_port()
        except (RuntimeError, subprocess.CalledProcessError) as e:
            die(str(e))
    finally:
        held.close()


def _npm_needed(changed) -> bool:
    """A release that changed the dashboard's package files leaves its node_modules stale, and the dashboard crash-loops on the
    missing module; only a household that installed the dashboard (it has node_modules) has anything to refresh."""
    web = Path(scheduler.repo_dir()) / "web"
    return (web / "node_modules").is_dir() and any(c in ("web/package.json", "web/package-lock.json") for c in changed)


def _npm_ci() -> None:
    web = Path(scheduler.repo_dir()) / "web"
    npm = shutil.which("npm")
    if not npm:
        die("this release changed web/package.json but `npm` is not on PATH, so the dashboard would crash-loop; install Node, run `cd web && npm ci`, then `finnamon update --no-pull`")
    print("web/ package files changed: npm ci")
    argv = [npm, "ci" if (web / "package-lock.json").exists() else "install"]
    try:
        r = subprocess.run(argv, cwd=web, timeout=NPM_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        die(f"npm did not finish ({e}); run `cd web && npm ci`, then `finnamon update --no-pull`")
    if r.returncode:
        die("npm ci failed, so nothing was restarted; fix that (`cd web && npm ci`), then `finnamon update --no-pull`")


def cmd_install(a) -> None:
    if _from_claude():
        die("`finnamon install` reprovisions the services and retires the household's conversation, which in channel mode is the session you are talking to; run it in a terminal on the Finnamon box")
    if a.uninstall:
        scheduler.uninstall(); print("removed"); return
    if not _retire_intercom_session(a.yes, dry_run=a.dry_run):
        return
    _install_bundle(dry_run=a.dry_run)   # before the jobs come up: the daemon and the dashboard start their sessions in it
    if not a.dry_run and config.db_path().exists():
        _register_channel_plugin(store.connect(), force=a.force)
    try:
        files = scheduler.install(dry_run=a.dry_run)
    except (RuntimeError, subprocess.CalledProcessError) as e:
        die(str(e))
    for f in files:
        print(f)
    if not a.dry_run and scheduler.web_args():
        _announce_dashboard_port()
    if config.db_path().exists():   # before init there is no inbound to speak of; install must not create the database
        _warn_no_bun_for_channel(store.connect())


def _retire_intercom_session(yes: bool = False, dry_run: bool = False) -> bool:
    """`install` reprovisions, so the assistant comes back new. The id lives here and not in the service files, which is
    why rewriting those never started a fresh one; the dashboard reads this file on every start and resumes whatever it
    finds (web/server.js, INTERCOM_FILE).

    There is no install-but-keep-the-conversation: that is `finnamon update`. So declining stops the whole command
    rather than half-reprovisioning. False = nothing was touched."""
    f = config.home() / config.INTERCOM_FILE
    try:
        rec = json.loads(f.read_text())
    except (OSError, ValueError):
        return True   # no dashboard yet, or nothing readable to retire
    if not isinstance(rec, dict):
        return True   # valid JSON, but not a record we wrote: leave it alone rather than guess
    old, prev = rec.get("id"), rec.get("prev")
    if dry_run:
        if old:
            print(f"would retire the household's conversation ({old}); `finnamon update` picks up a release and keeps it")
        return True
    if old and not yes:
        # The id is only useful after the decision, so it goes last: the line that has to stop a hand already typing
        # `install` out of habit is the one naming the other command.
        print("`install` reprovisions the services and starts the assistant over: it comes back knowing nothing,")
        print("and in channel mode that conversation is the Telegram chat's thread too.")
        print("  To pick up new code and keep it, that is `finnamon update`.")
        print(f"  retiring: {old}")
        try:
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                print("nothing changed.")
                return False
        except EOFError:
            die("install retires the assistant's conversation, so it needs a terminal to confirm (or pass --yes)")
    _retire_intercom_file()
    # --strict-mcp-config because this transcript lives under the assistant directory: a plain `claude` there starts a
    # second copy of the Telegram plugin's server and kills the household session's copy (README, the channel-mode note).
    # Claude Code keeps a transcript under the directory the session ran in: the assistant directory since v0.8.0.0, the
    # checkout before it, so the one retirement across that release resumes from the checkout, not from here.
    where = f"in the directory that session ran in ({assistant.dir()}, or the checkout {scheduler.repo_dir()} for a session older than v0.8.0.0)"
    if old:
        print(f"the assistant starts a fresh conversation; the retired one is still on disk: claude --strict-mcp-config --resume {old}, {where}")
    if prev:
        # web/server.js keeps the id it replaced precisely because nothing else can reach that transcript again.
        print(f"  and the one before it: claude --strict-mcp-config --resume {prev}")
    return True


def _retire_intercom_file() -> None:
    """Leave intercom.json as {"prev": <id>}: server.js mints a fresh id for a record with no valid `id`, so this retires
    the dashboard's conversation just as deleting the file would, while keeping the one handle on that transcript.
    Kept on disk rather than only in a terminal: if what follows fails, a red error reasonably reads as "nothing happened"."""
    f = config.home() / config.INTERCOM_FILE
    try:
        rec = json.loads(f.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(rec, dict):
        return
    keep = rec.get("id") or rec.get("prev")
    if keep:
        f.write_text(json.dumps({"prev": keep})); f.chmod(0o600)   # 0600 like the rest of ~/.finnamon
    else:
        f.unlink(missing_ok=True)


def _install_bundle(dry_run: bool = False) -> dict | None:
    """init, install and update write the assistant bundle to FINNAMON_HOME/assistant (finnamon/assistant.py). On the first
    write the household's conversations are retired: Claude Code keeps a session's transcript under the directory it ran
    in, so neither the daemon's `--resume` nor the dashboard's can pick the old ones up from the new cwd, and a fresh
    start now beats a failed resume and a restart later."""
    if dry_run:
        print(f"would write the assistant bundle to {assistant.dir()}")
        return None
    if os.environ.get("FINNAMON_ASSISTANT"):
        print(f"warning: FINNAMON_ASSISTANT is set in this shell ({assistant.dir()}); the installed jobs do not see it and use {assistant.default_dir()}", file=sys.stderr)
    res = assistant.install()
    if res["first"]:
        print(f"wrote the assistant bundle to {res['dir']}")
    elif res["changed"]:
        print(f"updated the assistant bundle in {res['dir']}")
    # Claude Code ignores a directory's settings until the directory is trusted; the flag in ~/.claude.json is the fix its
    # own warning names, and the one thing Finnamon writes outside FINNAMON_HOME.
    t = assistant.trust()
    if t:
        print(f"  marked {res['dir']} trusted in {assistant.claude_config_path()}, so Claude Code applies the settings there")
    elif t is None:
        print(f"warning: could not mark {res['dir']} trusted in {assistant.claude_config_path()}; until it is, Claude Code ignores the "
              f"assistant's settings there ({assistant.TRUST_FIX})", file=sys.stderr)
    for b in res["backed_up"]:
        print(f"  this release changes a file you had edited; your copy is kept as {b}")
    for k in res["kept"]:
        print(f"  left your edited {k} alone (this release does not change it)")
    for o in res["orphaned"]:
        print(f"  left your edited {o} alone (this release drops the file; it is yours now, and no release will touch it again)")
    for r in res["removed"]:
        print(f"  removed {r} (no longer part of the bundle)")
    if res["first"] and config.db_path().exists():
        conn = store.connect()
        if store.get_state(conn, "session"):
            store.set_state(conn, "session", None)
        _retire_intercom_file()
        print("  the assistant's conversations start over: Claude Code keeps a session under the directory it ran in, and that is now this one")
    return res


def _warn_no_bun_for_channel(conn) -> None:
    """Channel mode runs the Telegram plugin on Bun inside the intercom session; without it the session shows the
    channel banner and nothing polls the bot, with no error anywhere. Say so where it can still be fixed."""
    if store.get_state(conn, "inbound") == "channel" and not shutil.which("bun", path=scheduler.env_path()):   # the jobs' PATH, not the terminal's
        print("warning: inbound is the Claude Code channel but `bun` is not on your PATH, so the Telegram plugin cannot start; "
              "install Bun (https://bun.sh), then run `finnamon install` again.", file=sys.stderr)


# --- budgets / settings / categories / suppressions -------------------------------------------

def cmd_budget(a) -> None:
    conn = store.connect()
    try:
        if a.action == "suggest":
            out(budgets.suggest(conn, a.months, a.as_of))
        elif a.action == "overall":
            out(budgets.overall(conn, a.as_of))
        elif a.action == "set":
            _triage_read_only()
            out(budgets.budget_set(conn, a.name, a.amount, a.category, a.merchants, a.fixed))
        elif a.action == "remove":
            _triage_read_only()
            out({"removed": budgets.budget_remove(conn, a.name)})
        else:
            out(budgets.budget_list(conn, a.as_of))
    except budgets.ResolveError as e:
        die(f"{e}. " + ("Candidates: " + ", ".join(e.candidates) if e.candidates else "Pick a code from `finnamon category list`."))
    except ValueError as e:
        die(str(e))


def cmd_threshold(a) -> None:
    _triage_read_only()
    conn = store.connect()
    try:
        out(budgets.threshold_set(conn, a.account, a.amount))
    except ValueError as e:
        die(str(e))


def cmd_settings(a) -> None:
    conn = store.connect()
    if a.action == "set":
        _triage_read_only()
        if a.ops and _from_claude():
            die("operational settings are changed by a person at a terminal, not from a Claude session")
        if a.value is None and not a.account:
            die("a value is required (per-account overrides can be unset with --account <acct> and no value)")
        try:
            store.set_setting(conn, a.key, a.value, a.account or "*", ops=a.ops)
        except ValueError as e:
            die(str(e))
        print("ok")
    elif a.action == "get":
        out({"key": a.key, "value": store.setting(conn, a.key, a.account)})
    else:
        out([dict(r) for r in store.settings_list(conn)])


def cmd_category(a) -> None:
    conn = store.connect()
    if a.tx is not None:
        if a.category or (a.clear and a.merchant) or not (a.clear or a.merchant) or (a.every and a.clear) or a.uncategorized or a.rules:
            die("usage: finnamon category --tx <transaction_id> <category> [--every] | --tx <transaction_id> --clear")
        _triage_read_only()
        try:
            out(budgets.tx_merchant_rule(conn, a.tx, a.merchant) if a.every else budgets.tx_category_set(conn, a.tx, None if a.clear else a.merchant))
        except (budgets.ResolveError, ValueError) as e:
            die(str(e))
        return
    if a.every:
        die("usage: finnamon category --tx <transaction_id> <category> --every (the rule for that charge's merchant)")
    if a.rules:
        if a.merchant or a.category or a.clear:
            die("usage: finnamon category --rules")
        out(budgets.category_rules(conn)); return
    if a.uncategorized:
        if a.merchant or a.category or a.clear or a.rules:
            die("usage: finnamon category --uncategorized")
        out(budgets.uncategorized(conn)); return
    if a.clear:
        if not a.merchant or a.category or a.merchant in ("list", "resolve"):
            die("usage: finnamon category <merchant> --clear")
        _triage_read_only()
        out(budgets.category_clear(conn, a.merchant)); return
    if a.merchant is None:
        die("usage: finnamon category <merchant> <category> | <merchant> --clear | --rules | --uncategorized | --tx <transaction_id> <category> [--every] | list [--json] | resolve <text>")
    if a.merchant == "list":
        if a.json:   # the dashboard's pickers: every category as people read it, and the merchants the household has seen
            out({"categories": [{"code": c, "label": taxonomy.label(c), "primary": p} for p, ds in taxonomy.DETAILED.items() for c in [p] + [f"{p}_{d}" for d in ds]],
                 "merchants": [r[0] for r in conn.execute("SELECT display FROM tx_now WHERE display IS NOT NULL GROUP BY lower(display) ORDER BY count(*) DESC LIMIT 500")]})
        else:
            print(taxonomy.listing())
        return
    if a.merchant == "resolve":
        code, cands = taxonomy.resolve(a.category); out({"code": code, "candidates": cands}); return
    _triage_read_only()
    try:
        out(budgets.category_set(conn, a.merchant, a.category))
    except (budgets.ResolveError, ValueError) as e:
        die(str(e))


def cmd_alias(a) -> None:
    conn = store.connect()
    if a.list:
        if a.name or a.remove is not None:
            die("usage: finnamon alias --list")
        out(budgets.alias_list(conn)); return
    _triage_read_only()
    try:
        if a.remove is not None:
            if a.name:
                die("usage: finnamon alias --remove <name>")
            out(budgets.alias_remove(conn, a.remove)); return
        if not a.name or not a.canonical:
            die('usage: finnamon alias "<raw name or pattern>" "<merchant>" | --list | --remove "<raw name or pattern>"')
        out(budgets.alias_set(conn, a.name, a.canonical))
    except ValueError as e:
        die(str(e))


def cmd_normal(a) -> None:
    conn = store.connect()
    if a.list:
        out([dict(r) for r in conn.execute("SELECT * FROM suppressions ORDER BY id DESC")]); return
    _triage_read_only()
    if a.remove is not None:
        try:
            out(budgets.normal_remove(conn, a.remove))
        except ValueError as e:
            die(str(e))
        return
    alert_id = a.alert
    if a.roundup_item:
        mid, n = a.roundup_item
        r = conn.execute("SELECT alert_id FROM roundup_items WHERE telegram_chat_id=? AND telegram_message_id=? AND n=?",
                         (str(store.get_state(conn, "chat_id")), mid, n)).fetchone()
        if not r:
            die("no such roundup item")
        alert_id = r[0]
    try:
        out(budgets.normal(conn, a.merchant, a.kind, a.account, a.max_amount, a.note, alert_id))
    except ValueError as e:
        die(str(e))


# --- alerts / triage / query / chart ---------------------------------------------------------------

def cmd_alerts(a) -> None:
    conn = store.connect()
    if a.untriaged:
        out(triage.groups(conn)); return
    if a.suppressed:
        out([{**dict(r), "payload": json.loads(r["payload_json"])} for r in triage.suppressed(conn, a.since)]); return
    if a.dismiss is not None or a.undo is not None:   # the page's Dismiss and Undo run these too: one path for the chat and the page
        _triage_read_only()
        try:
            out(budgets.dismiss(conn, a.dismiss) if a.dismiss is not None else budgets.undo(conn, a.undo))
        except ValueError as e:
            die(str(e))
        return
    where = f" AND (sent_at IS NOT NULL OR resolution IS NOT NULL OR {notify.SENDABLE})" if a.sent else ""   # delivered, queued, or resolved by a person before it went
    where += " AND resolved_at IS NULL" if a.open else " AND resolved_at IS NOT NULL" if a.resolved else ""
    rows = conn.execute(f"SELECT *, {store.ALERT_GROUP} AS grp FROM alerts WHERE created_at >= datetime('now','localtime', ?){where} "
                        f"ORDER BY {'resolved_at DESC, ' if a.resolved else ''}id DESC LIMIT ?", (f"-{a.since} days", a.limit)).fetchall()
    families: dict[str, list] = {}   # one transaction is one alert here too; the page's Dismiss on it resolves the rest
    for r in rows:
        families.setdefault(r["grp"], []).append(r)
    shown = [(notify.best(fam), fam) for fam in families.values()]
    out([{"id": r["id"], "kind": r["kind"], "tier": r["tier"], "transaction_id": r["transaction_id"], "created_at": r["created_at"], "sent_at": r["sent_at"],
          "telegram_message_id": r["telegram_message_id"], "verdict": r["verdict"], "confidence": r["confidence"], "reason": r["reason"],
          "resolved_at": r["resolved_at"], "resolution": r["resolution"], "suppression_id": r["suppression_id"],
          "folded": [f["id"] for f in fam if f["id"] != r["id"]],
          "text": notify.render(r), "payload": json.loads(r["payload_json"]),
          **({"reconnect": item, "page_text": notify.render(r, page=True)} if (item := notify.relogin_item(r)) else {})} for r, fam in shown])


STDIN_WAIT_S = 2   # a heredoc is ready at once; a forgotten one would block a Claude Code Bash tool forever


def cmd_triage(a) -> None:
    conn = store.connect()
    if a.action == "set":
        if a.reason != "-":   # a shell argument has already lost its $ signs ("$45.67" -> "5.67") by the time it gets here
            die("triage set takes the sentence on stdin only: end the command with - and a quoted heredoc (- <<'EOF' ... EOF)")
        try:   # never wait on a terminal or an idle socket
            ready = not sys.stdin.isatty() and bool(select.select([sys.stdin], [], [], STDIN_WAIT_S)[0])
        except (OSError, ValueError):   # a pipe that select cannot watch (Windows, an in-memory stream)
            ready = not sys.stdin.isatty()
        reason = (sys.stdin.buffer.read(4000).decode("utf-8", "replace") if hasattr(sys.stdin, "buffer") else sys.stdin.read(4000)).strip() if ready else ""
        if not reason:
            die("triage set -: the sentence comes on stdin, e.g. a quoted heredoc (<<'EOF')")
        n = triage.set_verdict(conn, a.group, a.verdict, a.confidence, reason, repair=not (_from_claude() or os.environ.get("FINNAMON_TRIAGE")))   # Claude reads untrusted memos: only a person rewrites a stamped sentence
        out({"stamped": n})
    elif a.action == "suppressed":
        out([{**dict(r), "payload": json.loads(r["payload_json"])} for r in triage.suppressed(conn, a.since)])
    else:
        _human_only("triage")   # it spawns the headless /triage claude
        out(triage.run_if_needed(conn))


def cmd_query(a) -> None:
    try:
        print(query.to_json(query.run(a.sql, a.limit)))
    except Exception as e:  # noqa: BLE001 - any SQL error is a user-facing message here
        die(str(e))


def cmd_chart(a) -> None:
    from . import charts
    if a.sql and not (a.spec_json or a.table_json):
        die("--sql goes with --spec-json or --table-json")
    try:   # the board modes print current.json, which the dashboard redraws from; nothing to send
        if a.refresh:   # the dashboard asks on every load: the board, recomputed now
            out(charts.refresh(write=False))
        elif a.list:
            out([{"id": e["usermeta"]["finnamon"]["id"], "title": e.get("title")} for e in charts.board()])
        elif a.clear:
            print(charts.clear())
        elif a.remove:
            print(charts.remove(a.remove))
        elif a.spec_json:   # no store.connect(): data.sql runs through query.run, read-only
            print(charts.put(charts.custom(sys.stdin.read() if a.spec_json == "-" else a.spec_json, a.id, a.sql)))
        elif a.table_json:
            print(charts.put(charts.custom_table(sys.stdin.read() if a.table_json == "-" else a.table_json, a.id, a.sql)))
        elif not a.name:
            die("which chart? a name, --spec-json, --table-json, --list, --remove <id> or --clear")
        elif a.spec:
            print(charts.write_spec(store.connect(), a.name, a.arg, a.months))
        else:
            print(charts.render(store.connect(), a.name, a.arg, a.months))
    except (ValueError, RuntimeError) as e:
        die(str(e))


def cmd_networth(a) -> None:
    conn = store.connect()
    if a.sync:
        _human_only("networth --sync")   # Plaid calls, like `sync`
        out(investments.sync_all_holdings(conn))
    out(investments.net_worth_history(conn, a.months) if a.history else investments.net_worth(conn))


def cmd_serve(a) -> None:
    from . import mcp_server
    mcp_server.main()


# --- parser ------------------------------------------------------------------------------------

# What `finnamon help <cmd>` adds to the human-only commands: what each does, what it needs, what comes next. The assistant
# relays it to a person who will type the command on the Finnamon box, so plain words, and the step after.
HUMAN_HELP = {
    "init": """First-time setup, interactive: Plaid keys, the Telegram bot, the dashboard, the schedule. Run it once on the Finnamon box.
Needs: a Plaid account (dashboard.plaid.com, Team Settings > Keys) and a bot from @BotFather (/newbot, then /setprivacy, Disable).
  finnamon init --plaid   replace the Plaid keys on file (Sandbox → Production); prompts without echo, skips the other steps
Next: finnamon link --start --owner <name> for each bank, then ask the assistant to set up budgets.""",
    "link": """Connect, re-login or unlink a bank. --start and --finish (and --update <item_id> --telegram) the assistant may run;
the rest are for a person in a terminal on the Finnamon box:
  finnamon link --remove <item_id>    unlink a bank: Plaid stops billing, its accounts and transactions are dropped (asks first; --yes skips)
  finnamon link --update <item_id>    log back into a bank and wait here until done (up to hours)
  (the dashboard's Reconnect runs --web --update <item_id>: it prints the URL and returns; the daemon syncs the bank once done)
  finnamon link [--owner <name>]      add a bank and wait here until done
The item_id is in `finnamon status`. --owner names whose login it is (add a new person first with finnamon owner add).
Next, after --remove: the bank's alerts stop; link it again with finnamon link --start --owner <name> if it was a re-link.""",
    "owner": """list: the household members. add <name>: a new member (person at a terminal on the Finnamon box only), as are
rename <name> <new name> (every account follows) and remove <name> (refused while they own an account). Names match
without regard to case; `joint` is reserved for shared accounts.
Needs a name, and either:
  --user-id <id>   in channel mode (finnamon channel status says "channel"): their Telegram user id. Have them message the bot,
                   then run /telegram:access in the channel's Claude session; the id is listed there.
  (no --user-id)   the code dance: it prints a code and waits. Add them to the household Telegram group (no group yet,
                   or --new-group: create one with you, them and the bot, make the bot an admin; it becomes the
                   household chat), then have them send exactly that code there. The bot's privacy mode must be off
                   (@BotFather: /setprivacy, Disable).
  --no-telegram    a member who doesn't chat with the bot (a spouse who only has banks and budgets here): no id yet.
                   Give them Telegram later with --user-id <id> or the code dance on the same name.
Next: link their banks with finnamon link --start --owner <name>.""",
    "channel": """status: who answers Telegram: daemon (its own `claude -p` session), session (the dashboard's intercom session, one
conversation shared with the dashboard), or channel (Claude Code's Telegram channel plugin).
on / session / off (a person at a terminal on the Finnamon box only): switch Telegram conversation to the channel plugin, to the
dashboard's session, or back to the daemon; the daemon stays the bot's only reader except under `on`. After a switch involving
session or channel, restart the dashboard (finnamon update --no-pull). `on` prints the setup steps (register the plugin with
finnamon install, the bot token in ~/.claude/channels/telegram/.env, pairing with /telegram:access).
Next, after on: add each member with finnamon owner add <name> --user-id <id>.""",
    "account": """merge, unmerge, failover and remove are for a person at a terminal on the Finnamon box (ids from finnamon account list):
  merge <new_id> <existing_id>   the same account seen through two logins (e.g. a joint account): count it once, as joint.
                                 Refused for different types or very different balances unless --force
  unmerge <id>                   stop treating <id> as a copy; both get their own owners back
  failover <id>                  <id> (the one the copy was merged into) has a broken login: count the copy from the healthy one instead
  remove "<name>" [--yes]        a manual account and its transactions (a Plaid bank: finnamon link --remove <item_id>)
type and add are for anyone, the assistant included; owner is a household change, for a person at a terminal:
  type <account_id> <type>       what the account really is, whatever the bank says (checking, savings, credit, loan,
                                 investment): a managed CMA Plaid sends as cash is an investment. Kept through every sync;
                                 `account list` shows the bank's (bank_type) and the household's (type_override)
  type <account_id> --clear      back to the bank's own type
  owner <account_id> <member>    whose account it is (a household member, or joint; a name that is not one is refused):
                                 the dashboard groups and labels by it
Net worth (cash / investments / debt), the dashboard and spending follow the type: an investment account's own rows are
not spending or income, as with any brokerage.
Next: finnamon networth or the dashboard shows the totals counted once.""",
    "sync": """Pull new transactions and balances from every linked bank now (--item <item_id>: one bank). The daemon already does
this on its schedule; run it by hand in a terminal on the Finnamon box after a re-login or to see a change sooner.
Next: new alerts go out with the daemon's next pass.""",
    "install": """(Re)install the schedule: the daemon and the dashboard as services, the assistant's directory, and (channel mode)
the Telegram plugin. Starts the household's Claude conversation fresh (asks first; --yes skips). --dry-run shows what it
would do; --uninstall removes the services. Run it in a terminal on the Finnamon box.
Next: finnamon status to check the daemon is running.""",
    "update": """Pull the latest Finnamon, migrate, rewrite the assistant's directory, and restart whatever changed, keeping the
household's Claude conversation. Run it in a terminal on the Finnamon box.
When the release changed web/package*.json it runs `npm ci` in web/ first; a household that skipped scheduling has nothing to restart.
--dry-run only looks: nothing is pulled, migrated or restarted.
Next: finnamon status; finnamon --version shows the version now running.""",
    "detect": """detect --review (a person at a terminal on the Finnamon box only): go through detector drafts the assistant saved with
--draft, see each one's SQL and how many rows it finds today, and answer y (move it to candidates/, whose alerts triage
checks first; --tier rules alerts directly), N (keep it pending) or d (delete it).
Next: an approved detector runs on the daemon's next pass.""",
}


def cmd_help(a) -> None:
    """`finnamon help [<command>]`: that command's usage, the human-only ones included. It only ever prints, and reads nothing
    after the command word, so the assistant's allow list can name `finnamon help *` while every deny pattern stays as it is
    (`finnamon owner add --help` matches `owner add*`, and deny beats allow)."""
    p = build_parser()
    if not a.topic:
        p.print_help(); return
    if a.topic[0] not in p.commands:
        die(f"no command {a.topic[0]!r}; one of: {', '.join(p.commands)}")
    p.commands[a.topic[0]].print_help()


def build_parser() -> argparse.ArgumentParser:
    # no prefix matching: `networth --syn` would be --sync to argparse but not to the assistant's deny list
    p = argparse.ArgumentParser(prog="finnamon", description="Your money. Your financial AI.", allow_abbrev=False)
    p.add_argument("--version", action="version", version=__version__)
    sp = p.add_subparsers(dest="cmd", required=True, parser_class=functools.partial(argparse.ArgumentParser, allow_abbrev=False))
    p.commands = sp.choices   # main() reparses one intermixed

    s = sp.add_parser("init", help="keys, bot, dashboard, schedule"); s.add_argument("--owner"); s.add_argument("--plaid", action="store_true", help="replace the Plaid keys on file (Sandbox → Production), prompting without echo; the other steps are skipped"); s.add_argument("--yes", action="store_true", help="accept every prompt, including npm install and the scheduler"); s.set_defaults(fn=cmd_init)
    s = sp.add_parser("link", help="connect a bank via Plaid Hosted Link"); s.add_argument("--owner"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_link)
    g = s.add_mutually_exclusive_group(); g.add_argument("--update", metavar="ITEM_ID"); g.add_argument("--remove", metavar="ITEM_ID", help="unlink a bank (Plaid stops billing; its data is dropped)")
    g.add_argument("--start", action="store_true", help="open a Link session and return; the daemon (or --finish) completes it"); g.add_argument("--finish", action="store_true", help="complete a --start session once the bank login is done")
    s.add_argument("--telegram", action="store_true", help="also send the Plaid link to the household chat (e.g. to open it on a phone)")
    g.add_argument("--token", nargs="?", const="", metavar="REDIRECT_URI", help="web dashboard: print a plain Plaid Link token (OAuth banks return to the HTTPS REDIRECT_URI when given, else use a popup)")
    s.add_argument("--public-token", metavar="PUBLIC_TOKEN", help="web dashboard: finish a Plaid Link session with its public token")
    s.add_argument("--web", action="store_true", help="web dashboard, with --update: print the re-login URL as JSON (a session still open is reused); the daemon syncs the bank once the login is done")
    s = sp.add_parser("owner"); s.add_argument("action", choices=["list", "add", "rename", "remove"]); s.add_argument("name", nargs="?"); s.add_argument("new_name", nargs="?"); s.add_argument("--user-id", type=int, help="Telegram user id (channel mode: no code dance)")
    s.add_argument("--no-telegram", action="store_true", help="a member who doesn't chat with the bot (add their Telegram id later with --user-id)")
    s.add_argument("--new-group", action="store_true", help="the code may come from a new group, which becomes the household chat"); s.set_defaults(fn=cmd_owner)
    s = sp.add_parser("channel", help="who answers Telegram: session (default: the dashboard's conversation), on (Claude Code's channel plugin) or off (legacy: the daemon's own session)",
                     description="session: the daemon polls Telegram and types each message into the dashboard's intercom session (the default for new installs, no plugin). on: Claude Code's Telegram channel plugin reads the chat. off: the legacy mode, the daemon answers with its own separate claude -p session."); s.add_argument("action", choices=["on", "session", "off", "status"]); s.set_defaults(fn=cmd_channel)
    s = sp.add_parser("hook", help="Claude Code hooks (stdin: the hook event JSON)"); s.add_argument("name", choices=["reply-guard", "browser-guard", "permission", "secret-guard"]); s.set_defaults(fn=cmd_hook)
    s = sp.add_parser("property", help="stated assets a bank doesn't report (house, car), counted into net worth"); s.add_argument("action", choices=["list", "set", "remove"], nargs="?", default="list"); s.add_argument("name", nargs="?"); s.add_argument("value", nargs="?"); s.set_defaults(fn=cmd_property)
    s = sp.add_parser("account", help="list | add \"<name>\" --institution <bank> (a manual account, fed by import) | remove \"<name>\" (a manual account and its transactions) | type <account_id> <type>|--clear | balance \"<name>\" <amount> (a manual account's balance now) | merge | unmerge | failover | owner"); s.add_argument("action", choices=["list", "add", "remove", "type", "kind", "balance", "merge", "unmerge", "failover", "owner"]); s.add_argument("new", nargs="?"); s.add_argument("existing", nargs="?")
    s.add_argument("--institution", help="add: the bank's name (default: the first word of the account name)"); s.add_argument("--type", choices=list(imports.KINDS), default="checking"); s.add_argument("--owner"); s.add_argument("--mask", help="last 4 digits"); s.add_argument("--yes", action="store_true", help="remove: don't ask"); s.add_argument("--clear", action="store_true", help="type: back to the bank's own type"); s.add_argument("--force", action="store_true", help="merge: even when the types or balances differ")
    s.add_argument("--to", metavar="URL", help="list: the accounts on the Finnamon box at this dashboard URL (FINNAMON_WEB_TOKEN set to its `finnamon web token`)"); s.set_defaults(fn=cmd_account)
    s = sp.add_parser("import", help="a bank's CSV export into a manual account; --browser <bank> fetches it through a browser you log into"); s.add_argument("account", nargs="?", help="the manual account (name or id); with --browser, the bank")
    s.add_argument("file", nargs="?", help="the CSV (- for stdin)"); s.add_argument("--balance", type=float, help="the account's balance now, when the file has no balance column")
    s.add_argument("--flip", action="store_true", help="the file shows money out as positive (some card exports); default: negative, as banks do"); s.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    s.add_argument("--browser", action="store_true", help="open a Claude session that drives a visible browser: you log in, it downloads and imports"); s.add_argument("--to", metavar="URL", help="on another computer: the Finnamon box's dashboard URL; the file (or the browser session's result) is uploaded there, with FINNAMON_WEB_TOKEN set to the box's `finnamon web token`"); s.set_defaults(fn=cmd_import)
    s = sp.add_parser("sync"); s.add_argument("--item"); s.set_defaults(fn=cmd_sync)
    s = sp.add_parser("detect"); s.add_argument("--as-of"); s.add_argument("--only", nargs="*"); s.add_argument("--sql", metavar="NAME", help="print the assembled query"); s.add_argument("--prelude", action="store_true", help="print _prelude.sql, the CTEs every detector selects from")
    s.add_argument("--review", action="store_true"); s.add_argument("--tier", choices=["rules", "candidates"], default="candidates")
    s.add_argument("--draft", metavar="FILE|-", help="save a detector draft to the household's detector-drafts/ (under FINNAMON_HOME)"); s.add_argument("--name"); s.set_defaults(fn=cmd_detect)
    s = sp.add_parser("notify"); s.add_argument("--list", action="store_true"); s.add_argument("--roundup", action="store_true"); s.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS); s.set_defaults(fn=cmd_notify)
    s = sp.add_parser("run"); s.add_argument("--no-sync", action="store_true"); s.add_argument("--no-triage", action="store_true"); s.add_argument("--no-notify", action="store_true"); s.set_defaults(fn=cmd_run)
    sp.add_parser("daemon").set_defaults(fn=cmd_daemon)
    sp.add_parser("heartbeat").set_defaults(fn=cmd_heartbeat)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    sp.add_parser("doctor", help="check each prerequisite and piece of setup, with the fix for each").set_defaults(fn=cmd_doctor)
    s = sp.add_parser("install", help="(re)install the scheduler"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--uninstall", action="store_true"); s.add_argument("--yes", action="store_true", help="do not ask before retiring the assistant's conversation"); s.add_argument("--force", action="store_true", help="register the Telegram channel plugin even outside ~/.finnamon/assistant"); s.set_defaults(fn=cmd_install)
    s = sp.add_parser("update", help="pull and restart what went stale, keeping the household's Claude session (install reprovisions and starts it fresh)")
    s.add_argument("--no-pull", action="store_true", help="adopt the tree as it stands, without pulling")
    s.add_argument("--all", action="store_true", help="restart the daemon and the dashboard whatever changed")
    s.add_argument("--check", action="store_true", help="fetch and print, as JSON, whether there is anything to pull (the dashboard's Update button); changes nothing")
    s.add_argument("--dry-run", action="store_true", help="look only: fetch, then name the commits to pull and the services that would restart; nothing is pulled, migrated, installed or restarted"); s.add_argument("--force", action="store_true", help="register the Telegram channel plugin even outside ~/.finnamon/assistant"); s.set_defaults(fn=cmd_update)

    s = sp.add_parser("budget"); s.add_argument("action", choices=["list", "suggest", "set", "remove", "overall"], nargs="?", default="list")
    s.add_argument("name", nargs="?"); s.add_argument("amount", nargs="?", type=float)
    s.add_argument("--category", action="append"); s.add_argument("--merchant", action="append", dest="merchants")   # each repeatable; given, they replace the budget's
    s.add_argument("--fixed", action=argparse.BooleanOptionalAction); s.add_argument("--months", type=int, default=6); s.add_argument("--as-of"); s.set_defaults(fn=cmd_budget)
    s = sp.add_parser("threshold"); s.add_argument("account"); s.add_argument("amount", type=float); s.set_defaults(fn=cmd_threshold)
    s = sp.add_parser("settings"); s.add_argument("action", choices=["list", "get", "set"], nargs="?", default="list"); s.add_argument("key", nargs="?"); s.add_argument("value", nargs="?"); s.add_argument("--account"); s.add_argument("--ops", action="store_true", help="operational keys (daemon interval, timeouts); not for Claude"); s.set_defaults(fn=cmd_settings)
    s = sp.add_parser("category", help="<merchant> <category> (a rule: every past and future charge) | --tx <transaction_id> <category> (one charge, a one-time edit; --clear undoes it) | list | resolve <text>")
    s.add_argument("merchant", nargs="?"); s.add_argument("category", nargs="?"); s.add_argument("--tx", metavar="TRANSACTION_ID"); s.add_argument("--clear", action="store_true"); s.add_argument("--rules", action="store_true")
    s.add_argument("--every", action="store_true", help="with --tx: the rule for that charge's merchant (every charge), not the one charge")
    s.add_argument("--uncategorized", action="store_true", help="transactions with no category, by merchant"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_category)
    s = sp.add_parser("alias", help="raw bank string -> canonical merchant; a %% makes it a pattern (%% any run of characters, then _ one; every match becomes one merchant) | --list | --remove <name>")
    s.add_argument("name", nargs="?"); s.add_argument("canonical", nargs="?"); s.add_argument("--list", action="store_true"); s.add_argument("--remove", metavar="NAME"); s.set_defaults(fn=cmd_alias)
    s = sp.add_parser("normal", help="suppress a pattern. No --kind = every alert kind except recurring_changed (a merchant charging is normal; its subscription "
                     "stopping is still news). --alert on a recurring_changed or recurring_price alert acknowledges that one stream (a price: up to the accepted one). --list shows ids; --remove ID deletes one"); s.add_argument("merchant", nargs="?"); s.add_argument("--kind"); s.add_argument("--account"); s.add_argument("--max-amount", type=float)
    s.add_argument("--note"); s.add_argument("--alert", type=int); s.add_argument("--roundup-item", nargs=2, type=int, metavar=("MESSAGE_ID", "N")); s.add_argument("--list", action="store_true"); s.add_argument("--remove", type=int, metavar="ID", help="delete the rule with this id (from --list)"); s.set_defaults(fn=cmd_normal)

    s = sp.add_parser("alerts"); g = s.add_mutually_exclusive_group(); g.add_argument("--untriaged", action="store_true"); g.add_argument("--suppressed", action="store_true"); s.add_argument("--sent", action="store_true", help="only alerts sent to the household, or queued to be"); s.add_argument("--since", type=int, default=30); s.add_argument("--limit", type=int, default=50)
    g.add_argument("--open", action="store_true", help="only alerts not yet resolved"); g.add_argument("--resolved", action="store_true", help="only resolved ones, latest first")
    g.add_argument("--dismiss", type=int, metavar="ID", help="resolve this alert with no rule: fine this once, the next one still alerts")
    g.add_argument("--undo", type=int, metavar="ID", help="reopen an alert resolved by --dismiss or `normal --alert`; the rule that one wrote is removed")
    s.set_defaults(fn=cmd_alerts)
    s = sp.add_parser("triage"); s.add_argument("action", choices=["run", "set", "suppressed"], nargs="?", default="run"); s.add_argument("group", nargs="?"); s.add_argument("verdict", nargs="?", choices=["promote", "suppress"])
    s.add_argument("confidence", nargs="?", choices=["high", "low"]); s.add_argument("reason", nargs="?", help="- : the sentence on stdin"); s.add_argument("--since", type=int, default=30); s.set_defaults(fn=cmd_triage)
    s = sp.add_parser("query"); s.add_argument("sql"); s.add_argument("--limit", type=int, default=200); s.set_defaults(fn=cmd_query)
    s = sp.add_parser("chart"); s.add_argument("name", nargs="?"); s.add_argument("arg", nargs="?"); s.add_argument("--months", type=int, default=12)
    s.add_argument("--spec", action="store_true", help="add or update this preset on the web dashboard instead of writing a PNG")
    s.add_argument("--spec-json", metavar="SPEC", help="add or update a whole Vega-Lite spec, given inline or as - for stdin; data.sql runs read-only into data.values")
    s.add_argument("--table-json", metavar="SPEC", help='add or update a table panel: {"title", "columns": [{field, label, format: text|money|date|number, align}]}, rows from --sql')
    s.add_argument("--sql", help="the spec's top-level data.sql, given apart from the JSON")
    s.add_argument("--id", help="the --spec-json entry's id (default: from its title); the same id replaces that chart in place")
    s.add_argument("--remove", metavar="ID", help="take a chart off the dashboard"); s.add_argument("--clear", action="store_true", help="empty the dashboard's charts")
    s.add_argument("--list", action="store_true", help="the dashboard's charts, in order: id and title")
    s.add_argument("--refresh", action="store_true", help="recompute every chart from its query and print the board (JSON)")
    s.set_defaults(fn=cmd_chart)
    s = sp.add_parser("networth"); s.add_argument("--sync", action="store_true"); s.add_argument("--history", action="store_true"); s.add_argument("--months", type=int, default=12); s.set_defaults(fn=cmd_networth)
    sp.add_parser("serve", help="MCP server (stdio)").set_defaults(fn=cmd_serve)
    s = sp.add_parser("open", help="open the dashboard in a browser: the address carries its key once"); s.add_argument("--print", action="store_true", help="print the address instead of opening it")
    s.add_argument("--host", metavar="NAME", help="the address for a phone: the name `finnamon remote` serves the page on (printed, https), or a whole http(s) origin"); s.set_defaults(fn=cmd_open)
    s = sp.add_parser("remote", help=f"serve the dashboard to your phone over Tailscale (`tailscale serve --bg {config.DASHBOARD_PORT}`, never Funnel), let its name in, and show a one-time QR code that logs a phone in")
    s.add_argument("--off", action="store_true", help="stop letting the remote names in (~/.finnamon/web-hosts) and stop Tailscale serving the dashboard's port")
    s.add_argument("--dry-run", action="store_true", help="say what would change, change nothing"); s.set_defaults(fn=cmd_remote)
    s = sp.add_parser("web", help="the dashboard's key: `web token` prints it, `--rotate` replaces it"); s.add_argument("action", choices=["token"]); s.add_argument("--rotate", action="store_true", help="mint a new key; every open page is locked out until `finnamon open`"); s.set_defaults(fn=cmd_web)
    s = sp.add_parser("demo", help="try Finnamon on a made-up household (no Plaid or Telegram): its own FINNAMON_HOME (~/.finnamon-demo) and the dashboard on a spare port")
    g = s.add_mutually_exclusive_group(); g.add_argument("--stop", action="store_true", help="stop the demo dashboard"); g.add_argument("--reset", action="store_true", help="delete the demo household and make it again")
    s.add_argument("--port", type=int, help=f"the dashboard's port (default: the first free one from {demo.FIRST_PORT})"); s.add_argument("--print", action="store_true", help="print the address instead of opening it"); s.set_defaults(fn=cmd_demo)
    s = sp.add_parser("voice", help="Talk to Finnamon: `voice setup` installs whisper.cpp and a speech model (local, free); `voice status`"); s.add_argument("action", choices=["setup", "status"]); s.add_argument("--yes", action="store_true", help="install and download without asking (base.en model)"); s.set_defaults(fn=cmd_voice)
    sp.add_parser("help", help="a command's usage: finnamon help <command>").add_argument("topic", nargs=argparse.REMAINDER)
    sp.choices["help"].set_defaults(fn=cmd_help)
    for name, text in HUMAN_HELP.items():
        sp.choices[name].epilog, sp.choices[name].formatter_class = text, argparse.RawDescriptionHelpFormatter
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = build_parser()
    a, extra = p.parse_known_args(argv)
    if extra and a.cmd != "help":   # `help --x` leaves --x over, which the reparse can't take (REMAINDER); help ignores it
        # Before Python 3.14, argparse orphans a positional that follows a flag once an earlier positional stood
        # alone: `account add --institution HSBC -- "HSBC Checking"` (the dashboard's argv) lost the name.
        argv = sys.argv[1:] if argv is None else argv
        i = argv.index(a.cmd)
        if i:   # nothing legitimately precedes the subcommand (--version and -h exit), so the head is junk the reparse would drop
            p.error("unrecognized arguments: " + " ".join(argv[:i]))
        a = p.commands[a.cmd].parse_intermixed_args(argv[i + 1:], argparse.Namespace(cmd=a.cmd))
    return a


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=os.environ.get("FINNAMON_LOG", "WARNING"))
    a = parse_args(argv)
    if os.environ.get("FINNAMON_IMPORT_SESSION") and not (a.cmd == "import" or (a.cmd == "account" and a.action == "list") or a.cmd == "hook"):
        die("the browser-import session may only import and list accounts")   # its input is bank pages: no ledger reads, no other writes, whatever the pattern lists say
    a.fn(a)
    if _changes_charts(a):
        from . import charts
        charts.follow()


# Commands that can change what a board chart shows: new rows (sync, import, link, networth --sync), how rows classify
# (category, alias, account add/remove/merge), budgets, and what is suppressed or resolved. Their reads (`account list`, `budget`,
# which the dashboard's summary runs on every load) do not.
CHART_INPUTS = {"sync", "import", "link", "category", "alias", "normal", "account", "budget"}
CHART_READS = {("category", "list"), ("category", "resolve"), ("account", "list"), ("budget", "list"), ("budget", "suggest"), ("budget", "overall")}


def _changes_charts(a) -> bool:
    if os.environ.get("FINNAMON_TRIAGE"):   # read-only, current.json included
        return False
    if (a.cmd, getattr(a, "merchant", None) or getattr(a, "action", None)) in CHART_READS or (a.cmd == "normal" and a.list) or (a.cmd == "import" and a.dry_run) \
            or (a.cmd == "alias" and a.list) or (a.cmd == "category" and (a.rules or a.uncategorized)):
        return False
    return a.cmd in CHART_INPUTS or (a.cmd == "alerts" and (a.dismiss or a.undo)) or (a.cmd == "networth" and a.sync)


if __name__ == "__main__":
    main()
