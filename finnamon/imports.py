"""Manual accounts: a bank Plaid can't reach (HSBC US personal banking is one), fed by the CSV a person downloads
from the bank's site.

    imports.add_account(conn, name, institution, kind, owner)   one manual Item per institution, one account per name
    imports.parse(text)                                          rows from any bank's CSV: header or not, one amount column or debit + credit
    imports.apply(conn, account_id, parsed, ...)                 upsert under deterministic ids: importing the same file twice is a no-op

Sign: a bank file shows money out as negative (or in a debit column); the DB is Plaid's convention, positive = out.
`flip` is for a file that does it the other way (some card exports). The caller shows `sample` so a person can tell."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime

from . import store

KINDS = {"checking": ("depository", "checking"), "savings": ("depository", "savings"), "credit": ("credit", "credit card"),
         "loan": ("loan", "loan"), "investment": ("investment", "brokerage")}
MAX_NAME = 80
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d-%b-%Y", "%m-%d-%Y")
# header words per role, in the order roles are claimed: "Transaction Date" is the date, not the description; "Debit Amount" is a debit, not the amount
HEADERS = (("date", ("transaction date", "posting date", "posted date", "date")), ("name", ("description", "memo", "payee", "narrative", "details", "merchant", "name")),
           ("debit", ("debit", "withdrawal", "money out", "paid out")), ("credit", ("credit", "deposit", "money in", "paid in")),
           ("balance", ("balance",)), ("amount", ("amount",)))


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def add_account(conn: sqlite3.Connection, name: str, institution: str, kind: str = "checking", owner: str = "me", mask: str | None = None) -> dict:
    name, institution = " ".join(str(name or "").split()), " ".join(str(institution or "").split())
    if not name or len(name) > MAX_NAME or not institution or len(institution) > MAX_NAME:
        raise ValueError(f"an account needs a name and an institution of 1 to {MAX_NAME} characters")
    if kind not in KINDS:
        raise ValueError(f"--type is one of {', '.join(KINDS)}")
    if conn.execute("SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE", (name,)).fetchone():
        raise ValueError(f"an account named {name} already exists")
    item_id = f"manual:{slug(institution)}"
    account_id = f"{item_id}:{slug(name)}"
    typ, sub = KINDS[kind]
    if conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone():
        raise ValueError(f"the id {account_id} is taken by another account whose name reads the same once punctuation is dropped; pick a name that differs in letters or digits")
    with store.tx(conn):
        conn.execute("INSERT OR IGNORE INTO owners (owner) VALUES (?)", (owner,))
        conn.execute("INSERT INTO items (item_id, institution, owner, source) VALUES (?,?,?,'manual') ON CONFLICT(item_id) DO NOTHING", (item_id, institution, owner))
        conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES (?,?,?,?,?,?,?)",
                     (account_id, item_id, name, typ, sub, (mask or None) and str(mask)[-4:], owner))
    # ON CONFLICT DO NOTHING above: a second account at a bank keeps the institution the first one spelled, so report the
    # row that is actually there rather than the string this call was handed.
    stored = conn.execute("SELECT institution FROM items WHERE item_id=?", (item_id,)).fetchone()[0]
    return {"account_id": account_id, "item_id": item_id, "name": name, "institution": stored, "type": typ, "subtype": sub, "owner": owner}


def lookup_account(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    """Any account by id, else by name (case-insensitive; a manual one first when a Plaid account shares it), with its bank and
    how many transactions it holds."""
    return conn.execute("SELECT a.account_id, a.item_id, a.name, a.type, i.source, i.institution, "
                        "(SELECT count(*) FROM transactions t WHERE t.account_id=a.account_id) AS transactions FROM accounts a JOIN items i ON i.item_id=a.item_id "
                        "WHERE a.account_id=? OR a.name=? COLLATE NOCASE ORDER BY a.account_id=? DESC, i.source='manual' DESC LIMIT 1", (ref, " ".join(str(ref).split()), ref)).fetchone()


def set_type(conn: sqlite3.Connection, ref: str, kind: str | None) -> dict:
    """The household's type for any account (Plaid or manual), by id or name; None restores what Plaid (or `account add`)
    said. Sync keeps an override (sync._UPSERT_ACCOUNT), and type/subtype carry it to every reader."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"the type is one of {', '.join(KINDS)}")
    r = lookup_account(conn, ref)
    if not r:
        raise ValueError(f"no account {ref!r}; `finnamon account list` shows them")
    with store.tx(conn):
        conn.execute("UPDATE accounts SET source_type=COALESCE(source_type, type), source_subtype=COALESCE(source_subtype, subtype) WHERE account_id=?", (r["account_id"],))
        if kind is None:
            conn.execute("UPDATE accounts SET type_override=NULL, type=source_type, subtype=source_subtype WHERE account_id=?", (r["account_id"],))
        else:
            conn.execute("UPDATE accounts SET type_override=?, type=?, subtype=? WHERE account_id=?", (kind, *KINDS[kind], r["account_id"]))
    return dict(conn.execute("SELECT account_id, name, type, subtype, type_override, source_type AS bank_type, source_subtype AS bank_subtype FROM accounts WHERE account_id=?", (r["account_id"],)).fetchone())


def find_account(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """A manual account by id or name (case-insensitive). Imports into a Plaid account would fight the sync."""
    r = lookup_account(conn, ref)
    if not r:
        raise ValueError(f"no account {ref!r}; `finnamon account list` shows them, `finnamon account add` makes a manual one")
    if r["source"] != "manual":
        raise ValueError(f"{r['name']} is synced from Plaid; imports go into a manual account (finnamon account add)")
    return r


# --- parsing ------------------------------------------------------------------------------------

def parse_date(s: str) -> str | None:
    s = (s or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def parse_money(s: str) -> float | None:
    """'1,234.56', '$-12.00', '(12.00)', '12.00 DR' → a float; anything else None. Parentheses, a minus, DR: negative."""
    m = re.fullmatch(r"(\()?(-)?(\d+(?:\.\d+)?|\.\d+)(-)?(\))?(CR|DR)?", re.sub(r"[\s$,]", "", str(s or "")), re.I)
    if not m:
        return None
    v = float(m[3])
    return -v if (m[1] or m[2] or m[4] or (m[6] or "").upper() == "DR") else v


def _columns(header: list[str]) -> dict[str, int]:
    low, cols = [h.strip().lower() for h in header], {}
    for role, keys in HEADERS:
        for k in keys:
            hit = next((i for i, h in enumerate(low) if k in h and i not in cols.values()), None)
            if hit is not None:
                cols[role] = hit
                break
    return cols


def _infer(row: list[str]) -> dict[str, int]:
    """No header: the first date cell is the date, the first money cell the amount, a second money cell the balance,
    the longest other cell the description."""
    # ponytail: a headerless debit/credit pair reads as amount + balance; rows with an empty amount are then skipped and reported. Add a header to the file.
    dates = [i for i, c in enumerate(row) if parse_date(c)]
    monies = [i for i, c in enumerate(row) if i not in dates and parse_money(c) is not None]
    texts = [i for i in range(len(row)) if i not in dates and i not in monies and row[i].strip()]
    if not dates or not monies:
        return {}
    cols = {"date": dates[0], "amount": monies[0]}
    if len(monies) > 1:
        cols["balance"] = monies[-1]
    if texts:
        cols["name"] = max(texts, key=lambda i: len(row[i]))
    return cols


def parse(text: str) -> dict:
    """{'rows': [{date, amount (bank sign), name, balance, raw}], 'skipped': n, 'header': bool, 'columns': {role: header or index}}.
    Errors never quote the file: a path handed to `finnamon import` may not be a CSV at all."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("the file is empty")
    header = not any(parse_date(c) for c in rows[0])
    cols = _columns(rows[0]) if header else _infer(rows[0])
    if "date" not in cols or not ({"amount", "debit", "credit"} & cols.keys()):
        raise ValueError(f"could not find a date and an amount column ({'header' if header else 'first'} row has {len(rows[0])} cells); "
                         "expected a bank's CSV export with date, description, amount (or debit and credit) columns")
    out, skipped = [], 0
    for r in rows[1:] if header else rows:
        cell = lambda role: r[cols[role]].strip() if role in cols and cols[role] < len(r) else ""   # noqa: E731
        date = parse_date(cell("date"))
        if "amount" in cols:
            amt = parse_money(cell("amount"))
        else:
            d, c = parse_money(cell("debit")), parse_money(cell("credit"))
            amt = None if d is None and c is None else (c or 0) - abs(d or 0)   # a debit column holds money out, whatever its sign
        if date is None or amt is None:
            skipped += 1
            continue
        out.append({"date": date, "amount": amt, "name": " ".join(cell("name").split()) or "(no description)", "balance": parse_money(cell("balance")), "raw": r})
    return {"rows": out, "skipped": skipped, "header": header, "columns": {k: (rows[0][v].strip() if header else v) for k, v in cols.items()}}


# --- writing ------------------------------------------------------------------------------------

def apply(conn: sqlite3.Connection, account_id: str, parsed: dict, flip: bool = False, balance: float | None = None,
          now: str | None = None, dry_run: bool = False) -> dict:
    now = now or store.now_local()
    acct = conn.execute("SELECT a.item_id, a.type, i.source FROM accounts a JOIN items i ON i.item_id=a.item_id WHERE a.account_id=?", (account_id,)).fetchone()
    if not acct or acct["source"] != "manual":
        raise ValueError("imports go into a manual account (finnamon account add)")
    seen: dict[tuple, int] = {}
    recs = []
    for r in parsed["rows"]:
        amt = round(r["amount"] if flip else -r["amount"], 2)
        key = (r["date"], amt, r["name"])
        seen[key] = n = seen.get(key, 0) + 1   # the n-th identical row of a file (two coffees) is its own transaction; the same file again is not
        tid = "import:" + hashlib.sha1(f"{account_id}|{r['date']}|{amt:.2f}|{r['name']}|{n}".encode()).hexdigest()[:20]
        recs.append((tid, r["date"], amt, r["name"], json.dumps(r["raw"])))
    have = {x[0] for x in conn.execute("SELECT transaction_id FROM transactions WHERE account_id=?", (account_id,))}
    new = [x for x in recs if x[0] not in have]
    dates = sorted(r["date"] for r in parsed["rows"])
    if balance is None:
        with_bal = [r for r in parsed["rows"] if r["balance"] is not None]
        if with_bal:   # the row with the latest date; on a tie the file's order decides (an export lists newest first or last)
            latest = max(r["date"] for r in with_bal)
            tied = [r for r in with_bal if r["date"] == latest]
            balance = (tied[0] if parsed["rows"][0]["date"] >= parsed["rows"][-1]["date"] else tied[-1])["balance"]
    if balance is not None and acct["type"] in ("credit", "loan"):
        balance = abs(balance)   # Plaid reports what is owed as a positive number
    summary = {"account_id": account_id, "rows": len(recs), "added": len(new), "already": len(recs) - len(new), "skipped": parsed["skipped"],
               "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
               "money_out": round(sum(x[2] for x in recs if x[2] > 0), 2), "money_in": round(-sum(x[2] for x in recs if x[2] < 0), 2),
               "balance": balance, "columns": parsed["columns"], "sample": [{"date": d, "amount": a, "name": nm} for _, d, a, nm, _ in recs[:3]], "dry_run": dry_run}
    if dry_run:
        return summary
    with store.tx(conn):
        for tid, date, amt, name, raw in new:
            pfc = conn.execute("SELECT pfc_primary, pfc_detailed FROM transactions WHERE name=? AND pfc_primary IS NOT NULL LIMIT 1", (name,)).fetchone()   # the same merchant on a Plaid account already has a category
            conn.execute("INSERT OR IGNORE INTO transactions (transaction_id, account_id, date, amount, name, pfc_primary, pfc_detailed, pfc_confidence, pending, raw_json) VALUES (?,?,?,?,?,?,?,?,0,?)",   # two imports of one file at once: the second is a no-op, not a constraint error
                         (tid, account_id, date, amt, name, pfc[0] if pfc else None, pfc[1] if pfc else None, "LOW" if pfc else None, raw))
        if balance is not None:
            conn.execute("INSERT OR REPLACE INTO balances (account_id, as_of, current, available) VALUES (?,?,?,?)",
                         (account_id, now, balance, balance if acct["type"] == "depository" else None))
        if recs:   # a file with no readable row is not an import: the "nothing imported since" nag stays right
            conn.execute("UPDATE items SET last_synced_at=?, first_synced_at=COALESCE(first_synced_at, ?), status='good', last_error=NULL WHERE item_id=?", (now, now, acct["item_id"]))
    return summary
