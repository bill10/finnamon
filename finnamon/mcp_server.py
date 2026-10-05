"""Milestone 5: read-only MCP server over the same functions the CLI exposes, for clients that
aren't Claude Code (Claude Desktop, a phone app). Optional extra: pip install 'finnamon[mcp]'.

    finnamon serve            # stdio transport

Claude Code itself uses the CLI through Bash; this exists so other clients get the same reads."""
from __future__ import annotations

import json

from . import budgets, charts, investments, query, store, triage


def build():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise RuntimeError("MCP needs the mcp package: pip install 'finnamon[mcp]'") from e

    mcp = FastMCP("finnamon", instructions="Read-only view of a household's finances. Amount sign: positive = money out.")

    @mcp.tool()
    def list_accounts() -> str:
        """Accounts with latest balances."""
        return json.dumps(investments.net_worth(store.connect())["accounts"], default=str)

    @mcp.tool()
    def search_transactions(text: str = "", days: int = 90, limit: int = 50) -> str:
        """Transactions matching text (merchant or raw name) in the last N days."""
        rows = query.run(
            "SELECT transaction_id, date, amount, display merchant, category, account_name account FROM tx_now "
            "WHERE date >= date('now', ?) AND lower(COALESCE(display,'')||' '||COALESCE(merchant_name,'')||' '||COALESCE(name,'')) LIKE ? "
            "ORDER BY date DESC LIMIT ?", params=(f"-{int(days)} days", f"%{text.lower()}%", int(limit)))
        return query.to_json(rows)

    @mcp.tool()
    def spend_by_category(months: int = 3) -> str:
        """Spend per Plaid category per month: transfers between own accounts and linked-card payments left out, refunds netted."""
        return query.to_json(query.run(
            "SELECT strftime('%Y-%m',date) ym, category_primary, round(sum(amount)) spent FROM tx_now "
            "WHERE flow IN ('expense','refund') AND pending=0 AND date>=date('now', ?) GROUP BY ym, category_primary ORDER BY ym, spent DESC", params=(f"-{int(months)} months",)))

    @mcp.tool()
    def budget_progress() -> str:
        """Every active budget: limit, month-to-date spend, linear pace."""
        return json.dumps(budgets.budget_list(store.connect()), default=str)

    @mcp.tool()
    def list_alerts(days: int = 30) -> str:
        """Alerts raised in the last N days."""
        return query.to_json(query.run("SELECT id, kind, created_at, sent_at, reason, payload_json FROM alerts WHERE created_at>=datetime('now','localtime', ?) ORDER BY id DESC", params=(f"-{int(days)} days",)))

    @mcp.tool()
    def list_suppressed(days: int = 30) -> str:
        """Anomaly candidates triage chose not to mention, with its reasons."""
        return json.dumps([dict(r) for r in triage.suppressed(store.connect(), days)], default=str)

    @mcp.tool()
    def net_worth_history(months: int = 12) -> str:
        """Net worth, assets and liabilities by day from balance snapshots."""
        return json.dumps(investments.net_worth_history(store.connect(), months))

    @mcp.tool()
    def render_chart(name: str, arg: str = "") -> str:
        """Render a chart (budgets, spend_by_category, balance_history, merchant_history <merchant>, monthly_in_out); returns the PNG path."""
        return str(charts.render(store.connect(), name, arg or None))

    @mcp.tool()
    def sql(select: str) -> str:
        """Run a read-only SELECT against the household DB."""
        return query.to_json(query.run(select))

    return mcp


def main() -> None:
    build().run()
