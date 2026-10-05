"""Budgets: the mortgage counts (once), recurring bills are not extrapolated, several categories and merchants per budget."""
import json
import sqlite3

from finnamon import budgets, cli, detect, store
from tests.conftest import AS_OF, seed, txn

PACE = next(p for p in detect.detectors() if p.stem == "budget_pace")


def pace_alerts(conn, as_of=AS_OF):
    detect.run_one(conn, PACE, as_of)
    return [json.loads(r[0]) | {"key": r[1]} for r in conn.execute("SELECT payload_json, key FROM alerts WHERE kind='budget_pace' ORDER BY id")]


def stream(conn, sid, merchant, amount, account="chk"):
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, status, first_seen_at) "
                 "VALUES (?,?,'outflow',?,?,'MONTHLY',?,?,'MATURE','2026-07-01 00:00:00')", (sid, account, merchant, merchant.upper(), amount, amount))


def test_mortgage_budget_counts_the_checking_payment_once(conn):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('mtg','item1','Home Loan','loan','mortgage','5555','bill')")
    txn(conn, "pay", "chk", "2026-09-01", 3200, "LOAN SERVICER PMT", "Mr. Cooper", None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    txn(conn, "loan", "mtg", "2026-09-01", -3200, "PAYMENT RECEIVED", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")   # the loan side
    txn(conn, "sav", "chk", "2026-09-02", 500, "TO SAVINGS", None, None, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS")                          # not spend
    for cat in ("LOAN_PAYMENTS_MORTGAGE_PAYMENT", "LOAN_PAYMENTS"):
        budgets.budget_set(conn, "mortgage", 3200, cat, fixed=True)
        b = budgets.budget_list(conn, AS_OF)[0]
        assert (b["spent"], b["pace"]) == (3200, 3200)
    assert [a["key"] for a in pace_alerts(conn)] == ["budget:1:2026-09:over"]   # spent == limit; the detector reads the same rows


def test_recurring_bill_is_counted_once_not_projected(conn):
    seed(conn)
    stream(conn, "s-daycare", "Little Sprouts", 1500)
    budgets.budget_set(conn, "childcare", 1600, "GENERAL_SERVICES_CHILDCARE")
    txn(conn, "dc", "chk", "2026-09-03", 1500, "LITTLE SPROUTS", "Little Sprouts", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")
    txn(conn, "camp", "chk", "2026-09-04", 30, "PARK DEPT", "Park Dept", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")   # variable part
    b = budgets.budget_list(conn, "2026-09-06 12:00:00")[0]
    assert (b["spent"], b["recurring"], b["pace"]) == (1530, 1500, 1650)          # 1500 + 30 * 30/6, not 1530 * 5
    assert pace_alerts(conn, "2026-09-06 12:00:00")[0]["projection"] == 1650   # the detector agrees: a pace alert (1650 >= 1600)
    # a one-off at the same merchant far from the stream's amount stays variable
    txn(conn, "x", "chk", "2026-09-05", 400, "LITTLE SPROUTS", "Little Sprouts", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")
    assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["recurring"] == 1500


def test_fixed_budget_is_never_projected(conn):
    seed(conn)
    budgets.budget_set(conn, "hoa", 400, "RENT_AND_UTILITIES_OTHER_UTILITIES", fixed=True)
    txn(conn, "h", "chk", "2026-09-02", 350, "HOA DUES", None, None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_OTHER_UTILITIES")
    assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["pace"] == 350
    assert pace_alerts(conn, "2026-09-06 12:00:00") == []                    # 350 * 30/6 would be 1750
    budgets.budget_set(conn, "hoa", 400)                                       # a limit edit keeps fixed and the category
    b = budgets.budget_list(conn, "2026-09-06 12:00:00")[0]
    assert (b["fixed"], b["categories"]) == (True, ["RENT_AND_UTILITIES_OTHER_UTILITIES"])
    budgets.budget_set(conn, "hoa", 400, fixed=False)
    assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["pace"] == 1750


def test_several_categories_and_merchants_count_each_transaction_once(conn):
    seed(conn)
    txn(conn, "r", "cc", "2026-09-10", 40, "CHIPOTLE", "Chipotle", "mch_chip", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    txn(conn, "c", "cc", "2026-09-11", 6, "BLUE BOTTLE", "Blue Bottle", "mch_bb", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    txn(conn, "g", "cc", "2026-09-12", 90, "SAFEWAY", "Safeway", "mch_sw", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES")
    txn(conn, "w", "chk", "2026-09-05", 120, "SEATTLE PUBLIC UTIL", "Seattle Public Utilities", None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_WATER")
    txn(conn, "t", "chk", "2026-09-06", 45, "RECOLOGY", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    b = budgets.budget_set(conn, "Dining", 300, ["restaurant", "FOOD_AND_DRINK_COFFEE"], ["Blue Bottle"])   # coffee twice over: counted once
    assert (b["category"], b["categories"], b["merchants"]) == ("FOOD_AND_DRINK_RESTAURANT", ["FOOD_AND_DRINK_RESTAURANT", "FOOD_AND_DRINK_COFFEE"], ["Blue Bottle"])
    budgets.budget_set(conn, "water and trash", 200, merchants=["Seattle Public Utilities", "RECOLOGY"])
    budgets.budget_set(conn, "food", 500, "FOOD_AND_DRINK")                   # shares dining's categories: allowed
    spent = {b["name"]: b["spent"] for b in budgets.budget_list(conn, AS_OF)}
    assert spent == {"dining": 46, "water and trash": 165, "food": 136}
    wt = [b for b in budgets.budget_list(conn, AS_OF) if b["name"] == "water and trash"][0]
    assert wt["category"] is None and wt["merchants"] == ["Seattle Public Utilities", "RECOLOGY"]
    assert conn.execute("SELECT value FROM budget_selectors WHERE label='Blue Bottle'").fetchone()[0] == "mch_bb"   # canonical_for, as category rules
    a, = pace_alerts(conn, AS_OF)                                               # only water and trash: 165 * 30/19 = 260 >= 200
    assert (a["budget"], a["spent"], a["category"]) == ("water and trash", 165, None)


def test_cli_repeats_flags(home, conn, capsys):
    seed(conn)
    cli.main(["budget", "set", "dining", "300", "--category", "restaurant", "--category", "coffee", "--merchant", "Blue Bottle", "--fixed"])
    out = json.loads(capsys.readouterr().out)
    assert (out["categories"], out["merchants"], out["fixed"]) == (["FOOD_AND_DRINK_RESTAURANT", "FOOD_AND_DRINK_COFFEE"], ["Blue Bottle"], True)


def test_migration_moves_each_budgets_category_into_a_selector():
    db = sqlite3.connect(":memory:")
    files = sorted(store.MIGRATIONS.glob("*.sql"))
    for f in files[:-1]:
        for stmt in store._split(f.read_text()):
            db.execute(stmt)
    assert files[-1].name == "012_budget_selectors.sql"
    db.execute("INSERT INTO budgets (name, category, monthly_limit) VALUES ('groceries', 'FOOD_AND_DRINK_GROCERIES', 600)")
    for stmt in store._split(files[-1].read_text()):
        db.execute(stmt)
    assert db.execute("SELECT budget_id, kind, value FROM budget_selectors").fetchall() == [(1, "category", "FOOD_AND_DRINK_GROCERIES")]
    assert db.execute("SELECT fixed FROM budgets").fetchone() == (0,)
