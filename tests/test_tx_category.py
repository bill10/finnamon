"""A one-time category edit on one transaction (`finnamon category --tx`) versus the merchant rule (`finnamon category
<merchant>`): tx_now, tx_flow and the detectors' prelude all agree, the one-time edit wins, and clearing restores."""
import json

import pytest

from finnamon import budgets, cli, detect
from tests.conftest import AS_OF, seed, txn

PRELUDE_SQL = detect.PRELUDE.read_text().replace("__KIND__", "x") + "SELECT transaction_id, category, category_primary, flow FROM tx"


def cats(conn, as_of=AS_OF):
    view = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT transaction_id, category, category_primary, flow FROM tx_now")}
    prelude = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(PRELUDE_SQL, {"as_of": as_of})}
    assert view == prelude
    return {k: v[0] for k, v in view.items()}


def costco(conn):
    seed(conn)
    for tid, day in (("c1", "2026-09-05"), ("c2", "2026-09-12")):
        txn(conn, tid, "chk", day, 80, "COSTCO WHSE", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")


def test_one_transaction_moves_only_that_one_and_beats_the_merchant_rule_and_clears(conn):
    costco(conn)
    r = budgets.tx_category_set(conn, "c1", "FOOD_AND_DRINK_GROCERIES")
    assert r["category"] == "FOOD_AND_DRINK_GROCERIES" and r["merchant"] == "Costco"
    assert cats(conn, "2099-01-01") == {"c1": "FOOD_AND_DRINK_GROCERIES", "c2": "GENERAL_MERCHANDISE_SUPERSTORES"}

    budgets.category_set(conn, "Costco", "HOME_IMPROVEMENT_HARDWARE")              # the rule moves the rest, and future charges
    txn(conn, "c3", "chk", "2026-09-18", 40, "COSTCO WHSE", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    assert cats(conn, "2099-01-01") == {"c1": "FOOD_AND_DRINK_GROCERIES", "c2": "HOME_IMPROVEMENT_HARDWARE", "c3": "HOME_IMPROVEMENT_HARDWARE"}

    assert budgets.tx_category_set(conn, "c1", None) == {**r, "category": "HOME_IMPROVEMENT_HARDWARE", "cleared": True}
    assert cats(conn, "2099-01-01") == {k: "HOME_IMPROVEMENT_HARDWARE" for k in ("c1", "c2", "c3")}
    assert budgets.tx_category_set(conn, "c1", None)["cleared"] is False


def test_a_mirror_accounts_id_is_refused(conn):
    costco(conn)
    conn.execute("UPDATE accounts SET mirror_of='chk' WHERE account_id='cc'")
    txn(conn, "m1", "cc", "2026-09-05", 80, "COSTCO WHSE", "Costco", "mch_costco")
    with pytest.raises(ValueError, match="no transaction"):
        budgets.tx_category_set(conn, "m1", "groceries")


def test_prelude_time_scopes_the_one_time_edit(conn):
    costco(conn)
    conn.execute("INSERT INTO tx_category_override (transaction_id, pfc_primary, pfc_detailed, created_at) VALUES ('c1','FOOD_AND_DRINK','FOOD_AND_DRINK_GROCERIES','2026-09-15')")
    before = {r[0]: r[1] for r in conn.execute(PRELUDE_SQL, {"as_of": "2026-09-10 00:00:00"})}
    assert before["c1"] == "GENERAL_MERCHANDISE_SUPERSTORES"
    assert cats(conn)["c1"] == "FOOD_AND_DRINK_GROCERIES"


def test_a_one_time_transfer_edit_makes_that_row_a_transfer_and_counts_in_budgets(conn):
    costco(conn)
    budgets.budget_set(conn, "groceries", 500, "FOOD_AND_DRINK_GROCERIES")
    budgets.tx_category_set(conn, "c1", "FOOD_AND_DRINK_GROCERIES")
    assert budgets.month_to_date(conn, "FOOD_AND_DRINK_GROCERIES", AS_OF) == 80
    budgets.tx_category_set(conn, "c2", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    assert dict(conn.execute("SELECT transaction_id, flow FROM tx_now"))["c2"] == "transfer"


def test_cli_tx_form_validates_and_clears(home, conn, capsys):
    costco(conn)
    cli.main(["category", "--tx", "c1", "groceries"])
    assert json.loads(capsys.readouterr().out)["category"] == "FOOD_AND_DRINK_GROCERIES"
    for argv, msg in ((["category", "--tx", "nope", "groceries"], "no transaction"),
                      (["category", "--tx", "c1", "FOOD_AND_DRINK"], "detailed category"),
                      (["category", "--tx", "c1"], "usage"),
                      (["category", "--tx", "c1", "groceries", "extra"], "usage"),
                      (["category", "--tx", "c1", "groceries", "--clear"], "usage"),
                      (["category", "--tx", "", "groceries"], "no transaction"),
                      (["category", "--clear"], "usage")):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert msg in capsys.readouterr().err
    cli.main(["category", "--tx", "c1", "--clear"])
    assert json.loads(capsys.readouterr().out)["category"] == "GENERAL_MERCHANDISE_SUPERSTORES"
    assert conn.execute("SELECT count(*) FROM tx_category_override").fetchone()[0] == 0
