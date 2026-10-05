"""Link flow with Plaid faked: start (create vs update), register_item fallbacks, complete in both modes,
announce, and the failover no-op cases."""
import json

import pytest

from finnamon import assistant

from finnamon import config, link, plaid_api, secrets, store, sync
from finnamon.plaid_api import PlaidError
from tests.conftest import AS_OF, seed, txn


def test_link_start_register_complete_announce(conn, tg, monkeypatch):
    seed(conn)
    seen = []
    monkeypatch.setattr(plaid_api, "link_token_create", lambda user_id, access_token=None, products=("transactions",), redirect_uri=None, hosted=True:
                        (seen.append((user_id, access_token)), {"link_token": "lt", "hosted_link_url": "https://h", "expiration": "e"})[1])
    assert link.start("bill") == {"link_token": "lt", "url": "https://h", "expiration": "e"} and seen[-1] == ("finnamon-bill", None)
    with pytest.raises(ValueError, match="no token for item"):
        link.start("bill", update_item="item1")  # update mode needs the token in secrets.toml
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"item1": "tok1"})
    link.start("bill", update_item="item1")
    assert seen[-1] == ("finnamon-bill", "tok1")
    # register_item: item/get failing is not fatal; institution name falls back to the id lookup, then to the item_id
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "API_ERROR"})))
    assert link.register_item(conn, "jane", "tok2", "item2") == "item2"
    assert tuple(conn.execute("SELECT institution, owner, status FROM items WHERE item_id='item2'").fetchone()) == (None, "jane", "good")
    assert conn.execute("SELECT count(*) FROM owners WHERE owner='jane'").fetchone()[0] == 1 and config.item_token("item2") == "tok2"
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_9"}})
    monkeypatch.setattr(plaid_api, "institution_get", lambda i: {"institution": {"name": "Ally"}})
    assert link.register_item(conn, "jane", "tok2", "item2") == "Ally"
    assert conn.execute("SELECT institution FROM items WHERE item_id='item2'").fetchone()[0] == "Ally"
    # complete, update mode: no exchange, status reset, resync
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED', last_error='x' WHERE item_id='item1'")
    synced = []
    monkeypatch.setattr(sync, "sync_item", lambda c, i, t=None: (synced.append((i, t)), {"item_id": i, "accounts": 1, "transactions": 2, "recurring": 0, "error": None})[1])
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: pytest.fail("update mode must not exchange"))
    r = link.complete(conn, "bill", "", update_item="item1")
    assert r["updated"] and synced == [("item1", None)]
    assert tuple(conn.execute("SELECT status, last_error FROM items WHERE item_id='item1'").fetchone()) == ("good", None)
    # complete, new Item: exchange → register → first sync → mirror scan
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "item3", "access_token": "tok3"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_1", "institution_name": "Chase"}})
    r = link.complete(conn, "jane", "public-x")
    assert (r["item_id"], r["institution"], r["mirror_candidates"]) == ("item3", "Chase", []) and synced[-1] == ("item3", "tok3")
    assert config.item_token("item3") == "tok3"
    # announce goes to the household chat, and is silent without one
    link.announce(conn, "item1")
    assert "Linked Chase" in tg.sent[-1]["text"]
    store.set_state(conn, "chat_id", None)
    link.announce(conn, "item1")
    assert len(tg.sent) == 1
    # failover: nothing to promote without a mirror, or when the mirror's Item is unhealthy
    assert link.failover(conn, "chk") is None
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk_j','item2','x','depository','checking','4821','joint','chk')")
    conn.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED' WHERE item_id='item2'")
    assert link.failover(conn, "chk") is None
    # detect_mirrors ignores same-mask accounts with too little shared history
    conn.execute("UPDATE accounts SET mirror_of=NULL WHERE account_id='chk_j'")
    conn.execute("UPDATE items SET institution_id='ins_1' WHERE item_id='item2'")
    assert link.detect_mirrors(conn, "item2") == []
    # polling gives up at the deadline
    monkeypatch.setattr(plaid_api, "link_token_get", lambda t: pytest.fail("deadline already passed"))
    assert link.wait_for_public_token("lt", timeout_s=0, sleep=lambda s: None) is None


def test_link_remove_drops_item_rows_and_token(home, conn, monkeypatch, capsys):
    from finnamon import cli, secrets, config, link
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s", "production_secret": "p"}, {"bot_token": "1:x"}, {"item1": "tok1", "other": "tok2"})
    removed = []
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: removed.append(tok) or {"removed": True})
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1, 1)", (AS_OF,))
    txn(conn, "t1", "chk", "2026-09-01", 5, "X")
    cli.main(["link", "--remove", "item1", "--yes"])
    assert removed == ["tok1"] and config.item_tokens() == {"other": "tok2"}
    s = config.load_secrets()
    assert s["plaid"] == {"client_id": "c", "sandbox_secret": "s", "production_secret": "p"} and s["telegram"] == {"bot_token": "1:x"}  # other sections untouched
    assert conn.execute("SELECT count(*) FROM items WHERE item_id='item1'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM accounts WHERE item_id='item1'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    with pytest.raises(ValueError):
        link.remove(conn, "nope")


def test_link_remove_without_token_skips_plaid_and_secrets(home, conn, monkeypatch):
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"other": "tok2"})
    before = config.secrets_path().read_text()
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: pytest.fail("no token, so no Plaid call"))
    r = link.remove(conn, "item1")
    assert r == {"removed": "item1", "institution": "Chase", "accounts": 2}
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    assert config.secrets_path().read_text() == before  # not rewritten


def test_link_remove_tolerates_invalid_access_token_but_reraises_others(home, conn, monkeypatch):
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"item1": "tok1"})
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "RATE_LIMIT_EXCEEDED"})))
    with pytest.raises(PlaidError):
        link.remove(conn, "item1")
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1 and config.item_tokens() == {"item1": "tok1"}  # untouched
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_ACCESS_TOKEN"})))
    assert link.remove(conn, "item1")["removed"] == "item1"
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0 and config.item_tokens() == {}


def test_link_remove_unlinks_mirrors_and_drops_all_account_rows(home, conn, monkeypatch):
    seed(conn)
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: {})
    conn.execute("INSERT INTO items (item_id, owner) VALUES ('item2','bill')")
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner, mirror_of) VALUES ('chk_j','item2','x','depository','checking','4821','bill','chk')")
    conn.execute("INSERT INTO balances VALUES ('chk', ?, 1, 1)", (AS_OF,))
    conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, frequency, avg_amount, last_amount, first_date, last_date, status, first_seen_at) "
                 "VALUES ('s','chk','outflow','Netflix','MONTHLY',15,15,'2026-01-05','2026-09-05','MATURE',?)", (AS_OF,))
    conn.execute("INSERT INTO holdings (account_id, security_id, as_of, name, ticker, quantity, price, value) VALUES ('cc','s1',?,'V','VOO',1,1,1)", (AS_OF,))
    conn.execute("INSERT INTO settings (account_id, key, value) VALUES ('chk','low_balance_threshold','100')")
    conn.execute("INSERT INTO settings (account_id, key, value) VALUES ('*','low_balance_threshold','500')")
    conn.execute("INSERT INTO suppressions (kind, canonical, account_id) VALUES ('x','Costco','chk')")
    conn.execute("INSERT INTO suppressions (kind, canonical, account_id) VALUES ('x','Netflix',NULL)")
    txn(conn, "t1", "chk", "2026-09-05", 10, "COSTCO")
    conn.execute("INSERT INTO tx_category_override (transaction_id, pfc_primary, pfc_detailed) VALUES ('t1','FOOD_AND_DRINK','FOOD_AND_DRINK_GROCERIES')")
    link.remove(conn, "item1")
    assert conn.execute("SELECT count(*) FROM tx_category_override").fetchone()[0] == 0   # a re-import must not revive it
    assert conn.execute("SELECT mirror_of FROM accounts WHERE account_id='chk_j'").fetchone()[0] is None
    for table in ("balances", "transactions", "recurring", "holdings"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert [r[0] for r in conn.execute("SELECT DISTINCT account_id FROM settings")] == ["*"]  # global settings survive
    assert [r[0] for r in conn.execute("SELECT canonical FROM suppressions")] == ["Netflix"]  # account-scoped gone, household-wide kept
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1  # item2 untouched


def test_cli_link_remove_dies_cleanly(home, conn, monkeypatch, capsys):
    from finnamon import cli
    with pytest.raises(SystemExit) as ex:
        cli.main(["link", "--remove", "nope"])
    assert ex.value.code == 1 and "unknown item nope" in capsys.readouterr().err
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"item1": "tok1"})
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "INTERNAL_SERVER_ERROR", "error_message": "boom"})))
    with pytest.raises(SystemExit) as ex:
        cli.main(["link", "--remove", "item1", "--yes"])
    assert ex.value.code == 1 and "INTERNAL_SERVER_ERROR: boom" in capsys.readouterr().err
    # without --yes it asks, and "n" leaves everything in place
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    with pytest.raises(SystemExit):
        cli.main(["link", "--remove", "item1"])
    assert "not removed" in capsys.readouterr().err and conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_link_remove_orphan_token_without_item_row(home, conn, monkeypatch):
    """A crash between the DB delete and the secrets rewrite leaves a token with no row; a re-run scrubs it."""
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"ghost": "tok"})
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_ACCESS_TOKEN"})))
    assert link.remove(conn, "ghost") == {"removed": "ghost", "institution": None, "accounts": 0}
    assert config.item_tokens() == {}


def test_cli_link_update_and_remove_are_exclusive(home, conn):
    from finnamon import cli
    with pytest.raises(SystemExit) as ex:
        cli.main(["link", "--update", "a", "--remove", "b"])
    assert ex.value.code == 2


def test_link_remove_refuses_while_a_run_holds_the_lock(home, conn, monkeypatch, capsys):
    from finnamon import cli, run as runmod
    seed(conn)
    held = runmod.lock()
    try:
        with pytest.raises(SystemExit):
            cli.main(["link", "--remove", "item1", "--yes"])
        assert "another run is in progress" in capsys.readouterr().err
        assert conn.execute("SELECT count(*) FROM items WHERE item_id='item1'").fetchone()[0] == 1
    finally:
        held.close()


def test_link_remove_recovers_after_a_crash_and_resolves_pending_alerts(home, conn, monkeypatch):
    """Plaid says ITEM_NOT_FOUND once an Item is gone; a re-run after a crash between the Plaid call and the local delete must finish."""
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {"item1": "tok1"})
    monkeypatch.setattr(plaid_api, "item_remove", lambda tok: (_ for _ in ()).throw(PlaidError({"error_code": "ITEM_NOT_FOUND"})))
    conn.execute("INSERT INTO alerts (tier, kind, key, account_id, payload_json, as_of) VALUES ('rule','low_balance','lb:chk','chk','{}',?)", (AS_OF,))
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','sync_health','health:item1:2026-09-19','{}',?)", (AS_OF,))
    conn.execute("INSERT INTO alerts (tier, kind, key, account_id, payload_json, as_of, sent_at) VALUES ('rule','low_balance','lb:old','chk','{}',?,?)", (AS_OF, AS_OF))
    assert link.remove(conn, "item1")["accounts"] == 2
    assert config.item_tokens() == {}
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT key, resolved_at IS NOT NULL, sent_at IS NOT NULL FROM alerts")}
    assert rows["lb:chk"] == (1, 1) and rows["health:item1:2026-09-19"] == (1, 1)   # never sent pointing at a dead Item
    assert rows["lb:old"] == (0, 1)                                                    # already-sent history untouched


def test_link_complete_survives_a_failed_first_sync(home, conn, tg, monkeypatch):
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    def fake_register(conn, owner, tok, item):
        conn.execute("INSERT INTO items (item_id, owner, institution) VALUES (?,?,'Chase')", (item, owner)); return "Chase"
    monkeypatch.setattr(link, "register_item", fake_register)
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = link.complete(conn, "bill", "pt")
    assert r["institution"] == "Chase" and r["sync"]["error"].startswith("RuntimeError") and r["mirror_candidates"] == []


def _pending_env(conn, monkeypatch, tg):
    seed(conn)
    secrets.write({"client_id": "c", "sandbox_secret": "s"}, {}, {})
    store.set_state(conn, "chat_id", 555)
    monkeypatch.setattr(plaid_api, "link_token_create", lambda user_id, access_token=None, products=("transactions",), redirect_uri=None, hosted=True:
                        {"link_token": "lt", "hosted_link_url": "https://hosted.plaid.com/x", "expiration": "2026-09-20T00:00:00Z"})


def test_link_start_is_non_blocking_and_the_daemon_finishes_it(home, conn, tg, monkeypatch, capsys):
    import json
    from finnamon import cli, daemon
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")          # the assistant may start an add...
    cli.main(["link", "--start"])
    assert "hosted.plaid.com/x" in capsys.readouterr().out and not tg.sent      # asked at the terminal: the link stays there
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {}}]})
    created = []
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: created.append(1) or (_ for _ in ()).throw(AssertionError("must reuse")))
    cli.main(["link", "--start", "--telegram"])
    assert "hosted.plaid.com/x" in tg.sent[-1]["text"] and not created          # asked on Telegram: the same open session, sent to the chat
    assert "Still open from earlier" in capsys.readouterr().out
    assert json.loads(store.get_state(conn, "pending_link"))["owner"] == "bill"
    for argv in (["link"], ["link", "--update", "item1"], ["link", "--remove", "item1", "--yes"], ["link", "--owner", "bill", "--remove", "item1"]):
        with pytest.raises(SystemExit):                        # ...and nothing else about linking
            cli.main(argv)
        assert "for a person at a terminal" in capsys.readouterr().err
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE")
    # still in the browser: the tick does nothing
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {}}]})
    assert link.check_pending(conn)["state"] == "waiting" and store.get_state(conn, "pending_link")
    cli.main(["link", "--finish"]); assert "still waiting" in capsys.readouterr().out
    # logged in: the daemon tick completes, syncs, announces, reports a mirror instead of applying it
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    def fake_register(conn, owner, tok, item):
        conn.execute("INSERT INTO items (item_id, owner, institution) VALUES (?,?,'Chase')", (item, owner)); return "Chase"
    monkeypatch.setattr(link, "register_item", fake_register)
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})
    monkeypatch.setattr(link, "detect_mirrors", lambda conn, item: [{"new": "n1", "existing": "chk", "name": "Checking", "mask": "4821", "match": "persistent_account_id"}])
    d = daemon.Daemon(conn_factory=lambda: conn)
    monkeypatch.setattr(d, "sync_due", lambda c: False)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.sync_loop()
    assert store.get_state(conn, "pending_link") is None
    assert "Linked Chase" in tg.sent[-1]["text"] and "account merge n1 chk" in tg.sent[-1]["text"]
    assert conn.execute("SELECT mirror_of FROM accounts WHERE account_id='chk'").fetchone()[0] is None   # reported, not applied
    cli.main(["link", "--finish"]); assert "nothing pending" in capsys.readouterr().out


def test_link_finish_reports_closed_and_expired_sessions(home, conn, tg, monkeypatch, capsys):
    import json
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "x", "results": {}}]})
    with pytest.raises(SystemExit):
        cli.main(["link", "--finish"])
    assert "closed" in capsys.readouterr().err and store.get_state(conn, "pending_link")   # closed for now; the URL still works, keep polling
    cli.main(["link", "--finish"]); assert "still waiting" in capsys.readouterr().out       # said once
    # the person reopens the link and finishes: the second session wins
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "x", "results": {}}, {"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    def fake_register(conn, owner, tok, item):
        conn.execute("INSERT INTO items (item_id, owner, institution) VALUES (?,?,'Chase')", (item, owner)); return "Chase"
    monkeypatch.setattr(link, "register_item", fake_register)
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})
    cli.main(["link", "--finish"]); assert "Linked Chase" in capsys.readouterr().out and store.get_state(conn, "pending_link") is None
    store.set_state(conn, "pending_link", None)
    link.start_pending(conn, "bill")
    p = json.loads(store.get_state(conn, "pending_link")); p["started_at"] = "2020-01-01 00:00:00"
    store.set_state(conn, "pending_link", json.dumps(p))
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: (_ for _ in ()).throw(AssertionError("must not poll an expired session")))
    assert link.check_pending(conn) == {"state": "expired"} and store.get_state(conn, "pending_link") is None


def test_blocking_link_posts_to_telegram_only_when_asked(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setattr(link, "wait_for_public_token", lambda lt: None)
    with pytest.raises(SystemExit):        # session ended without a bank; that's fine for this test
        cli.main(["link"])
    assert "hosted.plaid.com/x" in capsys.readouterr().out and not tg.sent
    with pytest.raises(SystemExit):
        cli.main(["link", "--telegram"])
    assert "hosted.plaid.com/x" in tg.sent[-1]["text"]


def test_start_pending_without_a_chat_keeps_the_link_local(home, conn, tg, monkeypatch):
    _pending_env(conn, monkeypatch, tg)
    store.set_state(conn, "chat_id", None)
    s = link.start_pending(conn, "bill", to_telegram=True)
    assert s["url"] == "https://hosted.plaid.com/x" and not tg.sent and store.get_state(conn, "pending_link")


def test_link_finish_from_claude_prints_summary_and_mirror_note(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")           # the gate lets --finish through too
    link.start_pending(conn, "jane")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(link, "complete", lambda c, o, pt, u=None, **kw: {"item_id": "item9", "institution": "Ally", "sync": {"accounts": 2, "transactions": 5, "recurring": 0, "holdings": 1, "error": "RuntimeError: boom"},
                                                                     "mirror_candidates": [{"new": "n1", "existing": "chk", "name": "Checking", "mask": "4821", "match": "persistent_account_id"}]})
    monkeypatch.setattr(link, "announce", lambda c, i, m=None: tg.sent.append({"text": f"announce {i} {len(m or [])}"}))
    cli.main(["link", "--finish"])
    out = capsys.readouterr().out
    assert "Linked Ally (item9): 2 accounts, 5 transactions, 1 holdings, error RuntimeError: boom" in out
    assert "account merge n1 chk" in out and tg.sent[-1]["text"] == "announce item9 1" and store.get_state(conn, "pending_link") is None


def test_daemon_tick_survives_a_failing_pending_check(home, conn, tg, monkeypatch, caplog):
    from finnamon import daemon
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: (_ for _ in ()).throw(PlaidError({"error_code": "API_ERROR"})))
    d = daemon.Daemon(conn_factory=lambda: conn)
    ticks = []
    monkeypatch.setattr(d, "sync_due", lambda c: False)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: ticks.append(t) or (len(ticks) == 2 and d.stop.set()))
    d.sync_loop()                                              # two ticks, both raise inside check_pending; the loop keeps going
    assert len(ticks) == 2 and caplog.text.count("pending link check failed") == 2 and store.get_state(conn, "pending_link")


def test_settings_allow_only_start_and_finish_from_claude():
    import json
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]
    assert {"Bash(finnamon link --start)", "Bash(finnamon link --start *)", "Bash(finnamon link --finish)"} <= set(perms["allow"])
    assert {"Bash(finnamon link)", "Bash(finnamon link --remove*)", "Bash(finnamon link --owner*)"} <= set(perms["deny"])
    assert "Bash(finnamon link --update * --telegram)" in perms["allow"] and not any("--update" in d for d in perms["deny"])   # deny beats allow
    assert "Bash(finnamon link *)" not in perms["deny"]        # the old blanket deny would swallow --start


def test_failed_exchange_keeps_the_session_and_the_next_tick_succeeds(home, conn, tg, monkeypatch):
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: (_ for _ in ()).throw(PlaidError({"error_code": "NETWORK_ERROR"})))
    with pytest.raises(PlaidError):
        link.check_pending(conn)
    assert store.get_state(conn, "pending_link")                              # the person's login is not thrown away
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    def fake_register(conn, owner, tok, item):
        conn.execute("INSERT INTO items (item_id, owner, institution) VALUES (?,?,'Chase')", (item, owner)); return "Chase"
    monkeypatch.setattr(link, "register_item", fake_register)
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})
    assert link.check_pending(conn)["state"] == "linked" and store.get_state(conn, "pending_link") is None


def test_poll_scans_every_session_before_calling_it_closed():
    from finnamon import plaid_api as pa
    import finnamon.link as lk
    closed = {"finished_at": "x", "results": {}}
    ok = {"results": {"item_add_results": [{"public_token": "pt"}]}}
    for sessions, want in (([closed, ok], "pt"), ([closed, closed], lk.CLOSED), ([closed, {"results": {}}], None), ([], None)):
        pa.link_token_get = lambda lt, s=sessions: {"link_sessions": s}
        assert lk.poll_public_token("lt") == want, sessions


def test_pending_check_yields_to_a_running_cycle(home, conn, tg, monkeypatch):
    from finnamon import run as runmod
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: (_ for _ in ()).throw(AssertionError("must not poll while a run holds the lock")))
    held = runmod.lock()
    try:
        assert link.check_pending(conn)["state"] == "waiting"
    finally:
        held.close()


def test_link_start_and_finish_die_cleanly_and_triage_cannot_link(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    with pytest.raises(SystemExit):
        cli.main(["link", "--start"])
    assert "read-only" in capsys.readouterr().err
    monkeypatch.delenv("FINNAMON_TRIAGE")
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    with pytest.raises(SystemExit):
        cli.main(["link", "--start", "--owner", "eve"])             # the assistant does not pick owners
    assert "not a household member" in capsys.readouterr().err
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE")
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_API_KEYS", "error_message": "bad"})))
    with pytest.raises(SystemExit):
        cli.main(["link", "--start"])
    assert "INVALID_API_KEYS" in capsys.readouterr().err
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    p = __import__("json").loads(store.get_state(conn, "pending_link")); p["started_at"] = "2020-01-01 00:00:00"
    store.set_state(conn, "pending_link", __import__("json").dumps(p))
    with pytest.raises(SystemExit):
        cli.main(["link", "--finish"])
    assert "expired" in capsys.readouterr().err


def test_daemon_tells_the_chat_when_a_link_session_closes(home, conn, tg, monkeypatch):
    from finnamon import daemon
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"finished_at": "x", "results": {}}]})
    d = daemon.Daemon(conn_factory=lambda: conn)
    monkeypatch.setattr(d, "sync_due", lambda c: False)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.sync_loop()
    assert "closed without adding a bank" in tg.sent[-1]["text"] and store.get_state(conn, "pending_link")   # kept: the link can be reopened


def test_a_rejected_link_token_clears_the_session_and_never_starves_the_sync(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli, daemon, run as runmod
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_LINK_TOKEN"})))
    assert link.check_pending(conn) == {"state": "expired"} and store.get_state(conn, "pending_link") is None
    cli.main(["link", "--start"])                                  # a fresh session, not a wedge
    assert "hosted.plaid.com/x" in capsys.readouterr().out
    # any other Plaid failure in the pending check is logged and the sync still runs
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: (_ for _ in ()).throw(PlaidError({"error_code": "NETWORK_ERROR"})))
    cycles = []
    monkeypatch.setattr(runmod, "cycle", lambda c: cycles.append(1) or {"alerts": [], "sent": 0, "error": None})
    d = daemon.Daemon(conn_factory=lambda: conn)
    monkeypatch.setattr(d, "sync_due", lambda c: True)
    monkeypatch.setattr(d.stop, "wait", lambda t=None: d.stop.set())
    d.sync_loop()
    assert cycles == [1] and store.get_state(conn, "pending_link")


def test_start_reports_a_session_that_completed_in_the_meantime(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    def fake_register(conn, owner, tok, item):
        conn.execute("INSERT INTO items (item_id, owner, institution) VALUES (?,?,'Chase')", (item, owner)); return "Chase"
    monkeypatch.setattr(link, "register_item", fake_register)
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not open a new session")))
    cli.main(["link", "--start"])
    out = capsys.readouterr().out
    assert "Linked Chase" in out and "add another bank" in out and store.get_state(conn, "pending_link") is None


def _linkable(conn, monkeypatch):
    monkeypatch.setattr(plaid_api, "link_token_get", lambda lt: {"link_sessions": [{"results": {"item_add_results": [{"public_token": "pt"}]}}]})
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: {"item_id": "new", "access_token": "tok"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_3", "institution_name": "Chase"}})
    monkeypatch.setattr(sync, "sync_item", lambda *a, **k: {"accounts": 1, "transactions": 0, "recurring": 0, "holdings": 0, "error": None})


def test_a_crash_after_the_exchange_is_resumed_without_a_second_exchange(home, conn, tg, monkeypatch):
    import json
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    _linkable(conn, monkeypatch)
    exchanges = []
    monkeypatch.setattr(plaid_api, "public_token_exchange", lambda pt: exchanges.append(pt) or {"item_id": "new", "access_token": "tok"})
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: (_ for _ in ()).throw(RuntimeError("killed here")))   # dies after the exchange
    with pytest.raises(RuntimeError):
        link.check_pending(conn)
    p = json.loads(store.get_state(conn, "pending_link"))
    assert p["item_id"] == "new" and config.item_tokens()["new"] == "tok"        # the secret and the item_id survived
    monkeypatch.setattr(plaid_api, "item_get", lambda tok: {"item": {"institution_id": "ins_3", "institution_name": "Chase"}})
    r = link.check_pending(conn)
    assert r["state"] == "linked" and exchanges == ["pt"] and store.get_state(conn, "pending_link") is None   # exchanged exactly once
    assert conn.execute("SELECT institution FROM items WHERE item_id='new'").fetchone()[0] == "Chase"


def test_a_malformed_pending_row_is_dropped_not_retried_forever(home, conn, tg, monkeypatch):
    _pending_env(conn, monkeypatch, tg)
    for raw in ("not json", "{}", '{"owner": "bill"}'):
        store.set_state(conn, "pending_link", raw)
        assert link.check_pending(conn) == {"state": "expired"} and store.get_state(conn, "pending_link") is None


def test_announce_failure_is_not_a_link_failure(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    link.start_pending(conn, "bill")
    _linkable(conn, monkeypatch)
    from finnamon.telegram import TelegramError
    tg.fail_with = TelegramError(429, "slow down")
    cli.main(["link", "--finish"])
    assert "Linked Chase" in capsys.readouterr().out and store.get_state(conn, "pending_link") is None
    tg.fail_with = None
    cli.main(["link", "--finish"]); assert "nothing pending" in capsys.readouterr().out   # no second link


def test_plaid_url_is_escaped_for_telegram_html(home, conn, tg, monkeypatch):
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: {"link_token": "lt", "hosted_link_url": "https://x/hl?a=1&b=2"})
    link.start_pending(conn, "bill", to_telegram=True)
    assert "a=1&amp;b=2" in tg.sent[-1]["text"]


def test_assistant_may_name_an_existing_owner_only(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    conn.execute("INSERT INTO owners (owner) VALUES ('jane')")
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    cli.main(["link", "--start", "--owner", "jane"])
    assert __import__("json").loads(store.get_state(conn, "pending_link"))["owner"] == "jane"
    store.set_state(conn, "pending_link", None)
    with pytest.raises(SystemExit):
        cli.main(["link", "--start", "--owner", "eve"])
    assert "not a household member" in capsys.readouterr().err


def test_link_update_from_claude_sends_the_relogin_link_and_returns(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    secrets.update(items={"item1": "access-sandbox-x"})
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    monkeypatch.setattr(link, "wait_for_public_token", lambda *a, **k: pytest.fail("the assistant never blocks on Link"))
    cli.main(["link", "--update", "item1", "--telegram"])
    assert json.loads(capsys.readouterr().out)["institution"] == "Chase"
    assert tg.sent[-1]["text"].startswith("Log back into Chase (bill's login) here (valid for about 30 minutes)") and "https://hosted.plaid.com/x" in tg.sent[-1]["text"]
    assert store.get_state(conn, "pending_link") is None      # nothing to finish: the next sync clears the status


@pytest.mark.parametrize("argv", [["link", "--update", "item1"], ["link", "--remove", "item1"], ["link"], ["link", "--token", ""],
                                  ["link", "--public-token", "pt"]])
def test_other_link_actions_are_still_refused_from_claude(home, conn, tg, monkeypatch, argv):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    monkeypatch.setattr(link, "remove", lambda *a: pytest.fail("reached remove"))
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert not tg.sent


def test_link_update_from_claude_refuses_an_unknown_item(home, conn, tg, monkeypatch):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "nope", "--telegram"])
    assert not tg.sent


def test_start_update_guards_no_chat_manual_item_and_unnamed_bank(home, conn, tg, monkeypatch):
    _pending_env(conn, monkeypatch, tg)
    secrets.update(items={"item1": "access-sandbox-x"})
    conn.execute("UPDATE items SET source='manual' WHERE item_id='item1'")
    with pytest.raises(ValueError, match="no linked bank"):          # a CSV-imported bank has nothing to re-login to
        link.start_update(conn, "item1")
    conn.execute("UPDATE items SET source='plaid', institution=NULL WHERE item_id='item1'")
    assert link.start_update(conn, "item1")["institution"] == "item1"
    assert tg.sent[-1]["text"].startswith("Log back into item1 (bill's login) here")
    store.set_state(conn, "chat_id", None)
    n = len(tg.sent)
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: pytest.fail("no chat: no Link session opened"))
    with pytest.raises(ValueError, match="no household chat"):
        link.start_update(conn, "item1")
    assert len(tg.sent) == n


def test_link_update_from_claude_dies_cleanly_on_plaid_error(home, conn, tg, monkeypatch, capsys):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    secrets.update(items={"item1": "access-sandbox-x"})
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: (_ for _ in ()).throw(PlaidError({"error_code": "INVALID_ACCESS_TOKEN"})))
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "item1", "--telegram"])
    assert "INVALID_ACCESS_TOKEN" in capsys.readouterr().err and not tg.sent


def test_start_update_is_rate_limited_per_item_and_per_day(home, conn, tg, monkeypatch):
    from finnamon import cli
    _pending_env(conn, monkeypatch, tg)
    secrets.update(items={"item1": "access-sandbox-x"})
    import time
    t0 = time.time()                                                  # the CLI below runs on the real clock
    ok = plaid_api.link_token_create
    assert link.start_update(conn, "item1", now=t0)["sent"]
    n = len(tg.sent)
    monkeypatch.setattr(plaid_api, "link_token_create", lambda *a, **k: pytest.fail("over the limit: no Link token"))
    with pytest.raises(ValueError, match="still valid"):              # the same bank again within 15 min
        link.start_update(conn, "item1", now=t0 + 60)
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")                   # and the assistant's route says so
    with pytest.raises(SystemExit):
        cli.main(["link", "--update", "item1", "--telegram"])
    assert len(tg.sent) == n
    monkeypatch.setattr(plaid_api, "link_token_create", ok)
    for k in range(1, link.UPDATE_DAILY_CAP):                         # 15 min apart: fine until the daily cap
        link.start_update(conn, "item1", now=t0 + k * link.UPDATE_ITEM_GAP_S)
    with pytest.raises(ValueError, match="last 24 hours"):
        link.start_update(conn, "item1", now=t0 + link.UPDATE_DAILY_CAP * link.UPDATE_ITEM_GAP_S)
    assert link.start_update(conn, "item1", now=t0 + 86400 + link.UPDATE_ITEM_GAP_S)["sent"]   # a day later the oldest have aged out


def test_rate_limit_counts_only_sent_links_survives_bad_state_and_covers_add(home, conn, tg, monkeypatch):
    from finnamon import telegram
    _pending_env(conn, monkeypatch, tg)
    secrets.update(items={"item1": "access-sandbox-x"})
    t0 = 1_800_000_000
    store.set_state(conn, "update_links", "not json")                  # a corrupt value never locks out a re-login
    tg.fail_with = telegram.TelegramError(502, "down")
    with pytest.raises(telegram.TelegramError):
        link.start_update(conn, "item1", now=t0)
    tg.fail_with = None
    assert link.start_update(conn, "item1", now=t0 + 60)["sent"]      # the failed one never reached the chat: not counted
    for k in range(link.UPDATE_DAILY_CAP - 1):                         # the assistant's add links share the budget
        link.start_pending(conn, "bill", to_telegram=True, limited=True, now=t0 + (k + 1) * link.UPDATE_ITEM_GAP_S)
    n = len(tg.sent)
    with pytest.raises(ValueError, match="last 24 hours"):
        link.start_pending(conn, "bill", to_telegram=True, limited=True, now=t0 + 10 * link.UPDATE_ITEM_GAP_S)
    link.start_pending(conn, "bill", to_telegram=True, now=t0 + 10 * link.UPDATE_ITEM_GAP_S)   # a person at the terminal is not limited
    assert len(tg.sent) == n + 1
