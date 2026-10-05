"""Regression tests from the ship-time adversarial pass (cycle 3)."""
import io
import json
import sys
import urllib.error

import pytest

from finnamon import budgets, claude_runner, detect, heartbeat, plaid_api, store, sync, telegram, triage
from tests.conftest import AS_OF, seed, txn
from tests.test_daemon_cli import make_daemon, msg


def _detectors(tmp_path, monkeypatch):
    det = tmp_path / "detectors"
    for d in ("rules", "candidates", "pending"):
        (det / d).mkdir(parents=True)
    (det / "_prelude.sql").write_text(detect.PRELUDE.read_text())
    monkeypatch.setattr(detect, "DETECTORS", det)
    monkeypatch.setattr(detect, "PRELUDE", det / "_prelude.sql")
    return det


def test_review_is_human_only_and_exclusive(home, conn, monkeypatch, tmp_path, capsys):
    from finnamon import cli
    seed(conn)
    _detectors(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 1"))
    with pytest.raises(SystemExit):   # `--draft - --review` is not a way around the `--review` deny
        cli.main(["detect", "--draft", "-", "--name", "x", "--review"])
    assert "no other options" in capsys.readouterr().err
    for val in ("1", ""):   # claude_runner sets CLAUDECODE="" for nested runs; present is enough
        monkeypatch.setenv("CLAUDECODE", val)
        with pytest.raises(SystemExit):
            cli.main(["detect", "--review"])
        assert "Claude session" in capsys.readouterr().err
        with pytest.raises(SystemExit):
            cli.main(["settings", "set", "sync_interval_hours", "1", "--ops"])


def test_draft_and_promotion_never_shadow_a_live_detector(home, conn, monkeypatch, tmp_path, capsys):
    from finnamon import cli
    seed(conn)
    det = _detectors(tmp_path, monkeypatch)
    good = "SELECT account_id, 'x' AS kind, 'k' AS key, NULL AS transaction_id, '{}' AS payload FROM tx LIMIT 0"
    (det / "rules" / "low_balance.sql").write_text(good)
    monkeypatch.setattr(sys, "stdin", io.StringIO(good))
    with pytest.raises(SystemExit):
        cli.main(["detect", "--draft", "-", "--name", "low_balance"])
    assert "already exists" in capsys.readouterr().err and not (det / "pending" / "low_balance.sql").exists()
    # a hand-placed pending file with a live name is not promoted over it
    (det / "pending" / "low_balance.sql").write_text("SELECT 2 AS account_id, 'y' AS kind, 'k2' AS key, NULL AS transaction_id, '{}' AS payload")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    cli.main(["detect", "--review"])
    assert "rename the draft" in capsys.readouterr().out and (det / "rules" / "low_balance.sql").read_text() == good


def test_transient_plaid_errors_do_not_mark_the_item(home, conn, monkeypatch):
    from finnamon import secrets
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    for body in ({"error_code": "NETWORK_ERROR"}, {"error_type": "RATE_LIMIT_EXCEEDED", "error_code": "TRANSACTIONS_SYNC_LIMIT"},
                 {"error_code": "HTTP_502"}):
        monkeypatch.setattr(sync, "sync_accounts", lambda *a, **k: (_ for _ in ()).throw(plaid_api.PlaidError(body)))
        r = sync.sync_item(conn, "item1", token="tok")
        assert r["error"] == body["error_code"]
        assert conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "good"
    monkeypatch.setattr(sync, "sync_accounts", lambda *a, **k: (_ for _ in ()).throw(plaid_api.PlaidError({"error_code": "ITEM_LOGIN_REQUIRED"})))
    sync.sync_item(conn, "item1", token="tok")
    assert conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "ITEM_LOGIN_REQUIRED"


def test_poison_update_is_skipped_not_retried_forever(conn, tg, monkeypatch):
    seed(conn)
    d = make_daemon(conn)
    tg.updates = [[{"update_id": 7, "message": {"chat": {}}}, {**msg("hi"), "update_id": 8}]]  # first one has no chat id
    seen = []
    monkeypatch.setattr(d, "handle_update", lambda c, u: seen.append(u["update_id"]) if "text" in u.get("message", {}) else (_ for _ in ()).throw(KeyError("id")))
    monkeypatch.setattr(telegram, "get_updates", lambda offset=None, timeout=25, token=None: (d.stop.set(), tg.updates.pop(0) if tg.updates else [])[1])
    d.poll_loop()
    assert seen == [8] and store.get_state(conn, "telegram_offset") == "9"


def test_expired_enrolment_code_is_ignored_and_cleared(conn, tg):
    seed(conn)
    d = make_daemon(conn)
    store.set_state(conn, "pending_owner", json.dumps({"owner": "jane", "code": "ABC123", "deadline": "2000-01-01 00:00:00"}))
    d.handle_update(conn, msg("finnamon ABC123", uid=222, chat=-100, chat_type="group"))
    assert store.get_state(conn, "pending_owner") is None
    assert conn.execute("SELECT count(*) FROM owners WHERE owner='jane'").fetchone()[0] == 0


def test_telegram_network_errors_are_telegram_errors(home, monkeypatch):
    from finnamon import secrets
    secrets.write({}, {"bot_token": "1:x"}, {})
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError("dns")))
    with pytest.raises(telegram.TelegramError) as e:
        telegram.get_me()
    assert e.value.code == 0 and "URLError" in e.value.description


@pytest.mark.parametrize("bad", ["nan", "inf", "-5", "0", "abc"])
def test_amounts_must_be_finite_and_positive(conn, bad):
    seed(conn)
    with pytest.raises(ValueError):
        budgets.budget_set(conn, "groceries", bad)
    with pytest.raises(ValueError):
        budgets.normal(conn, kind="x", canonical="Costco", max_amount=bad)
    assert conn.execute("SELECT count(*) FROM budgets").fetchone()[0] == 0


def test_heartbeat_threshold_follows_the_sync_interval(conn, tg):
    seed(conn)
    store.set_setting(conn, "sync_interval_hours", "12", ops=True)
    store.set_state(conn, "last_run", "2026-09-19 00:00:00")
    assert not heartbeat.check(conn, now="2026-09-19 11:00:00")     # 11h stale is fine at a 12h interval
    assert heartbeat.check(conn, now="2026-09-19 14:30:00")         # 14.5h > 12+2
    with pytest.raises(ValueError):
        store.set_setting(conn, "sync_interval_hours", "24", ops=True)


def test_triage_attempts_count_only_groups_claude_saw(conn, monkeypatch):
    seed(conn)
    conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant','a:t1','t1','{}',?)", (AS_OF,))

    def fake_run(prompt, resume=None, timeout=120, env_extra=None, disallowed=()):
        # a new candidate lands while Claude is thinking; it was never shown
        conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant','a:t2','t2','{}',?)", (AS_OF,))
        return claude_runner.Result(False, "", None, error="timeout")
    monkeypatch.setattr(claude_runner, "run", fake_run)
    triage.run_if_needed(conn)
    rows = dict(conn.execute("SELECT key, triage_attempts FROM alerts").fetchall())
    assert rows == {"a:t1": 1, "a:t2": 0}


def test_prelude_with_is_case_insensitive(tmp_path):
    f = tmp_path / "lower.sql"
    f.write_text("with x as (select 1) SELECT 1")
    body = detect.assemble(f, "rule:lower")
    assert ", x as (select 1)" in body and body.lower().count("with ") == 1


def test_triage_runs_cannot_write_settings_or_budgets(home, conn, monkeypatch, capsys):
    """--disallowedTools is prefix-matched; argparse accepts options before the subcommand, so the CLI gates writes itself."""
    from finnamon import cli
    seed(conn)
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    for argv in (["settings", "--account", "*", "set", "large_amount", "9999999"], ["budget", "--category", "food", "set", "groceries", "1"],
                 ["normal", "Costco"], ["threshold", "Chase Checking", "1"], ["category", "costco", "groceries"], ["category", "--tx", "t1", "groceries"], ["alias", "a", "b"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "read-only" in capsys.readouterr().err
    assert store.setting(conn, "large_amount") != "9999999" and conn.execute("SELECT count(*) FROM budgets").fetchone()[0] == 0
    cli.main(["normal", "--list"]); cli.main(["budget"]); cli.main(["settings"])  # reads still work
    monkeypatch.setattr(sys, "stdin", io.StringIO("fine\n"))
    cli.main(["triage", "set", "t1", "suppress", "low", "-"])                      # the one allowed write
    monkeypatch.delenv("FINNAMON_TRIAGE")
    with pytest.raises(SystemExit):
        cli.main(["budget", "set", "groceries", "nan"])
    assert "positive amount" in capsys.readouterr().err
