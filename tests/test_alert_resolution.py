"""Resolving an alert from the chat or the dashboard (issue 74): `normal --alert` records its rule, `alerts --dismiss`
writes none, `alerts --undo` reopens either and removes only the rule that alert wrote; `alerts --open/--resolved` split the list."""
import json

import pytest

from finnamon import cli
from tests.conftest import AS_OF, seed, txn


def run(capsys, *argv):
    cli.main(list(argv))
    return json.loads(capsys.readouterr().out)


def alert(conn, key, kind="anomaly:first_merchant", payload='{"merchant": "Costco"}', account="chk"):
    return conn.execute("INSERT INTO alerts (tier, kind, key, account_id, payload_json, as_of, sent_at) VALUES ('anomaly',?,?,?,?,?,datetime('now','localtime'))",
                        (kind, key, account, payload, AS_OF)).lastrowid


def row(conn, aid):
    return dict(conn.execute("SELECT resolved_at, resolution, suppression_id FROM alerts WHERE id=?", (aid,)).fetchone())


def rules(conn):
    return [r[0] for r in conn.execute("SELECT id FROM suppressions ORDER BY id")]


def test_normal_records_its_rule_and_undo_removes_exactly_that_one(conn, capsys):
    seed(conn)
    txn(conn, "t1", "chk", "2026-09-10", 30, "ACME CO", "Acme")
    other = run(capsys, "normal", "Acme")["id"]                 # a rule the household wrote on its own
    a = alert(conn, "k1")
    rule = run(capsys, "normal", "--alert", str(a))["id"]
    assert row(conn, a)["resolution"] == "normal" and row(conn, a)["suppression_id"] == rule and row(conn, a)["resolved_at"]
    assert [x["id"] for x in run(capsys, "alerts", "--resolved")] == [a] and run(capsys, "alerts", "--open") == []
    out = run(capsys, "alerts", "--undo", str(a))
    assert out["undone"] == "normal" and out["removed_rule"]["id"] == rule
    assert rules(conn) == [other], "the household's own rule stays"
    assert row(conn, a) == {"resolved_at": None, "resolution": None, "suppression_id": None}
    assert [x["id"] for x in run(capsys, "alerts", "--open")] == [a]


def test_recurring_changed_undo_removes_the_stream_rule_only(conn, capsys):
    seed(conn)
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, status, first_seen_at) VALUES ('s1','chk','outflow','Netflix','TOMBSTONED',?)", (AS_OF,))
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, status, first_seen_at) VALUES ('s2','chk','outflow','Netflix','MATURE',?)", (AS_OF,))
    a1 = alert(conn, "anom:rec:s1:2026-09", "anomaly:recurring_changed", '{"merchant": "Netflix"}')
    a2 = alert(conn, "anom:rec:s2:2026-09", "anomaly:recurring_changed", '{"merchant": "Netflix"}')
    r1 = run(capsys, "normal", "--alert", str(a1)); r2 = run(capsys, "normal", "--alert", str(a2))
    assert (r1["stream_id"], r2["stream_id"]) == ("s1", "s2")    # the per-stream acknowledgement (#64/#70), unchanged
    run(capsys, "alerts", "--undo", str(a1))
    assert [dict(r) for r in conn.execute("SELECT stream_id FROM suppressions")] == [{"stream_id": "s2"}]


def test_dismiss_writes_no_rule_and_undo_reopens(conn, capsys):
    seed(conn)
    a = alert(conn, "k1")
    assert run(capsys, "alerts", "--dismiss", str(a)) == {"id": a, "resolution": "dismissed"}
    assert rules(conn) == [] and row(conn, a)["resolution"] == "dismissed"
    [r] = run(capsys, "alerts", "--resolved")
    assert (r["resolution"], r["suppression_id"]) == ("dismissed", None)
    assert run(capsys, "alerts", "--undo", str(a))["removed_rule"] is None
    assert row(conn, a)["resolved_at"] is None


def test_refusals(conn, capsys):
    seed(conn)
    a, b = alert(conn, "k1"), alert(conn, "k2")
    for argv, msg in ((("alerts", "--dismiss", "999"), "no alert 999"), (("alerts", "--undo", str(a)), "not resolved")):
        with pytest.raises(SystemExit):
            cli.main(list(argv))
        assert msg in capsys.readouterr().err
    run(capsys, "alerts", "--dismiss", str(a))
    with pytest.raises(SystemExit):   # a second click, or the chat got there first
        cli.main(["alerts", "--dismiss", str(a)])
    assert "already resolved" in capsys.readouterr().err
    conn.execute("UPDATE alerts SET resolved_at=datetime('now','localtime') WHERE id=?", (b,))   # Finnamon resolved it (link.py), or before 009
    with pytest.raises(SystemExit):
        cli.main(["alerts", "--undo", str(b)])
    assert "nothing to undo" in capsys.readouterr().err


def test_a_rule_removed_by_hand_is_never_undone_into_a_stranger(conn, capsys):
    seed(conn)
    a = alert(conn, "k1")
    rule = run(capsys, "normal", "--alert", str(a))["id"]
    run(capsys, "normal", "--remove", str(rule))
    txn(conn, "t1", "chk", "2026-09-10", 30, "ACME CO", "Acme")
    stranger = run(capsys, "normal", "Acme")["id"]
    assert stranger == rule, "sqlite reuses the freed id"
    assert run(capsys, "alerts", "--undo", str(a))["removed_rule"] is None
    assert rules(conn) == [stranger]


def test_triage_stays_read_only(conn, capsys, monkeypatch):
    seed(conn)
    a = alert(conn, "k1")
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    with pytest.raises(SystemExit):
        cli.main(["alerts", "--dismiss", str(a)])
    assert row(conn, a)["resolved_at"] is None


def test_a_queued_alert_resolved_on_the_page_is_never_sent(conn, capsys):
    from finnamon import notify
    seed(conn)
    a = conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','budget_pace','k-q','{}',?)", (AS_OF,)).lastrowid
    assert [r["id"] for r in notify.pending(conn)] == [a]
    run(capsys, "alerts", "--dismiss", str(a))
    assert notify.pending(conn) == []
    assert [x["id"] for x in run(capsys, "alerts", "--sent", "--resolved")] == [a], "still listed under Resolved, so the page can undo it"
    run(capsys, "alerts", "--undo", str(a))
    assert [r["id"] for r in notify.pending(conn)] == [a], "reopened: queued again"


def test_normal_on_a_resolved_alert_is_refused_so_undo_stays_whole(conn, capsys):
    seed(conn)
    a = alert(conn, "k1")
    run(capsys, "normal", "--alert", str(a))
    with pytest.raises(SystemExit):   # a stale tab, or the chat a second time
        cli.main(["normal", "--alert", str(a)])
    assert "already resolved" in capsys.readouterr().err
    assert len(rules(conn)) == 1


@pytest.mark.parametrize("argv", [["--dismiss", "1", "--undo", "2"], ["--untriaged", "--dismiss", "1"], ["--dismiss", "1", "--open"], ["--open", "--resolved"]])
def test_conflicting_flags_are_refused(conn, argv):
    with pytest.raises(SystemExit):
        cli.main(["alerts", *argv])
