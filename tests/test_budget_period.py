"""Budgets with a period (issue 53): the window math, its SQL twin in budget_pace.sql, and how the views use it."""
import pytest

from finnamon import budgets, charts, render
from tests.conftest import AS_OF, seed, txn

W = ("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_WATER")
T = ("TRAVEL", "TRAVEL_FLIGHTS")


@pytest.mark.parametrize("period, anchor, as_of, want", [
    ("monthly", None, "2026-09-19", ("2026-09-01", "2026-09-30")),
    ("monthly", None, "2028-02-29", ("2028-02-01", "2028-02-29")),       # a leap February ends on the 29th
    ("bimonthly", None, "2026-01-01", ("2026-01-01", "2026-02-28")),     # no anchor: windows start in January
    ("bimonthly", None, "2026-02-28", ("2026-01-01", "2026-02-28")),
    ("bimonthly", None, "2026-03-01", ("2026-03-01", "2026-04-30")),     # the month boundary opens the next window
    ("bimonthly", 2, "2026-01-15", ("2025-12-01", "2026-01-31")),        # anchor February: Dec-Jan straddles the year
    ("bimonthly", 2, "2026-02-01", ("2026-02-01", "2026-03-31")),
    ("quarterly", 11, "2026-01-10", ("2025-11-01", "2026-01-31")),       # Nov-Jan
    ("quarterly", 1, "2026-12-31", ("2026-10-01", "2026-12-31")),
    ("yearly", None, "2026-09-19 12:00:00", ("2026-01-01", "2026-12-31")),
    ("yearly", 7, "2026-06-30", ("2025-07-01", "2026-06-30")),           # a July travel year, before July: last year's
    ("yearly", 7, "2026-07-01", ("2026-07-01", "2027-06-30")),
])
def test_window(period, anchor, as_of, want):
    assert budgets.window(period, anchor, as_of) == want


def test_window_sql_matches_python(conn):
    """budget_pace.sql's `win` CTE is budgets.window() in SQL: every period, anchor and month of two years agree."""
    sql = ("SELECT date(:as_of, 'start of month', '-' || ((CAST(strftime('%m', :as_of) AS INTEGER) - COALESCE(:anchor, 1) + 12) % :n) || ' months'), "
           "date(date(:as_of, 'start of month', '-' || ((CAST(strftime('%m', :as_of) AS INTEGER) - COALESCE(:anchor, 1) + 12) % :n) || ' months'), '+' || :n || ' months', '-1 day')")
    assert "((CAST(strftime('%m', :as_of) AS INTEGER) - COALESCE(b.anchor, 1) + 12) % p.n)" in budgets_pace_sql()   # the expression tested is the detector's own
    for period, n in budgets.PERIODS.items():
        for anchor in (None, *range(1, 13)):
            for y in (2025, 2026):
                for m in range(1, 13):
                    as_of = f"{y}-{m:02d}-15"
                    assert tuple(conn.execute(sql, {"as_of": as_of, "anchor": anchor, "n": n}).fetchone()) == budgets.window(period, anchor, as_of)


def budgets_pace_sql() -> str:
    return (budgets.detect.DETECTORS / "rules" / "budget_pace.sql").read_text()


def test_parse_anchor_and_bad_input(conn):
    assert [budgets.parse_anchor(x) for x in ("3", "March", "mar", "sept", None, "")] == [3, 3, 3, 9, None, None]
    for bad in ("13", "0", "ma", "smarch"):
        with pytest.raises(ValueError, match="--anchor"):
            budgets.parse_anchor(bad)
    seed(conn)
    with pytest.raises(ValueError, match="--period"):
        budgets.budget_set(conn, "water", 130, list(W[1:]), period="weekly")
    with pytest.raises(ValueError, match="monthly budget starts on the 1st"):
        budgets.budget_set(conn, "water", 130, list(W[1:]), anchor="march")


def test_default_is_monthly_and_set_keeps_period(conn):
    seed(conn)
    b = budgets.budget_set(conn, "water", 130, list(W[1:]))
    assert (b["period"], b["anchor"], b["limit"]) == ("monthly", None, 130)
    b = budgets.budget_set(conn, "water", 130, period="bimonthly", anchor="Mar")
    assert (b["period"], b["anchor"]) == ("bimonthly", 3)
    b = budgets.budget_set(conn, "water", 140)   # the dashboard's limit edit: period and anchor stay
    assert (b["period"], b["anchor"], b["limit"], b["categories"]) == ("bimonthly", 3, 140, [W[1]])
    b = budgets.budget_set(conn, "water", 140, period="monthly")   # back to monthly: no anchor
    assert (b["period"], b["anchor"]) == ("monthly", None)


def test_bimonthly_bill_counts_over_its_window(conn):
    """A $120 water bill in March, anchor March: the Mar-Apr window holds it in April too; May starts empty."""
    seed(conn)
    budgets.budget_set(conn, "water", 130, list(W[1:]), period="bimonthly", anchor=3)
    txn(conn, "w1", "chk", "2026-01-20", 118, "SPU", "SPU", "mch_spu", *W)
    txn(conn, "w2", "chk", "2026-03-20", 120, "SPU", "SPU", "mch_spu", *W)
    for as_of, spent, start in (("2026-03-25", 120, "2026-03-01"), ("2026-04-19", 120, "2026-03-01"), ("2026-05-02", 0, "2026-05-01")):
        b = budgets.budget_list(conn, as_of)[0]
        assert (b["spent"], b["window_start"], b["pace"], b["left"], b["monthly_share"]) == (spent, start, spent, 130 - spent, 65)
    out = render.budgets(budgets.budget_list(conn, "2026-04-19"))
    assert "$130.00/2 mo" in out and "Mar 1 to Apr 30" in out


def test_yearly_shows_left_this_year(conn):
    seed(conn)
    budgets.budget_set(conn, "travel", 6000, ["travel"], period="yearly")
    txn(conn, "f", "cc", "2026-03-11", 2400, "DELTA", "Delta", "mch_delta", *T)
    txn(conn, "h", "cc", "2025-12-30", 900, "HOTEL", "Hotel", "mch_h", "TRAVEL", "TRAVEL_LODGING")   # last year's: not counted
    b = budgets.budget_list(conn, AS_OF)[0]
    assert (b["spent"], b["left"], b["window_start"], b["window_end"]) == (2400, 3600, "2026-01-01", "2026-12-31")
    assert "$3,600.00 left this year" in render.budgets([b]) and "$6,000.00/yr" in render.budgets([b])


def test_overall_prorates_and_lists_the_budgets_over(conn):
    """Overall's limit is each budget's monthly share; a non-monthly budget's spending is its window's over its months,
    so the bill's month and the empty one read the same; and every budget over its own limit is named."""
    seed(conn)
    budgets.budget_set(conn, "groceries", 400, "FOOD_AND_DRINK_GROCERIES")
    budgets.budget_set(conn, "water", 130, list(W[1:]), period="bimonthly", anchor=9)
    budgets.budget_set(conn, "travel", 1200, ["travel"], period="yearly")
    txn(conn, "g", "chk", "2026-09-05", 100, "WF", "Whole Foods", "mch_wf")
    txn(conn, "w", "chk", "2026-09-10", 150, "SPU", "SPU", "mch_spu", *W)   # over the water limit
    txn(conn, "t", "cc", "2026-02-11", 600, "DELTA", "Delta", "mch_delta", *T)
    o = budgets.overall(conn, AS_OF)
    assert o["limit"] == 400 + 65 + 100
    assert o["spent"] == 100 + 75 + 50
    assert o["over"] == [{"budget": "water", "spent": 150, "limit": 130, "period": "bimonthly"}]   # underspend in groceries does not hide it
    assert budgets.overall(conn, "2026-10-15")["spent"] == 75 + 50   # October: water's window still has the bill, groceries nothing yet


def test_suggest_proposes_period_and_anchor_from_the_stream(conn):
    seed(conn)
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, last_amount, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow','SPU','BIMONTHLY',120,'2026-09-10','MATURE',?), ('s2','chk','outflow','Netflix','MONTHLY',15,'2026-09-02','MATURE',?)", (AS_OF, AS_OF))
    rec = {r["merchant"]: r for r in budgets.suggest(conn, 3, AS_OF, include_ended=True)["recurring"]}
    assert (rec["SPU"]["period"], rec["SPU"]["anchor"]) == ("bimonthly", 9)
    assert "period" not in rec["Netflix"]


def test_chart_shows_the_window_with_you_are_here(conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(charts.store, "now_local", lambda: "2026-09-19 12:00:00")
    budgets.budget_set(conn, "groceries", 400, "FOOD_AND_DRINK_GROCERIES")
    budgets.budget_set(conn, "travel", 6000, ["travel"], period="yearly")
    txn(conn, "g", "chk", "2026-08-05", 100, "WF", "Whole Foods", "mch_wf")
    txn(conn, "f", "cc", "2026-03-11", 2400, "DELTA", "Delta", "mch_delta", *T)
    sp = charts.spec(conn, "budgets", "travel")
    rows = [v for v in sp["data"]["values"] if v["budget"] == "travel"]
    assert [(r["month"], r["spent"], r["limit"]) for r in rows] == [("2026 (so far)", 2400, 6000)]
    assert rows[0]["here"] == round(6000 * round(262 / 365, 3), 2)   # Sep 19 is day 262 of 365
    assert sp["layer"][-1]["encoding"]["color"] == {"datum": "you are here"}
    groceries = [v for v in sp["data"]["values"] if v["budget"] == "groceries"]
    assert all(v["here"] is None for v in groceries) and len(groceries) == 7   # monthly bars as before, Mar to Sep
    ov = [v for v in sp["data"]["values"] if v["budget"] == charts.OVERALL]
    assert ov[-1]["limit"] == 400 + 500 and ov[-1]["spent"] == 200   # travel's share in each month: 6000/12, 2400/12
