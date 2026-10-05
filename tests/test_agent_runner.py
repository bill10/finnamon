"""The agent seam (Codex card 1 of 6): the `assistant` setting, agent_runner's dispatch on it, and the codex stub."""
import json
import subprocess
from pathlib import Path

import pytest

from finnamon import agent_runner, claude_runner, cli, store

CODEX = Path(__file__).parent / "fixtures" / "codex"


def test_claude_runner_is_agent_runner():
    assert claude_runner is agent_runner, "the old name is the same module, so a monkeypatch through either reaches both"


def test_dispatch_picks_claude_by_default(conn, fake_claude, monkeypatch):
    seen = []
    real = subprocess.Popen
    monkeypatch.setattr(agent_runner.subprocess, "Popen", lambda cmd, **kw: (seen.append((cmd, kw["env"])), real(cmd, **kw))[1])
    assert agent_runner.kind() == "claude"
    r = agent_runner.run("hi")
    assert r.ok and r.text == "ok" and r.session_id == "sess-1"
    cmd, env = seen[0]
    assert cmd[0] == str(fake_claude) and cmd[1:3] == ["-p", "hi"]
    assert env["FINNAMON_FROM_AGENT"] == "1", "the human-only gates' marker rides every run"


def test_assistant_setting(conn, monkeypatch, capsys):
    cli.main(["settings", "set", "assistant", "claude"])
    assert store.assistant_kind(conn) == "claude"
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "assistant", "codex"])
    assert "Codex support is being built" in capsys.readouterr().err
    for bad in (["settings", "set", "assistant", "gemini"], ["settings", "set", "assistant", "claude", "--account", "acct"]):
        with pytest.raises(SystemExit):
            cli.main(bad)
    monkeypatch.setenv("FINNAMON_FROM_AGENT", "1")
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "assistant", "claude"])
    assert "by a person at a terminal" in capsys.readouterr().err
    monkeypatch.delenv("FINNAMON_FROM_AGENT")
    capsys.readouterr()
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out)["agent"] == "claude"


def test_a_codex_household_is_refused_not_run_as_claude(conn, fake_claude, monkeypatch):
    conn.execute("INSERT INTO settings(account_id, key, value) VALUES ('*', 'assistant', 'codex')")   # what card 5 will let `settings set` write
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(CODEX / "bin" / "codex"))
    assert agent_runner.kind() == "codex"
    assert agent_runner.harness_problems() == [agent_runner.CODEX_PENDING], "the daemon and triage refuse to start"
    with pytest.raises(NotImplementedError):
        agent_runner.run("hi")
    conn.execute("UPDATE settings SET value='gemini' WHERE key='assistant'")
    assert agent_runner.kind() == "claude", "a word this release does not know is Claude, the default"


def test_codex_stub_is_wired(monkeypatch):
    stub = CODEX / "bin" / "codex"
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(stub))
    assert agent_runner.binary("codex") == str(stub)
    out = subprocess.run([agent_runner.binary("codex"), "exec", "--json", "hi"], capture_output=True, text=True, check=True).stdout
    r = agent_runner._codex_result(out)
    assert r.ok and r.session_id == "01a10e2d-9a37-76c0-9e68-b563e3f90b15"
    assert r.text.startswith("1. Succeeded."), "the last agent_message is the reply, not the commentary before the tools ran"
    failed = agent_runner._codex_result('{"type":"thread.started","thread_id":"t"}\n{"type":"turn.failed","error":{"message":"usage limit"}}\n')
    assert not failed.ok and failed.error == "usage limit" and failed.session_id == "t"
