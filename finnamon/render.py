"""Readable terminal output for the commands people type. Each takes what the command's JSON holds and returns text; the CLI
prints it only on a terminal and without --json, so the assistant, the dashboard and any pipe keep getting JSON."""
from __future__ import annotations

import html
import re

from .notify import day, money


def plain(text) -> str:
    """Telegram HTML to plain text."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or "")))


def table(headers: list[str], rows: list[list], right: tuple[int, ...] = ()) -> str:
    cells = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    fmt = lambda r: "  ".join((c.rjust(w[i]) if i in right else c.ljust(w[i])) for i, c in enumerate(r)).rstrip()   # noqa: E731
    return "\n".join([fmt(headers), *(fmt(r) for r in cells)])


def alerts(rows: list[dict]) -> str:
    if not rows:
        return "No alerts in that window."
    lines = []
    for r in rows:
        state = "resolved" if r.get("resolved_at") else "sent" if r.get("sent_at") else "waiting"
        lines.append(f"#{r['id']}  {day(str(r['created_at'])[:10])}  {state:<8}  {plain(r['text'])}")
    return "\n".join(lines) + "\n\nDismiss one: finnamon alerts --dismiss <id>   (add --json for the full records)"


def budgets(rows: list[dict]) -> str:
    if not rows:
        return "No budgets yet. Ask the assistant to set some up, or: finnamon budget set <name> <amount>"
    body = [[r["name"].capitalize() if r["name"].islower() else r["name"], money(r["spent"]), money(r["monthly_limit"]),
             money(r["pace"]) if not r["fixed"] else "fixed", ("OVER" if r["spent"] > r["monthly_limit"] else "") , ", ".join(r.get("covers") or [])] for r in rows]
    return table(["Budget", "Spent", "Limit", "On pace for", "", "Counts"], body, right=(1, 2, 3))


def networth(d: dict) -> str:
    rows = [("Cash", d["cash"]), ("Investments", d["investments"]), ("Property", d["property"]), ("Owed (loans, cards)", -d["liabilities"])]
    lines = [f"Net worth  {money(d['net_worth'])}", ""] + [f"  {k:<22}{money(v):>14}" for k, v in rows]
    if d.get("accounts"):
        lines += ["", table(["Account", "Type", "Balance"], [[a["name"] + (f" …{a['mask']}" if a.get("mask") else ""), a["type"], money(a["current"] or 0)] for a in d["accounts"]], right=(2,))]
    return "\n".join(lines)


def history(rows: list[dict]) -> str:
    return table(["Date", "Net worth", "Assets", "Owed"], [[day(r["date"]), money(r["net_worth"]), money(r["assets"]), money(r["liabilities"])] for r in rows], right=(1, 2, 3)) if rows else "No balance history yet."


def properties(rows: list[dict]) -> str:
    if not rows:
        return 'Nothing stated yet. Add one: finnamon property set "House" 450000'
    return table(["Property", "Value", "Updated"], [[r["name"], money(r["value"]), day(str(r["updated_at"])[:10])] for r in rows], right=(1,))
