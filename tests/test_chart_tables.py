"""Table panels on the chart board: same read-only SQL as a chart's data.sql, same refresh, rows rendered by the page."""
import json
from datetime import date, timedelta

import pytest

from finnamon import budgets, charts, cli, config
from tests.conftest import seed, today, txn

COLS = [{"field": "date", "label": "Date", "format": "date"}, {"field": "m", "label": "Merchant"}, {"field": "amount", "label": "Amount", "format": "money"}]
SQL = "SELECT date, COALESCE(display, name) m, amount FROM tx_now WHERE amount > 50 ORDER BY date DESC"


def ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _table(id):
    return next(e for e in charts.board() if e["usermeta"]["finnamon"]["id"] == id)


def test_a_filtered_table_goes_on_the_board_and_follows_the_data(home, conn, capsys):
    seed(conn)
    txn(conn, "t1", "chk", today(), 80, "CHIPOTLE", "Chipotle")
    txn(conn, "t2", "chk", today(), 12, "COFFEE")
    cli.main(["chart", "--table-json", json.dumps({"title": "Dining over $50", "columns": COLS}), "--sql", SQL])
    t = _table("dining-over-50")
    assert t["table"] == {"columns": [{**COLS[0], "align": "left"}, {**COLS[1], "format": "text", "align": "left"}, {**COLS[2], "align": "right"}],
                          "total": 1, "more": False}
    assert [r["m"] for r in t["data"]["values"]] == ["Chipotle"]
    assert t["usermeta"]["finnamon"]["source"]["table"]["data"] == {"sql": SQL}
    p = config.charts_dir() / charts.CURRENT_SPEC
    text = p.read_text()
    charts.refresh(conn)
    assert p.read_text() == text, "nothing moved: the file the dashboard watches is not touched"
    txn(conn, "t3", "chk", today(), 99, "SUSHI")
    charts.refresh(conn)
    assert _table("dining-over-50")["table"]["total"] == 2, "a new matching row reaches the board"


def test_the_board_keeps_50_rows_and_says_how_many_there_were(home, conn, capsys):
    seed(conn)
    for i in range(60):
        txn(conn, f"t{i}", "chk", today(), 60 + i, f"SHOP {i}")
    t = charts.custom_table(json.dumps({"title": "Big", "columns": COLS}), sql=SQL)
    assert len(t["data"]["values"]) == charts.TABLE_ROWS and t["table"]["total"] == 60 and not t["table"]["more"]


def test_a_tables_sql_is_read_only_and_checked_like_a_charts(home, conn, capsys):
    seed(conn)
    txn(conn, "t1", "chk", today(), 80, "CHIPOTLE")
    for bad in ("DELETE FROM transactions", "WITH x AS (SELECT 1) DELETE FROM transactions", "SELECT * FROM nope"):
        with pytest.raises(ValueError):
            charts.custom_table(json.dumps({"title": "x", "columns": COLS}), sql=bad)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1
    p = config.charts_dir()
    cli.main(["chart", "--table-json", json.dumps({"title": "Big", "columns": COLS}), "--sql", SQL])
    board = json.loads((p / charts.CURRENT_SPEC).read_text())
    board[0]["usermeta"]["finnamon"]["source"]["table"]["data"]["sql"] = "DELETE FROM transactions"
    (p / charts.CURRENT_SPEC).write_text(json.dumps(board))
    charts.refresh(conn)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1 and "not refreshed" in capsys.readouterr().err


@pytest.mark.parametrize("spec, msg", [
    ({"columns": COLS}, "title"),
    ({"title": "x", "columns": []}, "columns"),
    ({"title": "x", "columns": [{"field": "m", "format": "html"}]}, "format"),
    ({"title": "x", "columns": [{"field": "m", "onclick": "x"}]}, "a column is"),
    ({"title": "x", "columns": [{"field": "nope"}]}, "not in the rows"),
    ({"title": "x", "columns": COLS, "mark": "bar"}, "not mark"),
    ({"title": "x", "columns": COLS, "data": {"url": "http://x"}}, "data is"),
])
def test_bad_table_specs_are_refused(home, conn, spec, msg):
    seed(conn)
    txn(conn, "t1", "chk", today(), 80, "CHIPOTLE")
    with pytest.raises(ValueError, match=msg):
        charts.custom_table(json.dumps(spec), sql=None if "data" in spec else SQL)


def test_inline_rows_are_static(home):
    t = charts.custom_table(json.dumps({"title": "Mine", "columns": [{"field": "a"}], "data": {"values": [{"a": 1}]}}))
    assert t["usermeta"]["finnamon"]["static"] and "source" not in t["usermeta"]["finnamon"]
    with pytest.raises(ValueError):
        charts.custom_table(json.dumps({"title": "Mine", "columns": [{"field": "a"}], "data": {"values": [{"a": {"b": 1}}]}}))


def test_table_presets(home, conn, capsys):
    seed(conn)
    txn(conn, "t1", "chk", today(), 80, "CHIPOTLE", "Chipotle")
    txn(conn, "t2", "chk", today(), 900, "BEST BUY", "Best Buy")
    txn(conn, "old", "chk", "2020-01-01", 5000, "ANCIENT")
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, last_amount, last_date, predicted_next_date, status, is_active, first_seen_at) "
                 f"VALUES ('s1','chk','outflow','Netflix','MONTHLY',15.49,'{ago(20)}','{ago(-10)}','MATURE',1,'2026-06-01'), "
                 "('s2','chk','outflow','Gone','MONTHLY',9,'2026-01-12','2026-02-12','MATURE',0,'2026-06-01')")
    for name in charts.TABLES:
        cli.main(["chart", "--spec", name])
    recent, large, rec = (_table(i) for i in ("recent_transactions", "large_transactions", "recurring"))
    assert [r["merchant"] for r in recent["data"]["values"]] == ["Chipotle", "Best Buy"] and recent["usermeta"]["finnamon"]["chart"] == "recent_transactions"
    assert [r["merchant"] for r in large["data"]["values"]] == ["Best Buy"]
    assert [(r["merchant"], r["ended"]) for r in rec["data"]["values"]] == [("Netflix", 0), ("Gone", 1)]
    assert charts.spec(conn, "large_transactions", "50", 12)["table"]["total"] == 2
    with pytest.raises(ValueError):
        charts.spec(conn, "large_transactions", "lots")
    with pytest.raises(ValueError, match="a table"):
        charts.render(conn, "recurring")
    cli.main(["chart", "--remove", "recurring"])
    assert "recurring" not in [e["usermeta"]["finnamon"]["id"] for e in charts.board()]


def test_a_chart_spec_cannot_pass_as_a_table(home):
    with pytest.raises(ValueError, match="table"):
        charts.custom(json.dumps({"title": "t", "table": {"columns": "x"}, "mark": "bar", "data": {"values": [{"a": 1}]}}))


def test_recurring_table_marks_ended_streams_by_their_own_interval(home, conn):
    seed(conn)
    rows = [("live-m", "MONTHLY", ago(40), 1, "MATURE"), ("late-m", "MONTHLY", ago(50), 1, "MATURE"),
            ("live-y", "ANNUALLY", ago(390), 1, "MATURE"), ("late-y", "ANNUALLY", ago(410), 1, "MATURE"),
            ("inactive", "MONTHLY", ago(5), 0, "MATURE"), ("tomb", "MONTHLY", ago(5), 1, "TOMBSTONED"),
            ("early", "MONTHLY", ago(5), 1, "EARLY_DETECTION")]
    for n, f, last, act, st in rows:
        conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, last_amount, last_date, status, is_active, first_seen_at) "
                     "VALUES (?, 'chk', 'outflow', ?, ?, 10, ?, ?, ?, '2026-01-01')", (n, n, f, last, st, act))
    got = {r["merchant"]: r["ended"] for r in charts.table_preset("recurring")["data"]["values"]}
    assert got == {"live-m": 0, "late-m": 1, "live-y": 0, "late-y": 1, "inactive": 1, "tomb": 1}, "early detections stay out entirely"
    live = {c["merchant"] for c in budgets.suggest(conn)["recurring"]}
    assert live == {"live-m", "live-y"}
    assert {c["merchant"] for c in budgets.suggest(conn, include_ended=True)["recurring"]} == set(got)
