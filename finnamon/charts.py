"""Named charts → PNG in ~/.finnamon/charts/. matplotlib is an optional extra (`finnamon[charts]`).
Claude calls `finnamon chart <name>` and the daemon sends any printed path as a photo."""
from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import budgets, config, query, store, taxonomy

WITHIN, OVER = "#4a9a7b", "#d9534f"   # the budget trend PNG's bars
CHARTS = ("budgets", "spend_by_category", "balance_history", "merchant_history", "monthly_in_out")
# Both read tx_now's `flow` (migrations/008_tx_flow.sql): transfers between the household's own accounts and payments
# onto linked cards are neither in nor out, refunds net against spending, and the mortgage is out but its own series.
SPEND_BY_CATEGORY = ("SELECT category_primary c, round(sum(amount)) s FROM tx_now "
                     "WHERE flow IN ('expense','refund') AND pending=0 AND date>=date('now', ?) GROUP BY c HAVING s>0 ORDER BY s DESC LIMIT 12")
IN_OUT = ("SELECT strftime('%Y-%m',date) ym, round(sum(CASE WHEN flow='income' THEN -amount END)) i, "
          "round(sum(CASE WHEN flow IN ('expense','refund') THEN amount END)) o, round(sum(CASE WHEN flow='mortgage' THEN amount END)) m "
          "FROM tx_now WHERE pending=0 AND date>=date('now', ?) GROUP BY ym ORDER BY ym")


def _cat(code) -> str:
    """A category as people read it: Plaid's FOOD_AND_DRINK is "Food and drink", none is "Uncategorized"."""
    return taxonomy.label(code) if code else "Uncategorized"


# balances: a loan (the mortgage) is hundreds of thousands, and flattens checking against it on one axis; arg "all" brings loans back
NOT_LOAN = "AND a.type <> 'loan' "


def _balance_rows(conn: sqlite3.Connection, months: int, arg: str | None) -> list:
    return conn.execute("SELECT a.name||COALESCE(' …'||a.mask, '') n, b.as_of, b.current FROM balances b JOIN accounts a ON a.account_id=b.account_id "
                        f"WHERE a.mirror_of IS NULL {'' if arg == 'all' else NOT_LOAN}AND b.as_of>=datetime('now', ?) ORDER BY n, b.as_of", (f"-{months} months",)).fetchall()


def _out(name: str) -> Path:
    d = config.charts_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"


def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError as e:
        raise RuntimeError("charts need matplotlib: pip install 'finnamon[charts]'") from e


def render(conn: sqlite3.Connection, name: str, arg: str | None = None, months: int = 12) -> Path:
    plt = _plt()
    from matplotlib.ticker import StrMethodFormatter
    dollars = StrMethodFormatter("${x:,.0f}")
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    if name == "budgets":
        from matplotlib.patches import Patch
        series = _budget_trend(conn, months)
        b, pts = _budget_pick(series + [_budget_overall(series)], arg) if series else ({"name": "no budgets yet"}, [])
        ax.yaxis.set_major_formatter(dollars)
        x = range(len(pts))
        bars = ax.bar(x, [p["spent"] for p in pts], color=[OVER if p["status"] == "over limit" else WITHIN for p in pts])
        if pts:
            bars[-1].set_alpha(0.45)   # this month is partial: paler
        line, = ax.plot(x, [p["limit"] for p in pts], color="#c9a227", lw=2.5, marker="o", label="limit")
        ax.set_xticks(x); ax.set_xticklabels([p["label"] for p in pts], fontsize=8, rotation=45 if len(pts) > 8 else 0, ha="right" if len(pts) > 8 else "center")
        ax.set_title(f"Budget trend: {_title(b['name'])} (paler = month to date)")
        ax.legend(handles=[Patch(color=WITHIN, label="within limit"), Patch(color=OVER, label="over limit"), line], fontsize=8)
    elif name == "spend_by_category":
        rows = conn.execute(SPEND_BY_CATEGORY, (f"-{months} months",)).fetchall()
        ax.barh([_cat(r[0]) for r in rows], [r[1] for r in rows], color="#4a7ebb"); ax.invert_yaxis(); ax.xaxis.set_major_formatter(dollars)
        ax.set_title(f"Spend by category, last {months} months")
    elif name == "balance_history":
        rows = _balance_rows(conn, months, arg)
        series: dict[str, list] = {}
        for n, t, v in rows:
            series.setdefault(n, []).append((datetime.fromisoformat(t), v))
        for n, pts in series.items():
            ax.plot([p[0] for p in pts], [p[1] for p in pts], label=n)
        ax.set_title("Balances" + ("" if arg == "all" else " (loans left out)")); ax.yaxis.set_major_formatter(dollars)
        if series:
            ax.legend(fontsize=7)
    elif name == "merchant_history":
        if not arg:
            raise ValueError("merchant_history needs a merchant name")
        rows = conn.execute("SELECT date, amount FROM tx_now WHERE lower(COALESCE(display,'')||' '||COALESCE(merchant_name,'')||' '||COALESCE(name,'')) LIKE ? AND pending=0 ORDER BY date",
                            (f"%{arg.lower()}%",)).fetchall()
        ax.bar([datetime.fromisoformat(r[0]) for r in rows], [r[1] for r in rows], width=3, color="#4a7ebb")
        ax.set_title(f"{arg}: every charge"); ax.yaxis.set_major_formatter(dollars)
    elif name == "monthly_in_out":
        rows = conn.execute(IN_OUT, (f"-{months} months",)).fetchall()
        x = range(len(rows))
        ax.bar([i - 0.2 for i in x], [r[1] or 0 for r in rows], width=0.4, label="in", color="#5cb85c")
        ax.bar([i + 0.2 for i in x], [r[2] or 0 for r in rows], width=0.4, label="spending", color="#d9534f")
        ax.bar([i + 0.2 for i in x], [r[3] or 0 for r in rows], width=0.4, bottom=[r[2] or 0 for r in rows], label="mortgage", color="#f0ad4e")
        ax.set_xticks(list(x)); ax.set_xticklabels([r[0] for r in rows], rotation=45, fontsize=7); ax.legend()
        ax.set_title("Monthly in vs out"); ax.yaxis.set_major_formatter(dollars)
    else:
        raise ValueError(f"{name} is a table, on the dashboard only: finnamon chart --spec {name}" if name in TABLES else f"unknown chart {name}; one of {', '.join(CHARTS)}")
    if not ax.has_data():   # empty axes read as a broken chart
        ax.set_axis_off()
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center", transform=ax.transAxes, fontsize=16, color="#888")
    fig.tight_layout()
    out = _out(name)
    fig.savefig(out)
    plt.close(fig)
    return out


# --- Vega-Lite specs for the web dashboard's chart board -------------------------------------------
# charts/current.json is a list of Vega-Lite specs, each tagged usermeta.finnamon.id; the web server watches the file and the
# page draws one panel per entry. `--spec <name>` adds a preset below (same queries as the PNGs above: tx_now's flow, so transfers and
# payments onto linked cards are not spend); `--spec-json` adds any spec the assistant writes, so a new kind of chart never needs Python.
# Colours are the page's (its Vega config), not fixed here.

CURRENT_SPEC = "current.json"
MAX_CHARTS = 8              # a ninth is refused, not dropped: put() says to remove one
MAX_BYTES = 256_000         # one serialised entry; the page fetches and holds every one
MAX_ROWS = 5000             # a data.sql result
VEGA_LITE = "https://vega.github.io/schema/vega-lite/v5.json"
# Bank strings (merchant names, memos) flow into these specs, so nothing in one may make the page fetch or navigate:
# data.url and lookup's from.data.url load a URL, the image mark loads its url channel, href links out, and a param's
# bind.element is a CSS selector that plants its widget anywhere on the page (beside the intercom, say).
FORBIDDEN = ("url", "href", "element")
# Nor may one carry an expression. The page walks Vega expressions with the interpreter under a CSP that forbids eval, so a
# spec's expression is never JavaScript; still, every computation a chart needs is done in its SQL, so the expression language
# adds nothing and is refused whole: these keys hold one (calculate, expr, signal, labelExpr, a param's expr), open a selection
# or an event stream (select, on), plant a widget (bind), register a formatter (customFormatTypes), or generate rows in the
# browser (sequence: a tiny spec asking for a billion of them hangs the tab); and a filter, a condition's test, and not/and/or
# take a string. Vega-Lite pastes some strings into an expression unquoted: a format string (so no quote or backslash), a
# formatType (so one of its own two), a date part and a predicate value under a timeUnit (so a plain date or number token),
# a data.format.parse specifier, an impute's field and keyvals, a config signal's update.
# ponytail: a denylist over Vega-Lite's keys; the interpreter under a no-eval CSP is the guard that holds (an expression that gets
# past this draws wrong numbers or hangs the tab, never runs script). Upgrade path if it keeps growing: compile the spec with
# vega-lite in Node and refuse any signal/expr/update Vega-Lite did not generate itself.
EXPR_KEYS = ("expr", "signal", "signals", "update", "init", "events", "calculate", "labelExpr", "select", "on", "bind",
             "customFormatTypes", "sequence", "impute", "keyvals")
EXPR_STRING = ("filter", "test", "not", "and", "or")
FORMAT_KEYS = ("format", "numberFormat", "timeFormat", "normalizedNumberFormat")
FORMAT_TYPE_KEYS = ("formatType", "timeFormatType")
FORMAT_TYPES = ("number", "time")
DATE_PARTS = ("year", "date", "hours", "minutes", "seconds", "milliseconds")
PREDICATE_VALUES = ("equal", "lt", "lte", "gt", "gte", "oneOf", "range")
TOKEN = re.compile(r"[\w:.+ -]+")   # a date, a time, a number: no bracket, quote or comma, so no call and no second argument
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:48] or "chart"


def preset_id(name: str, arg: str | None) -> str:
    """A preset's id. Cut to ID's 64: a long merchant made one the page's × and chips could not name."""
    return (name + (f"-{slug(arg)}" if arg else ""))[:64]


def spec(conn: sqlite3.Connection, name: str, arg: str | None = None, months: int = 12) -> dict:
    if name in TABLES:
        return table_preset(name, arg, months)
    if name not in CHARTS:
        raise ValueError(f"unknown chart {name}; one of {', '.join(CHARTS + TABLES)}")
    base = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "width": "container", "height": 300,
            "usermeta": {"finnamon": {"id": preset_id(name, arg), "chart": name, "arg": arg,   # the page lights the matching chip
                                      "source": {"preset": name, "arg": arg, "months": months}, "as_of": _now()}}}   # what refresh() redraws it from
    if name == "budgets":   # every budget's months in the data; the filter picks one, and the page swaps it from its selector
        series = _budget_trend(conn, months)
        if not series:
            return {**base, "title": "Budget trend", "data": {"values": []}, "mark": "bar"}
        series.append(_budget_overall(series))   # last here, so the default pick stays a real budget; first in the page's picker
        b, pts = _budget_pick(series, arg)
        values = [{"budget": c["name"], "month": p["label"], "spent": p["spent"], "limit": p["limit"], "period": p["period"], "status": p["status"]}
                  for c, ps in series for p in ps]
        tip = [{"field": "month"}, {"field": "spent", "format": "$,.0f"}, {"field": "limit", "format": "$,.0f"}]
        y = {"type": "quantitative", "title": "$"}
        return {**base, "title": f"Budget trend: {_title(b['name'])}", "data": {"values": values},
                "usermeta": {"finnamon": {**base["usermeta"]["finnamon"], "id": name, "budget": b["name"],   # one budgets panel, whichever it opens on
                                          "budgets": [{"name": c["name"], "over": c["spent"] > c["monthly_limit"]} for c, _ in series[-1:] + series[:-1]]}},
                "transform": [{"filter": {"field": "budget", "equal": b["name"]}}],
                "encoding": {"x": {"field": "month", "type": "ordinal", "title": None, "sort": [p["label"] for p in pts], "axis": {"labelAngle": 0}}},
                # the "" entry takes the theme's pale first colour; within, over and the limit line take the next three
                "layer": [{"mark": "bar", "encoding": {"y": {**y, "field": "spent"}, "tooltip": tip,
                                                       "color": {"field": "status", "title": None, "scale": {"domain": ["", "within limit", "over limit", "limit"]},
                                                                 "legend": {"values": ["within limit", "over limit", "limit"]}},
                                                       "opacity": {"field": "period", "type": "nominal", "scale": {"domain": ["full month", "month to date"], "range": [1, 0.45]}, "legend": None}}},
                          {"mark": {"type": "line", "point": True, "strokeWidth": 2.5}, "encoding": {"y": {**y, "field": "limit"}, "color": {"datum": "limit"}, "tooltip": tip}}]}
    if name == "spend_by_category":
        rows = conn.execute(SPEND_BY_CATEGORY, (f"-{months} months",)).fetchall()
        return {**base, "title": f"Spend by category, last {months} months", "data": {"values": [{"category": _cat(r[0]), "amount": r[1]} for r in rows]},
                "mark": "bar", "encoding": {"y": {"field": "category", "type": "nominal", "sort": "-x", "title": None},
                                            "x": {"field": "amount", "type": "quantitative", "title": "$"},
                                            "tooltip": [{"field": "category"}, {"field": "amount", "format": "$,.0f"}]}}
    if name == "balance_history":
        rows = _balance_rows(conn, months, arg)
        return {**base, "title": f"Balances, last {months} months" + ("" if arg == "all" else ", loans left out"), "data": {"values": [{"account": r[0], "as_of": r[1], "balance": r[2]} for r in rows]},
                "mark": {"type": "line", "point": True}, "encoding": {"x": {"field": "as_of", "type": "temporal", "title": None},
                                                                      "y": {"field": "balance", "type": "quantitative", "title": "$"},
                                                                      "color": {"field": "account", "title": None, "legend": {"columns": 2}},   # account names are long: two columns wrap under the plot on a phone
                                                                      "tooltip": [{"field": "account"}, {"field": "as_of", "type": "temporal"}, {"field": "balance", "format": "$,.0f"}]}}
    if name == "merchant_history":
        if not arg:
            raise ValueError("merchant_history needs a merchant: finnamon chart --spec merchant_history costco")
        rows = conn.execute("SELECT date, amount, display m FROM tx_now WHERE lower(COALESCE(display,'')||' '||COALESCE(merchant_name,'')||' '||COALESCE(name,'')) LIKE ? AND pending=0 ORDER BY date",
                            (f"%{arg.lower()}%",)).fetchall()
        return {**base, "title": f"{arg}: every charge", "data": {"values": [{"date": r[0], "amount": r[1], "merchant": r[2]} for r in rows]},
                "mark": {"type": "bar"}, "encoding": {"x": {"field": "date", "type": "temporal", "title": None},
                                                      "y": {"field": "amount", "type": "quantitative", "title": "$"},
                                                      "tooltip": [{"field": "date", "type": "temporal"}, {"field": "merchant"}, {"field": "amount", "format": "$,.2f"}]}}
    rows = conn.execute(IN_OUT, (f"-{months} months",)).fetchall()
    labels = _month_labels(rows)
    values = [{"month": r[0], "label": labels[r[0]], "direction": d, "series": k, "amount": r[c] or 0}
              for r in rows for d, k, c in (("in", "income", 1), ("out", "spending", 2), ("out", "mortgage", 3))]
    return {**base, "title": f"In and out by month, last {months} months", "data": {"values": values},
            "mark": "bar", "encoding": {"x": {"field": "label", "type": "ordinal", "title": None, "sort": [labels[r[0]] for r in rows]},   # "May", not "2026-05"
                                        "y": {"field": "amount", "type": "quantitative", "title": "$"},   # stacked: out = spending + mortgage
                                        "xOffset": {"field": "direction", "scale": {"domain": ["in", "out"]}},
                                        "color": {"field": "series", "scale": {"domain": ["mortgage", "income", "spending"]},   # theme colours 1, 2, 3
                                                  "legend": {"values": ["income", "spending", "mortgage"]}, "title": None},
                                        "tooltip": [{"field": "month"}, {"field": "series"}, {"field": "amount", "format": "$,.0f"}]}}


def _budget_trend(conn: sqlite3.Connection, months: int = 12) -> list[tuple[dict, list[dict]]]:
    """[(budget, [{label, spent, limit, period, status}])]: each active budget's last `months` months, the current one month to
    date. Months before the household's first transaction are left out: they would read as nothing spent."""
    as_of, months = store.now_local(), max(1, months)
    first = (conn.execute("SELECT min(date) FROM tx_now").fetchone()[0] or as_of)[:7]
    out = []
    for b in budgets.budget_list(conn, as_of):
        pts = [p for p in budgets.monthly_spent(conn, b["id"], months, as_of) if p[0] >= first] or budgets.monthly_spent(conn, b["id"], 1, as_of)
        labels = _month_labels(pts)
        last = len(pts) - 1
        # the month its first limit took effect (created_at for a budget older than the limit history): Overall counts it from there
        since = conn.execute("SELECT COALESCE((SELECT min(effective_from) FROM budget_limit_history WHERE budget_id=:id), "
                             "(SELECT created_at FROM budgets WHERE id=:id))", {"id": b["id"]}).fetchone()[0] or as_of
        out.append(({**b, "since": since[:7]}, [{"ym": ym, "label": labels[ym] + (" (so far)" if i == last else ""), "spent": v, "limit": lim,
                         "period": "month to date" if i == last else "full month", "status": "over limit" if v > lim else "within limit"}
                        for i, (ym, v, lim) in enumerate(pts)]))
    return out


# Overall's name in the data and the picker: budget_set strips names, so no budget can be called this. "overall" (the CLI's
# --budget, the page's label) picks it unless a budget really is named overall.
OVERALL = " overall"


def _budget_overall(series: list) -> tuple[dict, list[dict]]:
    """Every budget summed, month by month: spent against the sum of the limits, drawn as one budget is. A month counts a budget
    only from the month its first limit took effect, and a month no budget covers yet is left out, so a budget set up last
    month does not lift the older months' line. Every budget is monthly, so the periods line up."""
    months: dict[str, dict] = {}
    for b, pts in series:
        for p in pts:
            if p["ym"] >= b["since"]:
                m = months.setdefault(p["ym"], {**p, "spent": 0.0, "limit": 0.0})
                m["spent"] += p["spent"]; m["limit"] += p["limit"]
    pts = [{**m, "spent": round(m["spent"], 2), "limit": round(m["limit"], 2), "status": "over limit" if m["spent"] > m["limit"] else "within limit"}
           for _, m in sorted(months.items())]
    spent, limit = sum(b["spent"] for b, _ in series), sum(b["monthly_limit"] for b, _ in series)
    return {"name": OVERALL, "spent": round(spent, 2), "monthly_limit": limit}, pts


def _budget_pick(series: list, name: str | None) -> tuple[dict, list[dict]]:
    """The budget the chart shows: the one named, else the first over its limit this month, else the first. A name that is no
    budget (removed since a board saved it) falls back to the default, so the panel keeps refreshing; the CLI refuses one up front."""
    hit = [s for s in series if name and s[0]["name"].strip() == name.strip().lower()]
    return hit[0] if hit else next((s for s in series if s[0]["spent"] > s[0]["monthly_limit"]), series[0])


def check_budget(conn: sqlite3.Connection, name: str) -> None:
    names = [b["name"] for b in budgets.budget_list(conn)]
    if name.strip().lower() not in names + ["overall"] * bool(names):
        raise ValueError(f"no budget named {name}; one of {', '.join(names) or 'none yet (finnamon budget set)'}")


def _title(name: str) -> str:
    name = name.strip()   # OVERALL
    return name[:1].upper() + name[1:]


def _month_labels(rows: list) -> dict:
    """"2026-05" as the axis shows it: "May", with the year on January and on the first month when the range spans years
    (a chart is expression-free, so the label is data). More than a year of months would repeat a name: the year on all."""
    spans = len({r[0][:4] for r in rows}) > 1
    def one(ym: str) -> str:
        name = datetime(2000, int(ym[5:7]), 1).strftime("%b")
        return f"{name} {ym[:4]}" if len(rows) > 12 or ym[5:7] == "01" or (spans and ym == rows[0][0]) else name
    return {r[0]: one(r[0]) for r in rows}


def custom(text: str, id: str | None = None, sql: str | None = None) -> dict:
    """A whole Vega-Lite spec from the assistant, checked, with every data.sql replaced by its rows (read-only).
    `sql` is the top-level data.sql given apart, so the spec can sit in single quotes and the SQL's literals in double."""
    try:
        sp = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(sp, dict):
        raise ValueError("a chart spec is one JSON object")
    if sql:
        sp["data"] = {"sql": sql}
    if "table" in sp:   # the page draws an entry with `table` as a table panel, not a chart
        raise ValueError("a chart spec has no table key; a table is --table-json")
    # vega-embed picks Vega or Vega-Lite from $schema, and full Vega brings signals and loaders
    if sp.setdefault("$schema", VEGA_LITE) != VEGA_LITE:
        raise ValueError(f"$schema must be Vega-Lite v5 ({VEGA_LITE}), not {sp['$schema']}")
    src = copy.deepcopy(sp)
    src.pop("usermeta", None)
    _check(sp)
    static = src == {k: v for k, v in sp.items() if k != "usermeta"}   # _check swaps each data.sql for its rows: unchanged, there is no query to rerun
    if id is None:
        t = sp.get("title")
        id = slug(t.get("text", "") if isinstance(t, dict) else " ".join(map(str, t)) if isinstance(t, list) else t or "chart")
    if not ID.fullmatch(id):
        raise ValueError("--id is lowercase letters, digits, - and _ (up to 64)")
    sp.setdefault("width", "container")
    sp["usermeta"] = {"finnamon": {"id": id, "chart": None, "arg": None, "as_of": _now(),
                                   **({"static": True} if static else {"source": {"spec": src}})}}   # nothing else: vega-embed merges usermeta.embedOptions over the page's, and its actions/sourceHeader/patch run script on the page
    if len(json.dumps(sp, default=str)) > MAX_BYTES:
        raise ValueError(f"spec is over {MAX_BYTES // 1000} KB; aggregate in the SQL")
    return sp


def _check(node, key: str | None = None) -> None:
    if isinstance(node, str):
        if key in EXPR_STRING:
            raise ValueError(f"{key} takes a predicate object, not an expression string: compute in the SQL (a filter is a WHERE)")
        if key in FORMAT_KEYS and any(c in node for c in "\"'\\"):
            raise ValueError(f"{key} is a d3 format string like $,.0f; no quotes or backslashes")
        if key in FORMAT_TYPE_KEYS and node not in FORMAT_TYPES:
            raise ValueError(f"{key} is one of {', '.join(FORMAT_TYPES)}")
        if key in DATE_PARTS and not TOKEN.fullmatch(node):
            raise ValueError(f"a date's {key} is a number")
    if isinstance(node, list):
        for x in node:
            _check(x, key)
    elif isinstance(node, dict):
        if "timeUnit" in node:   # a value compared under a timeUnit is pasted into datetime(...)
            for k in PREDICATE_VALUES:
                for v in (node.get(k) if isinstance(node.get(k), list) else [node.get(k)]):
                    if isinstance(v, str) and not TOKEN.fullmatch(v):
                        raise ValueError(f"{k} under a timeUnit is a date or a number")
        if key == "parse" and any(isinstance(v, str) and any(c in v for c in "\"'\\") for v in node.values()):
            raise ValueError("data.format.parse takes date:%Y-%m-%d style specifiers; no quotes or backslashes")
        if bad := [k for k in FORBIDDEN if k in node]:
            raise ValueError(f"{key or 'spec'}.{bad[0]} is not allowed: a chart stays inside its panel and loads nothing from outside the spec (use data.sql or data.values)")
        if bad := [k for k in EXPR_KEYS if k in node]:
            raise ValueError(f"{key or 'spec'}.{bad[0]} is not allowed: a chart carries no expressions, selections or widgets; compute in the SQL (a calculate is a column)")
        m = node.get("mark")
        if (m.get("type") if isinstance(m, dict) else m) == "image":
            raise ValueError("the image mark is not allowed: it loads a URL")
        if key == "data" and isinstance(node.get("values"), str):   # a CSV/TSV string: Vega parses it with new Function, which the page's CSP forbids
            raise ValueError("data.values is a list of rows, not a string")
        if key == "data" and "sql" in node:   # also lookup's from.data
            try:
                rows = query.run(str(node.pop("sql")), MAX_ROWS + 1)
            except sqlite3.Error as e:
                raise ValueError(f"data.sql: {e}") from None
            if len(rows) > MAX_ROWS:   # a silently cut series would chart the wrong thing
                raise ValueError(f"data.sql returned over {MAX_ROWS} rows; aggregate in the SQL")
            node["values"] = rows
        for k, v in node.items():
            if k == "datasets" and key is None and isinstance(v, dict):   # named row lists: data, not spec (a column may be called url), but never a CSV string
                if any(isinstance(rows, str) for rows in v.values()):
                    raise ValueError("a dataset is a list of rows, not a string")
            elif not (k == "values" and key == "data"):   # an axis's values are spec
                _check(v, k)


# --- Table panels on the same board ---------------------------------------------------------------------------------
# A table entry is not Vega-Lite: {"title", "table": {"columns", "total", "more"}, "data": {"values"}, "usermeta"}. Its rows come from
# the same read-only query.run as a chart's data.sql; the page renders every cell as escaped text in a format named here, so a
# table carries no spec grammar at all to check.
TABLES = ("recent_transactions", "large_transactions", "recurring", "uncategorized")
TABLE_ROWS = 50             # rows kept on the board; the panel says "showing 50 of 312"
MAX_COLUMNS = 12
FORMATS = ("text", "money", "date", "number")   # money is tx_now's sign: positive is money out, drawn as -$52.10 in the debt colour
TX_COLUMNS = [{"field": "date", "label": "Date", "format": "date"}, {"field": "merchant", "label": "Merchant"},
              {"field": "category", "label": "Category"}, {"field": "account", "label": "Account"},
              {"field": "amount", "label": "Amount", "format": "money"}]
# category: the detailed one as people read it ("restaurant", not "food and drink"; taxonomy.label in SQL), "uncategorized" for none.
# transaction_id rides along, not as a column: the page puts a Change category button on a row that has one.
TX_SELECT = ("SELECT date, COALESCE(display, merchant_name, name) merchant, CASE WHEN category IS NULL THEN 'uncategorized' "
             "WHEN category_primary IS NOT NULL AND category <> category_primary AND substr(category, 1, length(category_primary) + 1) = category_primary || '_' "
             "THEN replace(lower(substr(category, length(category_primary) + 2)), '_', ' ') ELSE replace(lower(category), '_', ' ') END category, "
             "account_name || COALESCE(' …' || mask, '') account, amount, transaction_id FROM tx_now ")


def table_preset(name: str, arg: str | None = None, months: int = 12) -> dict:
    if name == "recent_transactions":
        title, cols, sql = "Recent transactions, last 30 days", TX_COLUMNS, TX_SELECT + "WHERE date >= date('now', '-30 days') ORDER BY date DESC, transaction_id"
    elif name == "large_transactions":
        try:
            over = abs(float(arg or 500))
        except ValueError:
            raise ValueError("large_transactions takes an amount: finnamon chart --spec large_transactions 1000") from None
        title, cols = f"Transactions over ${over:,.0f}, last {int(months)} months", TX_COLUMNS
        sql = TX_SELECT + f"WHERE flow NOT IN ('transfer', 'card_payment', 'skipped') AND abs(amount) >= {over} AND date >= date('now', '-{int(months)} months') ORDER BY date DESC"
    elif name == "uncategorized":   # an import's rows, mostly: no category, so no budget counts them
        title, cols = "Uncategorized transactions", [c for c in TX_COLUMNS if c["field"] != "category"]   # every row would say "uncategorized"
        sql = TX_SELECT + f"WHERE {budgets.UNCATEGORIZED} ORDER BY date DESC, transaction_id"
    elif name == "recurring":
        title = "Recurring charges"   # ended streams: `ended` is no column; the page hides those rows behind "ended (N)"
        cols = [{"field": "merchant", "label": "Merchant"}, {"field": "frequency", "label": "Every"}, {"field": "last_date", "label": "Last", "format": "date"},
                {"field": "next_date", "label": "Next", "format": "date"}, {"field": "amount", "label": "Amount", "format": "money"}]
        sql = ("SELECT COALESCE(NULLIF(trim(r.merchant_name), ''), r.description) merchant, replace(lower(r.frequency), '_', ' ') frequency, r.last_date, "
               f"CASE WHEN {budgets.STREAM_ENDED} THEN NULL ELSE r.predicted_next_date END next_date, r.last_amount amount, "
               f"{budgets.STREAM_ENDED} ended FROM recurring r JOIN accounts a ON a.account_id = r.account_id AND a.mirror_of IS NULL "
               "WHERE r.direction = 'outflow' AND COALESCE(r.status, '') <> 'EARLY_DETECTION' ORDER BY ended, r.last_amount DESC")   # ended rows ride along: the page hides them behind a toggle
    else:
        raise ValueError(f"unknown table {name}; one of {', '.join(TABLES)}")
    t = custom_table(json.dumps({"title": title, "columns": cols}), preset_id(name, arg), sql)
    t["usermeta"]["finnamon"].update(chart=name, arg=arg, source={"preset": name, "arg": arg, "months": months})
    return t


def custom_table(text: str, id: str | None = None, sql: str | None = None) -> dict:
    """A table panel from the assistant: {"title", "columns": [{field, label, format, align}], "data": {"sql"} or {"values"}}.
    `sql` is data.sql given apart, as for --spec-json. The SQL runs read-only through query.run, like a chart's."""
    try:
        t = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(t, dict):
        raise ValueError("a table spec is one JSON object")
    if sql:
        t["data"] = {"sql": sql}
    if bad := sorted(set(t) - {"title", "columns", "data"}):
        raise ValueError(f"a table spec has title, columns and data (or --sql); not {', '.join(bad)}")
    title = t.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("a table needs a title")
    cols = _columns(t.get("columns"))
    data = t.get("data")
    if not isinstance(data, dict) or len(data) != 1 or not ({"sql", "values"} & set(data)):
        raise ValueError('data is {"sql": "SELECT ..."} (or --sql), or {"values": [rows]}')
    src = copy.deepcopy(t)
    if "sql" in data:
        try:
            rows = query.run(str(data["sql"]), MAX_ROWS + 1)
        except sqlite3.Error as e:
            raise ValueError(f"data.sql: {e}") from None
    else:
        rows = data["values"]
        if not isinstance(rows, list) or not all(isinstance(r, dict) and all(not isinstance(v, (dict, list)) for v in r.values()) for r in rows):
            raise ValueError("data.values is a list of rows, each an object of plain values")
    if rows and (missing := [c["field"] for c in cols if c["field"] not in rows[0]]):
        raise ValueError(f"column {missing[0]!r} is not in the rows; they have: {', '.join(rows[0])}")
    if id is None:
        id = slug(title)
    if not ID.fullmatch(id):
        raise ValueError("--id is lowercase letters, digits, - and _ (up to 64)")
    t = {"title": title, "table": {"columns": cols, "total": min(len(rows), MAX_ROWS), "more": len(rows) > MAX_ROWS},
         "data": {"values": rows[:TABLE_ROWS]},
         "usermeta": {"finnamon": {"id": id, "chart": None, "arg": None, "as_of": _now(),
                                   **({"source": {"table": src}} if "sql" in data else {"static": True})}}}
    if len(json.dumps(t, default=str)) > MAX_BYTES:
        raise ValueError(f"table is over {MAX_BYTES // 1000} KB; select fewer or shorter columns")
    return t


def _columns(cols) -> list[dict]:
    if not isinstance(cols, list) or not cols or len(cols) > MAX_COLUMNS:
        raise ValueError(f'columns is a list of 1 to {MAX_COLUMNS} like {{"field": "amount", "label": "Amount", "format": "money"}}')
    out = []
    for c in cols:
        if not isinstance(c, dict) or not isinstance(c.get("field"), str) or set(c) - {"field", "label", "format", "align"}:
            raise ValueError("a column is {field, label, format, align}, field required")
        f = c.get("format", "text")
        if f not in FORMATS:
            raise ValueError(f"a column's format is one of {', '.join(FORMATS)}")
        a = c.get("align", "right" if f in ("money", "number") else "left")
        if a not in ("left", "right"):
            raise ValueError("a column's align is left or right")
        out.append({"field": c["field"], "label": str(c.get("label", c["field"])), "format": f, "align": a})
    return out


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _source(e: dict) -> dict | None:
    """What a board entry was drawn from. A preset drawn before sources were stored is known by its chart name (months from its title)."""
    fm = e["usermeta"]["finnamon"]
    if fm.get("source"):
        return fm["source"]
    if fm.get("chart") in CHARTS:
        m = re.search(r"last (\d+) months", str(e.get("title", "")))
        return {"preset": fm["chart"], "arg": fm.get("arg"), "months": int(m[1]) if m else 12}
    return None


def _redraw(conn: sqlite3.Connection, e: dict) -> dict | None:
    src = _source(e)
    if src is None:
        return None
    if "preset" in src:
        return spec(conn, src["preset"], src.get("arg"), int(src.get("months") or 12))
    if "table" in src:
        return custom_table(json.dumps(src["table"]), e["usermeta"]["finnamon"]["id"])
    return custom(json.dumps(src["spec"]), e["usermeta"]["finnamon"]["id"])   # the same checks and read-only query.run as --spec-json


def _sans_as_of(entries: list[dict]) -> str:
    return json.dumps([{**e, "usermeta": {"finnamon": {k: v for k, v in e["usermeta"]["finnamon"].items() if k != "as_of"}}} for e in entries], default=str)


def refresh(conn: sqlite3.Connection | None = None, write: bool = True) -> list[dict]:
    """Recompute every board chart from its source (a preset's query, a spec's data.sql), so the board follows syncs, imports and
    reclassifications. Returns the board as of now; current.json is rewritten only when a value changed, and never with write=False:
    the dashboard's load uses that, since its file watch re-fetches and a query on the clock ('now') changes on every run. A chart whose query now fails keeps its last values and their as_of; a
    spec with only inline values is marked static and left alone."""
    if not (config.charts_dir() / CURRENT_SPEC).exists():
        return []
    conn = conn or store.connect()
    with _board() as entries:
        fresh = []
        for e in entries:
            try:
                new = _redraw(conn, e)
            except Exception as err:  # noqa: BLE001 - one bad chart (or a hand-edited source) must not stop the rest
                print(f"chart {e['usermeta']['finnamon']['id']}: not refreshed: {err}", file=sys.stderr)
                new = e
            if new is None:
                new = copy.deepcopy(e)
                new["usermeta"]["finnamon"]["static"] = True
            fresh.append(new)
        if write and _sans_as_of(fresh) != _sans_as_of(entries):
            entries[:] = fresh
    return fresh


def follow(conn: sqlite3.Connection | None = None) -> None:
    """refresh() after a write (sync, import, reclassification): a chart that cannot refresh never fails the write that triggered it."""
    try:
        refresh(conn)
    except Exception as e:  # noqa: BLE001
        print(f"charts not refreshed: {e}", file=sys.stderr)


@contextmanager
def _board():
    """The list, locked for read-modify-write: two chips clicked together are two CLI processes."""
    d = config.charts_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / CURRENT_SPEC
    with open(d / "current.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = board()
        before = json.dumps(entries, default=str)
        yield entries
        if json.dumps(entries, default=str) == before:   # an unchanged board is not rewritten: the dashboard watches the file
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[-MAX_CHARTS:], default=str))
        os.replace(tmp, p)


def board() -> list[dict]:
    """What the dashboard shows. A pre-0.7.5 file holds one spec object: a board of one."""
    p = config.charts_dir() / CURRENT_SPEC
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    entries = [e for e in (data if isinstance(data, list) else [data]) if isinstance(e, dict)]
    for e in entries:
        fm = e.setdefault("usermeta", {}).setdefault("finnamon", {})
        fm.setdefault("id", preset_id(fm.get("chart") or "chart", fm.get("arg")))   # spec()'s id, so an upgraded file replaces rather than doubles
    return entries


def put(sp: dict) -> Path:
    """Add a spec to the board, or replace the one with its id where it stands."""
    id = sp["usermeta"]["finnamon"]["id"]
    with _board() as entries:
        i = next((i for i, e in enumerate(entries) if e["usermeta"]["finnamon"]["id"] == id), None)
        if i is None:
            if len(entries) >= MAX_CHARTS:   # the oldest used to fall off the front without a word
                raise ValueError(f"the board holds {MAX_CHARTS} charts at most; remove one (its × on the dashboard, or finnamon chart --remove <id>; --list names them)")
            entries.append(sp)
        else:
            entries[i] = sp
    return config.charts_dir() / CURRENT_SPEC


def remove(id: str) -> Path:
    with _board() as entries:
        ids = [e["usermeta"]["finnamon"]["id"] for e in entries]
        if id not in ids:
            raise ValueError(f"no chart {id!r} on the dashboard; there are: {', '.join(ids) or 'none'}")
        entries.pop(ids.index(id))
    return config.charts_dir() / CURRENT_SPEC


def clear() -> Path:
    with _board() as entries:
        entries.clear()
    return config.charts_dir() / CURRENT_SPEC


def write_spec(conn: sqlite3.Connection, name: str, arg: str | None = None, months: int = 12) -> Path:
    """Put a preset on the dashboard's board; returns current.json's path."""
    return put(spec(conn, name, arg, months))
