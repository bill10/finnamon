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
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": []})   # opened by nobody yet; tests that need more say so
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
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "results": {"item_add_results": [{"public_token": "p"}]}}]})
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
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "unknown", "--web"])
    assert "no linked bank" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["link", "--web"])                                               # never the blocking add
    assert "--web goes with --update" in capsys.readouterr().err
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: pytest.fail("the assistant never reaches the unlimited path"))
    for argv in (["link", "--update", "item1", "--web"], ["link", "--update", "item1", "--web", "--telegram"]):   # the allow list's `--update * --telegram` matches the second
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "from a Claude session" in capsys.readouterr().err
    assert len(json.loads(store.get_state(conn, "update_sessions"))) == 1 and not tg.sent


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


# Value: protects=a failed "<bank> is reconnected" chat message never stops the tick: every finished bank still syncs and goes good;
# fails_when=the TelegramError guard in check_updates is removed or narrowed; why_new=no test fails the chat send after a reconnect; seam=none
def test_a_failed_chat_message_still_reconnects_every_finished_bank(env, conn, tg, monkeypatch):
    from finnamon import telegram
    conn.execute("INSERT INTO items (item_id, institution, owner, status) VALUES ('item_2', 'Ally', 'bill', 'ITEM_LOGIN_REQUIRED')")
    secrets.update(items={"item_2": "access-sandbox-z"})
    link.start_update(conn, "item1", now=T0, to_chat=False)
    link.start_update(conn, "item_2", now=T0, to_chat=False)
    synced = []
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: synced.append(item) or {"item_id": item, "error": None})
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "results": {}}]})
    tg.fail_with = telegram.TelegramError(502, "down")
    assert [r["item_id"] for r in link.check_updates(conn, now=T0 + 60)] == ["item1", "item_2"] and synced == ["item1", "item_2"]
    assert {r[0] for r in conn.execute("SELECT status FROM items WHERE item_id IN ('item1', 'item_2')")} == {"good"}
    assert store.get_state(conn, "update_sessions") is None                     # not re-synced next tick


# Value: protects=a bank unlinked while its re-login was open is dropped, never synced; fails_when=the missing-item guard is removed
# (sync of a gone Item); why_new=check_updates tests always keep the item; seam=none
def test_a_bank_unlinked_mid_relogin_is_dropped_without_a_sync(env, conn, tg, monkeypatch):
    conn.execute("INSERT INTO items (item_id, institution, owner, status) VALUES ('item_3', 'Ally', 'bill', 'ITEM_LOGIN_REQUIRED')")
    secrets.update(items={"item_3": "access-sandbox-z"})
    link.start_update(conn, "item_3", now=T0, to_chat=False)
    conn.execute("DELETE FROM items WHERE item_id='item_3'")                      # `link --remove` meanwhile
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: pytest.fail("an unlinked bank is not synced"))
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "y", "results": {}}]})
    assert link.check_updates(conn, now=T0 + 60) == [] and store.get_state(conn, "update_sessions") is None and not tg.sent


# Value: protects=a re-login check that throws never kills the daemon's sync loop; fails_when=check_relogins stops catching;
# why_new=the tick test only runs a check_updates that works; seam=none
def test_a_broken_relogin_check_never_stops_the_tick(conn, monkeypatch):
    monkeypatch.setattr(link, "check_updates", lambda c_: (_ for _ in ()).throw(RuntimeError("db locked")))
    daemon.Daemon(conn_factory=lambda: conn).check_relogins(conn)                 # logged, not raised


# Value: protects=the dashboard's Reconnect gets a clean error (exit 1, message) on a Plaid failure, and no session is remembered;
# fails_when=PlaidError leaves cmd_link's --web catch, or a session is stored before start() works; why_new=only the unknown-id die is tested; seam=none
def test_cli_web_reports_a_plaid_error_and_remembers_no_session(env, conn, tg, monkeypatch, capsys):
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_ACCESS_TOKEN"})))
    with pytest.raises(SystemExit) as e:
        cli.main(["link", "--update", "item1", "--web"])
    assert e.value.code == 1 and "INVALID_ACCESS_TOKEN" in capsys.readouterr().err
    assert store.get_state(conn, "update_sessions") is None and not tg.sent


def _finished(proof):
    return lambda lt: {"link_sessions": [{"finished_at": "y", "results": {"item_add_results": [{"public_token": "p"}]} if proof else {}}]}


def test_a_page_closed_on_a_bank_that_still_syncs_proves_nothing(env, conn, tg, monkeypatch):
    # an expiring connection: the bank is good and syncs either way, so a closed page must not silence the expiry warning
    conn.execute("UPDATE items SET status='good', last_error=NULL WHERE item_id='item1'")
    c = _alert(conn, "health:item1:consent:2026-11-01", "consent_expiring")
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: pytest.fail("nothing to check: the bank never broke"))
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(False))
    assert link.check_updates(conn, now=T0 + 60) == [] and store.get_state(conn, "update_sessions") is None and not tg.sent
    assert conn.execute("SELECT resolved_at FROM alerts WHERE id=?", (c["id"],)).fetchone()[0] is None
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": None})
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(True))            # logged in: the consent is renewed
    link.check_updates(conn, now=T0 + 60)
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (c["id"],)).fetchone()[0] == "reconnected"
    assert tg.sent[-1]["text"] == "Chase is reconnected."


def test_a_closed_page_on_a_broken_bank_resolves_only_if_the_sync_works_and_never_the_consent_alert(env, conn, tg, monkeypatch):
    a, c = _alert(conn), _alert(conn, "health:item1:consent:2026-11-01", "consent_expiring")
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": None})
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(False))           # update-mode results can be thin: the sync decides
    link.check_updates(conn, now=T0 + 60)
    got = {r["id"]: r["resolution"] for r in conn.execute("SELECT id, resolution FROM alerts")}
    assert (got[a["id"]], got[c["id"]]) == ("reconnected", None)


def test_a_resolved_alert_frees_its_key_so_a_second_break_today_alerts_again(env, conn, tg, monkeypatch):
    a = _alert(conn)
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": None})
    link.reconnect(conn, "item1")
    assert conn.execute("SELECT key FROM alerts WHERE id=?", (a["id"],)).fetchone()[0] == f"health:item1:2026-10-04:reconnected:{a['id']}"
    assert _alert(conn)["resolved_at"] is None                                    # the detector's INSERT OR IGNORE lands


def test_a_sync_that_raises_keeps_the_session_for_the_next_tick(env, conn, tg, monkeypatch):
    import sqlite3
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(True))
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError):
        link.check_updates(conn, now=T0 + 60)
    assert store.get_state(conn, "update_sessions")


def test_a_session_opened_while_the_tick_runs_survives_and_polling_needs_no_lock(env, conn, tg, monkeypatch):
    link.start_update(conn, "item1", now=T0, to_chat=False)
    held, polled = run.lock(), []
    try:
        monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: polled.append(lt) or {"link_sessions": [{"started_at": "x"}]})
        assert link.check_updates(conn, now=T0 + 60) == [] and polled == ["lt1"]   # polled while a sync cycle holds the lock
    finally:
        held.close()
    def sync_and_click(c_, item, token=None):                                     # the person clicks Reconnect again mid-sync
        link.start_update(c_, "item1", now=T0 + link.UPDATE_REUSE_S, to_chat=False)
        return {"item_id": item, "error": None}
    monkeypatch.setattr(sync, "sync_item", sync_and_click)
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: _finished(True)(lt) if lt == "lt1" else {"link_sessions": []})
    link.check_updates(conn, now=T0 + 60)
    assert [x["link_token"] for x in json.loads(store.get_state(conn, "update_sessions"))] == ["lt2"]


def test_reconnect_never_hands_back_a_session_that_is_already_over(env, conn, monkeypatch):
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(False))            # closed, and the tick has not run yet
    assert link.start_update(conn, "item1", now=T0 + 5, to_chat=False)["url"] == "https://hosted.plaid.com/2"
    def down(lt): raise PlaidError({"error_code": "INTERNAL_SERVER_ERROR"})
    monkeypatch.setattr(plaid_api, "link_token_get", down)
    assert link.start_update(conn, "item1", now=T0 + 10, to_chat=False)["reused"] is False   # unknown: a new one


def test_the_page_and_notify_agree_on_which_statuses_need_a_login():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "web/public/app.js").read_text()
    rx = re.compile(re.search(r"const RELOGIN = /(.+?)/;", src).group(1))
    assert all(rx.search(s) for s in notify.RELOGIN) and not rx.search("INSTITUTION_DOWN") and not rx.search("good")


def test_a_bank_a_cycle_already_fixed_still_gets_its_alert_resolved_and_the_message(env, conn, tg, monkeypatch):
    # the person logged in while a sync cycle ran: the cycle set the bank good before the tick saw the session end
    a, c = _alert(conn), _alert(conn, "health:item1:consent:2026-11-01", "consent_expiring")
    conn.execute("UPDATE items SET status='good', last_error=NULL WHERE item_id='item1'")
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": None})
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(False))            # thin results: no token
    link.check_updates(conn, now=T0 + 60)
    got = {r["id"]: (r["resolution"], r["key"]) for r in conn.execute("SELECT id, resolution, key FROM alerts")}
    assert got[a["id"]][0] == "reconnected" and got[c["id"]] == (None, "health:item1:consent:2026-11-01")   # no proof: the expiry warning stays
    assert tg.sent[-1]["text"] == "Chase is reconnected."
    link.start_update(conn, "item1", now=T0 + link.UPDATE_REUSE_S, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(True))
    link.check_updates(conn, now=T0 + link.UPDATE_REUSE_S + 60)                   # renewed: resolved, and its key freed
    assert tuple(conn.execute("SELECT resolution, key FROM alerts WHERE id=?", (c["id"],)).fetchone()) == ("reconnected", f"health:item1:consent:2026-11-01:reconnected:{c['id']}")
    assert _alert(conn, "health:item1:consent:2026-11-01", "consent_expiring")["resolved_at"] is None   # a date the login did not move warns again


def test_a_transient_plaid_error_after_the_login_keeps_the_session_and_its_proof(env, conn, tg, monkeypatch):
    a = _alert(conn)
    link.start_update(conn, "item1", now=T0, to_chat=False)
    monkeypatch.setattr(plaid_api, "link_token_get", _finished(True))
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": "INTERNAL_SERVER_ERROR", "transient": True})
    link.check_updates(conn, now=T0 + 60)
    assert store.get_state(conn, "update_sessions") and not tg.sent              # Plaid's outage, not the bank's answer
    monkeypatch.setattr(sync, "sync_item", lambda c_, item, token=None: {"item_id": item, "error": None})
    link.check_updates(conn, now=T0 + 120)
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (a["id"],)).fetchone()[0] == "reconnected"
    assert tg.sent[-1]["text"] == "Chase is reconnected." and store.get_state(conn, "update_sessions") is None


def test_sync_item_says_when_its_error_is_plaids_outage(env, conn, monkeypatch):
    for code, transient in (("INTERNAL_SERVER_ERROR", True), ("ITEM_LOGIN_REQUIRED", False)):
        monkeypatch.setattr(plaid_api, "accounts_get", lambda tok, code=code: (_ for _ in ()).throw(PlaidError({"error_code": code})))
        assert sync.sync_item(conn, "item1")["transient"] is transient


def test_a_session_with_a_non_numeric_time_reads_as_no_session(env, conn):
    store.set_state(conn, "update_sessions", json.dumps([{"item_id": "item1", "link_token": "lt", "url": "u", "started_at": "soon", "expires_at": 1}]))
    assert link.check_updates(conn) == [] and link.start_update(conn, "item1", now=T0, to_chat=False)["reused"] is False


# Value: protects=a PENDING_EXPIRATION sync_health reads as a re-login, and a non-login error points at finnamon doctor with its error text;
# fails_when=render checks only ITEM_LOGIN_REQUIRED again, or the error branch drops the doctor line or last_error; why_new=render of
# these statuses was untested; seam=none
def test_sync_health_text_for_pending_expiration_and_a_plain_error(env, conn):
    assert "Chase needs a re-login" in notify.render(_alert(conn, "health:item1:pe", status="PENDING_EXPIRATION"))
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','sync_health','health:item1:err',?,'2026-10-04')",
                 (json.dumps({"item_id": "item1", "institution": "Chase", "status": "INSTITUTION_DOWN", "last_error": "bank is down."}),))
    text = notify.render(conn.execute("SELECT * FROM alerts WHERE key='health:item1:err'").fetchone())
    assert "Chase couldn't sync:</b> the bank's site is down (INSTITUTION_DOWN). bank is down. Run <code>finnamon doctor</code>" in text and "re-login" not in text
