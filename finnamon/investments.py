"""Milestone 6: holdings and net worth. Investments is a separate Plaid product; an Item linked
with transactions only will answer PRODUCTS_NOT_SUPPORTED and we skip it quietly."""
from __future__ import annotations

import logging
import sqlite3

from . import properties, config, plaid_api, store
from .plaid_api import PlaidError

log = logging.getLogger("finnamon.investments")


UNSUPPORTED = ("PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_ENABLED", "NO_INVESTMENT_ACCOUNTS", "INVALID_PRODUCT")


def sync_holdings(conn: sqlite3.Connection, item_id: str, token: str | None = None) -> int:
    """Refresh the Item's holdings. The table holds the latest snapshot per account, not history (balances carry
    the account's value over time); an account that sold everything ends up with no rows, not a stale one."""
    if store.get_state(conn, f"holdings_unsupported:{item_id}"):
        return 0   # asked once, answered "no investments"; don't ask every cycle
    token = token or config.item_token(item_id)
    now = store.now_local()
    try:
        resp = plaid_api.investments_holdings_get(token)
    except PlaidError as e:
        if e.code in UNSUPPORTED:
            log.info("no holdings for %s: %s", item_id, e.code)
            store.set_state(conn, f"holdings_unsupported:{item_id}", e.code)
            return 0
        if e.code == "PRODUCT_NOT_READY":
            return 0
        raise
    secs = {s["security_id"]: s for s in resp.get("securities", [])}
    n = 0
    with store.tx(conn):
        for a in resp.get("accounts", []):
            conn.execute("DELETE FROM holdings WHERE account_id=?", (a["account_id"],))
        for h in resp.get("holdings", []):
            s = secs.get(h["security_id"], {})
            conn.execute("INSERT OR REPLACE INTO holdings (account_id, security_id, as_of, name, ticker, quantity, price, value) VALUES (?,?,?,?,?,?,?,?)",
                         (h["account_id"], h["security_id"], now, s.get("name"), s.get("ticker_symbol"), h.get("quantity"),
                          h.get("institution_price"), h.get("institution_value")))
            n += 1
    return n


def sync_all_holdings(conn: sqlite3.Connection) -> dict[str, int]:
    tokens = config.item_tokens()   # walk the items table, not the token file: an orphan token must not break the command
    return {item_id: sync_holdings(conn, item_id, tokens.get(item_id)) for (item_id,) in conn.execute("SELECT item_id FROM items") if tokens.get(item_id)}


LIABILITY_TYPES = ("credit", "loan")   # every other Plaid type (depository, investment, other, the legacy brokerage) is an asset


def net_worth(conn: sqlite3.Connection) -> dict:
    """Latest balances per account (assets positive, liabilities negative) plus latest holdings value."""
    rows = conn.execute(
        "SELECT a.account_id, a.name, a.mask, a.type, b.current FROM accounts a JOIN balances b ON b.account_id=a.account_id "
        "AND b.as_of=(SELECT max(as_of) FROM balances WHERE account_id=a.account_id) WHERE a.mirror_of IS NULL").fetchall()
    cash = sum(r["current"] or 0 for r in rows if r["type"] == "depository")
    invested = sum(r["current"] or 0 for r in rows if r["type"] != "depository" and r["type"] not in LIABILITY_TYPES)
    assets = cash + invested                                  # what the banks report
    liabilities = sum(r["current"] or 0 for r in rows if r["type"] in LIABILITY_TYPES)
    hold = conn.execute("SELECT COALESCE(sum(value),0) FROM holdings h WHERE as_of=(SELECT max(as_of) FROM holdings WHERE account_id=h.account_id)").fetchone()[0]
    prop = properties.total(conn)                             # what the household says its house/car are worth
    return {"assets": round(assets, 2), "cash": round(cash, 2), "investments": round(invested, 2), "property": round(prop, 2),
            "liabilities": round(liabilities, 2), "holdings_value": round(hold or 0, 2),
            "net_worth": round(assets + prop - liabilities, 2),
            "accounts": [{"name": r["name"], "mask": r["mask"], "type": r["type"], "current": r["current"]} for r in rows]}


def net_worth_history(conn: sqlite3.Connection, months: int = 12) -> list[dict]:
    """One point per day (net worth, assets, liabilities): every account's last known balance on that day, carried forward from earlier days when
    a bank did not sync (a login that expired, a Plaid hiccup, the days before it was linked). Several syncs a day
    count once. The last point is what the banks report in net_worth() (assets minus liabilities; the page adds the
    stated property on top), and the dashboard's month change is read off this series."""
    since = f"-{months} months"
    rows = conn.execute(
        "WITH acct AS (SELECT account_id, type FROM accounts WHERE mirror_of IS NULL), "
        "     day AS (SELECT b.account_id, max(b.as_of) m FROM balances b JOIN acct USING (account_id) "
        "             WHERE b.as_of >= datetime('now', ?) GROUP BY b.account_id, date(b.as_of)), "
        "     seed AS (SELECT b.account_id, max(b.as_of) m FROM balances b JOIN acct USING (account_id) "
        "              WHERE b.as_of < datetime('now', ?) GROUP BY b.account_id) "
        "SELECT b.account_id, a.type, date(b.as_of) d, b.current, b.as_of >= datetime('now', ?) shown "
        "FROM balances b JOIN acct a USING (account_id) "
        "WHERE EXISTS (SELECT 1 FROM day WHERE day.account_id = b.account_id AND day.m = b.as_of) "
        "   OR EXISTS (SELECT 1 FROM seed WHERE seed.account_id = b.account_id AND seed.m = b.as_of) "
        "ORDER BY b.as_of", (since, since, since)).fetchall()
    latest: dict[str, tuple[float, bool]] = {}   # account -> (balance, is a liability)
    out: list[dict] = []
    for r in rows:
        latest[r["account_id"]] = (r["current"] or 0, r["type"] in LIABILITY_TYPES)
        if not r["shown"]:
            continue   # the balance an account carried into the window
        assets = sum(v for v, debt in latest.values() if not debt)
        liabilities = sum(v for v, debt in latest.values() if debt)
        point = {"date": r["d"], "net_worth": round(assets - liabilities, 2), "assets": round(assets, 2), "liabilities": round(liabilities, 2)}
        if out and out[-1]["date"] == r["d"]:
            out[-1] = point
        else:
            out.append(point)
    return out
