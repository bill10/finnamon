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
    """The Telegram channel plugin is registered local to this checkout, and a second copy of its server kills the
    household session's about a second after starting, with no reconnect: every triage run ended the household's
    Telegram until the dashboard was restarted. --strict-mcp-config with no --mcp-config is what keeps these runs bare."""
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
    t = tail(argv())
    assert set(claude_runner.TRIAGE_DISALLOWED) <= set(t), "the triage write ban is still there"
    assert t[-2:] == ["WebSearch", "WebFetch"], "and the web ban is appended to it"

    make_daemon(conn).converse(conn, {"owner": "bill", "text": "what's my house worth?", "reply_to": None, "message_id": 1})
    t = tail(argv())
    assert t == ["WebSearch", "WebFetch"], "the daemon's Telegram run passes no tail of its own, and still gets the ban"



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


def test_converse_prefix_session_and_reply(conn, tg, fake_claude, monkeypatch):
    seed(conn)
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, telegram_chat_id, telegram_message_id) VALUES ('anomaly','anomaly:no_source','k','{}',?,'1234567890',101)", (AS_OF,))
    d = make_daemon(conn)
    calls = []
    real = claude_runner.run
    def spy(prompt, resume=None, timeout=120):
        calls.append((prompt, resume)); return real(prompt, resume, timeout)
    monkeypatch.setattr(claude_runner, "run", spy)
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": "Got it, that's normal now.", "session_id": "sess-9"}))
    d.converse(conn, {"owner": "bill", "text": "it is normal", "reply_to": 101, "message_id": 500, "chat_id": "1234567890"})
    assert calls[0] == ("[Telegram, bill; replying to alert 1] it is normal", None)
    assert store.get_state(conn, "session") == "sess-9"
    assert tg.sent[-1]["text"] == "Got it, that&#x27;s normal now." or "normal now" in tg.sent[-1]["text"]
    fb = conn.execute("SELECT alert_id, owner, parsed_action FROM feedback").fetchone()
    assert tuple(fb) == (1, "bill", "claude")
    d.converse(conn, {"owner": "bill", "text": "thanks", "reply_to": None, "message_id": 501})
    assert calls[1][1] == "sess-9"  # resumed


def test_converse_no_reply_sends_nothing(conn, tg, fake_claude, monkeypatch):
    seed(conn)
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": "NO_REPLY", "session_id": "s"}))
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "pick up milk?", "reply_to": None, "message_id": 1})
    assert tg.sent == []


def test_converse_failure_posts_message(conn, tg, fake_claude, monkeypatch):
    seed(conn)
    monkeypatch.setenv("CLAUDE_FAKE_EXIT", "1"); monkeypatch.setenv("CLAUDE_FAKE_STDERR", "auth error")
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 1})
    assert "Claude is unavailable (login expired)" in tg.sent[-1]["text"]
    assert conn.execute("SELECT parsed_action FROM feedback").fetchone()[0] == "claude_error:login expired"


def test_converse_session_lost_retries_fresh(conn, tg, fake_claude, monkeypatch):
    seed(conn)
    store.set_state(conn, "session", "gone")
    seen = []
    def fake(prompt, resume=None, timeout=120):
        seen.append(resume)
        if resume:
            return claude_runner.Result(False, "", None, error="No conversation found with session id gone")
        return claude_runner.Result(True, "fresh", "new-sess")
    monkeypatch.setattr(claude_runner, "run", fake)
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 1})
    assert seen == ["gone", None] and store.get_state(conn, "session") == "new-sess" and tg.sent[-1]["text"] == "fresh"


def test_converse_sends_chart_path_as_photo(conn, tg, fake_claude, monkeypatch, tmp_path):
    seed(conn)
    png = tmp_path / "charts" / "budgets-1.png"
    png.parent.mkdir(); png.write_bytes(b"png")
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": f"Here you go:\n{png}", "session_id": "s"}))
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "chart", "reply_to": None, "message_id": 1})
    assert tg.photos[0]["path"] == str(png) and "Here you go" in tg.sent[-1]["text"] and str(png) not in tg.sent[-1]["text"]


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


def test_channel_mode_stops_the_daemon_polling_telegram(conn, tg, monkeypatch):
    from finnamon import store, telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not poll: the channel plugin owns getUpdates")))
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.poll_loop()
    assert store.get_state(conn, "daemon_alive_at")            # still heartbeating for `owner add` / status


def test_channel_command_and_owner_add_by_id(home, conn, fake_claude, monkeypatch, capsys):
    from finnamon import cli, store
    seed(conn)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    with pytest.raises(SystemExit):
        cli.main(["channel", "on"])                              # a person's call
    cli.main(["channel", "status"]); assert '"daemon"' in capsys.readouterr().out
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE")
    monkeypatch.setattr(cli.shutil, "which", lambda n, path=None: None)     # no bun anywhere the jobs look: the plugin could not start, say so now
    cli.main(["channel", "on"])
    out, err = capsys.readouterr()
    assert "claude --permission-mode dontAsk --channels plugin:telegram@claude-plugins-official --disallowedTools WebSearch WebFetch" in out and "finnamon install" in out
    assert "bun" in err and "bun.sh" in err
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    cli.main(["install"]); assert "bun" in capsys.readouterr().err   # and again where the jobs are (re)written
    monkeypatch.setattr(cli.shutil, "which", lambda n, path=None: "/opt/bun/bin/bun")
    cli.main(["install"]); out2, err2 = capsys.readouterr()
    assert err2 == "" and "registered the Telegram channel plugin for" in out2   # install registers the plugin for the assistant directory
    assert "TELEGRAM_BOT_TOKEN=..." in out and re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", out) is None   # never a real token
    assert store.get_state(conn, "inbound") == "channel"
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "jane"])                        # the code dance would poll the bot; refused in channel mode
    assert "--user-id" in capsys.readouterr().err
    cli.main(["owner", "add", "jane", "--user-id", "412587349"])
    assert conn.execute("SELECT telegram_user_id FROM owners WHERE owner='jane'").fetchone()[0] == 412587349
    cli.main(["status"]); assert '"inbound": "channel"' in capsys.readouterr().out
    cli.main(["channel", "off"]); assert store.get_state(conn, "inbound") is None


def test_channel_mode_is_read_each_tick_and_verifies_privacy_once(conn, tg, monkeypatch):
    """The flag flips while the daemon runs: no restart needed. Tick 1 (channel) sleeps 30 and skips getUpdates;
    tick 2 (daemon again) polls. getMe/verify_privacy runs once at thread start in either mode."""
    from finnamon import store, telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    polled, waits, verified = [], [], []
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: verified.append(1) or True)
    def wait(t=None):
        waits.append(t)
        if len(waits) == 1:
            store.set_state(conn, "inbound", None)   # `finnamon channel off` from another process
        else:
            d.stop.set()
    monkeypatch.setattr(d.stop, "wait", wait)
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: polled.append(1) or d.stop.set() or [])
    d.poll_loop()
    from finnamon import daemon
    assert waits == [daemon.CHANNEL_IDLE_S] and polled == [1] and verified == [1]


def test_channel_off_and_owner_add_are_human_only_but_reads_are_not(home, conn, monkeypatch, capsys):
    from finnamon import cli, store
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    monkeypatch.setenv("CLAUDECODE", "")                          # presence, not truthiness
    with pytest.raises(SystemExit):
        cli.main(["channel", "off"])
    assert store.get_state(conn, "inbound") == "channel"
    for argv in (["owner", "add", "eve", "--user-id", "7"], ["owner", "--user-id", "7", "add", "eve"]):   # argparse accepts either order
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert conn.execute("SELECT count(*) FROM owners WHERE owner='eve'").fetchone()[0] == 0
    cli.main(["owner", "list"]); assert '"bill"' in capsys.readouterr().out
    cli.main(["channel", "status"]); assert '"channel"' in capsys.readouterr().out


def test_owner_add_by_id_upserts_and_keeps_display_name(home, conn, capsys):
    """--user-id works in either inbound mode; on an existing owner it only changes the id."""
    from finnamon import cli
    seed(conn)                                                   # bill: id 111, display_name 'Bill'; inbound = daemon
    cli.main(["owner", "add", "bill", "--user-id", "222"])
    assert json.loads(capsys.readouterr().out) == {"owner": "bill", "telegram_user_id": 222, "replaced": 111}   # a typo here is visible
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='bill'").fetchone()) == (222, "Bill")
    cli.main(["owner", "add", "jane", "--user-id", "333"]); capsys.readouterr()
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='jane'").fetchone()) == (333, "Jane")
    cli.main(["status"]); assert '"inbound": "daemon"' in capsys.readouterr().out


def test_channel_mode_guards_at_the_source(home, conn, tg, monkeypatch, capsys):
    """The library refuses to poll the bot in channel mode even when called directly; duplicate ids are refused."""
    from finnamon import cli, owners, store, telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    for fn in (lambda: owners.add_first(conn, "x", "ABC123"), lambda: owners.add_member(conn, "x", "ABC123")):
        with pytest.raises(RuntimeError) as e:
            fn()
        assert "--user-id" in str(e.value)
    cli.main(["owner", "add", "jane", "--user-id", "222"])
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "eve", "--user-id", "222"])        # already jane's id
    assert "already jane" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "eve", "--user-id", "0"])
    assert "positive" in capsys.readouterr().err
    # a 409 from another consumer backs the daemon off instead of hammering
    store.set_state(conn, "inbound", None)
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


def test_channel_on_off_print_the_next_step(home, conn, capsys):
    from finnamon import cli
    seed(conn)
    cli.main(["channel", "on"]); on = capsys.readouterr().out
    assert "finnamon owner add <name> --user-id" in on and "/telegram:access" in on
    cli.main(["channel", "off"]); off = capsys.readouterr().out
    assert "finnamon install" in off and "Stop the channel session first" in off


def test_owner_add_requires_a_name(home, conn, capsys):
    """`owner add --user-id 5` with no name must not insert an owner=NULL row (owner is a nullable TEXT PRIMARY KEY)."""
    from finnamon import cli
    seed(conn)
    for argv in (["owner", "add", "--user-id", "5"], ["owner", "add"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "needs a name" in capsys.readouterr().err
    assert conn.execute("SELECT count(*) FROM owners WHERE owner IS NULL").fetchone()[0] == 0


def test_settings_channel_and_owner_gates():
    """The Claude permission set is the first gate for the channel surface; pin it like tests/test_link_more.py pins link."""
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]
    assert {"Bash(finnamon channel status)", "Bash(finnamon owner list)"} <= set(perms["allow"])
    assert {"Bash(finnamon owner add*)", "Bash(finnamon channel on*)", "Bash(finnamon channel off*)"} <= set(perms["deny"])
    assert "Bash(finnamon owner *)" not in perms["deny"]   # would swallow `owner list`
    assert any("channels/telegram/.env" in d for d in perms["deny"] if d.startswith("Read("))
    hooks = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["hooks"]["PreToolUse"]
    guard = [h for h in hooks if h["matcher"] == "mcp__plugin_telegram_telegram__reply"]
    assert guard and guard[0]["hooks"][0]["command"].startswith("finnamon hook reply-guard") and "exit 2" in guard[0]["hooks"][0]["command"]
    # the plugin's tools are mcp__plugin_<plugin>_<server>__<tool>; under dontAsk a name that drifts means silent refusal
    assert {"mcp__plugin_telegram_telegram__reply", "mcp__plugin_telegram_telegram__react"} <= set(perms["allow"]) and guard[0]["matcher"] in perms["allow"]


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
    assert len(sent) == 1 and "finnamon channel on" in sent[0][1]     # once per episode, not per 409
    assert marked and marked[0]                                       # status was marked before the notice went out
    assert store.get_state(conn, "inbound_conflict_at") is None       # the successful poll cleared it
    # while it lasts, status shows it
    store.set_state(conn, "inbound_conflict_at", "2026-09-20 10:00:00")
    cli.main(["status"]); assert '"inbound_conflict_at": "2026-09-20 10:00:00"' in capsys.readouterr().out


def test_switching_to_channel_mode_acknowledges_the_last_batch(conn, tg, monkeypatch):
    """Telegram only forgets an update once a later poll passes a higher offset; without the ack the plugin's
    first poll would replay what the daemon already handled ("it is normal" twice)."""
    from finnamon import daemon, store, telegram
    seed(conn)
    store.set_state(conn, "telegram_offset", "41")
    calls = []
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d, "handle_update", lambda c, u: None)
    idle = []
    def wait(t=None):
        if t == daemon.CHANNEL_IDLE_S:
            idle.append(1)
            if len(idle) == 2:
                d.stop.set()
        else:
            store.set_state(conn, "inbound", "channel")   # `finnamon channel on` between polls
    monkeypatch.setattr(d.stop, "wait", wait)
    monkeypatch.setattr(telegram, "get_updates", lambda offset=None, timeout=25, **k: calls.append((offset, timeout)) or (d.stop.wait(1) or []))
    d.poll_loop()
    assert calls == [(41, 25), (41, 0)]   # one real poll, one zero-timeout ack, then two idle ticks with no further call


def test_ack_skipped_without_offset_and_failure_is_swallowed(conn, tg, monkeypatch):
    from finnamon import store, telegram
    seed(conn)
    d = make_daemon(conn)
    calls = []
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: calls.append(k) or (_ for _ in ()).throw(telegram.TelegramError(500, "boom")))
    d.ack_updates(conn)                       # never polled: nothing to confirm, no call
    assert calls == []
    store.set_state(conn, "telegram_offset", "7")
    d.ack_updates(conn)                       # Telegram down: logged, not raised
    assert calls == [{"timeout": 0}]


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


def test_channel_on_clears_a_conflict_and_a_restart_still_acks(conn, tg, monkeypatch):
    """`channel on` is what the 409 notice asks for: entering channel mode clears inbound_conflict_at and the
    counter. And the ack keys off state on disk, so a daemon restarted straight into channel mode (its
    predecessor's batch still unconfirmed at Telegram) acks too."""
    from finnamon import daemon, store, telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    store.set_state(conn, "telegram_offset", "41")
    store.set_state(conn, "inbound_conflict_at", "2026-09-20 10:00:00")
    calls = []
    monkeypatch.setattr(telegram, "get_updates", lambda offset=None, timeout=25, **k: calls.append((offset, timeout)) or [])
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.poll_loop()
    assert calls == [(41, 0)] and store.get_state(conn, "inbound_conflict_at") is None


def test_failed_ack_is_retried_on_the_idle_tick_then_given_up(conn, tg, monkeypatch):
    from finnamon import daemon, store, telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    store.set_state(conn, "telegram_offset", "41")
    calls, waits = [], []
    monkeypatch.setattr(telegram, "get_updates", lambda offset=None, timeout=25, **k: calls.append(timeout) or (_ for _ in ()).throw(telegram.TelegramError(409, "Conflict")))
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    def wait(t=None):
        waits.append(t)
        if len(waits) == daemon.ACK_TRIES + 2:
            d.stop.set()
    monkeypatch.setattr(d.stop, "wait", wait)
    d.poll_loop()
    assert calls == [0] * daemon.ACK_TRIES and set(waits) == {daemon.CHANNEL_IDLE_S}   # retried, then idle without kicking the plugin further
    # a real poll re-arms the ack: channel off, one poll, channel on again → one more ack
    d.stop.clear(); calls.clear(); waits.clear()
    store.set_state(conn, "inbound", None)
    monkeypatch.setattr(telegram, "get_updates", lambda offset=None, timeout=25, **k: calls.append(timeout) or (store.set_state(conn, "inbound", "channel") if timeout else d.stop.set()) or [])
    monkeypatch.setattr(d.stop, "wait", lambda t=None: None)
    d.poll_loop()
    assert calls == [25, 0]


def test_channel_off_drops_messages_the_plugin_already_answered(home, conn, tg, monkeypatch):
    """Telegram replays the plugin's last un-acked batch to the daemon; those were sent before `channel off`."""
    from finnamon import cli, store, telegram
    seed(conn)
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000)
    cli.main(["channel", "off"])
    assert store.get_state(conn, "inbound_off_at") is None, "from daemon mode nothing was answered elsewhere: nothing to drop"
    store.set_state(conn, "inbound", "channel")
    cli.main(["channel", "off"])
    assert store.get_state(conn, "inbound_off_at") == "1800000000"
    d = make_daemon(conn)
    old = msg("it is normal", chat_type="group"); old["message"]["date"] = 1_799_999_000
    new = msg("and this one?", mid=501, chat_type="group"); new["message"]["date"] = 1_800_000_100
    d.handle_update(conn, old)
    d.handle_update(conn, new)
    assert [q["message_id"] for q in list(d.inbox.queue)] == [501]
    # the stamp lives only until the daemon has drained the backlog: an empty poll clears it
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: d.stop.set() or [])
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    d.poll_loop()
    assert store.get_state(conn, "inbound_off_at") is None


def test_reply_guard_hook_allows_household_chats_and_charts_only(home, conn, monkeypatch, capsys):
    from finnamon import cli, config, store
    seed(conn)                                                   # chat_id 1234567890; bill is 111
    charts = config.charts_dir(); charts.mkdir(parents=True, exist_ok=True)
    (charts / "ok.png").write_bytes(b"png")
    def run(payload):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"tool_name": "mcp__plugin_telegram_telegram__reply", "tool_input": payload})))
        try:
            cli.main(["hook", "reply-guard"]); return 0
        except SystemExit as e:
            return e.code
    assert run({"chat_id": 1234567890, "text": "hi"}) == 0
    assert run({"chat_id": "111", "text": "hi", "files": [str(charts / "ok.png")]}) == 0      # a member's private chat, a chart
    assert run({"chat_id": 999, "text": "hi"}) == 2 and "not the household" in capsys.readouterr().err
    assert run({"chat_id": 1234567890, "text": "hi", "files": [str(home / "secrets.toml")]}) == 2
    assert "only charts" in capsys.readouterr().err
    assert run({"chat_id": 1234567890, "files": [str(charts / ".." / "secrets.toml")]}) == 2   # traversal
    (home / "x.png").write_bytes(b"png"); (charts / "notes.txt").write_text("x")
    assert run({"chat_id": 1234567890, "files": [str(home / "x.png")]}) == 2                  # a png outside charts
    assert run({"chat_id": 1234567890, "files": [str(charts / "notes.txt")]}) == 2            # a non-png inside charts
    monkeypatch.setenv("CLAUDECODE", "")
    assert run({"chat_id": 1234567890, "text": "hi"}) == 0                                    # the hook runs inside the Claude session
    assert run({"text": "no chat"}) == 2                                                     # a missing chat id never matches "None"
    store.set_state(conn, "chat_id", None)
    assert run({"chat_id": None}) == 2
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    with pytest.raises(SystemExit) as e:
        cli.main(["hook", "reply-guard"])
    assert e.value.code == 2 and "could not run" in capsys.readouterr().err                   # fails closed


# --- channel mode: noticing that nothing is reading the chat ----------------------------------------------

from tests.test_detectors import alerts   # noqa: E402 - the same reader the detector tests use


def _deaf(conn, pending, monkeypatch, since=None):
    """Run one check with Telegram reporting `pending` updates waiting. The send is stubbed: no test talks to Telegram."""
    monkeypatch.setattr(daemon.telegram, "pending_updates", lambda *a, **k: pending)
    if getattr(daemon.notify.send_one, "__module__", "") == "finnamon.notify":
        monkeypatch.setattr(daemon.notify, "send_one", lambda c, row, chat=None: 1)
    if since is not None:
        store.set_state(conn, "channel_deaf_since", since)
    return daemon.Daemon().check_channel_heard(conn)


def test_a_backlog_has_to_persist_before_the_household_is_told(conn, monkeypatch):
    """A message that just arrived is not evidence the reader is gone; the plugin gets a moment to collect it."""
    seed(conn)
    _deaf(conn, 1, monkeypatch)
    assert store.get_state(conn, "channel_deaf_since"), "the clock starts on the first backlog seen"
    assert alerts(conn) == [], "but nobody is told yet"
    _deaf(conn, 2, monkeypatch)          # still within DEAF_AFTER_S
    assert alerts(conn) == []


def test_a_backlog_that_outlives_the_grace_tells_the_chat_it_cannot_hear(conn, monkeypatch):
    seed(conn)
    old = "2020-01-01 00:00:00"          # long past DEAF_AFTER_S
    _deaf(conn, 3, monkeypatch, since=old)
    a = alerts(conn)
    assert [x["kind"] for x in a] == ["channel_deaf"]
    assert json.loads(a[0]["payload_json"])["pending"] == 3
    _deaf(conn, 4, monkeypatch, since=old)
    assert len(alerts(conn)) == 1, "one telling per outage, not one per check"


def test_the_chat_being_read_again_clears_the_state(conn, monkeypatch):
    seed(conn)
    store.set_state(conn, "channel_deaf_since", "2020-01-01 00:00:00")
    _deaf(conn, 0, monkeypatch)
    assert store.get_state(conn, "channel_deaf_since") is None
    assert alerts(conn) == [], "nothing waiting means nothing to report"


def test_an_idle_household_is_never_reported_deaf(conn, monkeypatch):
    """pending is 0 when nobody has written, so silence alone never raises this."""
    seed(conn)
    _deaf(conn, 0, monkeypatch)
    assert store.get_state(conn, "channel_deaf_since") is None and alerts(conn) == []


def test_a_telegram_hiccup_is_not_evidence_of_a_dead_reader(conn, monkeypatch):
    seed(conn)
    def boom(*a, **k): raise OSError("network down")
    monkeypatch.setattr(daemon.telegram, "pending_updates", boom)
    d = daemon.Daemon()
    assert d.check_channel_heard(conn) is None
    assert store.get_state(conn, "channel_deaf_since") is None and alerts(conn) == []


def test_the_deaf_check_runs_only_in_channel_mode_after_the_hand_off_and_only_once_a_minute(conn, tg, monkeypatch):
    """It costs a Telegram call per tick if unthrottled, and asking before the ack would ask about our own backlog."""
    from finnamon import telegram
    seed(conn)
    store.set_state(conn, "inbound", "channel")
    store.set_state(conn, "telegram_offset", "41")
    checks, ticks = [], []
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: [])
    d = make_daemon(conn)
    monkeypatch.setattr(d, "verify_privacy", lambda c: True)
    monkeypatch.setattr(d, "check_channel_heard", lambda c: checks.append(len(ticks)))
    def wait(t=None):
        ticks.append(t)
        if len(ticks) == 3:
            d.stop.set()
    monkeypatch.setattr(d.stop, "wait", wait)
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 5.0)   # a runner booted 5s ago: monotonic counts from boot
    d.poll_loop()
    assert checks == [1], "not on the ack tick, once on the next, then held off by DEAF_CHECK_S"
    d.stop.clear(); checks.clear(); ticks.clear()
    store.set_state(conn, "inbound", None)
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: d.stop.set() or [])
    d.poll_loop()
    assert checks == [], "in daemon mode this process is the reader; a backlog is its own to collect"


def test_status_carries_the_deaf_stamp_the_dashboard_pill_reads(home, conn, capsys):
    """The pill is a falsy check on this field, so `status` has to print it in both states."""
    seed(conn)
    cli.main(["status"])
    assert '"channel_deaf_since": null' in capsys.readouterr().out
    store.set_state(conn, "channel_deaf_since", "2026-09-23 18:40:00")
    cli.main(["status"])
    assert '"channel_deaf_since": "2026-09-23 18:40:00"' in capsys.readouterr().out


def test_a_clock_that_stepped_back_waits_instead_of_telling_the_chat(conn, monkeypatch):
    """A `since` ahead of now must read as still inside the grace, not as a negative elapsed time worth an alert."""
    seed(conn)
    future = "2099-01-01 00:00:00"
    assert _deaf(conn, 2, monkeypatch, since=future) == future
    assert alerts(conn) == []


def test_a_later_outage_past_the_repeat_floor_is_told_again(conn, monkeypatch):
    """The floor is a floor, not a mute: a household deaf again tomorrow hears about it again, under a new key."""
    seed(conn)
    _deaf(conn, 1, monkeypatch, since="2020-01-01 10:00:00")
    conn.execute("UPDATE alerts SET as_of = datetime('now','localtime',?)", (f"-{daemon.DEAF_REPEAT_H + 1} hours",))
    _deaf(conn, 1, monkeypatch, since="2020-01-02 10:00:00")
    assert [a["key"] for a in alerts(conn)] == ["health:channel_deaf:2020-01-01 10:00:00", "health:channel_deaf:2020-01-02 10:00:00"]


def test_the_telling_goes_out_at_once_rather_than_waiting_for_the_next_cycle(conn, monkeypatch):
    """Only run.cycle sends pending alerts, and sync_interval_hours defaults to 6: a three-minute detection would
    otherwise be a six-hour telling."""
    seed(conn)
    sent = []
    monkeypatch.setattr(daemon.notify, "send_one", lambda c, row, chat=None: sent.append(dict(row)) or 1)
    _deaf(conn, 2, monkeypatch, since="2020-01-01 00:00:00")
    assert [s["kind"] for s in sent] == ["channel_deaf"], "told as soon as it is known"


def test_a_repeat_of_the_same_outage_is_not_told_twice(conn, monkeypatch):
    seed(conn)
    sent = []
    monkeypatch.setattr(daemon.notify, "send_one", lambda c, row, chat=None: sent.append(1) or 1)
    for _ in range(3):
        _deaf(conn, 2, monkeypatch, since="2020-01-01 00:00:00")
    assert len(sent) == 1 and len(alerts(conn)) == 1, "the dedupe swallows the repeat, and nothing is re-sent"


def test_a_flapping_reader_does_not_flood_the_chat(conn, monkeypatch):
    """A reader that crash-loops collects the backlog, dies, and starts a fresh outage every few minutes. Each is a new
    `since` and would be a new message; the household hears it once and the advice is the same every time."""
    seed(conn)
    sent = []
    monkeypatch.setattr(daemon.notify, "send_one", lambda c, row, chat=None: sent.append(1) or 1)
    for minute in ("05", "20", "35", "50"):
        _deaf(conn, 1, monkeypatch, since=f"2020-01-01 10:{minute}:00")
        _deaf(conn, 0, monkeypatch)   # the restarted reader drains the backlog, then dies again
    assert len(sent) == 1 and len(alerts(conn)) == 1


def test_leaving_channel_mode_clears_the_deaf_stamp(conn, monkeypatch):
    """Only the channel branch of the poll loop clears this, so `channel off` would strand it and the dashboard would
    blame a dashboard that is no longer in the picture."""
    seed(conn)
    store.set_state(conn, "channel_deaf_since", "2020-01-01 00:00:00")
    monkeypatch.setattr(cli.store, "connect", lambda *a, **k: conn)
    cli.main(["channel", "off"])
    assert store.get_state(conn, "channel_deaf_since") is None


def test_a_send_that_fails_leaves_the_row_for_the_next_cycle(conn, monkeypatch):
    seed(conn)
    def boom(*a, **k): raise RuntimeError("telegram down")
    monkeypatch.setattr(daemon.notify, "send_one", boom)
    _deaf(conn, 1, monkeypatch, since="2020-01-01 00:00:00")
    a = alerts(conn)
    assert len(a) == 1 and a[0]["sent_at"] is None, "still pending, so send_pending retries it"
