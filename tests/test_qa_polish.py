"""QA polish: typed amounts, terminal output, wording, doctor and settings text."""
import json
import os
import stat
import sys

import pytest

from finnamon import cli, notify, properties, render, scheduler, secrets, store
from tests.conftest import AS_OF, seed, txn


@pytest.mark.parametrize("text,want", [("1,200", 1200), ("$900", 900), ("$1,200.50", 1200.5), ("21500", 21500), (" $ 1 200 ", 1200), ("-$5", -5), (45, 45)])
def test_parse_amount_accepts_dollars_and_separators(text, want):
    assert store.parse_amount(text) == want


@pytest.mark.parametrize("text", ["", "abc", "$", "1.2.3", "12 dollars"])
def test_parse_amount_refuses_words(text):
    with pytest.raises(ValueError):
        store.parse_amount(text)


def test_every_typed_amount_takes_dollars_and_commas(conn, capsys):
    seed(conn)
    cli.main(["budget", "set", "groceries", "1,200"]); assert json.loads(capsys.readouterr().out)["monthly_limit"] == 1200
    cli.main(["budget", "set", "groceries", "$900"]); assert json.loads(capsys.readouterr().out)["monthly_limit"] == 900
    cli.main(["threshold", "Chase Checking", "$1,500"]); assert json.loads(capsys.readouterr().out)["threshold"] == 1500
    cli.main(["settings", "set", "large_amount", "$2,500"]); assert store.setting(conn, "large_amount") == "2500"
    assert properties.set_value(conn, "Car", "$21,500")["value"] == 21500
    with pytest.raises(SystemExit):
        cli.main(["budget", "set", "groceries", "lots"])
    assert "monthly limit must be a number of dollars" in capsys.readouterr().err


def test_property_remove_says_so_for_a_person_and_stays_json_in_a_pipe(conn, capsys, monkeypatch):
    cli.main(["property", "remove", "nope"]); assert json.loads(capsys.readouterr().out) == {"removed": False}
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cli.main(["property", "remove", "nope"]); assert capsys.readouterr().out == "no property called nope\n"


def test_terminal_output_is_a_table_and_json_flag_or_a_pipe_keeps_json(conn, capsys, monkeypatch):
    seed(conn)
    properties.set_value(conn, "House", 450000)
    cli.main(["budget", "set", "groceries", "400", "--category", "groceries"]); capsys.readouterr()
    for argv, needle in ((["property"], "House"), (["budget"], "Groceries"), (["networth"], "Net worth")):
        cli.main(argv); assert json.loads(capsys.readouterr().out) is not None   # piped: JSON
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    for argv, needle in ((["property"], "House"), (["budget"], "Groceries"), (["networth"], "Net worth"), (["alerts"], "No alerts")):
        cli.main(argv)
        text = capsys.readouterr().out
        assert needle in text and not text.lstrip().startswith(("[", "{"))
        cli.main([*argv, "--json"]); assert json.loads(capsys.readouterr().out) is not None


def test_normal_list_shows_the_merchant_name(conn, capsys):
    seed(conn)
    txn(conn, "s1", "chk", "2026-09-05", 40, "SHELL OIL", "Shell", "mch_shell")
    cli.main(["normal", "Shell"]); capsys.readouterr()
    cli.main(["normal", "--list"])
    assert json.loads(capsys.readouterr().out)[0]["merchant"] == "Shell"


def test_a_group_is_refused_the_same_way_for_a_rule_and_one_charge(conn):
    from finnamon import budgets
    seed(conn)
    txn(conn, "s1", "chk", "2026-09-05", 40, "SHELL OIL", "Shell", "mch_shell")
    for call in (lambda: budgets.category_set(conn, "Shell", "travel"), lambda: budgets.tx_category_set(conn, "s1", "travel")):
        with pytest.raises(ValueError, match='detailed category.*"Flights"'):
            call()


def test_dates_and_times_read_like_people_say_them():
    assert notify.day("2026-10-01", year=True) == "Oct 1, 2026" and notify.day("not a date") == "not a date"
    assert notify.when("2026-10-04 15:07:33") == "Oct 4, 3:07 PM" and notify.when("2026-10-04 00:05:00") == "Oct 4, 12:05 AM"


def test_alert_wording(conn):
    from tests.test_notify_run import alert
    row = lambda aid: conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()   # noqa: E731
    dup = notify.render(row(alert(conn, "duplicate_charge", "d1", payload={"merchant": "Shell", "amount": 52.18, "account": "Chk", "date_a": "2026-10-01", "date_b": "2026-10-01", "count": 2})))
    assert "both on Oct 1" in dup and "2026-10" not in dup and "Reply <i>it's normal</i>" in dup
    dup2 = notify.render(row(alert(conn, "duplicate_charge", "d2", payload={"merchant": "Shell", "amount": 52.18, "account": "Chk", "date_a": "2026-09-17", "date_b": "2026-09-18", "count": 2})))
    assert "charged Sep 17 and again Sep 18" in dup2
    rec = row(alert(conn, "new_recurring", "n1", payload={"merchant": "Peloton", "amount": 44, "frequency": "MONTHLY", "first_date": "2026-08-29", "account": "Sapphire", "mask": "7710"}))
    assert notify.render(rec).endswith("first seen Aug 29. Expected? Reply <i>it's normal</i> and I won't ask again.")
    assert "it's normal" not in notify.render(rec, page=True), "the page has its own button"
    first = row(alert(conn, "anomaly:first_merchant", "f1", "anomaly", {"merchant": "Best Buy", "amount": 899.99, "date": "2026-10-01", "account": "Blue Cash", "mask": "3005"}, "promote", "high", "Never shopped here."))
    text = notify.render(first)
    assert "<b>$899.99 to Best Buy</b> on Blue Cash …3005, Oct 1: first time at Best Buy. Never shopped here. Expected? Reply <i>it's normal</i>" in text
    assert "it's normal" not in notify.render(first, cue=False), "the roundup carries its own reply footer"
    over = notify.render(row(alert(conn, "budget_pace", "b1", payload={"budget": "dining", "spent": 400, "limit": 350, "day": 24, "days": 30, "state": "over"})))
    assert "Dining is over budget" in over
    err = notify.render(row(alert(conn, "sync_health", "e1", payload={"item_id": "i", "institution": "Chase", "status": "INSTITUTION_NOT_RESPONDING", "last_error": ""})))
    assert "the bank isn't responding (INSTITUTION_NOT_RESPONDING)" in err


def test_quiet_line_has_no_seconds_and_clearer_words(conn):
    seed(conn)
    conn.execute("UPDATE items SET last_synced_at='2026-09-19 12:00:07'")
    from tests.test_notify_run import alert
    alert(conn, "anomaly:first_merchant", "u1", "anomaly", {"merchant": "x", "amount": 5})   # untriaged: waiting
    text = notify.quiet_line(conn, "2026-09-20 18:00:00")
    assert "A quiet week, but 1 alert is still waiting" in text and ":07" not in text and "12:00 PM" in text


def test_linked_summary_of_an_empty_bank_does_not_print_a_question_mark(conn):
    seed(conn)
    conn.execute("DELETE FROM transactions")
    assert "?" not in notify.item_linked_summary(conn, "item1", AS_OF)


def test_database_is_created_0600_and_tightened(home):
    p = home / "finnamon.db"
    store.connect(p).close()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    os.chmod(p, 0o644)
    store.connect(p).close()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_settings_say_what_they_do(conn, capsys, monkeypatch):
    assert set(store.SETTINGS) | set(store.OPS_SETTINGS) <= set(store.SETTING_HELP)
    cli.main(["settings"]); assert all(r["description"] for r in json.loads(capsys.readouterr().out))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cli.main(["settings"]); text = capsys.readouterr().out
    assert "does not watch big purchases" in text and "finnamon settings set" in text
    with pytest.raises(ValueError, match="health_max_age_hours, 12 by default"):
        store.validate_setting("sync_interval_hours", 24, ops=True)


def test_every_command_has_a_description_and_the_human_ones_a_footer():
    p = cli.build_parser()
    for name in ("budget", "category", "alias", "threshold", "normal", "networth", "alerts"):
        assert p.commands[name].format_help().count("\n") > 3 and "show this help" in p.commands[name].format_help()
        assert any(a.help for a in p.commands[name]._actions if a.dest != "help") or name == "alias"
    for name in ("networth", "run", "daemon", "heartbeat", "notify", "triage", "settings", "import", "open", "remote", "web"):
        assert "Next:" in p.commands[name].format_help(), name
    assert "budgets" in p.commands["chart"].format_help() and "monthly_in_out" in p.commands["chart"].format_help()


def test_doctor_without_telegram_is_one_optional_line(home, fake_claude, capsys, monkeypatch):
    monkeypatch.setattr(scheduler, "unit_dir", lambda os_name=None: home / "units")
    monkeypatch.setattr(scheduler, "web_args", lambda: ["/usr/bin/node", "/x/web/server.js"])
    secrets.write({"client_id": "cid", "sandbox_secret": "sb"}, {}, {})
    store.connect().close()
    with pytest.raises(SystemExit):
        cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "· Telegram: not set up (optional)" in out and "Telegram bot" not in out and "Household chat" not in out


def test_heartbeat_with_no_chat_says_where_it_went(conn, capsys):
    cli.main(["heartbeat"])
    out = capsys.readouterr().out
    assert out.startswith("No Telegram chat is set up") and "Last sync was never" in out and '"sent"' not in out
