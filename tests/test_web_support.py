"""What the web dashboard leans on: chart specs, in-page Link, the scheduler's web job."""
import json

import pytest

from finnamon import budgets, charts, config, link, plaid_api, scheduler, secrets, store, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, REAL_UNIT_DIR, seed, txn


def test_spec_names_its_chart_for_the_dashboard(conn):
    fm = charts.spec(conn, "budgets")["usermeta"]["finnamon"]
    assert {k: fm[k] for k in ("id", "chart", "arg", "source")} == {"id": "budgets", "chart": "budgets", "arg": None,
                                                                     "source": {"preset": "budgets", "arg": None, "months": 12}} and fm["as_of"]
    assert charts.spec(conn, "merchant_history", "Whole Foods")["usermeta"]["finnamon"]["id"] == "merchant_history-whole-foods"
    # an id the page cannot name is a chart its × and chips cannot take off; server.js DELETE /api/chart/:id has the same 64
    long = charts.spec(conn, "merchant_history", "SQ *The Extremely Long Downtown Coffee Roastery and Bakery Company")
    assert charts.ID.fullmatch(long["usermeta"]["finnamon"]["id"])


def test_every_chart_has_a_spec(conn):
    seed(conn)
    for i in range(6):
        txn(conn, f"c{i}", "chk", f"2026-0{4 + i % 5}-1{i}", 40 + i, "COSTCO WHSE", "Costco")
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1000, 900)", (AS_OF,))
    budgets.budget_set(conn, "groceries", 600, "FOOD_AND_DRINK_GROCERIES")
    for name in charts.CHARTS:
        s = charts.spec(conn, name, "costco" if name == "merchant_history" else None, 12)
        assert s["$schema"].startswith("https://vega.github.io/schema/vega-lite/") and s["title"] and "values" in s["data"]
        json.dumps(s)   # serialisable, nothing sqlite-shaped leaked
    assert charts.spec(conn, "merchant_history", "costco")["data"]["values"][0]["merchant"] == "Costco"
    with pytest.raises(ValueError):
        charts.spec(conn, "merchant_history")
    with pytest.raises(ValueError):
        charts.spec(conn, "nope")


def test_chart_spec_writes_current_json_atomically(home, conn, capsys):
    from finnamon import cli
    seed(conn)
    cli.main(["chart", "--spec", "monthly_in_out", "--months", "3"])
    p = config.charts_dir() / charts.CURRENT_SPEC
    assert str(p) in capsys.readouterr().out and json.loads(p.read_text())[0]["title"].endswith("last 3 months")
    assert not list(config.charts_dir().glob("*.tmp"))


def _spec_json(monkeypatch, spec, *extra):
    import io
    from finnamon import cli
    monkeypatch.setattr("sys.stdin", io.StringIO(spec if isinstance(spec, str) else json.dumps(spec)))
    cli.main(["chart", "--spec-json", "-", *extra])


def test_the_board_is_a_list_the_assistant_owns(home, conn, capsys, monkeypatch):
    """Presets and whole specs share one list: same id replaces in place, --remove and --clear take them off, the oldest falls off."""
    from finnamon import cli
    seed(conn)
    ids = lambda: [e["usermeta"]["finnamon"]["id"] for e in charts.board()]
    cli.main(["chart", "--spec", "budgets"]); cli.main(["chart", "--spec", "monthly_in_out"])
    _spec_json(monkeypatch, {"title": "Dining by month", "mark": "bar", "data": {"values": [{"a": 1}]}})
    assert ids() == ["budgets", "monthly_in_out", "dining-by-month"]
    cli.main(["chart", "--spec", "monthly_in_out", "--months", "3"])   # a preset asked for again updates where it stands
    _spec_json(monkeypatch, {"title": "Dining, weekly", "mark": "line", "data": {"values": []}}, "--id", "dining-by-month")
    assert ids() == ["budgets", "monthly_in_out", "dining-by-month"]
    assert charts.board()[1]["title"].endswith("last 3 months") and charts.board()[2]["mark"] == "line"
    cli.main(["chart", "--remove", "budgets"])
    assert ids() == ["monthly_in_out", "dining-by-month"]
    with pytest.raises(SystemExit):
        cli.main(["chart", "--remove", "budgets"])
    assert "no chart 'budgets'" in capsys.readouterr().err
    cli.main(["chart", "--clear"])
    for i in range(charts.MAX_CHARTS):
        _spec_json(monkeypatch, {"mark": "bar", "data": {"values": []}}, "--id", f"c{i}")
    assert ids() == [f"c{i}" for i in range(charts.MAX_CHARTS)]
    with pytest.raises(SystemExit):   # a ninth is refused with the way out, not dropped silently
        _spec_json(monkeypatch, {"mark": "bar", "data": {"values": []}}, "--id", "c9")
    assert "8 charts at most; remove one" in capsys.readouterr().err and ids() == [f"c{i}" for i in range(charts.MAX_CHARTS)]
    _spec_json(monkeypatch, {"mark": "line", "data": {"values": []}}, "--id", "c3")   # replacing one in place is not a ninth
    cli.main(["chart", "--clear"])
    assert charts.board() == []
    with pytest.raises(SystemExit):
        cli.main(["chart"])


def test_a_chart_no_preset_covers_needs_only_a_spec_with_data_sql(home, conn, monkeypatch):
    """The point of --spec-json: the assistant draws any chart, rows from read-only SQL over tx_now, without touching Python."""
    seed(conn)
    for i, (d, amt) in enumerate((("2026-07-03", 40), ("2026-07-20", 60), ("2026-08-05", 25))):
        txn(conn, f"d{i}", "chk", d, amt, "CAFE", "Cafe", None, "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    txn(conn, "g", "chk", "2026-07-04", 99, "SHELL", "Shell", None, "TRANSPORTATION", "TRANSPORTATION_GAS")
    sql = "SELECT strftime('%Y-%m',date) ym, round(sum(amount),2) spent FROM tx_now WHERE amount>0 AND pending=0 AND category_primary='FOOD_AND_DRINK' GROUP BY ym ORDER BY ym"
    _spec_json(monkeypatch, {"title": "Dining by month", "layer": [{"mark": "bar"}, {"mark": "text"}], "data": {"sql": sql},
                             "encoding": {"x": {"field": "ym", "type": "ordinal"}, "y": {"field": "spent", "type": "quantitative"}}})
    e = charts.board()[0]
    assert e["data"] == {"values": [{"ym": "2026-07", "spent": 100.0}, {"ym": "2026-08", "spent": 25.0}]}
    assert e["$schema"] == charts.VEGA_LITE and e["usermeta"]["finnamon"]["id"] == "dining-by-month" and e["width"] == "container"
    _spec_json(monkeypatch, {"title": "Layered", "layer": [{"mark": "bar", "data": {"sql": "SELECT 1 a"}},
                                                     {"mark": "rule", "transform": [{"lookup": "a", "from": {"data": {"sql": "SELECT 1 a, 2 b"}, "key": "a", "fields": ["b"]}}]}]})
    layered = charts.board()[-1]
    assert layered["layer"][0]["data"] == {"values": [{"a": 1}]} and layered["layer"][1]["transform"][0]["from"]["data"] == {"values": [{"a": 1, "b": 2}]}
    with pytest.raises(SystemExit):   # read-only: the SQL cannot write, whatever it says
        _spec_json(monkeypatch, {"mark": "bar", "data": {"sql": "DELETE FROM transactions"}})
    with pytest.raises(SystemExit):
        _spec_json(monkeypatch, {"mark": "bar", "data": {"sql": "WITH x AS (SELECT 1) DELETE FROM transactions"}})
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 4
    # inline spec and --sql apart: a heredoc of JSON trips Claude Code's shell guard, a single-quoted argument does not
    from finnamon import cli
    cli.main(["chart", "--spec-json", '{"title": "Inline", "mark": "bar"}', "--sql", sql, "--id", "inline"])
    assert charts.board()[-1]["data"] == e["data"]
    with pytest.raises(SystemExit):   # --sql alone is a mistake, not a no-op
        cli.main(["chart", "budgets", "--spec", "--sql", sql])


@pytest.mark.parametrize("spec, why", [
    ({"mark": "bar", "data": {"url": "https://evil.example/x.json"}}, "data.url"),
    ({"layer": [{"mark": "bar", "data": {"url": "https://evil.example/?q=COSTCO"}}]}, "data.url"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"lookup": "k", "from": {"data": {"url": "https://evil.example"}, "key": "k"}}]}, "data.url"),
    ({"mark": "point", "data": {"values": []}, "encoding": {"url": {"value": "https://evil.example/p.png"}}}, "encoding.url"),
    ({"mark": {"type": "image"}, "data": {"values": []}}, "image mark"),
    ({"layer": [{"mark": "image"}], "data": {"values": []}}, "image mark"),
    ({"mark": "point", "data": {"values": []}, "params": [{"name": "p", "bind": {"input": "range", "element": "#intercom"}}]}, "params.bind"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"calculate": "datum.a + 1", "as": "b"}]}, "transform.calculate"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"filter": "datum.a > 0"}]}, "filter takes a predicate object"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"filter": {"not": "datum.a > 0"}}]}, "not takes a predicate object"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"filter": {"and": [{"field": "a", "gt": 0}, "datum.b"]}}]}, "and takes a predicate object"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"color": {"condition": {"test": "datum.a > 0", "value": "red"}, "value": "blue"}}}, "test takes a predicate object"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"x": {"datum": {"expr": "now()"}}}}, "datum.expr"),
    ({"mark": "bar", "data": {"values": []}, "title": {"text": {"signal": "width"}}}, "text.signal"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"x": {"field": "a", "axis": {"labelExpr": "datum.label"}}}}, "axis.labelExpr"),
    ({"mark": "bar", "data": {"values": []}, "params": [{"name": "p", "expr": "1"}]}, "params.expr"),
    ({"mark": "bar", "data": {"values": []}, "params": [{"name": "brush", "select": "interval"}]}, "params.select"),
    ({"mark": "bar", "data": {"values": []}, "params": [{"name": "p", "value": 1, "bind": {"input": "range"}}]}, "params.bind"),
    ({"layer": [{"mark": "bar", "encoding": {"y": {"field": "a", "type": "quantitative"}}}, {"mark": "text", "encoding": {"text": {"value": {"expr": "datum.a"}}}}], "data": {"values": []}}, "value.expr"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"x": {"field": "a", "axis": {"values": {"expr": "[1,2]"}}}}}, "values.expr"),   # an axis's values are spec, not rows
    ({"mark": "bar", "data": {"values": []}, "params": [{"name": "p", "value": 1, "on": [{"events": "click", "update": "1"}]}]}, "params.on"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"tooltip": {"field": "a", "format": "\") + warn(\"x"}}}, "format is a d3 format string"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"tooltip": {"field": "a", "formatType": "evil"}}}, "formatType is one of"),
    ({"mark": "bar", "data": {"values": []}, "config": {"customFormatTypes": True, "numberFormat": "'"}}, "config.customFormatTypes"),
    ({"mark": "bar", "data": {"values": []}, "config": {"numberFormat": "'"}}, "numberFormat is a d3 format string"),
    ({"mark": "bar", "data": {"sequence": {"start": 0, "stop": 1000000000}}}, "data.sequence"),
    ({"mark": "bar", "data": {"values": "a,b\n1,2", "format": {"type": "csv"}}}, "data.values is a list of rows"),
    ({"mark": "bar", "data": {"values": []}, "config": {"signals": [{"name": "s", "update": "warn(1)"}]}}, "config.signals"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"impute": "a", "key": "b", "keyvals": {"start": 0, "stop": 1000000000}}]}, "transform.impute"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"y": {"field": "a", "impute": {"value": 0}}}}, "y.impute"),
    ({"mark": "bar", "data": {"values": []}, "config": {"timeFormatType": "evil"}}, "timeFormatType is one of"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"tooltip": {"field": "a", "formatType": "utc"}}}, "formatType is one of"),   # Vega-Lite treats utc as custom
    ({"mark": "bar", "data": {"values": []}, "config": {"normalizedNumberFormat": "\""}}, "normalizedNumberFormat is a d3 format"),
    ({"mark": "bar", "data": {"values": [], "format": {"parse": {"d": "date:'%Y') + warn(1"}}}}, "parse takes date"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"filter": {"field": "d", "timeUnit": "hours", "equal": "1) || warn(1"}}]}, "under a timeUnit is a date or a number"),
    ({"mark": "bar", "data": {"values": []}, "transform": [{"filter": {"field": "d", "timeUnit": "hours", "range": [1, "2) || warn(1"]}}]}, "under a timeUnit is a date or a number"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"x": {"datum": {"year": "2026) || warn(1"}}}}, "a date's year is a number"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"x": {"field": "d", "scale": {"domain": [{"year": 2026, "hours": "x("}, {"year": 2027}]}}}}, "a date's hours is a number"),
    ({"mark": "bar", "data": {"name": "rows"}, "datasets": {"rows": "a,b\n1,2"}}, "a dataset is a list of rows"),
    ({"mark": "bar", "data": {"sql": "SELEC nonsense"}}, "SELECT"),
    ({"mark": "bar", "data": {"sql": "SELECT nope FROM tx_now"}}, "data.sql: no such column"),
    ({"mark": "bar", "data": {"sql": "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n LIMIT 5001) SELECT i FROM n"}}, "over 5000 rows"),
    ({"mark": "bar", "data": {"values": []}, "encoding": {"href": {"field": "a"}}}, "encoding.href"),
    ({"$schema": "https://vega.github.io/schema/vega/v5.json", "signals": []}, "Vega-Lite v5"),
    ({"$schema": "https://vega.github.io/schema/vega-lite/v4.json", "mark": "bar"}, "Vega-Lite v5"),
    ({"mark": "bar", "data": {"values": [{"s": "x" * charts.MAX_BYTES}]}}, "over 256 KB"),
    ("[1, 2]", "one JSON object"),
    ("{not json", "not JSON"),
    ({"mark": "bar", "data": {"values": []}}, None),   # --id below is refused
])
def test_a_spec_that_could_fetch_or_escape_vega_lite_is_refused(home, conn, monkeypatch, capsys, spec, why):
    """Merchant names and memos flow into these specs and are data, never instructions: nothing in one may load a URL."""
    with pytest.raises(SystemExit):
        _spec_json(monkeypatch, spec, *(("--id", "../x") if why is None else ()))
    assert (why or "--id") in capsys.readouterr().err and charts.board() == []


# What the assistant really writes (the skill's example, the presets' shapes, a layered bar+label, a predicate filter, a fold):
# all expression-free, so the check above costs nothing the product draws.
REAL_SPECS = [
    {"title": "Dining by month", "mark": "bar", "encoding": {"x": {"field": "ym", "type": "ordinal", "title": None}, "y": {"field": "spent", "type": "quantitative", "title": "$"},
                                                             "tooltip": [{"field": "ym"}, {"field": "spent", "format": "$,.0f"}]}},
    {"title": "Groceries vs dining", "mark": "line", "transform": [{"fold": ["groceries", "dining"], "as": ["kind", "spent"]}],
     "encoding": {"x": {"field": "ym", "type": "ordinal"}, "y": {"field": "spent", "type": "quantitative"}, "color": {"field": "kind", "title": None}}},
    {"title": "Top merchants", "transform": [{"filter": {"field": "spent", "gt": 0}}, {"window": [{"op": "rank", "as": "r"}], "sort": [{"field": "spent", "order": "descending"}]}],
     "layer": [{"mark": "bar", "encoding": {"x": {"field": "spent", "type": "quantitative"}, "y": {"field": "merchant", "type": "nominal", "sort": "-x"}}},
               {"mark": {"type": "text", "align": "left", "dx": 4}, "encoding": {"x": {"field": "spent", "type": "quantitative"}, "y": {"field": "merchant", "type": "nominal", "sort": "-x"},
                                                                                "text": {"field": "spent", "format": "$,.0f"}}}]},
    {"title": "Spend by category", "mark": "arc", "encoding": {"theta": {"field": "spent", "type": "quantitative"}, "color": {"field": "category", "type": "nominal"}}},
    {"title": "Weekly", "repeat": {"layer": ["in", "out"]}, "spec": {"mark": "line", "encoding": {"x": {"field": "week", "type": "temporal"}, "y": {"field": {"repeat": "layer"}, "type": "quantitative"}}}},
    {"title": "By month and kind", "facet": {"field": "kind", "type": "nominal"}, "spec": {"mark": "bar", "encoding": {"x": {"field": "ym", "type": "ordinal"}, "y": {"aggregate": "sum", "field": "spent", "type": "quantitative"}}}},
    {"title": "Flagged", "mark": "point", "encoding": {"x": {"field": "date", "type": "temporal"}, "y": {"field": "amount", "type": "quantitative"},
                                                        "color": {"condition": {"test": {"field": "flag", "equal": 1}, "value": "red"}, "value": "grey"}}},
    {"title": "Formats", "mark": "bar", "encoding": {"x": {"field": "ym", "type": "temporal", "axis": {"format": "%b %Y", "formatType": "time", "values": ["2026-01-01"]}},
                                                     "y": {"field": "spent", "type": "quantitative", "axis": {"format": "$,.0f"}},
                                                     "tooltip": [{"field": "spent", "format": "$,.2f", "formatType": "number"}]}},
    {"title": "This year by month", "mark": "bar", "transform": [{"filter": {"field": "ym", "timeUnit": "year", "equal": 2026}},
                                                                 {"filter": {"field": "ym", "timeUnit": "yearmonth", "range": ["2026-01-01", "2026-06-30T23:59:59"]}},
                                                                 {"filter": {"field": "ym", "timeUnit": "month", "oneOf": ["jan", "feb"]}}],
     "encoding": {"x": {"field": "ym", "type": "temporal", "timeUnit": "month", "scale": {"domain": [{"year": 2026, "month": "jan"}, {"year": 2026, "month": "dec", "date": 31}]}},
                  "y": {"field": "spent", "type": "quantitative"}}},
    {"title": "Named rows", "mark": "bar", "data": {"name": "rows"}, "datasets": {"rows": [{"url": "kept", "ym": "2026-01", "spent": 1}]},
     "encoding": {"x": {"field": "ym", "type": "ordinal"}, "y": {"field": "spent", "type": "quantitative"}}},
]


@pytest.mark.parametrize("sp", REAL_SPECS, ids=lambda sp: sp["title"])
def test_the_charts_the_assistant_really_writes_pass_the_expression_check(home, conn, sp):
    seed(conn)
    out = charts.custom(json.dumps({**sp, "data": sp.get("data", {"values": [{"ym": "2026-01", "spent": 1, "merchant": "m", "category": "c", "flag": 1}]})}))
    assert out["usermeta"]["finnamon"]["id"] == charts.slug(sp["title"])
    for name in charts.CHARTS:   # the presets go through the same check
        charts._check(charts.spec(conn, name, "costco" if name == "merchant_history" else None))


def test_a_spec_cannot_carry_embed_options_that_override_the_page(home, conn, monkeypatch):
    _spec_json(monkeypatch, {"mark": "bar", "data": {"values": []}, "usermeta": {"embedOptions": {"mode": "vega"}}}, "--id", "x")
    fm = charts.board()[0]["usermeta"].pop("finnamon")
    assert charts.board()[0]["usermeta"].keys() == {"finnamon"} and fm.pop("as_of") and fm == {"id": "x", "chart": None, "arg": None, "static": True}


def test_a_url_column_in_the_rows_is_just_data(home, conn, monkeypatch):
    _spec_json(monkeypatch, {"mark": "bar", "data": {"values": [{"url": "x", "href": "y"}]}}, "--id", "rows")
    assert charts.board()[0]["data"]["values"] == [{"url": "x", "href": "y"}]


def test_list_shows_the_board_and_a_garbled_file_is_an_empty_one(home, conn, capsys):
    from finnamon import cli
    config.charts_dir().mkdir(parents=True, exist_ok=True)
    (config.charts_dir() / charts.CURRENT_SPEC).write_text('[{"mark"')
    assert charts.board() == []
    assert charts.custom('{"title": ["Dining", 2026], "mark": "bar"}')["usermeta"]["finnamon"]["id"] == "dining-2026"
    seed(conn)
    cli.main(["chart", "--spec", "budgets"]); capsys.readouterr()
    cli.main(["chart", "--list"])
    assert json.loads(capsys.readouterr().out) == [{"id": "budgets", "title": "Budgets, month to date"}]


def test_a_legacy_single_spec_file_is_a_board_of_one(home, conn):
    config.charts_dir().mkdir(parents=True, exist_ok=True)
    (config.charts_dir() / charts.CURRENT_SPEC).write_text(json.dumps({"mark": "bar", "usermeta": {"finnamon": {"chart": "budgets", "arg": None}}}))
    assert [e["usermeta"]["finnamon"]["id"] for e in charts.board()] == ["budgets"]
    seed(conn)
    charts.write_spec(conn, "budgets")   # the upgraded file's first write replaces it in place rather than doubling it
    assert len(charts.board()) == 1 and charts.board()[0]["title"].startswith("Budgets")
    (config.charts_dir() / charts.CURRENT_SPEC).write_text(json.dumps({"mark": "bar", "usermeta": {"finnamon": {"chart": "merchant_history", "arg": "Costco"}}}))
    assert charts.board()[0]["usermeta"]["finnamon"]["id"] == "merchant_history-costco", "the id the same preset gets today, so it replaces rather than doubles"


def test_in_page_link_token_and_public_token(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    bodies = []
    monkeypatch.setattr(plaid_api, "call", lambda path, body, **k: bodies.append((path, body)) or {"link_token": "lt-plain"})
    cli.main(["link", "--token", "https://mac.tailnet.ts.net/"])
    assert json.loads(capsys.readouterr().out) == {"link_token": "lt-plain"}
    path, body = bodies[-1]
    assert path == "/link/token/create" and body["redirect_uri"] == "https://mac.tailnet.ts.net/" and "hosted_link" not in body   # plain Link JS token
    cli.main(["link", "--token"])   # plain http page: Plaid refuses a non-HTTPS redirect, so none is sent and OAuth uses a popup
    assert json.loads(capsys.readouterr().out) == {"link_token": "lt-plain"}
    path, body = bodies[-1]
    assert path == "/link/token/create" and "redirect_uri" not in body and "hosted_link" not in body
    with pytest.raises(ValueError, match="hosted=False"):   # a redirect on Hosted Link is a programming error, refused before any Plaid call
        plaid_api.link_token_create("finnamon-bill", redirect_uri="https://mac.tailnet.ts.net/")
    assert bodies[-1] == (path, body)
    # the page hands back the public token; register, sync, announce
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_3", "institution_name": "Chase"}})
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 2, "transactions": 9, "recurring": 0, "holdings": 0, "error": None})
    store.set_state(conn, "chat_id", 555)
    cli.main(["link", "--public-token", "public-sandbox-abc"])
    r = json.loads(capsys.readouterr().out)
    assert r["state"] == "linked" and r["institution"] == "Chase" and r["announced"] and "Linked Chase" in tg.sent[-1]["text"]
    # neither form is the assistant's
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    for argv in (["link", "--token", "http://x/"], ["link", "--token"], ["link", "--public-token", "public-sandbox-abc"],
                 ["link", "--finish", "--public-token", "public-sandbox-abc"]):   # --start is allow-listed; the flags are not (--start --token is argparse's, below)
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 1   # the gate, not a usage error
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE", raising=False)   # at a terminal too: argparse, not the gate, keeps --token off --start
    n = len(bodies)
    for argv in (["link", "--start", "--token"], ["link", "--start", "--token", "https://mac.tailnet.ts.net/"], ["link", "--finish", "--token"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2 and len(bodies) == n   # usage error; Plaid was not called


def test_scheduler_adds_the_web_job_only_when_installed(household_home, monkeypatch, tmp_path):
    (tmp_path / "web" / "node_modules").mkdir(parents=True); (tmp_path / "web" / "server.js").write_text("")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(scheduler.shutil, "which", lambda exe: "/usr/local/bin/node" if exe == "node" else "/usr/local/bin/finnamon")
    files = scheduler.render("Darwin")
    assert "com.finnamon.web.plist" in files and "server.js" in files["com.finnamon.web.plist"]
    units = scheduler.render("Linux")
    assert "finnamon-web.service" in units and "Restart=always" in units["finnamon-web.service"]
    monkeypatch.setattr(scheduler, "web_args", lambda: None)
    assert "com.finnamon.web.plist" not in scheduler.render("Darwin") and "finnamon-web.service" not in scheduler.render("Linux")


def test_in_page_link_reports_a_failed_announcement_and_plaid_errors(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli, telegram
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    store.set_state(conn, "chat_id", 555)
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_3", "institution_name": "Chase"}})
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 2, "transactions": 9, "recurring": 0, "holdings": 0, "error": None})
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: (_ for _ in ()).throw(telegram.TelegramError(429, "slow down")))
    r = link.finish_in_page(conn, "bill", "public-sandbox-abc")
    assert r["state"] == "linked" and r["announced"] is False and conn.execute("SELECT count(*) FROM items WHERE item_id='new'").fetchone()[0] == 1
    # Plaid errors on either form are a clean exit, not a traceback
    monkeypatch.setattr(plaid_api, "call", lambda path, body, **k: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_API_KEYS", "error_message": "bad keys"})))
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_PUBLIC_TOKEN", "error_message": "expired"})))
    for argv in (["link", "--token"], ["link", "--public-token", "public-sandbox-xyz"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 1 and capsys.readouterr().err.startswith("error:")


def test_in_page_link_with_owner_files_the_new_accounts_under_that_owner(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    seed(conn)
    conn.execute("INSERT INTO owners (owner, display_name) VALUES ('sam', 'Sam')")
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    users = []
    monkeypatch.setattr(plaid_api, "link_token_create", lambda user_id, **k: users.append(user_id) or {"link_token": "lt", "hosted_link_url": "https://h", "expiration": "e"})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_3", "institution_name": "Chase"}})
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: {"accounts": [{"account_id": "sy-chk", "name": "Checking", "type": "depository", "subtype": "checking", "balances": {"current": 5}}]})
    monkeypatch.setattr(sync, "sync_item", lambda c, item_id, token=None, **k: sync.sync_accounts(c, item_id, token, AS_OF) and {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})
    cli.main(["link", "--token", "--owner", "sam"])
    assert users == ["finnamon-sam"]
    cli.main(["link", "--public-token", "public-sandbox-abc", "--owner", "sam"])
    assert conn.execute("SELECT owner FROM accounts WHERE account_id='sy-chk'").fetchone()[0] == "sam"
    assert conn.execute("SELECT owner FROM items WHERE item_id='new'").fetchone()[0] == "sam"


def test_chart_spec_bad_name_dies(home, conn, capsys):
    from finnamon import cli
    seed(conn)
    with pytest.raises(SystemExit):
        cli.main(["chart", "--spec", "nope"])
    assert "unknown chart" in capsys.readouterr().err and charts.board() == []


def test_status_reports_a_live_daemon(home, conn, capsys):
    from finnamon import cli
    seed(conn)
    store.set_state(conn, "daemon_alive_at", store.now_local())
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out)["daemon_alive"] is True


def test_web_args_needs_node_server_and_node_modules(monkeypatch, tmp_path):
    which = {"node": "/opt/node/bin/node", "finnamon": "/opt/tools/bin/finnamon", "bun": "/opt/bun/bin/bun"}
    monkeypatch.setattr(scheduler.shutil, "which", lambda n: which.get(n))
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    assert scheduler.web_args() is None                                    # no web/server.js
    (tmp_path / "web").mkdir(); (tmp_path / "web" / "server.js").write_text("")
    assert scheduler.web_args() is None                                    # not npm-installed
    (tmp_path / "web" / "node_modules").mkdir()
    assert scheduler.web_args() == ["/opt/node/bin/node", str(tmp_path / "web" / "server.js")]
    assert {"/opt/node/bin", "/opt/bun/bin"} <= set(scheduler.env_path().split(":"))   # the units can find node, and bun for the Telegram channel plugin
    which.pop("node")
    assert scheduler.web_args() is None                                    # no node at all


def test_install_and_uninstall_manage_the_web_job(household_home, monkeypatch):
    from pathlib import Path
    home = household_home.parent
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    calls = []
    monkeypatch.setattr(scheduler.Path, "home", lambda: home)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(scheduler, "_launchd_reload", lambda domain, label, path: calls.append(["reload", label]))
    monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/repo/web/server.js"])
    written = scheduler.install("Darwin")
    assert [Path(w).name for w in written] == ["com.finnamon.daemon.plist", "com.finnamon.heartbeat.plist", "com.finnamon.web.plist"]
    assert ["reload", "com.finnamon.web"] in calls and (home / "Library/LaunchAgents/com.finnamon.web.plist").read_text().count("server.js")
    calls.clear(); monkeypatch.setattr(scheduler, "web_args", lambda: None)
    scheduler.install("Darwin")                                            # web/ gone: the old job is booted out, not reloaded
    assert ["reload", "com.finnamon.web"] not in calls and any(a[:2] == ["launchctl", "bootout"] and a[2].endswith("com.finnamon.web") for a in calls)
    calls.clear(); monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/repo/web/server.js"])
    scheduler.install("Linux")
    enable = next(a for a in calls if a[:3] == ["systemctl", "--user", "enable"])
    assert "finnamon-web.service" in enable and ["systemctl", "--user", "restart", "finnamon-web.service"] in calls
    assert "ExecStart=/usr/bin/node /repo/web/server.js" in (home / ".config/systemd/user/finnamon-web.service").read_text()
    calls.clear(); monkeypatch.setattr(scheduler, "web_args", lambda: None)
    scheduler.install("Linux")
    assert not any("finnamon-web.service" in a for a in calls)
    calls.clear()
    scheduler.uninstall("Darwin")
    assert any(a[2].endswith("com.finnamon.web") for a in calls) and not (home / "Library/LaunchAgents/com.finnamon.web.plist").exists()


def test_in_page_finish_waits_for_the_run_lock(home, conn, monkeypatch):
    from finnamon import cli, link, run
    seed(conn)
    attempts, slept = [], []
    class Held:
        def close(self): pass
    def lock():
        attempts.append(1)
        if len(attempts) < 3:
            raise run.Locked("busy")
        return Held()
    monkeypatch.setattr(run, "lock", lock)
    monkeypatch.setattr(link, "complete", lambda c, o, t: {"item_id": "item9", "institution": "Chase", "mirror_candidates": [], "sync": {}})
    monkeypatch.setattr(link, "announce", lambda *a, **k: None)
    r = link.finish_in_page(conn, "bill", "public-sandbox-abc", wait_s=30, sleep=slept.append)
    assert r["state"] == "linked" and slept == [3, 3]
    clock = iter([0, 0, 100, 100, 200])
    monkeypatch.setattr(link.time, "monotonic", lambda: next(clock))
    attempts.clear(); monkeypatch.setattr(run, "lock", lambda: (_ for _ in ()).throw(run.Locked("busy")))
    with pytest.raises(run.Locked, match="held the lock"):
        link.finish_in_page(conn, "bill", "public-sandbox-abc", wait_s=30, sleep=lambda s: None)
    monkeypatch.setattr(link, "finish_in_page", lambda *a, **k: (_ for _ in ()).throw(run.Locked("a sync held the lock for too long")))
    with pytest.raises(SystemExit) as e:
        cli.main(["link", "--public-token", "public-sandbox-abc"])
    assert e.value.code == 1


def test_linux_uninstall_and_status_cover_the_web_unit(household_home, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **k: calls.append(argv) or type("R", (), {"stdout": "", "stderr": ""})())
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path)
    units = tmp_path / ".config" / "systemd" / "user"; units.mkdir(parents=True)
    for n in ("finnamon-daemon.service", "finnamon-web.service"):
        (units / n).write_text("")
    scheduler.uninstall("Linux")
    assert "finnamon-web.service" in calls[0] and calls[-1][-1] == "daemon-reload" and not list(units.iterdir())
    scheduler.status("Linux")
    assert "finnamon-web.service" in calls[-1]


def test_on_wsl_the_page_opens_in_the_windows_browser(monkeypatch):
    """WSL's Linux side has no browser; wslview (wslu) or explorer.exe hands the address to Windows' default one."""
    import platform
    from finnamon import cli
    registered, opened = [], []
    monkeypatch.delenv("BROWSER", raising=False)
    wsl = platform.uname()._replace(release="5.15.167.4-microsoft-standard-WSL2")
    monkeypatch.setattr(cli.platform, "uname", lambda: wsl)
    monkeypatch.setattr(cli.shutil, "which", lambda c: "/mnt/c/Windows/explorer.exe" if c == "explorer.exe" else None)
    monkeypatch.setattr(cli.webbrowser, "register", lambda name, klass, inst, preferred: registered.append((name, inst.name, preferred)))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    assert cli._browser_open("http://localhost:8888/?token=x") and opened == ["http://localhost:8888/?token=x"]
    assert registered == [("wsl", "/mnt/c/Windows/explorer.exe", True)]
    monkeypatch.setenv("BROWSER", "firefox")
    cli._browser_open("http://localhost:8888/?token=x")
    assert len(registered) == 1, "a BROWSER the person set wins"


def test_the_dashboard_key_is_made_once_0600_and_rotated_by_a_person(home, capsys, monkeypatch):
    """`finnamon open` prints the page's address with its key (opening a browser when it can); `web token --rotate` mints a new
    one; the file is 0600 and the same one web/server.js reads; neither verb runs from a Claude session (the key is a shell)."""
    from pathlib import Path
    from finnamon import cli
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    cli.main(["open", "--print"])
    url = capsys.readouterr().out.strip()
    tok = url.removeprefix("http://localhost:8888/?token=")
    assert len(tok) == 64 and int(tok, 16) >= 0 and opened == []
    p = Path(home) / "web-token"
    assert p.read_text() == tok and p.stat().st_mode & 0o777 == 0o600 and Path(home).stat().st_mode & 0o777 == 0o700
    cli.main(["open"])
    assert opened == [url] and capsys.readouterr().out == "", "the browser opens it; nothing to copy off the terminal"
    cli.main(["open", "--host", "mac.tailnet.ts.net"])
    assert capsys.readouterr().out.strip() == f"https://mac.tailnet.ts.net/?token={tok}" and len(opened) == 1, "a phone's address is printed, not opened here"
    cli.main(["open", "--host", "http://192.168.1.9:8888/"])
    assert capsys.readouterr().out.strip() == f"http://192.168.1.9:8888/?token={tok}", "a whole origin is taken as given (a LAN address on plain http)"
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 1 5.6.7.8 22")
    cli.main(["open"])
    assert capsys.readouterr().out.strip() == url and len(opened) == 1, "over SSH a browser would open on the box's own screen: print instead"
    monkeypatch.delenv("SSH_CONNECTION")
    cli.main(["web", "token"])
    assert capsys.readouterr().out.strip() == tok
    cli.main(["web", "token", "--rotate"])
    out, err = capsys.readouterr()
    new = out.strip()
    assert new != tok and len(new) == 64 and p.read_text() == new and "locked out" in err and p.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: False)   # a headless box: nothing to open with, so the address is printed
    cli.main(["open"])
    assert capsys.readouterr().out.strip().endswith(new)
    p.write_text("  \n")   # a write that died halfway: a blank key would lock everyone out, so it counts as missing
    cli.main(["web", "token"])
    minted = capsys.readouterr().out.strip()
    assert len(minted) == 64 and minted != new and p.read_text() == minted
    p.write_text("my-password\n")   # hand-edited: neither a cookie nor an address could carry it, so it is reported, never served or overwritten
    for argv in (["open", "--print"], ["web", "token"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "not a hex key" in capsys.readouterr().err
    assert p.read_text() == "my-password\n"
    p.write_text(minted)
    monkeypatch.setattr(config, "web_token", lambda rotate=False: (_ for _ in ()).throw(PermissionError("web-token: unreadable")))
    for argv, what in ((["open", "--print"], "cannot read"), (["web", "token"], "cannot read"), (["web", "token", "--rotate"], "cannot replace")):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert what in capsys.readouterr().err, "an unreadable key file is one line, not a traceback"
    monkeypatch.setenv("CLAUDECODE", "1")
    for argv in (["open"], ["open", "--print"], ["web", "token"], ["web", "token", "--rotate"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "person at a terminal" in capsys.readouterr().err
    assert p.read_text() == minted, "a refused rotate changes nothing"


def test_claude_may_not_open_the_dashboard_or_read_its_key():
    from finnamon import assistant
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]   # the bundle is what every household session is sealed with
    assert {"Bash(finnamon open)", "Bash(finnamon open *)", "Bash(finnamon web *)"} <= set(perms["deny"])
    # the gates on the verbs mean nothing if the session can read the file itself (Read needs no permission), as with secrets.toml
    assert {"Read(~/.finnamon/web-token)", "Read(//Users/*/.finnamon/web-token)", "Read(//home/*/.finnamon/web-token)", "Edit(~/.finnamon/web-token)"} <= set(perms["deny"])


def test_uploads_to_the_box_carry_its_key(home, conn, capsys, monkeypatch, tmp_path):
    """`--to` sends the key as a bearer: FINNAMON_WEB_TOKEN on another computer, the box's own file on the box; none is never minted here."""
    from pathlib import Path
    from finnamon import cli
    assert cli._open.__self__ is cli._OPENER, "_http's seam is the no-redirect opener, not urllib's redirect-following urlopen"
    seed(conn)
    f = tmp_path / "hsbc.csv"; f.write_text("Date,Description,Amount\n2026-09-01,Coffee,-4.50\n")
    seen = []
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"accounts": []}'
    monkeypatch.setattr("finnamon.cli._open", lambda req, timeout=0: seen.append(req.headers) or R())
    cli.main(["account", "list", "--to", "https://box.ts.net"])
    assert "Authorization" not in seen[-1] and not (Path(home) / "web-token").exists(), "no key here: the box answers 401 and says what to run"
    (Path(home) / "web-token").write_text("boxkey\n")
    cli.main(["account", "list", "--to", "https://box.ts.net"])
    assert "Authorization" not in seen[-1], "this computer's key is not that box's: a local file never travels to another host (nor to a typo'd one)"
    cli.main(["account", "list", "--to", "http://localhost:8888"])
    assert seen[-1]["Authorization"] == "Bearer boxkey", "this computer is the box: its own file"
    monkeypatch.setenv("FINNAMON_WEB_TOKEN", "copied")
    cli.main(["import", "--to", "https://box.ts.net", "--dry-run", "HSBC Checking", str(f)])
    assert seen[-1]["Authorization"] == "Bearer copied" and seen[-1]["Content-type"] == "text/csv", "another computer: the key copied from `finnamon web token`"
    capsys.readouterr()
    # the box's 401 says `finnamon open`, which is for a browser on the box; from here the answer is the env var
    import io, urllib.error
    monkeypatch.setattr("finnamon.cli._open", lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error": "unauthorized: open the page with `finnamon open`"}'))))
    with pytest.raises(SystemExit):
        cli.main(["account", "list", "--to", "https://box.ts.net"])
    err = capsys.readouterr().err
    assert "FINNAMON_WEB_TOKEN" in err and "finnamon web token" in err and "finnamon open" not in err
    # a 30x is an error, never followed: urllib would copy the bearer onto the redirected request, to another host if Location says so
    assert cli._NoRedirect().redirect_request(None, None, 302, "Found", {"Location": "https://evil.example/"}, "https://evil.example/") is None
    assert any(isinstance(h, cli._NoRedirect) for h in cli._OPENER.handlers), "and that handler is on the opener _http uses"
    for to in ("http://192.168.1.9:8888", "http://box.ts.net"):   # plain http carries the key and the statement in the clear: only to this computer
        with pytest.raises(SystemExit):
            cli.main(["account", "list", "--to", to])
        assert "in the clear" in capsys.readouterr().err


def test_browser_import_to_another_box_needs_its_key_before_a_browser_opens(home, conn, capsys, monkeypatch):
    """The sealed session could only meet the 401 and cannot set an env var, so the launcher refuses first."""
    from pathlib import Path
    from finnamon import cli, claude_runner
    seed(conn)
    monkeypatch.setattr(claude_runner, "binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(cli, "chrome_path", lambda: "/Applications/Chrome")
    monkeypatch.setattr(cli, "chrome_launch", lambda *a, **k: pytest.fail("Chrome must not open without the key"))
    monkeypatch.delenv("FINNAMON_WEB_TOKEN", raising=False)
    for local in (None, "  \n", "thisboxkey"):   # no file, a blank one, and this computer's own key: none is the other box's
        if local is not None:
            (Path(home) / "web-token").write_text(local)
        with pytest.raises(SystemExit):
            cli.main(["import", "--browser", "hsbc", "--to", "https://box.ts.net"])
        assert "FINNAMON_WEB_TOKEN" in capsys.readouterr().err


def test_dashboard_default_port_is_one_number_in_both_languages(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    from finnamon import cli, config, scheduler
    js = (Path(__file__).resolve().parent.parent / "web" / "server.js").read_text()
    assert f"const DEFAULT_PORT = {config.DASHBOARD_PORT};" in js and config.DASHBOARD_PORT == 8888
    # the service files carry no PORT of their own, so a restart (update) lands on the default and an explicit PORT is never overridden
    monkeypatch.setattr(scheduler, "web_args", lambda: ["node", "server.js"])
    assert all("PORT" not in body for body in scheduler.render("Darwin").values())
    # the move is announced once
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path))
    cli._announce_dashboard_port(); first = capsys.readouterr().out
    cli._announce_dashboard_port()
    assert "localhost:8888" in first and "tailscale serve --bg 8888" in first and capsys.readouterr().out == ""
