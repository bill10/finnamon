"""QA 10/4 blockers 1 and 4: `category` and `normal` take any name the household sees for a merchant and refuse a name
that covers no charge (never a dead rule); `normal --alert` on a low-balance, budget or stale-sync alert resolves it."""
import json

import pytest

from finnamon import budgets, cli
from tests.conftest import AS_OF, seed, txn


@pytest.fixture
def house(conn):
    seed(conn)
    txn(conn, "w1", "chk", "2026-09-10", 80, "WHOLEFDS MKT #10234", "Whole Foods Market", "mch_wf")
    txn(conn, "w2", "chk", "2026-09-17", 60, "WHOLEFDS MKT #10234", "Whole Foods Market", "mch_wf")
    txn(conn, "a1", "cc", "2026-09-11", 25, "AMZN Mktp US*2K4", "Amazon", "mch_amzn")
    txn(conn, "a2", "cc", "2026-09-12", 15, "AMAZON.COM*9X1", "Amazon", None)   # same merchant, no entity id from Plaid
    txn(conn, "t1", "chk", "2026-09-13", 40, "TRADER JOE S #552", "Trader Joe's", "mch_tj")
    txn(conn, "s1", "cc", "2026-09-14", 50, "SHELL OIL 5744", "Shell", "mch_shell", "TRANSPORTATION", "TRANSPORTATION_GAS")
    txn(conn, "c1", "cc", "2026-09-15", 14, "CHIPOTLE 1234", None, None, "FOOD_AND_DRINK", "FOOD_AND_DRINK_FAST_FOOD")
    txn(conn, "c2", "cc", "2026-09-16", 12, "CHIPOTLE ONLINE 88", None, None, "FOOD_AND_DRINK", "FOOD_AND_DRINK_FAST_FOOD")
    return conn


def cat(conn, tid):
    return conn.execute("SELECT category FROM tx_now WHERE transaction_id=?", (tid,)).fetchone()[0]


def overrides(conn):
    return conn.execute("SELECT count(*) FROM category_override").fetchone()[0]


def test_category_by_raw_bank_text_lands_on_the_merchant(house):
    r = budgets.category_set(house, "wholefds mkt #10234", "restaurant")
    assert (r["canonical"], r["charges"]) == ("mch_wf", 2) and cat(house, "w1") == "FOOD_AND_DRINK_RESTAURANT"


@pytest.mark.parametrize("typed, meant", [("WHOLEFDS MKT", "Whole Foods Market"), ("Amazn", "Amazon"), ("Trader", "Trader Joe's"), ("Shel", "Shell")])
def test_a_partial_or_misspelled_name_is_refused_with_the_merchant_it_meant(house, typed, meant):
    with pytest.raises(ValueError, match="0 charges match") as e:
        budgets.category_set(house, typed, "groceries")
    assert meant in str(e.value) and overrides(house) == 0
    with pytest.raises(ValueError, match=f"did you mean.*{meant}"):
        budgets.normal(house, typed)
    assert house.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0


def test_an_unknown_merchant_is_refused_and_writes_nothing(house):
    with pytest.raises(ValueError, match="0 charges match 'Nopa SF'"):
        budgets.normal(house, "Nopa SF")
    with pytest.raises(ValueError, match="0 charges match"):
        budgets.category_set(house, "Nopa SF", "restaurant")
    assert overrides(house) == 0 and house.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0


def test_one_display_name_over_two_ids_covers_both(house):
    r = budgets.category_set(house, "Amazon", "groceries")
    assert set(r["canonicals"]) == {"mch_amzn", "Amazon"} and r["charges"] == 2
    assert cat(house, "a1") == cat(house, "a2") == "FOOD_AND_DRINK_GROCERIES"
    assert len(budgets.normal(house, "amazon")["ids"]) == 2
    assert budgets.category_clear(house, "Amazon")["cleared"] and overrides(house) == 0


def test_a_name_over_two_merchants_lists_them(house):
    txn(house, "x1", "chk", "2026-09-18", 9, "SHELL", "Shell Recharge", None)   # raw text "SHELL" here, Plaid's "Shell" there
    assert budgets.category_set(house, "Shell", "gas")["canonical"] == "mch_shell"   # the name the household sees wins
    txn(house, "q1", "chk", "2026-09-18", 6, "SQ *SHOP", "Blue Bottle", None)
    txn(house, "q2", "chk", "2026-09-19", 7, "SQ *SHOP", "Philz", None)
    with pytest.raises(ValueError, match=r"several merchants: Blue Bottle \(1 charges\); Philz \(1 charges\)"):
        budgets.category_set(house, "sq *shop", "coffee")
    assert overrides(house) == 1


def test_alias_display_name_and_pattern_both_resolve(house):
    budgets.alias_set(house, "CHIPOTLE%", "Chipotle Mexican Grill")
    r = budgets.category_set(house, "Chipotle Mexican Grill", "restaurant")
    assert (r["canonical"], r["charges"]) == ("Chipotle Mexican Grill", 2) and cat(house, "c2") == "FOOD_AND_DRINK_RESTAURANT"
    assert budgets.normal(house, "chipotle%")["canonical"] == "Chipotle Mexican Grill"
    # an alias over a merchant Plaid knows: its display name reaches the entity id the detectors key on
    budgets.alias_set(house, "TRADER JOE S%", "TJ's")
    assert budgets.normal(house, "TJ's")["canonical"] == "mch_tj"


def test_clear_removes_a_rule_by_its_own_key_even_with_no_charges_left(house):
    house.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed) VALUES ('Amazn','FOOD_AND_DRINK','FOOD_AND_DRINK_GROCERIES')")   # a dead rule from before
    assert budgets.category_clear(house, "amazn")["cleared"] and overrides(house) == 0
    assert budgets.category_clear(house, "Nopa SF")["cleared"] is False


def test_an_unknown_kind_is_refused_with_the_valid_ones(house, capsys):
    with pytest.raises(SystemExit):
        cli.main(["normal", "Shell", "--kind", "dupes"])
    err = capsys.readouterr().err
    assert "duplicate_charge" in err and "anomaly:amount_outlier" in err
    assert house.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0
    assert budgets.normal(house, "Shell", kind="amount_outlier")["kind"] == "anomaly:amount_outlier"   # the short name works


def alert(conn, kind, key, payload):
    return conn.execute("INSERT INTO alerts (tier, kind, key, account_id, payload_json, as_of) VALUES ('rule',?,?,NULL,?,?)",
                        (kind, key, json.dumps(payload), AS_OF)).lastrowid


@pytest.mark.parametrize("kind, payload, says", [
    ("low_balance", {"account": "Chase Checking", "mask": "4821", "available": 180.5, "threshold": 500}, "once Chase Checking has gone back above $500"),
    ("budget_pace", {"budget": "dining", "category": "FOOD_AND_DRINK", "limit": 300, "spent": 320, "state": "over"}, "for this month"),
    ("sync_health", {"item_id": "item1", "institution": "Chase", "status": "error"}, "only if Chase syncs and then has a new problem"),
])
def test_its_normal_on_an_alert_no_rule_can_quiet_resolves_it(house, capsys, kind, payload, says):
    a = alert(house, kind, f"{kind}:1", payload)
    cli.main(["normal", "--alert", str(a)])
    r = json.loads(capsys.readouterr().out)
    assert r["rule"] is None and says in r["next"]
    assert house.execute("SELECT resolution, suppression_id FROM alerts WHERE id=?", (a,)).fetchone()[:] == ("normal", None)
    assert house.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0
    cli.main(["alerts", "--undo", str(a)])   # the dashboard's Undo
    assert json.loads(capsys.readouterr().out)["removed_rule"] is None
    assert house.execute("SELECT resolved_at FROM alerts WHERE id=?", (a,)).fetchone()[0] is None


def test_a_kind_with_no_note_of_its_own_still_resolves_and_two_ids_with_an_alert_are_refused(house):
    a = alert(house, "new_recurring", "recurring:s1", {"merchant": "Netflix", "stream_id": "s1"})
    assert "next one still alerts" in budgets.normal(house, alert_id=a)["next"]
    b = alert(house, "duplicate_charge", "dup:x:y", {"amount": 10})
    with pytest.raises(ValueError, match="several merchant ids"):   # an undo removes the one rule it recorded
        budgets.normal(house, "Amazon", alert_id=b)


def test_clear_removes_a_rule_written_as_typed_and_shows_an_ambiguous_name(house):
    house.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed) VALUES ('whole foods market','FOOD_AND_DRINK','FOOD_AND_DRINK_GROCERIES')")
    assert budgets.category_clear(house, "Whole Foods Market")["cleared"] and overrides(house) == 0   # the old bug's key, though the name resolves
    txn(house, "q1", "chk", "2026-09-18", 6, "SQ *SHOP", "Blue Bottle", None)
    txn(house, "q2", "chk", "2026-09-19", 7, "SQ *SHOP", "Philz", None)
    with pytest.raises(ValueError, match="several merchants"):
        budgets.category_clear(house, "sq *shop")


def test_a_recurring_changed_rule_by_name_also_keys_on_the_stream(house):
    budgets.alias_set(house, "NETFLIX.COM", "Netflix Streaming")
    txn(house, "n1", "cc", "2026-09-05", 15.49, "NETFLIX.COM", None, None)
    house.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, status, first_seen_at) VALUES ('s9','cc','outflow',NULL,'MATURE',?)", (AS_OF,))
    house.execute("UPDATE recurring SET description='NETFLIX.COM' WHERE stream_id='s9'")
    r = budgets.normal(house, "Netflix Streaming", kind="recurring_changed")
    assert {x[0] for x in house.execute("SELECT canonical FROM suppressions")} == {"Netflix Streaming", "NETFLIX.COM"} and len(r["ids"]) == 2


def test_an_over_budget_note_waits_for_next_month(house):
    a = alert(house, "budget_pace", "budget:1:2026-09:over", {"budget": "dining", "state": "over", "limit": 300, "spent": 320})
    assert "next month if it goes over again" in budgets.normal(house, alert_id=a)["next"]


# Value: protects=`finnamon normal X --kind recurring_price` is accepted and writes a rule of that kind; fails_when=rule_kinds drops
# recurring_price (the CLI refuses it as an unknown kind); why_new=only the --alert path was tested; seam=none
def test_recurring_price_is_a_kind_a_rule_can_name(house, capsys):
    cli.main(["normal", "Shell", "--kind", "recurring_price"])
    assert json.loads(capsys.readouterr().out)["kind"] == "recurring_price"
    assert house.execute("SELECT kind FROM suppressions").fetchone()[0] == "recurring_price"
