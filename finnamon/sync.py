"""Plaid → SQLite.

    sync_item(conn, item_id)      one Item: accounts + balances, /transactions/sync pages, recurring streams
    sync_all(conn)                every Item with a token; never raises for one Item's Plaid error

Page loop (the part that must be right):

    cursor_at_start = items.cursor
    loop:
      page = /transactions/sync(cursor)
      BEGIN
        apply added / modified / removed
        items.cursor = page.next_cursor
        first_synced_at = now  (only if unset and this page had rows)
      COMMIT
      if not page.has_more: break
    on TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION: restart from cursor_at_start (upserts converge)
    on ITEM_LOGIN_REQUIRED / other Plaid error: items.status = code, last_error = message; stop
    at most MAX_RESTARTS mutation restarts per run; then log and keep what was applied (next run resumes)

A fresh Item can answer an empty page with no cursor for a few seconds; treat that as "not ready"
and retry a handful of times, exactly like PRODUCT_NOT_READY. Verified against the sandbox.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from . import config, investments, plaid_api, store
from .plaid_api import PlaidError

log = logging.getLogger("finnamon.sync")


def _txn_row(t: dict) -> tuple:
    pfc = t.get("personal_finance_category") or {}
    loc = t.get("location") or {}
    return (
        t["transaction_id"], t["account_id"], t["date"], t.get("datetime"), float(t["amount"]),
        t.get("name"), t.get("merchant_name"), t.get("merchant_entity_id"),
        pfc.get("primary"), pfc.get("detailed"), pfc.get("confidence_level"),
        1 if t.get("pending") else 0, t.get("pending_transaction_id"),
        loc.get("city"), loc.get("region"), json.dumps(t, default=str),
    )


_UPSERT_TXN = """
INSERT INTO transactions (transaction_id, account_id, date, datetime, amount, name, merchant_name,
  merchant_entity_id, pfc_primary, pfc_detailed, pfc_confidence, pending, pending_transaction_id,
  location_city, location_region, raw_json)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(transaction_id) DO UPDATE SET
  account_id=excluded.account_id, date=excluded.date, datetime=excluded.datetime, amount=excluded.amount,
  name=excluded.name, merchant_name=excluded.merchant_name, merchant_entity_id=excluded.merchant_entity_id,
  pfc_primary=excluded.pfc_primary, pfc_detailed=excluded.pfc_detailed, pfc_confidence=excluded.pfc_confidence,
  pending=excluded.pending, pending_transaction_id=excluded.pending_transaction_id,
  location_city=excluded.location_city, location_region=excluded.location_region, raw_json=excluded.raw_json
"""


_UPSERT_ACCOUNT = (
    "INSERT INTO accounts (account_id, item_id, name, official_name, type, subtype, mask, persistent_account_id, owner, source_type, source_subtype) "
    "VALUES (?,?,?,?,?,?,?,?,?,?5,?6) ON CONFLICT(account_id) DO UPDATE SET name=excluded.name, official_name=excluded.official_name, "
    "source_type=excluded.type, source_subtype=excluded.subtype, mask=excluded.mask, persistent_account_id=excluded.persistent_account_id, "
    # the household's type (`finnamon account type`) outlives every sync; Plaid's lands in source_* for --clear
    "type=CASE WHEN accounts.type_override IS NULL THEN excluded.type ELSE accounts.type END, "
    "subtype=CASE WHEN accounts.type_override IS NULL THEN excluded.subtype ELSE accounts.subtype END"
)
MAX_RESTARTS = 3          # TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION restarts per run
TRANSIENT_CODES = {"NETWORK_ERROR", "INTERNAL_SERVER_ERROR", "PLANNED_MAINTENANCE", "INSTITUTION_DOWN", "INSTITUTION_NOT_RESPONDING", "PRODUCT_NOT_READY"}
FIRST_PULL_NOT_READY_RETRIES = 40   # 730 days at a big bank can take a couple of minutes; only the first pull (no cursor) waits this long
TRANSIENT_TYPES = {"RATE_LIMIT_EXCEEDED", "API_ERROR"}   # Plaid puts rate limits in error_type; the code is TRANSACTIONS_SYNC_LIMIT etc.
NOT_READY_SLEEP_S = 3


def _account_row(a: dict, item_id: str, owner: str) -> tuple:
    return (a["account_id"], item_id, a.get("name"), a.get("official_name"), a.get("type"), a.get("subtype"),
            a.get("mask"), a.get("persistent_account_id"), owner)


def apply_page(conn: sqlite3.Connection, item_id: str, page: dict, now: str) -> int:
    """Apply one /transactions/sync page and its cursor in a single transaction. Returns rows touched."""
    added, modified, removed = page.get("added", []), page.get("modified", []), page.get("removed", [])
    with store.tx(conn):
        if page.get("accounts"):  # a page can name an account /accounts/get hadn't shown yet
            owner = conn.execute("SELECT owner FROM items WHERE item_id=?", (item_id,)).fetchone()[0]
            for a in page["accounts"]:
                conn.execute(_UPSERT_ACCOUNT, _account_row(a, item_id, owner))
        for t in added + modified:
            # A posted transaction that names its pending twin replaces it, keeping a one-time category edit made on the pending one.
            if t.get("pending_transaction_id"):
                conn.execute("DELETE FROM transactions WHERE transaction_id=?", (t["pending_transaction_id"],))
                conn.execute("UPDATE OR IGNORE tx_category_override SET transaction_id=? WHERE transaction_id=?", (t["transaction_id"], t["pending_transaction_id"]))   # an edit on the posted one wins
            conn.execute(_UPSERT_TXN, _txn_row(t))
        for r in removed:
            conn.execute("DELETE FROM transactions WHERE transaction_id=?", (r["transaction_id"],))
        conn.execute("UPDATE items SET cursor=?, last_synced_at=?, status='good', last_error=NULL WHERE item_id=?",
                     (page.get("next_cursor"), now, item_id))
        if added or modified:
            conn.execute("UPDATE items SET first_synced_at=COALESCE(first_synced_at, ?) WHERE item_id=?", (now, item_id))
    return len(added) + len(modified) + len(removed)


def sync_accounts(conn: sqlite3.Connection, item_id: str, token: str, now: str) -> list[dict]:
    resp = plaid_api.accounts_get(token)
    owner = conn.execute("SELECT owner FROM items WHERE item_id=?", (item_id,)).fetchone()[0]
    with store.tx(conn):
        for a in resp["accounts"]:
            conn.execute(_UPSERT_ACCOUNT, _account_row(a, item_id, owner))
            b = a.get("balances") or {}
            conn.execute("INSERT OR REPLACE INTO balances (account_id, as_of, current, available) VALUES (?,?,?,?)",
                         (a["account_id"], now, b.get("current"), b.get("available")))
        _warn_consent(conn, item_id, (resp.get("item") or {}).get("consent_expiration_time"), now)
    return resp["accounts"]


CONSENT_WARN_DAYS = 7


def _warn_consent(conn: sqlite3.Connection, item_id: str, expires: str | None, now: str) -> None:
    """Some institutions' consent lapses on a date (/accounts/get's item.consent_expiration_time); without webhooks this is the
    only PENDING_EXPIRATION signal. One alert per expiry date: the key carries it, so a renewed consent warns again next time.
    Keyed health:<item_id>:... so `link --remove` retires an unsent one."""
    try:
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return   # None or garbage: nothing to warn about, and never a reason to roll back the account sync
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    left = when - datetime.strptime(now, "%Y-%m-%d %H:%M:%S").astimezone()   # the run's clock (store.now_local), so a replay at as_of agrees
    if not timedelta(0) < left <= timedelta(days=CONSENT_WARN_DAYS):
        return
    day = when.astimezone().date().isoformat()   # the household's date; also the key, so a reformatted timestamp is not a new alert
    inst = conn.execute("SELECT institution, owner, EXISTS (SELECT 1 FROM items j WHERE j.institution = i.institution AND j.item_id <> i.item_id AND j.source = 'plaid') "
                        "FROM items i WHERE item_id=?", (item_id,)).fetchone()
    conn.execute("INSERT OR IGNORE INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','consent_expiring',?,?,?)",
                 (f"health:{item_id}:consent:{day}", json.dumps({"item_id": item_id, "institution": inst[0] if inst else None,
                                                                  "owner": inst[1] if inst else None, "duplicate": bool(inst and inst[2]),
                                                                  "expires": day, "consent_expiration_time": expires, "as_of": now}), now))


def sync_transactions(conn: sqlite3.Connection, item_id: str, token: str, now: str, not_ready_retries: int = 10) -> int:
    cursor_at_start = conn.execute("SELECT cursor FROM items WHERE item_id=?", (item_id,)).fetchone()[0]
    cursor = cursor_at_start
    if not cursor:
        not_ready_retries = max(not_ready_retries, FIRST_PULL_NOT_READY_RETRIES)
    total = 0
    for _attempt in range(MAX_RESTARTS):
        try:
            while True:
                page = plaid_api.transactions_sync(token, cursor)
                fresh_empty = not cursor and page.get("added") == [] and not page.get("has_more") and not page.get("next_cursor")
                if fresh_empty and not_ready_retries > 0:
                    not_ready_retries -= 1
                    time.sleep(NOT_READY_SLEEP_S)
                    continue
                total += apply_page(conn, item_id, page, now)
                cursor = page.get("next_cursor")
                if not page.get("has_more"):
                    return total
        except PlaidError as e:
            if e.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                cursor = cursor_at_start
                continue
            if e.code == "PRODUCT_NOT_READY" and not_ready_retries > 0:
                not_ready_retries -= 1
                time.sleep(NOT_READY_SLEEP_S)
                continue
            raise
    log.warning("sync %s: gave up after %d MUTATION_DURING_PAGINATION restarts; partial page set applied, will resume next run", item_id, MAX_RESTARTS)
    return total


def sync_recurring(conn: sqlite3.Connection, item_id: str, token: str, now: str) -> int:
    try:
        resp = plaid_api.recurring_get(token)
    except PlaidError as e:
        if e.code in ("PRODUCT_NOT_READY", "PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_ENABLED"):
            log.info("recurring not available for %s: %s", item_id, e.code)
            return 0
        raise
    n = 0
    with store.tx(conn):
        for direction in ("inflow", "outflow"):
            for s in resp.get(f"{direction}_streams", []):
                conn.execute(
                    "INSERT INTO recurring (stream_id, account_id, direction, merchant_entity_id, merchant_name, description, "
                    "frequency, avg_amount, last_amount, first_date, last_date, predicted_next_date, status, is_active, first_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stream_id) DO UPDATE SET "
                    "merchant_entity_id=excluded.merchant_entity_id, merchant_name=excluded.merchant_name, description=excluded.description, "
                    "frequency=excluded.frequency, avg_amount=excluded.avg_amount, last_amount=excluded.last_amount, "
                    "first_date=excluded.first_date, last_date=excluded.last_date, predicted_next_date=excluded.predicted_next_date, "
                    "status=excluded.status, is_active=excluded.is_active",  # first_seen_at never updated: that is what makes new_recurring reproducible
                    (s["stream_id"], s["account_id"], direction, s.get("merchant_entity_id"), s.get("merchant_name"),
                     s.get("description"), s.get("frequency"),
                     (s.get("average_amount") or {}).get("amount"), (s.get("last_amount") or {}).get("amount"),
                     s.get("first_date"), s.get("last_date"), s.get("predicted_next_date"), s.get("status"),
                     None if s.get("is_active") is None else int(bool(s["is_active"])), now),
                )
                n += 1
    return n


def sync_item(conn: sqlite3.Connection, item_id: str, token: str | None = None) -> dict:
    token = token or config.item_token(item_id)
    now = store.now_local()
    result = {"item_id": item_id, "accounts": 0, "transactions": 0, "recurring": 0, "holdings": 0, "error": None}
    row = conn.execute("SELECT source FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not row:
        result["error"] = "REMOVED"   # unlinked while this run was in progress
        return result
    if row[0] != "plaid":
        conn.execute("UPDATE items SET status='good', last_error=NULL WHERE item_id=? AND status<>'good'", (item_id,))   # a daemon still on older code may have stamped NO_TOKEN on it
        result["error"] = "MANUAL"   # fed by `finnamon import`, nothing to pull
        return result
    try:
        if not token:
            raise PlaidError({"error_code": "NO_TOKEN", "error_message": f"no access token for {item_id} in secrets.toml"})
        result["accounts"] = len(sync_accounts(conn, item_id, token, now))
        result["transactions"] = sync_transactions(conn, item_id, token, now)
        result["recurring"] = sync_recurring(conn, item_id, token, now)
        try:
            result["holdings"] = investments.sync_holdings(conn, item_id, token)
        except PlaidError as e:  # an investments-only failure is not the Item's health; balances still cover the accounts
            log.warning("holdings %s: %s", item_id, e)
    except PlaidError as e:
        result["transient"] = e.code in TRANSIENT_CODES or e.type in TRANSIENT_TYPES or e.code.startswith("HTTP_5")
        if not result["transient"]:  # an outage is not the Item's fault; sync_health's staleness clause covers a long one
            conn.execute("UPDATE items SET status=?, last_error=? WHERE item_id=?", (e.code, e.message[:500], item_id))
        result["error"] = e.code
        log.warning("sync %s failed: %s", item_id, e)
    except sqlite3.IntegrityError as e:  # one Item's bad page must not stop the others
        conn.execute("UPDATE items SET status='DB_ERROR', last_error=? WHERE item_id=?", (str(e)[:500], item_id))
        result["error"] = "DB_ERROR"
        log.exception("sync %s hit a constraint", item_id)
    except sqlite3.OperationalError:
        raise  # busy/locked: the whole run retries
    except Exception as e:  # noqa: BLE001 - a malformed page (Plaid contract violation) must not wedge every other Item forever
        conn.execute("UPDATE items SET status='SYNC_ERROR', last_error=? WHERE item_id=?", (f"{type(e).__name__}: {e}"[:500], item_id))
        result["error"] = "SYNC_ERROR"
        log.exception("sync %s failed unexpectedly", item_id)
    return result


def sync_all(conn: sqlite3.Connection) -> list[dict]:
    out = []
    tokens = config.item_tokens()
    for (item_id,) in conn.execute("SELECT item_id FROM items WHERE source='plaid' ORDER BY created_at").fetchall():
        out.append(sync_item(conn, item_id, tokens.get(item_id)))
    return out
