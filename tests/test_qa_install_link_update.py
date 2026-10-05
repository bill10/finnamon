"""The 10/4 household-journey QA: Sandbox → Production, one-line errors, the right `claude` hint, update without scheduling,
detector drafts in the household's home, a stranger in the chat, and two imports."""
import getpass
import io
import json
import subprocess
from pathlib import Path

import pytest

from finnamon import cli, config, detect, imports, link, notify, owners, plaid_api, scheduler, store, telegram
from tests.conftest import seed
from tests.test_cli_more import answers, run_cli
from tests.test_daemon_cli import make_daemon, msg


# --- init --plaid ----------------------------------------------------------------------------------

def test_init_plaid_replaces_the_keys_without_echo_and_skips_the_other_steps(home, monkeypatch, capsys):
    from finnamon import secrets
    secrets.update(plaid={"client_id": "cid", "sandbox_secret": "sand"}, items={"item1": "access-x"})
    answers(monkeypatch, "")   # client_id: Enter keeps it
    secret_prompts = iter(["prod-sec", ""])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(secret_prompts))
    probed = []
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: probed.append(env) or {})
    out = run_cli(capsys, "init", "--plaid")
    s = config.load_secrets()
    assert s["plaid"] == {"client_id": "cid", "production_secret": "prod-sec", "sandbox_secret": "sand"} and s["items"] == {"item1": "access-x"}
    assert probed == ["production"] and "Step 2" not in out and "Telegram" not in out and "prod-sec" not in out


def test_init_without_the_flag_still_says_where_the_keys_are_replaced(home, monkeypatch, capsys):
    from finnamon import secrets
    secrets.update(plaid={"client_id": "cid", "sandbox_secret": "sand"})
    monkeypatch.setattr(plaid_api, "institution_get", lambda i, env=None: {})
    monkeypatch.setattr("builtins.input", lambda prompt="": "")   # Telegram skipped; the rest is not this test's business
    monkeypatch.setattr(cli, "_check_claude_code", lambda: None)
    monkeypatch.setattr(cli, "_install_bundle", lambda dry_run=False: None)
    monkeypatch.setattr(cli, "_setup_dashboard", lambda a: False)
    monkeypatch.setattr(scheduler, "install", lambda *a, **k: [])
    out = run_cli(capsys, "init", "--yes")
    assert "init --plaid" in out


# --- one-line errors --------------------------------------------------------------------------------

def test_link_with_no_keys_is_one_line(home, monkeypatch, capsys):
    store.connect()
    monkeypatch.setenv("FINNAMON_PLAID_ENV", "production")
    with pytest.raises(SystemExit):
        cli.main(["link"])
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "init --plaid" in err


def test_link_update_takes_an_institution_name_and_refuses_the_rest_in_one_line(home, monkeypatch, capsys):
    conn = store.connect()
    conn.execute("INSERT OR IGNORE INTO owners (owner) VALUES ('me')")
    for item, inst in (("item_a", "American Express"), ("item_b", "Chase"), ("item_c", "Chase")):
        conn.execute("INSERT INTO items (item_id, institution, owner, source) VALUES (?,?,?, 'plaid')", (item, inst, "me"))
    seen = []
    monkeypatch.setattr(link, "start", lambda owner, update=None, **k: seen.append(update) or (_ for _ in ()).throw(plaid_api.PlaidError({"error_code": "X", "error_message": "stop"})))
    for ref, want in (("American Express", "item_a"), ("american express", "item_a"), ("item_b", "item_b")):
        with pytest.raises(SystemExit):
            cli.main(["link", "--update", ref])
        assert seen[-1] == want
    capsys.readouterr()
    for ref, needle in (("nope", "no linked bank"), ("Chase", "item_b")):   # unknown; ambiguous names both
        with pytest.raises(SystemExit):
            cli.main(["link", "--update", ref])
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and needle in err, err


def test_owner_add_with_no_bot_is_one_line(home, monkeypatch, capsys):
    store.connect()
    def boom(*a, **k):
        raise telegram.TelegramError(404, "Not Found")
    monkeypatch.setattr(owners, "add_member", boom)
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "kim"])
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.count("\n") == 1 and "finnamon init" in err


# --- the right claude -------------------------------------------------------------------------------

def test_hints_point_at_the_assistant_directory_not_a_bare_claude(home):
    assert "~/.finnamon/assistant" in cli._ASK_CLAUDE and "--strict-mcp-config" in cli._ASK_CLAUDE
    src = Path(notify.__file__).read_text()
    assert "run <code>claude</code>" not in src and "claude --strict-mcp-config" in src


# --- update -------------------------------------------------------------------------------------------

def _fake_repo(tmp_path, monkeypatch, changed, node_modules=True):
    repo = tmp_path / "checkout"
    (repo / "web" / ("node_modules" if node_modules else "x")).mkdir(parents=True, exist_ok=True)
    (repo / "web" / "package-lock.json").write_text("{}")
    (repo / ".git").write_text("gitdir: /elsewhere")   # a worktree: .git is a file
    monkeypatch.setattr(scheduler, "repo_dir", lambda: str(repo))
    heads = iter(["old111\n", "new222\n"])
    def fake_git(r, *args, **kw):
        out = {"rev-parse": next(heads, "new222\n") if args[0] == "rev-parse" else "", "pull": "Updating\n", "diff": "\n".join(changed) + "\n", "config": "git@x:f.git\n"}.get(args[0], "")
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cli, "_git", fake_git)
    return repo


def test_update_works_in_a_worktree_and_without_scheduled_services(home, monkeypatch, tmp_path, capsys):
    _fake_repo(tmp_path, monkeypatch, ["finnamon/notify.py"])
    monkeypatch.setattr(scheduler, "installed", lambda *a, **k: False)
    monkeypatch.setattr(scheduler, "drifted_units", lambda *a, **k: pytest.fail("no units installed: nothing to drift"))
    monkeypatch.setattr(scheduler, "restart", lambda *a, **k: pytest.fail("no units installed: nothing to restart"))
    out = run_cli(capsys, "update")
    assert "no scheduled services" in out


def test_update_runs_npm_ci_when_the_dashboards_packages_changed_and_only_then(scheduled, home, monkeypatch, tmp_path, capsys):
    ran = []
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/npm" if n == "npm" else None)
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: ran.append((argv, kw.get("cwd"))) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(scheduler, "restart", lambda names, *a, **k: list(names))
    repo = _fake_repo(tmp_path, monkeypatch, ["web/package-lock.json", "web/server.js"])
    run_cli(capsys, "update")
    assert ran == [(["/usr/bin/npm", "ci"], repo / "web")]
    ran.clear()
    _fake_repo(tmp_path, monkeypatch, ["web/server.js"])
    run_cli(capsys, "update")
    assert ran == []


def test_update_stops_before_restarting_when_npm_fails(scheduled, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/npm")
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    monkeypatch.setattr(scheduler, "drifted_units", lambda *a, **k: [])
    monkeypatch.setattr(scheduler, "restart", lambda *a, **k: pytest.fail("a dashboard with stale packages must not be restarted into"))
    _fake_repo(tmp_path, monkeypatch, ["web/package.json"])
    with pytest.raises(SystemExit):
        cli.main(["update"])
    assert "npm ci failed" in capsys.readouterr().err


def test_update_help_does_not_claim_dry_run_pulls():
    parser_help = cli.build_parser().format_help() + cli.HUMAN_HELP["update"]
    assert "pull and migrate as usual" not in parser_help
    assert "--dry-run only looks" in cli.HUMAN_HELP["update"]


# --- detector drafts ------------------------------------------------------------------------------------

def test_drafts_live_under_the_household_home_and_old_ones_move(home, monkeypatch, tmp_path, capsys):
    det = tmp_path / "checkout" / "detectors"
    (det / "pending").mkdir(parents=True)
    (det / "pending" / "old.sql").write_text("SELECT 1")
    (det / "pending" / "dup.sql").write_text("SELECT 'checkout'")
    monkeypatch.setattr(detect, "DETECTORS", det)
    (home / "detector-drafts").mkdir()
    (home / "detector-drafts" / "dup.sql").write_text("SELECT 'home'")   # a name already there is not overwritten
    assert detect.migrate_drafts() == ["old.sql"]
    assert (home / "detector-drafts" / "old.sql").read_text() == "SELECT 1" and (home / "detector-drafts" / "dup.sql").read_text() == "SELECT 'home'"
    assert not (det / "pending" / "old.sql").exists()
    assert oct((home / "detector-drafts").stat().st_mode & 0o777) in ("0o700", "0o755")   # made before this test; a fresh one is 0700 (below)
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("FINNAMON_HOME", str(fresh))
    assert oct(detect.drafts_dir().stat().st_mode & 0o777) == "0o700"


# --- a stranger in the household chat --------------------------------------------------------------------

def test_a_non_member_is_told_once_how_to_be_added(conn, tg):
    seed(conn)
    d = make_daemon(conn)
    d.handle_update(conn, msg("hello?", uid=999))
    d.handle_update(conn, msg("anyone?", uid=999, mid=501))   # rate limited: no second reply
    assert len(tg.sent) == 1 and "finnamon owner add" in tg.sent[0]["text"] and "--user-id 999" in tg.sent[0]["text"] and d.inbox.empty()
    d.handle_update(conn, msg("hi", chat=1, uid=998))        # another chat: not ours to answer
    assert len(tg.sent) == 1


# --- imports ------------------------------------------------------------------------------------------------

BOFA = '''Description,,Summary Amt.
Beginning balance as of 01/01/2026,,"1,000.00"
Total credits,,"500.00"
Total debits,,"-200.00"
Ending balance as of 01/31/2026,,"1,300.00"

Date,Description,Amount,Running Bal.
01/02/2026,"COFFEE SHOP",-5.00,995.00
01/03/2026,"PAYROLL",500.00,"1,495.00"
'''


def test_a_summary_block_above_the_header_is_skipped():
    p = imports.parse(BOFA)
    assert [r["amount"] for r in p["rows"]] == [-5.0, 500.0] and p["skipped"] == 0 and p["columns"]["date"] == "Date"
    with pytest.raises(ValueError, match="could not find a date and an amount column"):
        imports.parse("Description,,Summary Amt.\nTotal credits,,5\n")


def test_a_card_with_no_balance_can_be_given_one(home, capsys):
    conn = store.connect()
    imports.add_account(conn, "Amex", "Amex", "credit", "me")
    acct = "manual:amex:amex"
    assert dict(next(r for r in json.loads(run_cli(capsys, "account", "list")) if r["account_id"] == acct))["balance"] is None
    out = json.loads(run_cli(capsys, "account", "balance", "--", "Amex", "-1,234.50"))   # what is owed is stored positive
    assert out["balance"] == 1234.5
    assert next(r for r in json.loads(run_cli(capsys, "account", "list")) if r["account_id"] == acct)["balance"] == 1234.5
    with pytest.raises(SystemExit):
        cli.main(["account", "balance", "Amex", "lots"])
    assert "usage" in capsys.readouterr().err
