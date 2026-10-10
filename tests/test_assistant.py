"""The assistant bundle: shipped in the package, installed to FINNAMON_HOME/assistant, and the cwd of every household
`claude`. The checkout is developer-only."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from finnamon import assistant, claude_runner, cli, config, detect, scheduler, store
from tests.conftest import seed

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def own_dir(home, monkeypatch):
    """No pre-installed assistant directory (conftest writes one for every test): these exercise the install itself."""
    import shutil
    shutil.rmtree(home / "assistant", ignore_errors=True)
    assistant.untrust(home / "assistant")
    assert not (home / "assistant").exists() and assistant.dir() == home / "assistant" and not assistant.trusted()
    return home / "assistant"


def test_install_writes_the_bundle_to_finnamon_home_and_nothing_else(own_dir, home):
    res = assistant.install()
    assert res["first"] and res["changed"] and not res["backed_up"] and Path(res["dir"]) == own_dir
    bundle = assistant.files()
    assert {"CLAUDE.md", ".claude/settings.json", ".claude/skills/finnamon/SKILL.md", ".claude/skills/triage/SKILL.md", ".claude/skills/import-browser/SKILL.md"} <= set(bundle)
    for rel, data in bundle.items():
        assert (own_dir / rel).read_bytes() == data
    # claude-config and home are conftest's: its stand-in for ~/.claude.json and the autouse fixture's own scratch home
    assert sorted(os.listdir(home)) == ["assistant", "claude-config", "home"], "the bundle goes under assistant/ and touches nothing else in FINNAMON_HOME"
    fresh = home / "box" / "finnamon-home"
    assert assistant.install(fresh / "assistant")["first"] and oct(fresh.stat().st_mode & 0o777) == "0o700", "a home install creates is 0700, like init makes it"
    assert not list(own_dir.rglob("*.tmp")), "written atomically: no temp file left"
    assert len(assistant.problems()) == 1 and "not trusted" in assistant.problems()[0], "the files alone are not enough: claude ignores an untrusted directory's settings"
    assert assistant.trust() is True and assistant.trust() is False and assistant.problems() == []
    again = assistant.install()
    assert not again["changed"] and not again["first"] and again["written"] == [] and again["backed_up"] == []


def test_update_keeps_a_local_edit_as_bak_and_overwrites_only_what_the_release_changed(scheduled, own_dir, monkeypatch):
    assistant.install()
    skill = own_dir / ".claude/skills/finnamon/SKILL.md"
    triage = own_dir / ".claude/skills/triage/SKILL.md"
    skill.write_text("# the household's own version\n")
    triage.write_text("# tuned triage\n")
    # the next release changes the finnamon skill and CLAUDE.md, but ships the same triage skill as before
    new = dict(assistant.files())
    new[".claude/skills/finnamon/SKILL.md"] = b"# a newer skill\n"
    new["CLAUDE.md"] = b"# newer instructions\n"
    monkeypatch.setattr(assistant, "files", lambda: new)
    res = assistant.install()
    assert res["changed"] and res["backed_up"] == [str(skill) + ".bak"], "an edited file the release changes is replaced, with the edit kept beside it"
    assert (own_dir / ".claude/skills/finnamon/SKILL.md.bak").read_text() == "# the household's own version\n"
    assert skill.read_bytes() == b"# a newer skill\n" and (own_dir / "CLAUDE.md").read_bytes() == b"# newer instructions\n"
    assert res["kept"] == [str(triage)] and triage.read_text() == "# tuned triage\n", "an edited file the release leaves alone stays theirs"
    assert not triage.with_name("SKILL.md.bak").exists()
    # a directory nothing wrote before (no manifest): every differing file is treated as theirs
    (own_dir / assistant.MANIFEST).unlink()
    (own_dir / "CLAUDE.md").write_text("hand-written")
    res = assistant.install()
    assert set(res["backed_up"]) == {str(own_dir / "CLAUDE.md.bak"), str(triage) + ".bak"} and (own_dir / "CLAUDE.md").read_bytes() == b"# newer instructions\n"


def test_ensure_writes_only_a_directory_nothing_wrote_before(own_dir):
    own_dir.mkdir()   # an existing but empty directory is still a first install
    assert assistant.ensure()["first"] and (own_dir / assistant.MANIFEST).is_file()
    (own_dir / "CLAUDE.md").write_text("edited")
    assert assistant.ensure() is None and (own_dir / "CLAUDE.md").read_text() == "edited"
    (own_dir / ".claude/skills/triage/SKILL.md").unlink()   # an interrupted install: the daemon repairs it, and the edit still stands
    res = assistant.ensure()
    assert res and not res["first"] and (own_dir / ".claude/skills/triage/SKILL.md").is_file() and (own_dir / "CLAUDE.md").read_text() == "edited"


def test_a_file_a_release_drops_leaves_unless_the_household_changed_it(own_dir, monkeypatch):
    assistant.install()
    gone = own_dir / ".claude/skills/import-browser/SKILL.md"
    theirs = own_dir / ".claude/skills/triage/SKILL.md"; theirs.write_text("tuned")
    new = {k: v for k, v in assistant.files().items() if "import-browser" not in k and "triage" not in k}
    monkeypatch.setattr(assistant, "files", lambda: new)
    res = assistant.install()
    codex_gone = [str(own_dir / f".agents/skills/{n}/SKILL.md") for n in ("import-browser", "triage")]   # generated, so never theirs
    assert sorted(res["removed"]) == sorted([str(gone), *codex_gone]) and not gone.exists() and res["changed"]
    assert res["orphaned"] == [str(theirs)] and res["kept"] == [] and theirs.read_text() == "tuned"
    assert set(json.loads((own_dir / assistant.MANIFEST).read_text())) == set(new)


def test_the_permission_set_is_the_releases_and_the_daemon_restores_it(own_dir):
    """settings.json seals unattended runs over bank memos: an edit there is kept as .bak, never kept in place."""
    assistant.install()
    settings = own_dir / ".claude/settings.json"
    settings.write_text('{"permissions": {"allow": ["Bash(*)"]}}')
    res = assistant.install()   # the same release again: a skill edit would stand, this does not
    assert res["backed_up"] == [str(settings) + ".bak"] and settings.read_bytes() == assistant.files()[".claude/settings.json"]
    settings.write_text('{"permissions": {"allow": ["Bash(*)"]}}')
    (again,) = assistant.ensure()["backed_up"]   # the daemon's startup check puts it back too, under a second .bak name
    assert again != str(settings) + ".bak" and again.endswith(".bak") and settings.read_bytes() == assistant.files()[".claude/settings.json"]
    assert assistant.ensure() is None


def test_a_manifest_key_outside_the_directory_is_ignored(own_dir, tmp_path):
    assistant.install()
    victim = tmp_path / "victim.txt"; victim.write_text("keep me")
    m = json.loads((own_dir / assistant.MANIFEST).read_text())
    m["../victim.txt"] = assistant._sha(b"keep me"); m["/etc/passwd"] = "x"; m["nul\0name"] = "x"; m["list"] = ["not", "a", "sha"]
    (own_dir / assistant.MANIFEST).write_text(json.dumps(m))
    assert assistant.install()["removed"] == [] and victim.read_text() == "keep me"


def test_an_unparsable_or_misshapen_manifest_counts_as_none(own_dir):
    assistant.install()
    for body in ("{", "[]", '"x"'):
        (own_dir / assistant.MANIFEST).write_text(body)
        (own_dir / "CLAUDE.md").write_text("theirs " + body)
        res = assistant.install()
        assert len(res["backed_up"]) == 1 and isinstance(json.loads((own_dir / assistant.MANIFEST).read_text()), dict)


def test_a_second_edit_never_overwrites_the_first_backup(own_dir, monkeypatch):
    assistant.install()
    skill = own_dir / ".claude/skills/finnamon/SKILL.md"
    skill.write_text("first edit")
    new = dict(assistant.files()); new[".claude/skills/finnamon/SKILL.md"] = b"release 2"
    monkeypatch.setattr(assistant, "files", lambda: new)
    assert assistant.install()["backed_up"] == [str(skill) + ".bak"]
    skill.write_text("second edit")
    new[".claude/skills/finnamon/SKILL.md"] = b"release 3"
    (second,) = assistant.install()["backed_up"]
    assert second != str(skill) + ".bak" and second.endswith(".bak") and Path(second).read_text() == "second edit"
    assert (own_dir / ".claude/skills/finnamon/SKILL.md.bak").read_text() == "first edit"


def test_atomic_writes_use_a_private_temp_file_with_the_final_mode(own_dir, monkeypatch):
    seen = []
    real = os.replace
    def spy(src, dst):
        seen.append((Path(src).name, oct(os.stat(src).st_mode & 0o777))); real(src, dst)
    monkeypatch.setattr(os, "replace", spy)
    assistant.install()
    assert seen and all(name != "CLAUDE.md.tmp" and name.startswith(("CLAUDE.md.", "AGENTS.md.", ".finnamon-bundle.json.", "settings.json.", "SKILL.md.")) for name, _ in seen), "mkstemp names: two writers never share one"
    seen.clear()
    assistant.trust()
    assert seen == [(seen[0][0], "0o600")] and seen[0][0].startswith(".claude.json.")


def test_the_harness_check_has_a_floor_a_bundleless_wheel_cannot_pass(own_dir, monkeypatch):
    monkeypatch.setattr(assistant, "files", lambda: {})   # a wheel that lost the bundle
    with pytest.raises(FileNotFoundError):
        assistant.install()   # never an "installed" empty directory, and never the sessions retired for one
    assert not own_dir.exists()
    assistant.trust()
    assert len(assistant.problems()) == len(assistant.FLOOR) and all("missing" in p for p in assistant.problems())
    assert set(assistant.FLOOR) <= set(assistant.REQUIRED)


def test_files_ships_everything_the_bundle_tracks():
    """The allowlist and the checked-in bundle must agree: a file committed to the bundle that files() would not ship is
    silently left out of every install."""
    r = subprocess.run(["git", "ls-files", "finnamon/assistant_bundle"], cwd=REPO, capture_output=True, text=True)
    if r.returncode:
        pytest.skip("not a git checkout")
    tracked = {line.split("finnamon/assistant_bundle/", 1)[1] for line in r.stdout.split()}
    generated = {k for k in assistant.files() if k == "AGENTS.md" or k.startswith(".agents/")}   # the Codex files, from these (codex.bundle)
    assert tracked == set(assistant.files()) - generated, "add the new file to files() (and the wheel), or drop it from the bundle"


def test_a_symlink_in_the_assistant_directory_is_replaced_never_followed(own_dir, tmp_path):
    assistant.install()
    target = tmp_path / "elsewhere.json"; target.write_text("theirs")
    (own_dir / assistant.MANIFEST).unlink(); (own_dir / assistant.MANIFEST).symlink_to(target)
    (own_dir / "CLAUDE.md").unlink(); (own_dir / "CLAUDE.md").symlink_to(target)
    assistant.install()
    assert target.read_text() == "theirs" and not (own_dir / "CLAUDE.md").is_symlink() and not (own_dir / assistant.MANIFEST).is_symlink()
    assert (own_dir / "CLAUDE.md").read_bytes() == assistant.files()["CLAUDE.md"]
    link = own_dir.parent / "claude-link.json"; real = own_dir.parent / "claude-real.json"; real.write_text("{}")
    link.symlink_to(real)
    import os as _os
    _os.environ["CLAUDE_CONFIG_DIR"]   # conftest set it; the file inside is what trust() writes
    assistant.claude_config_path().unlink(missing_ok=True); assistant.claude_config_path().symlink_to(real)
    assert assistant.trust() is True and assistant.claude_config_path().is_symlink() and json.loads(real.read_text())["projects"], "a dotfiles-managed ~/.claude.json keeps its link"
    assistant.untrust(own_dir)
    assert assistant.claude_config_path().is_symlink() and json.loads(real.read_text())["projects"] == {}, "and through untrust too"


def test_init_writes_and_trusts_the_bundle(own_dir, tg, monkeypatch, capsys):
    from finnamon import owners, plaid_api
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False, force=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    monkeypatch.setattr(cli.shutil, "which", lambda exe: None)
    monkeypatch.delenv("FINNAMON_CLAUDE_BIN")
    it = iter(["cid", "prod-sec", "", "123:token"]); monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
    cli.main(["init", "--owner", "bill", "--yes"])
    assert f"wrote the assistant bundle to {own_dir}" in capsys.readouterr().out and assistant.problems() == []


def test_daemon_logs_a_bundle_it_cannot_write_and_still_refuses_to_run(own_dir, conn, monkeypatch, caplog):
    from finnamon import daemon
    seed(conn)
    monkeypatch.setattr(assistant, "ensure", lambda: (_ for _ in ()).throw(OSError("read-only file system")))
    d = daemon.Daemon(conn_factory=lambda: conn)
    for name in ("sync_loop", "poll_loop", "converse_loop"):
        monkeypatch.setattr(d, name, lambda: pytest.fail("no thread starts without a harness"))
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    exits = []
    d.start(exit=exits.append)
    assert exits == [2] and "could not write the assistant bundle" in caplog.text


def test_files_skips_what_a_session_or_an_install_leaves_in_the_source(tmp_path, monkeypatch):
    import shutil
    src = tmp_path / "bundle"; shutil.copytree(assistant.BUNDLE, src)
    (src / ".claude/settings.local.json").write_text("{}")
    (src / assistant.MANIFEST).write_text("{}")
    (src / "CLAUDE.md.bak").write_text("old"); (src / "CLAUDE.md.tmp").write_text("half"); (src / ".DS_Store").write_text(""); (src / ".claude/skills/finnamon/.SKILL.md.swp").write_text("")
    monkeypatch.setattr(assistant, "BUNDLE", src)
    names = set(assistant.files())
    assert names == {"CLAUDE.md", ".claude/settings.json", ".claude/skills/finnamon/SKILL.md", ".claude/skills/triage/SKILL.md", ".claude/skills/import-browser/SKILL.md",
                     ".claude/skills/import-extension/SKILL.md",   # Claude only: no .agents copy (assistant.CLAUDE_ONLY_SKILLS)
                     "AGENTS.md", ".agents/skills/finnamon/SKILL.md", ".agents/skills/triage/SKILL.md", ".agents/skills/import-browser/SKILL.md"}


def test_install_warns_when_trust_cannot_be_written(own_dir, monkeypatch, capsys):
    assistant.claude_config_path().write_text("not json {")
    ran = []
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False, force=False: ran.append(1) or [])
    cli.main(["install", "--yes"])
    err = capsys.readouterr().err
    assert "could not mark" in err and assistant.TRUST_FIX in err and own_dir.is_dir() and ran == [1], "a warning, never a stop: the bundle is written and the jobs still come up"
    assert assistant.claude_config_path().read_text() == "not json {"


def test_problems_name_every_missing_file_and_the_fix(own_dir):
    problems = assistant.problems()
    assert len(problems) == 11 and all("finnamon install" in p and str(own_dir) in p for p in problems)
    assert any("import-browser" in p for p in problems), "every skill a session could be started on is required"
    assistant.install(); assistant.trust()
    assert assistant.problems() == [] and claude_runner.harness_problems() == []


def test_trust_writes_one_key_into_claudes_config_and_leaves_the_rest(own_dir, tmp_path):
    """Claude Code ignores a workspace's settings until the directory is trusted; its warning names this key as the fix."""
    cfg = assistant.claude_config_path()
    assert str(cfg).startswith(str(tmp_path)), "never the developer's real ~/.claude.json"
    cfg.write_text(json.dumps({"numStartups": 7, "projects": {"/elsewhere": {"allowedTools": ["x"], "hasTrustDialogAccepted": True}}}))
    cfg.chmod(0o644)
    assert assistant.trust() is True and oct(cfg.stat().st_mode & 0o777) == "0o600", "claude's state file is 0600 after we touch it, whatever it was"
    saved = json.loads(cfg.read_text())
    assert saved["numStartups"] == 7 and saved["projects"]["/elsewhere"] == {"allowedTools": ["x"], "hasTrustDialogAccepted": True}
    assert saved["projects"][str(own_dir)]["hasTrustDialogAccepted"] is True and assistant.trusted()
    assert assistant.trust() is False, "already trusted: nothing written"
    cfg.write_text(json.dumps({"projects": {str(own_dir): {"allowedTools": ["y"]}}}))
    assistant.trust()
    assert json.loads(cfg.read_text())["projects"][str(own_dir)] == {"allowedTools": ["y"], "hasTrustDialogAccepted": True}, "an existing record keeps its other keys"
    assistant.untrust(own_dir)
    assert not assistant.trusted() and str(own_dir) not in json.loads(cfg.read_text())["projects"]
    cfg.unlink()
    assert assistant.trust() is True and json.loads(cfg.read_text()) == {"projects": {str(own_dir): {"hasTrustDialogAccepted": True}}}, "a box where claude never ran"
    assert oct(cfg.stat().st_mode & 0o777) == "0o600", "created the way claude creates it"
    cfg.write_text("not json {")
    assert assistant.trust() is None and cfg.read_text() == "not json {", "a file we cannot parse is not ours to rewrite"
    for body in ("[]", '{"projects": 1}'):
        cfg.write_text(body)
        assert assistant.trust() is None and cfg.read_text() == body, "a shape we do not recognise is not ours either"
    cfg.unlink()
    assistant.untrust(own_dir)   # nothing to remove, nothing raised


def test_every_headless_run_uses_the_assistant_directory_as_cwd(own_dir, fake_claude, tmp_path, monkeypatch):
    assistant.install()
    where = tmp_path / "cwd.txt"
    fake_claude.write_text(f'#!/bin/sh\npwd > {where}\nprintf \'{{"result":"ok","session_id":"s"}}\'\n')
    assert claude_runner.cwd() == own_dir
    res = claude_runner.run("hello")
    assert res.ok and Path(where.read_text().strip()).resolve() == own_dir.resolve()
    assert not str(own_dir).startswith(str(REPO)), "never the checkout"


def test_import_browser_starts_its_session_in_the_assistant_directory(own_dir, conn, monkeypatch, capsys):
    import sys
    seed(conn)
    calls = []
    monkeypatch.setattr("finnamon.claude_runner.binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr("os.chdir", lambda p: calls.append(("cd", str(p))))
    monkeypatch.setattr("os.execve", lambda exe, argv, env: calls.append((exe, argv, env)))
    monkeypatch.setattr("finnamon.cli.chrome_launch", lambda c, p, u: ("127.0.0.1:1", "T1"))
    monkeypatch.setenv("FINNAMON_CHROME", sys.executable)
    launched = []
    monkeypatch.setattr("finnamon.cli.chrome_launch", lambda c, p, u: launched.append(u) or ("127.0.0.1:1", "T1"))
    with pytest.raises(SystemExit):
        cli.main(["import", "--browser", "hsbc"])   # no bundle installed: refused, with the fix named
    assert "finnamon install" in capsys.readouterr().err and calls == [] and launched == [], "refused before the bank window opens"
    assistant.install()
    with pytest.raises(SystemExit):
        cli.main(["import", "--browser", "hsbc"])   # the files alone: claude would ignore the settings there
    assert "not trusted" in capsys.readouterr().err and calls == []
    assistant.trust()
    cli.main(["import", "--browser", "hsbc"])
    assert calls[0] == ("cd", str(own_dir)) and calls[1][1][2:4] == ["--setting-sources", "project"] and "--strict-mcp-config" in calls[1][1]


def test_update_writes_the_bundle_retires_the_sessions_once_and_restarts_both_jobs(scheduled, own_dir, conn, monkeypatch, capsys):
    """Claude Code keeps a session's transcript under the directory it ran in, so the first bundle install is the one time
    the household's conversations cannot be resumed: the daemon's session id is cleared and the dashboard's retired
    (kept as `prev`, the way `install` retires it). Later updates leave both alone."""
    seed(conn)
    store.set_state(conn, "session", "daemon-session-1")
    intercom = config.home() / config.INTERCOM_FILE
    intercom.write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))
    repo = config.home() / "checkout"; (repo / ".git").mkdir(parents=True)
    restarted = []
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: restarted.extend(names) or list(names))
    monkeypatch.setattr(cli, "_git", lambda r, *args, **kw: subprocess.CompletedProcess(args, 0, "same111\n" if args[0] == "rev-parse" else "", ""))
    cli.main(["update", "--no-pull"])
    out = capsys.readouterr().out
    assert own_dir.is_dir() and assistant.problems() == [] and assistant.trusted()
    assert f"wrote the assistant bundle to {own_dir}" in out and "start over" in out and f"marked {own_dir} trusted" in out
    assert store.get_state(conn, "session") is None
    assert json.loads(intercom.read_text()) == {"prev": "11111111-2222-3333-4444-555555555555"}
    assert restarted == ["daemon", "web"]
    # the second update: nothing changed in the bundle, so the conversations (a new pair by now) stay
    store.set_state(conn, "session", "daemon-session-2")
    intercom.write_text(json.dumps({"id": "66666666-2222-3333-4444-555555555555", "created": True}))
    restarted.clear()
    cli.main(["update", "--no-pull"])
    out = capsys.readouterr().out
    assert "assistant bundle" not in out and store.get_state(conn, "session") == "daemon-session-2"
    assert json.loads(intercom.read_text())["id"] == "66666666-2222-3333-4444-555555555555"
    assert restarted == ["daemon", "web"], "--no-pull restarts both whatever changed, as before"
    # a release that changes the bundle restarts both jobs even when git's diff would have said daemon only
    (own_dir / "CLAUDE.md").write_bytes(b"stale release")
    manifest = json.loads((own_dir / assistant.MANIFEST).read_text()); manifest["CLAUDE.md"] = assistant._sha(b"stale release")
    (own_dir / assistant.MANIFEST).write_text(json.dumps(manifest))   # what an older release wrote: not a household edit
    restarted.clear()
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        table = {"rev-parse": lambda: next(heads), "status": lambda: "", "pull": lambda: "Updating\n", "config": lambda: "x\n", "diff": lambda: "finnamon/notify.py\n"}
        return subprocess.CompletedProcess(args, 0, table[args[0]](), "")
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update"])
    out = capsys.readouterr().out
    assert f"updated the assistant bundle in {own_dir}" in out and restarted == ["daemon", "web"]
    assert (own_dir / "CLAUDE.md").read_bytes() == assistant.files()["CLAUDE.md"] and not (own_dir / "CLAUDE.md.bak").exists()


def test_install_writes_the_bundle_before_the_jobs_come_up(own_dir, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False, force=False: seen.append(own_dir.is_dir()) or [])
    cli.main(["install", "--dry-run"])
    assert seen == [False] and "would write the assistant bundle" in capsys.readouterr().out and not own_dir.exists(), "--dry-run writes nothing"
    cli.main(["install", "--yes"])
    assert seen == [False, True], "the daemon and the dashboard start their sessions in it the moment they are (re)loaded"
    assert assistant.problems() == []
    (config.home() / config.INTERCOM_FILE).write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))
    cli.main(["install", "--yes"])
    out = capsys.readouterr().out
    assert "claude --strict-mcp-config --resume 11111111-2222-3333-4444-555555555555" in out and str(own_dir) in out and "checkout" in out, "the retired transcript lives where that session ran: here now, the checkout before v0.8.0.0"


def test_daemon_start_writes_a_missing_bundle_once(own_dir, conn, monkeypatch):
    """The release that introduced the bundle is adopted by an `update` running the previous release's code, which knows
    nothing of it; the restarted daemon writes the directory itself rather than crash-looping on the harness check."""
    from finnamon import daemon
    seed(conn)
    def start():
        d = daemon.Daemon(conn_factory=lambda: conn)
        for name in ("sync_loop", "poll_loop", "converse_loop"):
            monkeypatch.setattr(d, name, lambda: None)
        monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
        d.start(exit=lambda code: pytest.fail(f"exited {code}"))
    start()
    assert assistant.problems() == [] and assistant.trusted()
    (own_dir / "CLAUDE.md").write_text("theirs")
    start()
    assert (own_dir / "CLAUDE.md").read_text() == "theirs", "an existing directory is never rewritten by the daemon"


def test_trust_reads_again_when_claude_wrote_in_between(own_dir, monkeypatch):
    cfg = assistant.claude_config_path()
    cfg.write_text(json.dumps({"projects": {}}))
    real = assistant._stamp
    calls = []
    def racing(p):
        calls.append(1)
        if len(calls) == 2:   # between our read and our write, claude saved something of its own
            cfg.write_text(json.dumps({"projects": {"/theirs": {"allowedTools": ["x"]}}, "numStartups": 3}))
            import os; os.utime(cfg, ns=(1, 1))
        return real(p)
    monkeypatch.setattr(assistant, "_stamp", racing)
    assert assistant.trust() is True
    saved = json.loads(cfg.read_text())
    assert saved["numStartups"] == 3 and saved["projects"]["/theirs"] == {"allowedTools": ["x"]} and saved["projects"][str(own_dir)]["hasTrustDialogAccepted"] is True


def test_a_failed_write_leaves_no_temp_file_and_the_target_unchanged(own_dir, monkeypatch):
    own_dir.mkdir(); target = own_dir / "t.txt"; target.write_text("before")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        assistant._write_atomic(target, b"after")
    assert target.read_text() == "before" and sorted(os.listdir(own_dir)) == ["t.txt"]


def test_update_that_cannot_write_the_bundle_dies_after_the_migration_and_restarts_nothing(scheduled, own_dir, conn, monkeypatch, capsys):
    seed(conn)
    repo = config.home() / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: pytest.fail("nothing may restart"))
    monkeypatch.setattr(cli, "_git", lambda r, *args, **kw: subprocess.CompletedProcess(args, 0, "same111\n" if args[0] == "rev-parse" else "", ""))
    monkeypatch.setattr(assistant, "install", lambda dest=None: (_ for _ in ()).throw(OSError("read-only file system")))
    with pytest.raises(SystemExit):
        cli.main(["update", "--no-pull"])
    err = capsys.readouterr().err
    assert "could not write the assistant bundle" in err and "nothing was restarted" in err and "migrated" in err


def test_install_warns_when_the_shell_overrides_the_assistant_directory(own_dir, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("FINNAMON_ASSISTANT", str(elsewhere))
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False, force=False: [])
    cli.main(["install", "--yes"])
    err = capsys.readouterr().err
    assert "FINNAMON_ASSISTANT is set" in err and str(assistant.default_dir()) in err and (elsewhere / "CLAUDE.md").is_file() and not own_dir.exists()


def test_status_reports_the_assistant_directory_and_its_problems(own_dir, conn, capsys):
    seed(conn)
    cli.main(["status"])
    st = json.loads(capsys.readouterr().out)
    assert st["assistant"] == str(own_dir) and st["assistant_problems"] and all("finnamon install" in p for p in st["assistant_problems"])
    assistant.install(); assistant.trust()
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out)["assistant_problems"] == [], "what the dashboard's health pill reads"


def test_update_moves_a_channel_household_to_session_and_unregisters_the_plugin(scheduled, own_dir, conn, fake_claude, tmp_path, monkeypatch, capsys):
    """Channel mode registered telegram@claude-plugins-official for the assistant directory; update takes it off (uninstall
    run there, then the enabledPlugins entry) and moves the household to the one mode, with one line. Other settings stay."""
    seed(conn); store.set_state(conn, "inbound", "channel"); store.set_state(conn, "channel_deaf_since", "2026-10-01 10:00:00")
    assistant.install(); assistant.trust()
    log = tmp_path / "plugin.txt"
    fake_claude.write_text(f'#!/bin/sh\n{{ pwd; printf "%s\\n" "$@"; }} >> {log}\n')
    local = own_dir / ".claude/settings.local.json"
    local.write_text(json.dumps({"enabledPlugins": {assistant.TELEGRAM_PLUGIN: True, "other@x": True}, "permissions": {"allow": ["Bash(ls)"]}}))
    repo = config.home() / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: list(names))
    monkeypatch.setattr(cli, "_git", lambda r, *args, **kw: subprocess.CompletedProcess(args, 0, "same111\n" if args[0] == "rev-parse" else "", ""))
    cli.main(["update", "--no-pull"])
    out = capsys.readouterr().out
    assert "unregistered the retired Telegram channel plugin" in out and "Telegram now always shares the dashboard's conversation" in out
    lines = log.read_text().splitlines()
    assert Path(lines[0]).resolve() == own_dir.resolve() and lines[1:] == ["plugin", "uninstall", assistant.TELEGRAM_PLUGIN, "--scope", "local"]
    assert json.loads(local.read_text()) == {"enabledPlugins": {"other@x": True}, "permissions": {"allow": ["Bash(ls)"]}}
    assert store.get_state(conn, "inbound") == "session" and store.get_state(conn, "channel_deaf_since") is None
    log.unlink()
    cli.main(["update", "--no-pull"])
    out = capsys.readouterr().out
    assert not log.exists() and "Telegram now" not in out and "unregistered" not in out, "once: nothing left to do"


def test_unregister_tolerates_not_installed_and_names_the_by_hand_step(own_dir, fake_claude, tmp_path, monkeypatch):
    assistant.install()
    reg = assistant.plugins_file(); reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"plugins": {assistant.TELEGRAM_PLUGIN: [{"scope": "local", "projectPath": str(own_dir)}, {"scope": "user"}]}}))
    fake_claude.write_text('#!/bin/sh\necho "Plugin telegram@claude-plugins-official is not installed" >&2; exit 1\n')
    assert assistant.unregister_telegram_plugin(str(fake_claude)) == [f"unregistered the retired Telegram channel plugin for {own_dir}"]
    fake_claude.write_text('#!/bin/sh\necho "marketplace unreachable" >&2; exit 1\n')
    (msg,) = assistant.unregister_telegram_plugin(str(fake_claude))
    assert msg.startswith("warning:") and "marketplace unreachable" in msg and f"cd {own_dir} && claude plugin uninstall" in msg
    (msg,) = assistant.unregister_telegram_plugin(None)
    assert msg.startswith("warning:") and "not on PATH" in msg
    monkeypatch.setattr(assistant, "PLUGIN_TIMEOUT_S", 1)
    fake_claude.write_text("#!/bin/sh\nsleep 5\n")
    (msg,) = assistant.unregister_telegram_plugin(str(fake_claude))
    assert msg.startswith("warning:") and f"cd {own_dir} && claude plugin uninstall" in msg, "a hung claude never stops update"
    reg.write_text(json.dumps({"plugins": {assistant.TELEGRAM_PLUGIN: [{"scope": "user"}]}}))
    assert assistant.unregister_telegram_plugin(str(fake_claude)) == [], "a user-scope registration is the person's"



# Value: protects=`finnamon install` on a channel household also retires the mode and the plugin; fails_when=cmd_install stops calling _retire_channel_mode; why_new=only the update path was tested; seam=none
def test_install_moves_a_channel_household_to_session_and_unregisters_the_plugin(own_dir, conn, fake_claude, tmp_path, monkeypatch, capsys):
    seed(conn); store.set_state(conn, "inbound", "channel")
    assistant.install(); assistant.trust()
    log = tmp_path / "plugin.txt"
    fake_claude.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {log}\n')
    local = own_dir / ".claude/settings.local.json"
    local.write_text(json.dumps({"enabledPlugins": {assistant.TELEGRAM_PLUGIN: True}}))
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False, force=False: [])
    cli.main(["install", "--yes"])
    out = capsys.readouterr().out
    assert "unregistered the retired Telegram channel plugin" in out and "Telegram now always shares" in out
    assert log.read_text().splitlines() == ["plugin", "uninstall", assistant.TELEGRAM_PLUGIN, "--scope", "local"]
    assert json.loads(local.read_text()) == {"enabledPlugins": {}} and store.get_state(conn, "inbound") == "session"


# Value: protects=update survives a hand-mangled settings.local.json and leaves it as found; fails_when=the parse error escapes unregister_telegram_plugin (update dies after the bundle write) or the file is rewritten; why_new=only well-formed files were tested; seam=none
def test_unregister_with_a_malformed_settings_local_still_uninstalls_and_leaves_the_file(own_dir, fake_claude):
    assistant.install()
    local = own_dir / ".claude/settings.local.json"
    local.write_text("{not json")
    assert assistant.unregister_telegram_plugin(str(fake_claude)) == [], "no registry entry, no parseable entry: nothing to do"
    reg = assistant.plugins_file(); reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"plugins": {assistant.TELEGRAM_PLUGIN: [{"scope": "local", "projectPath": str(own_dir)}]}}))
    fake_claude.write_text("#!/bin/sh\nexit 0\n")
    assert assistant.unregister_telegram_plugin(str(fake_claude)) == [f"unregistered the retired Telegram channel plugin for {own_dir}"]
    assert local.read_text() == "{not json"


def test_detect_prelude_prints_the_prelude_and_the_assistant_may_run_it(home, capsys):
    cli.main(["detect", "--prelude"])
    assert capsys.readouterr().out.strip() == detect.PRELUDE.read_text().strip()
    allow = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]["allow"]
    assert "Bash(finnamon detect --prelude)" in allow


def test_the_skills_read_no_source_file():
    """Everything the assistant needs is a command: the installed bundle sits next to no checkout, and a wheel has none."""
    for skill in (assistant.BUNDLE / ".claude/skills").rglob("SKILL.md"):
        text = skill.read_text()
        for needle in ("finnamon/detectors", "finnamon/charts.py", "_prelude.sql`", "AGENTS.md", "this checkout", "the checkout", "this repository"):
            assert needle not in text, f"{skill.name}: {needle}"
    assert "finnamon detect --prelude" in (assistant.BUNDLE / ".claude/skills/finnamon/SKILL.md").read_text()
    persona = (assistant.BUNDLE / "CLAUDE.md").read_text()
    assert "you are the household's finance assistant" in persona and "Working on the code" not in persona and "development" not in persona.lower()


def test_the_checkout_grants_the_assistant_nothing():
    """The root is developer-only: no assistant persona, and no permission set carrying the household allow list."""
    root_settings = REPO / ".claude/settings.json"
    if root_settings.exists():
        perms = json.loads(root_settings.read_text()).get("permissions", {})
        assert not any(a.startswith("Bash(finnamon") for a in perms.get("allow", [])), "the household allow list belongs to the bundle"
    for f in ("AGENTS.md", "CLAUDE.md"):
        text = (REPO / f).read_text()
        assert "you are the household's finance assistant" not in text and "assistant_bundle" in text
    assert not (REPO / ".claude/skills").exists()


def test_services_for_restarts_both_jobs_on_a_bundle_change():
    assert scheduler.services_for(["finnamon/assistant_bundle/.claude/skills/finnamon/SKILL.md"]) == ["daemon", "web"]
    assert scheduler.services_for(["finnamon/notify.py"]) == ["daemon"]


def test_the_wheel_carries_the_bundle(tmp_path):
    """Hatchling builds the package directory whole, dotfiles included; this is the check that a wheel install has an
    assistant to write."""
    import shutil
    import zipfile
    if not shutil.which("uv"):
        pytest.skip("needs uv to build a wheel")
    r = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), "--quiet"], cwd=REPO, capture_output=True, text=True, timeout=120)
    if r.returncode and any(w in r.stderr.lower() for w in ("network", "resolve", "offline", "connect", "fetch")):
        pytest.skip("uv could not fetch the build backend (offline box): " + r.stderr.strip()[-200:])
    assert r.returncode == 0, r.stderr[-800:]
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    for rel in assistant.REQUIRED:
        if rel != "AGENTS.md" and not rel.startswith(".agents/"):   # generated at install from the Claude files
            assert f"finnamon/assistant_bundle/{rel}" in names, rel


def test_status_reports_stray_telegram_plugins_read_only(conn, tmp_path, capsys):
    reg = assistant.plugins_file(); reg.parent.mkdir(parents=True)
    plugin = [{"scope": "local", "projectPath": str(assistant.dir())}, {"scope": "local", "projectPath": "/wt/Cobra"},
              {"scope": "local", "projectPath": "/private/tmp/x"}, {"scope": "user"}]
    reg.write_text(body := json.dumps({"version": 2, "plugins": {assistant.TELEGRAM_PLUGIN: plugin, "other@x": [{"scope": "user"}]}}))
    cli.main(["status"]); st = json.loads(capsys.readouterr().out)
    assert st["stray_telegram_plugins"] == ["(user scope: every folder)", "/private/tmp/x", "/wt/Cobra"]
    assert "plugin uninstall" in st["stray_telegram_plugins_fix"] and reg.read_text() == body   # reported, never edited
    assert "stray_telegram_plugins_seeding" not in st
    # a registered checkout that also enables it in settings.local.json hands a registration to each of its worktrees
    seed_dir = tmp_path / "checkout"; (seed_dir / ".claude").mkdir(parents=True)
    (seed_dir / ".claude/settings.local.json").write_text(json.dumps({"enabledPlugins": {assistant.TELEGRAM_PLUGIN: True}}))
    reg.write_text(json.dumps({"version": 2, "plugins": {assistant.TELEGRAM_PLUGIN: plugin + [{"scope": "local", "projectPath": str(seed_dir)}]}}))
    cli.main(["status"]); assert json.loads(capsys.readouterr().out)["stray_telegram_plugins_seeding"] == [str(seed_dir)]
    reg.write_text(json.dumps({"version": 2, "plugins": {assistant.TELEGRAM_PLUGIN: plugin[:1]}}))
    cli.main(["status"]); assert "stray_telegram_plugins" not in json.loads(capsys.readouterr().out)
    reg.write_text("not json"); cli.main(["status"]); capsys.readouterr()                 # a broken registry is not a crash
