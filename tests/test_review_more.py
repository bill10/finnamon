"""Tests added by the pre-landing review: guards and boundaries the first pass left untested."""
import json
import sqlite3

import pytest

from finnamon import claude_runner, config, link, plaid_api, secrets, store, triage
from tests.conftest import AS_OF, seed, txn
from tests.test_daemon_cli import make_daemon


def test_converse_refuses_chart_outside_charts_dir(conn, tg, fake_claude, monkeypatch, tmp_path):
    seed(conn)
    evil = tmp_path / "evil" / "charts" / "x.png"
    evil.parent.mkdir(parents=True); evil.write_bytes(b"png")
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": f"see {evil}", "session_id": "s"}))
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "chart", "reply_to": None, "message_id": 1})
    assert tg.photos == [] and str(evil) in tg.sent[-1]["text"]
    # traversal out of the real charts dir is refused too
    trav = config.charts_dir() / ".." / ".." / "evil" / "charts" / "x.png"
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": f"see {trav}", "session_id": "s"}))
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "chart", "reply_to": None, "message_id": 2})
    assert tg.photos == []


def test_plaid_env_argument_selects_the_matching_secret(home, monkeypatch):
    secrets.write({"client_id": "c", "sandbox_secret": "sb", "production_secret": "prod"}, {}, {})
    monkeypatch.setenv("FINNAMON_PLAID_ENV", "production")
    seen = {}

    class R:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url; seen["secret"] = json.loads(req.data)["secret"]
        return R(b'{"public_token": "p"}')
    monkeypatch.setattr(plaid_api.urllib.request, "urlopen", fake_urlopen)
    plaid_api.sandbox_public_token_create()
    assert seen["url"].startswith("https://sandbox.plaid.com/") and seen["secret"] == "sb"
    plaid_api.accounts_get("tok")
    assert seen["url"].startswith("https://production.plaid.com/") and seen["secret"] == "prod"


def test_triage_success_path_counts_remaining(conn, monkeypatch):
    seed(conn)
    for k, tid in (("anom:first:t1", "t1"), ("anom:first:t2", "t2")):
        conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant',?,?,'{}',?)", (k, tid, AS_OF))

    def fake_run(prompt, resume=None, timeout=120, env_extra=None, disallowed=()):
        triage.set_verdict(conn, "t1", "promote", "high", "looks off")   # the skill stamps one of two groups
        return claude_runner.Result(True, "done", "s")
    monkeypatch.setattr(claude_runner, "run", fake_run)
    r = triage.run_if_needed(conn)
    assert r == {"groups": 2, "ran": True, "ok": True, "remaining": 1, "gave_up": 0, "error": None}
    assert conn.execute("SELECT triage_attempts FROM alerts WHERE key='anom:first:t2'").fetchone()[0] == 1
    assert conn.execute("SELECT triage_attempts FROM alerts WHERE key='anom:first:t1'").fetchone()[0] == 0


@pytest.mark.parametrize("total,same,expect", [(4, 4, False), (5, 4, False), (5, 5, True)])
def test_mirror_detection_boundaries(conn, total, same, expect):
    seed(conn)
    conn.execute("INSERT INTO owners (owner) VALUES ('jane')")
    conn.execute("INSERT INTO items (item_id, institution_id, institution, owner, created_at) VALUES ('item2','ins_1','Chase','jane','2026-09-19')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('chk_j','item2','Chase Checking','depository','checking','4821','jane')")
    from tests.conftest import today
    from datetime import date, timedelta
    for i in range(total):
        d = (date.today() - timedelta(days=i + 1)).isoformat()
        txn(conn, f"j{i}", "chk_j", d, 10 + i, f"m{i}")
        if i < same:
            txn(conn, f"b{i}", "chk", d, 10 + i, f"m{i}")
    assert bool(link.detect_mirrors(conn, "item2")) is expect


# --- Fix-First batch (red team + specialists) -----------------------------------------------------

def test_unscoped_suppression_is_refused(conn):
    from finnamon import budgets
    seed(conn)
    with pytest.raises(ValueError, match="silence every detector"):
        budgets.normal(conn)
    with pytest.raises(ValueError):
        budgets.normal(conn, kind="anomaly:first_merchant")          # kind alone is still every merchant
    assert budgets.normal(conn, kind="anomaly:first_merchant", max_amount=50)["max_amount"] == 50
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):                       # and the schema refuses it even if code slips
        conn.execute("INSERT INTO suppressions (note) VALUES ('oops')")


def test_failed_cycle_sets_last_attempt_and_network_errors_are_plaid_errors(conn, tg, monkeypatch):
    from finnamon import plaid_api, run as runmod, store, sync
    import urllib.error
    seed(conn)
    monkeypatch.setattr(sync, "sync_all", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    r = runmod.cycle(conn, do_triage=False)
    assert r["error"] and store.get_state(conn, "last_run") is None and store.get_state(conn, "last_attempt") == r["as_of"]
    # a DNS blip inside plaid_api.call is a PlaidError(NETWORK_ERROR), handled per Item, not a crash
    monkeypatch.setattr(plaid_api.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError("dns")))
    from finnamon import secrets
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    with pytest.raises(plaid_api.PlaidError) as e:
        plaid_api.accounts_get("tok")
    assert e.value.code == "NETWORK_ERROR"


def test_daemon_exits_when_a_thread_dies_and_refuses_without_harness(conn, monkeypatch, tmp_path):
    from finnamon import daemon, claude_runner
    seed(conn)
    d = daemon.Daemon(conn_factory=lambda: conn)
    exits = []
    monkeypatch.setattr(d, "sync_loop", lambda: None)          # dies immediately
    monkeypatch.setattr(d, "poll_loop", lambda: d.stop.wait(5))
    monkeypatch.setattr(d, "converse_loop", lambda: d.stop.wait(5))
    monkeypatch.setattr(d.stop, "wait", lambda t=None: None)   # don't actually sleep 30s
    d.start(exit=lambda code: exits.append(code))
    assert exits == [1] and store.get_state(conn, "daemon_last_error").startswith("thread(s) died: sync")
    (tmp_path / "blocker").write_text("")   # a path nothing can create the directory under: the daemon's own write of the bundle fails too
    monkeypatch.setenv("FINNAMON_ASSISTANT", str(tmp_path / "blocker" / "assistant"))
    problems = claude_runner.harness_problems()
    assert len(problems) == 6 and any("settings.json" in p for p in problems) and "finnamon install" in problems[0] and "not trusted" in problems[-1]
    exits.clear()
    daemon.Daemon(conn_factory=lambda: conn).start(exit=lambda code: exits.append(code))
    assert exits == [2] and len(json.loads(store.get_state(conn, "daemon_starts"))) == 2


def test_daemon_up_for_a_while_clears_the_crash_loop_record(conn, monkeypatch):
    from finnamon import daemon
    seed(conn)
    store.set_state(conn, "daemon_starts", '["a", "b"]'); store.set_state(conn, "crash_loop_reported", "b")
    store.set_state(conn, "daemon_last_error", "harness check failed")
    import threading
    d = daemon.Daemon(conn_factory=lambda: conn)
    hold = threading.Event()
    for name in ("sync_loop", "poll_loop", "converse_loop"):
        monkeypatch.setattr(d, name, hold.wait)
    monkeypatch.setattr(daemon, "STABLE_S", 0)                          # up long enough on the first tick
    ticks = []
    monkeypatch.setattr(d.stop, "wait", lambda t=None: ticks.append(t) if len(ticks) < 4 else (hold.set(), d.stop.set()))
    d.start(exit=lambda code: pytest.fail("no thread died"))
    assert store.get_state(conn, "daemon_starts") is None and store.get_state(conn, "crash_loop_reported") is None
    assert store.get_state(conn, "daemon_last_error") is None


def test_a_clean_stop_takes_its_start_back(conn, monkeypatch):
    """`finnamon update` restarts several times in ten minutes: SIGTERM stops are not crashes."""
    from finnamon import daemon
    seed(conn)
    store.set_state(conn, "daemon_starts", '["a", "b"]')
    d = daemon.Daemon(conn_factory=lambda: conn)
    for name in ("sync_loop", "poll_loop", "converse_loop"):
        monkeypatch.setattr(d, name, lambda: None)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())   # the signal handler's stop, before any liveness check
    d.start(exit=lambda code: pytest.fail("a clean stop exits 0"))
    assert store.get_state(conn, "daemon_starts") == '["a", "b"]'


def test_daemon_start_records_a_crash_and_survives_a_broken_state_db(conn, monkeypatch):
    from finnamon import daemon
    seed(conn)
    store.set_state(conn, "daemon_starts", json.dumps([str(i) for i in range(10)]))
    monkeypatch.setattr(claude_runner, "harness_problems", lambda: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(OSError):
        daemon.Daemon(conn_factory=lambda: conn).start(exit=lambda code: pytest.fail("raised, not exited"))
    starts = json.loads(store.get_state(conn, "daemon_starts"))
    assert len(starts) == 10 and starts[0] == "1"                      # capped: the oldest start drops off
    assert store.get_state(conn, "daemon_last_error") == "OSError: disk gone"
    def broken():
        raise sqlite3.OperationalError("database is locked")
    exits = []
    monkeypatch.setattr(claude_runner, "harness_problems", lambda: ["no .claude/"])
    daemon.Daemon(conn_factory=broken).start(exit=exits.append)       # bookkeeping fails; the start still runs
    assert exits == [2]


def test_two_timeouts_rotate_the_session(conn, tg, monkeypatch):
    from finnamon import daemon, claude_runner, store
    seed(conn)
    store.set_state(conn, "session", "old")
    calls = []
    def fake(prompt, resume=None, timeout=120):
        calls.append(resume)
        return claude_runner.Result(False, "", None, error="timeout after 1s") if resume else claude_runner.Result(True, "fresh", "new")
    monkeypatch.setattr(claude_runner, "run", fake)
    d = daemon.Daemon(conn_factory=lambda: conn)
    d.converse(conn, {"owner": "bill", "text": "a", "reply_to": None, "message_id": 1, "chat_id": "1234567890"})
    assert calls == ["old"] and "timeout" in tg.sent[-1]["text"] and "partially" in tg.sent[-1]["text"]
    d.converse(conn, {"owner": "bill", "text": "b", "reply_to": None, "message_id": 2, "chat_id": "1234567890"})
    assert calls == ["old", "old", None] and store.get_state(conn, "session") == "new" and tg.sent[-1]["text"] == "fresh"


def test_roundup_is_chunked_and_survives_a_send_failure(conn, tg, monkeypatch):
    from finnamon import notify
    from finnamon.telegram import TelegramError
    seed(conn)
    for i in range(40):
        conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, verdict, confidence, reason) VALUES ('anomaly','anomaly:first_merchant',?,?,?,'promote','low',?)",
                     (f"k{i}", json.dumps({"merchant": "M" * 60, "amount": 10 + i, "date": "2026-09-16", "account": "Sapphire", "mask": "7710"}), AS_OF, "reason " * 20))
    monkeypatch.setattr(notify, "ROUNDUP_CHUNK_CHARS", 1200)
    mid = notify.send_roundup(conn, "2026-09-20 18:00:00")
    assert mid == 101 and len(tg.sent) > 1 and "(1/" in tg.sent[0]["text"] and tg.sent[-1]["text"].endswith("pattern.")
    assert conn.execute("SELECT count(*) FROM alerts WHERE sent_at IS NOT NULL").fetchone()[0] == 40
    assert conn.execute("SELECT count(DISTINCT telegram_message_id) FROM roundup_items").fetchone()[0] == len(tg.sent)
    assert max(len(m["text"]) for m in tg.sent) <= 1200 + 100
    # a failure mid-way leaves the rest unsent and the week unmarked, so the next run continues; nothing is re-sent
    conn.execute("UPDATE alerts SET sent_at=NULL, telegram_message_id=NULL"); conn.execute("DELETE FROM roundup_items")
    from finnamon import store
    store.set_state(conn, "last_roundup_week", None)
    tg.sent.clear(); n = {"i": 0}
    real = tg.send_message
    def flaky(chat_id, text, reply_to=None, token=None):
        n["i"] += 1
        if n["i"] == 2:
            raise TelegramError(400, "can't parse entities")
        return real(chat_id, text, reply_to, token)
    monkeypatch.setattr(notify.telegram, "send_message", flaky)
    notify.send_roundup(conn, "2026-09-20 18:00:00")
    sent = conn.execute("SELECT count(*) FROM alerts WHERE sent_at IS NOT NULL").fetchone()[0]
    assert 0 < sent < 40 and store.get_state(conn, "last_roundup_week") is None
    # the next run picks up the rest without re-sending
    monkeypatch.setattr(notify.telegram, "send_message", real)
    notify.send_roundup(conn, "2026-09-20 18:30:00")
    assert conn.execute("SELECT count(*) FROM alerts WHERE sent_at IS NOT NULL").fetchone()[0] == 40
    assert store.get_state(conn, "last_roundup_week") == "2026-37"
    assert conn.execute("SELECT count(*) FROM roundup_items").fetchone()[0] == 40


def test_tombstoned_streams_never_trip_recurring_changed(conn):
    from finnamon import detect
    seed(conn, first_synced_at="2026-01-01 00:00:00")
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, last_date, predicted_next_date, status, first_seen_at) "
                 "VALUES ('dead','cc','outflow','Peloton','MONTHLY',44,44,'2026-01-01','2026-06-01','2026-07-01','TOMBSTONED','2026-06-01 00:00:00')")
    f = next(p for p in detect.detectors() if p.stem == "recurring_changed")
    assert detect.run_one(conn, f, AS_OF) == []


def test_prompt_precedes_variadic_options(fake_claude, monkeypatch, tmp_path):
    """--disallowedTools is variadic: the prompt must come first or it is swallowed as a tool name."""
    from finnamon import claude_runner
    argv_file = tmp_path / "argv"
    fake_claude.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_file}\nprintf \'{{"result":"ok","session_id":"s"}}\'\n')
    claude_runner.run("/triage", disallowed=claude_runner.TRIAGE_DISALLOWED)
    args = argv_file.read_text().splitlines()
    assert args[:2] == ["-p", "/triage"] and "--disallowedTools" in args and args[-1] != "/triage"


def test_review_previews_read_only_and_draft_rejects_dml(home, conn, monkeypatch, tmp_path, capsys):
    import io, sys
    from finnamon import cli, detect
    seed(conn)
    det = tmp_path / "detectors"
    for d in ("rules", "candidates", "pending"):
        (det / d).mkdir(parents=True)
    (det / "_prelude.sql").write_text(detect.PRELUDE.read_text())
    monkeypatch.setattr(detect, "DETECTORS", det)
    monkeypatch.setattr(detect, "PRELUDE", det / "_prelude.sql")
    for body in ("-- looks fine\nDELETE FROM alerts", "WITH x AS (SELECT 1) DELETE FROM transactions", "SELECT 1; DROP TABLE alerts"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(body))
        with pytest.raises(SystemExit):
            cli.main(["detect", "--draft", "-", "--name", "evil"])
    assert not (det / "pending" / "evil.sql").exists()
    # a draft that slipped in by hand is previewed read-only: the write fails, nothing is deleted
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','x','k','{}','2026-09-19 12:00:00')")
    (det / "pending" / "sneaky.sql").write_text("SELECT 1 AS account_id, 'k' AS kind, 'k' AS key, NULL AS transaction_id, '{}' AS payload FROM (DELETE FROM alerts) ")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    cli.main(["detect", "--review"])
    assert "does not run" in capsys.readouterr().out and conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1


def test_ops_settings_refused_from_claude(home, monkeypatch, capsys):
    from finnamon import cli, store
    conn = store.connect()
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "sync_interval_hours", "1", "--ops"])
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "dup_min_amount"])   # no value for a global key
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE")
    cli.main(["settings", "set", "sync_interval_hours", "2", "--ops"])
    assert store.setting(conn, "sync_interval_hours") == "2"


def test_duplicate_charge_catches_a_late_second_leg(conn):
    from finnamon import detect
    seed(conn)
    txn(conn, "a", "chk", "2026-09-10", 52.18, "SHELL", "Shell", "mch_shell")   # 9 days before as_of: outside lookback alone
    txn(conn, "b", "chk", "2026-09-12", 52.18, "SHELL", "Shell", "mch_shell")   # within the window of a; synced late
    f = next(p for p in detect.detectors() if p.stem == "duplicate_charge")
    assert len(detect.run_one(conn, f, AS_OF)) == 1
