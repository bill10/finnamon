"""Eval: the assistant puts a filtered transaction table on the dashboard's board. Spends tokens. Run: pytest tests/eval -m eval -s"""
from finnamon import charts, store
from tests.eval.test_budget_skill import fixture_home, pytestmark, run_claude  # noqa: F401 - the fixture and marks are shared


def test_a_filtered_transaction_table(fixture_home):
    out = run_claude("Put a table on the dashboard of every Chipotle charge over $50, newest first.", {"FINNAMON_HOME": str(fixture_home)})
    tables = [e for e in charts.board() if "table" in e]
    assert tables, f"no table on the board; result was: {out.get('result', '')[:500]}"
    t = tables[-1]
    want = store.connect().execute("SELECT count(*) FROM tx_now WHERE merchant_name='Chipotle' AND amount > 50").fetchone()[0]
    assert t["table"]["total"] == want, (t["table"], want)
    money = [c["field"] for c in t["table"]["columns"] if c["format"] == "money"]
    dates = [c["field"] for c in t["table"]["columns"] if c["format"] == "date"]
    assert money and dates, t["table"]["columns"]
    rows = t["data"]["values"]
    assert all(r[money[0]] > 50 for r in rows)
    assert [r[dates[0]] for r in rows] == sorted((r[dates[0]] for r in rows), reverse=True)
    assert "source" in t["usermeta"]["finnamon"], "drawn from SQL, so it refreshes after syncs"
