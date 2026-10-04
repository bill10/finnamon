"""`finnamon remote` and its doctor line, against a fake tailscale (a script in a temp dir); never the real one."""
import json
import stat

from pathlib import Path

import pytest

from finnamon import cli, config, remote
from tests.conftest import REAL_TAILSCALE_BIN

REAL_DASHBOARD_ANSWERS = remote.dashboard_answers

NAME = "mini.tail1.ts.net"
FAKE = r"""#!/bin/sh
d=$(dirname "$0")
echo "$@" >> "$d/calls"
case "$*" in
  "status --json") cat "$d/status.json" ;;
  "serve status --json") [ -f "$d/serve-fails" ] && exit 1; cat "$d/serve.json" 2>/dev/null || echo 'null' ;;   # a fresh node prints null
  "serve --https="*" off") [ -f "$d/serve-off-fails" ] && { echo "serve: permission denied"; exit 1; }
    python3 - "$d" "${2#--https=}" <<'PY'
import json, os, sys
d, hp = sys.argv[1:3]
cfg = json.load(open(d + "/serve.json"))
cfg["Web"] = {k: v for k, v in cfg["Web"].items() if not (k.endswith(":" + hp) or (hp == "443" and ":" not in k))}
json.dump(cfg, open(d + "/serve.json", "w")) if cfg["Web"] else os.remove(d + "/serve.json")
PY
    ;;
  "serve --bg "*)
    if [ -f "$d/fail" ]; then cat "$d/fail"; exit 1; fi
    if [ -f "$d/hang" ]; then exec sleep 10; fi
    if [ -f "$d/needs-enable" ]; then echo "Serve is not enabled on your tailnet."; echo "To enable, visit: https://login.tailscale.com/f/serve?node=abc"; exit 1; fi
    case "$3" in --https=*) hp="${3#--https=}"; tgt=$4 ;; *) hp=443; tgt=$3 ;; esac
    # keeps what is already served (ports other than ours), as tailscale does
    python3 - "$d" "$(cat "$d/name")" "$hp" "$tgt" "$(cat "$d/after" 2>/dev/null)" <<'PY'
import json, os, sys
d, name, hp, tgt, after = sys.argv[1:6]
cfg = json.load(open(d + "/serve.json")) if os.path.exists(d + "/serve.json") else {}
cfg = cfg or {}
cfg.setdefault("Web", {})[f"{name}:{hp}"] = {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{tgt}"}}}
cfg.update(json.loads("{" + after.lstrip(",") + "}") if after else {})
json.dump(cfg, open(d + "/serve.json", "w"))
PY
    echo "Available within your tailnet: https://$(cat "$d/name")/" ;;
  *) exit 2 ;;
esac
"""


def serve_json(port, name=NAME, funnel=False):
    return {"Web": {f"{name}:443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}}} | ({"AllowFunnel": {f"{name}:443": True}} if funnel else {})


@pytest.fixture
def ts(tmp_path, monkeypatch, home):
    d = tmp_path / "ts"; d.mkdir()
    b = d / "tailscale"; b.write_text(FAKE); b.chmod(b.stat().st_mode | stat.S_IEXEC)
    (d / "status.json").write_text(json.dumps({"BackendState": "Running", "Self": {"DNSName": NAME + "."}}))
    (d / "name").write_text(NAME)
    monkeypatch.setattr(remote, "tailscale_bin", lambda: str(b))
    monkeypatch.setattr(remote, "dashboard_answers", lambda port=8888: True)   # no dashboard runs under the tests; its probe has its own test
    return d


def calls(d):
    return (d / "calls").read_text().splitlines() if (d / "calls").exists() else []


def test_serves_the_port_lets_the_name_in_and_is_idempotent(ts, capsys):
    cli.main(["remote"])
    out = capsys.readouterr().out
    assert "serve --bg 8888" in calls(ts) and f"Available within your tailnet: https://{NAME}/" in out   # streamed through
    assert remote.hosts() == [NAME] and f"https://{NAME}/?pair=" in out and "\u2580" in out   # the QR, and its address as text
    first = json.loads(config.home().joinpath(config.WEB_PAIR_FILE).read_text())
    cli.main(["remote"])   # set up already: straight to a fresh code for the next phone
    out = capsys.readouterr().out
    assert calls(ts).count("serve --bg 8888") == 1 and "already sends" in out and "already lets" in out and "?pair=" in out
    assert json.loads(config.home().joinpath(config.WEB_PAIR_FILE).read_text())["sha256"] != first["sha256"]
    assert config.web_hosts_path().read_text() == NAME + "\n"


def test_dry_run_changes_nothing(ts, capsys):
    cli.main(["remote", "--dry-run"])
    out = capsys.readouterr().out
    assert "Would run:" in out and "Would add" in out and not config.web_hosts_path().exists()
    assert not any(c.startswith("serve --bg") for c in calls(ts))


def test_stops_when_tailscale_is_missing_or_logged_out(ts, monkeypatch, capsys):
    (ts / "status.json").write_text(json.dumps({"BackendState": "NeedsLogin"}))
    with pytest.raises(SystemExit):
        cli.main(["remote"])
    assert "Tailscale is NeedsLogin" in capsys.readouterr().err and not config.web_hosts_path().exists()
    monkeypatch.setattr(remote, "tailscale_bin", lambda: None)
    with pytest.raises(SystemExit):
        cli.main(["remote"])
    assert "not installed" in capsys.readouterr().err
    # Value: protects=no ts.net name (MagicDNS off) stops before serve or web-hosts; fails_when=an empty name reaches serve/add_host; why_new=only logged-out and missing were covered; seam=none
    monkeypatch.setattr(remote, "tailscale_bin", lambda: str(ts / "tailscale"))
    (ts / "status.json").write_text(json.dumps({"BackendState": "Running", "Self": {}}))
    assert remote.setup() == 1 and "MagicDNS" in capsys.readouterr().err
    assert not any(c.startswith("serve") for c in calls(ts)) and not config.web_hosts_path().exists()


def busy(*ports):
    web = {f"{NAME}:{p}": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{3000 + i}"}}} for i, p in enumerate(ports)}
    return {"Web": web}


def test_443_busy_uses_8443_without_asking_and_off_removes_it(ts, capsys):
    (ts / "serve.json").write_text(json.dumps(busy(443)))
    assert remote.setup() == 0
    out = capsys.readouterr().out
    assert "serve --bg --https=8443 8888" in calls(ts) and not any(c.startswith("serve --bg 8888") for c in calls(ts))
    assert remote.hosts() == [f"{NAME}:8443"]   # the server's Host/Origin check takes host:port (web/test/routes.test.js)
    assert f"https://{NAME}:8443/?pair=" in out and "\u2580" in out
    assert remote.doctor_line() == (True, f"https://{NAME}:8443 \u2192 port 8888", "")
    assert remote.setup() == 0 and calls(ts).count("serve --bg --https=8443 8888") == 1   # kept as is
    remote.off()
    assert "serve --https=8443 off" in calls(ts) and "serve --https=443 off" not in calls(ts)
    assert list(json.loads((ts / "serve.json").read_text())["Web"]) == [f"{NAME}:443"]   # the other service is untouched
    assert remote.hosts() == []


def test_443_and_8443_busy_uses_10000(ts, capsys):
    (ts / "serve.json").write_text(json.dumps(busy(443, 8443)))
    assert remote.setup() == 0
    assert "serve --bg --https=10000 8888" in calls(ts) and remote.hosts() == [f"{NAME}:10000"]
    assert f"https://{NAME}:10000/?pair=" in capsys.readouterr().out


def test_every_https_port_busy_stops_and_changes_nothing(ts, capsys):
    (ts / "serve.json").write_text(json.dumps(busy(443, 8443, 10000)))
    assert remote.setup() == 1
    err = capsys.readouterr().err
    assert "443: http://127.0.0.1:3000" in err and "10000: http://127.0.0.1:3002" in err
    assert not any(c.startswith("serve --bg") for c in calls(ts)) and not config.web_hosts_path().exists()
    assert remote.setup(dry_run=True) == 1


def test_dry_run_with_443_busy_names_the_port(ts, capsys):
    (ts / "serve.json").write_text(json.dumps(busy(443)))
    assert remote.setup(dry_run=True) == 0
    assert "--https=8443 8888" in capsys.readouterr().out and not any(c.startswith("serve --bg") for c in calls(ts))

def test_shows_the_enable_link_when_serve_is_off(ts, capsys):
    (ts / "needs-enable").write_text("")
    with pytest.raises(SystemExit):
        cli.main(["remote"])
    cap = capsys.readouterr()
    assert "https://login.tailscale.com/f/serve?node=abc" in cap.out and "Enable it here" in cap.err and not config.web_hosts_path().exists()


def test_serve_failing_without_a_link_says_why_and_changes_nothing(ts, monkeypatch, capsys):
    # Value: protects=a failed `serve --bg` with no enable link reports its last line + a fix and writes no web-hosts; fails_when=exit code ignored or hint branch broken; why_new=only the link case was tested; seam=none
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")   # the hint depends on the OS; pin it so the runner's does not decide
    (ts / "fail").write_text("serve: access denied, try sudo\n")
    assert remote.setup() == 1
    err = capsys.readouterr().err
    assert "tailscale serve failed (serve: access denied, try sudo)" in err and "Run it yourself:" in err and not config.web_hosts_path().exists()


def test_serve_denied_on_linux_says_to_set_the_operator(ts, monkeypatch, capsys):
    # Value: protects=on Linux a permission failure from `serve --bg` names the `set --operator` fix and writes no web-hosts; fails_when=the Linux branch or its permission match breaks; why_new=split from the macOS case so neither depends on the runner's OS; seam=none
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    (ts / "fail").write_text("serve: access denied, try sudo\n")
    assert remote.setup() == 1
    err = capsys.readouterr().err
    assert "tailscale serve failed (serve: access denied, try sudo)" in err and "set --operator=$USER" in err and "Run it yourself:" not in err
    assert not config.web_hosts_path().exists()


def test_waiting_on_serve_ends_cleanly_on_timeout_and_ctrl_c(ts, monkeypatch, capsys):
    # Value: protects=serve that never returns is killed at the timeout (exit 1) and Ctrl-C exits 130, neither writing web-hosts; fails_when=timeout/interrupt handling removed; why_new=no test hangs serve; seam=none
    (ts / "hang").write_text("")
    real = remote.stream   # its timeout default binds SERVE_TIMEOUT_S at import, so shorten it at the call
    monkeypatch.setattr(remote, "stream", lambda argv: real(argv, timeout=0.5))
    assert remote.setup() == 1 and "Gave up waiting" in capsys.readouterr().err and not config.web_hosts_path().exists()
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(remote, "stream", interrupted)
    assert remote.setup() == 130 and "Stopped." in capsys.readouterr().err and not config.web_hosts_path().exists()


def test_add_host_keeps_the_names_already_there(home):
    # Value: protects=web-hosts merge never drops a name a person or earlier run put there; fails_when=add_host overwrites instead of merging; why_new=existing tests start from an empty file; seam=none
    config.web_hosts_path().write_text("other.example:8443, mac.ts.net\n")
    assert remote.add_host(NAME) and not remote.add_host(NAME)
    assert remote.hosts() == ["other.example:8443", "mac.ts.net", NAME]


def test_a_name_served_on_another_https_port_keeps_its_port(ts):
    (ts / "serve.json").write_text(json.dumps({"Web": {f"{NAME}:8443": {"Handlers": {"/": {"Proxy": "http://localhost:8888"}}}}}))
    assert remote.setup() == 0 and remote.hosts() == [f"{NAME}:8443"]


def test_claude_may_not_run_it(ts, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit):
        cli.main(["remote"])
    assert not calls(ts)


def test_doctor_line(ts, monkeypatch):
    monkeypatch.setattr(remote, "tailscale_bin", lambda: None)
    assert remote.doctor_line()[0] is None   # off is fine, and said
    remote.add_host(NAME)
    ok, _, fix = remote.doctor_line()
    assert ok is False and "remote --off" in fix
    monkeypatch.setattr(remote, "tailscale_bin", lambda: str(ts / "tailscale"))
    assert remote.doctor_line()[0] is False   # named but not served
    (ts / "serve.json").write_text(json.dumps(serve_json(8888)))
    assert remote.doctor_line()[:2] == (True, f"https://{NAME} → port 8888")
    (ts / "serve.json").write_text(json.dumps(serve_json(8888, funnel=True)))
    ok, detail, fix = remote.doctor_line()
    assert ok is False and "public internet" in detail and "funnel --https=443 off" in fix and "reset" not in fix
    config.web_hosts_path().unlink()
    (ts / "serve.json").write_text(json.dumps(serve_json(8888)))
    ok, detail, fix = remote.doctor_line()   # after --off, or a setup from before web-hosts: the phone gets 421, nothing is exposed
    assert ok is None and "web-hosts does not list" in detail and "optional" in fix
    # Value: protects=the shell's FINNAMON_WEB_HOSTS never vouches for the dashboard's (the unit's) allow-list, and a failing `serve status` is a warning, never a failure; fails_when=doctor reads os.environ again or a quit Tailscale app fails doctor; why_new=both were reached by no test; seam=none
    monkeypatch.setenv("FINNAMON_WEB_HOSTS", NAME)
    assert remote.doctor_line()[0] is None
    (ts / "serve-fails").write_text("")
    assert remote.doctor_line()[0] is None
    remote.add_host(NAME)
    ok, detail, _ = remote.doctor_line()
    assert ok is None and "failed" in detail and NAME in detail


def test_refuses_while_funnel_puts_the_dashboard_on_the_internet(ts, capsys):
    # Value: protects=remote never reports success or lets a name in while Funnel exposes the dashboard, foreground Funnel included; fails_when=the Funnel check only warns or reads only the top-level AllowFunnel; why_new=only doctor's Funnel branch was tested; seam=none
    for cfg in (serve_json(8888, funnel=True),
                {"Foreground": {"s1": serve_json(8888, funnel=True)}}):
        (ts / "serve.json").write_text(json.dumps(cfg))
        assert remote.setup() == 1
        err = capsys.readouterr().err
        assert "public internet" in err and "funnel --https=443 off" in err and "reset" not in err
        assert not config.web_hosts_path().exists() and not any(c.startswith("serve --bg") for c in calls(ts))


def test_serves_the_installed_port_whatever_the_shell_exports(ts, monkeypatch):
    # Value: protects=the units never set PORT, so a shell PORT (a dev server's) never decides what goes on the tailnet; fails_when=remote reads PORT again; why_new=conftest clears PORT, so nothing tried it; seam=none
    monkeypatch.setenv("PORT", "3000")
    assert remote.setup() == 0 and "serve --bg 8888" in calls(ts)
    assert remote.serve_targets(serve_json(88880), 8888)["names"] == set()   # a port that only starts with ours is not ours


def test_a_foreground_serve_is_made_permanent_and_every_name_let_in(ts):
    # Value: protects=a terminal's temporary `tailscale serve` is replaced by --bg, and every name the page is served on is let in; fails_when=foreground names count as served, or only the first name is added; why_new=all tests used one top-level Web entry; seam=none
    (ts / "serve.json").write_text(json.dumps({"Foreground": {"s1": serve_json(8888)}}))
    assert remote.setup() == 0 and "serve --bg 8888" in calls(ts)
    both = serve_json(8888)
    both["Web"][f"{NAME}:8443"] = both["Web"][f"{NAME}:443"]
    (ts / "serve.json").write_text(json.dumps(both))
    assert remote.setup() == 0 and remote.hosts() == [NAME, f"{NAME}:8443"] and remote.doctor_line()[0] is True


def test_stops_when_serve_status_cannot_be_read(ts, capsys):
    # Value: protects=an unreadable serve status is not "nothing served", so 443 is never taken over unasked; fails_when=a failing status falls through to serve --bg; why_new=only doctor saw a failing status; seam=none
    (ts / "serve-fails").write_text("")
    assert remote.setup() == 1 and "Could not read" in capsys.readouterr().err
    assert not any(c.startswith("serve --bg") for c in calls(ts)) and not config.web_hosts_path().exists()


def test_off_takes_the_names_back_out(ts, capsys):
    # Value: protects=a household can turn remote access off and doctor stops failing; fails_when=--off leaves names in web-hosts; why_new=web-hosts was only ever added to; seam=none
    remote.add_host("Mini.Tail1.ts.net:443")
    assert remote.hosts() == [NAME]   # as the server normalizes it
    cli.main(["remote", "--off", "--dry-run"])
    assert remote.hosts() == [NAME] and "Would remove" in capsys.readouterr().out
    cli.main(["remote", "--off"])
    out = capsys.readouterr().out
    assert remote.hosts() == [] and remote.doctor_line()[0] is None
    assert "serve --https=443 off" not in calls(ts)   # nothing served to the dashboard, so nothing to turn off
    cli.main(["remote"])
    capsys.readouterr()
    cli.main(["remote", "--off", "--dry-run"])
    assert "Would run:" in capsys.readouterr().out and "serve --https=443 off" not in calls(ts)
    remote.off()
    # Value: protects=--off stops Tailscale serving the dashboard's port, so whatever binds 8888 next is not handed to the tailnet; fails_when=--off only edits web-hosts; why_new=--off never ran tailscale before; seam=none
    assert "serve --https=443 off" in calls(ts) and remote.doctor_line()[0] is None


def test_the_dashboard_reads_the_file_remote_writes():
    # Value: protects=Python and Node name the same web-hosts file; fails_when=either side renames it and remote writes a file the dashboard never reads; why_new=routes.test injects the file; seam=none
    js = (Path(remote.__file__).parent.parent / "web" / "server.js").read_text()
    assert f"export const HOSTS_FILE = '{config.WEB_HOSTS_FILE}';" in js


def test_the_assistant_may_not_run_remote_or_edit_its_names():
    # Value: protects=the household session cannot put the dashboard on the tailnet or widen its allow-list; fails_when=a deny rule is dropped from the bundle; why_new=open/web had this, remote did not; seam=none
    from finnamon import assistant
    deny = set(json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]["deny"])
    assert {"Bash(finnamon remote)", "Bash(finnamon remote *)", "Edit(~/.finnamon/web-hosts)"} <= deny


def test_doctor_always_prints_the_line(home, fake_claude, capsys):
    with pytest.raises(SystemExit):
        cli.main(["doctor"])
    assert "Remote access: off (optional" in capsys.readouterr().out


def test_finds_the_mac_app_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(remote.shutil, "which", lambda _: None)
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")
    app = tmp_path / "Tailscale"; app.write_text("")
    monkeypatch.setattr(remote, "MAC_APP_CLI", str(app))
    assert REAL_TAILSCALE_BIN() == str(app)
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    assert REAL_TAILSCALE_BIN() is None


def test_a_failed_status_names_its_error_and_dry_run_changes_nothing(ts, capsys):
    # Value: protects=a broken `tailscale status` says why, and --dry-run with a busy 443 previews 8443 and changes nothing; fails_when=the status-failed branch or the dry-run guard breaks; why_new=only NeedsLogin and the non-tty refusal were tested; seam=none
    (ts / "status.json").write_text("not json")
    assert remote.setup() == 1 and "`tailscale status` failed" in capsys.readouterr().err
    (ts / "status.json").write_text(json.dumps({"BackendState": "Running", "Self": {"DNSName": NAME + "."}}))
    (ts / "serve.json").write_text(json.dumps(serve_json(3000)))
    assert remote.setup(dry_run=True) == 0   # previews the whole run
    out = capsys.readouterr().out
    assert "Would run:" in out and "--https=8443" in out and "Would add" in out
    assert not config.web_hosts_path().exists() and not any(c.startswith("serve --bg") for c in calls(ts))


def test_post_serve_checks_and_a_declined_takeover(ts, monkeypatch, capsys):
    # Value: protects=after serve --bg, Funnel on or nothing served to our port is refused before any name is let in, and the TCP forwarder on 443 counts as busy; fails_when=the post-serve re-read or the [y/N] default breaks; why_new=only the pre-serve branches were tested; seam=none
    (ts / "after").write_text(',"AllowFunnel":{"' + NAME + ':443":true}')
    assert remote.setup() == 1 and "public internet" in capsys.readouterr().err and not config.web_hosts_path().exists()
    (ts / "after").unlink(); (ts / "serve.json").unlink()
    with monkeypatch.context() as m:   # never undo() here: that would also drop conftest's stubs (the fake tailscale, the scratch home)
        m.setattr(remote, "stream", lambda argv: (0, ""))   # serve "succeeds" without changing anything
        assert remote.setup() == 1 and "does not send anything" in capsys.readouterr().err and not config.web_hosts_path().exists()
    tcp = {"TCP": {"443": {"TCPForward": "127.0.0.1:22"}}}
    assert "443" in remote.serve_targets(tcp, 8888)["busy"]   # a raw TCP forwarder holds 443 too


def test_never_serves_a_port_the_dashboard_does_not_answer_on(ts, monkeypatch, capsys):
    # Value: protects=a Jupyter (or nothing) on 8888 is never put on the tailnet, and doctor flags a serve whose port is not the dashboard; fails_when=setup or doctor stop probing the port; why_new=nothing checked what answers on 8888; seam=none
    monkeypatch.setattr(remote, "dashboard_answers", lambda port=8888: False)
    assert remote.setup() == 1 and "does not answer on 127.0.0.1:8888" in capsys.readouterr().err
    assert not any(c.startswith("serve --bg") for c in calls(ts)) and not config.web_hosts_path().exists()
    (ts / "serve.json").write_text(json.dumps(serve_json(8888)))
    remote.add_host(NAME)
    ok, detail, fix = remote.doctor_line()
    assert ok is False and "does not answer there" in detail and "remote --off" in fix


def test_dashboard_answers_only_for_the_dashboard():
    # Value: protects=the probe tells the dashboard (its pages name `finnamon open`) from any other server, and nothing listening from either; fails_when=the marker or the error handling changes; why_new=every other test stubs it; seam=none
    import http.server, socket, threading
    class H(http.server.BaseHTTPRequestHandler):
        body = b""
        def do_GET(self):
            self.send_response(401); self.end_headers(); self.wfile.write(H.body)
        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        H.body = b"This page needs its key. Run `finnamon open` on the Finnamon box"
        assert REAL_DASHBOARD_ANSWERS(port)
        H.body = b"<title>Jupyter Server</title>"
        assert not REAL_DASHBOARD_ANSWERS(port)
    finally:
        srv.shutdown(); srv.server_close()
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0)); free = sk.getsockname()[1]
    assert not REAL_DASHBOARD_ANSWERS(free)


def test_off_reports_what_it_could_not_do(ts, capsys):
    # Value: protects=--off never claims Tailscale stopped serving when it did not (a failed off, an unreadable status), and names a foreground serve it cannot stop; fails_when=off ignores an exit code; why_new=the fake's off always succeeded; seam=none
    (ts / "serve.json").write_text(json.dumps(serve_json(8888)))
    (ts / "serve-off-fails").write_text("")
    assert remote.off() == 1
    cap = capsys.readouterr()
    assert "failed" in cap.err and "run it yourself" in cap.err and "no longer serves" not in cap.out
    (ts / "serve-off-fails").unlink()
    remote.add_host(NAME)
    (ts / "serve-fails").write_text("")
    assert remote.off() == 1 and "Could not read" in capsys.readouterr().out and remote.hosts() == []
    (ts / "serve-fails").unlink()
    (ts / "serve.json").write_text(json.dumps({"Foreground": {"s1": serve_json(8888)}}))
    assert remote.off() == 0 and "foreground" in capsys.readouterr().out


def test_pairing_code_is_one_time_short_lived_and_never_stored(home):
    import hashlib, re, time
    url = remote.pair_url(NAME)
    code = re.fullmatch(rf"https://{re.escape(NAME)}/\?pair=([\w-]{{22}})", url)[1]
    p = config.home() / config.WEB_PAIR_FILE
    rec = json.loads(p.read_text())
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and code not in p.read_text() and config.web_token_path().exists() is False
    assert rec["sha256"] == hashlib.sha256(code.encode()).hexdigest() and 0 < rec["expires"] - time.time() <= remote.PAIR_TTL_S == 300
    assert remote.pair_url(NAME) != url and json.loads(p.read_text())["sha256"] != rec["sha256"]   # a new one replaces it
    assert [f for f in p.parent.iterdir() if f.name.startswith(".web-pair")] == []


def test_qr_matches_another_encoder():
    """Known answers from segno 1.6 (make_qr(text, error='m', mode='byte', version=v, mask=k, boost_error=False)): version 1,
    and version 7, the first with version information blocks."""
    import hashlib
    from finnamon import qr
    h = lambda m: hashlib.sha256("".join("1" if x else "0" for r in m for x in r).encode()).hexdigest()
    assert h(qr.matrix("finnamon pair!", mask=3)) == "f455fb8158d3b819067880bc523b101af16003527803c4f94d913f9152d60c54"
    assert h(qr.matrix("https://mini.tail1.ts.net/?pair=" + "Ab9-" * 22 + "xy", mask=5)) == "8bc3eacb4353b20f81e8fc24e6fdf19697e527ec3b2790d1234bc8fc6d26d17a"
    lines = qr.render("finnamon pair!").splitlines()
    assert len(lines) == (21 + 8 + 1) // 2 and all(l.count("\u2580") == 21 + 8 for l in lines)   # a 4-module quiet zone all round
    with pytest.raises(ValueError):
        qr.matrix("x" * 214)
