"""Codex card 3 of 6: the finnamon(argv) MCP tool, the guards on Codex's tool names and input shapes (the hook payloads
recorded in tests/fixtures/codex/hooks), and the rollout reader behind approval.ask. No login, no model call."""
import asyncio
import io
import json
import os
import tomllib
from pathlib import Path

import pytest

from finnamon import approval, assistant, cli, codex, mcp_server
from tests.conftest import conn  # noqa: F401 - fixture
from tests.test_daemon_cli import seed
from tests.test_permission_prompt import PROTECTED_FILES, HOME_FORMS, run_hook, tg  # noqa: F401 - fixture

CODEX = Path(__file__).parent / "fixtures" / "codex"
HOOKS = CODEX / "hooks"
ROLLOUT = next(CODEX.glob("rollout-*.jsonl"))
TOOL = approval.FINNAMON_TOOL


def payload(name: str, **inp) -> dict:
    e = json.loads((HOOKS / f"{name}.json").read_text())
    e["tool_input"] = inp or e["tool_input"]
    return e


def guard(monkeypatch, event) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(event if isinstance(event, str) else json.dumps(event)))
    try:
        cli.main(["hook", "secret-guard"])
        return 0
    except SystemExit as e:
        return e.code


# --- secret-guard on the recorded Codex payloads ----------------------------------------------------------------------

def test_the_recorded_payloads_pass_the_guard(home, monkeypatch):
    for f in sorted(HOOKS.glob("pre_tool_use_*.json")):
        assert guard(monkeypatch, json.loads(f.read_text())) == 0, f.name


def test_the_guard_blocks_each_codex_tool_shape(home, monkeypatch):
    for event in (payload("pre_tool_use_bash", command="cat ~/.finnamon/secrets.toml"),
                  payload("pre_tool_use_apply_patch", command="*** Begin Patch\n*** Update File: /Users/jane/.finnamon/secrets.toml\n@@\n-a\n+b\n*** End Patch"),
                  payload("pre_tool_use_view_image", path="/home/jane/.finnamon/chrome/Default/shot.png"),
                  payload("pre_tool_use_mcp", argv=["finnamon", "import", "x", "~/.finnamon/imports/a.csv"])):
        assert guard(monkeypatch, event) == 2, event["tool_input"]


def test_a_patch_names_its_files_relative_to_the_call_s_cwd(home, monkeypatch):
    e = payload("pre_tool_use_apply_patch", command="*** Begin Patch\n*** Add File: ../secrets.toml\n+x\n*** End Patch")
    e["cwd"] = "/Users/jane/.finnamon/assistant"
    assert guard(monkeypatch, e) == 2, "../secrets.toml from the assistant directory is the household's secrets"
    e["cwd"] = "/Users/jane/work"
    assert guard(monkeypatch, e) == 0
    for line in ("*** Delete File: ../finnamon.db", "*** Update File: notes.txt\n*** Move to: ../web-token"):
        e["cwd"], e["tool_input"] = "/Users/jane/.finnamon/assistant", {"command": f"*** Begin Patch\n{line}\n*** End Patch"}
        assert guard(monkeypatch, e) == 2, line


def test_a_guard_that_crashes_blocks_on_codex_too(home, monkeypatch):
    """Codex runs a call whose hook exits 1 (spike finding 4): every error inside the guard is an exit 2."""
    monkeypatch.setattr(approval, "protected_path", lambda *a, **k: 1 / 0)
    assert guard(monkeypatch, payload("pre_tool_use_bash")) == 2
    assert guard(monkeypatch, "{") == 2
    cfg = tomllib.loads(codex.render_config())
    assert cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"].endswith("|| exit 2"), "and a crash before Python runs too"


@pytest.mark.parametrize("rel", PROTECTED_FILES)
@pytest.mark.parametrize("prefix", HOME_FORMS)
def test_every_form_of_every_protected_path_in_codex_shapes(rel, prefix):
    path = prefix + rel
    for tool, inp in (("Bash", {"command": f"head -1 {path}"}), ("apply_patch", {"command": f"*** Begin Patch\n*** Add File: {path}\n+x\n*** End Patch"}),
                      ("view_image", {"path": path}), (TOOL, {"argv": ["finnamon", "import", "Checking", path]}), ("mcp__other__x", {"file": path})):
        assert approval.protected_path(tool, inp, home="/nonexistent"), (tool, path)
    assert approval.protected_path(TOOL, {"argv": ["finnamon", "query", "x", "--db", ".finnamon/finnamon.db"]}, home="/nonexistent")


# --- the PermissionRequest hook on Codex payloads ---------------------------------------------------------------------

def test_permission_request_on_codex_tools_keeps_its_decision_shape(home, conn, tg):
    deny = json.loads(assistant.files()[".claude/settings.json"])["permissions"]["deny"]
    e = payload("permission_request_mcp", argv=["finnamon", "sync"])
    e["tool_name"] = TOOL
    out = approval.ask(e, conn, deny=deny, env={})
    assert out == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                          "decision": {"behavior": "deny", "message": "Bash(finnamon sync *) is on the deny list"}}}
    e["tool_input"] = {"argv": ["finnamon", "status", "--x", "/Users/jane/.finnamon/web-token"]}
    assert approval.ask(e, conn, deny=deny, env={})["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert approval.ask(payload("permission_request_mcp"), conn, deny=deny, env={"FINNAMON_FROM_AGENT": "1"}) is None, "unattended: no decision"
    assert tg == []


# --- the rollout reader -----------------------------------------------------------------------------------------------

def rollout(tmp_path, prompt: str, items=()) -> Path:
    lines = [l for l in ROLLOUT.read_text().splitlines() if json.loads(l)["type"] != "event_msg"
             or json.loads(l)["payload"].get("item", {}).get("type") not in ("UserMessage", *approval.CODEX_CALLS)]
    user = {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": prompt}]}}}
    p = tmp_path / ROLLOUT.name
    p.write_text("\n".join([*lines[:9], json.dumps(user), *lines[9:], *map(json.dumps, items)]) + "\n")
    return p


def completed(item: dict) -> dict:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


def test_the_recorded_rollout_reads_as_a_dashboard_turn_with_three_finished_calls():
    entries, _ = approval._entries(str(ROLLOUT))
    assert not approval.telegram_turn(entries)
    assert approval.results(entries) == {"exec-8f29c369-d410-44d3-a7e8-b51000528f74", "exec-eb2def65-3d3f-4a6f-af42-a569ec7ca6ae", "exec-ad3a4c04-b8e7-4c40-9b3f-3f668bc9c95f"}
    assert approval.tool_use_id(entries, "Bash", {"command": "touch /tmp/spike/out/by_rule"}, set()) == "exec-8f29c369-d410-44d3-a7e8-b51000528f74"
    mcp = payload("pre_tool_use_mcp")
    assert approval.tool_use_id(entries, mcp["tool_name"], mcp["tool_input"], set()) == mcp["tool_use_id"], "the item id is the hook's tool_use_id"
    assert approval.tool_use_id(entries, "Bash", {"command": "touch /tmp/other"}, set()) is None


def test_a_telegram_turn_in_a_codex_rollout_reaches_the_phone_and_the_dashboard_answer_clears_it(home, conn, tmp_path, tg):
    seed(conn)
    assert not approval.telegram_turn(approval._entries(str(rollout(tmp_path, "what did we spend?")))[0])
    path = rollout(tmp_path, "[telegram · bill] what did we spend?")
    e = payload("permission_request_mcp")
    e["transcript_path"] = str(path)
    def poll(i):
        if i == 2:   # the call finishes at the dashboard: its McpToolCall item lands in the rollout
            with open(path, "a") as f:
                f.write(json.dumps(completed({"type": "McpToolCall", "id": "exec-1", "server": "spike", "tool": "echo", "arguments": e["tool_input"], "status": "completed"})) + "\n")
    assert run_hook(conn, e, poll) is None
    assert tg[0][0] == "send" and "mcp__spike__echo" in tg[0][2]
    assert tg[-1][0] == "edit" and "Answered at the dashboard" in tg[-1][3]


# --- the finnamon(argv) tool ------------------------------------------------------------------------------------------

def test_the_tool_s_lists_are_the_claude_allow_and_deny_lists():
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]
    allow, deny = mcp_server.rules()
    assert {f"Bash({p})" for p in allow} == {r for r in perms["allow"] if r.startswith("Bash(")}
    assert {f"Bash({p})" for p in deny} == {r for r in perms["deny"] if r.startswith("Bash(")}
    for p in allow:   # every allow rule, filled in, passes; every finnamon deny rule, filled in, is refused
        argv = p.replace("*", "x").split()
        assert mcp_server.refusal(argv) is None, p
    for p in deny:
        if p.startswith("finnamon"):
            argv = p.replace("*", "x").split()
            assert "deny list" in (mcp_server.refusal(argv) or ""), p


@pytest.mark.parametrize("argv", [["finnamon", "status"], ["finnamon", "alerts", "--sent"], ["finnamon", "query", "SELECT count(*) FROM tx_now"],
                                  ["finnamon", "budget"], ["finnamon", "networth", "--history", "--months", "6"], ["finnamon", "help", "owner"],
                                  ["finnamon", "settings", "set", "assistant", "codex"], ["finnamon", "link", "--update", "item1", "--telegram"],
                                  ["finnamon", "triage", "set", "g1", "mention", "0.9", "-"], ["finnamon", "chart", "--spec-json", '{"mark": "bar"}', "--sql", "SELECT 1"]])
def test_allowed(argv):
    assert mcp_server.refusal(argv) is None


@pytest.mark.parametrize("argv,why", [
    (["finnamon", "sync"], "deny list"), (["finnamon", "init", "--yes"], "deny list"), (["finnamon", "owner", "add", "jane"], "deny list"),
    (["finnamon", "settings", "set", "--ops", "x", "y"], "deny list"), (["finnamon", "networth", "--sync"], "deny list"),
    (["finnamon", "networth", "--history", "--months", "3", "--sync"], "deny list"), (["finnamon", "detect", "--review"], "deny list"),
    (["finnamon", "link"], "deny list"), (["finnamon", "link", "--remove", "i"], "deny list"), (["finnamon", "open"], "deny list"),
    (["finnamon", "update"], "allow list"), (["finnamon", "status", "--help"], "allow list"), (["finnamon", "link", "--update", "i"], "allow list"),
    (["finnamon", "status x"], "allow list"), (["finnamon", "alerts; rm -rf ~"], "allow list"),
    (["finnamon", "query", "SELECT 1", "--db", "~/.finnamon/finnamon.db"], "off limits"), (["finnamon", "import", "x", "$HOME/.finnamon/secrets.toml"], "off limits"),
    (["sqlite3", "x"], "starting with"), ("finnamon status", "starting with"), ([], "starting with"), (["finnamon", 3], "starting with")])
def test_refused(argv, why):
    msg = mcp_server.refusal(argv)
    assert msg and why in msg, msg
    assert "terminal" in msg or why == "starting with"


def test_triage_stays_read_only_through_the_tool(monkeypatch):
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    for argv in (["finnamon", "normal", "--alert", "1"], ["finnamon", "settings", "set", "x", "y"], ["finnamon", "import", "a", "b.csv"], ["finnamon", "detect", "--draft", "x"]):
        assert "deny list" in mcp_server.refusal(argv), argv
    assert mcp_server.refusal(["finnamon", "query", "SELECT 1"]) is None and mcp_server.refusal(["finnamon", "triage", "set", "g", "mention", "1", "-"]) is None


def test_the_tool_runs_the_real_cli_as_an_agent_against_the_household(home, conn, monkeypatch, tmp_path):
    seed(conn)
    out = mcp_server.run_argv(["finnamon", "status"])
    assert out["exit_code"] == 0, out
    assert isinstance(json.loads(out["stdout"]), dict), "stdout passes through as the CLI printed it"
    out = mcp_server.run_argv(["finnamon", "settings", "set", "assistant", "claude"])   # on the allow list; the CLI's human-only gate
    assert out["exit_code"] != 0 and "terminal" in out["stderr"], "FINNAMON_FROM_AGENT rides every call"
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    out = mcp_server.run_argv(["finnamon", "alias", "--list"])
    assert "deny list" in out["refused"], "triage's read-only list"
    assert mcp_server.run_argv(["finnamon", "sync"]) == {"refused": mcp_server.refusal(["finnamon", "sync"])}


def test_the_tool_over_mcp(home, conn):
    pytest.importorskip("mcp")
    seed(conn)
    server = mcp_server.build()
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert {"finnamon", "sql", "list_accounts"} <= tools, "the read-only tools stay"
    res = asyncio.run(server.call_tool("finnamon", {"argv": ["finnamon", "status"]}))
    content = res[0] if isinstance(res, tuple) else res
    assert json.loads(content[0].text)["exit_code"] == 0
    res = asyncio.run(server.call_tool("finnamon", {"argv": ["finnamon", "sync"]}))
    content = res[0] if isinstance(res, tuple) else res
    assert "a person runs this in a terminal" in json.loads(content[0].text)["refused"]


def test_codex_auto_approves_the_tool_and_sees_only_it():
    cfg = tomllib.loads(codex.render_config())["mcp_servers"]["finnamon"]
    assert cfg["command"] == "finnamon" and cfg["args"] == ["serve"]
    assert cfg["enabled_tools"] == ["finnamon"] and cfg["tools"]["finnamon"]["approval_mode"] == "approve"
    assert cfg["env"]["FINNAMON_FROM_AGENT"] == "1" and {"FINNAMON_TRIAGE", "FINNAMON_IMPORT_SESSION"} <= set(cfg["env_vars"])


def test_agents_md_says_finnamon_is_the_tool_on_codex():
    agents = assistant.files()["AGENTS.md"].decode()
    assert agents.split("\n\n")[1] == assistant.CODEX_TOOL_NOTE.strip() and "`finnamon` tool" in agents
    assert "finnamon` tool" not in assistant.files()["CLAUDE.md"].decode(), "Claude households are unchanged"
