"""Sync semantics against a scripted Plaid. Fixture shapes are copied from the sandbox probe."""
import pytest

from finnamon import plaid_api, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, plaid_txn, seed


class ScriptedPlaid:
    def __init__(self, pages):
        self.pages = list(pages)  # each: dict page, or PlaidError to raise
        self.calls = []

    def transactions_sync(self, token, cursor, count=500):
        self.calls.append(cursor)
        p = self.pages.pop(0)
        if isinstance(p, Exception):
            raise p
        return p


def page(added=(), modified=(), removed=(), cursor="c1", more=False):
    return {"added": list(added), "modified": list(modified), "removed": [{"transaction_id": r} for r in removed], "next_cursor": cursor, "has_more": more}


@pytest.fixture
def item(conn):
    ids = seed(conn, first_synced_at=None)
    conn.execute("UPDATE items SET first_synced_at=NULL, last_synced_at=NULL, cursor=NULL WHERE item_id='item1'")
    return ids


def run(monkeypatch, conn, pages, retries=0):
    sp = ScriptedPlaid(pages)
    monkeypatch.setattr(plaid_api, "transactions_sync", sp.transactions_sync)
    n = sync.sync_transactions(conn, "item1", "tok", AS_OF, not_ready_retries=retries)
    return sp, n


def test_first_page_with_rows_sets_baseline_and_keeps_sign(conn, item, monkeypatch):
    sp, n = run(monkeypatch, conn, [page([plaid_txn("t1", "chk", "2026-09-11", 5.4, "Uber 063015 SF**POOL**", "Uber", "mch_u", "TRANSPORTATION", "TRANSPORTATION_TAXIS_AND_RIDE_SHARES"),
                                          plaid_txn("t2", "chk", "2026-09-09", -500, "United Airlines", "United Airlines", "mch_ua", "TRAVEL", "TRAVEL_FLIGHTS")])])
    assert n == 2
    row = conn.execute("SELECT amount, merchant_name, pfc_detailed FROM transactions WHERE transaction_id='t2'").fetchone()
    assert row[0] == -500 and row[1] == "United Airlines" and row[2] == "TRAVEL_FLIGHTS"  # refund stays negative; never negated
    it = conn.execute("SELECT cursor, first_synced_at, last_synced_at, status FROM items WHERE item_id='item1'").fetchone()
    assert it[0] == "c1" and it[1] == AS_OF and it[2] == AS_OF and it[3] == "good"


def test_empty_first_page_does_not_set_baseline(conn, item, monkeypatch):
    run(monkeypatch, conn, [page(cursor="c0")])
    assert conn.execute("SELECT first_synced_at FROM items").fetchone()[0] is None


def test_fresh_item_empty_no_cursor_is_retried(conn, item, monkeypatch):
    fresh = {"added": [], "modified": [], "removed": [], "next_cursor": "", "has_more": False}
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    sp, n = run(monkeypatch, conn, [fresh, fresh, page([plaid_txn("t1", "chk", "2026-09-11", 5.4, "x")])], retries=5)
    assert n == 1 and len(sp.calls) == 3


def test_pagination_commits_cursor_per_page(conn, item, monkeypatch):
    sp, n = run(monkeypatch, conn, [page([plaid_txn("a", "chk", "2026-09-01", 1, "a")], cursor="c1", more=True),
                                    page([plaid_txn("b", "chk", "2026-09-02", 2, "b")], cursor="c2")])
    assert sp.calls == [None, "c1"] and n == 2
    assert conn.execute("SELECT cursor FROM items").fetchone()[0] == "c2"


def test_modified_and_removed(conn, item, monkeypatch):
    run(monkeypatch, conn, [page([plaid_txn("a", "chk", "2026-09-01", 10, "a"), plaid_txn("b", "chk", "2026-09-01", 11, "b")])])
    run(monkeypatch, conn, [page(modified=[plaid_txn("a", "chk", "2026-09-01", 12, "a-mod")], removed=["b"], cursor="c2")])
    rows = {r[0]: r[1] for r in conn.execute("SELECT transaction_id, amount FROM transactions")}
    assert rows == {"a": 12}


def test_pending_replaced_by_posted(conn, item, monkeypatch):
    run(monkeypatch, conn, [page([plaid_txn("p1", "chk", "2026-09-01", 10, "coffee", pending=True)])])
    run(monkeypatch, conn, [page([plaid_txn("x1", "chk", "2026-09-02", 10, "coffee", pending_id="p1")], cursor="c2")])
    rows = [dict(r) for r in conn.execute("SELECT transaction_id, pending FROM transactions")]
    assert rows == [{"transaction_id": "x1", "pending": 0}]


def test_mutation_during_pagination_restarts_from_run_start_cursor(conn, item, monkeypatch):
    conn.execute("UPDATE items SET cursor='c0'")
    err = PlaidError({"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "error_message": "x"})
    sp, n = run(monkeypatch, conn, [page([plaid_txn("a", "chk", "2026-09-01", 1, "a")], cursor="c1", more=True), err,
                                    page([plaid_txn("a", "chk", "2026-09-01", 1, "a"), plaid_txn("b", "chk", "2026-09-02", 2, "b")], cursor="c3")])
    assert sp.calls == ["c0", "c1", "c0"]  # restarted from the cursor at run start
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 2  # upserts converge


def test_item_login_required_sets_status(conn, item, monkeypatch):
    err = PlaidError({"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "the login details of this item have changed"})
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: (_ for _ in ()).throw(err))
    res = sync.sync_item(conn, "item1", "tok")
    assert res["error"] == "ITEM_LOGIN_REQUIRED"
    it = conn.execute("SELECT status, last_error FROM items").fetchone()
    assert it[0] == "ITEM_LOGIN_REQUIRED" and "login" in it[1]


def test_accounts_and_balances_append_only(conn, item, monkeypatch):
    resp = {"accounts": [{"account_id": "chk", "name": "Chase Checking", "type": "depository", "subtype": "checking", "mask": "4821",
                          "balances": {"current": 100.0, "available": 90.0}}]}
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: resp)
    sync.sync_accounts(conn, "item1", "tok", "2026-09-19 06:00:00")
    resp["accounts"][0]["balances"]["current"] = 120.0
    sync.sync_accounts(conn, "item1", "tok", "2026-09-19 12:00:00")
    assert conn.execute("SELECT count(*) FROM balances WHERE account_id='chk'").fetchone()[0] == 2


def test_recurring_first_seen_never_updated(conn, item, monkeypatch):
    stream = {"stream_id": "s1", "account_id": "chk", "merchant_name": "Netflix", "frequency": "MONTHLY", "average_amount": {"amount": 15.49},
              "last_amount": {"amount": 15.49}, "first_date": "2026-01-05", "last_date": "2026-09-05", "predicted_next_date": "2026-10-05", "status": "MATURE"}
    monkeypatch.setattr(plaid_api, "recurring_get", lambda tok: {"inflow_streams": [], "outflow_streams": [stream]})
    sync.sync_recurring(conn, "item1", "tok", "2026-09-01 00:00:00")
    stream["last_amount"]["amount"] = 17.99
    sync.sync_recurring(conn, "item1", "tok", "2026-09-19 00:00:00")
    r = conn.execute("SELECT first_seen_at, last_amount, is_active FROM recurring").fetchone()
    assert r[0] == "2026-09-01 00:00:00" and r[1] == 17.99 and r[2] is None   # no is_active from Plaid: unknown, read as active
    stream["is_active"] = False
    sync.sync_recurring(conn, "item1", "tok", "2026-09-20 00:00:00")
    assert conn.execute("SELECT is_active FROM recurring").fetchone()[0] == 0


def test_sync_all_uses_tokens_and_never_raises(conn, item, monkeypatch):
    from finnamon import config
    monkeypatch.setattr(config, "item_tokens", lambda: {"item1": "tok"})
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "API_ERROR", "error_message": "boom"})))
    res = sync.sync_all(conn)
    assert res[0]["error"] == "API_ERROR"


def test_sync_item_pulls_holdings_and_a_holdings_error_is_not_the_items_health(home, conn, monkeypatch):
    from finnamon import investments, plaid_api, secrets, sync
    from tests.conftest import seed
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    monkeypatch.setattr(sync, "sync_accounts", lambda *a, **k: [])
    monkeypatch.setattr(sync, "sync_transactions", lambda *a, **k: 0)
    monkeypatch.setattr(sync, "sync_recurring", lambda *a, **k: 0)
    monkeypatch.setattr(investments, "sync_holdings", lambda c, i, t: 3)
    assert sync.sync_item(conn, "item1", token="tok")["holdings"] == 3
    monkeypatch.setattr(investments, "sync_holdings", lambda c, i, t: (_ for _ in ()).throw(plaid_api.PlaidError({"error_code": "INVESTMENTS_LIMIT"})))
    r = sync.sync_item(conn, "item1", token="tok")
    assert r["error"] is None and r["holdings"] == 0
    assert conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "good"


def test_sync_item_skips_a_removed_item_and_isolates_unexpected_errors(home, conn, monkeypatch):
    from finnamon import secrets, sync
    from tests.conftest import seed
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    assert sync.sync_item(conn, "gone", token="tok")["error"] == "REMOVED"
    monkeypatch.setattr(sync, "sync_accounts", lambda *a, **k: (_ for _ in ()).throw(TypeError("float() argument must be a string or a real number, not 'NoneType'")))
    r = sync.sync_item(conn, "item1", token="tok")
    assert r["error"] == "SYNC_ERROR" and conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "SYNC_ERROR"


def test_first_pull_waits_longer_for_product_not_ready(home, conn, monkeypatch):
    from finnamon import plaid_api, sync
    from tests.conftest import seed
    seed(conn)
    monkeypatch.setattr(sync, "NOT_READY_SLEEP_S", 0)
    calls = {"n": 0}
    def not_ready(tok, cursor):
        calls["n"] += 1
        if calls["n"] < 25:
            raise plaid_api.PlaidError({"error_code": "PRODUCT_NOT_READY"})
        return {"added": [], "modified": [], "removed": [], "has_more": False, "next_cursor": "c1", "accounts": []}
    monkeypatch.setattr(plaid_api, "transactions_sync", not_ready)
    assert sync.sync_transactions(conn, "item1", "tok", "2026-09-19 12:00:00") == 0   # 24 not-ready answers on a first pull are fine
    assert conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "good"


def test_account_type_override_survives_sync_and_clears(conn, item, monkeypatch, capsys):
    from finnamon import cli, investments
    import json
    resp = {"accounts": [{"account_id": "cma", "name": "MANAGED CMA", "type": "depository", "subtype": "cash management", "mask": "1234",
                          "balances": {"current": 5000.0}}]}
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: resp)
    sync.sync_accounts(conn, "item1", "tok", "2026-09-19 06:00:00")
    assert investments.net_worth(conn)["cash"] == 5000
    cli.main(["account", "kind", "MANAGED CMA", "investment"]); capsys.readouterr()
    sync.sync_accounts(conn, "item1", "tok", "2026-09-19 12:00:00")   # Plaid still says depository
    nw = investments.net_worth(conn)
    assert nw["cash"] == 0 and nw["investments"] == 5000
    cli.main(["account", "list"])
    cma = {a["account_id"]: a for a in json.loads(capsys.readouterr().out)}["cma"]
    assert (cma["type"], cma["subtype"], cma["type_override"]) == ("investment", "brokerage", "investment")
    assert (cma["bank_type"], cma["bank_subtype"]) == ("depository", "cash management")   # Plaid's, shown beside the household's
    cli.main(["account", "kind", "cma", "--clear"]); capsys.readouterr()
    assert conn.execute("SELECT type, subtype, type_override FROM accounts WHERE account_id='cma'").fetchone()[:] == ("depository", "cash management", None)
    assert investments.net_worth(conn)["cash"] == 5000
    with pytest.raises(SystemExit):
        cli.main(["account", "kind", "cma", "piggybank"])


def test_account_type_on_a_manual_account(conn, item, capsys):
    from finnamon import cli, imports
    imports.add_account(conn, "HSBC Premier", "HSBC", "checking")
    r = imports.set_type(conn, "HSBC Premier", "savings")
    assert (r["type"], r["subtype"], r["type_override"]) == ("depository", "savings", "savings")
    r = imports.set_type(conn, "HSBC Premier", None)
    assert (r["type"], r["subtype"], r["type_override"]) == ("depository", "checking", None)
