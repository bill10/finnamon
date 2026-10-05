"""The 10/4 QA on categories, aliases and budgets: aliases listed and undone, a budget that says what it counts,
overlapping budgets counted once, recategorizing from a transaction, imported rows uncategorized, thresholds that can fire."""
import json

import pytest

from finnamon import budgets, charts, cli, imports
from tests.conftest import AS_OF, seed, today, txn


def run_cli(capsys, *args):
    cli.main(list(args))
    return capsys.readouterr().out


def test_alias_list_remove_and_refuse_a_typo(conn):
    seed(conn)
    txn(conn, "a1", "chk", "2026-09-01", 2400, "Loan Payment Confirmation# 1", None, None)
    txn(conn, "a2", "chk", "2026-08-01", 2400, "Loan Payment Confirmation# 2", None, None)
    with pytest.raises(ValueError, match="matches no transaction.*did you mean 'Loan Payment Confirmation# 1'"):
        budgets.alias_set(conn, "Loan Paymnet Confirmation# 1", "mortgage")
    budgets.alias_set(conn, "Loan Payment Confirmation#%", "BofA mortgage")
    assert budgets.alias_list(conn) == [{"name": "Loan Payment Confirmation#%", "canonical": "BofA mortgage", "pattern": True, "matches": 2,
                                         "distinct_names": 2, "created_at": budgets.alias_list(conn)[0]["created_at"]}]
    assert conn.execute("SELECT DISTINCT display FROM tx_now").fetchall()[0][0] == "BofA mortgage"
    assert budgets.alias_remove(conn, "loan payment confirmation#%")["removed"]["name"] == "Loan Payment Confirmation#%"
    assert budgets.alias_list(conn) == [] and {r[0] for r in conn.execute("SELECT display FROM tx_now")} == {"Loan Payment Confirmation# 1", "Loan Payment Confirmation# 2"}
    with pytest.raises(ValueError, match="no alias named"):
        budgets.alias_remove(conn, "nothing")


def test_a_budget_says_what_it_counts_and_when_its_name_was_a_guess(conn):
    seed(conn)
    u = budgets.budget_set(conn, "utilities", 300)   # not rent: a renter would be over on the 1st
    assert "RENT_AND_UTILITIES_RENT" not in u["categories"] and "RENT_AND_UTILITIES_WATER" in u["categories"]
    assert "Rent and utilities › Water" in u["counts"] and "'utilities' was read as" in u["guessed"] and "budget's name" in u["guessed"]
    d = budgets.budget_set(conn, "dining", 400)
    assert d["counts"] == ["Food and drink › Restaurant"] and "'dining' was read as Food and drink › Restaurant" in d["guessed"]
    g = budgets.budget_set(conn, "groceries", 600)
    assert "guessed" not in g and g["counts"] == ["Food and drink › Groceries"]
    for name in ("subscriptions", "kids", "car insurance"):
        with pytest.raises(ValueError, match=f"'{name}' is not a category.*--merchant"):
            budgets.budget_set(conn, name, 50)
    with pytest.raises(ValueError, match=r'maybe "Transportation" or "Insurance"|maybe "Insurance" or "Transportation"'):
        budgets.budget_set(conn, "car insurance", 50)
    f = budgets.budget_set(conn, "food", 900, ["FOOD_AND_DRINK"])
    assert "(all of it: beer wine and liquor, coffee" in f["counts"][0] and "guessed" not in f


def test_overlapping_budgets_warn_and_overall_counts_each_charge_once(conn):
    seed(conn)
    txn(conn, "r1", "chk", "2026-09-05", 60, "CHIPOTLE", "Chipotle", "mch_chip", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    txn(conn, "g1", "chk", "2026-09-06", 100, "SAFEWAY", "Safeway", "mch_safe")
    budgets.budget_set(conn, "dining", 400)
    b = budgets.budget_set(conn, "eating", 500, ["FOOD_AND_DRINK"])
    assert b["overlaps"] == [{"budget": "dining", "charges": 1}] and "also in the dining budget" in b["warning"]
    assert "overlaps" not in budgets.budget_set(conn, "eating", 550)   # a limit edit alone does not re-warn
    o = budgets.overall(conn, AS_OF)
    assert o["spent"] == 160 and o["limit"] == 950 and o["uncategorized"] == 0   # Chipotle is in both, counted once
    assert sum(b["spent"] for b in budgets.budget_list(conn, AS_OF)) == 220   # what the old Overall added up
    # the same category before any charge is an overlap too
    budgets.budget_remove(conn, "eating")   # a removed budget leaves Overall: its limit and its charges
    assert budgets.overall(conn, AS_OF) == {"spent": 60, "limit": 400, "uncategorized": 0}
    budgets.budget_set(conn, "travel", 100)
    assert budgets.budget_set(conn, "trips", 100, ["TRAVEL_FLIGHTS"])["overlaps"] == [{"budget": "travel", "charges": 0}]


def test_change_category_from_a_transaction_one_charge_or_every_charge(conn):
    seed(conn)
    txn(conn, "c1", "chk", "2026-09-05", 30, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    txn(conn, "c2", "chk", "2026-09-12", 40, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    budgets.tx_category_set(conn, "c1", "gas")
    r = budgets.tx_merchant_rule(conn, "c1", "Groceries")   # the picker's label resolves
    assert r["canonical"] == "mch_costco" and r["category"] == "FOOD_AND_DRINK_GROCERIES" and "one_time_edits_kept" not in r
    assert {x[0] for x in conn.execute("SELECT category FROM tx_now")} == {"FOOD_AND_DRINK_GROCERIES"}   # the charge picked follows the rule too
    with pytest.raises(budgets.ResolveError):
        budgets.tx_merchant_rule(conn, "c2", "zzzz")


def test_the_recent_table_shows_the_detailed_category_and_carries_the_id(conn):
    seed(conn)
    txn(conn, "r1", "chk", today(), 60, "CHIPOTLE", "Chipotle", "mch_chip", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    txn(conn, "u1", "chk", today(), 9, "HSBC TRANSFER", None, None, None, None)
    rows = {r["transaction_id"]: r for r in charts.table_preset("recent_transactions")["data"]["values"]}
    assert rows["r1"]["category"] == "restaurant" and rows["u1"]["category"] == "uncategorized"
    assert [r["transaction_id"] for r in charts.table_preset("uncategorized")["data"]["values"]] == ["u1"]


def test_an_import_says_how_many_rows_have_no_category(conn):
    seed(conn)
    acct = imports.add_account(conn, "Everyday", "HSBC", "checking")
    csv = "Date,Description,Amount\n2026-09-01,HSBC TRANSFER,-500.00\n2026-09-02,CORNER DELI,-12.50\n2026-09-03,CORNER DELI,-8.00\n"
    r = imports.apply(conn, acct["account_id"], imports.parse(csv))
    assert r["uncategorized"] == 3 and [m["merchant"] for m in r["uncategorized_merchants"]] == ["CORNER DELI", "HSBC TRANSFER"]
    assert "finnamon category" in r["next"]
    assert imports.apply(conn, acct["account_id"], imports.parse(csv))["uncategorized"] == 0   # the same file again adds nothing
    assert budgets.overall(conn, AS_OF)["uncategorized"] == 3
    budgets.category_set(conn, "HSBC TRANSFER", "transfer")
    assert budgets.uncategorized(conn)["uncategorized"] == 2
    assert conn.execute("SELECT flow FROM tx_now WHERE name='HSBC TRANSFER'").fetchone()[0] == "transfer"   # no longer spending


def test_threshold_refuses_a_card_and_lists_the_closest_accounts(conn):
    seed(conn)
    with pytest.raises(ValueError, match="credit account.*would never fire"):
        budgets.threshold_set(conn, "Sapphire", 500)
    conn.execute("UPDATE accounts SET name='Total Checking' WHERE account_id='chk'")
    with pytest.raises(ValueError, match=r'closest: "Total Checking" …4821'):
        budgets.threshold_set(conn, "Chase Checking", 1000)
    assert budgets.threshold_set(conn, "4821", 1000)["account"] == "Total Checking"


def test_the_cli_surface(conn, capsys):
    seed(conn)
    txn(conn, "c1", "chk", "2026-09-05", 30, "COSTCO", "Costco", "mch_costco")
    assert json.loads(run_cli(capsys, "budget", "overall", "--as-of", AS_OF))["spent"] == 0
    picker = json.loads(run_cli(capsys, "category", "list", "--json"))
    assert {"code": "FOOD_AND_DRINK_GROCERIES", "label": "Groceries", "primary": "FOOD_AND_DRINK"} in picker["categories"] and picker["merchants"] == ["Costco"]
    assert json.loads(run_cli(capsys, "category", "--tx", "c1", "Restaurant", "--every"))["category"] == "FOOD_AND_DRINK_RESTAURANT"
    assert json.loads(run_cli(capsys, "category", "--uncategorized")) == {"uncategorized": 0, "merchants": []}


# Value: protects=a refused category from the dashboard's "every charge" picker leaves that charge's one-time edit in place;
# fails_when=the DELETE of the edit runs outside store.tx, or before the category is resolved; why_new=the existing test refuses on a
# charge with no edit, so a lost edit would pass; seam=none
def test_every_charge_refused_keeps_the_one_time_edit(conn):
    seed(conn)
    txn(conn, "c1", "chk", "2026-09-05", 30, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    budgets.tx_category_set(conn, "c1", "gas")
    for bad, err in (("zzzz", budgets.ResolveError), ("food", ValueError)):   # no category; a primary, where a rule needs a detailed one
        with pytest.raises(err):
            budgets.tx_merchant_rule(conn, "c1", bad)
        assert conn.execute("SELECT category FROM tx_now WHERE transaction_id='c1'").fetchone()[0] == "TRANSPORTATION_GAS"
    assert conn.execute("SELECT count(*) FROM category_override").fetchone()[0] == 0
    with pytest.raises(ValueError, match="no transaction 'nope'"):
        budgets.tx_merchant_rule(conn, "nope", "Groceries")


# Value: protects=a new budget warns about one that counts the same charges before any charge exists (primary over detailed,
# a shared merchant), and stays quiet when nothing is shared; fails_when=the primary_of(theirs) or merchant selector comparison
# is dropped, or overlap fires for unrelated budgets; why_new=existing overlap tests cover a past charge and detailed-under-primary only; seam=none
def test_overlap_before_any_charge_and_none_for_unrelated_budgets(conn):
    seed(conn)
    txn(conn, "c1", "chk", "2026-01-05", 30, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")   # a past month: no charge in common this month
    budgets.budget_set(conn, "dining", 400)
    assert "overlaps" not in budgets.budget_set(conn, "travel", 100)
    assert budgets.budget_set(conn, "food", 900, ["FOOD_AND_DRINK"])["overlaps"] == [{"budget": "dining", "charges": 0}]
    budgets.budget_set(conn, "bulk", 200, ["TRAVEL_FLIGHTS"], ["Costco"])
    assert {o["budget"] for o in budgets.budget_set(conn, "warehouse", 200, None, ["Costco"])["overlaps"]} == {"bulk"}


# Value: protects=the dashboard's reads (budget overall on every summary load, alias --list, category --uncategorized) never rewrite
# the board, and the new flags' misuse is refused before any write; fails_when=a read drops out of CHART_READS/_changes_charts, or
# a usage guard is removed; why_new=test_chart_refresh checks the older reads only; seam=none
def test_new_reads_leave_the_board_alone_and_misuse_is_refused(home, conn, monkeypatch, capsys):
    seed(conn)
    calls = []
    monkeypatch.setattr(charts, "refresh", lambda c=None: calls.append(1) or [])
    for argv in (["budget", "overall"], ["alias", "--list"], ["category", "--uncategorized"], ["category", "--rules"], ["category", "list", "--json"]):
        run_cli(capsys, *argv)
    assert calls == [], "reads do not refresh the board"
    for argv in (["category", "--tx", "t1", "--clear", "--every"], ["category", "costco", "groceries", "--every"],
                 ["alias", "x", "--list"], ["alias", "x", "--remove", "y"], ["alias", "only-one"]):
        with pytest.raises(SystemExit):
            run_cli(capsys, *argv)
        assert "usage" in capsys.readouterr().err, argv
    assert conn.execute("SELECT count(*) FROM category_override").fetchone()[0] == 0


# Value: protects=a threshold with no accounts at all says so instead of offering an empty "closest" list;
# fails_when=the near/empty branch of find_account's message is broken; why_new=the existing test always has accounts; seam=none
def test_threshold_with_no_accounts_says_there_are_none(conn):
    with pytest.raises(ValueError, match="matches no account; there are no accounts yet"):
        budgets.threshold_set(conn, "Checking", 500)


def test_a_shared_label_stays_ambiguous_and_is_named_by_its_code():
    # Value: protects "Savings" never silently resolving to one of its two codes; a refusal names such a category so it can be typed back
    from finnamon import taxonomy
    code, cands = taxonomy.resolve("Savings")
    assert code is None and {"TRANSFER_IN_SAVINGS", "TRANSFER_OUT_SAVINGS"} <= set(cands)
    assert budgets._cat_name("TRANSFER_IN_SAVINGS") == "TRANSFER_IN_SAVINGS" and budgets._cat_name("FOOD_AND_DRINK_GROCERIES") == '"Groceries"'


def test_a_big_import_reports_uncategorized_with_capped_lists(conn):
    # Value: protects the summary of an import larger than SQLite's bound-variable limit, and the caps on what it lists
    seed(conn)
    acct = imports.add_account(conn, "Everyday", "HSBC", "checking")
    csv = "Date,Description,Amount\n" + "".join(f"2026-0{1 + i % 9}-{1 + i % 28:02d},SHOP {i % 40},-{i + 1}.00\n" for i in range(1500))
    r = imports.apply(conn, acct["account_id"], imports.parse(csv))
    assert r["uncategorized"] == r["added"] == 1500 and len(r["uncategorized_merchants"]) == budgets.UNCATEGORIZED_SHOWN
    assert all(len(m["transaction_ids"]) <= budgets.UNCATEGORIZED_SHOWN for m in r["uncategorized_merchants"])


def test_removing_an_alias_says_what_was_keyed_on_its_name(conn):
    # Value: protects against a rule, a budget or a suppression silently stopping when the alias under it goes
    seed(conn)
    txn(conn, "f1", "chk", "2026-09-05", 400, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    budgets.category_set(conn, "fence contractor", "repairs")
    budgets.budget_set(conn, "yard", 500, merchants=["fence contractor"])
    r = budgets.alias_remove(conn, "SQ *PMT 8827")
    assert r["still_keyed_on_it"] == {"category_rules": 1, "budgets": ["yard"]} and "stops covering them" in r["warning"]
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    budgets.alias_set(conn, "SQ *PMT%", "fence contractor")
    assert "warning" not in budgets.alias_remove(conn, "SQ *PMT 8827")   # another alias still makes the name


def test_uncategorized_is_spending_only(conn):
    # Value: protects the count from money in and paired transfers, which no budget would count anyway
    seed(conn)
    txn(conn, "in1", "chk", "2026-09-01", -2000, "DEPOSIT", None, None, None, None)
    txn(conn, "out1", "chk", "2026-09-02", 40, "CORNER DELI", None, None, None, None)
    txn(conn, "out2", "cc", "2026-09-03", 12, "CORNER DELI", "Corner Deli", "mch_deli", None, None)
    u = budgets.uncategorized(conn)
    assert u["uncategorized"] == 2 and budgets.overall(conn, AS_OF)["uncategorized"] == 2
    assert [m["charges"] for m in u["merchants"]] == [2], "one merchant as people see it, whatever its ids"
