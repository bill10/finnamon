"""#112 `finnamon demo` and #111's no-bot daemon: a made-up household in its own home, a dashboard over it, never ~/.finnamon."""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from finnamon import cli, daemon, demo, scheduler, store
from tests.conftest import REAL_BOT_CONFIGURED

TODAY = date(2026, 10, 3)


def test_seed_tells_the_stories(home, capsys):
    conn = store.connect()
    demo.seed(conn, TODAY)
    kinds = {r[0] for r in conn.execute("SELECT kind FROM alerts WHERE tier='rule' AND resolved_at IS NULL")}
    assert kinds == {"budget_pace", "duplicate_charge", "low_balance", "new_recurring"}
    assert conn.execute("SELECT count(*) FROM alerts WHERE resolved_at IS NOT NULL AND tier='rule'").fetchone()[0] >= 1   # last month's, dealt with
    assert conn.execute("SELECT count(DISTINCT substr(date,1,7)) FROM transactions").fetchone()[0] == 6
    assert conn.execute("SELECT min(date) FROM transactions").fetchone()[0] >= "2026-05-01" and conn.execute("SELECT max(date) FROM transactions").fetchone()[0] <= str(TODAY)
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out)["demo"] is True
    assert len(json.loads((home / "charts" / "current.json").read_text())) == 3


def test_seed_matches_the_launch_video(home):
    """scripts/demo/launch-video.mjs records on Sep 20 2026, and its recorded answers quote these numbers."""
    conn = store.connect()
    demo.seed(conn, date(2026, 9, 20))
    spent = conn.execute("SELECT round(sum(amount), 2) FROM transactions WHERE pfc_detailed='FOOD_AND_DRINK_RESTAURANT' AND date>='2026-09-01'").fetchone()[0]
    assert spent == 248.0
    assert conn.execute("SELECT count(*) FROM transactions WHERE name='PAYMENT THANK YOU'").fetchone()[0] == 10   # both cards paid off every past month
    pace = {json.loads(p)["budget"]: json.loads(p) for (p,) in conn.execute("SELECT payload_json FROM alerts WHERE kind='budget_pace' AND resolved_at IS NULL")}
    assert pace["dining"]["limit"] == 350 and pace["dining"]["projection"] == 372.0
    dup = json.loads(conn.execute("SELECT payload_json FROM alerts WHERE kind='duplicate_charge' AND resolved_at IS NULL").fetchone()[0])
    assert (dup["merchant"], dup["amount"], dup["date_a"], dup["date_b"], dup["mask"]) == ("Shell", 52.18, "2026-09-17", "2026-09-18", "4821")


def test_demo_never_uses_a_household_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for bad in (tmp_path / ".finnamon", tmp_path / ".finnamon" / "demo", tmp_path):
        with pytest.raises(ValueError, match="overlaps"):
            demo.check_home(bad.resolve())
    with pytest.raises(ValueError, match="overlaps"):
        demo.check_home(Path(os.environ["FINNAMON_HOME"]).resolve())   # the shell's own household, wherever it lives
    demo.check_home((tmp_path / ".finnamon-demo").resolve())
    stranger = tmp_path / "notes"; stranger.mkdir(); (stranger / "keep.txt").write_text("mine")
    with pytest.raises(ValueError, match="not made by"):
        demo.build(stranger)
    assert (stranger / "keep.txt").exists()


@pytest.fixture
def fake_web(tmp_path, monkeypatch):
    """A stand-in for web/server.js: answers HTTP on $PORT and records the environment it was started with."""
    server = tmp_path / "server.js"
    server.write_text("import http.server, json, os\n"
                      "json.dump({k: os.environ.get(k) for k in ('FINNAMON_HOME', 'FINNAMON_DEMO', 'PORT')}, open(os.environ['FINNAMON_HOME'] + '/env.json', 'w'))\n"
                      "http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), http.server.SimpleHTTPRequestHandler).serve_forever()\n")
    monkeypatch.setattr(scheduler, "web_args", lambda: [sys.executable, str(server)])
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: pytest.fail("--print must not open a browser"))
    return server


def test_demo_start_stop_reset(tmp_path, monkeypatch, capsys, fake_web):
    h = tmp_path / "demo"
    monkeypatch.setenv("FINNAMON_DEMO_HOME", str(h))
    household = Path(os.environ["FINNAMON_HOME"])
    try:
        cli.main(["demo", "--print"])
        out = capsys.readouterr().out
        st = json.loads((h / demo.STATE).read_text())
        env = json.loads((h / "env.json").read_text())
        assert env == {"FINNAMON_HOME": str(h.resolve()), "FINNAMON_DEMO": "1", "PORT": str(st["port"])}
        assert f"http://localhost:{st['port']}/?token=" in out and (h / demo.MARKER).exists() and (h / "assistant" / "CLAUDE.md").exists()
        assert not (household / "finnamon.db").exists()   # the shell's household is never touched
        cli.main(["demo", "--print"])   # running already: the same address, no second server
        assert json.loads((h / demo.STATE).read_text())["pid"] == st["pid"]
        store.connect(h / "finnamon.db").execute("INSERT INTO properties (name, value) VALUES ('Boat', 1)")
        cli.main(["demo", "--reset", "--print"])
        assert not store.connect(h / "finnamon.db").execute("SELECT 1 FROM properties WHERE name='Boat'").fetchone()
    finally:
        cli.main(["demo", "--stop"])
    assert "stopped the demo dashboard" in capsys.readouterr().out and not (h / demo.STATE).exists()
    assert not demo._ours(st)


def test_demo_refuses_a_busy_port_and_a_stale_pid(tmp_path, monkeypatch, fake_web):
    import socket
    h = tmp_path / "demo"
    monkeypatch.setenv("FINNAMON_DEMO_HOME", str(h))
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0)); held.listen()
        with pytest.raises(SystemExit):   # something else answering there must not pass for the demo
            cli.main(["demo", "--print", "--port", str(held.getsockname()[1])])
    assert not (h / demo.STATE).exists()
    # demo.json naming a live process that is not ours (a pid reused after a reboot): never signalled
    (h / demo.STATE).write_text(json.dumps({"pid": os.getpid(), "started": "Mon Jan  1 00:00:00 2001", "port": 1}))
    monkeypatch.setattr(demo.os, "kill", lambda *a: pytest.fail("signalled a process the demo did not start"))
    cli.main(["demo", "--stop"])


def test_a_half_built_demo_is_made_again(tmp_path, monkeypatch, fake_web):
    h = tmp_path / "demo"
    monkeypatch.setenv("FINNAMON_DEMO_HOME", str(h))
    h.mkdir(); (h / demo.MARKER).write_text(""); (h / "leftover").write_text("")   # a build that died after the marker
    try:
        cli.main(["demo", "--print"])
        assert (h / demo.READY).exists() and not (h / "leftover").exists()
    finally:
        cli.main(["demo", "--stop"])


def test_daemon_without_a_bot_does_not_poll(conn, monkeypatch):
    assert REAL_BOT_CONFIGURED() is False   # init skipped Telegram: secrets.toml has no token
    monkeypatch.setattr(daemon, "bot_configured", REAL_BOT_CONFIGURED)
    from finnamon import telegram
    d = daemon.Daemon(conn_factory=lambda: conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: False)
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: pytest.fail("no bot, no getUpdates"))
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.poll_loop()
    assert store.get_state(conn, "daemon_alive_at")   # still alive for the page's health pill
