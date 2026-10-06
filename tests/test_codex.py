"""The Codex bundle and home (Codex card 2 of 6): AGENTS.md, .agents/skills and config.toml generated from the Claude files,
the shared login, the pinned hook trust, and init/doctor/update/channel for a household that set Codex up. Everything runs
against the stub in tests/fixtures/codex/bin/codex; the tests marked `real_codex` run the installed codex's local
subcommands (no login, no model call) under a scratch CODEX_HOME. Regenerate the goldens with FINNAMON_REGEN_GOLDEN=1."""
import json
import os
import platform
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from finnamon import agent_runner, assistant, cli, codex, config, scheduler, store

CODEX = Path(__file__).parent / "fixtures" / "codex"
GOLDEN = CODEX / "golden"
STUB = CODEX / "bin" / "codex"
REAL = shutil.which("codex")
real_codex = pytest.mark.skipif(not REAL, reason="needs the codex CLI installed")


def golden(name: str, text: str) -> None:
    p = GOLDEN / name
    if os.environ.get("FINNAMON_REGEN_GOLDEN"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    assert p.read_text() == text, f"{name} changed: if that is intended, FINNAMON_REGEN_GOLDEN=1 pytest tests/test_codex.py and review the diff"


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(STUB))
    return str(STUB)


@pytest.fixture
def user_login():
    """The person's own Codex login, as a scratch file (conftest points codex.user_auth there)."""
    p = codex.user_auth()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"tokens": "theirs"}')
    return p


def normalized(text: str) -> str:
    return text.replace(str(config.home()), "$FINNAMON_HOME").replace(str(Path.home()), "$HOME")


# --- the generated bundle ---------------------------------------------------------------------------------------------

def test_agents_md_and_skills_are_generated_from_the_claude_files(home):
    files = assistant.files()
    agents = files["AGENTS.md"].decode()
    golden("AGENTS.md", agents)
    assert len(files["AGENTS.md"]) <= assistant.AGENTS_MD_MAX
    assert ".claude/skills" not in agents and ".agents/skills/finnamon/SKILL.md" in agents
    assert "| `$triage` |" in agents and not re.search(r"(?<![\w/])/triage", agents)
    assert "Telegram channel" not in agents and "<channel source" not in agents, "the channel plugin is Claude Code's alone"
    assert (home / "assistant" / "AGENTS.md").read_bytes() == files["AGENTS.md"], "install writes it beside CLAUDE.md"
    for name in ("finnamon", "triage", "import-browser"):
        skill = files[f".agents/skills/{name}/SKILL.md"].decode()
        golden(f"skills/{name}/SKILL.md", skill)
        assert ".claude/skills" not in skill and "Claude Code's Telegram channel" not in skill
    finnamon = files[".agents/skills/finnamon/SKILL.md"].decode()
    assert "`<channel source=" not in finnamon and "the channel doesn't tell you" not in finnamon, "the whole bullet goes, not just its line"
    assert "NO_REPLY" in finnamon, "the bullets around it stay"
    assert 'codex exec "$triage"' in files[".agents/skills/triage/SKILL.md"].decode()


def test_agents_md_over_codexs_limit_is_refused():
    with pytest.raises(ValueError, match="Codex reads only"):
        assistant.codex_bundle({"CLAUDE.md": b"x" * (assistant.AGENTS_MD_MAX + 1)})


def test_config_toml_is_generated_from_settings_json(home):
    text = codex.render_config()
    golden("config.toml", normalized(text))
    cfg = tomllib.loads(text)
    assert "sandbox_mode" not in cfg, "never: under it an allow rule escapes the sandbox (spike finding 1)"
    assert cfg["default_permissions"] == "finnamon" and cfg["permissions"]["finnamon"]["extends"] == ":workspace"
    fs = cfg["permissions"]["finnamon"]["filesystem"]
    assert fs[":workspace_roots"] == "read" and fs[str(assistant.dir())] == "read", "the assistant directory is read-only"
    for p in (config.secrets_path(), config.db_path(), home / "backups", config.web_token_path(), home / "imports", home / "codex" / "sessions",
              home / "codex" / "auth.json", Path.home() / ".agent-browser", Path.home() / ".claude" / "channels"):
        assert fs[str(p)] == "deny", p
    assert cfg["web_search"] == "disabled" and cfg["approval_policy"] == "on-request" and cfg["cli_auth_credentials_store"] == "file"
    assert cfg["features"]["apps"] is False and cfg["features"]["hooks"] is True
    assert cfg["shell_environment_policy"]["set"]["FINNAMON_FROM_AGENT"] == "1" and cfg["mcp_servers"]["finnamon"]["env"]["FINNAMON_FROM_AGENT"] == "1"
    assert cfg["projects"][str(assistant.dir())]["trust_level"] == "trusted"
    hooks = cfg["hooks"]
    assert [h["command"] for g in hooks["PreToolUse"] for h in g["hooks"]] == ["finnamon hook secret-guard || exit 2"], "reply-guard is the channel plugin's"
    assert hooks["PermissionRequest"][0]["hooks"][0] == {"type": "command", "command": "finnamon hook permission", "timeout": 660}
    assert codex.config_problems() == [f"cannot read {home / 'codex' / 'config.toml'}: [Errno 2] No such file or directory: '{home / 'codex' / 'config.toml'}'"]


def test_config_problems_flag_what_would_unseal_the_session(home, stub):
    codex.install()
    assert codex.config_problems() == []
    cfg = home / "codex" / "config.toml"
    cfg.write_text('sandbox_mode = "danger-full-access"\n' + cfg.read_text())
    probs = codex.config_problems()
    assert any("full access" in p for p in probs) and any("sandbox_mode" in p for p in probs)


@pytest.mark.parametrize("edit, says", [
    (lambda t: t.replace("hooks = true", "hooks = false"), "hooks are off"),
    (lambda t: t + '\n[mcp_servers.extra]\ncommand = "x"\n', "other than finnamon"),
    (lambda t: t.replace("[shell_environment_policy]", '[shell_environment_policy]\ninclude_only = ["PATH"]'), "FINNAMON_FROM_AGENT"),
    (lambda t: t.replace(f'"{Path.home() / ".ssh"}" = "deny"', ""), ".ssh"),
])
def test_config_problems_catch_an_edit_the_command_line_does_not_pin(home, stub, edit, says):
    # Value: protects=the harness check (daemon, triage, every dashboard (re)start) refusing a config.toml that drops a hook, adds an unsealed MCP server, strips the agent marker or a deny; fails_when=one of those checks is removed; why_new=only sandbox_mode/danger were tested; seam=none
    codex.install()
    cfg = home / "codex" / "config.toml"
    cfg.write_text(edit(cfg.read_text()))
    assert any(says in p for p in codex.config_problems()), codex.config_problems()


# --- install: config, pinned trust, the shared login ------------------------------------------------------------------

def test_install_pins_the_hooks_and_links_the_login(home, stub, user_login):
    res = codex.install()
    link = home / "codex" / "auth.json"
    assert res["auth"] == "linked" and link.is_symlink() and os.readlink(link) == str(user_login)
    assert res["pinned"] == 2 and codex.problems() == []
    state = tomllib.loads((home / "codex" / "config.toml").read_text())["hooks"]["state"]
    assert sorted(k.split(":", 1)[1] for k in state) == ["permission_request:0:0", "pre_tool_use:0:0"]
    again = codex.install()
    assert again["written"] == [] and not again["changed"], "a steady state writes nothing, so update restarts nothing"
    assert user_login.read_text() == '{"tokens": "theirs"}', "the person's login is only ever linked to"


def test_an_edited_config_goes_to_bak_and_a_stale_hash_is_refused(home, stub):
    codex.install()
    cfg = home / "codex" / "config.toml"
    cfg.write_text(cfg.read_text().replace("finnamon hook permission", "true"))   # an edit Codex no longer trusts
    assert "permission_request" in codex.problems()[0] and "finnamon update" in codex.problems()[0]
    res = codex.install()
    assert res["backed_up"] == [str(cfg) + ".bak"] and "true" in (home / "codex" / "config.toml.bak").read_text()
    assert codex.problems() == [] and "finnamon hook permission" in cfg.read_text(), "the release wins, re-pinned"


def test_link_states(home, user_login):
    link = home / "codex" / "auth.json"
    assert codex.link_auth() == "linked"
    user_login.unlink()
    assert codex.link_auth() == "none" and link.is_symlink(), "a dangling link is reported, never replaced by a file"
    user_login.write_text("{}")
    link.unlink(); link.symlink_to(home / "elsewhere.json")
    assert codex.link_auth() == "relinked" and os.readlink(link) == str(user_login)
    link.unlink(); link.write_text('{"own": true}')
    assert codex.link_auth() == "own" and link.read_text() == '{"own": true}', "Finnamon's own login (the fallback) stays"


def test_no_login_to_share_leaves_no_link(home, stub):
    assert codex.install()["auth"] == "none" and not (home / "codex" / "auth.json").exists()


def test_finnamon_never_logs_codex_out():
    """`codex logout` revokes the tokens the link shares: the person would be logged out everywhere."""
    assert "logout" not in Path(codex.__file__).read_text().replace("Never `codex logout`", "").replace("never `codex logout`", "")


def test_the_seal_hides_the_persons_skills(home, stub):
    codex.install()
    assert codex.leaked_skills(str(STUB)) == []
    leak = codex.seal_home() / ".agents" / "skills" / "mine"; leak.mkdir(parents=True); (leak / "SKILL.md").write_text("---\nname: mine\n---\n")
    assert codex.leaked_skills(str(STUB)) == [str(leak / "SKILL.md")]
    env = codex.env()
    assert env["HOME"] == str(codex.seal_home()) and env["CODEX_HOME"] == str(home / "codex") and env["FINNAMON_HOME"] == str(home)


def test_a_claude_household_gets_no_codex_home_and_no_path_change(home, stub):
    cli._install_bundle()
    assert not (home / "codex").exists() and not codex.configured()
    assert str(STUB.parent) not in scheduler.env_path().split(":")
    codex.install()
    assert str(STUB.parent) in scheduler.env_path().split(":"), "the jobs' PATH finds codex once it is set up"


def test_update_rewrites_the_config_and_restarts(home, stub, capsys):
    codex.install()
    cfg = home / "codex" / "config.toml"
    cfg.write_text(cfg.read_text() + "\n# mine\n")
    assert cli._install_bundle()["changed"], "a rewritten config restarts the sessions that read it"
    assert "config.toml.bak" in capsys.readouterr().out


# --- the harness check, channel, init, doctor -------------------------------------------------------------------------

def test_harness_check_for_a_codex_household(conn, stub):
    conn.execute("INSERT INTO settings(account_id, key, value) VALUES ('*', 'assistant', 'codex')")   # what `settings set` writes once Codex is set up
    assert any("config.toml" in p for p in agent_runner.harness_problems())
    codex.install()
    assert agent_runner.harness_problems() == [], "no Claude trust is asked of a Codex household"


def test_channel_mode_is_refused_for_codex(conn):
    conn.execute("INSERT INTO settings(account_id, key, value) VALUES ('*', 'assistant', 'codex')")
    with pytest.raises(SystemExit):
        cli.main(["channel", "on"])
    assert store.get_state(conn, "inbound") != "channel"


def init(monkeypatch, answers, *argv):
    from finnamon import owners, plaid_api
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    monkeypatch.setattr(cli, "_setup_dashboard", lambda a: True)
    it = iter(answers); monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
    cli.main(["init", "--owner", "bill", *argv])


def test_init_codex_path_selects_codex_and_forces_session(home, tg, stub, fake_claude, monkeypatch, capsys):
    init(monkeypatch, ["cid", "prod-sec", "", "123:token", "codex", "n"])
    out = capsys.readouterr().out
    assert "2 hook(s) trusted" in out and "logging in for Finnamon alone" in out and (home / "codex" / "auth.json").read_text() == '{"fake": true}', "no login to share: codex login under Finnamon's home"
    assert "Codex runs the household's assistant" in out
    conn = store.connect()
    assert store.assistant_kind(conn) == "codex" and store.get_state(conn, "inbound") == "session"


def test_settings_set_assistant_codex_only_once_it_would_start_sealed(conn, stub, monkeypatch, capsys):
    restarted = []
    monkeypatch.setattr(scheduler, "installed", lambda job: True)
    monkeypatch.setattr(scheduler, "restart", lambda jobs: restarted.append(jobs))
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "assistant", "codex"])
    assert "Codex is not ready" in capsys.readouterr().err and store.assistant_kind(conn) == "claude", "not set up here: refused"
    codex.install()
    store.set_state(conn, "inbound", "channel")
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "assistant", "codex"])
    assert "finnamon channel session" in capsys.readouterr().err, "channel mode is Claude Code's plugin"
    store.set_state(conn, "inbound", "session")
    cfg = codex.home() / codex.CONFIG
    good = cfg.read_text()
    cfg.write_text(good.replace('web_search = "disabled"', 'web_search = "live"'))
    with pytest.raises(SystemExit):
        cli.main(["settings", "set", "assistant", "codex"])
    assert "web search" in capsys.readouterr().err, "a config that would unseal it is refused too"
    cfg.write_text(good)
    cli.main(["settings", "set", "assistant", "codex"])
    assert store.assistant_kind(conn) == "codex" and restarted == [["web"]], "the dashboard restarts onto the new CLI"
    cli.main(["settings", "set", "assistant", "codex"])
    assert restarted == [["web"]], "no change, no restart"
    cli.main(["settings", "set", "assistant", "claude"])
    assert store.assistant_kind(conn) == "claude" and len(restarted) == 2, "and back, with no check"
    def down(jobs): raise RuntimeError("launchctl said no")
    monkeypatch.setattr(scheduler, "restart", down)
    cli.main(["settings", "set", "assistant", "CODEX"])
    err = capsys.readouterr().err
    assert store.assistant_kind(conn) == "codex" and "could not restart the dashboard" in err, "a failed restart warns; the setting stands"


def test_init_with_no_codex_says_how_to_get_it(home, tg, fake_claude, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"loggedIn": True, "email": "a@b"}))
    init(monkeypatch, ["cid", "prod-sec", "", "123:token", "n"])
    assert codex.INSTALL_HINT in capsys.readouterr().out and not codex.configured()


def doctor(capsys) -> str:
    with pytest.raises(SystemExit):
        cli.main(["doctor"])   # a scratch box has problems elsewhere (no scheduler, no keys): only the Codex lines matter here
    return capsys.readouterr().out


def lines(out: str) -> dict[str, str]:
    return {l[2:].split(":", 1)[0]: l for l in out.splitlines() if l[2:].startswith(("Codex", "MCP package"))}


def test_doctor_codex_lines(home, stub, user_login, fake_claude, capsys, monkeypatch):
    assert "Codex" not in doctor(capsys), "a Claude household that never set Codex up sees no Codex lines"
    codex.install()
    got = lines(doctor(capsys))
    for what in ("Codex", "Codex login", "Codex hooks", "Codex config", "Codex skills"):
        assert got[what].startswith("✓"), got[what]
    assert "shared:" in got["Codex login"]
    (home / "codex" / "auth.json").unlink()
    (home / "codex" / "auth.json").symlink_to(home / "gone.json")
    monkeypatch.setenv("CODEX_FAKE_VERSION", "0.160.2")
    monkeypatch.setenv("CODEX_FAKE_NO_HOOKS", "1")
    got = lines(doctor(capsys))
    assert "re-made" in got["Codex login"] and os.readlink(home / "codex" / "auth.json") == str(user_login), "doctor re-makes a broken link"
    assert got["Codex"].startswith("!") and "newer than 0.157.0" in got["Codex"]
    assert got["Codex hooks"].startswith("✗")
    monkeypatch.setenv("CODEX_FAKE_VERSION", "0.150.0")
    monkeypatch.setenv("CODEX_FAKE_LOGGED_OUT", "1")
    cfg = home / "codex" / "config.toml"; cfg.write_text(cfg.read_text().replace("secret-guard", "secret-guard --off"))
    got = lines(doctor(capsys))
    assert got["Codex"].startswith("✗") and got["Codex login"].startswith("✗") and "not trust" in got["Codex config"]


# --- the real codex: local subcommands only, scratch CODEX_HOME -------------------------------------------------------

@real_codex
def test_real_codex_pins_trust_with_hooks_list(home, monkeypatch):
    monkeypatch.setenv("FINNAMON_CODEX_BIN", REAL)
    res = codex.install()
    assert res["pinned"] == 2 and codex.problems() == [] and codex.config_problems() == []
    assert codex.leaked_skills(REAL) == [], "Codex reads the user skill root from HOME: the sealed home has none"
    assert codex.version(REAL) >= codex.MIN_VERSION and codex.features(REAL).get("hooks") is True
    assert codex.features(REAL).get("apps") is False, "the ChatGPT-apps MCP server is off"


@real_codex
@pytest.mark.skipif(platform.system() != "Darwin", reason="codex sandbox is seatbelt here")
def test_real_codex_profile_denies_the_households_secrets(home, monkeypatch):
    monkeypatch.setenv("FINNAMON_CODEX_BIN", REAL)
    codex.install()
    config.secrets_path().write_text("[plaid]\n"); config.db_path().write_text("db")
    r = subprocess.run([REAL, "sandbox", "-P", codex.PROFILE, "-C", str(assistant.dir()), "--", "sh", "-c",
                        f"cat {config.secrets_path()}; cat {config.db_path()}; touch {assistant.dir() / 'AGENTS.md'}; echo HOME=$HOME"],
                       capture_output=True, text=True, env=codex.env(), timeout=60)
    assert r.stderr.count("Operation not permitted") == 3 and "[plaid]" not in r.stdout and "db" not in r.stdout.split("HOME=")[0]
    assert f"HOME={Path.home()}" in r.stdout, "tool commands get the real HOME back"


def test_install_keeps_the_pins_when_codex_cannot_answer(home, stub, monkeypatch):
    codex.install()
    pins = codex.pinned_state()
    assert len(pins) == 2
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(home / "gone"))   # an update from a shell without codex on PATH
    codex.install()
    assert codex.pinned_state() == pins
    monkeypatch.setenv("FINNAMON_CODEX_BIN", str(STUB))
    monkeypatch.setattr(codex, "app_server", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crashed")))
    codex.install()
    assert codex.pinned_state() == pins


def test_app_server_reads_replies_that_arrive_together(home, tmp_path):
    fake = tmp_path / "codex"
    fake.write_text('#!/bin/sh\nread a; read b; read c\nprintf \'{"id":0,"result":{}}\\n{"id":1,"result":{"data":[]}}\\n\'\nsleep 30\n')
    fake.chmod(0o755)
    assert codex.app_server(str(fake), "hooks/list", {}, timeout=5) == {"data": []}
    fake.write_text('#!/bin/sh\nread a; read b; read c\necho \'"not an object"\'\necho \'{"id":1,"error":"boom"}\'\nsleep 30\n')
    with pytest.raises(RuntimeError, match="boom"):
        codex.app_server(str(fake), "hooks/list", {}, timeout=5)
