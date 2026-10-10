"""Telegram through the dashboard: the daemon reads Telegram and hands each message to the dashboard, which types it into its intercom
session (web/test/talk.test.js covers that half). A fake dashboard on a spare port and the fake Telegram; never a real one."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from finnamon import claude_runner, cli, config, store, telegram
from tests.conftest import seed
from tests.test_daemon_cli import make_daemon, msg


@pytest.fixture
def dashboard(home, monkeypatch):
    """A stand-in for web/server.js's /api/telegram/turn: records each request, answers with `answer(body) -> (status, json)`."""
    seen = []
    state = {"answer": lambda b: (200, {"reply": f"you said {b['text']}"})}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "host": self.headers.get("Host"), **body})
            status, out = state["answer"](body)
            data = json.dumps(out).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("PORT", str(srv.server_address[1]))
    monkeypatch.setattr(claude_runner, "run", lambda *a, **k: pytest.fail("session mode never runs claude -p"))
    yield seen, state
    srv.shutdown(); srv.server_close()


def session_mode(conn):
    seed(conn)
    store.set_state(conn, "inbound", "session")


def test_one_message_round_trip(conn, tg, dashboard):
    seen, _ = dashboard
    session_mode(conn)
    conn.execute("INSERT INTO alerts (tier, kind, key, payload_json, as_of, telegram_chat_id, telegram_message_id) VALUES ('anomaly','anomaly:x','k','{}','2026-08-01',  '1234567890',101)")
    d = make_daemon(conn)
    d.handle_update(conn, msg("it is normal", reply_to=101))
    d.converse(conn, d.inbox.get_nowait())
    assert seen == [{"path": "/api/telegram/turn", "auth": f"Bearer {config.web_token()}", "host": seen[0]["host"],
                     "from": "bill", "text": "it is normal", "note": "replying to alert 1", "timeout": 120}]
    assert seen[0]["host"].startswith("127.0.0.1:"), "loopback: the dashboard's Host check lets it in"
    assert tg.sent[-1]["text"] == "you said it is normal" and tg.sent[-1]["reply_to"] == 500
    assert tuple(conn.execute("SELECT alert_id, owner, parsed_action FROM feedback").fetchone()) == (1, "bill", "claude")


def test_two_messages_go_one_at_a_time_in_order(conn, tg, dashboard):
    seen, _ = dashboard
    session_mode(conn)
    d = make_daemon(conn)
    d.handle_update(conn, msg("first", mid=1))
    d.handle_update(conn, msg("second", mid=2))
    real = d.converse
    def converse(c, m):
        real(c, m)
        if d.inbox.empty():
            d.stop.set()
    d.converse = converse
    d.converse_loop()
    assert [s["text"] for s in seen] == ["first", "second"]
    assert [(m["text"], m["reply_to"]) for m in tg.sent] == [("you said first", 1), ("you said second", 2)]


def test_no_reply_and_charts_are_handled_as_in_daemon_mode(conn, tg, dashboard, home):
    _, state = dashboard
    session_mode(conn)
    d = make_daemon(conn)
    state["answer"] = lambda b: (200, {"reply": "NO_REPLY"})
    d.converse(conn, {"owner": "bill", "text": "thanks honey", "reply_to": None, "message_id": 3, "chat_id": "1234567890"})
    assert tg.sent == []
    png = config.charts_dir() / "dining.png"; png.parent.mkdir(parents=True, exist_ok=True); png.write_bytes(b"png")
    state["answer"] = lambda b: (200, {"reply": f"Dining is up.\n{png}"})
    d.converse(conn, {"owner": "bill", "text": "chart dining", "reply_to": None, "message_id": 4, "chat_id": "1234567890"})
    assert tg.photos and tg.sent[-1]["text"] == "Dining is up."


def test_an_empty_answer_is_told_not_swallowed(conn, tg, dashboard):
    _, state = dashboard
    session_mode(conn)
    state["answer"] = lambda b: (200, {"reply": "  "})
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 9, "chat_id": "1234567890"})
    assert "without an answer" in tg.sent[-1]["text"]


def test_the_timeout_asked_for_is_what_the_dashboard_waits(conn, tg, dashboard):
    seen, state = dashboard
    session_mode(conn)
    state["answer"] = lambda b: (504, {"error": "timeout"})
    store.set_setting(conn, "claude_timeout_seconds", "900", ops=True)
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "x", "reply_to": None, "message_id": 10, "chat_id": "1234567890"})
    assert seen[-1]["timeout"] == 300 and "longer than 300s" in tg.sent[-1]["text"]


def test_a_turn_that_times_out_says_so(conn, tg, dashboard):
    seen, state = dashboard
    session_mode(conn)
    store.set_setting(conn, "claude_timeout_seconds", "45", ops=True)
    state["answer"] = lambda b: (504, {"status": 504, "error": "timeout"})
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "slow one", "reply_to": None, "message_id": 5, "chat_id": "1234567890"})
    assert seen[0]["timeout"] == 45
    assert "longer than 45s" in tg.sent[-1]["text"] and "intercom" in tg.sent[-1]["text"]
    assert conn.execute("SELECT parsed_action FROM feedback").fetchone()[0] == "intercom_error"


def test_session_not_up_or_dashboard_refusing_is_told_to_the_chat(conn, tg, dashboard):
    _, state = dashboard
    session_mode(conn)
    d = make_daemon(conn)
    state["answer"] = lambda b: (503, {"status": 503, "error": "The assistant session is not running yet."})
    d.converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 6, "chat_id": "1234567890"})
    assert "starting up" in tg.sent[-1]["text"]
    state["answer"] = lambda b: (409, {"error": "this dashboard started with inbound=daemon; restart it (finnamon update --no-pull)"})
    d.converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 7, "chat_id": "1234567890"})
    assert "inbound=daemon" in tg.sent[-1]["text"] and "finnamon doctor" in tg.sent[-1]["text"]


def test_dashboard_down(conn, tg, home, monkeypatch):
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # nothing listens there now
    monkeypatch.setenv("PORT", str(port))
    monkeypatch.setattr(claude_runner, "run", lambda *a, **k: pytest.fail("no fallback to claude -p: that would split the conversation"))
    session_mode(conn)
    make_daemon(conn).converse(conn, {"owner": "bill", "text": "hi", "reply_to": None, "message_id": 8, "chat_id": "1234567890"})
    assert "dashboard isn't answering" in tg.sent[-1]["text"]


def test_wrong_chat_and_strangers_never_reach_the_session(conn, tg, dashboard):
    seen, _ = dashboard
    session_mode(conn)
    d = make_daemon(conn)
    d.handle_update(conn, msg("hi", chat=1))          # not the household chat
    d.handle_update(conn, msg("hi", uid=999))         # not a member
    d.handle_update(conn, msg("hi", is_bot=True))     # a bot
    assert d.inbox.empty() and seen == []
