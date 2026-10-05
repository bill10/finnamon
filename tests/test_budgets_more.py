"""The write primitives Claude calls: remove, category guard, aliases, canonical lookup, and every `normal` shape."""
import json

import pytest

from finnamon import budgets, taxonomy
from tests.conftest import AS_OF, seed, txn


def test_budget_remove_category_guard_alias_and_resolve_errors(conn):
    seed(conn)
    budgets.budget_set(conn, "Dining ", 300)
    assert budgets.budget_remove(conn, "dining") is True and budgets.budget_remove(conn, "dining") is True  # idempotent update
    assert budgets.budget_remove(conn, "never") is False
    assert budgets.budget_list(conn, AS_OF) == []  # inactive budgets are hidden
    budgets.budget_set(conn, "dining", 350)  # re-set reactivates with the new limit
    assert budgets.budget_list(conn, AS_OF)[0]["monthly_limit"] == 350
    # a merchant override must be a detailed category
    with pytest.raises(ValueError, match="detailed category"):
        budgets.category_set(conn, "Costco", "food")
    # aliases feed canonical_for; entity id still wins when Plaid knows the merchant
    assert budgets.alias_set(conn, " SQ *PMT 8827 ", " fence contractor ") == {"name": "SQ *PMT 8827", "canonical": "fence contractor", "matches": 0, "distinct_names": 0}
    assert budgets.canonical_for(conn, "sq *pmt 8827") == "fence contractor"
    assert budgets.canonical_for(conn, "Unknown Shop") == "Unknown Shop"
    txn(conn, "t", "chk", "2026-09-10", 10, "COSTCO WHSE", "Costco", "mch_costco")
    assert budgets.canonical_for(conn, "costco") == "mch_costco"
    # a pattern alias (issue 18): canonical_for resolves a raw string through it, budgets see the override through it
    txn(conn, "m", "chk", "2026-09-11", 2400, "Loan Payment Confirmation# 998877", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_OTHER_PAYMENT")
    r = budgets.alias_set(conn, "Loan Payment Confirmation#%", "Bank of America mortgage")
    assert (r["name"], r["matches"], r["distinct_names"]) == ("Loan Payment Confirmation#%", 1, 1)   # the blast radius, at write time
    assert budgets.canonical_for(conn, "Loan Payment Confirmation# XXXXX66675") == "Bank of America mortgage"
    assert budgets.canonical_for(conn, "Bank of America mortgage") == "Bank of America mortgage"
    for bad in ("%", "ab%", "%_%", "%a b%", "% - %"):
        with pytest.raises(ValueError, match="literal characters"):
            budgets.alias_set(conn, bad, "everything")
    with pytest.raises(ValueError, match="raw name"):
        budgets.alias_set(conn, "x", " ")
    conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant','k-m','m','{}',?)", (AS_OF,))
    alert_id = conn.execute("SELECT max(id) FROM alerts").fetchone()[0]
    assert budgets.normal(conn, alert_id=alert_id)["canonical"] == "Bank of America mortgage"   # "it is normal" on the alert lands on the pattern's canonical
    budgets.category_set(conn, "Bank of America mortgage", "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE")   # a spend category, so month_to_date counts it
    assert budgets.month_to_date(conn, budgets.budget_set(conn, "repairs", 3000, "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE")["id"], AS_OF)[0] == 2400
    txn(conn, "m0", "chk", "2026-08-11", 2400, "Loan Payment Confirmation# 112233", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_OTHER_PAYMENT")
    sug = {c["category"]: c for c in budgets.suggest(conn, 3, AS_OF)["categories"]}
    assert sug["HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE"]["months"]["2026-08"] == 2400
    txn(conn, "f", "chk", "2026-09-12", 900, "SQ *PMT 8827")
    r = budgets.category_set(conn, "SQ *PMT 8827", "repairs")
    assert r["canonical"] == "fence contractor" and r["category"] == "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE"  # override keyed on the alias canonical, as the prelude will compute it
    # resolution errors carry the candidate list (or none)
    with pytest.raises(budgets.ResolveError) as e:
        budgets.budget_set(conn, "other", 1)
    assert e.value.candidates and "matches" in str(e.value)
    with pytest.raises(budgets.ResolveError) as e:
        budgets.budget_set(conn, "zzzz", 1)
    assert e.value.candidates == [] and "no category" in str(e.value)
    # taxonomy leftovers: a lone token that hits exactly one code, and the listing
    assert taxonomy.resolve("sewage") == ("RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT", [])
    assert taxonomy.resolve("food-and-drink") == ("FOOD_AND_DRINK", [])
    assert taxonomy.resolve("") == (None, [])
    assert taxonomy.primary_of("NOPE") is None
    lst = taxonomy.listing().splitlines()
    assert "FOOD_AND_DRINK" in lst and "  FOOD_AND_DRINK_GROCERIES" in lst


def test_normal_every_shape(conn):
    seed(conn)
    txn(conn, "t1", "chk", "2026-09-10", 80, "WF", "Whole Foods", "mch_wf")
    # plain merchant + account + cap: canonical resolved from history, account resolved by name
    r = budgets.normal(conn, "Whole Foods", account="checking", max_amount=150, note="weekly")
    assert (r["canonical"], r["account_id"], r["max_amount"], r["kind"]) == ("mch_wf", "chk", 150, None)
    # an entity id is taken as-is; an ambiguous account is refused
    assert budgets.normal(conn, "mch_zzz")["canonical"] == "mch_zzz"
    with pytest.raises(ValueError, match="matches 0 accounts"):
        budgets.normal(conn, "x", account="nothing-like-this")
    # from an alert on a stream (no transaction): merchant and kind come from the payload, alert gets resolved
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('anomaly','anomaly:recurring_changed','anom:rec:s1','{\"merchant\": \"Comcast\"}',?)", (AS_OF,))
    r = budgets.normal(conn, alert_id=1)
    assert (r["canonical"], r["kind"]) == ("Comcast", "anomaly:recurring_changed")
    assert conn.execute("SELECT resolved_at IS NOT NULL FROM alerts WHERE id=1").fetchone()[0] == 1
    # from an alert whose transaction has since been removed: falls back to the payload
    conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant','k2','ghost','{\"name\": \"GHOST LLC\"}',?)", (AS_OF,))
    assert budgets.normal(conn, alert_id=2, kind="duplicate_charge")["canonical"] == "GHOST LLC"
    assert conn.execute("SELECT kind FROM suppressions ORDER BY id DESC LIMIT 1").fetchone()[0] == "duplicate_charge"
    # explicit merchant with an alert id: the alert is resolved but its payload is not consulted
    txn(conn, "c1", "chk", "2026-09-12", 40, "COSTCO WHSE #12", "Costco", None)
    r = budgets.normal(conn, "Costco", alert_id=2)
    assert r["canonical"] == "Costco" and r["kind"] is None
    with pytest.raises(ValueError, match="no alert 99"):
        budgets.normal(conn, alert_id=99)
    # find_account: by id, by mask with a leading dot, by partial name (case-insensitive); ambiguity is an error
    assert budgets.find_account(conn, "cc")["name"] == "Sapphire"
    assert budgets.find_account(conn, ".4821")["account_id"] == "chk"
    assert budgets.find_account(conn, "SAPPH")["account_id"] == "cc"
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, mask, owner) VALUES ('chk2','item1','Chase Checking 2','depository','9999','bill')")
    with pytest.raises(ValueError, match="matches 2 accounts"):
        budgets.find_account(conn, "chase")
    assert len(budgets.suggest(conn)["existing_budgets"]) == 0 and budgets.suggest(conn, 1)["months"] == 1


def test_normal_alert_id_still_resolves_when_the_account_became_a_mirror(conn):
    """tx_now excludes mirrors, and link.py:306-311 can turn an account that already has alerts into one (the
    mirror is promoted when the original's item breaks). The lookup then finds no row, so this pins the fallback
    that saves it: the payload's raw merchant goes through canonical_for, which resolves the alias the same way."""
    seed(conn)
    txn(conn, "m1", "chk", "2026-09-10", 50, "SQ *PMT 8827", None, None)
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('chk2','item1','Chase Checking','depository','checking','4821','joint')")
    conn.execute("UPDATE accounts SET mirror_of='chk2' WHERE account_id='chk'")
    assert conn.execute("SELECT 1 FROM tx_now WHERE transaction_id='m1'").fetchone() is None   # the view really does drop it
    cur = conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) "
                       "VALUES ('anomaly','anomaly:first_merchant','k-m','m1',?,?)", (json.dumps({"merchant": "SQ *PMT 8827"}), AS_OF))
    assert budgets.normal(conn, alert_id=cur.lastrowid)["canonical"] == "fence contractor"
