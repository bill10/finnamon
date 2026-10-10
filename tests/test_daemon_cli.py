import json
import os
import re
import time
import platform

import pytest

from finnamon import assistant

from finnamon import budgets, claude_runner, cli, daemon, link, query, scheduler, store, taxonomy, triage
from tests.conftest import AS_OF, seed, txn


# --- claude runner + daemon conversation -------------------------------------------------------

def test_claude_runner_ok_and_json(fake_claude):
    r = claude_runner.run("hi")
    assert r.ok and r.text == "ok" and r.session_id == "sess-1"


def test_headless_runs_load_no_mcp_server_at_all(fake_claude, monkeypatch):
    """A Telegram plugin registered for the directory would poll the household's bot from every triage run (the retired
    channel mode once registered one). --strict-mcp-config with no --mcp-config is what keeps these runs bare."""
    seen = []

    class Fake:
        pid = 4242
        stdout = stderr = None
        returncode = 0
        def communicate(self, timeout=None): return (json.dumps({"result": "ok", "session_id": "s"}), "")
    monkeypatch.setattr(claude_runner.subprocess, "Popen", lambda cmd, **kw: (seen.append(cmd), Fake())[1])

    claude_runner.run("/triage", disallowed=["Edit"])
    argv = seen[0]
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" not in argv, "a config would put servers back; strict alone is what empties the set"
    # -p has no human to refuse a command, and this session reads bank memos: the person's own settings (which may allow
    # Bash outright) must not reach it, exactly as `import --browser` already seals its own session.
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv.index("--setting-sources") < argv.index("--disallowedTools"), "before the variadic tail or it is just a tool name"
    assert argv[1:3] == ["-p", "/triage"], "the prompt stays right after -p: a variadic option before it would swallow it"

    seen.clear()
    claude_runner.run("hi", resume="sess-1")
    assert "--strict-mcp-config" in seen[0], "the daemon's conversation runs are headless too"


def test_strict_mcp_config_reaches_claude_ahead_of_the_variadic_tail(fake_claude, tmp_path):
    """What the flag does depends entirely on where it sits: --disallowedTools is variadic, so an option appended after
    it is read as one more tool name and silently does nothing — the run would load the plugin's server again."""
    argv = _argv_recording_claude(fake_claude, tmp_path)
    claude_runner.run("/triage", resume="sess-1", disallowed=claude_runner.TRIAGE_DISALLOWED)
    args = argv()
    assert args[:2] == ["-p", "/triage"], "the prompt is still the first thing after -p, not swallowed by an option"
    assert "--strict-mcp-config" in args, "the fix has to survive the trip through the real argv, not just the cmd list"
    assert args.index("--strict-mcp-config") < args.index("--disallowedTools"), "after the variadic tail it is a tool name, not a flag"
    assert args[args.index("--resume") + 1] == "sess-1", "--resume takes an optional value: its id must still follow it"
    assert "--mcp-config" not in args, "strict with no config is the empty server set; a config would refill it"


def _argv_recording_claude(fake_claude, tmp_path):
    argv_file = tmp_path / "argv"
    fake_claude.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_file}\nprintf \'{{"result":"ok","session_id":"s"}}\'\n')
    return lambda: argv_file.read_text().splitlines()


def test_every_unattended_run_disallows_the_web(fake_claude, tmp_path, conn, tg, monkeypatch):
    """At the dashboard each fetch asks a person (ask mode). A -p run has no person to ask, reads bank
    memos, and can `finnamon query`: a memo saying "post this to https://..." would be the rows out the door with
    nobody asked. So run() itself puts both on the one --disallowedTools tail it builds, last, whatever the caller asked for."""
    argv = _argv_recording_claude(fake_claude, tmp_path)

    def tail(args):
        assert args.count("--disallowedTools") == 1, "one variadic tail; a second flag would be read as a tool name by the first"
        assert args.index("--disallowedTools") > args.index("--strict-mcp-config"), "the tail is last: options after it are tool names"
        return args[args.index("--disallowedTools") + 1:]

    seed(conn)
    monkeypatch.setattr(triage, "groups", lambda c: [{"group": "g"}])   # something to judge, so the real /triage spawn happens
    assert triage.run_if_needed(conn)["ran"]
    # Value: protects=a Claude household's triage still invokes the skill as /triage; fails_when=the $triage/Codex form leaks into Claude runs; why_new=only direct run("/triage") calls were checked, not what triage.py passes; seam=none
    assert argv()[:2] == ["-p", "/triage"], "Claude runs the skill as /triage ($triage is Codex's form)"
    t = tail(argv())
    assert set(claude_runner.TRIAGE_DISALLOWED) <= set(t), "the triage write ban is still there"
    assert t[-2:] == ["WebSearch", "WebFetch"], "and the web ban is appended to it"
    claude_runner.run("hi")
    assert tail(argv()) == ["WebSearch", "WebFetch"], "a run that passes no tail of its own still gets the ban"



def test_live_registry_empties_on_both_exits(fake_claude, monkeypatch):
    """_LIVE holds pids that terminate_all() SIGKILLs by group. A pid left behind after the child is gone is a pid the
    OS can hand to something else, and the next daemon shutdown would kill that instead."""
    claude_runner._LIVE.clear()
    assert claude_runner.run("hi").ok
    assert not claude_runner._LIVE, "a finished run leaves nothing to kill"
    monkeypatch.setenv("CLAUDE_FAKE_SLEEP", "3")
    assert not claude_runner.run("hi", timeout=1).ok
    assert not claude_runner._LIVE, "the timeout path kills the group itself, so it must also drop the pid"


def test_claude_runner_timeout_and_errors(fake_claude, monkeypatch):
    monkeypatch.setenv("CLAUDE_FAKE_SLEEP", "3")
    r = claude_runner.run("hi", timeout=1)
    assert not r.ok and "timeout" in r.error and claude_runner.classify_error(r.error) == "timeout"
    monkeypatch.delenv("CLAUDE_FAKE_SLEEP")
    monkeypatch.setenv("CLAUDE_FAKE_EXIT", "1")
    monkeypatch.setenv("CLAUDE_FAKE_STDERR", "Not logged in. Please run /login")
    r = claude_runner.run("hi")
    assert not r.ok and claude_runner.classify_error(r.error) == "login expired"
    monkeypatch.setenv("FINNAMON_CLAUDE_BIN", "/nonexistent/claude")
    assert claude_runner.classify_error(claude_runner.run("hi").error) == "error"


def make_daemon(conn):
    d = daemon.Daemon(conn_factory=lambda: conn)
    return d


def msg(text, uid=111, chat=1234567890, reply_to=None, mid=500, is_bot=False, chat_type="private"):
    m = {"message_id": mid, "text": text, "chat": {"id": chat, "type": chat_type}, "from": {"id": uid, "first_name": "Bill", "is_bot": is_bot}}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": mid, "message": m}


def test_handle_update_filters(conn, tg):
    seed(conn)
    d = make_daemon(conn)
    d.handle_update(conn, msg("hi", uid=999))            # unknown user
    d.handle_update(conn, msg("hi", chat=1))             # wrong chat
    d.handle_update(conn, msg("hi", is_bot=True))        # a bot
    d.handle_update(conn, {"update_id": 1, "message": {"chat": {"id": 1234567890}, "new_chat_members": []}})  # service message
    assert d.inbox.empty()
    d.handle_update(conn, msg("it is normal", reply_to=101))
    assert d.inbox.get_nowait()["reply_to"] == 101


def test_verify_privacy_warns_group(conn, tg):
    seed(conn)
    store.set_state(conn, "chat_id", -100123)
    tg.me = {"username": "b", "can_read_all_group_messages": False}
    assert make_daemon(conn).verify_privacy(conn) is False
    assert "privacy mode" in tg.sent[-1]["text"] and store.get_state(conn, "bot_privacy_off") == "0"


# --- triage ------------------------------------------------------------------------------------

def test_triage_groups_and_set_verdict(conn):
    seed(conn)
    for k, kind in (("anom:first:t1", "anomaly:first_merchant"), ("anom:nosrc:t1", "anomaly:no_source"), ("anom:rec:s1:2026-09", "anomaly:recurring_changed")):
        conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly',?,?,?,'{}',?)",
                     (kind, k, "t1" if "t1" in k else None, AS_OF))
    g = triage.groups(conn)
    assert [x["group"] for x in g] == ["anom:rec:s1:2026-09", "t1"] and len(g[1]["candidates"]) == 2
    assert triage.set_verdict(conn, "t1", "promote", "high", "Never paid this merchant.") == 2
    assert triage.set_verdict(conn, "anom:rec:s1:2026-09", "suppress", "low", "price change is small") == 1
    assert triage.groups(conn) == []
    with pytest.raises(ValueError):
        triage.set_verdict(conn, "t1", "maybe", "high", "")


def test_triage_gives_up_loudly_after_three_failures(conn, fake_claude, monkeypatch):
    seed(conn)
    conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:no_source','k','t1','{}',?)", (AS_OF,))
    monkeypatch.setenv("CLAUDE_FAKE_EXIT", "1")
    for i in range(3):
        r = triage.run_if_needed(conn, timeout=5)
    assert r["gave_up"] == 1
    a = conn.execute("SELECT verdict, reason, triage_attempts FROM alerts WHERE key='k'").fetchone()
    assert tuple(a) == ("suppress", "triage_unavailable", 3)
    assert conn.execute("SELECT count(*) FROM alerts WHERE kind='triage_error'").fetchone()[0] == 1


# --- link / joint accounts --------------------------------------------------------------------------

def test_detect_mirrors_by_transactions_and_persistent_id(conn):
    seed(conn)
    conn.execute("INSERT INTO owners (owner) VALUES ('jane')")
    conn.execute("INSERT INTO items (item_id, institution_id, institution, owner, created_at) VALUES ('item2','ins_1','Chase','jane','2026-09-19')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('chk_j','item2','Chase Checking','depository','checking','4821','jane')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, persistent_account_id) VALUES ('sav_j','item2','Savings','depository','savings','0093','jane','pid-1')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, persistent_account_id) VALUES ('sav','item1','Savings','depository','savings','0093','bill','pid-1')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('free_j','item2','Freedom','credit','credit card','2210','jane')")
    for i in range(10):
        txn(conn, f"b{i}", "chk", f"2026-09-{1 + i:02d}", 10 + i, f"m{i}")
        txn(conn, f"j{i}", "chk_j", f"2026-09-{1 + i:02d}", 10 + i, f"m{i}")
    m = link.detect_mirrors(conn, "item2")
    got = {x["new"]: x["match"] for x in m}
    assert got["chk_j"] == "10 of 10 transactions identical" and got["sav_j"] == "persistent_account_id" and "free_j" not in got
    link.mark_mirror(conn, "chk_j", "chk")
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT account_id, owner, mirror_of FROM accounts")}
    assert rows["chk_j"] == ("joint", "chk") and rows["chk"] == ("joint", None)
    # failover: bill's item breaks, jane's is healthy
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED' WHERE item_id='item1'")
    assert link.failover(conn, "chk") == "chk_j"
    rows = {r[0]: r[1] for r in conn.execute("SELECT account_id, mirror_of FROM accounts")}
    assert rows == {**rows, "chk": "chk_j", "chk_j": None}


def test_wait_for_public_token(monkeypatch):
    from finnamon import plaid_api
    resps = [{"link_sessions": []}, {"link_sessions": [{"results": {"item_add_results": [{"public_token": "public-x"}]}}]}]
    monkeypatch.setattr(plaid_api, "link_token_get", lambda t: resps.pop(0))
    assert link.wait_for_public_token("lt", sleep=lambda s: None) == "public-x"
    monkeypatch.setattr(plaid_api, "link_token_get", lambda t: {"link_sessions": [{"finished_at": "x", "results": {"item_add_results": []}}]})
    assert link.wait_for_public_token("lt", sleep=lambda s: None) is None


# --- budgets / taxonomy / query / scheduler -------------------------------------------------------

def test_taxonomy_resolution():
    assert taxonomy.resolve("groceries") == ("FOOD_AND_DRINK_GROCERIES", [])
    assert taxonomy.resolve("car stuff") == ("TRANSPORTATION", [])
    assert taxonomy.resolve("FOOD_AND_DRINK_COFFEE") == ("FOOD_AND_DRINK_COFFEE", [])
    code, cands = taxonomy.resolve("other")
    assert code is None and len(cands) > 3
    assert taxonomy.primary_of("TRAVEL_FLIGHTS") == "TRAVEL" and taxonomy.primary_of("TRAVEL") == "TRAVEL"


def test_budget_set_list_suggest_threshold_category_normal(conn):
    seed(conn)
    for i in range(1, 7):
        txn(conn, f"g{i}", "chk", f"2026-0{3 + i // 3}-1{i % 3}", 200 + i, "WF", "Whole Foods", "mch_wf")
        txn(conn, f"c{i}", "chk", f"2026-0{3 + i // 3}-2{i % 3}", 300, "COSTCO", "Costco", "mch_costco", "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES")
    txn(conn, "now", "chk", "2026-09-10", 210, "WF", "Whole Foods", "mch_wf")
    assert budgets.budget_set(conn, "Groceries", 600)["category"] == "FOOD_AND_DRINK_GROCERIES"
    assert budgets.budget_set(conn, "shopping", 300, "amazon")["category"] == "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES"
    with pytest.raises(ValueError, match="is not a category"):
        budgets.budget_set(conn, "stuff", 10)
    lst = budgets.budget_list(conn, "2026-09-19 12:00:00")
    g = next(b for b in lst if b["name"] == "groceries")
    assert g["spent"] == 210 and g["pace"] == pytest.approx(210 * 30 / 19, abs=0.01)
    s = budgets.suggest(conn, 6, AS_OF)
    cats = {c["category"]: c for c in s["categories"]}
    assert "FOOD_AND_DRINK_GROCERIES" in cats and cats["GENERAL_MERCHANDISE_SUPERSTORES"]["top_merchants"][0]["merchant"] == "Costco"
    # Costco counted as groceries after an override
    budgets.category_set(conn, "Costco", "groceries")
    assert conn.execute("SELECT canonical, pfc_primary FROM category_override").fetchone()[0] == "mch_costco"
    assert budgets.threshold_set(conn, "checking", 1000)["mask"] == "4821"
    assert store.setting(conn, "low_balance_threshold", "chk") == "1000"
    with pytest.raises(ValueError):
        budgets.threshold_set(conn, "nope", 1)
    conn.execute("INSERT INTO alerts (tier, kind, key, transaction_id, payload_json, as_of) VALUES ('anomaly','anomaly:first_merchant','k','now','{}',?)", (AS_OF,))
    n = budgets.normal(conn, alert_id=1, note="weekly shop")
    assert n["canonical"] == "mch_wf" and n["kind"] == "anomaly:first_merchant"
    assert conn.execute("SELECT resolved_at IS NOT NULL FROM alerts").fetchone()[0] == 1


def test_query_is_read_only(home, conn):
    seed(conn)
    assert query.run("SELECT count(*) AS n FROM accounts")[0]["n"] == 2
    with pytest.raises(ValueError):
        query.run("DELETE FROM accounts")
    with pytest.raises(Exception):
        query.run("WITH x AS (SELECT 1) INSERT INTO owners (owner) VALUES ('z')")


def test_scheduler_renders_both_oses(household_home):
    """The household's jobs keep exactly these names: a rename would leave the installed ones running beside new ones."""
    mac = scheduler.render("Darwin")
    assert {"com.finnamon.daemon.plist", "com.finnamon.heartbeat.plist"} <= set(mac)   # plus the web job when web/ is installed
    assert "<key>KeepAlive</key><true/>" in mac["com.finnamon.daemon.plist"] and "StartInterval" in mac["com.finnamon.heartbeat.plist"]
    assert "FINNAMON_HOME" in mac["com.finnamon.daemon.plist"]
    lin = scheduler.render("Linux")
    assert {"finnamon-daemon.service", "finnamon-heartbeat.service", "finnamon-heartbeat.timer"} <= set(lin)
    assert "Restart=always" in lin["finnamon-daemon.service"] and "Persistent=true" in lin["finnamon-heartbeat.timer"]


def test_web_job_keeps_the_tailscale_name_across_reboots(household_home, monkeypatch):
    """launchctl setenv is gone after a reboot; the name the dashboard answers to is baked into the web job instead."""
    monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/x/web/server.js"])
    assert "FINNAMON_WEB_HOSTS" not in scheduler.render("Darwin")["com.finnamon.web.plist"]
    monkeypatch.setenv("FINNAMON_WEB_HOSTS", "mac.tail&net.ts.net")
    mac, lin = scheduler.render("Darwin"), scheduler.render("Linux")
    assert "<key>FINNAMON_WEB_HOSTS</key><string>mac.tail&amp;net.ts.net</string>" in mac["com.finnamon.web.plist"]
    assert "FINNAMON_WEB_HOSTS" not in mac["com.finnamon.daemon.plist"]
    assert "Environment=FINNAMON_WEB_HOSTS=mac.tail&net.ts.net\n" in lin["finnamon-web.service"] and "FINNAMON_WEB_HOSTS" not in lin["finnamon-daemon.service"]


def test_cli_smoke(home, capsys):
    cli.main(["budget", "list"])
    assert capsys.readouterr().out.strip() == "[]"
    cli.main(["category", "resolve", "dining"])
    assert "FOOD_AND_DRINK_RESTAURANT" in capsys.readouterr().out
    cli.main(["install", "--dry-run"])
    assert "finnamon" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["query", "DROP TABLE x"])


def test_owner_add_is_human_only_but_reads_are_not(home, conn, monkeypatch, capsys):
    from finnamon import cli
    seed(conn)
    monkeypatch.setenv("CLAUDECODE", "")                          # presence, not truthiness
    for argv in (["owner", "add", "eve", "--user-id", "7"], ["owner", "--user-id", "7", "add", "eve"]):   # argparse accepts either order
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert conn.execute("SELECT count(*) FROM owners WHERE owner='eve'").fetchone()[0] == 0
    cli.main(["owner", "list"]); assert '"bill"' in capsys.readouterr().out


def test_channel_is_a_no_op_that_says_so(home, conn, capsys):
    """`finnamon channel` (any old form) only says Telegram has one mode; it switches nothing, even from a Claude session."""
    from finnamon import cli
    seed(conn); store.set_state(conn, "inbound", "session")
    for argv in (["channel"], ["channel", "on"], ["channel", "off"], ["channel", "status"]):
        cli.main(argv)
        assert capsys.readouterr().out.strip() == "Telegram always shares the dashboard's conversation; there is nothing to switch."
    assert store.get_state(conn, "inbound") == "session"


@pytest.mark.parametrize("was", ["channel", None])
def test_a_retired_mode_moves_to_session_once_with_one_line(conn, was):
    seed(conn)
    store.set_state(conn, "inbound", was); store.set_state(conn, "session", "old-sess"); store.set_state(conn, "channel_deaf_since", "2026-10-01 10:00:00")
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','channel_deaf','health:channel_deaf:x','{}',?)", (store.now_local(),))
    line = daemon.migrate_inbound(conn)
    assert line.startswith("Telegram now always shares the dashboard's conversation") and ("channel plugin" in line) == (was == "channel") and (".claude/channels/telegram/.env" in line) == (was == "channel")
    assert store.get_state(conn, "inbound") == "session" and store.get_state(conn, "session") is None and store.get_state(conn, "channel_deaf_since") is None
    assert conn.execute("SELECT resolution FROM alerts WHERE kind='channel_deaf'").fetchone()[0] == "superseded", "an unsent deaf notice is never sent"
    assert daemon.migrate_inbound(conn) is None, "once"


# Value: protects=a household with no bot still leaves the retired modes; fails_when=migrate_inbound checks bot_configured before setting inbound, or announces to a botless household; why_new=conftest forces bot_configured True everywhere, so the no-bot branch never ran; seam=none
def test_a_retired_mode_moves_without_a_bot_and_says_nothing(conn, monkeypatch):
    seed(conn); store.set_state(conn, "inbound", "channel"); store.set_state(conn, "session", "old-sess")
    monkeypatch.setattr(daemon, "bot_configured", lambda: False)
    assert daemon.migrate_inbound(conn) is None
    assert store.get_state(conn, "inbound") == "session" and store.get_state(conn, "session") is None


def test_moving_off_channel_drops_the_backlog_the_plugin_answered(conn, tg, monkeypatch):
    """The plugin may die before confirming its last batch; Telegram then replays it to the daemon, already answered."""
    import time
    seed(conn); store.set_state(conn, "inbound", "channel")
    daemon.migrate_inbound(conn)
    d = make_daemon(conn)
    d.handle_update(conn, msg("it is normal"))                          # date 0: sent before the move
    assert d.inbox.empty()
    m = msg("and now?"); m["message"]["date"] = int(time.time()) + 60
    d.handle_update(conn, m)
    assert d.inbox.get_nowait()["text"] == "and now?"
    assert store.get_state(conn, "inbound_off_at"), "kept until a poll comes back empty"
    d.verify_privacy = lambda c: True
    monkeypatch.setattr(daemon.telegram, "get_updates", lambda *a, **k: (d.stop.set(), [])[1])
    d.poll_loop()                                                        # the backlog is drained: the filter goes
    assert store.get_state(conn, "inbound_off_at") is None


def test_a_member_writing_privately_is_told_once_where_to_ask(conn, tg):
    seed(conn)
    d = make_daemon(conn)
    for _ in range(2):
        d.handle_update(conn, msg("how much on dining?", chat=111, chat_type="private"))
    assert d.inbox.empty() and len(tg.sent) == 1 and "household group" in tg.sent[0]["text"]
    d.handle_update(conn, msg("hi", uid=999, chat=999, chat_type="private"))   # a stranger's DM gets nothing from this path
    assert len(tg.sent) == 1


def test_the_daemon_start_migrates_a_retired_mode(conn, monkeypatch, caplog):
    seed(conn); store.set_state(conn, "inbound", "channel")
    called = []
    monkeypatch.setattr(daemon.assistant, "unregister_telegram_plugin", lambda exe: called.append(exe) or [])
    d = daemon.Daemon(conn_factory=lambda: conn)
    for name in ("sync_loop", "poll_loop", "converse_loop"):
        monkeypatch.setattr(d, name, lambda: None)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    with caplog.at_level("INFO", logger="finnamon.daemon"):
        d.start(exit=lambda code: pytest.fail(f"exited {code}"))
    assert store.get_state(conn, "inbound") == "session" and "Telegram now always shares" in caplog.text
    import time
    for _ in range(100):   # the plugin's removal runs on a thread of its own, so a stop never waits behind it
        if called:
            break
        time.sleep(0.02)
    assert called, "the first start after the release takes channel mode's plugin off too"


def test_owner_add_by_id_upserts_and_keeps_display_name(home, conn, capsys):
    """On an existing owner --user-id only changes the id."""
    from finnamon import cli
    seed(conn)                                                   # bill: id 111, display_name 'Bill'
    cli.main(["owner", "add", "bill", "--user-id", "222"])
    assert json.loads(capsys.readouterr().out) == {"owner": "bill", "telegram_user_id": 222, "replaced": 111}   # a typo here is visible
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='bill'").fetchone()) == (222, "Bill")
    cli.main(["owner", "add", "jane", "--user-id", "333"]); capsys.readouterr()
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='jane'").fetchone()) == (333, "Jane")


def test_owner_add_by_id_refuses_duplicates_and_a_409_backs_off(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli, telegram
    seed(conn)
    cli.main(["owner", "add", "jane", "--user-id", "222"])
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "eve", "--user-id", "222"])        # already jane's id
    assert "already jane" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "eve", "--user-id", "0"])
    assert "positive" in capsys.readouterr().err
    # a 409 from another consumer backs the daemon off instead of hammering
    waits = []
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: (_ for _ in ()).throw(telegram.TelegramError(409, "Conflict")))
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: (waits.append(t), d.stop.set()))
    d.poll_loop()
    from finnamon import daemon
    assert waits == [daemon.CONFLICT_FIRST_BACKOFF_S]   # usually our own predecessor still long-polling after `install`


def test_owner_add_by_id_same_owner_same_id_and_display_name(conn):
    """Re-running `owner add bill --user-id 111` is a no-op, not 'already bill'; an existing display_name is kept."""
    from finnamon import owners
    seed(conn)                                                   # bill: 111, 'Bill'
    assert owners.add_by_id(conn, "bill", 111) == {"owner": "bill", "telegram_user_id": 111}
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='bill'").fetchone()) == (111, "Bill")
    assert conn.execute("SELECT count(*) FROM owners").fetchone()[0] == 1


def test_owner_add_requires_a_name(home, conn, capsys):
    """`owner add --user-id 5` with no name must not insert an owner=NULL row (owner is a nullable TEXT PRIMARY KEY)."""
    from finnamon import cli
    seed(conn)
    for argv in (["owner", "add", "--user-id", "5"], ["owner", "add"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "needs a name" in capsys.readouterr().err
    assert conn.execute("SELECT count(*) FROM owners WHERE owner IS NULL").fetchone()[0] == 0


def test_settings_owner_gates_and_no_telegram_plugin():
    """The Claude permission set is the first gate for the owner surface; pin it like tests/test_link_more.py pins link."""
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]
    assert "Bash(finnamon owner list)" in perms["allow"]
    assert "Bash(finnamon owner add*)" in perms["deny"]
    assert "Bash(finnamon owner *)" not in perms["deny"]   # would swallow `owner list`
    assert any("channels/telegram/.env" in d for d in perms["deny"] if d.startswith("Read(")), "the retired plugin's token file stays off limits"
    settings = (assistant.BUNDLE / ".claude/settings.json").read_text()
    assert "mcp__plugin_telegram" not in settings and "reply-guard" not in settings and "finnamon channel" not in settings


def test_repeated_409s_tell_the_household_once_and_show_in_status(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli, daemon, store, telegram
    seed(conn)
    sent, waits, calls = [], [], []
    marked = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, text, **k: (sent.append((chat, text)), marked.append(store.get_state(conn, "inbound_conflict_at"))) and 1)
    def get_updates(offset=None, timeout=25, **k):
        calls.append(1)
        if len(calls) <= daemon.CONFLICTS_BEFORE_NOTICE + 1:
            raise telegram.TelegramError(409, "Conflict")
        d.stop.set()                                                   # one clean poll, then done
        return []
    monkeypatch.setattr(telegram, "get_updates", get_updates)
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: waits.append(t))
    d.poll_loop()
    assert waits[:4] == [daemon.CONFLICT_FIRST_BACKOFF_S, daemon.CONFLICT_BACKOFF_S, daemon.CONFLICT_BACKOFF_S, daemon.CONFLICT_BACKOFF_S]
    assert len(sent) == 1 and "Stop that program" in sent[0][1]     # once per episode, not per 409
    assert marked and marked[0]                                       # status was marked before the notice went out
    assert store.get_state(conn, "inbound_conflict_at") is None       # the successful poll cleared it
    # while it lasts, status shows it
    store.set_state(conn, "inbound_conflict_at", "2026-09-20 10:00:00")
    cli.main(["status"]); assert '"inbound_conflict_at": "2026-09-20 10:00:00"' in capsys.readouterr().out


def test_conflict_notice_without_chat_id_marks_status_only_and_send_errors_are_swallowed(conn, tg, monkeypatch):
    from finnamon import store, telegram
    seed(conn)
    store.set_state(conn, "chat_id", None)   # 409s before init recorded the household chat
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(1))
    d = make_daemon(conn)
    d.conflict_notice(conn)
    assert sent == [] and store.get_state(conn, "inbound_conflict_at")
    store.set_state(conn, "chat_id", 1234567890)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: (_ for _ in ()).throw(telegram.TelegramError(500, "boom")))
    d.conflict_notice(conn)                   # must not kill the poll thread's 409 handling
