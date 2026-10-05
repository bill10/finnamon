"""Evals for the household's everyday commands in a real claude: normal / dismiss / undo, category and alias, a roundup
reply, /triage and a drafted detector. Assertions are on the database and the filesystem, never on the model's prose.
Skipped without `claude` on PATH. Spends tokens. Run: pytest tests/eval -m eval -s"""
import json
import shutil

import pytest

from finnamon import store
from tests.conftest import AS_OF
from tests.eval.test_budget_skill import fixture_home, run_claude  # noqa: F401 - fixture_home is a fixture

pytestmark = [pytest.mark.eval, pytest.mark.skipif(not shutil.which("claude") or not shutil.which("finnamon"), reason="needs claude and finnamon on PATH")]


def alert(conn, key, kind="anomaly:first_merchant", payload='{"merchant": "Costco"}', tx=None, verdict=None):
    return conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, account_id, payload_json, as_of, sent_at, verdict) VALUES ('anomaly',?,?,?,'chk',?,?,datetime('now','localtime'),?)",
                        (kind, key, tx, payload, AS_OF, verdict)).lastrowid


def test_normal_dismiss_and_undo(fixture_home):
    env = {"FINNAMON_HOME": str(fixture_home)}
    conn = store.connect()
    a, b = alert(conn, "k-a", payload='{"merchant": "Costco", "amount": 310}'), alert(conn, "k-b", payload='{"merchant": "Chipotle", "amount": 41}')
    run_claude(f"Alert {a} (the Costco charge) is normal for us; that's fine from now on. Don't ask me anything.", env)
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (a,)).fetchone()[0] == "normal"
    assert conn.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 1
    run_claude(f"Undo that: reopen alert {a}.", env)
    assert conn.execute("SELECT resolved_at FROM alerts WHERE id=?", (a,)).fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0, "undo removes the rule the normal wrote"
    run_claude(f"Alert {b} is fine this once, dismiss it, but the next Chipotle charge should still alert. Don't ask me anything.", env)
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (b,)).fetchone()[0] == "dismissed"
    assert conn.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0, "a dismissal writes no rule"


def test_category_rule_clear_and_unknown_merchant(fixture_home):
    env = {"FINNAMON_HOME": str(fixture_home)}
    conn = store.connect()
    run_claude("Costco is always groceries for us. Make that the rule; don't ask me anything.", env)
    assert conn.execute("SELECT count(*) FROM category_override").fetchone()[0] == 1
    run_claude("I changed my mind: stop putting Costco in groceries. Remove the rule; don't ask me anything.", env)
    assert conn.execute("SELECT count(*) FROM category_override").fetchone()[0] == 0
    out = run_claude("Zorblax Cafe is always dining out for us. Make that the rule; don't ask me anything.", env)
    assert conn.execute("SELECT count(*) FROM category_override").fetchone()[0] == 0, f"a merchant with no charges got a rule: {out.get('result', '')[:300]}"


def test_alias(fixture_home):
    env = {"FINNAMON_HOME": str(fixture_home)}
    conn = store.connect()
    conn.execute("INSERT INTO transactions (transaction_id, account_id, date, amount, name, pfc_primary, pfc_detailed, pfc_confidence, pending, raw_json) "
                 "VALUES ('fence1','chk','2026-08-20',900,'SQ *PMT 8827','HOME_IMPROVEMENT','HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE','HIGH',0,'{}')")
    run_claude('The charge "SQ *PMT 8827" is the fence contractor. Please show it that way from now on; don\'t ask me anything.', env)
    assert [r[0] for r in conn.execute("SELECT lower(canonical) FROM merchant_alias WHERE name='SQ *PMT 8827'")] == ["fence contractor"]


def test_roundup_reply_normal_n(fixture_home):
    env = {"FINNAMON_HOME": str(fixture_home)}
    conn = store.connect()
    a1, a2 = alert(conn, "k-r1", payload='{"merchant": "Costco"}'), alert(conn, "k-r2", payload='{"merchant": "Chipotle"}')
    for n, aid in ((1, a1), (2, a2)):
        conn.execute("INSERT INTO roundup_items VALUES ('1234567890', 777, ?, ?)", (n, aid))
    conn.execute("UPDATE alerts SET telegram_message_id=777, telegram_chat_id='1234567890' WHERE id IN (?,?)", (a1, a2))
    run_claude("[Telegram, bill, reply to message 777, the Sunday roundup]: normal 2", env)
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (a2,)).fetchone()[0] == "normal"
    assert conn.execute("SELECT resolved_at FROM alerts WHERE id=?", (a1,)).fetchone()[0] is None, "only item 2"


def test_triage_writes_verdicts_with_a_heredoc(fixture_home):
    """The skill has the sentence go on stdin through a quoted heredoc; this proves the allow list and the shell guard let that through."""
    env = {"FINNAMON_HOME": str(fixture_home), "FINNAMON_TRIAGE": "1"}
    conn = store.connect()
    store.set_state(conn, "x", "1")
    conn.execute("INSERT INTO transactions (transaction_id, account_id, date, amount, name, merchant_name, pfc_primary, pfc_detailed, pfc_confidence, pending, raw_json) "
                 "VALUES ('big1','cc','2026-08-27',4200,'WIRE XFER UNKNOWN',NULL,'TRANSFER_OUT','TRANSFER_OUT_ACCOUNT_TRANSFER','HIGH',0,'{}')")
    alert(conn, "anom:big1", kind="anomaly:no_source", payload='{"name": "WIRE XFER UNKNOWN", "amount": 4200}', tx="big1")
    out = run_claude("/triage", env)
    row = conn.execute("SELECT verdict, reason FROM alerts WHERE transaction_id='big1'").fetchone()
    assert row["verdict"] in ("promote", "suppress"), f"no verdict written; result: {out.get('result', '')[:400]}"
    assert any("triage set" in c and "<<" in c for c in out["commands"]), out["commands"]


def test_detector_draft_through_stdin(fixture_home):
    env = {"FINNAMON_HOME": str(fixture_home)}
    out = run_claude("Watch for any single charge over 500 dollars on the Sapphire. Draft the detector; don't ask me anything.", env)
    drafts = list((fixture_home / "detector-drafts").glob("*.sql"))
    assert drafts and "tx" in drafts[0].read_text().lower(), f"no draft written; result: {out.get('result', '')[:400]}"
    assert any("detect --draft" in c and "<<" in c for c in out["commands"]), out["commands"]
