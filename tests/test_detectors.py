"""Every rule and candidate: hit, miss, baseline scoping, dedup, suppression, alias/override time-scoping,
mirror exclusion, and an EXPLAIN check that the self-join in duplicate_charge uses an index."""
import json

import pytest

from finnamon import budgets, detect, notify, store
from tests import conftest
from tests.conftest import AS_OF, seed, txn

RULES = {p.stem: p for p in detect.detectors()}


def run(conn, name, as_of=AS_OF):
    return detect.run_one(conn, RULES[name], as_of)


def alerts(conn, kind=None):
    q = "SELECT * FROM alerts" + (f" WHERE kind='{kind}'" if kind else "") + " ORDER BY id"
    return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in conn.execute(q)]


# --- prelude -------------------------------------------------------------------------------------

def test_prelude_canonical_and_category_precedence(conn):
    seed(conn)
    txn(conn, "a", "chk", "2026-09-10", 50, "COSTCO WHSE #123", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    txn(conn, "b", "chk", "2026-09-10", 20, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('SQ *PMT 8827', 'fence contractor', '2026-09-01')")
    conn.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed, created_at) VALUES ('mch_costco', 'FOOD_AND_DRINK', 'FOOD_AND_DRINK_GROCERIES', '2026-09-01')")
    sql = detect.PRELUDE.read_text().replace("__KIND__", "x") + "SELECT transaction_id, canonical, display, category, category_primary FROM tx ORDER BY transaction_id"
    rows = conn.execute(sql, {"as_of": AS_OF}).fetchall()
    assert tuple(rows[0]) == ("a", "mch_costco", "Costco", "FOOD_AND_DRINK_GROCERIES", "FOOD_AND_DRINK")   # entity id wins; override applied
    assert tuple(rows[1]) == ("b", "fence contractor", "fence contractor", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES", "GENERAL_SERVICES")
    # time-scoping: same query as of before the alias/override existed sees neither
    rows = conn.execute(sql, {"as_of": "2026-08-15 00:00:00"}).fetchall()
    assert rows[0]["category"] == "GENERAL_MERCHANDISE_SUPERSTORES" and rows[1]["canonical"] == "SQ *PMT 8827"


def test_tx_now_view_matches_prelude_and_every_read_path(conn):
    """Issue 23: a pattern alias plus a category override must move the transaction on every read path, not
    just in the detectors. Live case: four 1st Security Bank mortgage payments Plaid filed under rent."""
    from finnamon import charts
    seed(conn)
    from datetime import date, timedelta
    today = conftest.today()
    last = (date.fromisoformat(today).replace(day=1) - timedelta(days=10)).isoformat()   # suggest() looks at whole months before this one
    for tag, when in (("", today), ("_prev", last)):
        txn(conn, f"m1{tag}", "chk", when, 3605.99, "1st Security Bank Loan Pmt 4471", None, None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
        txn(conn, f"rent{tag}", "chk", when, 1800, "Sunset Apartments", "Sunset Apartments", None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    budgets.alias_set(conn, "1st Security Bank Loan Pmt%", "1st Security Bank mortgage")
    budgets.category_set(conn, "1st Security Bank mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    budgets.budget_set(conn, "rent", 2000, "RENT_AND_UTILITIES_RENT")
    # Two rows that make every COALESCE order observable, so the parity check below is load-bearing: without
    # them merchant_name and alias_canonical never differ on one row and display's precedence could silently
    # flip. An account transfer keeps them out of the spend assertions above.
    txn(conn, "both", "chk", today, 5, "SQ *PMT 8827", "Square", "mch_square", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    txn(conn, "noent", "chk", today, 5, "SQ *TIP 11", "Square", None, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    budgets.alias_set(conn, "SQ *%", "the fence guy")
    as_of = store.now_local()

    view = {r["transaction_id"]: (r["canonical"], r["display"], r["category"], r["category_primary"]) for r in conn.execute("SELECT * FROM tx_now")}
    assert view["m1"] == ("1st Security Bank mortgage", "1st Security Bank mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", "LOAN_PAYMENTS")
    assert view["rent"] == ("Sunset Apartments", "Sunset Apartments", "RENT_AND_UTILITIES_RENT", "RENT_AND_UTILITIES")
    assert view["both"][:2] == ("mch_square", "the fence guy")     # canonical takes the entity id, display takes the alias
    assert view["noent"][:2] == ("the fence guy", "the fence guy")  # no entity id: the alias beats Plaid's merchant_name
    # Every column, not just the ones this test reads: the two copies of the rule are kept in step by hand
    # (migrations/005 and _prelude.sql both say so), so drift in a column nobody selects has to fail here.
    prelude_sql = detect.PRELUDE.read_text().replace("__KIND__", "x")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tx_now)")]
    assert [d[0] for d in conn.execute(prelude_sql + "SELECT * FROM tx LIMIT 0", {"as_of": as_of}).description] == cols + ["suppressed"]
    sel = ", ".join(cols)
    assert sorted(map(tuple, conn.execute(prelude_sql + f"SELECT {sel} FROM tx", {"as_of": as_of}))) == \
           sorted(map(tuple, conn.execute(f"SELECT {sel} FROM tx_now")))

    rent = [b for b in budgets.budget_list(conn, as_of) if b["name"] == "rent"][0]
    assert rent["spent"] == 1800.0                                       # the mortgage payment is no longer rent
    sug = budgets.suggest(conn, 6, as_of)["categories"]
    assert [c for c in sug if c["category"] != "LOAN_PAYMENTS_MORTGAGE_PAYMENT"] == [{"category": "RENT_AND_UTILITIES_RENT", "primary": "RENT_AND_UTILITIES", "months": {last[:7]: 1800.0},
                                                              "months_seen": 1, "min": 0, "max": 1800.0, "median": 0.0, "variance_ratio": None,   # one month in six: zeros count in the median
                                                              "top_merchants": [{"merchant": "Sunset Apartments", "total": 1800.0}]}]   # same relation and same months as the totals, so they add up
    assert "LOAN_PAYMENTS_MORTGAGE_PAYMENT" in {c["category"] for c in sug}   # a mortgage is budgetable, so suggest shows it
    by_cat = {v["category"]: v["amount"] for v in charts.spec(conn, "spend_by_category")["data"]["values"]}
    assert by_cat == {"Rent And Utilities": 3600}                        # moved out of rent and, being a loan payment, out of the chart
    assert [v["merchant"] for v in charts.spec(conn, "merchant_history", "1st Security bank MORTGAGE")["data"]["values"]] == ["1st Security Bank mortgage"] * 2

def test_flow_classifies_own_account_transfers_mortgage_and_card_payments(conn):
    """Issue 72: tx_now's flow, one row per rule, and monthly_in_out / spend_by_category / budgets reading it."""
    from datetime import date, timedelta
    from finnamon import charts
    seed(conn)
    today = conftest.today()
    before = (date.fromisoformat(today) - timedelta(days=2)).isoformat()
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('sav','item1','Savings','depository','savings','9001','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('mtg','item1','Home Loan','loan','mortgage','5555','bill')")
    conn.execute("INSERT INTO items (item_id, institution, owner, source) VALUES ('manual:hsbc','HSBC','bill','manual')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, owner) VALUES ('m1','manual:hsbc','HSBC Checking','depository','checking','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, owner) VALUES ('m2','manual:hsbc','HSBC Bills','depository','checking','bill')")
    rows = [  # id, account, date, amount, name, primary, detailed, expected flow
        ("pay", "chk", today, -3000, "ACME PAYROLL", "INCOME", "INCOME_WAGES", "income"),
        ("tosav", "chk", today, 3000, "ONLINE TRANSFER TO SAV", "TRANSFER_OUT", "TRANSFER_OUT_OTHER_TRANSFER_OUT", "transfer"),   # mirrored: same paycheck, counted once
        ("insav", "sav", today, -3000, "ONLINE TRANSFER FROM CHK", "TRANSFER_IN", "TRANSFER_IN_OTHER_TRANSFER_IN", "transfer"),
        ("mort", "chk", today, 2500, "LOAN SERVICER WEB PMT", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER", "mortgage"),        # Plaid called it a transfer; the loan account says mortgage
        ("mortin", "mtg", before, -2500, "PAYMENT RECEIVED", "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", "skipped"),
        ("ccpay", "chk", today, 800, "SAPPHIRE AUTOPAY", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "transfer"),      # onto a linked card
        ("ccin", "cc", today, -800, "AUTOMATIC PAYMENT - THANK YOU", "INCOME", "INCOME_WAGES", "card_payment"),                 # Plaid's autopay credit labelled wages
        ("amex", "chk", today, 450, "AMEX EPAYMENT", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "expense"),          # card not linked: the only record of that spending
        ("zelle", "chk", today, 200, "Zelle payment to Jane Doe", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER", "expense"),  # a person, not an own account
        ("groc", "cc", today, 60, "WHOLE FOODS", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", "expense"),
        ("ret", "cc", today, -30, "WHOLE FOODS", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", "refund"),
        ("brk", "chk", today, 700, "TRANSFER TO BROKERAGE", "TRANSFER_OUT", "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS", "transfer"),  # other side not linked: the category alone
        ("cc2", "chk", today, 333, "CARD ONLINE PMT", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "transfer"),
        ("cc2in", "cc", today, -333, "ONLINE PAYMENT", None, None, "card_payment"),                                              # no category: the mirror alone
        ("xin", "chk", today, -1200, "ONLINE TRANSFER FROM BROKERAGE", "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER", "transfer"),
        ("sin", "chk", today, -444, "TRANSFER FROM BROKERAGE", "TRANSFER_IN", "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS", "transfer"),  # the round trip of brk
        ("zin", "chk", today, -900, "Zelle from Tenant", "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER", "income"),               # rent received from a person
        ("dref", "chk", today, -25, "HARDWARE STORE", "HOME_IMPROVEMENT", "HOME_IMPROVEMENT_HARDWARE", "refund"),                # a debit-card refund
        ("mpay", "m1", today, -1500, "HSBC PAYROLL", None, None, "income"),                                                      # imported, no category: never paired...
        ("mchk", "m2", today, 1500, "CHECK 1043", None, None, "expense"),                                                       # ...with an unrelated equal outflow
    ]
    for tid, acct, when, amt, name, prim, det, _ in rows:
        txn(conn, tid, acct, when, amt, name, None, None, prim, det)
    flows = lambda: dict(conn.execute("SELECT transaction_id, flow FROM tx_all_accounts"))   # tx_all_accounts: the loan row too
    assert flows() == {r[0]: r[7] for r in rows}
    prelude = detect.PRELUDE.read_text().replace("__KIND__", "x")
    assert dict(conn.execute(prelude + "SELECT transaction_id, flow FROM tx", {"as_of": store.now_local()})) == \
           dict(conn.execute("SELECT transaction_id, flow FROM tx_now"))                     # the detectors' copy agrees

    def in_out():
        v = charts.spec(conn, "monthly_in_out")["data"]["values"]
        return {x["series"]: x["amount"] for x in v if x["month"] == today[:7]}
    c = charts.spec(conn, "monthly_in_out")["encoding"]["color"]   # issue 88: mortgage, income, spending take theme colours 1-3; legend reads income first
    assert c["scale"]["domain"] == ["mortgage", "income", "spending"] and "range" not in c["scale"]
    assert c["legend"]["values"] == ["income", "spending", "mortgage"]
    assert in_out() == {"income": 3000 + 900 + 1500, "spending": 450 + 200 + 60 - 30 - 25 + 1500, "mortgage": 2500}
    # an imported account's transfer is the person's word: `finnamon category "CHECK 1043" transfer`
    budgets.category_set(conn, "CHECK 1043", "transfer")
    budgets.category_set(conn, "HSBC PAYROLL", "internal transfer")   # either direction
    assert flows()["mchk"] == "transfer" and flows()["mpay"] == "transfer"
    assert in_out() == {"income": 3900, "spending": 655, "mortgage": 2500}
    by_cat = {v["category"]: v["amount"] for v in charts.spec(conn, "spend_by_category")["data"]["values"]}
    assert by_cat == {"Loan Payments": 450, "Transfer Out": 200, "Food And Drink": 30}             # refunds netted (a category netted below zero drops out); no transfer, card payment or mortgage
    budgets.budget_set(conn, "groceries", 500, "FOOD_AND_DRINK_GROCERIES")
    assert budgets.budget_list(conn)[0]["spent"] == 30.0


def test_prelude_pattern_alias_exact_first_then_longest_and_time_scoped(conn):
    """Issue 18: payees whose bank string changes every payment. One alias per transaction, exact before pattern,
    longer pattern before shorter, all as of :as_of."""
    seed(conn)
    txn(conn, "m1", "chk", "2026-09-01", 2400, "Loan Payment Confirmation# XXXXX66675", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_OTHER_PAYMENT")
    txn(conn, "m2", "chk", "2026-09-10", 2400, "Loan Payment Confirmation# lywvr7yzt", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_OTHER_PAYMENT")
    txn(conn, "rent", "chk", "2026-09-03", -1800, "Jason Heberer \"Sept Rent\"", None, None, "TRANSFER_IN", "TRANSFER_IN_OTHER_TRANSFER_IN")
    txn(conn, "other", "chk", "2026-09-04", 30, "Loan Payment Confirmation Fee", None, None, "BANK_FEES", "BANK_FEES_OTHER_BANK_FEES")
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('Loan Payment Confirmation#%', 'Bank of America mortgage', '2026-09-05')")
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('Loan Payment%', 'some loan', '2026-09-05')")             # shorter pattern: loses
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('Loan Payment Confirmation# lywvr7yzt', 'the odd one', '2026-09-05')")   # exact: wins
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('Jason Heberer%', 'Jason Heberer', '2026-09-05')")
    txn(conn, "tie", "chk", "2026-09-06", 5, "Loan Payme", None, None, "BANK_FEES", "BANK_FEES_OTHER_BANK_FEES")
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('Loan Pay%', 'A', '2026-09-05'), ('Loan P_y%', 'B', '2026-09-05')")   # equal length: by name, '_' < 'a'
    conn.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed, created_at) VALUES ('Bank of America mortgage', 'LOAN_PAYMENTS', 'LOAN_PAYMENTS_MORTGAGE_PAYMENT', '2026-09-05')")
    conn.execute("INSERT INTO suppressions (kind, canonical, created_at) VALUES ('x', 'Bank of America mortgage', '2026-09-05')")
    sql = detect.PRELUDE.read_text().replace("__KIND__", "x") + "SELECT transaction_id, canonical, category, alias_canonical, display, suppressed FROM tx ORDER BY transaction_id"
    rows = {r["transaction_id"]: tuple(r)[1:] for r in conn.execute(sql, {"as_of": AS_OF})}
    assert rows["m1"] == ("Bank of America mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", "Bank of America mortgage", "Bank of America mortgage", 1)   # display and the suppression follow the pattern
    assert rows["m2"] == ("the odd one", "LOAN_PAYMENTS_OTHER_PAYMENT", "the odd one", "the odd one", 0)            # an exact alias beats every pattern
    assert rows["rent"][0] == "Jason Heberer"
    assert rows["other"][:3] == ("some loan", "BANK_FEES_OTHER_BANK_FEES", "some loan")           # only the short pattern covers it
    assert rows["tie"][0] == "B" and budgets.canonical_for(conn, "Loan Payme") == "B"           # a stable tie, the same in Python
    same = "SELECT transaction_id, alias_canonical FROM tx"
    assert sorted(map(tuple, conn.execute(detect.PRELUDE.read_text().replace("__KIND__", "x") + same, {"as_of": AS_OF}))) == \
           sorted(map(tuple, conn.execute("SELECT transaction_id, alias_canonical FROM tx_now")))   # the tx_now copy of the rule agrees
    rows = {r["transaction_id"]: tuple(r)[1:] for r in conn.execute(sql, {"as_of": "2026-09-04 00:00:00"})}
    assert rows["m1"][:3] == ("Loan Payment Confirmation# XXXXX66675", "LOAN_PAYMENTS_OTHER_PAYMENT", None)   # replay from before the aliases
    assert run(conn, "no_source") == []                                                        # pattern-aliased: it has a source
    assert {a["payload"]["name"] for a in alerts(conn)} == set()
    assert len(run(conn, "no_source", "2026-09-04 12:00:00")) == 3                             # m1, the fee and the rent, before any alias (abs(amount): money in counts; the tie row is under the minimum)


def test_prelude_excludes_mirror_accounts(conn):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk2','item1','Chase Checking','depository','checking','4821','joint','chk')")
    txn(conn, "a", "chk", "2026-09-10", 50, "x")
    txn(conn, "a2", "chk2", "2026-09-10", 50, "x")
    sql = detect.PRELUDE.read_text().replace("__KIND__", "x") + "SELECT count(*) FROM tx"
    assert conn.execute(sql, {"as_of": AS_OF}).fetchone()[0] == 1


# --- rules --------------------------------------------------------------------------------------

def test_duplicate_charge_hit_and_dedup(conn):
    seed(conn)
    txn(conn, "a", "chk", "2026-09-17", 52.18, "SHELL OIL", "Shell", "mch_shell")
    txn(conn, "b", "chk", "2026-09-18", 52.18, "SHELL OIL", "Shell", "mch_shell")
    assert len(run(conn, "duplicate_charge")) == 1
    a = alerts(conn, "duplicate_charge")[0]
    assert a["key"] == "dup:a:b" and a["payload"]["merchant"] == "Shell"
    assert run(conn, "duplicate_charge") == []  # second run: dedup on key
    assert len(alerts(conn)) == 1


@pytest.mark.parametrize("case", ["below_min", "in_ignore", "outside_window", "pending", "pre_baseline", "different_amount", "suppressed"])
def test_duplicate_charge_misses(conn, case):
    seed(conn, first_synced_at="2026-09-01 00:00:00")
    amt, d2, pend, name = 52.18, "2026-09-18", 0, "SHELL OIL"
    if case == "below_min":
        amt = 5
    if case == "outside_window":
        d2 = "2026-09-25"
    if case == "pending":
        pend = 1
    if case == "in_ignore":
        conn.execute("INSERT INTO suppressions (canonical, created_at) VALUES ('mch_shell','2026-09-01')")
    if case == "suppressed":
        conn.execute("INSERT INTO suppressions (kind, canonical, created_at) VALUES ('duplicate_charge','mch_shell','2026-09-01')")
    d1 = "2026-08-20" if case == "pre_baseline" else "2026-09-17"
    if case == "pre_baseline":
        d2 = "2026-08-21"
    txn(conn, "a", "chk", d1, amt, name, "Shell", "mch_shell")
    txn(conn, "b", "chk", d2, amt if case != "different_amount" else amt + 1, name, "Shell", "mch_shell", pending=pend)
    assert run(conn, "duplicate_charge") == []


@pytest.mark.parametrize("case", ["transfer_out", "transfer_in", "p2p", "loan_payment", "card_autopay", "two_plans_one_merchant", "one_leg_transfer"])
def test_duplicate_charge_skips_repeats_by_design(conn, case):
    """Pre-launch review must-fix #6: a rule reaches the chat untriaged, so money that repeats on purpose must not pair."""
    seed(conn, first_synced_at="2026-09-01 00:00:00")
    name2, cats = None, {
        "transfer_out": ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
        "transfer_in": ("TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"),
        "p2p": ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),   # Plaid files Venmo/Zelle/Cash App as transfers
        "loan_payment": ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CAR_PAYMENT"),
        "card_autopay": ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
    }
    raw, merchant, entity = {"p2p": ("VENMO *JANE", "Venmo", "mch_venmo"), "transfer_in": ("ONLINE TRANSFER FROM SAV", None, None)}.get(case, ("APPLE.COM/BILL", "Apple", "mch_apple"))
    primary, detailed = cats.get(case, ("ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES"))
    if case == "two_plans_one_merchant":
        raw, name2 = "APPLE.COM/BILL ICLOUD", "APPLE.COM/BILL MUSIC"
    txn(conn, "a", "chk", "2026-09-17", 250, raw, merchant, entity, primary, detailed)
    b_cat = ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER") if case == "one_leg_transfer" else (primary, detailed)
    txn(conn, "b", "chk", "2026-09-18", 250, name2 or raw, merchant, entity, *b_cat)
    assert run(conn, "duplicate_charge") == []


def test_duplicate_charge_true_duplicate_still_fires_and_normal_mutes_only_that_amount(conn):
    seed(conn, first_synced_at="2026-09-01 00:00:00")
    for tid in "ab":
        txn(conn, tid, "chk", "2026-09-17", 89.99, "BESTBUY 00123", "Best Buy", "mch_bby", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_ELECTRONICS")
    for tid in "xy":   # no category at all (a CSV import): NULL must not drop out of the NOT IN
        txn(conn, tid, "chk", "2026-09-17", 40, "CORNER HARDWARE", None, None, None, None)
    assert {x["key"] for x in alerts(conn, "duplicate_charge")} == set() and len(run(conn, "duplicate_charge")) == 2
    alert_id = next(x["id"] for x in alerts(conn, "duplicate_charge") if x["key"] == "dup:a:b")
    r = budgets.normal(conn, alert_id=alert_id)
    assert (r["canonical"], r["kind"], r["account_id"], r["max_amount"]) == ("mch_bby", "duplicate_charge", "chk", 89.99)
    conn.execute("UPDATE alerts SET resolved_at=NULL WHERE id=?", (alert_id,))   # reopened, keeping the rule: normal refuses a resolved alert
    r = budgets.normal(conn, alert_id=alert_id, max_amount=500, kind="amount_outlier")   # explicit flags win; another kind is not narrowed
    assert (r["kind"], r["account_id"], r["max_amount"]) == ("anomaly:amount_outlier", None, 500)
    conn.execute("UPDATE suppressions SET created_at='2026-09-01'")   # made at the real clock; detectors see it as of AS_OF
    for tid in "de":
        txn(conn, tid, "chk", "2026-09-18", 1200, "BESTBUY 00123", "Best Buy", "mch_bby", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_ELECTRONICS")
    for tid in "fg":
        txn(conn, tid, "chk", "2026-09-18", 60, "BESTBUY 00123", "Best Buy", "mch_bby", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_ELECTRONICS")
    assert len(run(conn, "duplicate_charge")) == 1
    assert alerts(conn, "duplicate_charge")[-1]["key"] == "dup:d:e"   # a bigger double charge there still reaches the chat; at or under the cap stays muted


def test_duplicate_charge_three_identical_charges_are_one_alert(conn, tg):
    """QA 10/4: three $52.18 Shell charges raised three pair alerts and went out as two messages."""
    seed(conn)
    for tid, d in (("a", "2026-09-17"), ("b", "2026-09-17"), ("c", "2026-09-18")):
        txn(conn, tid, "chk", d, 52.18, "SHELL OIL", "Shell", "mch_shell")
    assert len(run(conn, "duplicate_charge")) == 1
    a = alerts(conn, "duplicate_charge")[0]
    assert (a["key"], a["transaction_id"], a["payload"]["count"], a["payload"]["date_a"]) == ("dup:a:c:3", "c", 3, "2026-09-17")
    assert notify.send_pending(conn) == 1 and "Charged 3 times:</b> Shell $52.18 on Chase Checking …4821, Sep 17 to Sep 18" in tg.sent[0]["text"]
    assert run(conn, "duplicate_charge") == []
    txn(conn, "d", "chk", "2026-09-19", 52.18, "SHELL OIL", "Shell", "mch_shell")   # a fourth lands on a later sync: news of its own
    assert len(run(conn, "duplicate_charge")) == 1 and alerts(conn, "duplicate_charge")[-1]["payload"]["count"] == 4


def test_duplicate_charge_reads_transactions_once(conn):
    # The quadratic case (8s at 30k rows) was a pair self-join; runs are one windowed pass over an index range.
    seed(conn)
    plan = detect.explain(conn, RULES["duplicate_charge"], AS_OF)
    reads = [p for p in plan if p.startswith(("SEARCH t ", "SCAN t"))]
    assert len(reads) == 1 and "USING INDEX" in reads[0], plan


def test_duplicate_charge_a_chained_run_is_counted_whole(conn):
    """Codex review: charges 3 days apart with a 3-day window are one run of three, not a hidden pair and a pair."""
    seed(conn)
    for tid, d in (("a", "2026-09-12"), ("b", "2026-09-15"), ("c", "2026-09-18")):
        txn(conn, tid, "chk", d, 52.18, "SHELL OIL", "Shell", "mch_shell")
    txn(conn, "far", "chk", "2026-09-02", 52.18, "SHELL OIL", "Shell", "mch_shell")   # 10 days before: its own (single) charge
    assert len(run(conn, "duplicate_charge")) == 1
    a = alerts(conn, "duplicate_charge")[0]
    assert (a["key"], a["payload"]["count"], a["payload"]["date_a"], a["payload"]["txn_a"]) == ("dup:a:c:3", 3, "2026-09-12", "a")


def test_sync_health_stale_and_error(conn):
    seed(conn)
    assert run(conn, "sync_health") == []  # synced at AS_OF: healthy
    conn.execute("UPDATE items SET last_synced_at='2026-09-18 12:00:00'")
    ids = run(conn, "sync_health")
    assert len(ids) == 1 and alerts(conn)[0]["key"] == "health:item1:stale:2026-09-18 12:00:00"
    assert run(conn, "sync_health") == []
    assert run(conn, "sync_health", "2026-09-22 06:00:00") == []   # one alert per problem, not one per day
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED', last_synced_at=?", (AS_OF,))
    assert run(conn, "sync_health", "2026-09-20 06:00:00") and alerts(conn)[-1]["payload"]["status"] == "ITEM_LOGIN_REQUIRED"
    assert alerts(conn)[-1]["payload"]["duplicate"] == 0
    conn.execute("INSERT INTO items (item_id, institution, owner, status, last_synced_at) SELECT 'item2', institution, owner, 'good', ? FROM items", (AS_OF,))
    assert len(run(conn, "sync_health", "2026-09-21 06:00:00")) == 1   # item2, now stale; item1's login problem is still the one alert
    assert alerts(conn)[-1]["payload"]["item_id"] == "item2" and alerts(conn)[-1]["payload"]["duplicate"] == 1   # two logins at one bank


def test_sync_health_closes_on_the_next_good_sync_and_when_replaced(conn):
    seed(conn)
    conn.execute("UPDATE items SET last_synced_at='2026-09-18 12:00:00'")
    detect.run(conn, AS_OF, only=["sync_health"])
    stale = alerts(conn, "sync_health")[0]
    assert stale["resolved_at"] is None and "finnamon doctor" in notify.render(conn.execute("SELECT * FROM alerts").fetchone())
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED'")   # the stale bank turns out to want a login: one alert, the new one
    detect.run(conn, "2026-09-20 06:00:00", only=["sync_health"])
    old, login = alerts(conn, "sync_health")
    assert (old["resolution"], login["resolved_at"]) == ("superseded", None)   # not "synced again": the bank has not synced
    detect.run(conn, "2026-09-21 06:00:00", only=["sync_health"])
    assert len(alerts(conn, "sync_health")) == 1 + 1 and alerts(conn, "sync_health")[-1]["resolved_at"] is None   # still one open
    conn.execute("UPDATE items SET status='good', last_synced_at='2026-09-21 07:00:00'")   # the bank came back by itself
    assert detect.run(conn, "2026-09-21 08:00:00", only=["sync_health"]) == []
    assert [a["resolution"] for a in alerts(conn, "sync_health")] == ["superseded", "recovered"]
    conn.execute("UPDATE items SET last_synced_at='2026-09-21 07:00:00'")
    assert len(detect.run(conn, "2026-09-23 08:00:00", only=["sync_health"])) == 1   # breaks again later: a new problem, a new alert


def test_new_recurring_after_baseline_only_and_once(conn):
    seed(conn, first_synced_at="2026-09-01 00:00:00")
    base = "INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) VALUES (?,?,'outflow',?,?,?,?,?,?,?,?)"
    conn.execute(base, ("old", "cc", "Netflix", "MONTHLY", 15.49, 15.49, "2026-01-05", "2026-09-05", "MATURE", "2026-09-15 00:00:00"))   # predates baseline: no
    conn.execute(base, ("new", "cc", "Peloton", "MONTHLY", 44, 44, "2026-09-10", "2026-09-10", "EARLY_DETECTION", "2026-09-15 00:00:00"))  # early detection: no
    conn.execute(base, ("new2", "cc", "Peloton", "MONTHLY", 44, 44, "2026-09-10", "2026-09-10", "MATURE", "2026-09-15 00:00:00"))
    conn.execute(base, ("stale", "cc", "Hulu", "MONTHLY", 10, 10, "2026-09-02", "2026-09-02", "MATURE", "2026-09-01 00:00:00"))  # seen >7d ago: no
    ids = run(conn, "new_recurring")
    assert [alerts(conn)[i]["key"] for i in range(len(ids))] == ["recurring:new2"]
    assert run(conn, "new_recurring") == []


def test_new_recurring_names_a_stream_whose_name_is_only_in_the_description(conn):
    """Plaid sends '' as well as NULL: a plain COALESCE takes the '' and the alert goes out with an empty merchant."""
    seed(conn, first_synced_at="2026-09-01 00:00:00")
    base = ("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, "
            "first_date, last_date, status, first_seen_at) VALUES (?,?,'outflow',?,?,?,?,?,?,?,?,?)")
    conn.execute(base, ("blank", "cc", "", "SPOTIFY USA", "MONTHLY", 12, 12, "2026-09-10", "2026-09-10", "MATURE", "2026-09-15 00:00:00"))   # hit: named from description
    conn.execute(base, ("null", "cc", None, "NETFLIX.COM", "MONTHLY", 15, 15, "2026-09-10", "2026-09-10", "MATURE", "2026-09-15 00:00:00"))  # hit: the NULL case that always worked
    conn.execute(base, ("noname", "cc", "  ", "\t", "MONTHLY", 9, 9, "2026-09-10", "2026-09-10", "MATURE", "2026-09-15 00:00:00"))          # miss on the name only: nothing to call it
    run(conn, "new_recurring")
    got = {a["key"]: json.loads(a["payload_json"])["merchant"] for a in alerts(conn)}
    assert got["recurring:blank"] == "SPOTIFY USA", "an empty merchant_name must fall through to the description"
    assert got["recurring:null"] == "NETFLIX.COM"
    # SQLite trim() strips spaces only, so a tab reaches the payload; tidy() in notify.py is the one place that becomes no name.
    assert notify.render(next(a for a in alerts(conn) if a["key"] == "recurring:noname")).startswith("\U0001f501 <b>New recurring charge:</b> an unnamed payee")


def test_low_balance_once_per_dip(conn):
    seed(conn)
    store.set_setting(conn, "low_balance_threshold", 1000, "chk")
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-19 06:00:00', 900, 640)")
    assert len(run(conn, "low_balance")) == 1
    assert run(conn, "low_balance", "2026-09-20 12:00:00") == []
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-09-27 06:00:00', 900, 610)")
    assert run(conn, "low_balance", "2026-09-28 12:00:00") == []       # next week, still low: the same dip
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-10-01 06:00:00', 2500, 2400)")   # payday: recovered
    assert run(conn, "low_balance", "2026-10-01 12:00:00") == []
    conn.execute("INSERT INTO balances VALUES ('chk', '2026-10-03 06:00:00', 700, 650)")     # and down again: a new dip
    assert len(run(conn, "low_balance", "2026-10-03 12:00:00")) == 1
    assert [a["key"] for a in alerts(conn)] == ["lowbal:chk:2026-09-19 06:00:00", "lowbal:chk:2026-10-03 06:00:00"]
    conn.execute("INSERT INTO balances VALUES ('cc', '2026-10-03 06:00:00', -500, 100)")   # credit account: never
    assert run(conn, "low_balance", "2026-10-05 12:00:00") == []


def test_budget_pace_and_over_are_exclusive_and_net(conn):
    seed(conn)
    budgets.budget_set(conn, "groceries", 600, "FOOD_AND_DRINK_GROCERIES")
    txn(conn, "a", "chk", "2026-09-05", 300, "WF", "Whole Foods", "mch_wf")
    txn(conn, "b", "chk", "2026-09-10", 100, "WF", "Whole Foods", "mch_wf")
    txn(conn, "r", "chk", "2026-09-11", -50, "WF refund", "Whole Foods", "mch_wf")            # refund nets out
    txn(conn, "x", "chk", "2026-09-12", 900, "XFER", None, None, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS")  # excluded
    ids = run(conn, "budget_pace", "2026-09-19 12:00:00")   # spent 350 by day 19 → pace 552 < 600: nothing
    assert ids == []
    txn(conn, "c", "chk", "2026-09-15", 100, "WF", "Whole Foods", "mch_wf")   # 450 → pace 710
    ids = run(conn, "budget_pace", "2026-09-19 12:00:00")
    a = alerts(conn, "budget_pace")[-1]
    assert a["key"].endswith(":pace") and a["payload"]["spent"] == 450 and a["payload"]["projection"] == pytest.approx(710.53, abs=0.1)
    txn(conn, "d", "chk", "2026-09-18", 200, "WF", "Whole Foods", "mch_wf")   # 650 → over
    run(conn, "budget_pace", "2026-09-19 12:00:00")
    keys = [a["key"] for a in alerts(conn, "budget_pace")]
    assert keys == ["budget:1:2026-09:pace", "budget:1:2026-09:over"]


def test_budget_pace_silent_before_min_day_and_primary_budget(conn):
    seed(conn)
    budgets.budget_set(conn, "food", 100, "FOOD_AND_DRINK")
    txn(conn, "a", "chk", "2026-09-02", 90, "Chipotle", "Chipotle", "mch_c", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT")
    assert run(conn, "budget_pace", "2026-09-03 12:00:00") == []          # day 3 < budget_min_day
    ids = run(conn, "budget_pace", "2026-09-06 12:00:00")                # day 6: 90*30/6 = 450 pace; primary matches detailed child
    assert len(ids) == 1 and alerts(conn)[0]["payload"]["budget"] == "food"


def test_budget_uses_category_override(conn):
    seed(conn)
    budgets.budget_set(conn, "groceries", 100, "FOOD_AND_DRINK_GROCERIES")
    txn(conn, "a", "chk", "2026-09-06", 400, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    assert run(conn, "budget_pace", "2026-09-10 12:00:00") == []
    conn.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed, created_at) VALUES ('mch_costco','FOOD_AND_DRINK','FOOD_AND_DRINK_GROCERIES','2026-09-01')")
    assert len(run(conn, "budget_pace", "2026-09-10 12:00:00")) == 1


# --- candidates ------------------------------------------------------------------------------------

def test_first_merchant_and_no_source(conn):
    seed(conn)
    txn(conn, "old", "chk", "2026-08-01", 40, "WF", "Whole Foods", "mch_wf")
    txn(conn, "again", "chk", "2026-09-15", 40, "WF", "Whole Foods", "mch_wf")          # seen before: no
    txn(conn, "sq", "cc", "2026-09-16", 412, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    txn(conn, "small", "cc", "2026-09-16", 6.5, "BLUE BOTTLE", "Blue Bottle", "mch_bb", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")  # below min: no
    assert {alerts(conn)[i]["key"] for i in range(len(run(conn, "first_merchant")))} == {"anom:first:sq"}
    assert len(run(conn, "no_source")) == 1 and alerts(conn)[-1]["key"] == "anom:nosrc:sq"
    conn.execute("INSERT INTO merchant_alias (name, canonical, created_at) VALUES ('SQ *PMT 8827','fence contractor','2026-09-19 11:00:00')")
    conn.execute("DELETE FROM alerts")
    assert run(conn, "no_source") == []   # aliased before as_of → has a source now
    assert len(run(conn, "no_source", "2026-09-19 10:00:00")) == 1   # replay from before the alias: still a candidate


def test_amount_outlier_needs_three_priors(conn):
    seed(conn)
    for i, d in enumerate(["2026-07-01", "2026-07-15", "2026-08-01"]):
        txn(conn, f"p{i}", "chk", d, 70, "WF", "Whole Foods", "mch_wf")
    txn(conn, "big", "chk", "2026-09-17", 214, "WF", "Whole Foods", "mch_wf")
    ids = run(conn, "amount_outlier")
    assert len(ids) == 1 and alerts(conn)[0]["payload"]["median"] == 70
    conn.execute("DELETE FROM alerts"); conn.execute("DELETE FROM transactions WHERE transaction_id='p2'")
    assert run(conn, "amount_outlier") == []


def test_unmatched_transfer_matched_vs_not(conn):
    seed(conn)
    txn(conn, "out", "chk", "2026-09-15", 500, "Transfer to savings", None, None, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS")
    txn(conn, "in", "cc", "2026-09-16", -500, "Transfer", None, None, "TRANSFER_IN", "TRANSFER_IN_SAVINGS")   # counterpart within window
    txn(conn, "zelle", "chk", "2026-09-16", 890, "ZELLE TO M RIVERA", None, None, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    txn(conn, "pay", "chk", "2026-09-16", -3000, "PAYROLL", "Acme", "mch_acme", "INCOME", "INCOME_WAGES")   # income: excluded
    keys = {alerts(conn)[i]["key"] for i in range(len(run(conn, "unmatched_transfer")))}
    assert keys == {"anom:xfer:zelle"}


def test_recurring_changed_is_skipped_payments_only(conn):
    seed(conn, first_synced_at="2026-01-01 00:00:00")
    base = "INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, last_date, predicted_next_date, status, first_seen_at) VALUES (?,?,'outflow',?,?,?,?,?,?,?,?,?)"
    conn.execute(base, ("comcast", "chk", "Comcast", "MONTHLY", 89.99, 109.99, "2026-01-05", "2026-09-15", "2026-10-15", "MATURE", "2026-06-01 00:00:00"))   # recurring_price's
    conn.execute(base, ("peloton", "cc", "Peloton", "MONTHLY", 44, 44, "2026-01-01", "2026-08-01", "2026-09-01", "MATURE", "2026-06-01 00:00:00"))
    conn.execute(base, ("netflix", "cc", "Netflix", "MONTHLY", 15.49, 15.49, "2026-01-05", "2026-09-05", "2026-10-05", "MATURE", "2026-06-01 00:00:00"))
    n = len(run(conn, "recurring_changed"))
    got = {a["payload"]["merchant"]: a["payload"]["change"] for a in alerts(conn)}
    assert n == 1 and got == {"Peloton": "skipped"}


def netflix(conn, amounts, stream="netflix", **kw):
    """A monthly stream with one charge per amount, the last on 2026-09-15; recurring's average includes the new price."""
    seed(conn, first_synced_at="2026-01-01 00:00:00")
    for i, amt in enumerate(amounts):
        txn(conn, f"{stream}{i}", "cc", f"2026-{9 - len(amounts) + 1 + i:02d}-15", amt, "NETFLIX.COM", "Netflix", "mch_netflix", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, merchant_entity_id, frequency, avg_amount, last_amount, first_date, last_date, "
                 "predicted_next_date, status, is_active, first_seen_at) VALUES (?,'cc','outflow','Netflix','mch_netflix','MONTHLY',?,?,'2026-01-15','2026-09-15','2026-10-15',?,?,'2026-02-01 00:00:00')",
                 (stream, sum(amounts) / len(amounts), amounts[-1], kw.get("status", "MATURE"), kw.get("active", 1)))


def test_recurring_price_hit_names_both_prices_and_dedups(conn):
    netflix(conn, [15.49, 15.49, 15.49, 17.99])   # the average (16.12) is within 20% of the new price: the old check never fired
    ids = run(conn, "recurring_price")
    assert len(ids) == 1
    a = alerts(conn)[0]
    assert (a["key"], a["tier"], a["transaction_id"]) == ("recprice:netflix3", "rule", "netflix3")
    assert "Netflix went from $15.49 to $17.99" in notify.render(conn.execute("SELECT * FROM alerts").fetchone())
    assert run(conn, "recurring_price") == []   # dedup on the charge
    txn(conn, "netflix4", "cc", "2026-10-15", 17.99, "NETFLIX.COM", "Netflix", "mch_netflix", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
    conn.execute("UPDATE recurring SET last_amount=17.99, last_date='2026-10-15'")
    assert run(conn, "recurring_price", "2026-10-16 08:00:00") == []   # the new price, again: nothing changed


def test_recurring_price_accepted_then_raised_again_alerts(conn):
    netflix(conn, [15.49, 15.49, 15.49, 17.99])
    run(conn, "recurring_price")
    budgets.normal(conn, alert_id=alerts(conn)[0]["id"])   # "that's expected": $17.99 is the price now
    conn.execute("UPDATE suppressions SET created_at='2026-09-01'")
    for i, d in ((4, "2026-10-15"), (5, "2026-11-15")):
        txn(conn, f"netflix{i}", "cc", d, 17.99 if i == 4 else 22.99, "NETFLIX.COM", "Netflix", "mch_netflix", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
    conn.execute("UPDATE recurring SET last_amount=22.99, last_date='2026-11-15'")
    assert len(run(conn, "recurring_price", "2026-11-16 08:00:00")) == 1   # above the accepted price: news again


@pytest.mark.parametrize("stream_fields", ["entity_id", "merchant_name_only", "description_only"])
def test_recurring_price_matches_a_stream_without_an_entity_id(conn, stream_fields):
    # Value: protects=streams Plaid sends without merchant_entity_id still alert; fails_when=the stream match compares one COALESCE; why_new=fixture streams always had the id; seam=none
    netflix(conn, [15.49, 15.49, 15.49, 17.99])
    if stream_fields != "entity_id":
        conn.execute("UPDATE recurring SET merchant_entity_id=NULL")
    if stream_fields == "description_only":
        conn.execute("UPDATE recurring SET merchant_name=NULL, description='NETFLIX.COM'")
    assert len(run(conn, "recurring_price")) == 1


@pytest.mark.parametrize("case", ["same_price", "variable_bill", "too_few_charges", "early_detection", "inactive", "old_charge", "normal_on_stream", "kindless_normal",
                                  "backfill", "second_plan_at_the_merchant"])
def test_recurring_price_misses(conn, case):
    amounts = {"same_price": [15.49] * 3, "variable_bill": [82.10, 95.40, 101.75], "too_few_charges": [15.49, 17.99]}.get(case, [15.49, 15.49, 17.99])
    netflix(conn, amounts, status="EARLY_DETECTION" if case == "early_detection" else "MATURE", active=0 if case == "inactive" else 1)
    if case == "backfill":   # the re-price happened before the first sync
        conn.execute("UPDATE items SET first_synced_at='2026-09-16 00:00:00'")
    if case == "second_plan_at_the_merchant":   # two live Netflix plans on one card: whose charge is whose is a guess
        conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, merchant_entity_id, frequency, avg_amount, last_amount, first_date, last_date, "
                     "status, first_seen_at) VALUES ('netflix-b','cc','outflow','Netflix','mch_netflix','MONTHLY',6.99,6.99,'2026-01-03','2026-09-03','MATURE','2026-02-01 00:00:00')")
    as_of = "2026-10-20 08:00:00" if case == "old_charge" else AS_OF
    if case == "kindless_normal":
        conn.execute("INSERT INTO suppressions (canonical, created_at) VALUES ('mch_netflix', '2026-09-01')")
    if case == "normal_on_stream":
        run(conn, "recurring_price")
        r = budgets.normal(conn, alert_id=alerts(conn)[0]["id"])
        assert (r["kind"], r["stream_id"], r["canonical"], r["account_id"], r["max_amount"]) == ("recurring_price", "netflix", "mch_netflix", "cc", 17.99)
        conn.execute("UPDATE suppressions SET created_at='2026-09-01'")
        conn.execute("DELETE FROM alerts")
    assert run(conn, "recurring_price", as_of) == []


def test_recurring_changed_ignores_backfilled_and_inactive_streams(conn):
    """Pre-launch must-fix #5: a subscription cancelled long before the first sync (the 24-month backfill) or one
    Plaid marks inactive is not "skipped", month after month; an active stream that misses a date after it is."""
    seed(conn, first_synced_at="2026-06-01 00:00:00")
    base = ("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, "
            "last_date, predicted_next_date, status, is_active, first_seen_at) VALUES (?,?,'outflow',?,?,?,?,?,?,?,?,?,?)")
    conn.execute(base, ("hulu", "cc", "Hulu", "MONTHLY", 12, 12, "2024-10-01", "2025-03-01", "2025-04-01", "MATURE", None, "2026-06-01 00:00:00"))   # died in backfill
    conn.execute(base, ("spotify", "cc", "Spotify", "MONTHLY", 11, 11, "2025-01-01", "2026-07-01", "2026-08-01", "MATURE", 0, "2026-06-01 00:00:00"))   # cancelled after sync
    conn.execute(base, ("peloton", "cc", "Peloton", "MONTHLY", 44, 44, "2026-01-01", "2026-08-01", "2026-09-01", "MATURE", 1, "2026-06-01 00:00:00"))  # really skipped
    run(conn, "recurring_changed")
    assert {a["payload"]["merchant"]: a["payload"]["change"] for a in alerts(conn)} == {"Peloton": "skipped"}

def test_new_category(conn):
    seed(conn)
    txn(conn, "a", "chk", "2026-08-01", 30, "WF", "Whole Foods", "mch_wf")
    txn(conn, "b", "chk", "2026-09-16", 80, "PETCO", "Petco", "mch_petco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_PET_SUPPLIES")
    assert len(run(conn, "new_category")) == 1 and alerts(conn)[0]["payload"]["category"] == "GENERAL_MERCHANDISE_PET_SUPPLIES"


def test_suppression_wildcards_and_max_amount(conn):
    seed(conn)
    txn(conn, "a", "chk", "2026-09-16", 300, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    conn.execute("INSERT INTO suppressions (canonical, max_amount, created_at) VALUES ('mch_costco', 400, '2026-09-01')")
    assert run(conn, "first_merchant") == []
    conn.execute("DELETE FROM suppressions")
    conn.execute("INSERT INTO suppressions (canonical, max_amount, created_at) VALUES ('mch_costco', 100, '2026-09-01')")   # too small a cap
    assert len(run(conn, "first_merchant")) == 1


# --- runner -------------------------------------------------------------------------------------

def test_run_all_and_detector_error_is_loud(conn, tmp_path, monkeypatch):
    seed(conn)
    assert detect.run(conn, AS_OF) == []
    import shutil
    tree = tmp_path / "detectors"
    shutil.copytree(detect.DETECTORS, tree)               # never write into the real package tree
    monkeypatch.setattr(detect, "DETECTORS", tree)
    monkeypatch.setattr(detect, "PRELUDE", tree / "_prelude.sql")
    (tree / "rules" / "zz_bad.sql").write_text("SELECT nonsense FROM nowhere;")
    ids = detect.run(conn, AS_OF)
    assert len(ids) == 1 and alerts(conn)[0]["kind"] == "detector_error" and "zz_bad" in alerts(conn)[0]["key"]
    assert detect.run(conn, AS_OF) == []  # once per day


def test_recurring_changed_cancelled_acknowledges_one_stream_and_kindless_normal_never_covers_it(conn):
    """#64: "yes, I cancelled it" mutes that stream only; another stream at the merchant, and a later new one, still
    alert. A merchant-level "normal" with no kind means its charges are normal, not that its subscriptions stopping is."""
    seed(conn, first_synced_at="2026-01-01 00:00:00")
    base = ("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, merchant_entity_id, frequency, avg_amount, last_amount, first_date, "
            "last_date, predicted_next_date, status, first_seen_at) VALUES (?,?,'outflow','Acme','mch_acme','MONTHLY',?,?,'2026-01-01',?,?,'MATURE','2026-06-01 00:00:00')")
    conn.execute(base, ("acme-a", "cc", 10, 10, "2026-07-01", "2026-08-01"))   # stopped
    conn.execute(base, ("acme-b", "cc", 20, 20, "2026-09-10", "2026-10-10"))   # fine for now
    txn(conn, "t1", "cc", "2026-09-10", 20, "ACME", "Acme", "mch_acme")
    budgets.normal(conn, "Acme")   # kind NULL: does not cover recurring_changed
    conn.execute("UPDATE suppressions SET created_at='2026-09-01'")
    assert run(conn, "recurring_changed") and [a["key"] for a in alerts(conn)] == ["anom:rec:acme-a:2026-09"]
    r = budgets.normal(conn, alert_id=alerts(conn)[0]["id"])
    assert (r["kind"], r["stream_id"], r["canonical"], r["account_id"]) == ("anomaly:recurring_changed", "acme-a", "mch_acme", "cc")
    conn.execute("UPDATE suppressions SET created_at='2026-09-01'")
    later = "2026-10-20 09:00:00"
    # the other Acme stream (predicted 2026-10-10) has now missed a payment too
    conn.execute(base, ("acme-c", "cc", 5, 5, "2026-06-01", "2026-08-01"))       # and a newer one has stopped too
    run(conn, "recurring_changed", as_of=later)
    assert {a["key"] for a in alerts(conn)} - {"anom:rec:acme-a:2026-09"} == {"anom:rec:acme-b:2026-10", "anom:rec:acme-c:2026-10"}
    # the acknowledged stream does not come back next month; removing the rule brings it back
    assert budgets.normal_remove(conn, r["id"])["removed"]["stream_id"] == "acme-a"
    run(conn, "recurring_changed", as_of=later)
    assert "anom:rec:acme-a:2026-10" in {a["key"] for a in alerts(conn)}
    with pytest.raises(ValueError, match="no suppression"):
        budgets.normal_remove(conn, r["id"])


def test_migration_007_adds_stream_id_on_an_existing_db(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.db"); db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
    old = sorted(p for p in store.MIGRATIONS.glob("*.sql") if p.name < "007")
    for f in old:
        for stmt in store._split(f.read_text()):
            db.execute(stmt)
        db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (f.name,))
    db.execute("INSERT INTO suppressions (kind, canonical) VALUES ('anomaly:recurring_changed', 'mch_acme')")
    db.commit()
    assert "007_suppression_stream.sql" in store.migrate(db)
    assert dict(db.execute("SELECT kind, canonical, stream_id FROM suppressions").fetchone()) == {"kind": "anomaly:recurring_changed", "canonical": "mch_acme", "stream_id": None}


def test_transfer_and_mortgage_synonyms_are_whole_phrase_only():
    """Issue 72: an override to TRANSFER_OUT_ACCOUNT_TRANSFER takes a payee out of spending, so only the bare word gets it."""
    from finnamon import taxonomy
    assert taxonomy.resolve("transfer")[0] == "TRANSFER_OUT_ACCOUNT_TRANSFER" and taxonomy.resolve("Mortgage")[0] == "LOAN_PAYMENTS_MORTGAGE_PAYMENT"
    for phrase in ("transfer fee", "wire transfer", "venmo transfer", "mortgage interest"):
        assert taxonomy.resolve(phrase)[0] not in ("TRANSFER_OUT_ACCOUNT_TRANSFER", "LOAN_PAYMENTS_MORTGAGE_PAYMENT"), phrase


# --- QA 10/4: routine money movements are not anomalies ------------------------------------------

CANDIDATES = ["no_source", "first_merchant", "new_category", "amount_outlier"]


def test_card_payment_both_sides_and_own_transfers_raise_no_candidate(conn):
    seed(conn)
    txn(conn, "pay", "chk", "2026-09-17", 812.40, "CHASE CREDIT CRD EPAY", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "payin", "cc", "2026-09-17", -812.40, "PAYMENT THANK YOU", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "sav", "chk", "2026-09-18", 1500, "ONLINE TRANSFER TO SAV", None, None, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS")
    assert [i for n in CANDIDATES for i in run(conn, n)] == []
    txn(conn, "odd", "chk", "2026-09-18", 240, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    assert len(run(conn, "no_source")) == 1   # an unknown payee still is


def test_zelle_rent_is_one_candidate_group(conn):
    from finnamon import triage
    seed(conn)
    txn(conn, "rent", "chk", "2026-09-17", 2400, "Zelle payment to Pat Landlord", None, None, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    store.set_setting(conn, "large_amount", 1000)
    ids = [i for n in [*CANDIDATES, "unmatched_transfer"] for i in run(conn, n)]
    assert len(ids) > 1 and [g["group"] for g in triage.groups(conn)] == ["rent"]   # several detectors, one transaction, one thing to judge


def test_two_charges_at_a_new_merchant_on_one_day_are_one_first_merchant(conn):
    seed(conn)
    for tid in ("v1", "v2"):
        txn(conn, tid, "chk", "2026-09-17", 60, "BLUE BOTTLE", "Blue Bottle", "mch_bb", "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")
    assert [a["transaction_id"] for a in alerts(conn)] == [] and len(run(conn, "first_merchant")) == 1
    assert len(run(conn, "new_category")) == 1


# Value: protects=one bank's good sync closes only its own sync_health alert; fails_when=resolve_recovered drops the item_id match
# (closes every open sync_health on any good sync); why_new=the existing close test has one bank; seam=none
def test_sync_health_recovery_closes_only_the_bank_that_synced(conn):
    seed(conn)
    conn.execute("INSERT INTO items (item_id, institution, owner, status, first_synced_at, last_synced_at) "
                 "SELECT 'item2', 'Ally', owner, 'good', first_synced_at, last_synced_at FROM items WHERE item_id='item1'")
    conn.execute("UPDATE items SET last_synced_at='2026-09-18 12:00:00'")
    assert len(detect.run(conn, AS_OF, only=["sync_health"])) == 2
    conn.execute("UPDATE items SET last_synced_at='2026-09-19 07:00:00' WHERE item_id='item1'")   # item1 back; item2 still stale
    detect.run(conn, "2026-09-19 08:00:00", only=["sync_health"])
    got = {a["payload"]["item_id"]: a["resolution"] for a in alerts(conn, "sync_health")}
    assert got == {"item1": "recovered", "item2": None}


def test_mortgage_and_a_card_payment_with_history_raise_no_outlier(conn):
    # Value: protects=candidates skip flow mortgage/card_payment even past amount_outlier's median; fails_when=the flow filter is dropped from amount_outlier; why_new=the card test has no 3-charge history; seam=none
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('mtg','item1','Home Loan','loan','mortgage','5555','bill')")
    for i, d in enumerate(("2026-05-03", "2026-06-03", "2026-07-03", "2026-08-03")):
        txn(conn, f"pay{i}", "chk", d, 100, "CHASE CREDIT CRD EPAY", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "pay", "chk", "2026-09-17", 2400, "CHASE CREDIT CRD EPAY", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "payin", "cc", "2026-09-17", -2400, "PAYMENT THANK YOU", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "mort", "chk", "2026-09-18", 3605.99, "LOAN SERVICER WEB PMT", None, None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    assert [i for n in CANDIDATES for i in run(conn, n)] == []
    txn(conn, "pay", "chk", "2026-09-17", 2400, "CHASE CREDIT CRD EPAY", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    conn.execute("DELETE FROM transactions WHERE transaction_id='payin'")
    assert [a["kind"] for a in alerts(conn) if a["transaction_id"] == "pay"] == [] and run(conn, "amount_outlier")   # unpaired, it is an outlier again


def test_sync_health_an_error_that_returns_every_sync_is_one_alert(conn):
    # Value: protects=one alert while the same bank error persists; fails_when=the open-same-status guard is dropped; why_new=apply_page moves last_synced_at before a later step fails; seam=none
    seed(conn)
    conn.execute("UPDATE items SET status='ADDITIONAL_CONSENT_REQUIRED'")
    assert len(detect.run(conn, AS_OF, only=["sync_health"])) == 1
    conn.execute("UPDATE items SET last_synced_at='2026-09-19 18:00:00'")   # a page applied, then recurring failed again
    assert detect.run(conn, "2026-09-19 18:05:00", only=["sync_health"]) == []
    assert [a["resolved_at"] for a in alerts(conn, "sync_health")] == [None]



def test_duplicate_charge_a_run_aging_out_of_the_lookback_does_not_alert_again(conn):
    # Value: protects=one alert per run as days pass; fails_when=runs are formed only inside the lookback bound; why_new=every other dup test runs one as_of; seam=none
    seed(conn)
    for tid, d in (("a", "2026-09-09"), ("b", "2026-09-12"), ("c", "2026-09-15")):
        txn(conn, tid, "chk", d, 52.18, "SHELL OIL", "Shell", "mch_shell")
    assert len(run(conn, "duplicate_charge", "2026-09-19 12:00:00")) == 1
    for day in range(20, 29):
        assert run(conn, "duplicate_charge", f"2026-09-{day} 12:00:00") == []
