"""Daemon loops driven one tick at a time (stop.wait is stubbed to set the stop flag), plus converse edge cases."""
import json

from finnamon import claude_runner, daemon, run as runmod, store, telegram
from finnamon.telegram import TelegramError
from tests.conftest import seed
from tests.test_daemon_cli import make_daemon, msg


def one_tick(d):
    """Any wait inside the loop ends the loop: exactly one iteration runs."""
    d.stop.wait = lambda t=None: d.stop.set()
    return d


def test_poll_and_sync_loops(conn, tg, monkeypatch):
    seed(conn)
    d = one_tick(make_daemon(conn))
    calls = []

    def get_updates(offset=None, timeout=25, token=None):
        calls.append(offset)
        d.stop.set()
        return [msg("hi", mid=500), msg("again", mid=501)]
    monkeypatch.setattr(telegram, "get_updates", get_updates)
    d.poll_loop()
    assert calls == [None] and store.get_state(conn, "telegram_offset") == "502" and d.inbox.qsize() == 2
    assert store.get_state(conn, "bot_username") == "test_bot" and store.get_state(conn, "bot_privacy_off") == "1"  # verify_privacy ran first
    # next start resumes from the stored offset; a 429 waits retry_after and does not crash the loop
    d = one_tick(make_daemon(conn))
    waited = []
    d.stop.wait = lambda t=None: (waited.append(t), d.stop.set())

    def flaky(offset=None, timeout=25, token=None):
        calls.append(offset)
        raise TelegramError(429, "slow down", retry_after=3)
    monkeypatch.setattr(telegram, "get_updates", flaky)
    d.poll_loop()
    assert calls[-1] == 502 and waited == [3]
    # any other exception: logged, waits 10s, loop continues
    d = one_tick(make_daemon(conn))
    waited.clear()
    d.stop.wait = lambda t=None: (waited.append(t), d.stop.set())
    monkeypatch.setattr(telegram, "get_updates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dns")))
    d.poll_loop()
    assert waited == [10]
    # getMe failing at startup is not fatal
    monkeypatch.setattr(telegram, "get_me", lambda token=None: (_ for _ in ()).throw(RuntimeError("down")))
    assert make_daemon(conn).verify_privacy(conn) is False

    # --- sync loop -----------------------------------------------------------------------
    ran = []
    monkeypatch.setattr(runmod, "cycle", lambda c: (ran.append(1), {"alerts": [], "sent": 0, "error": None})[1])
    one_tick(make_daemon(conn)).sync_loop()
    assert ran == [1]  # never run → due
    store.set_state(conn, "last_run", store.now_local())
    one_tick(make_daemon(conn)).sync_loop()
    assert ran == [1]  # just ran → not due
    store.set_setting(conn, "sync_interval_hours", 1, ops=True)
    conn.execute("UPDATE state SET value=datetime('now','localtime','-2 hours') WHERE key IN ('last_run','last_attempt')")
    one_tick(make_daemon(conn)).sync_loop()
    assert ran == [1, 1]  # interval 1h, last run 2h ago → due
    store.set_state(conn, "last_run", None)
    store.set_state(conn, "last_attempt", store.now_local())   # a cycle just failed: wait RETRY_AFTER_ERROR_MIN, not 60s
    one_tick(make_daemon(conn)).sync_loop()
    assert ran == [1, 1]
    monkeypatch.setattr(runmod, "cycle", lambda c: (_ for _ in ()).throw(runmod.Locked("held")))
    one_tick(make_daemon(conn)).sync_loop()  # lock held: skip the tick, no exception
    monkeypatch.setattr(runmod, "cycle", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    one_tick(make_daemon(conn)).sync_loop()  # anything else: logged, loop survives


def test_converse_loop_and_edge_cases(conn, tg, fake_claude, monkeypatch, tmp_path):
    seed(conn)
    # converse_loop drains the inbox serially and survives a converse() exception
    d = make_daemon(conn)
    seen = []

    def fake_converse(c, m):
        seen.append(m["text"])
        if m["text"] == "boom":
            raise RuntimeError("x")
        d.stop.set()
    monkeypatch.setattr(d, "converse", fake_converse)
    d.inbox.put({"owner": "bill", "text": "boom", "reply_to": None, "message_id": 1})
    d.inbox.put({"owner": "bill", "text": "ok", "reply_to": None, "message_id": 2})
    d.converse_loop()
    assert seen == ["boom", "ok"]
    # a chart path whose photo upload fails: the text still goes out, path left in place
    d = make_daemon(conn)
    png = tmp_path / "charts" / "b.png"
    png.parent.mkdir()
    png.write_bytes(b"png")
    monkeypatch.setattr(telegram, "send_photo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("too big")))
    calls = []
    monkeypatch.setattr(d, "ask_intercom", lambda m, note, timeout: (calls.append((note, timeout)), (f"see {png}", None))[1])
    d.converse(conn, {"owner": "bill", "text": "chart", "reply_to": None, "message_id": 7})
    assert tg.photos == [] and str(png) in tg.sent[-1]["text"] and tg.sent[-1]["reply_to"] == 7
    # empty text sends nothing
    monkeypatch.setattr(d, "ask_intercom", lambda m, note, timeout: (calls.append((note, timeout)), ("   ", None))[1])
    n = len(tg.sent)
    d.converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 8})
    assert len(tg.sent) == n
    # replying to a message that is not one of our alerts: no note
    d.converse(conn, {"owner": "jane", "text": "?", "reply_to": 999, "message_id": 9})
    assert calls[-1] == ("", 120)
    # claude_timeout_seconds setting is honoured
    store.set_setting(conn, "claude_timeout_seconds", "45", ops=True)
    d.converse(conn, {"owner": "bill", "text": "x", "reply_to": None, "message_id": 11})
    assert calls[-1] == ("", 45)
