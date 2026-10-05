import json

import pytest

from finnamon import budgets, heartbeat, notify, run as runmod, store, sync, telegram
from finnamon.telegram import TelegramError
from tests.conftest import AS_OF, seed, txn


def alert(conn, kind="duplicate_charge", key=None, tier="rule", payload=None, verdict=None, confidence=None, reason=None):
    payload = payload or {"merchant": "Shell <Oil> & Co", "amount": 52.18, "account": "Chase Checking", "mask": "4821", "date_a": "2026-09-17", "date_b": "2026-09-18"}
    cur = conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, verdict, confidence, reason) VALUES (?,?,?,?,?,?,?,?)",
                       (tier, kind, key or f"k{conn.execute('SELECT count(*) FROM alerts').fetchone()[0]}", json.dumps(payload), AS_OF, verdict, confidence, reason))
    return cur.lastrowid


def test_one_message_per_alert_and_escaping(conn, tg):
    seed(conn)
    alert(conn); alert(conn, key="k2")
    assert notify.send_pending(conn) == 2
    assert len(tg.sent) == 2
    assert "Shell &lt;Oil&gt; &amp; Co" in tg.sent[0]["text"] and "$52.18" in tg.sent[0]["text"]
    ids = [r[0] for r in conn.execute("SELECT telegram_message_id FROM alerts ORDER BY id")]
    assert ids == [101, 102]
    assert notify.send_pending(conn) == 0  # nothing left


def test_failed_send_leaves_alert_unsent(conn, tg):
    seed(conn)
    alert(conn)
    tg.fail_with = TelegramError(429, "Too Many Requests", retry_after=1)
    slept = []
    assert notify.send_pending(conn, sleep=slept.append) == 0
    assert conn.execute("SELECT sent_at FROM alerts").fetchone()[0] is None and slept == [1]
    tg.fail_with = None
    assert notify.send_pending(conn) == 1


def test_anomaly_delivery_split(conn, tg):
    seed(conn)
    p = {"merchant": "SQ *PMT 8827", "amount": 412, "date": "2026-09-16", "account": "Sapphire", "mask": "7710"}
    on = lambda d: {**p, "date": d}   # four transactions: one per verdict
    alert(conn, "anomaly:no_source", "a1", "anomaly", on("2026-09-13"), "promote", "high", "You've never paid this merchant.")
    alert(conn, "anomaly:first_merchant", "a2", "anomaly", on("2026-09-14"), "promote", "low", "New coffee spot.")
    alert(conn, "anomaly:new_category", "a3", "anomaly", on("2026-09-15"), "suppress", "low", "normal")
    alert(conn, "anomaly:new_category", "a4", "anomaly", p)  # untriaged
    assert notify.send_pending(conn) == 1 and "never paid" in tg.sent[0]["text"]
    assert notify.send_roundup(conn, "2026-09-19 18:00:00") is None   # Saturday: no roundup
    mid = notify.send_roundup(conn, "2026-09-20 18:00:00")             # Sunday: the low one, numbered, with a roundup_items row
    assert mid and "1. " in tg.sent[-1]["text"] and "New coffee spot" in tg.sent[-1]["text"]
    assert [tuple(r) for r in conn.execute("SELECT n, alert_id FROM roundup_items")] == [(1, 2)]
    assert conn.execute("SELECT telegram_chat_id FROM roundup_items").fetchone()[0] == "1234567890"
    assert notify.send_roundup(conn, "2026-09-20 23:00:00") is None  # same week: once
    sent = {r[0]: r[1] for r in conn.execute("SELECT key, sent_at IS NOT NULL FROM alerts")}
    assert sent == {"a1": 1, "a2": 1, "a3": 0, "a4": 0}


def test_render_every_kind(conn):
    seed(conn)
    cases = {
        "sync_health": {"item_id": "item1", "institution": "Chase", "status": "ITEM_LOGIN_REQUIRED"},
        "new_recurring": {"merchant": "Peloton", "amount": 44, "frequency": "MONTHLY", "first_date": "2026-09-20", "account": "Sapphire", "mask": "7710"},
        "low_balance": {"account": "Chase Checking", "mask": "4821", "available": 640, "threshold": "1000"},
        "budget_pace": {"budget": "dining", "spent": 248, "projection": 372, "limit": 350, "day": 24, "days": 30, "state": "pace"},
        "detector_error": {"detector": "zz.sql", "error": "no such column"},
        "run_error": {"error": "PlaidError: boom"},
        "triage_error": {},
        "consent_expiring": {"item_id": "item1", "institution": "Chase", "expires": "2026-10-01T00:00:00Z"},
    }
    for kind, p in cases.items():
        aid = alert(conn, kind, f"r-{kind}", payload=p)
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
        text = notify.render(row)
        assert text and "<" in text  # has HTML formatting
    relogin = notify.render(conn.execute("SELECT * FROM alerts WHERE kind='sync_health'").fetchone())
    assert "re-login" in relogin and "fix Chase" in relogin and "finnamon" not in relogin   # the phone fix, not a terminal command
    expiring = notify.render(conn.execute("SELECT * FROM alerts WHERE kind='consent_expiring'").fetchone())
    assert "expires Oct 1" in expiring and "fix Chase" in expiring
    dup = notify.render(conn.execute("SELECT * FROM alerts WHERE id=?", (alert(conn, "sync_health", "r-dup", payload={
        "item_id": "item2", "institution": "Chase", "owner": "jane", "status": "ITEM_LOGIN_REQUIRED", "duplicate": True}),)).fetchone())
    assert "Chase (jane's login, item2)" in dup and "fix item2" in dup and "fix Chase" not in dup   # two logins at Chase: say which


def test_item_linked_summary(conn):
    seed(conn)
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 3240, 3200)", (AS_OF,))
    for i in range(30):
        txn(conn, f"t{i}", "chk", f"2026-{6 + i % 4:02d}-{1 + i % 27:02d}", 40 + i, "WF", "Whole Foods", "mch_wf")
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s','chk','outflow','Netflix','MONTHLY',15.49,15.49,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "Linked Chase" in s and "Netflix" in s and "Whole Foods" in s and "watching from today" in s
    assert "$" not in s and "3,240" not in s and "15.49" not in s   # the chat is shared; no balances or amounts in the summary
    assert s.split("\n\n")[0].count("\n• ") == conn.execute("SELECT count(*) FROM accounts WHERE item_id='item1'").fetchone()[0]   # the account block; merchants are listed below


def test_summary_names_merchants_the_way_the_household_does(conn):
    """Issue 23: the linked-bank summary was the last place still reading raw bank strings, so a payee the
    household had already aliased and recategorised came back under Plaid's name, and its loan payments
    counted as merchant spend. It now reads the same resolved relation the budgets and charts do."""
    seed(conn)
    txn(conn, "t1", "chk", "2026-09-10", 3605.99, "1st Security Bank Loan Pmt 4471", None, None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    txn(conn, "t2", "chk", "2026-09-11", 80, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    budgets.alias_set(conn, "1st Security Bank Loan Pmt%", "1st Security Bank mortgage")
    budgets.category_set(conn, "1st Security Bank mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    top = notify.item_linked_summary(conn, "item1", AS_OF).split("Top merchants, last 90 days:</b>")[1].split("\n\n")[0]
    assert top == "\n• fence contractor"   # the alias, and the mortgage is a loan payment now, not the biggest merchant


def test_summary_of_a_bank_whose_accounts_are_all_mirrors_is_not_empty(conn):
    """`finnamon link` marks a duplicate account joint (cli.py mark_mirror) *before* it sends this summary, so
    reading tx_now here -- which drops mirrors so the household's money is never counted twice -- would answer
    'linked 0 transactions' for the one case the summary exists to describe: a bank someone already linked."""
    seed(conn)
    conn.execute("INSERT INTO items (item_id, institution, owner, first_synced_at, last_synced_at, created_at) VALUES ('item2','Chase','bill',?,?,?)", (AS_OF, AS_OF, AS_OF))
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk_j','item2','Chase Checking','depository','checking','4821','joint','chk')")
    txn(conn, "t1", "chk_j", "2026-09-11", 80, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    budgets.alias_set(conn, "SQ *PMT 8827", "fence contractor")
    s = notify.item_linked_summary(conn, "item2", AS_OF)
    assert "• fence contractor" in s and "1 transaction since Sep 11, 2026" in s
    assert conn.execute("SELECT count(*) FROM tx_now WHERE item_id='item2'").fetchone()[0] == 0   # tx_now still hides it from every money total

def test_send_one_without_a_chat_id_sends_nothing(conn):
    """The daemon raises channel_deaf before it knows there is anywhere to send it; the row stays pending."""
    seed(conn)
    store.set_state(conn, "chat_id", None)
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','channel_deaf','k','{}',?)", (AS_OF,))
    row = conn.execute("SELECT * FROM alerts WHERE key='k'").fetchone()
    assert notify.send_one(conn, row) is None and row["sent_at"] is None


# --- run ------------------------------------------------------------------------------------------

def test_crash_becomes_one_alert_per_day(conn, tg, monkeypatch):
    seed(conn)
    monkeypatch.setattr(sync, "sync_all", lambda c: (_ for _ in ()).throw(RuntimeError("plaid down")))
    r1 = runmod.cycle(conn, do_triage=False)
    r2 = runmod.cycle(conn, do_triage=False)
    assert r1["error"].startswith("RuntimeError") and r2["error"]
    rows = conn.execute("SELECT kind, key FROM alerts").fetchall()
    assert len(rows) == 1 and rows[0][0] == "run_error" and rows[0][1].startswith("health:run:RuntimeError:")
    assert len(tg.sent) == 1 and "crashed" in tg.sent[0]["text"]
    assert store.get_state(conn, "last_run") is None  # a crashed cycle is not a completed run


def test_cycle_happy_path_sets_last_run(conn, tg, monkeypatch):
    seed(conn)
    monkeypatch.setattr(sync, "sync_all", lambda c: [])
    r = runmod.cycle(conn, do_triage=False)
    assert r["error"] is None and store.get_state(conn, "last_run") == r["as_of"]


def test_run_lock(conn, tg, monkeypatch):
    seed(conn)
    monkeypatch.setattr(sync, "sync_all", lambda c: [])
    lock = runmod.lock()
    with pytest.raises(runmod.Locked):
        runmod.cycle(conn)
    lock.close()


# --- heartbeat -------------------------------------------------------------------------------------

def test_heartbeat_once_per_day_when_stale(conn, tg):
    seed(conn)
    assert heartbeat.check(conn, "2026-09-19 12:00:00")  # never run → stale
    assert heartbeat.check(conn, "2026-09-19 13:00:00") is None  # already said so today
    store.set_state(conn, "last_run", "2026-09-20 06:00:00")
    assert heartbeat.check(conn, "2026-09-20 12:00:00") is None  # fresh
    assert heartbeat.check(conn, "2026-09-20 15:00:00")  # 9h old, new day
    assert len(tg.sent) == 2


def test_heartbeat_after_overnight_sleep_waits_for_the_catch_up(conn, tg):
    """Laptop slept 22:00 → 07:00: the heartbeat fires on wake before the daemon stamps alive or catches up."""
    seed(conn)
    store.set_state(conn, "last_run", "2026-09-19 22:00:00")
    store.set_state(conn, "daemon_alive_at", "2026-09-19 23:59:30")   # the last stamp before the lid closed
    naps = []
    def wake(s):   # fake clock: the daemon's poll loop stamps alive 20s into the wait
        naps.append(s)
        if len(naps) == 2:
            store.set_state(conn, "daemon_alive_at", "2026-09-20 07:00:20")
    assert heartbeat.check(conn, "2026-09-20 07:00:00", sleep=wake) is None and naps == [10, 10]
    store.set_state(conn, "daemon_alive_at", "2026-09-20 07:19:50")
    assert heartbeat.check(conn, "2026-09-20 07:20:00") is None     # a quick re-run is not the next heartbeat
    store.set_state(conn, "last_run", "2026-09-20 07:01:00"); store.set_state(conn, "heartbeat_deferred_at", None)   # catch-up done (run.cycle)
    assert heartbeat.check(conn, "2026-09-20 08:00:00") is None
    assert tg.sent == [] and store.get_state(conn, "last_heartbeat_text") is None   # the day's slot is untouched
    # a real outage later that day still gets through: syncs start failing, the daemon stays alive
    store.set_state(conn, "daemon_alive_at", "2026-09-20 17:00:00")
    assert heartbeat.check(conn, "2026-09-20 17:00:00") is None                 # stale since 15:01: deferred once
    store.set_state(conn, "daemon_alive_at", "2026-09-20 18:00:00")
    msg = heartbeat.check(conn, "2026-09-20 18:00:00")
    assert "Last sync was 2026-09-20 07:01:00" in msg and "syncs aren't finishing" in msg
    assert len(tg.sent) == 1


def test_heartbeat_dead_daemon_and_stale_sync_alerts_now(conn, tg):
    seed(conn)
    store.set_state(conn, "last_run", "2026-09-19 22:00:00")
    store.set_state(conn, "daemon_alive_at", "2026-09-19 23:00:00")
    naps = []
    assert heartbeat.check(conn, "2026-09-20 09:00:00", sleep=naps.append)
    assert sum(naps) == heartbeat.WAKE_WAIT_S and len(tg.sent) == 1


def test_heartbeat_reports_a_crash_loop_once(conn, tg, monkeypatch):
    from finnamon import claude_runner, daemon
    seed(conn)
    store.set_state(conn, "last_run", "2026-09-20 06:00:00")
    monkeypatch.setattr(claude_runner, "harness_problems", lambda: ["no .claude/settings.json"])
    for _ in range(2):
        daemon.Daemon(conn_factory=lambda: conn).start(exit=lambda code: None)
    assert heartbeat.check(conn, "2026-09-20 08:00:00") is None          # two starts: an update and a restart, say
    daemon.Daemon(conn_factory=lambda: conn).start(exit=lambda code: None)
    msg = heartbeat.check(conn, "2026-09-20 08:00:00")
    assert "keeps crashing" in msg and "no .claude/settings.json" in msg and "daemon.err" in msg and "finnamon daemon" not in msg
    daemon.Daemon(conn_factory=lambda: conn).start(exit=lambda code: None)
    assert heartbeat.check(conn, "2026-09-20 09:00:00") is None and len(tg.sent) == 1   # once per loop
    assert store.get_state(conn, "last_heartbeat_text") is None          # and not the stale-sync slot


def test_heartbeat_wake_mid_catch_up_regrants_but_a_failed_sync_does_not(conn, tg):
    seed(conn)
    store.set_state(conn, "last_run", "2026-09-20 22:00:00")
    store.set_state(conn, "heartbeat_deferred_at", "2026-09-21 07:00:00")   # granted on a morning wake...
    store.set_state(conn, "daemon_alive_at", "2026-09-21 07:02:00")         # ...then the lid closed mid catch-up
    wake = lambda s: store.set_state(conn, "daemon_alive_at", "2026-09-21 19:00:10")
    assert heartbeat.check(conn, "2026-09-21 19:00:00", sleep=wake) is None and tg.sent == []   # slept, not an outage
    assert store.get_state(conn, "heartbeat_deferred_at") == "2026-09-21 19:00:00"
    # a sync attempt finished (and failed: last_run did not move) inside the grace, then the laptop slept
    store.set_state(conn, "last_attempt", "2026-09-21 19:01:00")
    store.set_state(conn, "daemon_alive_at", "2026-09-21 19:05:00")
    wake = lambda s: store.set_state(conn, "daemon_alive_at", "2026-09-22 07:00:10")
    msg = heartbeat.check(conn, "2026-09-22 07:00:00", sleep=wake)
    assert "syncs aren't finishing" in msg and len(tg.sent) == 1


def test_a_successful_run_ends_the_heartbeat_grace(conn, tg):
    seed(conn)
    store.set_state(conn, "heartbeat_deferred_at", "2026-09-20 07:00:00")
    assert runmod.cycle(conn, do_sync=False, do_triage=False, do_notify=False)["error"] is None
    assert store.get_state(conn, "heartbeat_deferred_at") is None


def test_heartbeat_crash_loop_without_a_recorded_error(conn, tg):
    seed(conn)
    store.set_state(conn, "daemon_starts", json.dumps(["2026-09-20 07:00:00", "2026-09-20 07:01:00", "2026-09-20 07:02:00"]))
    msg = heartbeat.check(conn, "2026-09-20 08:00:00")
    assert "no error recorded" in msg and "3 starts since 2026-09-20 07:00:00" in msg
    assert store.get_state(conn, "crash_loop_reported") == "2026-09-20 07:02:00"


def test_item_linked_summary_masks_kinds_and_holdings(conn):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, owner) VALUES ('inv','item1','Brokerage','investment',NULL,'bill')")  # no mask, no subtype
    conn.execute("INSERT INTO holdings (account_id, security_id, as_of, name, ticker, quantity, price, value) VALUES ('inv','s1',?,'V','VOO',2,500,1000)", (AS_OF,))
    conn.execute("INSERT INTO holdings (account_id, security_id, as_of, name, ticker, quantity, price, value) VALUES ('inv','s2',?,'B','BND',1,80,80)", (AS_OF,))
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "<b>Linked Chase</b> (3 accounts):" in s
    assert "• Chase Checking …4821 (checking)" in s and "• Sapphire …7710 (credit card)" in s
    assert "• Brokerage (investment)" in s and "Brokerage …" not in s  # no mask, type falls back
    assert "no transactions to show (an investment or loan account often has none), 2 holdings." in s
    assert "Recurring" not in s and "Top merchants" not in s and "$" not in s and "1,000" not in s
    assert s.endswith("Balances and amounts: just ask me here.")


def test_item_linked_summary_no_type_no_subtype(conn):
    seed(conn)
    conn.execute("INSERT INTO accounts (account_id, item_id, name, owner) VALUES ('x','item1','Mystery','bill')")
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "• Mystery" in s and "()" not in s


@pytest.mark.parametrize("raw,want", [
    ("Online Banking transfer to CHK 9665 Confirmation# XXXXX40731", "Online Banking transfer to CHK 9665"),
    ("Loan Payment Confirmation# XXXXX66675", "Loan Payment"),
    ("1st Security Ban DES:monthly pa ID:Jane Doe INDN:Jane Doe 602840 CO ID:XXXXX59933 WEB", "1st Security Ban (monthly pa)"),
    ("AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:John Doe CO ID:XXXXX33497 WEB", "AMERICAN EXPRESS (ACH PMT)"),
    ("Homeowners Insurance Disbursement", "Homeowners Insurance Disbursement"),
    ("COSTCO WHSE #1234", "COSTCO WHSE"),
    ("Costco", "Costco"),
    (None, ""),
    ("ACME DES:", "ACME"),                                                          # DES: with nothing after it: just the company
    ("ACME DES:BILL PAYMENT INDN:Jane Doe", "ACME (BILL PAYMENT)"),                 # INDN: is the first tail field
    ("X DES:PMT CO ID:1", "X (PMT)"),                                               # CO ID: is the first tail field
    ("X DES:PMT PPD", "X (PMT)"),                                                   # bare SEC class ends the description
    ("Zelle payment to Jane Doe Conf# abc123", "Zelle payment to Jane Doe"),        # Conf# spelling; the payee stays, it is the merchant
    ("Store XXXX1234", "Store"),                                                    # masked account number on its own
    ("PAYPAL INST XFER 1234 WEB", "PAYPAL INST XFER 1234"),                         # trailing SEC class without a DES: block
    ("  Spaced   out  name ", "Spaced out name"),                                   # whitespace collapse
    ("- Foo -", "Foo"),                                                             # stray separators
    ("XXX Cinema", "XXX Cinema"),                                                   # X-run needs digits after it
    ("#12345", "#12345"),                                                           # cleaning would leave nothing: original wins
    ("ARC", "ARC"),                                                                 # a merchant literally named like a SEC class
    (123, "123"),                                                                   # non-strings pass through as text
])
def test_tidy_bank_descriptors(raw, want):
    assert notify.tidy(raw) == want
    assert notify.tidy(notify.tidy(raw)) == notify.tidy(raw)   # idempotent: a tidied name survives a second pass unchanged


def test_summary_lists_merchants_one_per_line_without_transfers(conn):
    seed(conn)
    txn(conn, "a1", "chk", "2026-09-10", 900, "AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:John Doe CO ID:XXXXX33497 WEB", primary="LOAN_PAYMENTS", detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    txn(conn, "a2", "chk", "2026-09-11", 800, "Online Banking transfer to CHK 9665 Confirmation# XXXXX40731", primary="TRANSFER_OUT", detailed="TRANSFER_OUT_ACCOUNT_TRANSFER")
    txn(conn, "a3", "chk", "2026-09-12", 120, "WHOLEFDS MKT 10259", "Whole Foods")
    txn(conn, "a4", "chk", "2026-09-13", 60, "1st Security Ban DES:monthly pa ID:Jane Doe INDN:Jane Doe 602840 CO ID:XXXXX59933 WEB", primary="GENERAL_SERVICES", detailed="GENERAL_SERVICES_OTHER")
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "• Whole Foods" in s and "• 1st Security Ban (monthly pa)" in s
    assert "AMERICAN EXPRESS" not in s and "Online Banking" not in s          # payments to the household's own card and accounts are not merchants
    assert "INDN" not in s and "Confirmation" not in s and "XXXXX" not in s   # no bank junk, no personal names from ACH fields
    block = s.split("Top merchants, last 90 days:</b>")[1]
    assert block.startswith("\n• ")                                            # a list, one per line


def test_alerts_render_tidy_merchants(conn):
    seed(conn)
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','duplicate_charge','d1',?,?)",
                 (json.dumps({"merchant": "AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:John Doe CO ID:XXXXX33497 WEB", "amount": 50, "account": "Chk", "mask": "1", "date_a": "2026-09-01", "date_b": "2026-09-02"}), AS_OF))
    text = notify.render(conn.execute("SELECT * FROM alerts WHERE key='d1'").fetchone())
    assert "AMERICAN EXPRESS (ACH PMT)" in text and "INDN" not in text


def test_alerts_render_tidy_every_merchant_site(conn):
    seed(conn)
    raw = "1st Security Ban DES:monthly pa ID:Jane Doe INDN:Jane Doe 602840 CO ID:XXXXX59933 WEB"
    alert(conn, "new_recurring", "n1", payload={"merchant": raw, "amount": 60, "frequency": "MONTHLY", "first_date": "2026-09-20", "account": "Chk", "mask": "1"})
    alert(conn, "anomaly:no_source", "a1", "anomaly", {"name": raw, "amount": 60, "date": "2026-09-16"}, "promote", "high", "Never seen.")
    alert(conn, "anomaly:no_source", "a2", "anomaly", {"amount": 60}, "promote", "high", "No name at all.")
    texts = {k: notify.render(conn.execute("SELECT * FROM alerts WHERE key=?", (k,)).fetchone()) for k in ("n1", "a1", "a2")}
    assert "1st Security Ban (monthly pa)" in texts["n1"] and "Jane" not in texts["n1"]
    assert "$60.00 to 1st Security Ban (monthly pa)</b>, Sep 16" in texts["a1"] and "Jane" not in texts["a1"]   # name falls back when merchant is absent
    assert texts["a2"].endswith("<b>$60.00 to a transaction</b>. No name at all.")                            # no merchant, no name, no account, no date


def test_summary_recurring_tidied_one_per_line_and_null_category_kept(conn):
    seed(conn)
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow',NULL,'AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:John Doe CO ID:XXXXX33497 WEB','MONTHLY',900,900,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s2','chk','outflow','Netflix','NETFLIX.COM','MONTHLY',15.49,15.49,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    txn(conn, "u1", "chk", "2026-09-12", 80, "COSTCO WHSE #1234", primary=None, detailed=None)   # Plaid gave no category: still a merchant
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    rec = s.split("Recurring I can see:</b>")[1].split("\n\n")[0]
    assert rec == "\n• AMERICAN EXPRESS (ACH PMT)\n• Netflix"       # one per line, highest amount first, description tidied
    assert "John" not in s and "• COSTCO WHSE\n" in s


def test_summary_skips_a_heading_whose_rows_are_all_nameless(conn):
    seed(conn)   # AmEx sends recurring streams with neither a merchant nor a description: the heading was printed with an empty bullet
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow',NULL,'  ','MONTHLY',900,900,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "Recurring I can see" not in s and "Top merchants" not in s and "\n• \n" not in s and not s.endswith("• ")


def test_summary_drops_a_nameless_row_and_keeps_the_heading_its_neighbours_earned(conn):
    seed(conn)   # a CSV row with a blank description, or an AmEx stream with no merchant: one empty bullet, not a missing section
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow',NULL,NULL,'MONTHLY',900,900,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s2','chk','outflow','Netflix','NETFLIX.COM','MONTHLY',15.49,15.49,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    txn(conn, "u1", "chk", "2026-09-13", 900, "  ")                  # the nameless row spends the most: unfiltered it heads the list
    txn(conn, "u2", "chk", "2026-09-12", 80, "COSTCO WHSE #1234")
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert s.split("Recurring I can see:</b>")[1].split("\n\n")[0] == "\n• Netflix"
    assert s.split("Top merchants, last 90 days:</b>")[1].split("\n\n")[0] == "\n• COSTCO WHSE"
    assert "• \n" not in s


def test_summary_skips_a_row_the_bank_left_as_a_tab_or_nbsp(conn):
    seed(conn)   # SQLite trim() strips spaces only, and tidy() hands a tab straight back: both guards have to hold
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow',NULL,'\t','MONTHLY',900,900,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    txn(conn, "u1", "chk", "2026-09-13", 900, "\u00a0")
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "Recurring I can see" not in s and "Top merchants" not in s
    assert "\t" not in s and "\u00a0" not in s


def test_summary_keeps_a_row_whose_name_lives_in_the_description(conn):
    seed(conn)   # Plaid sends '' as well as NULL: a plain COALESCE takes the '' and the real name in description is never reached
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s1','chk','outflow','','SPOTIFY USA','MONTHLY',12,12,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    txn(conn, "u1", "chk", "2026-09-13", 60, "TRADER JOES", merchant="")
    s = notify.item_linked_summary(conn, "item1", AS_OF)
    assert "• SPOTIFY USA" in s, "a stream named only in description must be named, not dropped"
    assert "• TRADER JOES" in s, "and its spend must still count toward the top merchants"


def test_alerts_name_a_charge_the_bank_left_nameless(conn):
    seed(conn)   # duplicate_charge and new_recurring had no fallback: the name slot came out empty
    alert(conn, "duplicate_charge", "d1", payload={"merchant": "\t", "amount": 49.99, "account": "Chk", "mask": "1", "date_a": "2026-09-01", "date_b": "2026-09-02"})
    alert(conn, "new_recurring", "n1", payload={"merchant": None, "amount": 12.99, "frequency": "MONTHLY", "first_date": "2026-09-20", "account": "Chk", "mask": "1"})
    texts = {k: notify.render(conn.execute("SELECT * FROM alerts WHERE key=?", (k,)).fetchone()) for k in ("d1", "n1")}
    assert "an unnamed charge $49.99" in texts["d1"] and "</b>  " not in texts["d1"]
    assert "an unnamed payee $12.99" in texts["n1"] and "</b>  " not in texts["n1"]


@pytest.mark.parametrize("raw,want", [
    ("Conference Center Cafe", "Conference Center Cafe"),          # 'Conf' inside a word is not a confirmation number
    ("Confidential Shredding", "Confidential Shredding"),
    ("PAYROLL DES:ACME CORP ID:1", "PAYROLL (ACME CORP)"),            # 'CO' inside a word is not the CO ID field
    ("PG&E DES:GAS CO PMT ID:1", "PG&E (GAS CO PMT)"),
    ("AT&T DES:AT&T TELEPHONE ID:1", "AT&T (AT&T TELEPHONE)"),
    ("AMEX EPAYMENT ACH PMT INDN:JANE DOE CO ID:1234", "AMEX EPAYMENT ACH PMT"),   # person field without DES: still dropped
    ("AMAZON WEB", "AMAZON WEB"),                                      # a trailing class code only counts after a number
    ("PAYPAL INST XFER 1234 WEB", "PAYPAL INST XFER 1234"),
    ("T.J. MAXXX123", "T.J. MAXXX123"),
    ("Zelle payment to Jane Doe Conf# abc123", "Zelle payment to Jane Doe"),       # the payee is the merchant; keep it
])
def test_tidy_does_not_over_strip_real_names(raw, want):
    assert notify.tidy(raw) == want and notify.tidy(notify.tidy(raw)) == notify.tidy(raw)


@pytest.mark.parametrize("raw,want", [
    ("CHECK #1023", "CHECK #1023"),                                    # a check number is information, a store number is not
    ("COSTCO WHSE #0123", "COSTCO WHSE"),
    ("SSA TREAS 310 XXSOC SEC PPD ID: XXXXX1234", "SSA TREAS 310 XXSOC SEC"),
    ("Conf Center Parking", "Conf Center Parking"),                    # a confirmation token must contain a digit
    ("ZELLE CONF# ABC123XYZ", "ZELLE"),
    ("WELLS FARGO AUTO PAY 240301 REF #A12345678", "WELLS FARGO AUTO PAY 240301"),
    ("ORIG CO NAME:VENMO ORIG ID:5264681992 DESC DATE:240301 CO ENTRY DESCR:PAYMENT SEC:WEB TRACE#:021000021234567 EED:240301 IND ID:1234567890 IND NAME:JOHN DOE TRN: 0612345678TC", "VENMO (PAYMENT)"),
    ("   ", ""),
])
def test_tidy_more_bank_formats(raw, want):
    assert notify.tidy(raw) == want and notify.tidy(notify.tidy(raw)) == notify.tidy(raw)


def test_recurring_change_headline_names_the_tracked_charge(conn):
    p = {"merchant": "Apple", "avg_amount": 12.14, "last_amount": 12.14, "last_date": "2026-06-28", "account": "CREDIT CARD", "mask": "3690", "change": "skipped"}
    alert(conn, "anomaly:recurring_changed", "rc1", "anomaly", p, "promote", "low", "Apple's $12.14 monthly charge hasn't appeared since June 28.")
    text = notify.render(conn.execute("SELECT * FROM alerts WHERE key='rc1'").fetchone())
    assert text.startswith("🔍 <b>$12.14 to Apple</b>") and "$12.14 monthly" in text


def test_income_headline_is_unsigned_and_the_payload_keeps_its_sign(conn):
    p = {"merchant": "Acme Corp (PAYROLL)", "amount": -1234.56, "account": "Checking", "mask": "0000", "date": "2026-01-15"}
    alert(conn, "anomaly:no_source", "inc1", "anomaly", p, "promote", "low", "No source.")
    row = conn.execute("SELECT * FROM alerts WHERE key='inc1'").fetchone()
    assert notify.render(row).startswith("🔍 <b>$1,234.56 from Acme Corp (PAYROLL)</b> on Checking …0000, Jan 15.")
    assert json.loads(row["payload_json"])["amount"] == -1234.56


def test_the_deaf_notice_says_how_many_are_waiting_and_how_to_be_heard_again(conn):
    """It is sent into the chat nobody is reading, so it has to be worth finding later: the count and the one command."""
    seed(conn)
    alert(conn, "channel_deaf", "cd1", payload={"pending": 1, "since": "2026-09-23 18:40:00"})
    alert(conn, "channel_deaf", "cd2", payload={"pending": 4, "since": "2026-09-23 18:40:00"})
    one, many = (notify.render(conn.execute("SELECT * FROM alerts WHERE key=?", (k,)).fetchone()) for k in ("cd1", "cd2"))
    assert "1 message is waiting" in one
    assert "4 messages are waiting" in many                                 # the plural is built, not guessed
    assert "2026-09-23 18:40:00" in one                                     # when it went quiet, so a person can match it to what they sent
    assert "finnamon update --no-pull" in many                              # the fix, in the message itself


def test_empty_week_sends_the_all_quiet_line(conn, tg):
    seed(conn)
    txn(conn, "t1", "chk", "2026-09-18", 40, "WF", "Whole Foods")
    mid = notify.send_roundup(conn, "2026-09-20 18:00:00")
    assert mid and len(tg.sent) == 1
    text = tg.sent[0]["text"]
    assert "All quiet" in text and "2 accounts watched" in text and "all synced by Sep 19, 12:00 PM" in text
    assert "1 transaction in the last 7 days" in text and "Nothing flagged" in text and len(text) < 300
    assert notify.send_roundup(conn, "2026-09-20 23:00:00") is None and len(tg.sent) == 1   # once a week
    tg.fail_with = TelegramError(500, "boom")                                               # a failed send leaves the week open
    assert notify.send_roundup(conn, "2026-09-27 09:00:00") is None and store.get_state(conn, "last_roundup_week") == "2026-37"


def test_missed_sunday_catches_up_once_on_monday(conn, tg):
    seed(conn)
    assert notify.send_roundup(conn, "2026-09-21 07:00:00") and "All quiet" in tg.sent[-1]["text"]   # Monday: Sunday was slept through
    assert store.get_state(conn, "last_roundup_week") == "2026-37"                                   # stamped as Sunday's week
    assert notify.send_roundup(conn, "2026-09-21 13:00:00") is None and len(tg.sent) == 1            # once
    assert notify.send_roundup(conn, "2026-09-22 07:00:00") is None                                   # Tuesday is too late: wait for the next Sunday
    notify.send_roundup(conn, "2026-09-27 07:00:00")                                                  # Sunday sent: Monday does not repeat it
    assert notify.send_roundup(conn, "2026-09-28 07:00:00") is None and len(tg.sent) == 2


def test_first_link_sends_the_welcome_once(conn, tg):
    from finnamon import link
    seed(conn)
    link.announce(conn, "item1")
    first = tg.sent[-1]["text"]
    assert "Linked Chase" in first and "What I watch" in first and "set up my budgets" in first
    assert "finnamon threshold 'Chase Checking' 500" in first and "alert me if Chase Checking drops below $500" in first
    link.announce(conn, "item1")                                          # a re-announce of the same bank is not a first link again
    conn.execute("INSERT INTO items (item_id, institution, owner) VALUES ('item2','Amex','bill')")
    link.announce(conn, "item2")
    assert len(tg.sent) == 3 and not any("What I watch" in m["text"] for m in tg.sent[1:])


def test_welcome_skips_the_threshold_nudge_when_one_is_set_and_old_households(conn, tg):
    from finnamon import link
    seed(conn)
    store.set_setting(conn, "low_balance_threshold", 800, "chk")
    assert "What I watch" in notify.first_link_welcome(conn, "item1") and "threshold" not in notify.first_link_welcome(conn, "item1")
    conn.execute("INSERT INTO items (item_id, institution, owner) VALUES ('item2','Amex','bill')")   # linked before this shipped: no flag, but not first
    link.announce(conn, "item2")
    assert "What I watch" not in tg.sent[-1]["text"]


def test_quiet_line_does_not_hide_a_stale_bank_or_waiting_alerts(conn, tg):
    seed(conn)
    conn.execute("INSERT INTO items (item_id, institution, owner, last_synced_at) VALUES ('item2','Amex','bill','2026-08-01 09:00:00')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('amx','item2','Gold','credit','credit card','1001','bill')")
    alert(conn, "anomaly:first_merchant", "u1", "anomaly", {"merchant": "x", "amount": 5})   # untriaged: triage is down
    text = notify.quiet_line(conn, "2026-09-20 18:00:00")
    assert "all synced by Aug 1, 9:00 AM" in text and "1 alert is still waiting" in text and "All quiet" not in text


def test_forced_midweek_roundup_does_not_cancel_sunday(conn, tg):
    seed(conn)
    assert notify.send_roundup(conn, "2026-09-16 12:00:00", force=True)   # Wednesday, by hand
    assert store.get_state(conn, "last_roundup_week") is None
    assert notify.send_roundup(conn, "2026-09-20 09:00:00")               # Sunday still goes out


def test_roundup_week_key_across_the_year_boundary(conn, tg):
    seed(conn)
    notify.send_roundup(conn, "2026-12-27 09:00:00")
    assert notify.send_roundup(conn, "2027-01-03 09:00:00") and notify.send_roundup(conn, "2027-01-04 09:00:00") is None and len(tg.sent) == 2
