"""Budgets, thresholds, category overrides, merchant aliases, suppressions: the CLI primitives
Claude calls. Claude proposes; these write. Every write is plain SQL on the household DB."""
from __future__ import annotations

import json
import math
import re
import sqlite3

from . import store, taxonomy


class ResolveError(Exception):
    def __init__(self, text: str, candidates: list[str]):
        self.text, self.candidates = text, candidates
        super().__init__(f"'{text}' matches {len(candidates)} categories" if candidates else f"'{text}' matches no category")


def resolve_category(text: str) -> str:
    code, cands = taxonomy.resolve(text)
    if code:
        return code
    raise ResolveError(text, cands)


# --- budgets ---------------------------------------------------------------------------------

def _positive(amount, what: str) -> float:
    try:
        v = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{what} must be a positive amount")
    return v


def budget_set(conn: sqlite3.Connection, name: str, amount: float, category: str | None = None) -> dict:
    name = name.strip().lower()
    amount = _positive(amount, "monthly limit")
    code = resolve_category(category or name)
    conn.execute(
        "INSERT INTO budgets (name, category, monthly_limit, active) VALUES (?,?,?,1) "
        "ON CONFLICT(name) DO UPDATE SET category=excluded.category, monthly_limit=excluded.monthly_limit, active=1",
        (name, code, float(amount)),
    )
    return {"name": name, "category": code, "monthly_limit": float(amount)}


def budget_remove(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("UPDATE budgets SET active=0 WHERE name=?", (name.strip().lower(),)).rowcount > 0


def budget_list(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    as_of = as_of or store.now_local()
    rows = conn.execute("SELECT id, name, category, monthly_limit FROM budgets WHERE active=1 ORDER BY name").fetchall()
    out = []
    for b in rows:
        mtd = month_to_date(conn, b["category"], as_of)
        day = int(as_of[8:10])
        days = conn.execute("SELECT CAST(strftime('%d', date(?, 'start of month', '+1 month', '-1 day')) AS INTEGER)", (as_of,)).fetchone()[0]
        out.append({"id": b["id"], "name": b["name"], "category": b["category"], "monthly_limit": b["monthly_limit"],
                    "spent": round(mtd, 2), "day": day, "pace": round(mtd * days / day, 2) if day else None})
    return out


# tx_now (migration 005): transactions with the alias and the category override resolved, mirrors excluded --
# the prelude's `tx` without the :as_of scoping. Everything here reads categories and merchants through it.
SPEND = "flow IN ('expense','refund')"   # tx_now's flow (migration 008): not a transfer between the household's own accounts, not a payment onto a linked card, not money in; the mortgage is fixed, not budgeted


def month_to_date(conn: sqlite3.Connection, category: str, as_of: str) -> float:
    r = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM tx_now "
        "WHERE pending=0 AND date >= date(?, 'start of month') AND date <= date(?) "
        f"AND {SPEND} AND (category = ? OR category_primary = ?)",
        (as_of, as_of, category, category)).fetchone()
    return float(r[0] or 0)


def suggest(conn: sqlite3.Connection, months: int = 6, as_of: str | None = None) -> dict:
    """The numbers Claude reasons over in Step 3: per-category monthly spend, spread, top merchants, fixed vs variable."""
    as_of = as_of or store.now_local()
    rows = conn.execute(
        "SELECT strftime('%Y-%m', date) ym, category cat, category_primary prim, round(sum(amount),2) spent "
        "FROM tx_now WHERE pending=0 AND amount>0 AND date >= date(?,'start of month', ?) AND date < date(?,'start of month') "
        f"AND {SPEND} GROUP BY ym, cat ORDER BY cat, ym", (as_of, f"-{months} months", as_of)).fetchall()
    by_cat: dict[str, dict] = {}
    for r in rows:
        c = by_cat.setdefault(r["cat"] or "UNCATEGORIZED", {"category": r["cat"], "primary": r["prim"], "months": {}})
        c["months"][r["ym"]] = r["spent"]
    out = []
    for c in by_cat.values():
        vals = sorted(c["months"].values())
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        top = conn.execute(   # the same relation and the same category as the totals above, or a category's total would list merchants that do not add up to it
            "SELECT display m, round(sum(amount)) s FROM tx_now "
            f"WHERE pending=0 AND amount>0 AND {SPEND} AND category=? AND date >= date(?,'start of month', ?) AND date < date(?,'start of month') GROUP BY m ORDER BY s DESC LIMIT 3",
            (c["category"], as_of, f"-{months} months", as_of)).fetchall()
        out.append({**c, "months_seen": n, "min": vals[0], "max": vals[-1], "median": round(med, 2),
                    "variance_ratio": round((vals[-1] - vals[0]) / med, 2) if med else None,
                    "top_merchants": [{"merchant": t[0], "total": t[1]} for t in top]})
    out.sort(key=lambda c: -c["median"])
    recurring = conn.execute("SELECT COALESCE(merchant_name, description) m, last_amount, frequency FROM recurring r JOIN accounts a ON a.account_id=r.account_id "
                             "AND a.mirror_of IS NULL WHERE direction='outflow' AND status<>'EARLY_DETECTION' ORDER BY last_amount DESC").fetchall()
    low = conn.execute("SELECT a.name, a.mask, min(b.available) lo FROM balances b JOIN accounts a ON a.account_id=b.account_id "
                       "WHERE a.type='depository' AND a.mirror_of IS NULL AND b.as_of >= datetime(?, ?) GROUP BY a.account_id", (as_of, f"-{months} months")).fetchall()
    return {"months": months, "as_of": as_of, "categories": out,
            "recurring": [{"merchant": r[0], "amount": r[1], "frequency": r[2]} for r in recurring],
            "lowest_balances": [{"account": r[0], "mask": r[1], "lowest_available": r[2]} for r in low],
            "existing_budgets": budget_list(conn, as_of)}


# --- thresholds, categories, aliases, suppressions ----------------------------------------------

def find_account(conn: sqlite3.Connection, text: str) -> sqlite3.Row:
    t = text.strip()
    rows = conn.execute("SELECT account_id, name, mask, type FROM accounts WHERE mirror_of IS NULL AND (account_id=? OR mask=? OR lower(name) LIKE ?)",
                        (t, t.lstrip("."), f"%{t.lower()}%")).fetchall()
    if len(rows) != 1:
        raise ValueError(f"'{text}' matches {len(rows)} accounts: " + ", ".join(f"{r['name']} …{r['mask']}" for r in rows))
    return rows[0]


def threshold_set(conn: sqlite3.Connection, account_text: str, amount: float) -> dict:
    a = find_account(conn, account_text)
    store.set_setting(conn, "low_balance_threshold", amount, a["account_id"])
    return {"account": a["name"], "mask": a["mask"], "threshold": float(amount)}


def category_set(conn: sqlite3.Connection, merchant: str, category_text: str) -> dict:
    code = resolve_category(category_text)
    if code in taxonomy.DETAILED:
        raise ValueError("a merchant override needs a detailed category, e.g. FOOD_AND_DRINK_GROCERIES")
    canonical = canonical_for(conn, merchant)
    conn.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed) VALUES (?,?,?) "
                 "ON CONFLICT(canonical) DO UPDATE SET pfc_primary=excluded.pfc_primary, pfc_detailed=excluded.pfc_detailed, created_at=datetime('now','localtime')",
                 (canonical, taxonomy.primary_of(code), code))
    return {"merchant": merchant, "canonical": canonical, "category": code}


def tx_category_set(conn: sqlite3.Connection, transaction_id: str, category_text: str | None) -> dict:
    """A one-time edit: this one transaction only, ahead of the merchant rule. category_text None clears it."""
    t = conn.execute("SELECT transaction_id, date, amount, display FROM tx_now WHERE transaction_id=?", (transaction_id.strip(),)).fetchone()   # a mirror's id: its twin is the one counted
    if not t:
        raise ValueError(f"no transaction '{transaction_id}' (ids are tx_now.transaction_id)")
    if category_text is None:
        n = conn.execute("DELETE FROM tx_category_override WHERE transaction_id=?", (t["transaction_id"],)).rowcount
        code = None
    else:
        code = resolve_category(category_text)
        if code in taxonomy.DETAILED:
            raise ValueError("a transaction override needs a detailed category, e.g. FOOD_AND_DRINK_GROCERIES")
        conn.execute("INSERT INTO tx_category_override (transaction_id, pfc_primary, pfc_detailed) VALUES (?,?,?) "
                     "ON CONFLICT(transaction_id) DO UPDATE SET pfc_primary=excluded.pfc_primary, pfc_detailed=excluded.pfc_detailed, created_at=datetime('now','localtime')",
                     (t["transaction_id"], taxonomy.primary_of(code), code))
    now = conn.execute("SELECT category FROM tx_now WHERE transaction_id=?", (t["transaction_id"],)).fetchone()[0]
    out = {"transaction_id": t["transaction_id"], "date": t["date"], "amount": t["amount"], "merchant": t["display"], "category": now}
    if category_text is None:
        out["cleared"] = bool(n)
    return out


def canonical_for(conn: sqlite3.Connection, merchant: str) -> str:
    """What the prelude will compute for this merchant: entity id if Plaid knows it, else the name as typed."""
    r = conn.execute("SELECT merchant_entity_id FROM transactions WHERE merchant_entity_id IS NOT NULL AND lower(merchant_name)=lower(?) LIMIT 1",
                     (merchant.strip(),)).fetchone()
    if r:
        return r[0]
    r = conn.execute("SELECT canonical FROM merchant_alias WHERE lower(name)=lower(?)", (merchant.strip(),)).fetchone()
    if not r:   # a raw bank string that a pattern alias covers (the longest pattern, as the prelude picks it)
        r = conn.execute("SELECT canonical FROM merchant_alias WHERE instr(name, '%') > 0 AND ? LIKE name ORDER BY length(name) DESC, name LIMIT 1",
                         (merchant.strip(),)).fetchone()
    return r[0] if r else merchant.strip()


PATTERN_MIN_LITERAL = 3   # "%" alone would alias every transaction


def alias_set(conn: sqlite3.Connection, raw_name: str, canonical: str) -> dict:
    """A raw bank string, or a pattern of one. A % anywhere makes it a pattern (there is no literal %): % matches any run
    of characters and, only then, _ matches one; SQLite LIKE, so a pattern is case-insensitive. Payees that put a
    per-payment number in the string (`Loan Payment Confirmation# %`) need one. Every match becomes one merchant, for
    duplicate and outlier alerts too, so the reply carries how many past transactions the pattern covers."""
    raw_name = raw_name.strip()
    if "%" in raw_name and len(re.sub(r"[%_\s]", "", raw_name)) < PATTERN_MIN_LITERAL:
        raise ValueError(f"a pattern alias needs at least {PATTERN_MIN_LITERAL} literal characters besides %, _ and spaces, or it would alias everything")
    if not raw_name or not canonical.strip():
        raise ValueError("an alias needs a raw name and a canonical")
    # what it covers today, so a too-broad pattern is visible at the one moment a person can still narrow it
    n, distinct = conn.execute("SELECT count(*), count(DISTINCT name) FROM transactions WHERE name = ? OR (instr(?, '%') > 0 AND name LIKE ?)",
                               (raw_name, raw_name, raw_name)).fetchone()
    conn.execute("INSERT INTO merchant_alias (name, canonical) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET canonical=excluded.canonical, created_at=datetime('now','localtime')",
                 (raw_name, canonical.strip()))
    return {"name": raw_name, "canonical": canonical.strip(), "matches": n, "distinct_names": distinct}


def normal(conn: sqlite3.Connection, canonical: str | None = None, kind: str | None = None, account: str | None = None,
           max_amount: float | None = None, note: str | None = None, alert_id: int | None = None) -> dict:
    """A structured suppression. With alert_id, defaults are taken from that alert's payload. A rule with no kind
    covers every detector but recurring_changed; that one needs its kind, and from an alert is scoped to its stream."""
    stream_id = None
    account_id = find_account(conn, account)["account_id"] if account else None
    if max_amount is not None:
        max_amount = _positive(max_amount, "max amount")
    if alert_id and not canonical:
        a = conn.execute("SELECT kind, key, payload_json, transaction_id, account_id, resolved_at FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not a:
            raise ValueError(f"no alert {alert_id}")
        if a["resolved_at"]:   # a second rule would outlive the undo, which removes only the one recorded
            raise ValueError(f"alert {alert_id} is already resolved; `finnamon alerts --undo {alert_id}` first to change how")
        p = json.loads(a["payload_json"])
        if a["transaction_id"]:
            # the same expression the prelude uses, so the suppression matches what the detectors see
            r = conn.execute("SELECT canonical FROM tx_now WHERE transaction_id=?", (a["transaction_id"],)).fetchone()
            canonical = r[0] if r else None
        canonical = canonical or p.get("merchant") or p.get("name")
        kind = kind or a["kind"]
        if kind == "anomaly:recurring_changed" == a["kind"]:   # "I cancelled it" acknowledges this stream, not the merchant
            stream_id = a["key"].removeprefix("anom:rec:").rsplit(":", 1)[0]   # anom:rec:<stream_id>:<YYYY-MM>
            r = conn.execute("SELECT COALESCE(merchant_entity_id, merchant_name, description) FROM recurring WHERE stream_id=?", (stream_id,)).fetchone()
            canonical = (r and r[0]) or canonical   # the detector's own expression, so the rule matches what it sees
            account_id = account_id or a["account_id"]
        if kind == "duplicate_charge":   # "normal" means this charge twice is fine, not every duplicate at the merchant
            # ponytail: a cap, so a smaller duplicate there stays muted too; an exact-amount column if that bites
            account_id = account_id or a["account_id"]
            max_amount = max_amount if max_amount is not None else p.get("amount")
    if canonical and not stream_id:
        canonical = canonical_for(conn, canonical) if not canonical.startswith("mch_") else canonical
    if not (canonical or account_id or (kind and max_amount is not None)):
        raise ValueError("a suppression needs a merchant, an account, or a kind with a max amount; an unscoped one would silence every detector")
    cur = conn.execute("INSERT INTO suppressions (kind, canonical, account_id, max_amount, note, stream_id) VALUES (?,?,?,?,?,?)",
                       (kind, canonical, account_id, max_amount, note, stream_id))
    if alert_id:   # the rule it wrote is recorded, so an undo removes that one and never another
        _resolve(conn, alert_id, "normal", cur.lastrowid)
    return {"id": cur.lastrowid, "kind": kind, "canonical": canonical, "account_id": account_id, "max_amount": max_amount, "note": note,
            "stream_id": stream_id}


def normal_remove(conn: sqlite3.Connection, rule_id: int) -> dict:
    """Delete one suppression by the id `normal --list` shows."""
    r = conn.execute("SELECT * FROM suppressions WHERE id=?", (rule_id,)).fetchone()
    if not r:
        raise ValueError(f"no suppression {rule_id}")
    conn.execute("DELETE FROM suppressions WHERE id=?", (rule_id,))
    conn.execute("UPDATE alerts SET suppression_id=NULL WHERE suppression_id=?", (rule_id,))   # ids are reused: an undo must not find a stranger's rule here
    return {"removed": dict(r)}


def _alert(conn: sqlite3.Connection, alert_id: int) -> sqlite3.Row:
    a = conn.execute("SELECT id, resolved_at, resolution, suppression_id FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not a:
        raise ValueError(f"no alert {alert_id}")
    return a


def _resolve(conn: sqlite3.Connection, alert_id: int, resolution: str, suppression_id: int | None) -> None:
    """Resolve the alert and every other open alert on its transaction: one transaction, one alert, one Dismiss."""
    ids = store.alert_group(conn, alert_id)
    conn.execute(f"UPDATE alerts SET resolved_at=datetime('now','localtime'), resolution=?, suppression_id=? WHERE id IN ({','.join('?' * len(ids))}) "
                 "AND (id=? OR resolved_at IS NULL)", (resolution, suppression_id, *ids, alert_id))


def dismiss(conn: sqlite3.Connection, alert_id: int) -> dict:
    """Resolve one alert and write no rule: fine this once, and the next such event still alerts."""
    if _alert(conn, alert_id)["resolved_at"]:
        raise ValueError(f"alert {alert_id} is already resolved")
    _resolve(conn, alert_id, "dismissed", None)
    return {"id": alert_id, "resolution": "dismissed"}


def undo(conn: sqlite3.Connection, alert_id: int) -> dict:
    """Reopen an alert resolved by `normal --alert` or `alerts --dismiss`; the first's rule goes with it, only that one."""
    a = _alert(conn, alert_id)
    if not a["resolved_at"]:
        raise ValueError(f"alert {alert_id} is not resolved")
    if a["resolution"] not in ("normal", "dismissed"):
        raise ValueError(f"alert {alert_id} was resolved by Finnamon or before it recorded how; nothing to undo "
                         "(`finnamon normal --list` and `--remove` for a rule)")
    ids = store.alert_group(conn, alert_id)   # the transaction reopens as it was resolved: the members this same action resolved
    conn.execute(f"UPDATE alerts SET resolved_at=NULL, resolution=NULL, suppression_id=NULL WHERE id IN ({','.join('?' * len(ids))}) "
                 "AND (id=? OR (resolution=? AND suppression_id IS ?))", (*ids, alert_id, a["resolution"], a["suppression_id"]))
    removed = normal_remove(conn, a["suppression_id"])["removed"] if a["suppression_id"] is not None else None
    return {"id": alert_id, "undone": a["resolution"], "removed_rule": removed}
