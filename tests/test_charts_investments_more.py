"""Charts (needs matplotlib) and holdings / net worth with Plaid faked."""
import pytest

from finnamon import budgets, store, charts, config, investments, plaid_api
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, seed, txn, today

pytest.importorskip("matplotlib")


def test_spend_by_category_buckets_null_category_instead_of_dropping_it(conn):
    """Regression (issue 23 diff): the filter changed from `t.pfc_primary NOT IN (...)` to
    `COALESCE(category_primary,'?') NOT IN (...)`. `NULL NOT IN (...)` evaluates to NULL (row dropped);
    `'?' NOT IN (...)` is true, so an uncategorized transaction now shows up under a '?' bucket instead
    of silently vanishing from the chart. Both the PNG and the dashboard spec share this query."""
    seed(conn)
    txn(conn, "u", "chk", today(), 42, "MYSTERY BIZ", None, None, None, None)
    by_cat = {v["category"]: v["amount"] for v in charts.spec(conn, "spend_by_category")["data"]["values"]}
    assert by_cat == {"Uncategorized": 42}
    out = charts.render(conn, "spend_by_category")
    assert out.exists() and out.stat().st_size > 1000   # PNG side runs the identical query without raising


def test_merchant_history_matches_raw_bank_name_even_when_merchant_name_present(conn):
    """Regression (issue 23 diff): the match went from `lower(COALESCE(merchant_name,name)) LIKE ?` (raw name
    checked only when merchant_name is NULL) to `lower(display) LIKE ? OR lower(name) LIKE ?`, so a search
    term that only appears in the raw bank string now still finds the transaction even when Plaid's clean
    merchant_name (which becomes `display`) doesn't contain it."""
    seed(conn)
    txn(conn, "a", "chk", today(), 10, "SQ *ACMEPLUMBING 555", "Square", None)   # merchant_name "Square" doesn't match "acmeplumbing"
    vals = charts.spec(conn, "merchant_history", "acmeplumbing")["data"]["values"]
    assert len(vals) == 1 and vals[0]["merchant"] == "Square" and vals[0]["amount"] == 10


def test_merchant_history_finds_an_aliased_payee_by_all_three_of_its_names(conn):
    """Once the household aliases a payee, `display` becomes their name and Plaid's `merchant_name` stops being
    what the search sees. All three have to keep working: the household's own name (the point of issue 23),
    Plaid's clean name, and the raw bank string, or renaming a payee quietly loses the other two."""
    from finnamon import budgets
    seed(conn)
    txn(conn, "a", "chk", today(), 20, "SQ *PMT 8827", "Square", None)
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    for term in ("fence contractor", "square", "sq *pmt"):
        vals = charts.spec(conn, "merchant_history", term)["data"]["values"]
        assert len(vals) == 1 and vals[0]["merchant"] == "fence contractor", term


def test_every_chart_renders_and_bad_input_is_refused(conn, home):
    seed(conn)
    budgets.budget_set(conn, "groceries", 600, "FOOD_AND_DRINK_GROCERIES")
    for i in range(6):
        txn(conn, f"t{i}", "chk", f"2026-0{4 + i}-10", 100 + i, "WF", "Whole Foods", "mch_wf")
        txn(conn, f"p{i}", "chk", f"2026-0{4 + i}-02", -3000, "PAYROLL", "Acme", "mch_acme", "INCOME", "INCOME_WAGES")
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-01 06:00:00', 1000, 900)")
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1200, 1100)", (AS_OF,))
    for name, arg in (("budgets", None), ("spend_by_category", None), ("balance_history", None), ("merchant_history", "whole"), ("monthly_in_out", None)):
        out = charts.render(conn, name, arg, months=6)
        assert out.parent == config.charts_dir() and out.name.startswith(name) and out.suffix == ".png" and out.stat().st_size > 1000, name
    with pytest.raises(ValueError, match="needs a merchant name"):
        charts.render(conn, "merchant_history")
    with pytest.raises(ValueError, match="unknown chart"):
        charts.render(conn, "pie_of_doom")


def test_holdings_sync_and_net_worth(conn, monkeypatch):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('inv','item1','Brokerage','investment','brokerage','0001','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk_m','item1','Mirror','depository','checking','4821','joint','chk')")
    resp = {"securities": [{"security_id": "s1", "name": "Vanguard 500", "ticker_symbol": "VOO"}],
            "holdings": [{"account_id": "inv", "security_id": "s1", "quantity": 2, "institution_price": 500.0, "institution_value": 1000.0},
                         {"account_id": "inv", "security_id": "s-unknown", "quantity": 1, "institution_price": 5, "institution_value": 5}]}
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: resp)
    assert investments.sync_holdings(conn, "item1", "tok") == 2
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT security_id, ticker, value FROM holdings")}
    assert rows == {"s1": ("VOO", 1000.0), "s-unknown": (None, 5)}
    # a later snapshot replaces the account's rows: selling everything leaves none, not a stale value
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: {"accounts": [{"account_id": "inv"}], "securities": [], "holdings": []})
    assert investments.sync_holdings(conn, "item1", "tok") == 0 and conn.execute("SELECT count(*) FROM holdings").fetchone()[0] == 0
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "PRODUCT_NOT_READY"})))
    assert investments.sync_holdings(conn, "item1", "tok") == 0 and store.get_state(conn, "holdings_unsupported:item1") is None  # try again next cycle
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "ITEM_LOGIN_REQUIRED"})))
    with pytest.raises(PlaidError):
        investments.sync_holdings(conn, "item1", "tok")
    calls = []
    for code in ("PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_ENABLED", "NO_INVESTMENT_ACCOUNTS", "INVALID_PRODUCT"):
        store.set_state(conn, "holdings_unsupported:item1", None)
        monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok, c=code: calls.append(c) or (_ for _ in ()).throw(PlaidError({"error_code": c})))
        assert investments.sync_holdings(conn, "item1", "tok") == 0
        assert investments.sync_holdings(conn, "item1", "tok") == 0   # remembered: no second call
    assert len(calls) == 4 and store.get_state(conn, "holdings_unsupported:item1") == "INVALID_PRODUCT"
    store.set_state(conn, "holdings_unsupported:item1", None)
    monkeypatch.setattr(config, "item_tokens", lambda: {"item1": "tok"})
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: {"securities": [], "holdings": []})
    assert investments.sync_all_holdings(conn) == {"item1": 0}
    # net worth: latest balance per account, mirrors excluded, credit is a liability, holdings summed at their latest as_of
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-01 06:00:00', 500, 500)")
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1000, 900)", (AS_OF,))
    conn.execute("INSERT INTO balances VALUES ('chk_m', ?, 1000, 900)", (AS_OF,))
    conn.execute("INSERT INTO balances VALUES ('cc', ?, 250, NULL)", (AS_OF,))
    conn.execute("INSERT INTO balances VALUES ('inv', ?, 1005, NULL)", (AS_OF,))
    monkeypatch.setattr(plaid_api, "investments_holdings_get", lambda tok: resp)
    investments.sync_holdings(conn, "item1", "tok")   # the snapshot again, after the sold-everything step above
    nw = investments.net_worth(conn)
    assert (nw["assets"], nw["liabilities"], nw["net_worth"], nw["holdings_value"]) == (2005, 250, 1755, 1005)
    assert {a["name"] for a in nw["accounts"]} == {"Chase Checking", "Sapphire", "Brokerage"}
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-19 06:00:00', 400, 400)")   # an earlier sync the same day: superseded, not added
    conn.execute("INSERT INTO balances VALUES ('cc', '2026-09-19 06:00:00', 900, NULL)")
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-20 08:00:00', 1100, 1100)")   # a day only checking synced: the others carry forward
    hist = investments.net_worth_history(conn, months=240)  # fixed 2026 dates; a wide window keeps this off the wall clock
    assert [h["date"] for h in hist] == ["2026-09-01", "2026-09-19", "2026-09-20"]
    assert [h["net_worth"] for h in hist] == [500, 1755, 1855]   # 09-01: checking only; 09-19: last snapshots; 09-20: 1100 + 1005 - 250, the others carried
    nw = investments.net_worth(conn)
    assert hist[-1]["net_worth"] == nw["net_worth"]
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('ln','item1','Car loan','loan','auto','9','bill')")
    conn.execute("INSERT INTO balances VALUES ('ln', '2000-01-01 00:00:00', 100, NULL)")   # only ever synced before the window: carried into every day, never a day of its own
    hist = investments.net_worth_history(conn, months=240)
    assert [h["date"] for h in hist] == ["2026-09-01", "2026-09-19", "2026-09-20"] and [h["net_worth"] for h in hist] == [400, 1655, 1755]
    assert [(h["assets"], h["liabilities"]) for h in hist] == [(500, 100), (2005, 350), (2105, 350)]   # the carried loan on every day; 09-20: 1100 + 1005 against 250 + 100
    assert all(round(h["assets"] - h["liabilities"], 2) == h["net_worth"] for h in hist)


def test_net_worth_splits_cash_investments_property(conn):
    """Regression for the dashboard split: depository is cash, whatever else the bank reports (investment, other, the
    legacy brokerage type) is investments, credit/loan are liabilities, a mirror is skipped, and stated property
    counts in net_worth but never in what the banks report. The history classifies the same way."""
    from finnamon import properties
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('inv','item1','Brokerage','investment','brokerage','0001','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('oth','item1','HSA','other','hsa','0002','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('ln','item1','Auto loan','loan','auto','0003','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk_m','item1','Mirror','depository','checking','4821','joint','chk')")
    for acct, cur in (("chk", 1000), ("chk_m", 1000), ("cc", 250), ("inv", 3000), ("oth", 500), ("ln", 4000)):
        conn.execute("INSERT INTO balances VALUES (?, ?, ?, NULL)", (acct, AS_OF, cur))
    nw = investments.net_worth(conn)
    assert (nw["cash"], nw["investments"], nw["assets"], nw["liabilities"], nw["property"]) == (1000, 3500, 4500, 4250, 0)
    assert nw["net_worth"] == 250 and nw["holdings_value"] == 0
    properties.set_value(conn, "House", 800000)
    nw = investments.net_worth(conn)
    assert (nw["assets"], nw["property"], nw["net_worth"]) == (4500, 800000, 800250)   # property is not a bank asset
    assert {a["name"] for a in nw["accounts"]} == {"Chase Checking", "Sapphire", "Brokerage", "HSA", "Auto loan"}
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('brk','item1','Old brokerage','brokerage','brokerage','0004','bill')")
    conn.execute("INSERT INTO balances VALUES ('brk', ?, 700, NULL)", (AS_OF,))
    nw = investments.net_worth(conn)
    hist = investments.net_worth_history(conn, months=240)
    assert (nw["investments"], nw["assets"]) == (4200, 5200)
    assert (hist[-1]["assets"], hist[-1]["liabilities"], hist[-1]["net_worth"]) == (805200, 4250, 800950)   # property counts, at its current value
    assert hist[-1]["net_worth"] == nw["net_worth"] and hist[-1]["property"] == 800000 and "no history" in hist[-1]["property_basis"]   # the last point is net worth today


def test_colour_legends_never_take_plot_width(home, conn):   # #97
    import pathlib
    import re
    app = (pathlib.Path(__file__).parent.parent / "web/public/app.js").read_text()
    assert re.search(r"legend: \{ orient: 'top', direction: 'horizontal'", app), "the page's Vega config sets the default legend: a row above the plot"
    for name in ("budgets", "balance_history", "monthly_in_out"):
        legend = charts.spec(conn, name)["encoding"]["color"].get("legend", {})
        assert "orient" not in legend, f"{name} leaves orient to the theme default (a spec that sets its own still wins)"
    assert charts.spec(conn, "monthly_in_out")["encoding"]["color"]["legend"]["values"] == ["income", "spending", "mortgage"], "#92 order kept"


def test_balance_chart_leaves_loans_out_unless_asked_and_empty_charts_say_so(conn):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('mtg','item1','Mortgage','loan','mortgage','1','bill')")
    for a, v in (("chk", 900), ("mtg", 400000)):
        conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES (?, datetime('now','-1 day'), ?)", (a, v))
    names = lambda arg=None: {v["account"] for v in charts.spec(conn, "balance_history", arg)["data"]["values"]}
    assert names() == {"Chase Checking …4821"} and len(names("all")) == 2
    assert "loans left out" in charts.spec(conn, "balance_history")["title"]
    conn.execute("DELETE FROM balances")
    assert charts.render(conn, "balance_history").stat().st_size > 1000   # "No data yet" text, not bare axes
    assert charts.spec(conn, "balance_history")["data"]["values"] == []


def test_net_worth_history_points_carry_what_an_account_added_this_month_contributes(conn):
    seed(conn)
    conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES ('chk', '2026-08-31 10:00:00', 1000)")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('new','item1','HSBC','depository','checking','9','bill')")
    conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES ('new', '2026-09-05 10:00:00', 5000)")
    hist = investments.net_worth_history(conn, months=240)
    assert [(h["date"], h["net_worth"], h["new_this_month"]) for h in hist] == [("2026-08-31", 1000, 1000), ("2026-09-05", 6000, 5000)]


def test_in_out_axis_says_month_names(conn):
    e = charts._month_label([("2025-11",), ("2025-12",), ("2026-01",)])
    assert "datum.value=='2025-11'" in e and "=='01'" in e
    assert "datum.value==" not in charts._month_label([("2026-02",), ("2026-03",)])
