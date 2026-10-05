"""Budgets, thresholds, category overrides, merchant aliases, suppressions: the CLI primitives
Claude calls. Claude proposes; these write. Every write is plain SQL on the household DB."""
from __future__ import annotations

import difflib
import json
import math
import re
import sqlite3

from . import detect, store, taxonomy


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


def budget_set(conn: sqlite3.Connection, name: str, amount: float, categories: list[str] | str | None = None,
               merchants: list[str] | None = None, fixed: bool | None = None) -> dict:
    """Categories and merchants given replace the budget's selectors; none given keeps an existing budget's (the
    dashboard's limit edit) and a new one takes its name as the category. fixed None keeps what it was."""
    name = name.strip().lower()
    amount = _positive(amount, "monthly limit")
    categories = [categories] if isinstance(categories, str) else list(categories or [])
    merchants = [m.strip() for m in merchants or []]
    if any(not m for m in merchants):
        raise ValueError("a merchant needs a name")
    old = conn.execute("SELECT id FROM budgets WHERE name=?", (name,)).fetchone()
    keep = not (categories or merchants) and old and conn.execute("SELECT 1 FROM budget_selectors WHERE budget_id=?", (old[0],)).fetchone()
    codes = [] if keep else list(dict.fromkeys(resolve_category(c) for c in categories or ([] if merchants else [name])))
    sel = [("category", c, None) for c in codes] + [("merchant", canonical_for(conn, m), m) for m in merchants]
    conn.execute(
        "INSERT INTO budgets (name, category, monthly_limit, active, fixed) VALUES (?,?,?,1,?) "
        "ON CONFLICT(name) DO UPDATE SET category=CASE WHEN ? THEN category ELSE excluded.category END, monthly_limit=excluded.monthly_limit, active=1, "
        "fixed=COALESCE(?, fixed)",
        (name, codes[0] if codes else "", amount, int(bool(fixed)), bool(keep), None if fixed is None else int(fixed)))
    bid = conn.execute("SELECT id FROM budgets WHERE name=?", (name,)).fetchone()[0]
    if not keep:
        conn.execute("DELETE FROM budget_selectors WHERE budget_id=?", (bid,))
        conn.executemany("INSERT OR IGNORE INTO budget_selectors (budget_id, kind, value, label) VALUES (?,?,?,?)", [(bid, *s) for s in sel])
    return next(b for b in _budgets(conn, "WHERE id=?", (bid,)))


def budget_remove(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("UPDATE budgets SET active=0 WHERE name=?", (name.strip().lower(),)).rowcount > 0


def _budgets(conn: sqlite3.Connection, where: str, args: tuple = ()) -> list[dict]:
    out = []
    for b in conn.execute(f"SELECT id, name, category, monthly_limit, fixed FROM budgets {where} ORDER BY name", args).fetchall():
        sel = conn.execute("SELECT kind, value, label FROM budget_selectors WHERE budget_id=? ORDER BY rowid", (b["id"],)).fetchall()
        cats = [s["value"] for s in sel if s["kind"] == "category"]
        merchants = [s["label"] or s["value"] for s in sel if s["kind"] == "merchant"]
        out.append({"id": b["id"], "name": b["name"], "category": cats[0] if cats else None,   # the first, as before selectors
                    "categories": cats, "merchants": merchants, "covers": [taxonomy.label(c) for c in cats] + merchants,   # covers: the dashboard's line
                    "fixed": bool(b["fixed"]), "monthly_limit": b["monthly_limit"]})
    return out


def budget_list(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    as_of = as_of or store.now_local()
    day = int(as_of[8:10])
    days = conn.execute("SELECT CAST(strftime('%d', date(?, 'start of month', '+1 month', '-1 day')) AS INTEGER)", (as_of,)).fetchone()[0]
    out = []
    for b in _budgets(conn, "WHERE active=1"):
        mtd, rec = month_to_date(conn, b["id"], as_of)
        pace = mtd if b["fixed"] else rec + (mtd - rec) * days / day
        out.append({**b, "spent": round(mtd, 2), "recurring": round(rec, 2), "day": day, "pace": round(pace, 2)})
    return out


# tx_now (migration 005): transactions with the alias and the category override resolved, mirrors excluded --
# the prelude's `tx` without the :as_of scoping. Everything here reads categories and merchants through it.
# tx_now's flow (migration 008): not a transfer between the household's own accounts, not a payment onto a linked card,
# not money in. The mortgage counts, once: its checking-side payment is 'mortgage', the loan account's mirror 'skipped'.
SPEND = "flow IN ('expense','refund','mortgage')"
# KEEP IN STEP WITH detectors/rules/budget_pace.sql. A budget counts a transaction any of its selectors matches, once.
# A payment paired with the mortgage loan is a mortgage whatever Plaid filed it under (often a transfer).
MATCH = ("EXISTS (SELECT 1 FROM budget_selectors s WHERE s.budget_id = :bid AND ((s.kind = 'category' AND (s.value IN (t.category, t.category_primary) "
         "OR (t.flow = 'mortgage' AND s.value IN ('LOAN_PAYMENTS_MORTGAGE_PAYMENT', 'LOAN_PAYMENTS')))) "
         "OR (s.kind = 'merchant' AND s.value = t.canonical)))")
# A charge of one of Plaid's recurring streams (a bill, a subscription): pace counts it once, never projects it.
# Same merchant and within 25% of the stream's amount, so a one-off at a merchant that also bills monthly stays variable.
# ponytail: merchant + amount, not Plaid's transaction_ids per stream (not stored); store them if this misfiles.
RECURRING = ("EXISTS (SELECT 1 FROM recurring r WHERE r.direction = 'outflow' AND r.first_seen_at <= :as_of "
             "AND (r.merchant_entity_id = t.merchant_entity_id OR lower(r.merchant_name) = lower(t.merchant_name) OR r.description = t.name) "
             "AND (abs(t.amount - r.avg_amount) <= 0.25 * abs(r.avg_amount) OR abs(t.amount - r.last_amount) <= 0.25 * abs(r.last_amount)))")


def month_to_date(conn: sqlite3.Connection, budget_id: int, as_of: str) -> tuple[float, float]:
    """(spent, the part of it that is recurring charges)."""
    r = conn.execute(
        f"SELECT COALESCE(SUM(amount),0), COALESCE(SUM(CASE WHEN amount > 0 AND {RECURRING} THEN amount END),0) FROM tx_now t "
        "WHERE pending=0 AND date >= date(:as_of, 'start of month') AND date <= date(:as_of) "
        f"AND {SPEND} AND {MATCH}",
        {"as_of": as_of, "bid": budget_id}).fetchone()
    return float(r[0] or 0), float(r[1] or 0)


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
    m = resolve_merchant(conn, merchant)
    for c in m["canonicals"]:
        conn.execute("INSERT INTO category_override (canonical, pfc_primary, pfc_detailed) VALUES (?,?,?) "
                     "ON CONFLICT(canonical) DO UPDATE SET pfc_primary=excluded.pfc_primary, pfc_detailed=excluded.pfc_detailed, created_at=datetime('now','localtime')",
                     (c, taxonomy.primary_of(code), code))
    out = {"merchant": merchant, **_covers(m), "category": code}
    pinned = conn.execute(f"SELECT count(*) FROM tx_category_override x JOIN tx_now t USING (transaction_id) WHERE t.canonical IN ({','.join('?' * len(m['canonicals']))})",
                          m["canonicals"]).fetchone()[0]
    if pinned:   # a one-time edit beats the rule, so those charges keep their own category
        out["one_time_edits_kept"] = pinned
    return out


def category_clear(conn: sqlite3.Connection, merchant: str) -> dict:
    """Delete a merchant's rule so its charges fall back to the bank's category; one-time --tx edits stay."""
    err = None
    try:
        canonicals = resolve_merchant(conn, merchant)["canonicals"]
    except ValueError as e:
        canonicals, err = [], e
    # and the rule's own key, as --rules lists it: a rule written as typed before names were resolved, or whose merchant has no charges left
    keys = [r[0] for r in conn.execute("SELECT canonical FROM category_override WHERE canonical=? COLLATE NOCASE", (merchant.strip(),))]
    if err and not keys and "several merchants" in str(err):
        raise err
    canonicals = list(dict.fromkeys(canonicals + keys))
    marks = ",".join("?" * len(canonicals))
    removed = [x[0] for x in conn.execute(f"SELECT pfc_detailed FROM category_override WHERE canonical IN ({marks})", canonicals)] if canonicals else []
    if removed:
        conn.execute(f"DELETE FROM category_override WHERE canonical IN ({marks})", canonicals)
    return {"merchant": merchant, "canonical": canonicals[0] if canonicals else merchant.strip(), "cleared": bool(removed),
            "removed_category": removed[0] if removed else None,
            **({} if removed else {"note": "no rule for this merchant (see: finnamon category --rules)"})}


def category_rules(conn: sqlite3.Connection) -> dict:
    """Every merchant rule (with the charges it covers today) and every one-time edit."""
    rules = conn.execute(
        "SELECT o.canonical, (SELECT max(display) FROM tx_now t WHERE t.canonical=o.canonical) AS display, o.pfc_detailed AS category, o.created_at, "
        "(SELECT count(*) FROM tx_now t WHERE t.canonical=o.canonical) AS charges FROM category_override o ORDER BY o.canonical").fetchall()
    edits = conn.execute(
        "SELECT x.transaction_id, t.date, t.amount, t.display, x.pfc_detailed AS category, x.created_at FROM tx_category_override x "
        "LEFT JOIN tx_now t USING (transaction_id) ORDER BY x.created_at, x.transaction_id").fetchall()
    return {"rules": [dict(r) for r in rules], "one_time_edits": [dict(r) for r in edits]}


def tx_category_set(conn: sqlite3.Connection, transaction_id: str, category_text: str | None) -> dict:
    """A one-time edit: this one transaction only, ahead of the merchant rule. category_text None clears it."""
    t = conn.execute("SELECT transaction_id, date, amount, display, pending FROM tx_now WHERE transaction_id=?", (transaction_id.strip(),)).fetchone()   # a mirror's id: its twin is the one counted
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
    elif t["pending"]:
        out["note"] = "pending: the edit follows it when the bank posts it, unless the bank posts it as an unrelated new transaction"
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


def resolve_merchant(conn: sqlite3.Connection, text: str) -> dict:
    """Any name the household sees for a merchant (the display name, Plaid's merchant name, the raw bank text, an alias's
    canonical or the alias pattern itself), exact and case-insensitive, to the canonical(s) the detectors key on.
    Several canonicals under one display name (Plaid's entity id on some charges, none on others) are one merchant to the
    household, so all of them; several display names is ambiguous and refused with the candidates; no charge is refused
    with suggestions, so a rule is never written that covers nothing. An entity id (mch_...) is taken as given."""
    t = text.strip()
    if not t:
        raise ValueError("a merchant name is empty")
    if t.startswith("mch_"):
        n = conn.execute("SELECT count(*) FROM tx_now WHERE canonical=?", (t,)).fetchone()[0]
        return {"canonicals": [t], "display": t, "charges": n}
    q = "SELECT canonical, max(display) AS display, count(*) AS n FROM tx_now WHERE {} GROUP BY canonical ORDER BY n DESC, canonical"
    # the name as the household sees it first, so "Shell" is Plaid's Shell even where another merchant's raw text is SHELL
    rows = conn.execute(q.format("lower(?) IN (lower(display), lower(canonical))"), (t,)).fetchall() or conn.execute(q.format(
        "lower(?) IN (lower(merchant_name), lower(name)) OR alias_canonical IN (SELECT canonical FROM merchant_alias WHERE lower(name)=lower(?))"), (t, t)).fetchall()
    if not rows:
        sug = _suggest_merchants(conn, t)
        raise ValueError(f"0 charges match '{t}'" + (f"; did you mean {' or '.join(sug)}?" if sug else "; no merchant by that name")
                         + " (a merchant is named as its charges show it: finnamon query \"SELECT display, count(*) FROM tx_now GROUP BY 1\")")
    if len({r["display"].lower() for r in rows}) > 1:
        raise ValueError(f"'{t}' matches several merchants: " + "; ".join(f"{r['display']} ({r['n']} charges)" for r in rows) + ". Name one of them")
    return {"canonicals": [r["canonical"] for r in rows], "display": rows[0]["display"], "charges": sum(r["n"] for r in rows)}


def _suggest_merchants(conn: sqlite3.Connection, t: str, limit: int = 3) -> list[str]:
    """Display names a partial or misspelled name probably meant: containing it first, then close spellings."""
    rows = conn.execute("SELECT display, lower(display), lower(COALESCE(merchant_name, '')), lower(name), count(*) AS n FROM tx_now "
                        "WHERE display IS NOT NULL GROUP BY 1, 2, 3, 4 ORDER BY n DESC").fetchall()
    low = t.lower()
    hits = [r[0] for r in rows if any(low in h for h in r[1:4])]   # a partial name, or the start of the raw bank text
    spelled = {h: r[0] for r in rows for h in r[1:4] if h}
    hits += [spelled[m] for m in difflib.get_close_matches(low, list(spelled), n=limit, cutoff=0.6)]   # a typo
    return list(dict.fromkeys(hits))[:limit]


def _covers(m: dict) -> dict:
    """What a merchant rule covers, for the reply: the canonical (all of them when there are several) and today's charges."""
    return {"canonical": m["canonicals"][0], **({"canonicals": m["canonicals"]} if len(m["canonicals"]) > 1 else {}), "charges": m["charges"]}


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


def rule_kinds() -> list[str]:
    """The alert kinds a suppression can quiet: the detectors that read the prelude's `suppressed`, and recurring_changed
    (by its stream). A rule for any other kind would never match, so `--kind` refuses it."""
    return sorted({detect.kind_of(f)[1] for f in detect.detectors() if "suppressed" in f.read_text()} | {"anomaly:recurring_changed"})


# "It's normal" on an alert no rule can quiet: it resolves that alert, writes no rule, and says what happens next.
NO_RULE_NOTES = {
    "low_balance": "Resolved for this week; it alerts again next week while {account} stays under ${threshold:,.0f}. "
                   "To alert at a lower balance: finnamon threshold \"{account}\" <amount>.",
    "budget_pace": "Resolved for this month; the {budget} budget alerts again once spending reaches the limit, or next month. "
                   "To change the limit: finnamon budget set \"{budget}\" <amount>.",
    "budget_pace:over": "Resolved for this month; the {budget} budget is over its limit and alerts again next month if it goes over again. "
                        "To change the limit: finnamon budget set \"{budget}\" <amount>.",
    "sync_health": "Resolved for today; it alerts again tomorrow if {institution} still isn't syncing. "
                   "If the bank wants a new login, reconnect it from the dashboard's Accounts.",
}


def _no_rule_note(kind: str, p: dict) -> str:
    try:
        kind = f"{kind}:over" if kind == "budget_pace" and p.get("state") == "over" else kind
        return NO_RULE_NOTES[kind].format(**{k: v for k, v in p.items() if v is not None})
    except (KeyError, ValueError, TypeError):   # an older payload, or a kind with no note of its own
        return "Resolved; no rule applies to this kind of alert, so the next one still alerts."


def normal(conn: sqlite3.Connection, canonical: str | None = None, kind: str | None = None, account: str | None = None,
           max_amount: float | None = None, note: str | None = None, alert_id: int | None = None) -> dict:
    """A structured suppression. With alert_id, defaults are taken from that alert's payload. A rule with no kind
    covers every detector but recurring_changed; that one needs its kind, and from an alert is scoped to its stream.
    A merchant typed by a person goes through resolve_merchant, so a rule always covers charges that exist; an alert of a
    kind no rule can quiet (low balance, budget, sync health) is resolved with no rule and a note on what comes next."""
    stream_id = None
    account_id = find_account(conn, account)["account_id"] if account else None
    if max_amount is not None:
        max_amount = _positive(max_amount, "max amount")
    if kind:
        kinds = rule_kinds()
        kind = kind if kind in kinds else f"anomaly:{kind}" if f"anomaly:{kind}" in kinds else None
        if not kind:
            raise ValueError(f"no alert kind a rule can quiet is called that; the kinds are: {', '.join(kinds)}")
    m = resolve_merchant(conn, canonical) if canonical else None
    canonicals = m["canonicals"] if m else []
    if m and kind == "anomaly:recurring_changed":   # that detector keys on the stream's own name, with no alias step
        t, marks = canonical.strip(), ",".join("?" * len(canonicals))
        canonicals = list(dict.fromkeys(canonicals + [r[0] for r in conn.execute(
            f"SELECT DISTINCT COALESCE(r.merchant_entity_id, r.merchant_name, r.description) FROM recurring r WHERE lower(?) IN (lower(r.merchant_name), lower(r.description)) "
            f"OR r.merchant_entity_id IN ({marks}) OR EXISTS (SELECT 1 FROM tx_now t WHERE t.canonical IN ({marks}) "   # the names behind an alias too
            f"AND lower(COALESCE(r.merchant_name, r.description)) IN (lower(t.merchant_name), lower(t.name)))", (t, *canonicals, *canonicals))]))
    if alert_id and not canonical:
        a = conn.execute("SELECT kind, key, payload_json, transaction_id, account_id, resolved_at FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not a:
            raise ValueError(f"no alert {alert_id}")
        if a["resolved_at"]:   # a second rule would outlive the undo, which removes only the one recorded
            raise ValueError(f"alert {alert_id} is already resolved; `finnamon alerts --undo {alert_id}` first to change how")
        p = json.loads(a["payload_json"])
        if a["kind"] not in rule_kinds() and not kind:
            _resolve(conn, alert_id, "normal", None)   # Undo reopens it like any other
            return {"id": None, "alert_id": alert_id, "kind": a["kind"], "rule": None, "next": _no_rule_note(a["kind"], p)}
        found = None
        if a["transaction_id"]:
            # the same expression the prelude uses, so the suppression matches what the detectors see
            r = conn.execute("SELECT canonical FROM tx_now WHERE transaction_id=?", (a["transaction_id"],)).fetchone()
            found = r[0] if r else None
        found = found or p.get("merchant") or p.get("name")
        kind = kind or a["kind"]
        if kind == "anomaly:recurring_changed" == a["kind"]:   # "I cancelled it" acknowledges this stream, not the merchant
            stream_id = a["key"].removeprefix("anom:rec:").rsplit(":", 1)[0]   # anom:rec:<stream_id>:<YYYY-MM>
            r = conn.execute("SELECT COALESCE(merchant_entity_id, merchant_name, description) FROM recurring WHERE stream_id=?", (stream_id,)).fetchone()
            found = (r and r[0]) or found   # the detector's own expression, so the rule matches what it sees
            account_id = account_id or a["account_id"]
        if kind == "duplicate_charge":   # "normal" means this charge twice is fine, not every duplicate at the merchant
            # ponytail: a cap, so a smaller duplicate there stays muted too; an exact-amount column if that bites
            account_id = account_id or a["account_id"]
            max_amount = max_amount if max_amount is not None else p.get("amount")
        if found and not stream_id and not found.startswith("mch_"):   # a raw string from the payload (its transaction now a mirror's) resolves through the aliases
            found = canonical_for(conn, found)
        canonicals = [found] if found else []
    if not (canonicals or account_id or (kind and max_amount is not None)):
        raise ValueError("a suppression needs a merchant, an account, or a kind with a max amount; an unscoped one would silence every detector")
    if alert_id and len(canonicals) > 1:   # an undo removes the one rule it recorded
        raise ValueError(f"'{canonical}' is several merchant ids ({', '.join(canonicals)}); with --alert, name one of them")
    ids = [conn.execute("INSERT INTO suppressions (kind, canonical, account_id, max_amount, note, stream_id) VALUES (?,?,?,?,?,?)",
                        (kind, c, account_id, max_amount, note, stream_id)).lastrowid for c in (canonicals or [None])]
    if alert_id:   # the rule it wrote is recorded, so an undo removes that one and never another
        _resolve(conn, alert_id, "normal", ids[0])
    return {"id": ids[0], **({"ids": ids} if len(ids) > 1 else {}), "kind": kind, "canonical": canonicals[0] if canonicals else None,
            **({"charges": m["charges"]} if m else {}), "account_id": account_id, "max_amount": max_amount, "note": note, "stream_id": stream_id}


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
