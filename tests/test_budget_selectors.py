"""Budgets: the mortgage counts (once), recurring bills are not extrapolated, several categories and merchants per budget."""
import json
import sqlite3

import pytest

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
    # October: Plaid files the payment as a transfer, but it pairs with the loan account, so flow is 'mortgage' and it still counts
    txn(conn, "pay2", "chk", "2026-10-01", 3200, "LOAN SERVICER WEB PMT", None, None, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    txn(conn, "loan2", "mtg", "2026-10-01", -3200, "PAYMENT RECEIVED", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    assert budgets.budget_list(conn, "2026-10-05 12:00:00")[0]["spent"] == 3200
    assert pace_alerts(conn, "2026-10-05 12:00:00")[-1]["key"] == "budget:1:2026-10:over"


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
    # a stream first stored after as_of still counts: a run takes as_of before its sync stamps first_seen_at
    conn.execute("UPDATE recurring SET first_seen_at='2026-09-10 06:00:00'")
    assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["recurring"] == 1500


def test_recurring_needs_a_live_monthly_stream_and_a_refund_never_projects_below_spent(conn):
    seed(conn)
    budgets.budget_set(conn, "childcare", 1600, "GENERAL_SERVICES_CHILDCARE")
    stream(conn, "s-dc", "Little Sprouts", 1500)
    txn(conn, "dc", "chk", "2026-09-03", 1500, "LITTLE SPROUTS", "Little Sprouts", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")
    txn(conn, "rf", "chk", "2026-09-04", -300, "PARK DEPT REFUND", "Park Dept", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")
    b = budgets.budget_list(conn, "2026-09-06 12:00:00")[0]
    assert (b["spent"], b["pace"]) == (1200, 1200)                             # 1500 + -300 * 5 would be -0; never below spent
    assert pace_alerts(conn, "2026-09-06 12:00:00") == []
    txn(conn, "rv", "chk", "2026-09-05", -1500, "LITTLE SPROUTS REVERSAL", "Little Sprouts", None, "GENERAL_SERVICES", "GENERAL_SERVICES_CHILDCARE")
    assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["recurring"] == 0   # the bill's reversal nets in the recurring part, not projected
    for change in ("is_active=0", "status='TOMBSTONED'", "frequency='WEEKLY'", "frequency='SEMI_MONTHLY'", "frequency='UNKNOWN'"):   # stopped, or recurring within the month: projected
        conn.execute(f"UPDATE recurring SET is_active=1, status='MATURE', frequency='MONTHLY'"); conn.execute(f"UPDATE recurring SET {change}")
        conn.execute("DELETE FROM transactions WHERE transaction_id IN ('rf','rv')")
        assert budgets.budget_list(conn, "2026-09-06 12:00:00")[0]["recurring"] == 0, change


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
    # typed in another case, or as `display` shows it after an alias: still matched, and the reply says how many charges
    assert budgets.budget_set(conn, "trash", 50, merchants=["recology"])["matches"] == {"recology": 1}
    txn(conn, "c2", "cc", "2026-09-13", 5, "BLUE BOTTLE #2", "Blue Bottle", None, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")   # Plaid sent no entity id
    assert budgets.budget_set(conn, "bb", 50, merchants=["Blue Bottle"])["matches"] == {"Blue Bottle": 2}
    budgets.alias_set(conn, "BLUE BOTTLE", "my coffee place")
    assert budgets.budget_set(conn, "cafe", 50, merchants=["My Coffee Place"])["matches"] == {"My Coffee Place": 1}   # display, entity id kept
    a = {a["budget"]: a for a in pace_alerts(conn, AS_OF)}["water and trash"]   # 165 * 30/19 = 260 >= 200
    assert (a["spent"], a["category"]) == (165, None)
    budgets.budget_remove(conn, "dining")
    budgets.budget_set(conn, "water and trash", 200, fixed=True); budgets.budget_remove(conn, "water and trash")
    budgets.budget_set(conn, "dining", 300)                                    # re-added from the dashboard: what it covered comes back
    budgets.budget_set(conn, "water and trash", 200)                           # a merchant-only name resolves to no category: no error
    back = {b["name"]: b for b in budgets.budget_list(conn, AS_OF)}
    assert back["dining"]["covers"] == ["Restaurant", "Coffee", "Blue Bottle"] and back["water and trash"]["fixed"] is False   # but not fixed


def test_cli_repeats_flags(home, conn, capsys):
    seed(conn)
    cli.main(["budget", "set", "dining", "300", "--category", "restaurant", "--category", "coffee", "--merchant", "Blue Bottle", "--fixed"])
    out = json.loads(capsys.readouterr().out)
    assert (out["categories"], out["merchants"], out["fixed"]) == (["FOOD_AND_DRINK_RESTAURANT", "FOOD_AND_DRINK_COFFEE"], ["Blue Bottle"], True)


def test_migration_moves_each_budgets_category_into_a_selector():
    db = sqlite3.connect(":memory:")
    m012 = store.MIGRATIONS / "012_budget_selectors.sql"
    for f in sorted(store.MIGRATIONS.glob("*.sql")):
        if f.name < m012.name:
            for stmt in store._split(f.read_text()):
                db.execute(stmt)
    db.execute("INSERT INTO budgets (name, category, monthly_limit) VALUES ('groceries', 'FOOD_AND_DRINK_GROCERIES', 600)")
    for stmt in store._split(m012.read_text()):
        db.execute(stmt)
    assert db.execute("SELECT budget_id, kind, value FROM budget_selectors").fetchall() == [(1, "category", "FOOD_AND_DRINK_GROCERIES")]
    assert db.execute("SELECT fixed FROM budgets").fetchone() == (0,)


# Value: protects=the dashboard's covers line (taxonomy.label: detailed code -> its leaf, primary -> itself, merchants as typed); fails_when=label drops the primary prefix wrongly or covers loses merchants/order; why_new=nothing asserted covers or taxonomy.label; seam=none
def test_covers_labels_categories_for_people_then_merchants(conn):
    seed(conn)
    b = budgets.budget_set(conn, "dining", 300, ["FOOD_AND_DRINK_COFFEE", "FOOD_AND_DRINK"], ["Blue Bottle"])
    assert b["covers"] == ["Coffee", "Food and drink", "Blue Bottle"]


# Value: protects=selectors given replace the old ones, and a blank merchant is refused before anything is written; fails_when=budget_set appends instead of DELETE-then-insert, or the blank check moves after the write; why_new=existing tests only set selectors once or keep them; seam=none
def test_new_selectors_replace_old_and_blank_merchant_is_refused(conn):
    seed(conn)
    txn(conn, "r", "cc", "2026-09-10", 40, "CHIPOTLE", "Chipotle", "mch_chip", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    txn(conn, "c", "cc", "2026-09-11", 6, "BLUE BOTTLE", "Blue Bottle", "mch_bb", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    budgets.budget_set(conn, "dining", 300, "restaurant")
    b = budgets.budget_set(conn, "dining", 300, merchants=["Blue Bottle"])
    assert (b["category"], b["categories"], b["merchants"]) == (None, [], ["Blue Bottle"])
    assert budgets.budget_list(conn, AS_OF)[0]["spent"] == 6
    with pytest.raises(ValueError):
        budgets.budget_set(conn, "dining", 300, merchants=["  "])
    assert budgets.budget_list(conn, AS_OF)[0]["merchants"] == ["Blue Bottle"]


# Value: protects=the dashboard's limit edit (`budget set -- name amount`, no flags) keeps merchants and fixed; --no-fixed clears it; fails_when=--fixed defaults to False or a None --category/--merchant is read as "clear"; why_new=the CLI test only covers the all-flags path; seam=none
def test_cli_limit_edit_keeps_selectors_and_fixed(home, conn, capsys):
    seed(conn)
    cli.main(["budget", "set", "daycare", "1500", "--merchant", "Little Sprouts", "--fixed"])
    capsys.readouterr()
    cli.main(["budget", "set", "--", "daycare", "1600"])
    out = json.loads(capsys.readouterr().out)
    assert (out["monthly_limit"], out["merchants"], out["fixed"]) == (1600, ["Little Sprouts"], True)
    cli.main(["budget", "set", "daycare", "1600", "--no-fixed"])
    out = json.loads(capsys.readouterr().out)
    assert (out["merchants"], out["fixed"]) == (["Little Sprouts"], False)
