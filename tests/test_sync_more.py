import json
"""Sync error paths: PRODUCT_NOT_READY retries, transactional page apply, recurring skips, missing token."""
import sqlite3

import pytest

from finnamon import notify, plaid_api, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, plaid_txn, seed
from tests.test_sync import ScriptedPlaid, page


@pytest.fixture
def item(conn):
    ids = seed(conn, first_synced_at=None)
    conn.execute("UPDATE items SET first_synced_at=NULL, last_synced_at=NULL, cursor=NULL WHERE item_id='item1'")
    return ids


def scripted(monkeypatch, pages):
    sp = ScriptedPlaid(pages)
    monkeypatch.setattr(plaid_api, "transactions_sync", sp.transactions_sync)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    return sp


def test_product_not_ready_retried_then_exhausted(conn, item, monkeypatch):
    nr = PlaidError({"error_code": "PRODUCT_NOT_READY", "error_message": "not ready"})
    sp = scripted(monkeypatch, [nr, nr, page([plaid_txn("t1", "chk", "2026-09-11", 5, "x")])])
    assert sync.sync_transactions(conn, "item1", "tok", AS_OF, not_ready_retries=2) == 1
    assert sp.calls == [None, None, None]
    # retries exhausted: the error escapes and sync_item records it on the Item
    scripted(monkeypatch, [nr])
    with pytest.raises(PlaidError):
        sync.sync_transactions(conn, "item1", "tok", AS_OF, not_ready_retries=0)
    # MUTATION_DURING_PAGINATION three times in a row: gives up quietly with what it has, cursor untouched
    mut = PlaidError({"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "error_message": "x"})
    sp = scripted(monkeypatch, [mut, mut, mut])
    assert sync.sync_transactions(conn, "item1", "tok", AS_OF) == 0 and sp.calls == ["c1", "c1", "c1"]


def test_apply_page_transactional_recurring_and_missing_token(conn, item, monkeypatch):
    good = plaid_txn("ok", "chk", "2026-09-11", 5, "x")
    bad = {**plaid_txn("bad", "chk", "2026-09-11", 5, "x")}
    del bad["amount"]
    with pytest.raises(KeyError):
        sync.apply_page(conn, "item1", page([good, bad], cursor="c9"), AS_OF)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0  # 'ok' rolled back with the page
    assert conn.execute("SELECT cursor FROM items").fetchone()[0] is None       # cursor never advanced
    with pytest.raises(sqlite3.IntegrityError):  # unknown account: FK violation, same rollback
        sync.apply_page(conn, "item1", page([good, plaid_txn("fk", "nope", "2026-09-11", 5, "x")], cursor="c9"), AS_OF)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0 and conn.in_transaction is False
    # a page of only removals still moves the cursor but never sets the baseline
    assert sync.apply_page(conn, "item1", page(removed=["ghost"], cursor="c2"), AS_OF) == 1
    assert tuple(conn.execute("SELECT cursor, first_synced_at FROM items").fetchone()) == ("c2", None)

    # sync_accounts: a bad account rolls the batch back too (balances are append-only, so this matters)
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: {"accounts": [{"account_id": "chk", "balances": {"current": 1}}, {"name": "no id"}]})
    with pytest.raises(KeyError):
        sync.sync_accounts(conn, "item1", "tok", AS_OF)
    assert conn.execute("SELECT count(*) FROM balances").fetchone()[0] == 0 and conn.in_transaction is False
    # --- recurring streams and the missing-token guard -------------------------------------
    for code in ("PRODUCT_NOT_READY", "PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_ENABLED"):
        monkeypatch.setattr(plaid_api, "recurring_get", lambda tok, c=code: (_ for _ in ()).throw(PlaidError({"error_code": c})))
        assert sync.sync_recurring(conn, "item1", "tok", AS_OF) == 0
    monkeypatch.setattr(plaid_api, "recurring_get", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "API_ERROR"})))
    with pytest.raises(PlaidError):
        sync.sync_recurring(conn, "item1", "tok", AS_OF)
    # a bad stream (unknown account) rolls the whole recurring batch back
    monkeypatch.setattr(plaid_api, "recurring_get", lambda tok: {"outflow_streams": [
        {"stream_id": "s1", "account_id": "chk", "status": "MATURE"}, {"stream_id": "s2", "account_id": "nope", "status": "MATURE"}]})
    with pytest.raises(sqlite3.IntegrityError):
        sync.sync_recurring(conn, "item1", "tok", AS_OF)
    assert conn.execute("SELECT count(*) FROM recurring").fetchone()[0] == 0 and conn.in_transaction is False
    # no token in secrets.toml: recorded on the Item like any other Plaid error, never raised (so sync_all keeps going)
    res = sync.sync_item(conn, "item1")
    assert res["error"] == "NO_TOKEN"
    assert conn.execute("SELECT status FROM items WHERE item_id='item1'").fetchone()[0] == "NO_TOKEN"
    assert sync.sync_all(conn)[0]["error"] == "NO_TOKEN"


def test_consent_expiry_within_a_week_warns_once(conn, item, monkeypatch):
    soon, later = "2026-09-25T12:00:00Z", "2026-10-20T12:00:00Z"   # noon UTC: the same local date in any US/EU zone   # AS_OF is 2026-09-20: five days and a month out
    resp = {"accounts": [{"account_id": "chk", "balances": {"current": 1}}], "item": {"consent_expiration_time": later}}
    monkeypatch.setattr(plaid_api, "accounts_get", lambda tok: resp)
    warned = lambda: [r[0] for r in conn.execute("SELECT key FROM alerts WHERE kind='consent_expiring'")]
    sync.sync_accounts(conn, "item1", "tok", AS_OF)
    assert warned() == []                                               # a month out: quiet
    resp["item"]["consent_expiration_time"] = soon
    sync.sync_accounts(conn, "item1", "tok", AS_OF)
    resp["item"]["consent_expiration_time"] = "2026-09-25T12:00:00.000+00:00"   # the same instant, spelled differently
    sync.sync_accounts(conn, "item1", "tok", AS_OF)
    assert warned() == ["health:item1:consent:2026-09-25"]              # once, not every run
    resp["item"] = {"consent_expiration_time": None}
    sync.sync_accounts(conn, "item1", "tok", AS_OF)                     # most institutions send none
    assert len(warned()) == 1


def test_consent_expiry_past_naive_or_garbage_is_quiet_or_utc(conn, item):
    past, naive = "2026-09-18T12:00:00Z", "2026-09-22T12:00:00"          # AS_OF is 2026-09-19; no offset: read as UTC, not a crash
    for exp in (past, "not-a-date", naive):
        sync._warn_consent(conn, "item1", exp, AS_OF)
    rows = conn.execute("SELECT key, payload_json FROM alerts WHERE kind='consent_expiring'").fetchall()
    assert [r[0] for r in rows] == ["health:item1:consent:2026-09-22"] and '"institution": "Chase"' in rows[0][1]
    assert '"duplicate": false' in rows[0][1]
    conn.execute("INSERT INTO items (item_id, institution, owner, status) SELECT 'item2', institution, owner, 'good' FROM items")
    sync._warn_consent(conn, "item1", "2026-09-23T12:00:00Z", AS_OF)       # a second login at Chase: the alert says which one
    p = json.loads(conn.execute("SELECT payload_json FROM alerts WHERE key='health:item1:consent:2026-09-23'").fetchone()[0])
    assert p["duplicate"] is True and p["owner"]
    assert "fix item1" in notify.render(conn.execute("SELECT * FROM alerts WHERE key='health:item1:consent:2026-09-23'").fetchone())
