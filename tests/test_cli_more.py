"""The CLI surface. `init` and `link` are the user flows Claude may never run; the rest are the read/write
commands it does run. Everything external (Plaid, Telegram, scheduler, claude) is faked."""
import subprocess
import io
import os
import json
import sys
import threading
from pathlib import Path

import pytest

from finnamon import cli, claude_runner, config, detect, investments, link, notify, owners, plaid_api, run as runmod, scheduler, store, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, seed, txn


def answers(monkeypatch, *vals):
    it = iter(vals)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def run_cli(capsys, *argv):
    cli.main(list(argv))
    return capsys.readouterr().out


def test_init_then_link_flow(home, tg, fake_claude, capsys, monkeypatch, tmp_path):
    (tmp_path / "web" / "node_modules" / "node-pty").mkdir(parents=True); (tmp_path / "web" / "package.json").write_text("{}")  # dashboard found, never prompts
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: ["/LaunchAgents/com.finnamon.daemon.plist"])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    answers(monkeypatch, "cid", "prod-sec", "", "123:token")
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    s = config.load_secrets()
    assert s["plaid"] == {"client_id": "cid", "production_secret": "prod-sec"} and s["telegram"]["bot_token"] == "123:token"
    assert "Plaid credentials verified" in out and "chat id 555 recorded" in out and "com.finnamon.daemon.plist" in out
    assert "set up" in tg.sent[-1]["text"] and store.get_state(store.connect(), "chat_id") == "555"
    # second run: everything found, no prompts, owner row added idempotently, scheduler skipped on 'n'
    answers(monkeypatch, "n")
    out = run_cli(capsys, "init", "--owner", "bill")
    assert "found in secrets.toml" in out and "already recorded" in out
    assert store.connect().execute("SELECT count(*) FROM owners").fetchone()[0] == 1
    # bad Plaid keys re-prompt (twice more), then stop
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: (_ for _ in ()).throw(PlaidError({"error_type": "INVALID_INPUT", "error_code": "INVALID_API_KEYS"})))
    answers(monkeypatch, "cid2", "bad", "", "cid3", "bad", "")
    with pytest.raises(SystemExit):
        run_cli(capsys, "init", "--yes")
    cap = capsys.readouterr()
    assert "three times" in cap.err and cap.out.count("Plaid rejected") == 3 and config.load_secrets()["plaid"]["client_id"] == "cid3"
    # a business error after authentication (production keys, unknown institution) still counts as verified
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: (_ for _ in ()).throw(PlaidError({"error_type": "INVALID_INPUT", "error_code": "INVALID_INSTITUTION"})))
    answers(monkeypatch, "n")
    assert "credentials verified" in run_cli(capsys, "init", "--owner", "bill")
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(tg, "get_me", lambda token=None: (_ for _ in ()).throw(RuntimeError("401")))
    monkeypatch.setattr("finnamon.telegram.get_me", tg.get_me)
    answers(monkeypatch, "tok2", "tok3")  # a bad Telegram token re-prompts (twice more), then stops, same as Plaid
    with pytest.raises(SystemExit):
        run_cli(capsys, "init", "--yes")
    cap = capsys.readouterr()
    assert "Telegram rejected the token three times" in cap.err and cap.out.count("Telegram rejected") == 3
    # link: URL printed and sent, mirror candidate accepted with --yes, summary announced
    conn = store.connect()
    seed(conn)
    monkeypatch.setattr(link, "start", lambda owner, update=None: {"link_token": "lt", "url": "https://hosted.plaid/x", "expiration": None})
    monkeypatch.setattr(link, "wait_for_public_token", lambda lt: "public-x")
    monkeypatch.setattr(link, "complete", lambda c, o, pt, u=None: {"item_id": "item2", "institution": "Ally", "sync": {"accounts": 2, "transactions": 40, "recurring": 3, "error": None},
                                                                     "mirror_candidates": [{"new": "n1", "existing": "chk", "name": "Checking", "mask": "4821", "match": "persistent_account_id"}]})
    merged, announced = [], []
    monkeypatch.setattr(link, "mark_mirror", lambda c, n, e: merged.append((n, e)))
    monkeypatch.setattr(link, "announce", lambda c, i: announced.append(i))
    out = run_cli(capsys, "link", "--yes")
    assert "https://hosted.plaid/x" in out and "Linked Ally (item2)" in out and "2 accounts, 40 transactions, 3 recurring" in out and "marked joint" in out
    assert merged == [("n1", "chk")] and announced == ["item2"] and not any("hosted.plaid" in m["text"] for m in tg.sent)  # the link stays in the terminal
    # declining the mirror, and a session closed without a bank
    answers(monkeypatch, "n")
    conn.execute("INSERT INTO owners (owner) VALUES ('jane')")
    out = run_cli(capsys, "link", "--owner", "JANE")   # any case: the member is jane
    assert "marked joint" not in out and merged == [("n1", "chk")]
    monkeypatch.setattr(link, "wait_for_public_token", lambda lt: None)
    with pytest.raises(SystemExit):
        run_cli(capsys, "link")
    assert "without adding a bank" in capsys.readouterr().err
    # update mode: no public token needed, result printed as JSON
    monkeypatch.setattr(link, "complete", lambda c, o, pt, u=None: {"item_id": u, "updated": True})
    assert json.loads(run_cli(capsys, "link", "--update", "item1").split("...")[-1])["updated"] is True


def test_init_skips_blank_plaid_keys_and_telegram(home, tg, fake_claude, capsys, monkeypatch, tmp_path):
    """#111: Enter on a blank client_id or token skips the step (never "rejected", never written as an empty string), and
    the rest of init still runs: the assistant bundle, the dashboard, the schedule. A second run asks again."""
    (tmp_path / "web" / "node_modules" / "node-pty").mkdir(parents=True); (tmp_path / "web" / "package.json").write_text("{}")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    installed = []
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: installed.append(1) or [])
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: pytest.fail("a skipped step must not call Plaid"))
    answers(monkeypatch, "", "")   # client_id, bot token
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert "rejected" not in out and "verified" not in out
    assert "Skipped for now: Plaid keys, Telegram" in out and installed and not tg.sent
    assert config.load_secrets() in ({}, {"telegram": {}, "items": {}}) or '""' not in config.secrets_path().read_text()
    assert store.connect().execute("SELECT owner FROM owners").fetchall()[0][0] == "bill"
    assert (home / "assistant" / "CLAUDE.md").exists()
    # a client_id with no secret at all is a skip too, and keeps nothing blank
    answers(monkeypatch, "cid", "", "", "", "n")
    out = run_cli(capsys, "init")
    assert "Skipped for now: Plaid keys, Telegram" in out and '""' not in (config.secrets_path().read_text() if config.secrets_path().exists() else "")


def test_init_skipping_a_rejected_saved_bot_drops_it_and_sends_nothing(home, tg, fake_claude, capsys, monkeypatch, tmp_path):
    """A re-run whose saved token is now rejected, then skipped: the token goes (the daemon stops polling it) and init
    ends without trying to message a chat it cannot reach."""
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    from finnamon import secrets
    secrets.write({}, {"bot_token": "123:revoked"}, {})
    store.set_state(store.connect(), "chat_id", 555)
    monkeypatch.setattr("finnamon.telegram.get_me", lambda token=None: (_ for _ in ()).throw(RuntimeError("401")))
    answers(monkeypatch, "", "")   # Plaid skipped, then Enter at the token re-prompt
    out = run_cli(capsys, "init", "--yes")
    assert "Skipped for now: Plaid keys, Telegram" in out and not tg.sent and "bot_token" not in config.load_secrets().get("telegram", {})


def test_init_reports_sandbox_only_plaid_keys(home, tg, fake_claude, capsys, monkeypatch, tmp_path):
    """A sandbox-only setup (production secret left blank, e.g. for testing) is verified against sandbox
    rather than failing against production, and says plainly that it's sandbox only."""
    (tmp_path / "web" / "node_modules" / "node-pty").mkdir(parents=True); (tmp_path / "web" / "package.json").write_text("{}")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    seen_envs = []
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: seen_envs.append(env) or {"institution": {"name": "First Platypus Bank"}})
    answers(monkeypatch, "cid", "", "sandbox-sec", "123:token")  # production secret left blank
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert seen_envs == ["sandbox"]
    assert "sandbox only: you'll see test banks, not yours" in out


def test_init_does_not_blame_the_keys_for_a_network_error(home, fake_claude, capsys, monkeypatch):
    """A Plaid outage or local network blip must not be reported as bad keys, and must not exhaust the
    three tries with a misleading 'Plaid rejected the keys' message."""
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: (_ for _ in ()).throw(PlaidError({"error_code": "NETWORK_ERROR", "error_message": "timed out"})))
    answers(monkeypatch, *(["cid", "sec", ""] * 3))
    with pytest.raises(SystemExit):
        run_cli(capsys, "init", "--yes")
    cap = capsys.readouterr()
    assert cap.out.count("could not reach Plaid") == 3
    assert "Plaid rejected the keys" not in cap.out


def test_check_claude_code(fake_claude, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"loggedIn": True, "email": "bill@example.com"}))
    cli._check_claude_code()
    assert "logged in (bill@example.com)" in capsys.readouterr().out

    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"loggedIn": False}))
    cli._check_claude_code()
    assert "not logged in" in capsys.readouterr().out

    monkeypatch.setenv("CLAUDE_FAKE_RESULT", "null")  # valid JSON, not an object: must not crash cmd_init
    cli._check_claude_code()
    assert "could not check" in capsys.readouterr().out

    monkeypatch.setattr(claude_runner, "binary", lambda: None)
    cli._check_claude_code()
    assert "not on PATH" in capsys.readouterr().out


def test_command_surface(home, tg, capsys, monkeypatch, tmp_path):
    conn = store.connect()
    seed(conn)
    txn(conn, "t1", "chk", "2026-09-10", 80, "WF", "Whole Foods", "mch_wf")
    txn(conn, "t2", "cc", "2026-09-16", 412, "SQ *PMT 8827", None, None, "GENERAL_SERVICES", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
    # budgets / thresholds / settings / categories / aliases
    assert json.loads(run_cli(capsys, "budget", "set", "groceries", "600"))["category"] == "FOOD_AND_DRINK_GROCERIES"
    assert json.loads(run_cli(capsys, "budget", "--as-of", "2026-09-19 12:00:00"))[0]["spent"] == 80
    assert "categories" in json.loads(run_cli(capsys, "budget", "suggest", "--months", "3"))
    assert json.loads(run_cli(capsys, "budget", "remove", "groceries"))["removed"] is True
    with pytest.raises(SystemExit):
        run_cli(capsys, "budget", "set", "other", "10")
    assert "Candidates:" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run_cli(capsys, "budget", "set", "zzzz", "10")
    assert "category list" in capsys.readouterr().err
    assert json.loads(run_cli(capsys, "threshold", "checking", "1000"))["threshold"] == 1000
    with pytest.raises(SystemExit):
        run_cli(capsys, "threshold", "nope", "1")
    run_cli(capsys, "settings", "set", "dup_window_days", "5")
    run_cli(capsys, "settings", "set", "low_balance_threshold", "50", "--account", "cc")
    rows = {(r["account_id"], r["key"]): r["value"] for r in json.loads(run_cli(capsys, "settings"))}
    assert rows[("*", "dup_window_days")] == "5" and rows[("cc", "low_balance_threshold")] == "50"
    assert "FOOD_AND_DRINK_GROCERIES" in run_cli(capsys, "category", "list")
    assert json.loads(run_cli(capsys, "category", "Whole Foods", "groceries"))["canonical"] == "mch_wf"
    with pytest.raises(SystemExit):
        run_cli(capsys, "category", "Whole Foods", "food")  # primary, not detailed
    with pytest.raises(SystemExit):
        run_cli(capsys, "category", "Whole Foods", "zzzz")
    assert json.loads(run_cli(capsys, "alias", "SQ *PMT 8827", "fence contractor"))["canonical"] == "fence contractor"
    assert json.loads(run_cli(capsys, "alias", "Loan Payment Confirmation#%", "Bank of America mortgage"))["name"] == "Loan Payment Confirmation#%"
    with pytest.raises(SystemExit):
        run_cli(capsys, "alias", "%", "everything")
    assert "literal characters" in capsys.readouterr().err
    # detect / alerts / triage / normal
    assert "SELECT" in run_cli(capsys, "detect", "--sql", "duplicate_charge")
    with pytest.raises(SystemExit):
        run_cli(capsys, "detect", "--sql", "nope")
    r = json.loads(run_cli(capsys, "detect", "--as-of", AS_OF, "--only", "first_merchant", "no_source"))
    assert len(r["new_alerts"]) == 2
    groups = json.loads(run_cli(capsys, "alerts", "--untriaged"))
    assert groups[0]["group"] == "t2" and len(groups[0]["candidates"]) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO("Never paid this: $412 to a new merchant.\n"))   # `-`: the sentence on stdin keeps its $ signs
    assert json.loads(run_cli(capsys, "triage", "set", "t2", "promote", "high", "-"))["stamped"] == 2
    assert conn.execute("SELECT reason FROM alerts WHERE transaction_id='t2'").fetchone()[0] == "Never paid this: $412 to a new merchant."
    assert json.loads(run_cli(capsys, "triage"))["groups"] == 0  # nothing left: claude is not invoked
    lst = json.loads(run_cli(capsys, "alerts"))
    assert len(lst) == 1 and lst[0]["verdict"] == "promote" and "Never paid" in lst[0]["text"] and lst[0]["tier"] == "anomaly"   # the dashboard's unsent filter reads tier
    assert lst[0]["transaction_id"] == "t2" and len(lst[0]["folded"]) == 1   # two detectors on t2: one alert, the other folded in
    assert json.loads(run_cli(capsys, "notify", "--list"))[0]["kind"].startswith("anomaly:")
    assert json.loads(run_cli(capsys, "notify"))["sent"] == 1 and len([m for m in tg.sent if "All quiet" not in m["text"]]) == 1   # one transaction, one message; on a Sunday or Monday the roundup says all quiet too
    n = json.loads(run_cli(capsys, "normal", "--alert", "1", "--note", "fence guy"))
    assert n["canonical"] == "fence contractor" and n["kind"] == "anomaly:first_merchant"
    assert json.loads(run_cli(capsys, "normal", "Whole Foods", "--max-amount", "200", "--account", "chk"))["account_id"] == "chk"
    rules = json.loads(run_cli(capsys, "normal", "--list"))
    assert len(rules) == 2 and all("id" in r for r in rules)
    assert json.loads(run_cli(capsys, "normal", "--remove", str(n["id"])))["removed"]["id"] == n["id"]
    assert [r["id"] for r in json.loads(run_cli(capsys, "normal", "--list"))] == [r["id"] for r in rules if r["id"] != n["id"]]
    with pytest.raises(SystemExit):
        run_cli(capsys, "normal", "--remove", str(n["id"]))
    with pytest.raises(SystemExit):
        run_cli(capsys, "normal", "--roundup-item", "1", "9")
    assert conn.execute("SELECT count(*) FROM alerts WHERE transaction_id='t2' AND resolution='normal'").fetchone()[0] == 2   # resolving one resolved both
    assert json.loads(run_cli(capsys, "alerts", "--undo", "2"))["undone"] == "normal"   # and undo on either reopens both
    assert conn.execute("SELECT count(*) FROM alerts WHERE transaction_id='t2' AND resolved_at IS NULL").fetchone()[0] == 2
    conn.execute("INSERT INTO roundup_items VALUES ('1234567890', 101, 1, 2)")
    assert json.loads(run_cli(capsys, "normal", "--roundup-item", "101", "1"))["kind"] == "anomaly:no_source"
    with pytest.raises(SystemExit):
        run_cli(capsys, "normal", "--alert", "99")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x\n"))
    run_cli(capsys, "triage", "set", "anom:nosrc:zzz", "suppress", "low", "-")  # no such group: stamps 0, no error
    assert json.loads(run_cli(capsys, "triage", "suppressed")) == [] and json.loads(run_cli(capsys, "alerts", "--suppressed")) == []
    # query / chart / networth / status / heartbeat / accounts / owners
    assert json.loads(run_cli(capsys, "query", "SELECT count(*) AS n FROM transactions", "--limit", "5"))[0]["n"] == 2
    pytest.importorskip("matplotlib")
    assert run_cli(capsys, "chart", "budgets").strip().endswith(".png")
    with pytest.raises(SystemExit):
        run_cli(capsys, "chart", "merchant_history")
    monkeypatch.setattr(investments, "sync_all_holdings", lambda c: {"item1": 0})
    assert json.loads(run_cli(capsys, "networth"))["net_worth"] == 0
    out = run_cli(capsys, "networth", "--sync", "--history")
    assert out.startswith("{") and out.rstrip().endswith("]")
    monkeypatch.setattr(scheduler, "status", lambda os_name=None: "not loaded")
    st = json.loads(run_cli(capsys, "status"))
    assert "last_error" in st["items"][0] and st["items"][0]["accounts"]   # what "fix <bank>" lists when two logins share a bank
    assert st["items"][0]["item_id"] == "item1" and st["pending_alerts"] == 0 and st["untriaged"] == 0 and st["scheduler"] == "not loaded"
    assert st["version"] == cli.__version__   # the VERSION file, not the version the package was installed as
    assert json.loads(run_cli(capsys, "heartbeat"))["sent"]  # never run → stale
    assert json.loads(run_cli(capsys, "account", "list"))[0]["institution"] == "Chase"
    conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES ('chk','2026-01-02 00:00:00',10.5),('chk','2026-01-01 00:00:00',25)")   # newest first and smallest: not max(), not last rowid
    assert {a["account_id"]: a["balance"] for a in json.loads(run_cli(capsys, "account", "list"))}["chk"] == 10.5
    conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES ('chk','2026-01-03 00:00:00',NULL)")   # a sync that sent no figure
    assert {a["account_id"]: a["balance"] for a in json.loads(run_cli(capsys, "account", "list"))}["chk"] == 10.5   # the dashboard's Accounts list shows the latest
    conn.execute("INSERT OR IGNORE INTO owners (owner) VALUES ('jane')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, mask, owner) VALUES ('chk_j','item1','x','depository','4821','jane')")
    run_cli(capsys, "account", "merge", "chk_j", "chk")
    assert tuple(conn.execute("SELECT mirror_of, owner FROM accounts WHERE account_id='chk_j'").fetchone()) == ("chk", "joint")
    assert json.loads(run_cli(capsys, "account", "failover", "chk"))["promoted"] == "chk_j"  # mirror's Item is healthy: promoted
    run_cli(capsys, "account", "failover", "chk_j")  # and back
    run_cli(capsys, "account", "unmerge", "chk_j")
    assert [r[0] for r in conn.execute("SELECT owner FROM accounts WHERE account_id IN ('chk_j','chk') ORDER BY account_id")] == ["bill", "jane"]   # the owners they had
    run_cli(capsys, "account", "owner", "chk_j", "Jane")
    assert tuple(conn.execute("SELECT mirror_of, owner FROM accounts WHERE account_id='chk_j'").fetchone()) == (None, "jane")
    assert json.loads(run_cli(capsys, "owner", "list"))[0]["owner"] == "bill"
    monkeypatch.setattr(owners, "add_member", lambda c, name, code, **kw: {"owner": name, "code_len": len(code)})
    assert json.loads(run_cli(capsys, "owner", "add", "jane").split("waiting...")[-1])["owner"] == "jane"
    # sync / run / install / daemon / serve / version
    monkeypatch.setattr(sync, "sync_item", lambda c, i: {"item_id": i, "error": None})
    monkeypatch.setattr(sync, "sync_all", lambda c: [{"item_id": "item1", "error": None}])
    assert json.loads(run_cli(capsys, "sync", "--item", "item1"))["item_id"] == "item1" and json.loads(run_cli(capsys, "sync"))[0]["item_id"] == "item1"
    r = json.loads(run_cli(capsys, "run", "--no-sync", "--no-triage", "--no-notify"))
    assert r["error"] is None and r["sync"] == []
    lock = runmod.lock()
    with pytest.raises(SystemExit):
        run_cli(capsys, "run")
    assert "another run is in progress" in capsys.readouterr().err
    lock.close()
    monkeypatch.setattr(scheduler, "uninstall", lambda os_name=None: None)
    assert run_cli(capsys, "install", "--uninstall").strip() == "removed"
    started = []
    monkeypatch.setattr("finnamon.daemon.Daemon", lambda: type("D", (), {"start": lambda self: started.append(1)})())
    run_cli(capsys, "daemon")
    monkeypatch.setattr("finnamon.mcp_server.main", lambda: started.append(2))
    run_cli(capsys, "serve")
    assert started == [1, 2]
    with pytest.raises(SystemExit) as e:
        run_cli(capsys, "--version")
    assert e.value.code == 0

    # --- detector drafts and review (DETECTORS pointed at a scratch tree) ------------------
    det = tmp_path / "detectors"
    for d in ("rules", "candidates"):
        (det / d).mkdir(parents=True)
    monkeypatch.setattr(detect, "DETECTORS", det)
    assert "no pending" in run_cli(capsys, "detect", "--review")
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT account_id, 'anomaly:big' AS kind, 'big:'||transaction_id AS key, transaction_id, '{}' AS payload FROM tx WHERE amount > 500;"))
    out = run_cli(capsys, "detect", "--draft", "-", "--name", "big_charge")
    assert "Not live until reviewed" in out and (det / "pending" / "big_charge.sql").exists()
    (det / "pending" / "broken.sql").write_text("SELECT nonsense FROM nowhere")
    (det / "pending" / "unwanted.sql").write_text("SELECT 1")
    (det / "pending" / "keep.sql").write_text("SELECT 1")
    answers(monkeypatch, "y", "n", "", "d")  # pending/ is reviewed in name order: big_charge, broken, keep, unwanted
    out = run_cli(capsys, "detect", "--review", "--tier", "candidates")
    assert "(runs; 0 rows right now)" in out and "(does not run:" in out and "live" in out and "deleted" in out
    assert (det / "candidates" / "big_charge.sql").exists() and (det / "pending" / "broken.sql").exists()
    assert not (det / "pending" / "unwanted.sql").exists() and (det / "pending" / "keep.sql").exists()
    # drafts come from stdin only; names are confined to pending/; no overwrite; SQL must be a SELECT
    src = tmp_path / "d.sql"
    src.write_text("SELECT NULL, 'r', 'r:1', NULL, '{}' WHERE 0")
    with pytest.raises(SystemExit):
        run_cli(capsys, "detect", "--draft", str(src))                                   # file path refused
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 1"))
    with pytest.raises(SystemExit):
        run_cli(capsys, "detect", "--draft", "-", "--name", "../rules/evil")              # traversal refused
    assert not (det / "rules" / "evil.sql").exists()
    monkeypatch.setattr(sys, "stdin", io.StringIO("DELETE FROM alerts"))
    with pytest.raises(SystemExit):
        run_cli(capsys, "detect", "--draft", "-", "--name", "evil")                       # not a SELECT
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 2"))
    with pytest.raises(SystemExit):
        run_cli(capsys, "detect", "--draft", "-", "--name", "keep")                       # exists: no overwrite
    assert (det / "pending" / "keep.sql").read_text() == "SELECT 1"
    monkeypatch.setattr(sys, "stdin", io.StringIO(src.read_text()))
    run_cli(capsys, "detect", "--draft", "-", "--name", "draft")
    answers(monkeypatch, "n", "y", "n")  # broken, draft, keep
    run_cli(capsys, "detect", "--review", "--tier", "rules")
    assert (det / "rules" / "draft.sql").exists()


def test_triage_set_takes_the_sentence_on_stdin_only(home, capsys, monkeypatch):
    """#63: a positional sentence has lost its `$` signs before the command sees it, so it is refused with the heredoc hint; `-` keeps them."""
    from tests.test_notify_run import alert
    conn = store.connect()
    seed(conn)
    alert(conn, "anomaly:first_merchant", "anom:first:t9", "anomaly", {"merchant": "x", "amount": 9})
    stdin = io.StringIO("never read\n")
    monkeypatch.setattr(sys, "stdin", stdin)
    for argv in (["Acme's 5.67 monthly charge"], []):   # what "$45.67" became in a double-quoted argument; or no sentence at all
        with pytest.raises(SystemExit):
            run_cli(capsys, "triage", "set", "anom:first:t9", "suppress", "low", *argv)
        assert "<<'EOF'" in capsys.readouterr().err
    assert stdin.tell() == 0 and conn.execute("SELECT verdict FROM alerts WHERE key='anom:first:t9'").fetchone()[0] is None
    monkeypatch.setattr(sys, "stdin", io.StringIO("Acme's $45.67 monthly charge hasn't appeared since June.\n"))
    assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t9", "promote", "low", "-"))["stamped"] == 1
    assert conn.execute("SELECT reason FROM alerts WHERE key='anom:first:t9'").fetchone()[0] == "Acme's $45.67 monthly charge hasn't appeared since June."
    # `-` on the real, buffered stdin (what the daemon's shell gives it): bytes decoded, `$` and accents intact, trimmed
    alert(conn, "anomaly:first_merchant", "anom:first:t10", "anomaly", {"merchant": "y", "amount": 9})
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO("  It's 6x your usual $40 café charge.\n".encode()), encoding="utf-8"))
    assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t10", "promote", "high", "-"))["stamped"] == 1
    assert conn.execute("SELECT reason FROM alerts WHERE key='anom:first:t10'").fetchone()[0] == "It's 6x your usual $40 café charge."
    # `-` with nothing on stdin, or a terminal: refuse instead of stamping an empty sentence or waiting on a keyboard
    alert(conn, "anomaly:first_merchant", "anom:first:t11", "anomaly", {"merchant": "z", "amount": 9})
    for stdin in (io.TextIOWrapper(io.BytesIO(b"  \n")), type("Tty", (io.StringIO,), {"isatty": lambda self: True})("typed later\n")):
        monkeypatch.setattr(sys, "stdin", stdin)
        with pytest.raises(SystemExit):
            run_cli(capsys, "triage", "set", "anom:first:t11", "promote", "high", "-")
        assert "comes on stdin" in capsys.readouterr().err
    assert conn.execute("SELECT verdict FROM alerts WHERE key='anom:first:t11'").fetchone()[0] is None


def test_triage_set_again_repairs_the_sentence_and_keeps_the_rest(home, capsys, monkeypatch):
    """#63 repair: re-setting a sent alert with the same verdict replaces only its sentence; a different verdict changes nothing."""
    from tests.test_notify_run import alert
    conn = store.connect(); seed(conn)
    alert(conn, "anomaly:first_merchant", "anom:first:t14", "anomaly", {"merchant": "x", "amount": 9})
    conn.execute("UPDATE alerts SET verdict='promote', confidence='low', reason='a .75 final bill', sent_at='2026-09-27 09:00', telegram_message_id=7 WHERE key='anom:first:t14'")
    before = dict(conn.execute("SELECT * FROM alerts WHERE key='anom:first:t14'").fetchone())
    monkeypatch.setattr(sys, "stdin", io.StringIO("Flipped: suppress this.\n"))
    assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t14", "suppress", "low", "-"))["stamped"] == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("a $3.75 final bill\n"))
    assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t14", "promote", "high", "-"))["stamped"] == 1   # a confidence can't reroute it
    after = dict(conn.execute("SELECT * FROM alerts WHERE key='anom:first:t14'").fetchone())
    assert after == {**before, "reason": "a $3.75 final bill"}
    # no Claude session (the /triage run, the chat) rewrites a stamped sentence; nor does a person while the group has a new, untriaged candidate
    for env in ("FINNAMON_TRIAGE", "FINNAMON_FROM_CLAUDE", "CLAUDECODE"):
        monkeypatch.setenv(env, "1"); monkeypatch.setattr(sys, "stdin", io.StringIO("rewritten\n"))
        assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t14", "promote", "low", "-"))["stamped"] == 0
        monkeypatch.delenv(env)
    conn.execute("UPDATE alerts SET transaction_id='t14' WHERE key='anom:first:t14'")
    alert(conn, "anomaly:no_source", "anom:nosrc:t14", "anomaly", {"merchant": "x", "amount": 9})
    conn.execute("UPDATE alerts SET transaction_id='t14' WHERE key='anom:nosrc:t14'")
    monkeypatch.setattr(sys, "stdin", io.StringIO("No source either.\n"))
    assert json.loads(run_cli(capsys, "triage", "set", "t14", "promote", "low", "-"))["stamped"] == 1
    assert conn.execute("SELECT reason FROM alerts WHERE key='anom:first:t14'").fetchone()[0] == "a $3.75 final bill"


def test_init_sets_up_the_dashboard_when_node_is_there(home, tg, capsys, monkeypatch, tmp_path):
    """Step 4 installs web/ with npm and the closing message points at the page; without Node it says how to get it."""
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: ["/LaunchAgents/com.finnamon.web.plist"])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    (tmp_path / "web").mkdir(); (tmp_path / "web" / "package.json").write_text("{}")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    ran = []
    monkeypatch.setattr(cli.shutil, "which", lambda exe: {"node": "/opt/homebrew/bin/node", "npm": "/opt/homebrew/bin/npm", "finnamon": "/x/finnamon"}.get(exe))
    monkeypatch.delenv("FINNAMON_CLAUDE_BIN")   # no claude at all on this box: step 3 says so and moves on
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: ran.append((argv, kw.get("cwd"), kw.get("timeout"))) or type("R", (), {"returncode": 0})())
    answers(monkeypatch, "cid", "prod-sec", "", "123:token")
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert ran == [(["/opt/homebrew/bin/npm", "install"], tmp_path / "web", cli.NPM_TIMEOUT_S)]
    assert "dashboard installed" in out and "http://localhost:8888/?token=" in out and "Link account adds a bank" in out and "set up my budgets" in out
    assert "Link account" in tg.sent[-1]["text"] and "set up" in tg.sent[-1]["text"] and "finnamon open" in tg.sent[-1]["text"]
    assert "?token=" not in tg.sent[-1]["text"], "the chat is shared: the key is printed at the terminal, never sent"
    token = (tmp_path / "web-token").read_text() if (tmp_path / "web-token").exists() else None
    assert token and f"?token={token}" in out
    # second run: a whole node_modules present, nothing to install (an interrupted one, without node-pty, is redone)
    (tmp_path / "web" / "node_modules" / "node-pty").mkdir(parents=True); ran.clear()
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert ran == [] and "found web/node_modules" in out
    # npm failing or hanging is reported, not fatal; the scheduler still runs
    (tmp_path / "web" / "node_modules" / "node-pty").rmdir()
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: type("R", (), {"returncode": 1})())
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert "npm install failed" in out and "com.finnamon.web.plist" in out and "finnamon link" in out
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(cli.subprocess.TimeoutExpired(argv, 900)))
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert "did not finish" in out and "finnamon link" in out
    # a checkout without web/ (non-editable install)
    (tmp_path / "web" / "package.json").unlink()
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert "no web/" in out and "uv tool install -e ." in out
    (tmp_path / "web" / "package.json").write_text("{}")
    # dashboard installed but the scheduler declined: no URL promised
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: type("R", (), {"returncode": 0})())
    answers(monkeypatch, "y", "n")  # dashboard yes, scheduler no
    out = run_cli(capsys, "init", "--owner", "bill")
    assert "dashboard installed" in out and "localhost:8888" not in out and "nothing scheduled" in out
    # no Node: say how, point at the terminal flow
    monkeypatch.setattr(cli.shutil, "which", lambda exe: "/x/finnamon" if exe == "finnamon" else None)
    out = run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert "Node 20+ is not on PATH" in out and "brew install node" in out and "finnamon link" in tg.sent[-1]["text"] and "localhost" not in tg.sent[-1]["text"]


def test_init_asks_about_the_dashboard_without_yes(home, tg, capsys, monkeypatch, tmp_path):
    """Without --yes the dashboard question is asked: '' and 'y' install, 'n' skips it and drops it from the scheduler
    line and the closing notes; npm missing from PATH is taken from next to node."""
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    (tmp_path / "web").mkdir(); (tmp_path / "web" / "package.json").write_text("{}")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda exe: {"node": "/opt/node/bin/node", "finnamon": "/x/finnamon"}.get(exe))  # no npm
    monkeypatch.delenv("FINNAMON_CLAUDE_BIN")   # and no claude: step 3 only says so
    ran, prompts = [], []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: ran.append(argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    def ask(*vals):
        it = iter(vals)
        monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or next(it))

    ask("cid", "prod-sec", "", "123:token", "", "")  # Enter installs the dashboard, Enter schedules it
    out = run_cli(capsys, "init", "--owner", "bill")
    assert ran == [["/opt/node/bin/npm", "install"]] and "dashboard installed" in out and "http://localhost:8888" in out
    assert "npm install in web/" in prompts[-2] and "daemon, hourly heartbeat and the dashboard as" in prompts[-1]
    assert "Link account" in tg.sent[-1]["text"]
    ask("y", "n")  # secrets and chat already recorded: only the two questions are asked; 'y' installs, scheduler declined
    out = run_cli(capsys, "init", "--owner", "bill")
    assert len(ran) == 2 and "dashboard installed" in out and prompts[-2:] == prompts[-4:-2]
    assert "nothing scheduled" in out and "localhost" not in out and "finnamon link" in tg.sent[-1]["text"]
    ask("n", "")  # dashboard declined: the scheduler line drops it and the closing note is the terminal flow
    out = run_cli(capsys, "init", "--owner", "bill")
    assert len(ran) == 2 and "skipped; later: cd web && npm install && finnamon install" in out
    assert "and the dashboard" not in prompts[-1] and "Done. Next: finnamon link" in out and "localhost" not in out
    assert "finnamon link" in tg.sent[-1]["text"] and "localhost" not in tg.sent[-1]["text"]


def test_alerts_sent_keeps_delivered_and_queued_rows_only(home, capsys):
    """--sent is what the dashboard reads: delivered rows plus what the sender will deliver; triage's other notes stay out."""
    from tests.test_notify_run import alert
    conn = store.connect(); seed(conn)
    sent = alert(conn, "anomaly:first_merchant", "sent", "anomaly", {"merchant": "a", "amount": 1}, "promote", "low", "sent in a roundup")
    conn.execute("UPDATE alerts SET sent_at = datetime('now','localtime') WHERE id = ?", (sent,))
    rule = alert(conn, "duplicate_charge", "rule")
    high = alert(conn, "anomaly:first_merchant", "high", "anomaly", {"merchant": "b", "amount": 2}, "promote", "high", "queued")
    alert(conn, "anomaly:first_merchant", "low", "anomaly", {"merchant": "c", "amount": 3}, "promote", "low", "waits for the roundup")
    alert(conn, "anomaly:first_merchant", "sup", "anomaly", {"merchant": "d", "amount": 4}, "suppress", "high", "usual spot")
    alert(conn, "anomaly:first_merchant", "untriaged", "anomaly", {"merchant": "e", "amount": 5})
    conn.commit()
    assert [r["id"] for r in json.loads(run_cli(capsys, "alerts", "--sent"))] == [high, rule, sent]
    assert len(json.loads(run_cli(capsys, "alerts"))) == 6


def test_triage_set_dash_on_a_real_pipe(home, capsys, monkeypatch):
    """The daemon's shell hands `-` a real pipe: select sees the heredoc at once; an open pipe with nothing on it is refused, not waited on."""
    from tests.test_notify_run import alert
    conn = store.connect(); seed(conn)
    alert(conn, "anomaly:first_merchant", "anom:first:t12", "anomaly", {"merchant": "x", "amount": 9}); conn.commit()
    monkeypatch.setattr(cli, "STDIN_WAIT_S", 0)
    r, w = os.pipe(); os.write(w, "It's 6x your usual $40 charge.\n".encode()); os.close(w)
    with os.fdopen(r, "rb") as raw:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw, encoding="utf-8"))
        assert json.loads(run_cli(capsys, "triage", "set", "anom:first:t12", "promote", "high", "-"))["stamped"] == 1
    assert conn.execute("SELECT reason FROM alerts WHERE key='anom:first:t12'").fetchone()[0] == "It's 6x your usual $40 charge."
    alert(conn, "anomaly:first_merchant", "anom:first:t13", "anomaly", {"merchant": "y", "amount": 9}); conn.commit()
    r, w = os.pipe()   # the writer stays open and silent: a forgotten heredoc
    def write_late():   # a regressed guard would read this and stamp, so the test fails instead of hanging; on the good path the reader is gone already
        try:
            os.write(w, b"late\n")
        except OSError:
            pass
        finally:
            os.close(w)
    late = threading.Timer(0.5, write_late)
    late.start()
    with os.fdopen(r, "rb") as raw:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw, encoding="utf-8"))
        with pytest.raises(SystemExit):
            run_cli(capsys, "triage", "set", "anom:first:t13", "promote", "high", "-")
    late.join()
    assert "comes on stdin" in capsys.readouterr().err
    assert conn.execute("SELECT verdict FROM alerts WHERE key='anom:first:t13'").fetchone()[0] is None


def test_version_comes_from_the_version_file(capsys):
    """One source: the VERSION file the release workflow bumps; a pull is enough, no reinstall."""
    from pathlib import Path
    assert cli.__version__ == Path(__file__).resolve().parent.parent.joinpath("VERSION").read_text().strip()
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == cli.__version__


# --- finnamon update: adopt new code without reprovisioning -------------------------------------------------

def test_services_for_only_restarts_what_went_stale():
    from finnamon.scheduler import services_for
    assert services_for(["finnamon/notify.py"]) == ["daemon"]
    assert services_for(["web/server.js"]) == ["web"]
    assert services_for(["web/public/app.js"]) == [], "served off disk per request: a page reload is the whole update"
    assert services_for(["web/public/style.css", "finnamon/cli.py"]) == ["daemon"]
    assert services_for(["README.md", "docs/DEVELOPMENT.md"]) == []
    assert services_for([".github/workflows/release.yml", ".gitignore", "./README.md"]) == [], "a leading dot is part of the name"
    assert services_for(["VERSION", "web/package.json"]) == ["web"]
    assert services_for(["web/package.json", "pyproject.toml"]) == ["daemon", "web"]


def test_a_release_commit_restarts_nothing():
    """The release workflow's commit touches only VERSION, CHANGELOG.md and changelog.d/: no code, so no restart."""
    from finnamon.scheduler import services_for
    assert services_for(["CHANGELOG.md", "VERSION", "changelog.d/fix-a-thing.md", "changelog.d/add-b.md"]) == []


def test_update_is_for_a_person_at_a_terminal(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")   # the dashboard's session is the household's: it must not restart itself
    with pytest.raises(SystemExit):
        cli.main(["update"])
    assert "terminal on the Finnamon box" in capsys.readouterr().err


def test_update_refuses_a_dirty_tree_and_restarts_nothing(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    seen = []
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a dirty tree must not reach the restart"))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: seen.append(a) or subprocess.CompletedProcess(a, 0, " M finnamon/notify.py\n", ""))
    with pytest.raises(SystemExit):
        cli.main(["update"])
    assert "uncommitted changes" in capsys.readouterr().err
    status = next(a for a in seen if a[0] == "status")
    assert "--untracked-files=no" in status, "an approved detector left untracked by `detect --review` must not block updates forever"


def test_update_sends_you_to_install_when_the_unit_files_changed(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: ["com.finnamon.web.plist"])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a stale unit must not be restarted into"))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: subprocess.CompletedProcess(a, 0, "abc123\n", ""))
    with pytest.raises(SystemExit):
        cli.main(["update", "--no-pull"])
    err = capsys.readouterr().err
    assert "com.finnamon.web.plist" in err and "finnamon install" in err


def test_update_restarts_only_the_services_the_pull_touched(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    restarted = []
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: restarted.extend(names) or list(names))
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(heads), "")
        if args[0] == "status":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "pull":
            return subprocess.CompletedProcess(args, 0, "Updating old111..new222\n", "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "finnamon/notify.py\nweb/public/app.js\n", "")
        if args[0] == "config":   # the remote is named in the output: --ff-only says the history did not fork, not who wrote it
            return subprocess.CompletedProcess(args, 0, "git@example:finnamon.git\n", "")
        raise AssertionError(args)
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update"])
    assert restarted == ["daemon"], "a public/ file is not a reason to end the household's assistant"
    out = capsys.readouterr().out
    assert "restarted daemon" in out
    assert "adopted old111..new222 from git@example:finnamon.git" in out, "say what was adopted and from where"


def test_update_no_pull_adopts_the_tree_rather_than_reporting_nothing_to_do(home, monkeypatch, tmp_path, capsys):
    """A pull by hand leaves no range to diff; restarting nothing would leave the new code unrun."""
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    restarted = []
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: restarted.extend(names) or list(names))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: subprocess.CompletedProcess(a, 0, "same111\n", ""))
    cli.main(["update", "--no-pull"])
    assert restarted == ["daemon", "web"]


def test_update_needs_a_checkout_to_pull_into(home, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(tmp_path / "site-packages" / "finnamon"))
    with pytest.raises(SystemExit):
        cli.main(["update"])
    assert "not a git checkout" in capsys.readouterr().err   # a wheel install has nothing to pull; `install` is the way in


def test_update_stops_when_the_pull_does_not_fast_forward(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a failed pull must not reach the restart"))
    def fake_git(r, *args, **kw):
        if args[0] == "pull":
            return subprocess.CompletedProcess(args, 128, "", "fatal: Not possible to fast-forward, aborting.\n")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(cli, "_git", fake_git)
    with pytest.raises(SystemExit) as e:
        cli.main(["update"])
    assert e.value.code == 1 and "fast-forward" in capsys.readouterr().err   # the box keeps running what it has


def test_update_dry_run_names_what_would_restart_and_all_ignores_the_diff(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("--dry-run ends nobody's session"))
    def fake_git(r, *args, **kw):
        return subprocess.CompletedProcess(args, 0, "same111\n" if args[0] == "rev-parse" else "", "")
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update", "--no-pull", "--dry-run"])
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "would restart: daemon, web (nothing was pulled or migrated)" and out[0].startswith("would write the assistant bundle to ")
    # With a pull in play --dry-run reports against the fetched tip and moves nothing: `install --dry-run` writes nothing either.
    monkeypatch.setattr(cli, "_git", lambda r, *args, **kw: subprocess.CompletedProcess(
        args, 0, {"rev-list": "2\n", "diff": "finnamon/notify.py\n", "config": "git@example:x.git\n", "status": ""}.get(args[0], "same111\n"), ""))
    cli.main(["update", "--dry-run"])
    out = capsys.readouterr().out
    assert "2 commit(s) to pull from git@example:x.git" in out
    assert "would restart: daemon (nothing was pulled or migrated)" in out


def test_update_leaves_the_assistant_alone_when_only_the_page_changed(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a page file is not a reason to end the household's assistant"))
    monkeypatch.setattr(cli.store, "migrate", lambda conn: ["004_manual_items.sql"])
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(heads), "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "web/public/app.js\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update"])
    out = capsys.readouterr().out
    assert "migration 004_manual_items.sql" in out   # the schema moves before anything bounces, and says so
    assert "reload the page" in out                  # new code nobody restarted into: the household is told where it is


def test_update_adopts_a_release_commit_without_a_restart(home, monkeypatch, tmp_path, capsys):
    """The release workflow's own commit (VERSION, CHANGELOG.md, deleted fragments) carries no code to restart into."""
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a release commit is not a reason to end the household's assistant"))
    monkeypatch.setattr(cli.store, "migrate", lambda conn: [])
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(heads), "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "CHANGELOG.md\nVERSION\nchangelog.d/some-branch.md\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update"])
    out = capsys.readouterr().out
    assert "nothing to restart" in out and "reload the page" not in out


def test_update_reports_a_restart_the_service_manager_refused(home, monkeypatch, tmp_path, capsys):
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("com.finnamon.web: Could not find service")))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: subprocess.CompletedProcess(a, 0, "same111\n", ""))
    with pytest.raises(SystemExit) as e:
        cli.main(["update", "--no-pull"])
    assert e.value.code == 1 and "Could not find service" in capsys.readouterr().err   # a half-done update names the job it could not bounce


def test_update_will_not_migrate_or_restart_while_a_sync_holds_the_run_lock(home, monkeypatch, tmp_path, capsys):
    """migrate outside the lock races the daemon's own write transactions, and a restart mid-cycle kills a Plaid page."""
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("a running cycle must not be interrupted"))
    monkeypatch.setattr(cli.store, "migrate", lambda conn: pytest.fail("the schema must not move under a live daemon"))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: subprocess.CompletedProcess(a, 0, "same111\n" if a[0] == "rev-parse" else "", ""))
    held = runmod.lock()                                  # stand in for a sync in flight
    try:
        with pytest.raises(SystemExit):
            cli.main(["update", "--no-pull"])
    finally:
        held.close()
    assert "a sync is running right now" in capsys.readouterr().err


def test_update_names_a_release_that_changes_what_the_assistant_may_do(home, monkeypatch, tmp_path, capsys):
    """The bundle is the assistant's permission set and instructions: adopting it silently is the thing to avoid."""
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    restarted = []
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda names, *a, **k: restarted.extend(names) or list(names))
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        table = {"rev-parse": lambda: next(heads), "status": lambda: "", "pull": lambda: "Updating\n",
                 "config": lambda: "git@example:x.git\n", "diff": lambda: "finnamon/assistant_bundle/.claude/settings.json\n"}
        return subprocess.CompletedProcess(args, 0, table[args[0]](), "")
    monkeypatch.setattr(cli, "_git", fake_git)
    cli.main(["update"])
    out = capsys.readouterr().out
    assert "note: this release changes the assistant bundle" in out
    assert restarted == ["daemon", "web"], "the session holding those settings read them at startup"


def test_update_dry_run_never_takes_the_lock_or_moves_the_schema(home, monkeypatch, tmp_path, capsys):
    """--no-pull --dry-run used to fall past the early return and migrate for real, unlike the default path."""
    repo = tmp_path / "checkout"; (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(cli.scheduler, "restart", lambda *a, **k: pytest.fail("--dry-run ends nobody's session"))
    monkeypatch.setattr(cli.store, "migrate", lambda conn: pytest.fail("--dry-run must not move the schema"))
    monkeypatch.setattr(cli.runmod, "lock", lambda: pytest.fail("--dry-run must not take the run lock off a sync"))
    monkeypatch.setattr(cli, "_git", lambda r, *a, **k: subprocess.CompletedProcess(a, 0, "same111\n" if a[0] == "rev-parse" else "", ""))
    cli.main(["update", "--no-pull", "--dry-run"])
    assert "nothing was pulled or migrated" in capsys.readouterr().out


def test_install_asks_before_retiring_the_conversation(home, capsys, monkeypatch):
    """There is no install-but-keep-the-conversation, so declining stops the whole command: `update` is that command."""
    ran = []
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: ran.append(1) or [])
    f = config.home() / "intercom.json"
    old = "11111111-2222-3333-4444-555555555555"
    f.write_text(json.dumps({"id": old, "created": True}))

    answers(monkeypatch, "n")
    out = run_cli(capsys, "install")
    assert f.exists() and ran == [], "declined: the services are untouched too, not just the session"
    assert old in out and "finnamon update" in out, "and it names the command that keeps the conversation"

    answers(monkeypatch, "y")
    out = run_cli(capsys, "install")
    assert json.loads(f.read_text()) == {"prev": old} and ran == [1], "no id left, so the dashboard mints one on its next start"
    assert f"--resume {old}" in out, "the transcript is still on disk, and the id is the only way back to it"


def test_install_only_asks_when_there_is_something_to_retire(home, capsys, monkeypatch):
    """A question about ending the household's conversation is only honest when the command is about to end one:
    a dry run ends nothing, --yes was already answered, and a file with nothing readable in it holds no session."""
    ran = []
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: ran.append(len(ran) + 1) or [])
    f = config.home() / "intercom.json"
    old = "11111111-2222-3333-4444-555555555555"
    f.write_text(json.dumps({"id": old, "created": True}))

    out = run_cli(capsys, "install", "--dry-run")   # no input() stubbed: a dry run must not ask
    assert json.loads(f.read_text())["id"] == old and "Continue?" not in out, "a dry run reports; it retires nothing and asks nothing"
    assert old in out and "would retire" in out, "and `update` sends people here to look before they leap"

    run_cli(capsys, "install", "--yes")
    assert "id" not in json.loads(f.read_text())

    f.unlink()
    run_cli(capsys, "install")                      # nothing to retire, so nothing to ask -- and still an install
    assert "Continue?" not in capsys.readouterr().out
    assert ran == [1, 2, 3], "a box with no dashboard yet still gets its services"

    f.write_text("{not json")
    run_cli(capsys, "install")
    assert f.exists() and ran == [1, 2, 3, 4], "a file we cannot read is left alone, and does not abort the install"


def test_install_without_a_terminal_refuses_rather_than_retiring(home, capsys, monkeypatch):
    """Nobody to answer is not the same as yes; the conversation is not dropped by a script that meant `update`."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    f = config.home() / "intercom.json"
    f.write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))
    def eof(prompt=""): raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit):
        cli.main(["install"])
    assert "--yes" in capsys.readouterr().err and f.exists()


def test_the_dashboard_and_the_cli_agree_on_the_intercom_file():
    """Two languages, one filename: the CLI retires the file web/server.js writes. Derived from the Python name rather
    than spelled twice, so a rename on either side fails this rather than silently breaking the pair."""
    server = (Path(__file__).resolve().parent.parent / "web" / "server.js").read_text()
    assert f"INTERCOM_FILE = '{config.INTERCOM_FILE}'" in server


def test_a_record_we_did_not_write_is_left_alone(home, capsys, monkeypatch):
    """Valid JSON is not the same as a record: `null`, a list or a number would have crashed the whole install on
    `.get`, before the services were touched, with the traceback landing where the confirmation should have been."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    f = config.home() / config.INTERCOM_FILE
    for junk in ("null", "[1, 2]", "5", '"an id"'):
        f.write_text(junk)
        run_cli(capsys, "install")
        assert f.read_text() == junk, f"{junk} is not ours to throw away"


def test_the_id_the_dashboard_could_not_resume_is_printed_too(home, capsys, monkeypatch):
    """server.js keeps the id it replaced as `prev` because nothing else can reach that transcript again. Deleting the
    file is the only thing that can lose it, so retiring says it out loud."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    cur, gone = "11111111-2222-3333-4444-555555555555", "99999999-8888-7777-6666-555555555555"
    (config.home() / config.INTERCOM_FILE).write_text(json.dumps({"id": cur, "created": True, "prev": gone}))
    out = run_cli(capsys, "install", "--yes")
    assert f"--resume {cur}" in out and f"--resume {gone}" in out


def test_the_way_back_is_sealed_against_the_telegram_plugin(home, capsys, monkeypatch):
    """The transcript lives under this directory, so the printed command runs here, and a plain `claude` here starts a
    second copy of the channel plugin's server that kills the household session's. The way back must not cost them
    Telegram (v0.7.1.0)."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    (config.home() / config.INTERCOM_FILE).write_text(json.dumps(
        {"id": "11111111-2222-3333-4444-555555555555", "prev": "99999999-8888-7777-6666-555555555555"}))
    printed = [l for l in run_cli(capsys, "install", "--yes").splitlines() if "claude" in l]
    assert len(printed) == 2 and all("--strict-mcp-config" in l for l in printed), printed


def test_install_refuses_to_run_from_a_claude_session(home, capsys, monkeypatch):
    """AGENTS.md lists install among the commands that refuse a Claude session, and this is what makes that true: the
    conversation it retires is, in channel mode, the one the assistant is speaking in."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    f = config.home() / config.INTERCOM_FILE
    f.write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit):
        cli.main(["install", "--yes"])
    assert "in a terminal" in capsys.readouterr().err and f.exists(), "refused before anything was retired"


def test_uninstall_leaves_the_conversation_alone(home, capsys, monkeypatch):
    """Taking the jobs away is not reprovisioning: there is no fresh assistant coming, so there is nothing to retire
    and nothing to ask about."""
    monkeypatch.setattr(scheduler, "uninstall", lambda os_name=None: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("uninstall has no new session to make room for"))
    f = config.home() / "intercom.json"
    f.write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))
    assert run_cli(capsys, "install", "--uninstall").strip() == "removed"
    assert f.exists(), "the jobs are gone but the conversation is still resumable when they come back"


def test_the_question_defaults_to_keeping_the_conversation(home, capsys, monkeypatch):
    """[y/N]: a tired Enter must keep the household's assistant, and only an actual yes may end it, however it is typed."""
    ran = []
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: ran.append(1) or [])
    f = config.home() / "intercom.json"
    f.write_text(json.dumps({"id": "11111111-2222-3333-4444-555555555555", "created": True}))

    answers(monkeypatch, "")
    run_cli(capsys, "install")
    assert f.exists() and ran == [], "Enter is the default, and the default is no"

    answers(monkeypatch, "  YES \n")
    run_cli(capsys, "install")
    assert json.loads(f.read_text()) == {"prev": "11111111-2222-3333-4444-555555555555"} and ran == [1], \
        "a yes is a yes in any case, and the id it retired stays on disk"


def test_a_record_with_no_id_is_nothing_to_retire(home, capsys, monkeypatch):
    """The dashboard writes this file before it knows an id is worth keeping; a record without one holds no
    conversation, so there is nothing to warn about and nothing to print a way back to."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("no id means no conversation to end"))
    f = config.home() / "intercom.json"
    f.write_text(json.dumps({"created": False}))
    out = run_cli(capsys, "install")
    assert "claude --resume" not in out, "nothing was retired, so nothing is offered as a way back"


def test_the_way_back_is_printed_before_the_install_can_fail(home, capsys, monkeypatch):
    """The id is the only handle on a transcript that is still on disk. A reload that blows up after the file is gone
    must not take it with it, or the household's conversation is unreachable for good."""
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: (_ for _ in ()).throw(RuntimeError("launchctl bootstrap failed")))
    old = "11111111-2222-3333-4444-555555555555"
    (config.home() / "intercom.json").write_text(json.dumps({"id": old, "created": True}))
    with pytest.raises(SystemExit):
        cli.main(["install", "--yes"])
    cap = capsys.readouterr()
    assert f"--resume {old}" in cap.out and "launchctl bootstrap failed" in cap.err
    assert json.loads((config.home() / config.INTERCOM_FILE).read_text()) == {"prev": old}, \
        "a red error reads as `nothing happened`; the id must not depend on the window staying open"


class Reached(Exception):
    pass


@pytest.mark.parametrize("env", ["FINNAMON_FROM_CLAUDE", "CLAUDECODE"])
def test_human_only_commands_refuse_a_claude_session(conn, monkeypatch, capsys, env):
    from finnamon import daemon, heartbeat, notify, triage
    def reached(*a, **k):
        raise Reached()
    for mod, fn in [(sync, "sync_all"), (notify, "send_pending"), (runmod, "cycle"), (daemon, "Daemon"), (heartbeat, "check"),
                    (triage, "run_if_needed"), (link, "mark_mirror"), (link, "merge_problem"), (link, "unmerge"), (owners, "resolve"), (link, "failover"), (link, "remove_account"), (investments, "sync_all_holdings")]:
        monkeypatch.setattr(mod, fn, reached)
    gated = [["sync"], ["notify"], ["run"], ["daemon"], ["heartbeat"], ["triage"], ["account", "merge", "a", "b"],
             ["account", "unmerge", "a"], ["account", "owner", "a", "jane"], ["owner", "rename", "a", "b"], ["owner", "remove", "a"], ["account", "failover", "a"], ["account", "remove", "--yes", "a"], ["networth", "--sync"], ["networth", "--history", "--sync"]]
    for argv in gated:
        with pytest.raises(Reached):
            cli.main(argv)                               # a person at a terminal gets through
    monkeypatch.setenv(env, "")
    capsys.readouterr()
    for argv in gated + [["init", "--yes"]]:
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "is for a person at a terminal" in capsys.readouterr().err, argv
    cli.main(["networth"]); cli.main(["notify", "--list"]); cli.main(["triage", "suppressed"])   # the read-only forms stay open


def test_the_assistant_may_not_allow_networth_sync():
    import fnmatch
    perms = json.loads((Path(cli.__file__).parent / "assistant_bundle/.claude/settings.json").read_text())["permissions"]
    def hits(rules, cmd):
        return any(fnmatch.fnmatchcase(cmd, r[5:-1]) for r in rules if r.startswith("Bash("))
    for cmd in ["finnamon networth", "finnamon networth --history", "finnamon networth --history --months 12"]:
        assert hits(perms["allow"], cmd) and not hits(perms["deny"], cmd)
    for cmd in ["finnamon networth --sync", "finnamon networth --history --sync", "finnamon networth --history --months 3 --sync"]:
        assert hits(perms["deny"], cmd) or not hits(perms["allow"], cmd)


HELP_ONLY = [["init"], ["link"], ["owner", "add", "x"], ["channel", "on"], ["channel", "off"], ["account", "merge", "a", "b"],
             ["account", "failover", "a"], ["sync"], ["install"], ["update"], ["detect", "--review"], ["run"], ["daemon"], ["notify"], ["networth", "--sync"]]


def test_help_is_readable_from_a_claude_session_and_runs_nothing(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(store, "connect", lambda *a, **k: pytest.fail("help touched the database"))
    for argv in HELP_ONLY:
        for form in (argv + ["--help"], argv + ["-h"], ["help"] + argv):   # argparse prints help and exits before any command runs
            try:
                cli.main(form)                                # `help` returns; --help exits 0
            except SystemExit as e:
                assert e.code in (0, None) and "help" not in form, form
            assert "usage: finnamon " + argv[0] in capsys.readouterr().out, form
    for argv in (["help"], ["help", "--version"], ["help", "-x"]):   # a flag first: top-level usage, no reparse crash
        cli.main(argv); assert "usage: finnamon" in capsys.readouterr().out
    for cmd, says in [("owner", ["--user-id", "/telegram:access", "--new-group", "link --start --owner"]), ("link", ["--remove <item_id>"]),
                      ("account", ["merge <new_id>", "failover <id>"]), ("detect", ["--review"]), ("channel", ["owner add"])]:
        cli.main(["help", cmd]); out = capsys.readouterr().out
        assert all(w in out for w in says), (cmd, out)
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "x"])                   # the command itself still refuses
    assert "person at a terminal" in capsys.readouterr().err


def test_the_assistant_may_read_help_but_not_run_human_only_commands():
    import fnmatch
    perms = json.loads((Path(cli.__file__).parent / "assistant_bundle/.claude/settings.json").read_text())["permissions"]
    def hits(rules, cmd):
        return any(fnmatch.fnmatchcase(cmd, r[5:-1]) for r in rules if r.startswith("Bash("))
    for cmd in ["finnamon help", "finnamon help owner", "finnamon help owner add", "finnamon help account merge"]:
        assert hits(perms["allow"], cmd) and not hits(perms["deny"], cmd), cmd
    for argv in HELP_ONLY:   # running them is never allowed, and neither is their own --help (deny beats allow): `help` is the way in
        for cmd in ["finnamon " + " ".join(argv), "finnamon " + " ".join(argv) + " --help"]:
            assert hits(perms["deny"], cmd) or not hits(perms["allow"], cmd), cmd


def test_flags_are_not_prefix_matched():
    with pytest.raises(SystemExit):   # `--syn` would be --sync to argparse, and slip past the `*--sync*` deny rule
        cli.build_parser().parse_args(["networth", "--history", "--months", "3", "--syn"])


def test_owner_add_asks_for_the_household_group_unless_new_group(conn, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(owners, "add_member", lambda c, name, code, switch=False: seen.append(switch) or {})
    run_cli(capsys, "owner", "add", "jane")                          # a DM household: the group it makes is the move
    store.set_state(conn, "chat_id", "-100")
    assert "household group" in run_cli(capsys, "owner", "add", "jane")
    assert "Create a Telegram group" in run_cli(capsys, "owner", "add", "jane", "--new-group")
    assert seen == [False, False, True]


def test_notify_restarted_sends_the_one_line_and_nothing_else(conn, tg, capsys, monkeypatch):
    assert json.loads(run_cli(capsys, "notify", "--restarted"))["sent"] is None and not tg.sent   # no chat yet: nothing to tell
    store.set_state(conn, "chat_id", "-100")
    run_cli(capsys, "notify", "--restarted")
    assert [m["text"] for m in tg.sent] == [notify.RESTARTED]   # not the pending alerts, not the roundup
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit):
        cli.main(["notify", "--restarted"])                     # the household's session cannot post it
    assert len(tg.sent) == 1


def test_only_the_release_script_is_inert_under_scripts():
    """scripts/release.py runs in CI, never in a service; any other script is unknown, so it restarts both."""
    from finnamon.scheduler import services_for
    assert services_for(["scripts/release.py"]) == []
    assert services_for(["scripts/backfill.py"]) == ["daemon", "web"]


def test_account_list_carries_each_account_owner(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path))
    conn = store.connect()
    conn.execute("INSERT INTO owners (owner) VALUES ('sam')")
    conn.execute("INSERT INTO items (item_id, institution, owner) VALUES ('i1','Chase','sam')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, owner) VALUES ('a1','i1','Checking','depository','sam')")
    conn.commit()
    assert json.loads(run_cli(capsys, "account", "list"))[0]["owner"] == "sam"


def test_update_check_reports_what_the_pull_would_bring_and_changes_nothing(home, monkeypatch, tmp_path, capsys):
    """The dashboard's Update available: the same upstream `update` pulls from, looked at without moving HEAD."""
    g = lambda d, *a: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    origin = tmp_path / "origin"; origin.mkdir(); g(origin, "init", "-q", "-b", "main")
    (origin / "VERSION").write_text("1.0.0.0\n"); (origin / "CHANGELOG.md").write_text("# Changelog\n")
    g(origin, "add", "."); g(origin, "commit", "-qm", "one")
    repo = tmp_path / "checkout"; subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    monkeypatch.setattr(cli.scheduler, "repo_dir", lambda: str(repo))
    monkeypatch.setattr(cli, "__version__", "1.0.0.0")
    monkeypatch.setattr(cli.scheduler, "drifted_units", lambda *a, **k: pytest.fail("looking must not need the units to match"))
    cli.main(["update", "--check"])
    assert json.loads(capsys.readouterr().out) == {"current": "1.0.0.0", "latest": "1.0.0.0", "ahead": 0, "notes": None}

    (origin / "VERSION").write_text("1.1.0.0\n")
    (origin / "CHANGELOG.md").write_text("# Changelog\n\n## [1.1.0.0] - 2026-10-03\n\n### Added\n- **Update from the\n  page.** More.\n- **Other.**\n\n## [1.0.0.0]\n- **Old.**\n")
    g(origin, "commit", "-qam", "two")
    head = (repo / ".git" / "HEAD").read_text()
    cli.main(["update", "--check"])
    assert json.loads(capsys.readouterr().out) == {"current": "1.0.0.0", "latest": "1.1.0.0", "ahead": 1, "notes": "Update from the page."}
    assert (repo / "VERSION").read_text() == "1.0.0.0\n" and (repo / ".git" / "HEAD").read_text() == head, "a check pulls nothing"

    g(repo, "branch", "--unset-upstream")
    with pytest.raises(SystemExit):
        cli.main(["update", "--check"])
    assert "no upstream" in capsys.readouterr().err


def _init_with_dashboard(home, tg, capsys, monkeypatch, tmp_path, *vals):
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    (tmp_path / "web" / "node_modules" / "node-pty").mkdir(parents=True, exist_ok=True); (tmp_path / "web" / "package.json").write_text("{}")
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda exe: {"node": "/n", "npm": "/n", "finnamon": "/x"}.get(exe))
    monkeypatch.delenv("FINNAMON_CLAUDE_BIN", raising=False)
    answers(monkeypatch, *vals)
    return run_cli(capsys, "init", "--owner", "bill", "--yes")


def test_init_defaults_a_new_bot_to_session_but_keeps_an_existing_mode(home, tg, capsys, monkeypatch, tmp_path):
    out = _init_with_dashboard(home, tg, capsys, monkeypatch, tmp_path, "cid", "prod-sec", "", "123:token")
    assert store.get_state(store.connect(), "inbound") == "session" and "session mode (the default)" in out
    store.set_state(store.connect(), "inbound", None)   # a household on the legacy mode re-runs init: the bot is not new
    _init_with_dashboard(home, tg, capsys, monkeypatch, tmp_path)
    assert store.get_state(store.connect(), "inbound") is None


def test_init_without_a_dashboard_leaves_inbound_alone(home, tg, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {"institution": {"name": "Chase"}})
    monkeypatch.setattr(scheduler, "install", lambda osn=None, dry_run=False: [])
    monkeypatch.setattr(owners, "add_first", lambda conn, owner, code, **kw: (store.set_state(conn, "chat_id", 555), {"chat_id": 555})[1])
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda exe: None)   # no Node: session mode would have nothing to type into
    monkeypatch.delenv("FINNAMON_CLAUDE_BIN")
    answers(monkeypatch, "cid", "prod-sec", "", "123:token")
    run_cli(capsys, "init", "--owner", "bill", "--yes")
    assert store.get_state(store.connect(), "inbound") is None


@pytest.mark.parametrize("mode,tip", [(None, True), ("daemon", True), ("channel", True), ("session", False)])
def test_session_tip_is_shown_once_and_only_off_session(home, tg, capsys, mode, tip):
    conn = store.connect()
    cli.secrets.update(telegram={"bot_token": "123:token"})
    store.set_state(conn, "inbound", mode)
    cli._session_tip(conn)
    assert ("channel session" in capsys.readouterr().out) is tip
    cli._session_tip(conn)
    assert capsys.readouterr().out == "", "once"
    assert store.get_state(conn, "inbound") == mode, "never switches the mode"


def test_session_tip_needs_a_bot(home, capsys):
    cli._session_tip(store.connect())
    assert capsys.readouterr().out == ""
