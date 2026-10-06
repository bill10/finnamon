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
    raise ResolveError(text, cands or taxonomy.suggestions(text))


def detailed_category(text: str, what: str) -> str:
    """The one resolution for a merchant rule and a one-charge edit: a detailed category. A group ("Travel") is refused with its
    members named, because picking one for the household would file charges where nobody chose."""
    code = resolve_category(text)
    if code in taxonomy.DETAILED:
        opts = ", ".join(f'"{taxonomy.label(f"{code}_{d}")}"' for d in taxonomy.DETAILED[code])
        raise ValueError(f"{what} needs a detailed category, and '{text}' is a group: say which, {opts}. "
                         "(A budget can take the whole group: finnamon budget set <name> <amount> --category " + f'"{taxonomy.label(code)}")')
    return code


def _budget_codes(text: str, from_name: bool) -> list[str]:
    """A budget's category text to its codes: one category, or a name that is several ("utilities"). A budget's own name
    that is no category is refused here, with what it might mean, rather than make a budget that counts nothing."""
    code, cands = taxonomy.resolve(text)
    if code:
        return [code]
    if (group := taxonomy.GROUPS.get(text.strip().lower())):
        return group
    if not from_name:
        raise ResolveError(text, cands or taxonomy.suggestions(text))
    sug = cands or taxonomy.suggestions(text)
    raise ValueError(f"'{text}' is not a category, so a budget by that name would count nothing. Say what it counts: --category <category> "
                     "(repeat it for several" + (f"; maybe {' or '.join(_cat_name(c) for c in sug[:6])}{', …' if len(sug) > 6 else ''}" if sug else "") + ") or --merchant <name> "
                     "(each merchant, e.g. for subscriptions or car insurance); `finnamon category list` has every category")


def _cat_name(code: str) -> str:
    """A category as a person can type it back: its label, or the code when that label is shared ("Savings")."""
    lab = taxonomy.label(code)
    return f'"{lab}"' if taxonomy.resolve(lab)[0] == code else code


# --- budgets ---------------------------------------------------------------------------------

def _positive(amount, what: str) -> float:
    try:
        v = store.parse_amount(amount, what)
    except ValueError:
        raise ValueError(f"{what} must be a number of dollars, like 1200 or $1,200") from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{what} must be a positive amount")
    return v


def budget_set(conn: sqlite3.Connection, name: str, amount: float, categories: list[str] | str | None = None,
               merchants: list[str] | None = None, fixed: bool | None = None) -> dict:
    """Categories and merchants given replace the budget's selectors; none given keeps an existing budget's (the
    dashboard's limit edit, or re-adding a removed one) and a new one takes its name as the category. fixed None keeps
    what it was, except that a removed budget comes back not fixed. A merchant is any name the household sees for it
    (resolve_merchant: all its canonicals; a name with no charge is refused with suggestions); the reply carries how many
    past spending charges each one matches."""
    name = name.strip().lower()
    amount = _positive(amount, "monthly limit")
    categories = [categories] if isinstance(categories, str) else list(categories or [])
    merchants = list({m.strip().lower(): m.strip() for m in merchants or []}.values())   # "Recology" and "recology" are one
    with store.tx(conn):   # one write, read and all: a crash or a concurrent edit mid-way would leave a budget counting nothing
        old = conn.execute("SELECT id FROM budgets WHERE name=?", (name,)).fetchone()
        keep = not (categories or merchants) and old and conn.execute("SELECT 1 FROM budget_selectors WHERE budget_id=?", (old[0],)).fetchone()
        texts = [] if keep else categories or ([] if merchants else [name])
        read = {t: _budget_codes(t, not categories) for t in texts}
        codes = list(dict.fromkeys(c for cs in read.values() for c in cs))
        sel = [("category", c, None) for c in codes] + [("merchant", c, m) for m in merchants for c in resolve_merchant(conn, m)["canonicals"]]
        conn.execute(
            "INSERT INTO budgets (name, category, monthly_limit, active, fixed) VALUES (?,?,?,1,?) "
            "ON CONFLICT(name) DO UPDATE SET category=CASE WHEN ? THEN category ELSE excluded.category END, monthly_limit=excluded.monthly_limit, "
            "fixed=CASE WHEN active=1 THEN COALESCE(?, fixed) ELSE excluded.fixed END, active=1",
            (name, codes[0] if codes else "", amount, int(bool(fixed)), bool(keep), None if fixed is None else int(fixed)))
        bid = conn.execute("SELECT id FROM budgets WHERE name=?", (name,)).fetchone()[0]
        if not keep:
            conn.execute("DELETE FROM budget_selectors WHERE budget_id=?", (bid,))
            conn.executemany("INSERT OR IGNORE INTO budget_selectors (budget_id, kind, value, label) VALUES (?,?,?,?)", [(bid, *s) for s in sel])
    out = next(b for b in _budgets(conn, "WHERE id=?", (bid,)))
    # what it counts, in words, every time: a name read as a category ("utilities", "dining") is said to be a guess
    out["counts"] = [taxonomy.describe(c) for c in out["categories"]] + [f"merchant {m}" for m in out["merchants"]]
    guesses = [f"'{t}' was read as {', '.join(taxonomy.describe(c) for c in cs)}" for t, cs in read.items()
               if len(cs) > 1 or taxonomy.guessed(t, cs[0])]
    if guesses:
        out["guessed"] = "; ".join(guesses) + (" (from the budget's name)" if not categories else "") + \
            ". If that is not what it should count, pick its categories or merchants (--category, repeatable, or --merchant)"
    if not keep:   # another budget counting the same charges: both would alert on them
        both = MATCH.replace(":bid", ":other")
        mine = {(s["kind"], s["value"]) for s in conn.execute("SELECT kind, value FROM budget_selectors WHERE budget_id=?", (bid,))}
        wide = mine | {("category", taxonomy.primary_of(v)) for k, v in mine if k == "category"}   # groceries is inside a food-and-drink budget
        overlaps = []
        for o in conn.execute("SELECT id, name FROM budgets WHERE active=1 AND id<>? ORDER BY name", (bid,)).fetchall():
            theirs = {(s["kind"], s["value"]) for s in conn.execute("SELECT kind, value FROM budget_selectors WHERE budget_id=?", (o["id"],))}
            n = conn.execute(f"SELECT count(*) FROM tx_now t WHERE pending=0 AND {SPEND} AND {MATCH} AND {both}", {"bid": bid, "other": o["id"]}).fetchone()[0]
            if n or wide & theirs or mine & {("category", taxonomy.primary_of(v)) for k, v in theirs if k == "category"}:   # past charges in both, or the same selector before any charge
                overlaps.append({"budget": o["name"], "charges": n})
        if overlaps:
            out["overlaps"] = overlaps
            out["warning"] = ("these charges are also in " + ", ".join(f"the {o['budget']} budget ({o['charges']} past charges)" for o in overlaps)
                              + ": each budget counts them and alerts on them; narrow one's categories or merchants (--category, --merchant) if they should count once")
    if merchants:
        one = MERCHANT.replace(":v", "s.value").replace(":l", "s.label")
        out["matches"] = {m: conn.execute(f"SELECT count(*) FROM tx_now t WHERE pending=0 AND {SPEND} AND EXISTS (SELECT 1 FROM budget_selectors s "
                                          f"WHERE s.budget_id=:bid AND s.kind='merchant' AND s.label=:l AND {one})", {"bid": bid, "l": m}).fetchone()[0]
                          for m in merchants}
    return out


def budget_remove(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("UPDATE budgets SET active=0 WHERE name=?", (name.strip().lower(),)).rowcount > 0


def _budgets(conn: sqlite3.Connection, where: str, args: tuple = ()) -> list[dict]:
    out = []
    for b in conn.execute(f"SELECT id, name, category, monthly_limit, fixed FROM budgets {where} ORDER BY name", args).fetchall():
        sel = conn.execute("SELECT kind, value, label FROM budget_selectors WHERE budget_id=? ORDER BY rowid", (b["id"],)).fetchall()
        cats = [s["value"] for s in sel if s["kind"] == "category"]
        merchants = list(dict.fromkeys(s["label"] or s["value"] for s in sel if s["kind"] == "merchant"))   # one name, maybe several canonicals
        out.append({"id": b["id"], "name": b["name"], "category": cats[0] if cats else None,   # the first, as before selectors
                    "categories": cats, "merchants": merchants, "covers": [taxonomy.label(c) for c in cats] + merchants,   # covers: the dashboard's line
                    "fixed": bool(b["fixed"]), "monthly_limit": b["monthly_limit"]})
    return out


def budget_list(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    as_of = as_of or store.now_local()
    out = []
    for b in _budgets(conn, "WHERE active=1"):
        day = int(as_of[8:10])
        days = conn.execute("SELECT CAST(strftime('%d', date(?, 'start of month', '+1 month', '-1 day')) AS INTEGER)", (as_of,)).fetchone()[0]
        mtd, rec = month_to_date(conn, b["id"], as_of)
        pace = mtd if b["fixed"] else max(mtd, rec + (mtd - rec) * days / day)   # a refund never projects below what is spent
        out.append({**b, "spent": round(mtd, 2), "recurring": round(rec, 2), "day": day, "pace": round(pace, 2)})
    return out


# tx_now (migration 005): transactions with the alias and the category override resolved, mirrors excluded --
# the prelude's `tx` without the :as_of scoping. Everything here reads categories and merchants through it.
# tx_now's flow (migration 008): not a transfer between the household's own accounts, not a payment onto a linked card,
# not money in. The mortgage counts, once: its checking-side payment is 'mortgage', the loan account's mirror 'skipped'.
SPEND = "flow IN ('expense','refund','mortgage')"
# KEEP IN STEP WITH detectors/rules/budget_pace.sql. A budget counts a transaction any of its selectors matches, once.
# A payment paired with the mortgage loan is a mortgage whatever Plaid filed it under (often a transfer), and only a mortgage. A merchant is
# a canonical resolve_merchant found or the name as typed, either one case-insensitively against canonical or `display`: an
# alias added later (the raw bank name stays a candidate), or a charge Plaid sent without the entity id, still matches.
MERCHANT = "(lower(:v) IN (lower(t.canonical), lower(t.display), lower(t.name)) OR lower(:l) IN (lower(t.canonical), lower(t.display), lower(t.name)))"
MATCH = ("EXISTS (SELECT 1 FROM budget_selectors s WHERE s.budget_id = :bid AND ((s.kind = 'category' AND ((t.flow <> 'mortgage' AND s.value IN (t.category, t.category_primary)) "
         "OR (t.flow = 'mortgage' AND s.value IN ('LOAN_PAYMENTS_MORTGAGE_PAYMENT', 'LOAN_PAYMENTS')))) "
         "OR (s.kind = 'merchant' AND " + MERCHANT.replace(":v", "s.value").replace(":l", "COALESCE(s.label, s.value)") + ")))")
# The mortgage (often filed as a transfer, with no stream to match), or a charge of one of Plaid's live recurring
# streams (a bill, a subscription), or its refund: pace counts it once, never
# projects it. Monthly and annual streams only: one that recurs within the month (weekly, semi-monthly) or irregularly
# (Plaid's UNKNOWN, a restaurant) stays projected. Same merchant (a bare description: on the stream's account) and within 25%
# (the tolerance, four times here and in budget_pace.sql) of the stream's amount, so a one-off at a merchant that also
# bills monthly stays variable. Not scoped to :as_of: a run takes as_of before its sync stamps a new stream's first_seen_at.
# ponytail: merchant + amount, not Plaid's transaction_ids per stream (not stored); store them if this misfiles.
RECURRING = ("(t.flow = 'mortgage' OR EXISTS (SELECT 1 FROM recurring r WHERE r.direction = 'outflow' AND COALESCE(r.is_active, 1) = 1 "
             "AND COALESCE(r.status, '') NOT IN ('TOMBSTONED', 'EARLY_DETECTION') AND r.frequency IN ('MONTHLY', 'ANNUALLY') "
             "AND (r.merchant_entity_id = t.merchant_entity_id OR lower(r.merchant_name) = lower(t.merchant_name) OR (r.description = t.name AND r.account_id = t.account_id)) "
             "AND (abs(abs(t.amount) - r.avg_amount) <= 0.25 * abs(r.avg_amount) OR abs(abs(t.amount) - r.last_amount) <= 0.25 * abs(r.last_amount))))")


# Any active budget's selectors: the Overall line counts a charge two budgets share once. Built from MATCH's own text, so
# a reworded MATCH must keep this scope clause (the assert says so at import, not as a silently double-counting Overall).
assert "s.budget_id = :bid" in MATCH
ANY_BUDGET = MATCH.replace("s.budget_id = :bid", "s.budget_id IN (SELECT id FROM budgets WHERE active = 1)")
UNCATEGORIZED = "category IS NULL AND pending = 0 AND flow IN ('expense', 'refund', 'income')"   # not a paired transfer; an imported transfer-in reads as income until categorized   # the Budgets card's count and charts' uncategorized table: one rule
UNCATEGORIZED_SHOWN = 20   # merchants, and ids per merchant, in a reply; the count is always the whole


def overall(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    """The dashboard's Overall: this month's spending any budget counts, each charge once; the limits' sum; and how many
    transactions have no category at all (an import's rows: they count toward no budget until someone categorizes them)."""
    as_of = as_of or store.now_local()
    spent = conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM tx_now t WHERE pending=0 AND date >= date(:as_of, 'start of month') AND date <= date(:as_of) "
                         f"AND {SPEND} AND {ANY_BUDGET}", {"as_of": as_of}).fetchone()[0]
    limit = conn.execute("SELECT COALESCE(SUM(monthly_limit),0) FROM budgets WHERE active=1").fetchone()[0]
    n = conn.execute(f"SELECT count(*) FROM tx_now WHERE {UNCATEGORIZED}").fetchone()[0]
    return {"spent": round(float(spent), 2), "limit": round(float(limit), 2), "uncategorized": n}


def uncategorized(conn: sqlite3.Connection, transaction_ids: list[str] | None = None) -> dict:
    """Transactions with no category, by merchant (one rule each covers them all) with their ids (for one charge at a time)."""
    if transaction_ids == []:
        return {"uncategorized": 0, "merchants": []}
    # the ids as one JSON parameter: an import of years of rows never meets SQLite's limit on bound variables
    where, args = (UNCATEGORIZED, ()) if transaction_ids is None else (UNCATEGORIZED + " AND transaction_id IN (SELECT value FROM json_each(?))", (json.dumps(transaction_ids),))
    rows = conn.execute(f"SELECT display, count(*) n, round(sum(amount), 2) total, group_concat(transaction_id) ids FROM tx_now WHERE {where} "
                        "GROUP BY lower(display) ORDER BY n DESC, display", args).fetchall()   # one merchant as people see it, as resolve_merchant has it
    return {"uncategorized": sum(r["n"] for r in rows),
            "merchants": [{"merchant": r["display"], "charges": r["n"], "total": r["total"], "transaction_ids": r["ids"].split(",")[:UNCATEGORIZED_SHOWN]}
                          for r in rows][:UNCATEGORIZED_SHOWN]}


def month_to_date(conn: sqlite3.Connection, budget_id: int, as_of: str) -> tuple[float, float]:
    """(spent, the part of it that is recurring charges)."""
    r = conn.execute(
        f"SELECT COALESCE(SUM(amount),0), COALESCE(SUM(CASE WHEN {RECURRING} THEN amount END),0) FROM tx_now t "
        "WHERE pending=0 AND date >= date(:as_of, 'start of month') AND date <= date(:as_of) "
        f"AND {SPEND} AND {MATCH}",
        {"as_of": as_of, "bid": budget_id}).fetchone()
    return float(r[0] or 0), float(r[1] or 0)


def monthly_spent(conn: sqlite3.Connection, budget_id: int, months: int, as_of: str) -> list[tuple[str, float, float]]:
    """[("2026-05", spent, limit), ...] for the `months` calendar months ending with as_of's, oldest first, each by month_to_date's rules;
    the last is month to date, so it is partial. limit is the one in effect at the month's end (budget_limit_history); a month
    before the budget's first recorded limit takes that first one."""
    rows = dict(conn.execute(
        f"SELECT strftime('%Y-%m', date), SUM(amount) FROM tx_now t WHERE pending=0 AND date >= date(:as_of, 'start of month', :back) AND date <= date(:as_of) "
        f"AND {SPEND} AND {MATCH} GROUP BY 1", {"as_of": as_of, "bid": budget_id, "back": f"-{months - 1} months"}).fetchall())
    hist = "SELECT monthly_limit FROM budget_limit_history WHERE budget_id = :bid"
    yms = conn.execute("WITH RECURSIVE m(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM m WHERE i < :n), "
                       "y(ym) AS (SELECT strftime('%Y-%m', date(:as_of, 'start of month', '-'||(:n - i)||' months')) FROM m) "
                       f"SELECT ym, COALESCE(({hist} AND effective_from < date(ym||'-01', '+1 month') ORDER BY effective_from DESC, rowid DESC LIMIT 1), "
                       f"({hist} ORDER BY effective_from, rowid LIMIT 1), (SELECT monthly_limit FROM budgets WHERE id = :bid)) FROM y",
                       {"as_of": as_of, "n": months - 1, "bid": budget_id}).fetchall()
    return [(ym, round(float(rows.get(ym) or 0), 2), float(lim)) for ym, lim in yms]


# A payment paired with the mortgage loan is the mortgage in suggest, as MATCH counts it, whatever Plaid filed it under.
SUGGEST_CAT = "CASE WHEN flow = 'mortgage' THEN 'LOAN_PAYMENTS_MORTGAGE_PAYMENT' ELSE category END"
SUGGEST_PRIM = "CASE WHEN flow = 'mortgage' THEN 'LOAN_PAYMENTS' ELSE category_primary END"


def suggest(conn: sqlite3.Connection, months: int = 6, as_of: str | None = None) -> dict:
    """The numbers Claude reasons over in Step 3: per-category monthly spend, spread, top merchants, fixed vs variable."""
    as_of = as_of or store.now_local()
    rows = conn.execute(
        f"SELECT strftime('%Y-%m', date) ym, {SUGGEST_CAT} cat, {SUGGEST_PRIM} prim, round(sum(amount),2) spent "
        "FROM tx_now WHERE pending=0 AND amount>0 AND date >= date(?,'start of month', ?) AND date < date(?,'start of month') "
        f"AND {SPEND} GROUP BY ym, cat ORDER BY cat, ym", (as_of, f"-{months} months", as_of)).fetchall()
    by_cat: dict[str, dict] = {}
    for r in rows:
        c = by_cat.setdefault(r["cat"] or "UNCATEGORIZED", {"category": r["cat"], "primary": r["prim"], "months": {}})
        c["months"][r["ym"]] = r["spent"]
    y, mo = int(as_of[:4]), int(as_of[5:7])
    window = []
    for i in range(1, months + 1):   # the complete months the query covers, newest first
        yy, mm = divmod(y * 12 + mo - 1 - i, 12)
        window.append(f"{yy:04d}-{mm + 1:02d}")
    out = []
    for c in by_cat.values():
        vals = sorted(c["months"].get(m, 0) for m in window)   # a month with no spending is a zero: one $642 flight in six months is a $0 median, not $642
        n, k = len(c["months"]), len(vals)
        med = vals[k // 2] if k % 2 else (vals[k // 2 - 1] + vals[k // 2]) / 2
        top = conn.execute(   # the same relation and the same category as the totals above, or a category's total would list merchants that do not add up to it
            "SELECT display m, round(sum(amount)) s FROM tx_now "
            f"WHERE pending=0 AND amount>0 AND {SPEND} AND {SUGGEST_CAT} = ? AND date >= date(?,'start of month', ?) AND date < date(?,'start of month') GROUP BY m ORDER BY s DESC LIMIT 3",
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
    if not rows:   # the name the person says ("Chase Checking") is often not the bank's ("Total Checking"): the closest, or every one
        every = conn.execute("SELECT name, mask, type FROM accounts WHERE mirror_of IS NULL ORDER BY name").fetchall()
        words = set(t.lower().split())
        near = sorted(every, key=lambda r: (-len(words & set(r["name"].lower().split())), -difflib.SequenceMatcher(None, t.lower(), r["name"].lower()).ratio()))
        raise ValueError(f"'{text}' matches no account; " + ("the closest: " + ", ".join(f"\"{r['name']}\" …{r['mask']} ({r['type']})" for r in near[:5])
                                                           + ". Name one of them, or its last four digits" if near else "there are no accounts yet"))
    if len(rows) != 1:
        raise ValueError(f"'{text}' matches {len(rows)} accounts: " + ", ".join(f"{r['name']} …{r['mask']}" for r in rows) + ". Name one of them, or its last four digits")
    return rows[0]


def threshold_set(conn: sqlite3.Connection, account_text: str, amount: float) -> dict:
    a = find_account(conn, account_text)
    if a["type"] != "depository":   # low_balance.sql reads depository balances only: a card's or a brokerage's line could never fire
        raise ValueError(f"{a['name']} …{a['mask']} is a {a['type']} account; a low-balance alert watches the cash in a checking or savings "
                         "account, so a threshold here would never fire. Name a checking or savings account")
    amount = store.parse_amount(amount, "a threshold")
    store.set_setting(conn, "low_balance_threshold", amount, a["account_id"])
    return {"account": a["name"], "mask": a["mask"], "threshold": amount}


def category_set(conn: sqlite3.Connection, merchant: str, category_text: str, m: dict | None = None) -> dict:
    """A merchant rule; m is the merchant already resolved (from a transaction: its own canonical, never a name lookup)."""
    code = detailed_category(category_text, "a merchant override")
    m = m or resolve_merchant(conn, merchant)
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
        code = detailed_category(category_text, "a transaction override")
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


def tx_merchant_rule(conn: sqlite3.Connection, transaction_id: str, category_text: str) -> dict:
    """"Every charge from this merchant", picked from one transaction (the dashboard's row): the rule on that charge's own
    merchant, and that charge's one-time edit, if it had one, goes so the charge picked follows the rule too."""
    t = conn.execute("SELECT transaction_id, canonical, display FROM tx_now WHERE transaction_id=?", (transaction_id.strip(),)).fetchone()
    if not t:
        raise ValueError(f"no transaction '{transaction_id}' (ids are tx_now.transaction_id)")
    n = conn.execute("SELECT count(*) FROM tx_now WHERE canonical=?", (t["canonical"],)).fetchone()[0]
    with store.tx(conn):   # a refused category leaves the edit where it was
        conn.execute("DELETE FROM tx_category_override WHERE transaction_id=?", (t["transaction_id"],))
        out = category_set(conn, t["display"], category_text, {"canonicals": [t["canonical"]], "display": t["display"], "charges": n})
    return {**out, "transaction_id": t["transaction_id"]}


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
    n, distinct = _alias_matches(conn, raw_name)
    if not n:   # a typo would sit there matching nothing, with no way to see it
        sug = _suggest_raw(conn, raw_name.replace("%", "").replace("_", " "))
        raise ValueError(f"'{raw_name}' matches no transaction's bank text" + (f"; did you mean {' or '.join(repr(x) for x in sug)}?" if sug else "")
                         + " (an alias names the raw bank text exactly, or a pattern of it with %: finnamon query \"SELECT DISTINCT name FROM transactions WHERE name LIKE '%word%'\")")
    conn.execute("INSERT INTO merchant_alias (name, canonical) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET canonical=excluded.canonical, created_at=datetime('now','localtime')",
                 (raw_name, canonical.strip()))
    return {"name": raw_name, "canonical": canonical.strip(), "matches": n, "distinct_names": distinct}


def _alias_matches(conn: sqlite3.Connection, name: str) -> tuple[int, int]:
    """(transactions, distinct raw names) an alias name covers: the exact text, or a pattern's LIKE."""
    return tuple(conn.execute("SELECT count(*), count(DISTINCT name) FROM transactions WHERE name = ? OR (instr(?, '%') > 0 AND name LIKE ?)",
                              (name, name, name)).fetchone())


def _suggest_raw(conn: sqlite3.Connection, t: str, limit: int = 3) -> list[str]:
    """Raw bank texts a mistyped alias probably meant: containing it first, then close spellings."""
    names = [r[0] for r in conn.execute("SELECT name FROM transactions WHERE name IS NOT NULL GROUP BY name ORDER BY count(*) DESC")]
    low = t.lower().strip()
    hits = [x for x in names if low and low in x.lower()]
    by_low = {x.lower(): x for x in names}
    hits += [by_low[m] for m in difflib.get_close_matches(low, list(by_low), n=limit, cutoff=0.6)]
    return list(dict.fromkeys(hits))[:limit]


def alias_list(conn: sqlite3.Connection) -> list[dict]:
    """Every alias with the transactions it covers today: a pattern that grew too broad, or one that matches nothing, shows here."""
    out = []
    for r in conn.execute("SELECT name, canonical, created_at FROM merchant_alias ORDER BY canonical, name").fetchall():
        n, distinct = _alias_matches(conn, r["name"])
        out.append({"name": r["name"], "canonical": r["canonical"], "pattern": "%" in r["name"], "matches": n, "distinct_names": distinct, "created_at": r["created_at"]})
    return out


def alias_remove(conn: sqlite3.Connection, name: str) -> dict:
    """Delete one alias by its name (the raw text or pattern, as `alias --list` shows it); its charges go back to their own names."""
    r = conn.execute("SELECT name, canonical FROM merchant_alias WHERE name=?", (name.strip(),)).fetchone() or \
        conn.execute("SELECT name, canonical FROM merchant_alias WHERE name=? COLLATE NOCASE", (name.strip(),)).fetchone()
    if not r:
        names = [x[0] for x in conn.execute("SELECT name FROM merchant_alias WHERE canonical=? COLLATE NOCASE", (name.strip(),))]
        raise ValueError(f"no alias named '{name.strip()}'" + (f"; '{name.strip()}' is the merchant of {', '.join(repr(x) for x in names)}: remove those by name" if names
                                                              else " (finnamon alias --list shows them)"))
    # What the alias decided for its charges, before and after: a budget that stops counting them, a category rule that
    # stops applying, a "normal" rule keyed on the old name. Measured on the charges themselves, whatever other alias,
    # pattern or Plaid entity id ends up naming them.
    ids = [x[0] for x in conn.execute("SELECT transaction_id FROM transactions WHERE name = ? OR (instr(?, '%') > 0 AND name LIKE ?)", (r["name"],) * 3)]
    def state() -> tuple[dict, dict]:
        marks = "SELECT value FROM json_each(:ids)"
        rows = {x[0]: (x[1], x[2], x[3]) for x in conn.execute(f"SELECT transaction_id, canonical, category, account_id FROM tx_now WHERE transaction_id IN ({marks})", {"ids": json.dumps(ids)})}
        counted = {b[1]: {x[0] for x in conn.execute(f"SELECT transaction_id FROM tx_now t WHERE transaction_id IN ({marks}) AND pending=0 AND {SPEND} AND {MATCH}",
                                                       {"ids": json.dumps(ids), "bid": b[0]})} for b in conn.execute("SELECT id, name FROM budgets WHERE active=1")}
        return rows, counted
    with store.tx(conn):
        before, counted_before = state()
        conn.execute("DELETE FROM merchant_alias WHERE name=?", (r["name"],))
        after, counted_after = state()
    out = {"removed": {"name": r["name"], "canonical": r["canonical"]}, "matches": len(ids)}
    # a "normal" rule the way the prelude applies it: that exact canonical, that account or any; never a subscription's (keyed on its stream)
    renamed = [{"c": before[t][0], "a": before[t][2]} for t in before if t in after and after[t][0] != before[t][0]]
    keyed = {k: v for k, v in (
        ("budgets", sorted(n for n, got in counted_before.items() if got - counted_after.get(n, set()))),
        ("charges_recategorized", sum(1 for t in before if t in after and after[t][1] != before[t][1])),
        ("suppressions", conn.execute("SELECT count(DISTINCT s.id) FROM suppressions s JOIN json_each(?) r ON s.canonical = json_extract(r.value, '$.c') "
                                      "AND (s.account_id IS NULL OR s.account_id = json_extract(r.value, '$.a')) AND COALESCE(s.kind, '') <> 'anomaly:recurring_changed'",
                                      (json.dumps(renamed),)).fetchone()[0])) if v}
    if keyed:
        out["still_keyed_on_it"] = keyed
        out["warning"] = (f"'{r['canonical']}' no longer names those charges, so what was set up under that name stops covering them: "
                          + "; ".join(f"{k.replace('_', ' ')}: {', '.join(v) if isinstance(v, list) else v}" for k, v in keyed.items())
                          + ". Set them again under the charges' own name, or add the alias back")
    return out


def rule_kinds() -> list[str]:
    """The alert kinds a suppression can quiet: the detectors that read the prelude's `suppressed`, and recurring_changed and
    recurring_price (by their stream). A rule for any other kind would never match, so `--kind` refuses it."""
    return sorted({detect.kind_of(f)[1] for f in detect.detectors() if "suppressed" in f.read_text()} | {"anomaly:recurring_changed", "recurring_price"})


# "It's normal" on an alert no rule can quiet: it resolves that alert, writes no rule, and says what happens next.
NO_RULE_NOTES = {
    "low_balance": "Resolved for this dip; it alerts again once {account} has gone back above ${threshold:,.0f} and drops under it again. "
                   "To alert at a lower balance: finnamon threshold \"{account}\" <amount>.",
    "budget_pace": "Resolved for this month; the {budget} budget alerts again once spending reaches the limit, or next month. "
                   "To change the limit: finnamon budget set \"{budget}\" <amount>.",
    "budget_pace:over": "Resolved for this month; the {budget} budget is over its limit and alerts again next month if it goes over again. "
                        "To change the limit: finnamon budget set \"{budget}\" <amount>.",
    "sync_health": "Resolved; it alerts again if {institution}'s problem changes, or if it syncs and then breaks again. Run finnamon doctor to see why it "
                   "isn't syncing; if the bank wants a new login, reconnect it from the dashboard's Accounts.",
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
        if kind == "recurring_price" == a["kind"]:   # "that's expected" is this subscription's new price, not every price at the merchant
            stream_id = p.get("stream_id")
            account_id = account_id or a["account_id"]
            max_amount = max_amount if max_amount is not None else p.get("amount")   # a later rise above the accepted price still alerts
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
