"""claude_runner output shapes, the MCP server's tools against a seeded DB, and store/scheduler leftovers."""
import asyncio
import json
from pathlib import Path

import pytest

from finnamon import budgets, query, claude_runner, config, scheduler, store
from tests.conftest import REAL_UNIT_DIR, today, AS_OF, seed, txn


def test_claude_runner_output_shapes(fake_claude, monkeypatch):
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", "plain text, not json")
    r = claude_runner.run("hi")
    assert r.ok and r.text == "plain text, not json" and r.session_id is None
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps([{"type": "system"}, {"result": "last wins", "session_id": "s2"}]))
    r = claude_runner.run("hi", resume="s1")
    assert r.ok and r.text == "last wins" and r.session_id == "s2"
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", "[]")
    assert claude_runner.run("hi") == claude_runner.Result(True, "", None)
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": "No conversation found with session id s1", "is_error": True, "session_id": "s1"}))
    r = claude_runner.run("hi")
    assert not r.ok and "No conversation" in r.error and claude_runner.classify_error(r.error) == "session lost"
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"is_error": True}))
    assert claude_runner.run("hi").error == "is_error"
    monkeypatch.setenv("CLAUDE_FAKE_EXIT", "2")
    monkeypatch.setenv("CLAUDE_FAKE_STDERR", "")
    assert claude_runner.run("hi").error == "exit 2"
    monkeypatch.setenv("FINNAMON_CLAUDE_BIN", "")
    monkeypatch.setenv("CLAUDE_BIN", "/opt/bin/claude")  # the dashboard's own override; init's Claude Code check honours it too
    assert claude_runner.binary() == "/opt/bin/claude"
    monkeypatch.delenv("CLAUDE_BIN")
    monkeypatch.setattr(claude_runner.shutil, "which", lambda n: None)
    r = claude_runner.run("hi")
    assert r.error == "claude not on PATH" and claude_runner.classify_error(r.error) == "not installed"
    assert claude_runner.classify_error(None) == "error" and claude_runner.classify_error("invalid credential") == "login expired"


def test_mcp_server_tools(home, conn):
    pytest.importorskip("mcp")
    from finnamon import mcp_server
    seed(conn)
    budgets.budget_set(conn, "groceries", 600, "FOOD_AND_DRINK_GROCERIES")
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1000, 900)", (store.now_local(),))   # MCP tools use the wall clock
    txn(conn, "t1", "chk", today(), 80, "WF", "Whole Foods", "mch_wf")
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, verdict, confidence, reason) VALUES ('anomaly','anomaly:first_merchant','k','{\"merchant\":\"x\"}',?,'suppress','low','normal')", (AS_OF,))
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','duplicate_charge','k2','{}',?)", (AS_OF,))
    mcp = mcp_server.build()

    def call(name, **args):
        res = asyncio.run(mcp.call_tool(name, args))
        blocks = res[0] if isinstance(res, tuple) else res
        return json.loads(blocks[0].text)

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"list_accounts", "search_transactions", "spend_by_category", "budget_progress", "list_alerts", "list_suppressed", "net_worth_history", "render_chart", "sql"}
    assert [a["name"] for a in call("list_accounts")] == ["Chase Checking"]
    assert call("search_transactions", text="whole", days=30)[0]["merchant"] == "Whole Foods"
    assert call("search_transactions", text="' OR 1=1 --", days=30) == []  # bound parameter, not interpolated
    assert query.run("SELECT ? AS x", params=("a'b",)) == [{"x": "a'b"}]
    assert call("spend_by_category", months=1)[0]["spent"] == 80
    assert call("budget_progress")[0]["spent"] == 80
    assert [a["kind"] for a in call("list_alerts", days=1)] == ["duplicate_charge", "anomaly:first_merchant"]
    assert call("list_suppressed", days=1)[0]["reason"] == "normal"
    assert call("net_worth_history", months=1)[0]["net_worth"] == 1000
    assert call("sql", select="SELECT 1 AS one") == [{"one": 1}]
    with pytest.raises(Exception):
        call("sql", select="DELETE FROM alerts")
    pytest.importorskip("matplotlib")
    out = asyncio.run(mcp.call_tool("render_chart", {"name": "budgets"}))
    blocks = out[0] if isinstance(out, tuple) else out
    assert blocks[0].text.endswith(".png") and Path(blocks[0].text).exists()


def test_query_run_migrates_and_resolves_through_tx_now(home):
    """Issue 23's headline path: the assistant answers category questions with `finnamon query`, which opens the
    DB read-only. A read-only open never migrates, so without the connect in query.run this raises `no such
    table: tx_now` on the first query after the release -- and the alias/override would be invisible anyway."""
    conn = store.connect()
    seed(conn)
    txn(conn, "m1", "chk", today(), 3605.99, "1st Security Bank Loan Pmt 4471", None, None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    budgets.alias_set(conn, "1st Security Bank Loan Pmt%", "1st Security Bank mortgage")
    budgets.category_set(conn, "1st Security Bank mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    conn.close()
    r = query.run("SELECT display, canonical, category FROM tx_now WHERE transaction_id='m1'")[0]
    assert r == {"display": "1st Security Bank mortgage", "canonical": "1st Security Bank mortgage",
                 "category": "LOAN_PAYMENTS_MORTGAGE_PAYMENT"}
    assert query.run("SELECT pfc_detailed FROM transactions WHERE transaction_id='m1'")[0]["pfc_detailed"] == "RENT_AND_UTILITIES_RENT"   # the raw row is untouched

def test_mcp_search_and_spend_by_category_read_tx_now_with_renamed_columns(conn):
    """Regression (issue 23): both tools now read `tx_now` instead of `transactions`, and their output JSON
    was renamed pfc_detailed->category, pfc_primary->category_primary. An MCP client (Claude Desktop, a phone
    app) must see the household's alias and category override applied, the same as the CLI and Telegram do."""
    pytest.importorskip("mcp")
    from finnamon import budgets, mcp_server
    seed(conn)
    txn(conn, "m1", "chk", today(), 3605.99, "1st Security Bank Loan Pmt 4471", None, None, "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
    budgets.alias_set(conn, "1st Security Bank Loan Pmt%", "1st Security Bank mortgage")
    budgets.category_set(conn, "1st Security Bank mortgage", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    mcp = mcp_server.build()

    def call(name, **args):
        res = asyncio.run(mcp.call_tool(name, args))
        blocks = res[0] if isinstance(res, tuple) else res
        return json.loads(blocks[0].text)

    row = call("search_transactions", text="1st security", days=30)[0]
    assert row["merchant"] == "1st Security Bank mortgage" and row["category"] == "LOAN_PAYMENTS_MORTGAGE_PAYMENT"
    assert "pfc_detailed" not in row
    txn(conn, "g1", "chk", today(), 40, "WHOLE FOODS", "Whole Foods")
    cats = call("spend_by_category", months=1)
    assert [c["category_primary"] for c in cats] == ["FOOD_AND_DRINK"] and "pfc_primary" not in cats[0]   # the mortgage is not category spend (issue 72)


def test_store_and_scheduler_leftovers(home, monkeypatch):
    parts = store._split("CREATE TABLE a (x);\n-- trailing comment\nINSERT INTO a VALUES ('a;b--c');")
    assert parts[0] == "CREATE TABLE a (x);" and parts[1].endswith("INSERT INTO a VALUES ('a;b--c');") and len(parts) == 2  # quotes and comments respected
    assert store._split(";;\n-- only comments\n") == []
    conn = store.connect()
    store.set_setting(conn, "large_amount", 300)
    with pytest.raises(ValueError, match="cannot be unset"):
        store.set_setting(conn, "large_amount", None)                 # the global row is the default; it always exists
    store.set_setting(conn, "low_balance_threshold", 1234567.89, "acct1")
    assert store.setting(conn, "low_balance_threshold", "acct1") == "1234567.89"   # cents kept, no exponent form
    store.set_setting(conn, "low_balance_threshold", None, "acct1")   # per-account override unset → falls back
    assert store.setting(conn, "low_balance_threshold", "acct1") is None
    with pytest.raises(ValueError):
        store.set_setting(conn, "sync_interval_hours", 3)          # operational key without --ops
    with pytest.raises(ValueError):
        store.set_setting(conn, "dup_min_amount", "lots")          # non-numeric
    with pytest.raises(ValueError):
        store.set_setting(conn, "nonsense", 1)                     # unknown key
    store.set_setting(conn, "sync_interval_hours", 3, ops=True)
    assert store.setting_num(conn, "sync_interval_hours", 6) == 3
    conn.execute("UPDATE settings SET value='x' WHERE key='sync_interval_hours'")
    assert store.setting_num(conn, "sync_interval_hours", 6) == 6  # a corrupt row never breaks the daemon
    assert config.charts_dir() == home / "charts" and config.lock_path() == home / "run.lock"
    # a failed secrets write leaves no temp file behind and keeps the old file
    from finnamon import secrets
    secrets.write({"client_id": "old"}, {}, {})
    monkeypatch.setattr(secrets.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        secrets.write({"client_id": "new"}, {}, {})
    assert not list(home.glob(".secrets.*.tmp")) and config.load_secrets()["plaid"]["client_id"] == "old"
    monkeypatch.setattr(scheduler.shutil, "which", lambda n: None)
    assert " ".join(scheduler.exe_args()).endswith(" -m finnamon.cli") and scheduler.exe_args()[1:] == ["-m", "finnamon.cli"]
    assert scheduler.env_path().startswith("/usr/local/bin:") and {str(Path.home() / ".local/bin"), str(Path.home() / ".bun/bin")} <= set(scheduler.env_path().split(":"))
    monkeypatch.setattr(scheduler.shutil, "which", lambda n: f"/opt/tools/bin/{n}")
    assert scheduler.env_path().split(":")[0] == "/opt/tools/bin" and scheduler.exe_args() == ["/opt/tools/bin/finnamon"]
    monkeypatch.setattr(scheduler.Path, "home", lambda: home)
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    monkeypatch.setenv("FINNAMON_HOME", str(home / ".finnamon"))   # the household's names
    written = scheduler.install("Linux", dry_run=True)
    assert [Path(w).name for w in written][:3] == ["finnamon-daemon.service", "finnamon-heartbeat.service", "finnamon-heartbeat.timer"]   # plus finnamon-web.service when web/ is installed
    assert not (home / ".config").exists()  # dry run writes nothing
    written = scheduler.install("Darwin", dry_run=True)
    assert all(w.startswith(str(home / "Library" / "LaunchAgents")) for w in written) and scheduler.log_dir().stat().st_mode & 0o777 == 0o700   # log_dir is scratch (conftest)


def test_launchd_reload_waits_for_the_old_service_to_go_away():
    from pathlib import Path
    from types import SimpleNamespace
    from finnamon import scheduler
    calls, prints = [], iter([0, 0, 1])   # still there, still there, gone
    def run(argv, **kw):
        calls.append(argv[1])
        if argv[1] == "print":
            return SimpleNamespace(returncode=next(prints))
        if argv[1] == "bootstrap":
            return SimpleNamespace(returncode=5 if calls.count("bootstrap") == 1 else 0, stderr="Input/output error", stdout="")
        return SimpleNamespace(returncode=0)
    scheduler._launchd_reload("gui/503", "com.finnamon.daemon", Path("/x.plist"), run=run, sleep=lambda s: None)
    assert calls == ["bootout", "print", "print", "print", "bootstrap", "bootstrap"]   # one retry after the EIO


def test_launchd_reload_reports_a_persistent_failure():
    from pathlib import Path
    from types import SimpleNamespace
    from finnamon import scheduler
    run = lambda argv, **kw: SimpleNamespace(returncode=1 if argv[1] == "print" else 5, stderr="boom", stdout="")
    with pytest.raises(RuntimeError) as e:
        scheduler._launchd_reload("gui/503", "com.finnamon.daemon", Path("/x.plist"), run=run, sleep=lambda s: None)
    assert "bootstrap com.finnamon.daemon failed (5): boom" in str(e.value)


def test_launchd_reload_gives_up_waiting_after_30_polls_and_reports_stdout_when_stderr_is_empty():
    from pathlib import Path
    from types import SimpleNamespace
    from finnamon import scheduler
    calls, slept = [], []
    def run(argv, **kw):
        calls.append(argv[1])
        if argv[1] == "print":
            return SimpleNamespace(returncode=0)   # label never goes away
        if argv[1] == "bootstrap":
            return SimpleNamespace(returncode=0 if calls.count("bootstrap") == 5 else 5, stderr="", stdout="Input/output error\n")
        return SimpleNamespace(returncode=0)
    scheduler._launchd_reload("gui/503", "com.finnamon.daemon", Path("/x.plist"), run=run, sleep=slept.append)
    assert calls.count("print") == 30 and calls.count("bootstrap") == 5 and calls[-1] == "bootstrap"   # bounded wait, then the fifth try lands
    assert slept == [0.5] * 30 + [1] * 4   # 15s cap on the wait, 1s between failed bootstraps
    run = lambda argv, **kw: SimpleNamespace(returncode=1 if argv[1] == "print" else 5, stderr="", stdout="  Input/output error\n")
    with pytest.raises(RuntimeError, match=r"failed \(5\): Input/output error$"):
        scheduler._launchd_reload("gui/503", "com.finnamon.daemon", Path("/x.plist"), run=run, sleep=lambda s: None)


def test_cmd_install_dry_run_prints_paths_and_scheduler_failures_die(home, capsys, monkeypatch):
    import subprocess
    from finnamon import cli
    seen = []
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False: seen.append(dry_run) or ["/LaunchAgents/a.plist", "/LaunchAgents/b.plist"])
    cli.main(["install", "--dry-run"])
    out = capsys.readouterr().out.splitlines()
    assert seen == [True] and out[1:] == ["/LaunchAgents/a.plist", "/LaunchAgents/b.plist"] and out[0].startswith("would write the assistant bundle to ")
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False: (_ for _ in ()).throw(RuntimeError("launchctl bootstrap com.finnamon.daemon failed (5): Input/output error")))
    with pytest.raises(SystemExit) as e:
        cli.main(["install"])
    assert e.value.code == 1 and capsys.readouterr().err == "error: launchctl bootstrap com.finnamon.daemon failed (5): Input/output error\n"
    monkeypatch.setattr(scheduler, "install", lambda os_name=None, dry_run=False: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["systemctl", "--user", "daemon-reload"])))
    with pytest.raises(SystemExit) as e:
        cli.main(["install"])
    assert e.value.code == 1 and "systemctl" in capsys.readouterr().err


def test_launchd_reload_does_not_retry_a_rejected_plist():
    from pathlib import Path
    from types import SimpleNamespace
    from finnamon import scheduler
    calls = []
    def run(argv, **kw):
        calls.append(argv[1]); assert kw.get("timeout")   # a hung launchctl must not hang the installer
        if argv[1] == "print":
            return SimpleNamespace(returncode=113)
        if argv[1] == "bootstrap":
            return SimpleNamespace(returncode=37, stderr="Operation already in progress", stdout="")   # not EIO: no point retrying
        return SimpleNamespace(returncode=0)
    with pytest.raises(RuntimeError) as e:
        scheduler._launchd_reload("gui/503", "com.finnamon.daemon", Path("/x.plist"), run=run, sleep=lambda s: (_ for _ in ()).throw(AssertionError("no sleep")))
    assert calls == ["bootout", "print", "bootstrap"] and "(37): Operation already in progress" in str(e.value)


def test_restart_bounces_the_jobs_install_loaded_under_the_names_it_wrote(household_home, monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 503)
    monkeypatch.setattr(scheduler, "_not_running", lambda *a, **k: None)   # liveness is its own test; here it is the argv that matters
    assert scheduler.restart(["daemon", "web"], "Darwin") == ["daemon", "web"]
    assert [c[0] for c in calls] == [["launchctl", "kickstart", "-k", "gui/503/com.finnamon.daemon"],
                                     ["launchctl", "kickstart", "-k", "gui/503/com.finnamon.web"]]   # kickstart, not bootout/bootstrap: the unit stays as installed
    assert all(c[1].get("timeout") == scheduler.LAUNCHCTL_TIMEOUT_S for c in calls), "a hung launchctl must not hang the update either"
    calls.clear()
    assert scheduler.restart(["web"], "Linux") == ["web"]
    assert calls[0][0] == ["systemctl", "--user", "restart", "finnamon-web.service"]
    monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/x/web/server.js"])
    assert {f"{scheduler.LABEL}.{n}.plist" for n in ("daemon", "web")} <= set(scheduler.render("Darwin"))     # the labels restart() kickstarts are the ones install wrote
    assert {f"finnamon-{n}.service" for n in ("daemon", "web")} <= set(scheduler.render("Linux"))


def test_restart_reports_a_service_manager_that_refused(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 503)
    monkeypatch.setattr(scheduler, "_not_running", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=3, stderr="Could not find service\n", stdout=""))
    with pytest.raises(RuntimeError, match="web: Could not find service"):
        scheduler.restart(["web"], "Darwin")
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=3, stderr="", stdout=""))
    with pytest.raises(RuntimeError, match="launchctl exited 3"):
        scheduler.restart(["web"], "Darwin")   # a silent failure still names the job rather than reporting success


def test_restart_that_does_not_stay_up_is_a_failure_and_names_what_already_moved(monkeypatch):
    """kickstart returns when the job respawns, not when it survives: a crash-looping dashboard reported as restarted
    would leave the household with no assistant and every status surface saying fine."""
    from types import SimpleNamespace
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 503)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(scheduler, "_not_running", lambda name, *a, **k: None if name == "daemon" else "state = exited")
    with pytest.raises(RuntimeError, match=r"web restarted but is not running \(state = exited\).*already restarted.*daemon"):
        scheduler.restart(["daemon", "web"], "Darwin")


def test_not_running_reads_the_service_manager(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(scheduler.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout="\tstate = running\n", stderr=""))
    assert scheduler._not_running("web", "Darwin", 503) is None
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout="\tstate = exited\n", stderr=""))
    assert scheduler._not_running("web", "Darwin", 503) == "state = exited"
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert scheduler._not_running("web", "Darwin", 503) == "launchctl no longer lists it"
    monkeypatch.setattr(scheduler.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout="active\n", stderr=""))
    assert scheduler._not_running("web", "Linux", 503) is None
    def boom(*a, **k): raise OSError("no such manager")
    monkeypatch.setattr(scheduler.subprocess, "run", boom)
    assert scheduler._not_running("web", "Darwin", 503) is None, "a manager we cannot read is not proof of a dead job"


def test_drifted_units_notices_a_release_that_changed_the_unit_files(household_home, monkeypatch):
    home = household_home.parent
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    assert scheduler.unit_dir("Darwin") == home / "Library" / "LaunchAgents"
    assert scheduler.unit_dir("Linux") == home / ".config" / "systemd" / "user"
    assert scheduler.drifted_units("Linux") == sorted(scheduler.render("Linux"))   # nothing installed: every unit is drift, and `update` sends you to `install`
    dest = scheduler.unit_dir("Linux"); dest.mkdir(parents=True)
    for name, body in scheduler.render("Linux").items():
        (dest / name).write_text(body)
    assert scheduler.drifted_units("Linux") == []
    (dest / "finnamon-daemon.service").write_text("[Service]\nExecStart=/old/finnamon daemon\n")
    assert scheduler.drifted_units("Linux") == ["finnamon-daemon.service"]   # restarting this would keep running the old definition against the new code


def test_terminate_all_kills_the_claude_children_a_supervisor_would_orphan(monkeypatch):
    """start_new_session puts each child in its own group, so SIGTERM to the daemon leaves it running; the daemon that
    replaces us would then --resume a session the orphan still holds (daemon.py's own warning)."""
    from finnamon import claude_runner
    killed = []
    monkeypatch.setattr(claude_runner.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    with claude_runner._LIVE_LOCK:
        claude_runner._LIVE.update({4242, 4243})
    assert claude_runner.terminate_all() == 2
    assert {p for p, _ in killed} == {4242, 4243}
    assert claude_runner.terminate_all() == 0, "the registry empties, so a second signal is a no-op"


def test_terminate_all_survives_a_child_that_is_already_gone(monkeypatch):
    from finnamon import claude_runner
    def gone(*_a): raise ProcessLookupError()
    monkeypatch.setattr(claude_runner.os, "killpg", gone)
    with claude_runner._LIVE_LOCK:
        claude_runner._LIVE.add(999)
    assert claude_runner.terminate_all() == 1   # counted as signalled; a dead child needs no further action
