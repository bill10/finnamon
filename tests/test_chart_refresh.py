"""Board charts follow the data (#76): each keeps what drew it, and refresh() reruns that after writes and on page load."""
import json

from finnamon import charts, cli, config, run as runmod, sync
from tests.conftest import seed, today, txn


def _values(id):
    return next(e for e in charts.board() if e["usermeta"]["finnamon"]["id"] == id)["data"]["values"]


def _draw(conn, monkeypatch):
    import io
    seed(conn)
    txn(conn, "t1", "chk", today(), 50, "WHOLEFDS #123", "Whole Foods")
    cli.main(["chart", "--spec", "spend_by_category", "--months", "3"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"title": "By merchant", "mark": "bar"})))
    cli.main(["chart", "--spec-json", "-", "--id", "by-merchant", "--sql",
              "SELECT COALESCE(display, name) m, sum(amount) s FROM tx_now GROUP BY m ORDER BY m"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"title": "Inline", "mark": "bar", "data": {"values": [{"a": 1}]}})))
    cli.main(["chart", "--spec-json", "-", "--id", "inline"])


def test_a_reclassification_refreshes_the_board_to_what_a_fresh_draw_shows(home, conn, monkeypatch, capsys):
    _draw(conn, monkeypatch)
    before = _values("spend_by_category")
    cli.main(["alias", "WHOLEFDS #123", "WFM"])
    cli.main(["category", "WFM", "GENERAL_MERCHANDISE_SUPERSTORES"])
    assert _values("spend_by_category") != before
    assert _values("spend_by_category") == charts.spec(conn, "spend_by_category", None, 3)["data"]["values"], "the stored months, not the default 12"
    fresh = charts.custom(json.dumps({"title": "By merchant", "mark": "bar"}), "by-merchant",
                          "SELECT COALESCE(display, name) m, sum(amount) s FROM tx_now GROUP BY m ORDER BY m")
    assert _values("by-merchant") == fresh["data"]["values"] == [{"m": "WFM", "s": 50.0}]


def test_static_charts_are_marked_and_left_alone(home, conn, monkeypatch, capsys):
    _draw(conn, monkeypatch)
    inline = next(e for e in charts.board() if e["usermeta"]["finnamon"]["id"] == "inline")
    assert inline["usermeta"]["finnamon"]["static"] and "source" not in inline["usermeta"]["finnamon"]
    txn(conn, "t2", "chk", today(), 9, "SOMETHING NEW")
    charts.refresh(conn)
    assert next(e for e in charts.board() if e["usermeta"]["finnamon"]["id"] == "inline") == inline


def test_load_time_refresh_returns_fresh_values_and_rewrites_only_when_one_moved(home, conn, monkeypatch, capsys):
    _draw(conn, monkeypatch)
    p = config.charts_dir() / charts.CURRENT_SPEC
    capsys.readouterr()
    cli.main(["chart", "--refresh"])
    assert json.loads(capsys.readouterr().out)[0]["usermeta"]["finnamon"]["as_of"]
    text = p.read_text()
    charts.refresh(conn)
    assert p.read_text() == text, "nothing moved: the file the dashboard watches is not touched"
    txn(conn, "t2", "chk", today(), 9, "SOMETHING NEW", primary="GENERAL_MERCHANDISE", detailed="GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE")
    cli.main(["chart", "--refresh"])
    shown = json.loads(capsys.readouterr().out)
    assert {"category": "General Merchandise", "amount": 9.0} in shown[0]["data"]["values"]
    assert p.read_text() == text, "the page's load never writes: its watch would re-fetch, and a query on 'now' differs every run"
    charts.refresh(conn)
    assert json.loads(p.read_text())[0]["data"]["values"] == shown[0]["data"]["values"], "a write's refresh does"


def test_sync_and_import_refresh_the_board(home, conn, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(charts, "refresh", lambda c=None: calls.append("refresh") or [])
    monkeypatch.setattr(sync, "sync_all", lambda c: [])
    runmod.cycle(conn, do_triage=False, do_notify=False)
    assert calls == ["refresh"], "the daemon's cycle refreshes after its sync"
    cli.main(["sync"])
    assert len(calls) == 2
    cli.main(["account", "add", "Credit Union Checking", "--institution", "CU"])
    csv = home / "x.csv"
    csv.write_text("Date,Description,Amount\n2026-09-01,COFFEE,-4.50\n")
    cli.main(["import", "Credit Union Checking", str(csv)])
    assert len(calls) == 4
    cli.main(["chart", "--list"]); cli.main(["account", "list"]); cli.main(["budget"]); cli.main(["normal", "--list"])
    assert len(calls) == 4, "reads do not (the dashboard's summary runs account list and budget on every load)"


def test_a_failing_refresh_never_fails_the_write(home, conn, monkeypatch, capsys):
    seed(conn)
    txn(conn, "t1", "chk", today(), 50, "WHOLEFDS #123", "Whole Foods")
    monkeypatch.setattr(charts, "refresh", lambda c=None: 1 / 0)
    cli.main(["alias", "WHOLEFDS #123", "WFM"])
    assert "charts not refreshed" in capsys.readouterr().err


def test_a_garbled_source_does_not_stop_the_rest(home, conn, monkeypatch, capsys):
    _draw(conn, monkeypatch)
    p = config.charts_dir() / charts.CURRENT_SPEC
    board = json.loads(p.read_text())
    board[1]["usermeta"]["finnamon"]["source"] = ["nonsense"]
    board[0]["data"]["values"] = []
    p.write_text(json.dumps(board))
    charts.refresh(conn)
    assert _values("spend_by_category") == [{"category": "Food And Drink", "amount": 50.0}]


def test_stored_sql_still_runs_read_only(home, conn, monkeypatch, capsys):
    _draw(conn, monkeypatch)
    p = config.charts_dir() / charts.CURRENT_SPEC
    board = json.loads(p.read_text())
    before = board[1]["data"]["values"]
    board[1]["usermeta"]["finnamon"]["source"]["spec"]["data"]["sql"] = "DELETE FROM transactions"
    board[1]["usermeta"]["finnamon"]["source"]["spec"]["data"] = {"sql": "WITH x AS (SELECT 1) DELETE FROM transactions"}
    p.write_text(json.dumps(board))
    charts.refresh(conn)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1
    assert _values("by-merchant") == before, "a chart whose query fails keeps its last values"
    assert "not refreshed" in capsys.readouterr().err


def test_a_preset_drawn_before_sources_gets_one_on_the_next_refresh(home, conn):
    seed(conn)
    old = charts.spec(conn, "spend_by_category", None, 6)
    old["usermeta"] = {"finnamon": {"id": "spend_by_category", "chart": "spend_by_category", "arg": None}}
    old["data"]["values"] = [{"category": "Stale", "amount": 1}]
    config.charts_dir().mkdir(parents=True, exist_ok=True)
    (config.charts_dir() / charts.CURRENT_SPEC).write_text(json.dumps([old, {"mark": "bar", "data": {"values": []}}]))
    charts.refresh(conn)
    new, legacy = charts.board()
    assert new["usermeta"]["finnamon"]["source"] == {"preset": "spend_by_category", "arg": None, "months": 6}, "months read back off its title"
    assert new["data"]["values"] == [] and legacy["usermeta"]["finnamon"]["static"]
