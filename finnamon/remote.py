"""`finnamon remote`: the dashboard on the household's phones over Tailscale, in one step. `tailscale serve --bg <port>`
puts the page at https://<machine>.<tailnet>.ts.net (tailscaled keeps it across reboots), and that name goes into
~/.finnamon/web-hosts, which web/server.js reads on every request: its Host and Origin checks let that name in, nothing
else. Installs nothing and never logs in; never Funnel (the public internet), and refuses while Funnel is on; never takes
over an HTTPS port that serves something else: with 443 taken it uses the next free one Tailscale Serve allows (8443, 10000). The page still needs its key on every device
(`finnamon open --host <name>`). `--off` takes the names back out and stops Tailscale serving the port. Modelled on agent-007's `install --remote`
(server/remote-setup.js, server/tailscale.js)."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import tempfile
import time
import platform
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import config, qr

MAC_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"   # the macOS app's CLI, on PATH only if the owner added it
TS_TIMEOUT_S = 15
HTTPS_PORTS = ("443", "8443", "10000")   # the only HTTPS ports Tailscale Serve accepts, in the order we try them
PAIR_TTL_S = 300   # a pairing code's life: long enough to find the phone, short enough that a photo of the screen is useless later
SERVE_TIMEOUT_S = 300   # `tailscale serve` waits for the owner to enable Serve in the admin console; past this it gives up
# The installed dashboard's port. Not the shell's PORT: the launchd/systemd units never carry it, so a PORT exported for
# some dev server would put that server on the tailnet instead.
PORT = config.DASHBOARD_PORT


def dashboard_answers(port: int = PORT) -> bool:
    """Is it the dashboard on that port? Its every answer, a 401 included, names `finnamon open` (cli._dashboard_port_problem
    asks the same). 8888 is also Jupyter's: serving whatever else listens there would hand it to the whole tailnet."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/"); return "finnamon open" in c.getresponse().read(4000).decode("utf-8", "replace")
    except (OSError, http.client.HTTPException):
        return False


def tailscale_bin() -> str | None:
    return shutil.which("tailscale") or (MAC_APP_CLI if platform.system() == "Darwin" and Path(MAC_APP_CLI).exists() else None)


def ts(bin_: str, *args: str) -> tuple[int, str, str]:
    try:
        r = subprocess.run([bin_, *args], capture_output=True, text=True, timeout=TS_TIMEOUT_S)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def _json(s: str) -> dict | None:
    try:
        v = json.loads(s)
        return {} if v is None else v if isinstance(v, dict) else None   # a node that never served anything prints `null`
    except ValueError:
        return None


def norm(h: str) -> str:
    """Lowercase, :443 dropped (web/server.js norm() does the same for the names it is given, and :80 too)."""
    return re.sub(r":443$", "", h.strip().lower())


def serve_targets(cfg: dict, port: int) -> dict:
    """From `tailscale serve status --json`. names: hosts (host:port off 443) the background config (`serve --bg`, kept by
    tailscaled) sends to this port; temporary: those only a foreground `tailscale serve` in some terminal sends there;
    targets: what else 443 serves; busy: HTTPS ports in use for something else; funnel: our names Funnel opens to the internet."""
    proxy = re.compile(rf"^https?://(localhost|127\.0\.0\.1|\[::1\]):{port}(/|$)")
    configs = [cfg, *((cfg.get("Foreground") or {}).values())]
    ours = lambda web: any(proxy.match(str((h or {}).get("Proxy") or "")) for h in ((web or {}).get("Handlers") or {}).values())
    entries = lambda c: ((c or {}).get("Web") or {}).items()
    names = {norm(hp) for hp, web in entries(cfg) if ours(web)}
    temporary = {norm(hp) for c in configs for hp, web in entries(c) if ours(web)} - names
    others = [(hp, web) for c in configs for hp, web in entries(c) if not ours(web)]
    port_of = lambda hp: (re.search(r":(\d+)$", hp) or [None, "443"])[1]
    by_port: dict[str, set] = {}
    for hp, web in others:
        by_port.setdefault(port_of(hp), set()).update(str(h.get("Proxy") or h.get("Path") or ("text" if h.get("Text") else ""))
                                                      for h in ((web or {}).get("Handlers") or {}).values() if h)
    targets = sorted(by_port.get("443", set()) - {""})
    busy = {port_of(hp) for hp, _ in others} | {p for c in configs for p in ((c or {}).get("TCP") or {})
                                                   if ((c or {}).get("TCP") or {}).get(p, {}).get("TCPForward")}   # a raw TCP forwarder on 443 is just as taken
    funnel = {norm(hp) for c in configs for hp, on in ((c or {}).get("AllowFunnel") or {}).items() if on} & (names | temporary)
    return {"names": names, "temporary": temporary, "targets": targets, "by_port": by_port, "busy": busy, "funnel": funnel}


def funnel_off(cli: str, names) -> str:
    """Turn Funnel off for those names' ports only; `funnel reset` would drop every other thing serve puts on the tailnet too."""
    return " && ".join(f"{cli} funnel --https={(n.split(':')[1] if ':' in n else '443')} off" for n in sorted(names))


def hosts() -> list[str]:
    try:
        return [norm(h) for h in re.split(r"[\s,]+", config.web_hosts_path().read_text()) if h.strip()]
    except OSError:
        return []


def add_host(name: str) -> bool:
    """Merge `name` into web-hosts, never removing one; False when it was already there."""
    have = hosts()
    if norm(name) in have:
        return False
    p = config.web_hosts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{h}\n" for h in [*have, norm(name)]))
    return True


def pair_url(name: str) -> str:
    """A fresh one-time address for a phone, replacing any code not yet used. Only the code's sha256 is kept (0600, written
    atomically); web/server.js redeems it once, before it expires, on a tailnet name, and swaps it for the key's cookie."""
    code = secrets.token_urlsafe(16)
    p = config.home() / config.WEB_PAIR_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".web-pair.")   # 0600
    with os.fdopen(fd, "w") as f:
        json.dump({"sha256": hashlib.sha256(code.encode()).hexdigest(), "expires": time.time() + PAIR_TTL_S}, f)
    os.replace(tmp, p)
    return f"https://{name}/?pair={code}"


def show_pairing(name: str) -> None:
    url = pair_url(name)
    print(f"\nScan this with the phone's camera to open the dashboard there, logged in (one phone, within {PAIR_TTL_S // 60} minutes):\n")
    print(qr.render(url))
    print(f"\nNo camera? Open this on the phone instead (same rules; never send it through the household chat):\n  {url}\n"
          "Another phone: run `finnamon remote` again for a fresh code.")


def stream(argv: list[str], timeout: float = SERVE_TIMEOUT_S) -> tuple[int | None, str]:
    """Run, echoing output as it arrives (the "enable Serve" link must show while tailscale waits on it). None = timed out."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out: list[str] = []

    def pump():
        for line in p.stdout:
            out.append(line); sys.stdout.write(line); sys.stdout.flush()
    t = threading.Thread(target=pump, daemon=True); t.start()
    try:
        code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(); code = None
    except KeyboardInterrupt:
        p.kill(); p.wait(); raise
    t.join(1)
    return code, "".join(out)


def off(dry_run: bool = False) -> int:
    """Stop letting the remote names in, and stop Tailscale serving the dashboard's port: left on, it would hand whatever
    binds 8888 next (Jupyter's default too) to the tailnet. Only HTTPS ports that proxy to the dashboard are turned off."""
    path, have = config.web_hosts_path(), hosts()
    if not have:
        print(f"{path} lets no remote name in.")
    elif dry_run:
        print(f"Would remove {path} ({', '.join(have)}).")
    else:
        path.unlink(missing_ok=True)
        print(f"✓ Removed {', '.join(have)} from the dashboard's allowed names; from the next request it answers to localhost only "
              "(a FINNAMON_WEB_HOSTS from an older setup stays until you unset it and run `finnamon install`).")
    bin_ = tailscale_bin()
    if not bin_:
        return 0
    code, out, _ = ts(bin_, "serve", "status", "--json")
    cfg = _json(out) if code == 0 else None
    if cfg is None:
        print(f"Could not read `tailscale serve status`; to stop serving the dashboard: {bin_} serve --https=443 off")
        return 1
    st = serve_targets(cfg, PORT)
    for h in sorted(st["names"]):
        cmd = ["serve", f"--https={h.split(':')[1] if ':' in h else '443'}", "off"]
        if dry_run:
            print(f"Would run: {bin_} {' '.join(cmd)}")
            continue
        code, out, e = ts(bin_, *cmd)
        if code:
            print(f"✗ `{bin_} {' '.join(cmd)}` failed ({((e or out).strip().splitlines() or [f'exit {code}'])[-1]}); run it yourself.", file=sys.stderr)
            return 1
        print(f"✓ Tailscale no longer serves https://{h}.")
    if st["temporary"]:
        print(f"! A foreground `tailscale serve` still sends {', '.join(sorted(st['temporary']))} to port {PORT}; stop it in its terminal (Ctrl-C).")
    return 0


def setup(dry_run: bool = False) -> int:
    """0 once https://<name> reaches the dashboard and the dashboard lets it in."""
    n = PORT
    err = lambda m: print(m, file=sys.stderr)
    print(f"Setting up remote access to the dashboard over Tailscale (port {n}).")
    bin_ = tailscale_bin()
    if not bin_:
        err(f"✗ Tailscale is not installed (no tailscale on PATH{' and no /Applications/Tailscale.app' if platform.system() == 'Darwin' else ''}). Nothing was changed.\n"
            "  Install it from https://tailscale.com/download, log in, then run `finnamon remote` again.")
        return 1
    cli = f'"{bin_}"' if " " in bin_ else bin_
    code, out, e = ts(bin_, "status", "--json")
    status = _json(out) or {}
    if status.get("BackendState") != "Running":
        why = f"Tailscale is {status['BackendState']}" if status.get("BackendState") else f"`tailscale status` failed ({((e or out).strip().splitlines() or [f'exit {code}'])[-1]})"
        err(f"✗ {why}, so this machine is not on your tailnet. Nothing was changed.\n"
            f"  {'Open the Tailscale app and log in, or run: ' if platform.system() == 'Darwin' else 'Run: '}{cli} up\n  Then run `finnamon remote` again.")
        return 1
    name = norm(str((status.get("Self") or {}).get("DNSName") or "").rstrip("."))
    if not name:
        err("✗ Tailscale did not report this machine's ts.net name (turn on MagicDNS: https://login.tailscale.com/admin/dns). Nothing was changed.")
        return 1
    print(f"This machine is {name}.")
    if not dashboard_answers(n):
        err(f"✗ The dashboard does not answer on 127.0.0.1:{n} (nothing does, or another program, Jupyter's default is 8888 too). "
            f"Serving it would put that on your tailnet. Nothing was changed.\n  Start the dashboard (finnamon install; `finnamon doctor` says what is wrong), then run `finnamon remote` again.")
        return 1

    def serve_status():
        code, out, e = ts(bin_, "serve", "status", "--json")
        cfg = _json(out) if code == 0 else None
        if cfg is None:   # unknown is not "nothing served": it could be serving something we would take over
            err(f"✗ Could not read `tailscale serve status` ({((e or out).strip().splitlines() or [f'exit {code}'])[-1]}). Nothing was changed.")
        return None if cfg is None else serve_targets(cfg, n)

    if (st := serve_status()) is None:
        return 1
    if st["funnel"]:
        err(f"✗ Funnel is on for {', '.join(sorted(st['funnel']))}: the dashboard is on the public internet. Nothing was changed.\n"
            f"  Turn it off: {funnel_off(cli, st['funnel'])}, then run `finnamon remote` again.")
        return 1
    https = next((p for p in HTTPS_PORTS if p not in st["busy"]), None)   # 443 when free, else the next free one
    serve = f"{cli} serve --bg {n}" if https == "443" else f"{cli} serve --bg --https={https} {n}"
    if st["names"]:
        print(f"✓ tailscale serve already sends {', '.join(f'https://{h}' for h in sorted(st['names']))} to port {n}; keeping it.")
    else:
        if st["temporary"]:
            print(f"! Only a foreground `tailscale serve` sends {', '.join(sorted(st['temporary']))} to port {n}; it ends with its terminal, so serving it in the background.")
        if https is None:
            used = "; ".join(f"{p}: {', '.join(sorted(st['by_port'].get(p, set()) - {''})) or 'something else'}" for p in HTTPS_PORTS)
            err(f"✗ Every HTTPS port Tailscale Serve allows ({', '.join(HTTPS_PORTS)}) is in use ({used}). Nothing was changed.\n"
                f"  Free one with `{cli} serve --https=<port> off`, then run `finnamon remote` again.")
            return 1
        if https != "443":
            print(f"! HTTPS port 443 already serves {', '.join(st['targets']) or 'something else'}; leaving it, and serving the dashboard on port {https} instead.")
        if dry_run:
            print(f"Would run: {serve}")
        else:
            print(f"Running: {serve}\nIf Serve isn't enabled on your tailnet yet, Tailscale prints a link to enable it; open it and this continues "
                  f"(waiting up to {SERVE_TIMEOUT_S // 60} min, Ctrl-C to stop).")
            try:
                code, out = stream([bin_, "serve", "--bg", *([f"--https={https}"] if https != "443" else []), str(n)])
            except KeyboardInterrupt:
                err("Stopped. Nothing else was changed. Run `finnamon remote` again when Serve is enabled.")
                return 130
            enable = (re.search(r"https://login\.tailscale\.com/\S+", out) or [None])[0]
            if code is None:
                err(f"✗ Gave up waiting for Serve to be enabled after {SERVE_TIMEOUT_S // 60} min.{f' Enable it here: {enable}' if enable else ''}\n  Then run `finnamon remote` again.")
                return 1
            if code:
                last = (out.strip().splitlines() or [f"exit {code}"])[-1]
                hint = (f" Let your user run it: sudo {cli} set --operator=$USER, then `finnamon remote` again."
                        if platform.system() == "Linux" and re.search(r"denied|operator|permission", out, re.I) else f" Run it yourself: {serve}")
                err(f"✗ Serve is not enabled on your tailnet. Enable it here, then run `finnamon remote` again:\n  {enable}" if enable
                    else f"✗ tailscale serve failed ({last}).{hint}")
                return 1
            if (st := serve_status()) is None:   # what tailscale now does, not what it said: the name, and never Funnel
                return 1
            if st["funnel"]:
                err(f"✗ Funnel is on for {', '.join(sorted(st['funnel']))}: the dashboard is on the public internet.\n"
                    f"  Turn it off: {funnel_off(cli, st['funnel'])}, then run `finnamon remote` again.")
                return 1
            if not st["names"]:
                err(f"✗ tailscale serve exited cleanly but does not send anything to port {n}. Run `{cli} serve status` to see what it serves.")
                return 1
            print(f"✓ tailscale serve sends {', '.join(f'https://{h}' for h in sorted(st['names']))} to port {n}.")

    path = config.web_hosts_path()
    names = sorted(st["names"]) or [name]   # every name it is served on: doctor checks each, and the phone may use any
    new = [h for h in names if h not in hosts()]
    if not new:
        print(f"✓ {path} already lets {', '.join(names)} in.")
    elif dry_run:
        print(f"Would add {', '.join(new)} to {path} (the dashboard's allowed Host/Origin names; read on every request, no restart).")
    else:
        for h in new:
            add_host(h)
        print(f"✓ Added {', '.join(new)} to {path}; the dashboard lets it in from the next request.")
    if not dry_run:
        show_pairing(names[0])
    return 0


def doctor_line() -> tuple[bool | None, str, str]:
    """(ok, detail, fix) for `finnamon doctor`'s Remote access line; ok None = off or unknown right now, which is not a failure."""
    have, n = hosts(), PORT
    OFF = (None, "off (optional: `finnamon remote` serves the dashboard to your phone over Tailscale)", "")
    turned_off = "finnamon remote, or `finnamon remote --off` if you turned it off"
    bin_ = tailscale_bin()
    if not bin_:
        return (False, f"{', '.join(have)} let in, but Tailscale is not installed", f"install Tailscale and log in; {turned_off}") if have else OFF
    code, out, _ = ts(bin_, "serve", "status", "--json")
    cfg = _json(out) if code == 0 else None
    if cfg is None:   # the app quit or logged out for now: worth saying, not a broken setup
        return (None, f"`tailscale serve status` failed, so {', '.join(have)} cannot be checked (is Tailscale running and logged in?)", "") if have else OFF
    st = serve_targets(cfg, n)
    if st["funnel"]:
        return False, f"Funnel puts {', '.join(sorted(st['funnel']))} on the public internet", f"{funnel_off(bin_, st['funnel'])}, then finnamon remote"
    if not st["names"]:
        return (False, f"{', '.join(have)} let in, but tailscale serve does not send it to port {n}", turned_off) if have else OFF
    missing = sorted(st["names"] - set(have))
    if missing:   # the phone gets 421, nothing is exposed: after `--off`, or a setup from before web-hosts (FINNAMON_WEB_HOSTS in the unit)
        return None, f"tailscale serve sends https://{missing[0]} to port {n}, but web-hosts does not list that name", \
            "optional: `finnamon remote` lets your phone in through that mapping; ignore this if the mapping is not Finnamon's"
    if not dashboard_answers(n):
        return False, f"tailscale serve sends {', '.join(f'https://{h}' for h in sorted(st['names']))} to port {n}, but the dashboard does not answer there", \
            f"start the dashboard (finnamon install), or `finnamon remote --off`: whatever else listens on {n} is on your tailnet"
    return True, ", ".join(f"https://{h}" for h in sorted(st["names"])) + f" → port {n}", ""
