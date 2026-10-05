"""MCP server over the same functions the CLI exposes. Optional extra: pip install 'finnamon[mcp]' (a Codex household
needs it, and doctor says so; a Claude household never starts it, so it stays an extra rather than pulling pydantic and
an HTTP stack into every install).

    finnamon serve            # stdio transport

Two kinds of tool:
- read-only views (accounts, transactions, budgets...) for clients that aren't Claude Code (Claude Desktop, a phone app);
- `finnamon(argv)`, the Codex assistant's one way to run the CLI. Codex's sandbox keeps its shell away from the
  household's database (docs/designs/codex-spike.md, finding 1), and this server runs outside it. The tool checks argv
  against the bundle's own `Bash(finnamon …)` allow and deny lists in .claude/settings.json, the one source of truth for
  both CLIs, and runs the CLI with FINNAMON_FROM_AGENT set, so the human-only gates hold. Codex auto-approves the tool
  (config.toml `approval_mode = "approve"`), so it never prompts: what the list does not allow, a person runs in a
  terminal. Claude Code itself uses the CLI through Bash."""
from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import shlex
import subprocess
import sys

from . import agent_runner, approval, assistant, budgets, charts, investments, query, store, triage

TIMEOUT_S = 600
TERMINAL = "a person runs this in a terminal"


@functools.cache
def rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(allow, deny) command patterns: the bundle's Bash(finnamon …) allow rules and every Bash(…) deny rule."""
    perms = json.loads(assistant.files()[".claude/settings.json"])["permissions"]

    def bash(xs):
        return tuple(m.group(1) for r in xs if (m := re.fullmatch(r"Bash\((.*)\)", r)))
    return tuple(p for p in bash(perms["allow"]) if p.split()[0] == "finnamon"), bash(perms["deny"])


def _denies(cmd: str, pat: str) -> bool:   # as approval.denied_by: `x *` also covers a bare `x`, so a deny can only cover more
    return fnmatch.fnmatchcase(cmd, pat) or (pat.endswith(" *") and cmd == pat[:-2])


def refusal(argv) -> str | None:
    """Why this argv may not run through the tool, or None. Deny first, then the allow list, matched exactly as written."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or argv[0] != "finnamon":
        return 'argv is a list of strings starting with "finnamon", one string per argument, e.g. ["finnamon", "alerts", "--sent"]'
    cmd = shlex.join(argv)   # quoting: an argument with a space can never pass for two words of a pattern
    if (hit := approval.protected_path("Bash", {"command": cmd})):
        return f"{hit} is off limits to every tool; {TERMINAL}"
    deny = rules()[1]
    if os.environ.get("FINNAMON_TRIAGE"):   # the triage run reads bank memos: its read-only list holds here too
        deny += tuple(m.group(1) for r in agent_runner.TRIAGE_DISALLOWED if (m := re.fullmatch(r"Bash\((.*)\)", r)))
    if (rule := next((p for p in deny if _denies(cmd, p)), None)):
        return f"`{cmd}` is on the deny list ({rule}): {TERMINAL}"
    if not any(fnmatch.fnmatchcase(cmd, p) for p in rules()[0]):
        return f"`{cmd}` is not on the assistant's allow list: {TERMINAL}"
    return None


def run_argv(argv) -> dict:
    """{"exit_code", "stdout", "stderr"} from the CLI (stdout as it printed it, JSON included), or {"refused": why}."""
    if (why := refusal(argv)):
        return {"refused": why}
    env = {**os.environ, "FINNAMON_FROM_AGENT": "1"}   # the human-only gates (cli._from_agent), whatever launched the server
    try:
        r = subprocess.run([sys.executable, "-m", "finnamon.cli", *argv[1:]], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "stdout": "", "stderr": f"`{shlex.join(argv)}` did not finish within {TIMEOUT_S}s"}
    return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def build():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise RuntimeError("MCP needs the mcp package: pip install 'finnamon[mcp]'") from e

    mcp = FastMCP("finnamon", instructions="A household's finances. Amount sign: positive = money out.")

    @mcp.tool(name="finnamon")
    def finnamon_tool(argv: list[str]) -> str:
        """Run one `finnamon` command: argv is the command line as a list, one string per argument, never a shell line,
        e.g. ["finnamon", "query", "SELECT count(*) FROM tx_now"]. Returns JSON {exit_code, stdout, stderr}, or
        {refused} for a command off the assistant's allow list (a person runs those in a terminal)."""
        return json.dumps(run_argv(argv))

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
