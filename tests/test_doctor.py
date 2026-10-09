"""`finnamon doctor`: every prerequisite with its fix, and nothing written on a box that has none of it yet.
Plaid, Telegram, the scheduler's unit directory and claude are all faked."""
import json
import shutil

import pytest

from finnamon import cli, config, plaid_api, scheduler, secrets, store, telegram
from finnamon.plaid_api import PlaidError
from tests.conftest import seed


def doctor(capsys) -> tuple[int, str]:
    try:
        cli.main(["doctor"])
        code = 0
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


def line(out: str, what: str) -> str:
    return next(l for l in out.splitlines() if l[2:].startswith(what + ":") or l[2:].startswith(what + " ("))


@pytest.fixture
def units(tmp_path, monkeypatch):
    d = tmp_path / "units"; d.mkdir()
    monkeypatch.setattr(scheduler, "unit_dir", lambda os_name=None: d)
    # Whether this checkout has web/node_modules must not decide what doctor reports: the dashboard counts as installed
    # unless a test says otherwise (test_no_dashboard_no_key_line)
    monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/x/web/server.js"])
    return d


def test_fresh_box_every_fix_is_init_and_nothing_is_written(home, units, fake_claude, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"loggedIn": False}))
    shutil.rmtree(home / "assistant")   # a box before init: not even the assistant directory
    before = sorted(p.name for p in home.iterdir())
    code, out = doctor(capsys)
    assert code == 1
    assert line(out, "Claude Code").startswith("✗") and "sign in" in out
    for what in ("Secrets", "Database"):
        assert line(out, what).startswith("✗"), what
    assert "Plaid keys" not in out and "Telegram bot" not in out, "no secrets file is said once, not once per key"
    assert "fix: finnamon init" in out
    assert line(out, "Scheduler").startswith("✗") and "not installed" in out
    assert line(out, "Dashboard key").startswith("✗") and "fix: finnamon open" in out
    assert line(out, "Assistant directory").startswith("✗")
    assert sorted(p.name for p in home.iterdir()) == before, "doctor reads only: no db, key or secrets made"


def test_set_up_household_is_all_good(home, units, fake_claude, tg, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"loggedIn": True, "email": "bill@example.com"}))
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    secrets.write({"client_id": "cid", "sandbox_secret": "sb"}, {"bot_token": "123:tok"}, {})
    conn = store.connect(); seed(conn)
    store.set_state(conn, "daemon_alive_at", store.now_local())
    for name, body in scheduler.render().items():
        (units / name).write_text(body)
    config.web_token()
    code, out = doctor(capsys)
    for what in ("Claude Code", "Secrets", "Plaid keys", "Telegram bot", "Household chat", "Banks", "Scheduler", "Dashboard key", "Assistant directory"):
        if what == "Secrets":
            assert "Secrets" not in out   # only said when it is wrong
            continue
        assert line(out, what).startswith("✓"), line(out, what)
    assert "1 linked" in out and "daemon running" in out and "(sandbox)" in out
    assert line(out, "Telegram inbound").startswith("✓ Telegram inbound: daemon")
    assert line(out, "Assistant").startswith("✓ Assistant: claude runs"), "which CLI is the assistant, named"
    store.set_state(conn, "inbound", "session")
    assert line(doctor(capsys)[1], "Telegram inbound").startswith("✓ Telegram inbound: session")
    monkeypatch.setattr(scheduler, "web_args", lambda: [])   # session mode with no dashboard to type into
    out2 = doctor(capsys)[1]
    assert line(out2, "Telegram inbound").startswith("✗") and "not installed" in out2
    store.set_state(conn, "inbound", None)
    # Node and the checkout's packages depend on the machine running the suite; every other line is this test's
    machine = ("Node", "Dashboard packages", "finnamon on PATH", "Linger")   # Linger: the real loginctl on Linux (off in a fresh WSL)
    assert not [l for l in out.splitlines() if l.startswith("✗") and not l[2:].startswith(machine)]


def test_reads_only_even_with_a_database(home, units, fake_claude, tg, capsys, monkeypatch):
    """A `git pull` before `finnamon update` leaves migrations pending: doctor must not apply them under the old daemon."""
    conn = store.connect(); seed(conn); conn.close()
    monkeypatch.setattr(store, "migrate", lambda c: (_ for _ in ()).throw(AssertionError("doctor migrated the database")))
    _, out = doctor(capsys)
    assert line(out, "Banks").startswith("✓")


def test_a_broken_secrets_file_is_a_line_not_a_traceback(home, units, fake_claude, capsys):
    (home / "secrets.toml").write_text("[plaid\nclient_id = ")
    (home / "secrets.toml").chmod(0o600)
    code, out = doctor(capsys)
    assert code == 1 and "cannot read" in line(out, "Secrets") and "Database" in out   # the checks after it still ran
    (home / "secrets.toml").write_text('plaid = "not a table"\n')
    _, out = doctor(capsys)
    assert "cannot read" in line(out, "Secrets")


def test_only_a_login_problem_is_sent_to_relink(home, units, fake_claude, tg, capsys):
    conn = store.connect(); seed(conn)
    conn.execute("UPDATE items SET status='INSTITUTION_DOWN', last_error='bank is down'")
    _, out = doctor(capsys)
    assert "INSTITUTION_DOWN: bank is down" in line(out, "Bank Chase") and "link --update" not in out and "retried on every sync" in out


def test_what_breaks_says_how_to_fix_it(home, units, fake_claude, tg, capsys, monkeypatch):
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_API_KEYS", "error_message": "bad"})))
    secrets.write({"client_id": "cid", "sandbox_secret": "sb"}, {"bot_token": "123:tok"}, {})
    tg.me = {"username": "test_bot", "can_read_all_group_messages": False}
    conn = store.connect()
    code, out = doctor(capsys)
    assert code == 1
    assert "Plaid rejected the keys" in line(out, "Plaid keys")
    assert line(out, "Telegram bot").startswith("!") and "/setprivacy" in out   # a warning, not a failure
    assert "none linked, so nothing is watched" in line(out, "Banks") and "Link account" in out
    seed(conn); conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED'")
    _, out = doctor(capsys)
    assert "ITEM_LOGIN_REQUIRED" in line(out, "Bank Chase") and "fix: finnamon link --update item1" in out
    monkeypatch.setenv("FINNAMON_PLAID_ENV", "production")
    _, out = doctor(capsys)
    assert "no production secret (only sandbox)" in line(out, "Plaid keys")


def test_no_dashboard_no_key_line(home, units, fake_claude, capsys, monkeypatch):
    """Without Node or web/node_modules there is no page to open: the key line would only repeat what Node says."""
    monkeypatch.setattr(scheduler, "web_args", lambda: None)
    _, out = doctor(capsys)
    assert "Dashboard key" not in out


def test_backups_line(home, units, fake_claude, tg, capsys):
    conn = store.connect(); seed(conn)
    _, out = doctor(capsys)
    assert line(out, "Backups").startswith("!") and "each week" in out
    store.set_state(conn, "last_backup_at", "2026-09-20 03:00:00")
    _, out = doctor(capsys)
    assert line(out, "Backups").startswith("✓") and "2026-09-20" in line(out, "Backups")
    store.set_state(conn, "backup_error", "2026-09-27 03:00:00 OSError: disk full")
    _, out = doctor(capsys)
    assert line(out, "Backups").startswith("✗") and "disk full" in out


def test_voice_is_worth_knowing_not_a_problem(home, units, fake_claude, capsys, monkeypatch):
    """Talk works without whisper.cpp (the browser's recognition), so doctor says how to set it up but counts no failure for it."""
    from finnamon import voice
    monkeypatch.setattr(voice, "status", lambda: (False, "not set up"))
    _, out = doctor(capsys)
    assert line(out, "Voice").startswith("· Voice: not set up")
    assert "fix: optional: finnamon voice setup" in out
    monkeypatch.setattr(voice, "status", lambda: (True, "/opt/homebrew/bin/whisper-cli, model x"))
    _, out = doctor(capsys)
    assert line(out, "Voice").startswith("✓ Voice:")


def test_wsl_lines_only_on_wsl(home, units, fake_claude, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_is_wsl", lambda: False)
    assert "WSL" not in doctor(capsys)[1]
    monkeypatch.setattr(cli, "_is_wsl", lambda: True)
    out = doctor(capsys)[1]
    assert line(out, "WSL systemd")[0] in "✓✗" and "vmIdleTimeout" in out and "localhost" in out


def test_fetch_by_ai_extension_line(home, units, fake_claude, capsys, monkeypatch):
    """Unpaired is optional (it pairs itself on first use); paired is fine, but on a Claude Code without --chrome, is a fault."""
    conn = store.connect(); seed(conn)
    monkeypatch.setattr(cli, "claude_version", lambda: (2, 1, 292))
    out = doctor(capsys)[1]
    assert not line(out, "Fetch by AI (extension)").startswith(("✗", "✓")) and "pairs itself" in out
    store.set_state(conn, cli.EXTENSION_DEVICE, "dev-1")
    assert line(doctor(capsys)[1], "Fetch by AI (extension)").startswith("✓")
    monkeypatch.setattr(cli, "claude_version", lambda: (2, 1, 100))
    out = doctor(capsys)[1]
    assert line(out, "Fetch by AI (extension)").startswith("✗") and "claude update" in out


def test_a_rejected_bot_token_is_named_as_such(home, units, fake_claude, tg, capsys, monkeypatch):
    secrets.write({"client_id": "cid", "sandbox_secret": "sb"}, {"bot_token": "000000:DUMMY"}, {})
    monkeypatch.setattr(telegram, "get_me", lambda token=None: (_ for _ in ()).throw(telegram.TelegramError(401, "Unauthorized")))
    _, out = doctor(capsys)
    assert line(out, "Telegram bot").startswith("✗") and "Telegram rejected the bot token (401: Unauthorized)" in out
    monkeypatch.setattr(telegram, "get_me", lambda token=None: (_ for _ in ()).throw(telegram.TelegramError(0, "URLError: no route")))
    _, out = doctor(capsys)
    assert "could not reach Telegram (URLError: no route)" in line(out, "Telegram bot")
