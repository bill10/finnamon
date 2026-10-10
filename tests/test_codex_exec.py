"""Headless runs on Codex (Codex card 4 of 6): `codex exec --json` / `exec resume` for triage and the daemon's conversation,
the JSONL parser, classify_error's Codex wording. Everything runs against the stub in tests/fixtures/codex/bin/codex,
which replays JSONL recorded from real Codex 0.157 runs (exec-triage.jsonl and exec-resume.jsonl: a triage turn that
called the finnamon tool, and its `exec resume` continuation, under a scratch FINNAMON_HOME)."""
import json
from pathlib import Path

import pytest

from finnamon import agent_runner, codex, daemon, store, triage
from tests.conftest import seed

CODEX = Path(__file__).parent / "fixtures" / "codex"
STUB = CODEX / "bin" / "codex"


@pytest.fixture
def household(conn, monkeypatch, tmp_path):
    """A Codex household (the setting written directly), set up by install."""
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(STUB))
    monkeypatch.setenv("CODEX_FAKE_LOG", str(tmp_path / "codex.log"))
    conn.execute("INSERT INTO settings(account_id, key, value) VALUES ('*', 'assistant', 'codex')")
    codex.install()
    seed(conn)
    return conn


def calls(tmp_path) -> list[dict]:
    return [json.loads(l) for l in (tmp_path / "codex.log").read_text().splitlines()]


# --- the parser, on recorded runs -------------------------------------------------------------------------------------

def test_parser_reads_a_real_triage_turn():
    r = agent_runner._codex_result((CODEX / "exec-triage.jsonl").read_text())
    assert r.ok and r.session_id == "01a10e8e-ccf4-75c1-a418-f561a9a99c63"
    assert r.text.startswith("Promoted the $412.50 Zelle payment"), "the last agent_message is the reply, not the commentary before it"
    assert r.usage["output_tokens"] == 537


def test_parser_reads_a_failed_turn():
    r = agent_runner._codex_result((CODEX / "exec-failed.jsonl").read_text())
    assert not r.ok and "at capacity" in r.error and agent_runner.classify_error(r.error) == "busy"


@pytest.mark.parametrize("err, kind", [
    ("Selected model is at capacity. Please try a different model.", "busy"),
    ("You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), or try again at 3:42 PM.", "busy"),
    ("exceeded retry limit, last status: 429 Too Many Requests", "busy"),
    ('API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', "busy"),   # Claude's wording
    ("Your access token could not be refreshed because your refresh token has expired. Please log out and sign in again.", "login expired"),
    ("unexpected status 401 Unauthorized", "login expired"),
    ("Not logged in", "login expired"),
    ('Error loading config.toml: invalid type: string "maybe", expected a boolean\nin `features`', "bad config"),   # real 0.157 wording
    ("Error loading config.toml: unknown variant `sometimes`, expected one of `untrusted`, `on-failure`", "bad config"),
    # Value: protects=busy and bad config are checked before login's broad words (token, auth); fails_when=the login branch moves ahead and a usage limit or a config typo reads as "login expired"; why_new=no row carried both kinds of words; seam=none
    ("You've hit your usage limit: 1,204,331 tokens used this week on your plan.", "busy"),
    ("Error loading config.toml: unknown field `auth_mode` in `model_providers.openai`", "bad config"),
    ((CODEX / "exec-resume-gone.stderr").read_text(), "session lost"),
    ("No conversation found with session id gone", "session lost"),
    ("Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying.", "session lost"),
    ("timeout after 120s", "timeout"),
    ("codex not on PATH", "not installed"),
    ("something else", "error"),
])
def test_classify_error(err, kind):
    assert agent_runner.classify_error(err) == kind


# --- the command line -------------------------------------------------------------------------------------------------

def test_every_codex_run_is_locked_on_its_command_line(home):
    fresh = agent_runner._codex_cmd("codex", "-starts with a dash", None, agent_runner.TRIAGE_DISALLOWED)
    resumed = agent_runner._codex_cmd("codex", "hi", "tid-1", ())
    assert fresh[:2] == ["codex", "exec"] and fresh[-4:] == ["-C", str(agent_runner.cwd()), "--", "-starts with a dash"]
    assert resumed[:3] == ["codex", "exec", "resume"] and resumed[-3:] == ["--", "tid-1", "hi"] and "-C" not in resumed, "`exec resume` has no -C"
    for cmd in (fresh, resumed):
        assert "--json" in cmd and "--skip-git-repo-check" in cmd and "--ignore-rules" in cmd
        assert 'default_permissions="finnamon"' in cmd and "features.apps=false" in cmd
        assert 'approval_policy="never"' in cmd and 'web_search="disabled"' in cmd and f'permissions.{codex.PROFILE}.extends=":read-only"' in cmd
        assert "--ignore-user-config" not in cmd, "it would skip CODEX_HOME/config.toml: the profile, the hooks and the tool"
        assert not any("danger" in a or "bypass" in a or a in ("-s", "--sandbox") for a in cmd), "never the legacy sandbox_mode (spike finding 1)"


# --- run(), triage and the daemon on the stub -------------------------------------------------------------------------

def test_run_uses_the_sealed_home_and_registers_the_child(household, tmp_path, monkeypatch):
    seen = []

    class Recording(set):
        def add(self, pid):
            seen.append(pid)
            super().add(pid)
    monkeypatch.setattr(agent_runner, "_LIVE", Recording())
    r = agent_runner.run("hi")
    assert r.ok and r.session_id == "01a10e2d-9a37-76c0-9e68-b563e3f90b15" and r.text.startswith("1. Succeeded")
    assert seen and not agent_runner._LIVE, "terminate_all() sees a Codex child while it runs, and a finished one is dropped"
    c = calls(tmp_path)[-1]
    assert c["CODEX_HOME"] == str(codex.home()) and c["HOME"] == str(codex.seal_home())


def test_a_failed_turn_is_an_error_with_its_reason(household, monkeypatch):
    monkeypatch.setenv("CODEX_FAKE_JSONL", str(CODEX / "exec-failed.jsonl"))
    r = agent_runner.run("hi")
    assert not r.ok and r.error.startswith("Selected model is at capacity") and r.returncode == 1


def test_triage_runs_the_skill_the_codex_way(household, tmp_path, monkeypatch):
    monkeypatch.setattr(triage, "groups", lambda c: [{"group": "g"}])
    assert agent_runner.harness_problems() == []
    res = triage.run_if_needed(household)
    assert res["ran"] and res["ok"], res
    c = calls(tmp_path)[-1]
    assert c["argv"][-1] == "$triage" and c["FINNAMON_TRIAGE"] == "1", "the tool reads FINNAMON_TRIAGE through env_vars and refuses writes"


def test_harness_refuses_a_config_that_unseals_the_run(household):
    cfg = codex.home() / "config.toml"
    cfg.write_text('sandbox_mode = "danger-full-access"\n' + cfg.read_text())
    assert any("sandbox_mode" in p for p in agent_runner.harness_problems()), "the daemon and triage refuse what doctor flags"


def test_a_logged_out_codex_is_not_a_harness_problem(household, monkeypatch):
    monkeypatch.setenv("CODEX_FAKE_LOGGED_OUT", "1")
    assert agent_runner.harness_problems() == [], "the daemon must still start and sync; the run itself reports the login"


def test_a_project_codex_layer_is_a_harness_problem(household):
    from finnamon import assistant
    (assistant.dir() / ".codex").mkdir()
    assert any(".codex" in p for p in agent_runner.harness_problems())


def test_a_turn_that_never_completed_is_not_a_reply():
    r = agent_runner._codex_result('{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n')
    assert not r.ok and r.error == agent_runner.CODEX_INCOMPLETE and r.session_id == "t"


def test_a_completed_turn_is_the_reply_even_on_a_nonzero_exit(household, tmp_path, monkeypatch):
    fake = tmp_path / "codex-exit1"
    fake.write_text(f"#!/bin/sh\ncat {CODEX / 'exec.jsonl'}\necho 'mcp shutdown' >&2\nexit 1\n"); fake.chmod(0o755)
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(fake))
    r = agent_runner.run("hi")
    assert r.ok and r.text.startswith("1. Succeeded") and r.returncode == 1


def test_an_unreadable_database_keeps_a_codex_household_on_codex(household, monkeypatch):
    monkeypatch.setattr(agent_runner.store, "assistant_kind", lambda c: (_ for _ in ()).throw(agent_runner.sqlite3.OperationalError("database is locked")))
    assert agent_runner.kind() == "codex"


def test_an_error_the_turn_recovered_from_is_not_a_failure():
    out = '{"type":"thread.started","thread_id":"t"}\n{"type":"error","message":"Reconnecting... 1/5"}\n' \
          '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n{"type":"turn.completed","usage":{}}\n'
    r = agent_runner._codex_result(out)
    assert r.ok and r.text == "ok" and r.session_id == "t"
