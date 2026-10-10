"""Owner onboarding over a fake Telegram: first owner via a one-time code in a DM, second owner via the code in a group,
directly (no daemon) and through the daemon's pending-owner path."""
import json

import pytest

from finnamon import daemon, owners, store
from tests.conftest import seed


def upd(uid, text, chat, mid, chat_type, is_bot=False, first_name="Jane"):
    return {"update_id": mid, "message": {"message_id": mid, "text": text, "chat": {"id": chat, "type": chat_type},
                                          "from": {"id": uid, "first_name": first_name, "is_bot": is_bot}}}


def test_add_first_requires_the_code_in_a_private_chat(conn, tg):
    code = "A1B2C3"
    tg.updates = [
        upd(999, f"/start {code}", -500, 10, "group"),        # wrong place: a group
        upd(999, "/start", 999, 11, "private"),               # a stranger without the code never becomes the owner
        {"update_id": 12, "message": {"chat": {"id": 111, "type": "private"}}},  # service message, no text
        upd(111, f"/start {code}", 111, 13, "private", first_name="Bill"),
    ]
    r = owners.add_first(conn, "bill", code, sleep=lambda s: None)
    assert r == {"owner": "bill", "telegram_user_id": 111, "chat_id": 111}
    assert tuple(conn.execute("SELECT telegram_user_id, display_name FROM owners WHERE owner='bill'").fetchone()) == (111, "Bill")
    assert store.get_state(conn, "chat_id") == "111" and store.get_state(conn, "telegram_offset") == "14"
    tg.updates = []
    with pytest.raises(TimeoutError, match="code"):
        owners.add_first(conn, "bill", code, wait_s=0, sleep=lambda s: None)


def test_add_member_direct_switches_household_chat_to_group(conn, tg):
    seed(conn)  # bill=111, chat_id = private 1234567890
    code = "ZZ9900"
    tg.me = {"username": "b", "can_read_all_group_messages": False}
    with pytest.raises(RuntimeError, match="privacy mode"):
        owners.add_member(conn, "jane", code, sleep=lambda s: None)
    tg.me["can_read_all_group_messages"] = True
    tg.updates = [
        upd(111, code, -100, 20, "supergroup"),                  # a known owner saying the code: fine, but it's Jane we want... still first match wins
    ]
    # a known owner typing the code would enrol themselves as jane; keep the code private to the newcomer
    tg.updates = [
        upd(333, code, -100, 21, "supergroup", is_bot=True),     # a bot
        upd(222, code, 222, 22, "private"),                      # the new person, but in a DM
        upd(555, "hey everyone", -100, 23, "supergroup"),        # a stranger without the code
        upd(222, code, -100, 24, "supergroup"),
    ]
    r = owners.add_member(conn, "jane", code, sleep=lambda s: None)
    assert r == {"owner": "jane", "telegram_user_id": 222, "chat_id": -100}
    assert store.get_state(conn, "chat_id") == "-100" and store.get_state(conn, "telegram_offset") == "25"
    assert store.get_state(conn, "session") is None  # fresh conversation for the new chat
    assert [str(m["chat_id"]) for m in tg.sent] == ["-100", "1234567890"]
    assert "Jane is in" in tg.sent[0]["text"] and "Bill, Jane" in tg.sent[0]["text"] and "goes quiet" in tg.sent[1]["text"]
    with pytest.raises(TimeoutError, match="code"):
        owners.add_member(conn, "nobody", "NOPE00", wait_s=0, sleep=lambda s: None)


def test_add_member_via_daemon_when_it_is_polling(conn, tg):
    seed(conn)
    store.set_state(conn, "daemon_alive_at", store.now_local())   # the daemon owns getUpdates
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, telegram_chat_id, telegram_message_id) VALUES ('rule','x','k','{}','2026-09-19 12:00:00','1234567890',5)")
    code = "C0DE01"
    d = daemon.Daemon(conn_factory=lambda: conn)
    ticks = {"n": 0}

    def sleep(s):  # the daemon handles updates while the CLI waits
        ticks["n"] += 1
        if ticks["n"] == 1:
            d.handle_update(conn, upd(555, "hi", -100, 30, "supergroup"))          # stranger, no code: ignored
            d.handle_update(conn, upd(222, code, 222, 31, "private"))              # code in a DM: ignored
            d.handle_update(conn, upd(222, code, -100, 32, "supergroup"))          # the newcomer with the code, in the group
    r = owners.add_member(conn, "jane", code, wait_s=60, sleep=sleep)
    assert r["owner"] == "jane" and r["telegram_user_id"] == 222 and str(r["chat_id"]) == "-100"
    assert store.get_state(conn, "pending_owner") is None and store.get_state(conn, "chat_id") == "-100"
    assert conn.execute("SELECT telegram_message_id FROM alerts").fetchone()[0] is None  # DM-era ids can't be replied to any more
    assert not tg.updates and "Jane is in" in tg.sent[0]["text"]
    # a message in the new group from jane now reaches the inbox with its chat id
    d.handle_update(conn, upd(222, "how much did we spend?", -100, 40, "supergroup"))
    assert d.inbox.get_nowait()["chat_id"] == "-100"


def test_a_household_group_takes_the_enrolment_code_only_from_itself(conn, tg):
    seed(conn)
    store.set_state(conn, "chat_id", "-100")                     # the household is already a group
    store.set_state(conn, "daemon_alive_at", store.now_local())
    d = daemon.Daemon(conn_factory=lambda: conn)
    store.set_state(conn, "pending_owner", json.dumps({"owner": "jane", "code": "C0DE02", "deadline": "2999-01-01 00:00:00", "switch": False}))
    d.handle_update(conn, upd(222, "C0DE02", -200, 50, "group"))   # another group the bot is in: refused, the chat stays
    assert store.get_state(conn, "chat_id") == "-100" and store.get_state(conn, "pending_owner")
    assert conn.execute("SELECT count(*) FROM owners WHERE owner='jane'").fetchone()[0] == 0
    d.handle_update(conn, upd(222, "C0DE02", -100, 51, "supergroup"))  # the household group itself
    assert store.get_state(conn, "chat_id") == "-100" and store.get_state(conn, "pending_owner") is None
    # `owner add --new-group` is the explicit ask to move
    store.set_state(conn, "pending_owner", json.dumps({"owner": "ann", "code": "C0DE03", "deadline": "2999-01-01 00:00:00", "switch": True}))
    d.handle_update(conn, upd(444, "C0DE03", -200, 52, "group"))
    assert store.get_state(conn, "chat_id") == "-200"


def test_add_member_direct_refuses_another_group_without_new_group(conn, tg):
    seed(conn)
    store.set_state(conn, "chat_id", "-100")
    tg.updates = [upd(222, "ZZ9901", -200, 60, "group"), upd(222, "ZZ9901", -100, 61, "group")]
    r = owners.add_member(conn, "jane", "ZZ9901", sleep=lambda s: None)
    assert str(r["chat_id"]) == "-100" and store.get_state(conn, "chat_id") == "-100"
    tg.updates = [upd(444, "ZZ9902", -200, 62, "group")]
    assert str(owners.add_member(conn, "ann", "ZZ9902", sleep=lambda s: None, switch=True)["chat_id"]) == "-200"


def test_may_enrol_from(conn):
    assert owners.may_enrol_from(conn, -200, False)                 # no household chat yet
    store.set_state(conn, "chat_id", "111")
    assert owners.may_enrol_from(conn, -200, False)                 # a DM household moves to its first group
    store.set_state(conn, "chat_id", "-100")
    assert owners.may_enrol_from(conn, -100, False) and not owners.may_enrol_from(conn, -200, False) and owners.may_enrol_from(conn, -200, True)


def test_a_pending_code_from_an_older_cli_has_no_switch_and_stays_in_the_group(conn, tg):
    seed(conn)
    store.set_state(conn, "chat_id", "-100")
    d = daemon.Daemon(conn_factory=lambda: conn)
    store.set_state(conn, "pending_owner", json.dumps({"owner": "jane", "code": "C0DE04", "deadline": "2999-01-01 00:00:00"}))
    d.handle_update(conn, upd(222, "C0DE04", -200, 70, "group"))
    assert store.get_state(conn, "chat_id") == "-100" and store.get_state(conn, "pending_owner")


def test_owner_add_no_telegram(home, conn, tg, monkeypatch, capsys):
    import json
    from finnamon import cli, plaid_api, secrets
    seed(conn)
    cli.main(["owner", "add", "jane", "--no-telegram"])
    assert json.loads(capsys.readouterr().out) == {"owner": "jane", "telegram_user_id": None}
    assert tuple(conn.execute("SELECT display_name, telegram_user_id FROM owners WHERE owner='jane'").fetchone()) == ("Jane", None)
    for argv, why in ((["owner", "add", "jane", "--no-telegram"], "already"), (["owner", "add", "kim", "--no-telegram", "--user-id", "9"], "can't be combined"),
                      (["owner", "add", "kim", "--no-telegram", "--new-group"], "can't be combined")):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert why in capsys.readouterr().err
    assert not conn.execute("SELECT 1 FROM owners WHERE owner='kim'").fetchone()
    # the daemon: a NULL id matches no sender, a missing id neither
    store.set_state(conn, "chat_id", 555)
    d = daemon.Daemon() if hasattr(daemon, "Daemon") else None
    for sender in ({"id": 777, "first_name": "X"}, {"first_name": "no id"}):
        d.handle_update(conn, {"update_id": 1, "message": {"message_id": 1, "date": 1, "text": "hi", "chat": {"id": 555, "type": "private"}, "from": sender}})
    assert d.inbox.empty()
    # a Claude session may attribute banks to her
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: {"link_token": "lt", "hosted_link_url": "https://h/x", "expiration": "2026-09-20T00:00:00Z"})
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    capsys.readouterr()
    cli.main(["link", "--start", "--owner", "jane"])
    assert "https://h/x" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["owner", "add", "kim", "--no-telegram"])
    # later: the id fills in
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE")
    cli.main(["owner", "add", "jane", "--user-id", "42"])
    assert conn.execute("SELECT telegram_user_id FROM owners WHERE owner='jane'").fetchone()[0] == 42
