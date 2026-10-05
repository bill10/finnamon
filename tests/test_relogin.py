"""Re-login: the dashboard's Reconnect (link --update --web) and the daemon tick that syncs a bank as soon as its
update-mode session is over. Plaid and Telegram are faked."""
import json

import pytest

from finnamon import cli, daemon, link, notify, plaid_api, run, secrets, store, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import seed

T0 = 1_800_000_000


@pytest.fixture
def env(home, conn, tg, monkeypatch):
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"item1": "access-sandbox-x"})
    made = []
    monkeypatch.setattr(plaid_api, "link_token_create", lambda user_id, access_token=None, products=("transactions",), redirect_uri=None, hosted=True:
                        (made.append(access_token), {"link_token": f"lt{len(made)}", "hosted_link_url": f"https://hosted.plaid.com/{len(made)}", "expiration": "e"})[1])
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED', last_error='login' WHERE item_id='item1'")
    return made


def _alert(conn, key="health:item1:2026-10-04", kind="sync_health", status="ITEM_LOGIN_REQUIRED"):
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, sent_at) VALUES ('rule',?,?,?,'2026-10-04','2026-10-04 09:00:00')",
                 (kind, key, json.dumps({"item_id": "item1", "institution": "Chase", "status": status, "owner": "bill", "expires": "2026-11-01"})))
    return conn.execute("SELECT * FROM alerts WHERE key=?", (key,)).fetchone()


def test_reconnect_needs_no_chat_reuses_a_fresh_session_and_skips_the_assistants_budget(env, conn, tg):
    store.set_state(conn, "chat_id", None)                                     # a click needs no chat
    store.set_state(conn, "update_links", json.dumps([["item1", T0 - 60]]))    # the assistant sent one a minute ago
    r = link.start_update(conn, "item1", now=T0, to_chat=False)
    assert (r["url"], r["reused"], env) == ("https://hosted.plaid.com/1", False, ["access-sandbox-x"]) and not tg.sent
    assert link.start_update(conn, "item1", now=T0 + 5, to_chat=False)["url"] == "https://hosted.plaid.com/1"   # a double click: one session
    assert len(env) == 1
    assert link.start_update(conn, "item1", now=T0 + link.UPDATE_REUSE_S, to_chat=False)["url"] == "https://hosted.plaid.com/2"
    assert [x["link_token"] for x in json.loads(store.get_state(conn, "update_sessions"))] == ["lt1", "lt2"]
    assert json.loads(store.get_state(conn, "update_links")) == [["item1", T0 - 60]]   # a click spends none of the assistant's links
    with pytest.raises(ValueError, match="no linked bank"):
        link.start_update(conn, "nope", to_chat=False)


def test_the_chats_fix_link_is_remembered_too(env, conn, tg):
    link.start_update(conn, "item1", now=T0)
    assert "https://hosted.plaid.com/1" in tg.sent[-1]["text"]
    assert json.loads(store.get_state(conn, "update_sessions"))[0] == {
        "item_id": "item1", "link_token": "lt1", "url": "https://hosted.plaid.com/1", "started_at": T0, "expires_at": T0 + link.UPDATE_SESSION_TTL_S}


def test_the_tick_syncs_a_finished_relogin_at_once_resolves_its_alerts_and_says_so(env, conn, tg, monkeypatch):
    conn.execute("INSERT INTO items (item_id, institution, owner, status) VALUES ('item_1', 'Chase', 'bill', 'ITEM_LOGIN_REQUIRED')")
    secrets.update(items={"item_1": "access-sandbox-y"})
    a, c = _alert(conn, "health:item_1:2026-10-04"), _alert(conn, "health:item_1:consent:2026-11-01", "consent_expiring")
    other = _alert(conn)                                                      # health:item1:..., which LIKE 'health:item_1:%' would have matched
    link.start_update(conn, "item_1", now=T0, to_chat=False)
    synced = []
    def fake_sync(c_, item, token=None):
        synced.append(item)
        c_.execute("UPDATE items SET status='good' WHERE item_id=?", (item,))
        return {"item_id": item, "accounts": 2, "transactions": 0, "recurring": 0, "holdings": 0, "error": None}
    monkeypatch.setattr(sync, "sync_item", fake_sync)
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": []})                     # not opened yet
    assert link.check_updates(conn, now=T0 + 60) == [] and store.get_state(conn, "update_sessions")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"started_at": "x"}]})  # logging in
    assert link.check_updates(conn, now=T0 + 120) == [] and not synced
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "results": {}}]})
    held = run.lock()                                                                                      # a sync cycle is running
    try:
        assert link.check_updates(conn, now=T0 + 180) == [] and not synced
    finally:
        held.close()
    d = daemon.Daemon(conn_factory=lambda: conn)
    monkeypatch.setattr(d, "sync_due", lambda c_: False)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.sync_loop()                                                                                          # the next tick
    assert synced == ["item_1"]
    assert tuple(conn.execute("SELECT status, last_error FROM items WHERE item_id='item_1'").fetchone()) == ("good", None)
    rows = {r["id"]: (r["resolution"], bool(r["resolved_at"])) for r in conn.execute("SELECT * FROM alerts")}
    assert rows[a["id"]] == rows[c["id"]] == ("reconnected", True) and rows[other["id"]] == (None, False)
    assert tg.sent[-1]["text"] == "Chase is reconnected." and len(tg.sent) == 1
    assert store.get_state(conn, "update_sessions") is None
    d.stop.clear(); d.sync_loop()
    assert synced == ["item_1"] and len(tg.sent) == 1                                                     # once


def test_two_sessions_for_one_bank_sync_it_once(env, conn, tg, monkeypatch):
    link.start_update(conn, "item1", now=T0)
    link.start_update(conn, "item1", now=T0 + link.UPDATE_ITEM_GAP_S)
    synced = []
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: synced.append(item) or {"error": None})
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "results": {"item_add_results": [{"public_token": "p"}]}}]})
    assert len(link.check_updates(conn, now=T0 + link.UPDATE_ITEM_GAP_S + 60)) == 1 and synced == ["item1"]
    assert store.get_state(conn, "update_sessions") is None


def test_closed_expired_and_forgotten_sessions_are_dropped_quietly(env, conn, tg, monkeypatch):
    a = _alert(conn)
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": "ITEM_LOGIN_REQUIRED"})
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "exit": {"error": None}}]})   # closed without logging in
    assert link.check_updates(conn, now=T0 + 60)[0]["sync"]["error"] == "ITEM_LOGIN_REQUIRED"
    assert store.get_state(conn, "update_sessions") is None and not tg.sent
    assert conn.execute("SELECT resolved_at FROM alerts WHERE id=?", (a["id"],)).fetchone()[0] is None    # still broken: still open
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: pytest.fail("an expired session is not polled"))
    assert link.check_updates(conn, now=T0 + link.UPDATE_SESSION_TTL_S) == [] and store.get_state(conn, "update_sessions") is None
    link.start_update(conn, "item1", now=T0, to_chat=False)
    def forgot(lt): raise PlaidError({"error_code": "INVALID_LINK_TOKEN"})
    monkeypatch.setattr(plaid_api, "link_token_get", forgot)
    link.check_updates(conn, now=T0 + 60)
    assert store.get_state(conn, "update_sessions") is None and not tg.sent
    link.start_update(conn, "item1", now=T0, to_chat=False)
    def down(lt): raise PlaidError({"error_code": "INTERNAL_SERVER_ERROR"})
    monkeypatch.setattr(plaid_api, "link_token_get", down)
    link.check_updates(conn, now=T0 + 60)
    assert store.get_state(conn, "update_sessions"), "an outage is retried next tick"
    store.set_state(conn, "update_sessions", "not json")
    assert link.check_updates(conn) == []                                         # a corrupt row never wedges the tick


def test_cli_web_prints_the_url_and_the_assistant_cannot_use_it(env, conn, tg, monkeypatch, capsys):
    cli.main(["link", "--update", "item1", "--web"])
    assert json.loads(capsys.readouterr().out)["url"] == "https://hosted.plaid.com/1" and not tg.sent
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "item1", "--web"])
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "unknown", "--web"])


def test_the_page_gets_reconnect_and_its_own_words_and_telegram_keeps_the_reply_hint(env, conn, capsys):
    a = _alert(conn)
    assert "Reply <i>fix Chase</i>" in notify.render(a)
    assert "Reply" not in notify.render(a, page=True) and "needs a re-login" in notify.render(a, page=True)
    c = _alert(conn, "health:item1:consent:2026-11-01", "consent_expiring")
    assert "Reply" in notify.render(c) and "Reply" not in notify.render(c, page=True)
    for status, item in (("PENDING_EXPIRATION", "item1"), ("PENDING_DISCONNECT", "item1"), ("ITEM_LOGIN_REQUIRED", "item1"), ("INSTITUTION_DOWN", None)):
        assert notify.relogin_item(_alert(conn, f"health:item1:{status}", status=status)) == item
    _alert(conn, "health:dup", "duplicate_charge")
    cli.main(["alerts", "--sent", "--open"])
    shown = {r["kind"]: r for r in json.loads(capsys.readouterr().out)}
    assert shown["consent_expiring"]["reconnect"] == "item1" and "Reply" in shown["consent_expiring"]["text"]
    assert "reconnect" not in shown["duplicate_charge"] and "page_text" not in shown["duplicate_charge"]
