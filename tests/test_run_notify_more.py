"""run.cycle branches (triage no-op, notify failing after a crash, SQLite-broken fallback) and the notify
renderer/sender branches the main suite does not reach."""
import json
import sqlite3

from finnamon import notify, run as runmod, store, sync
from tests.conftest import AS_OF, seed
from tests.test_notify_run import alert


class FlakyConn:
    """Delegates to a real connection but fails every INSERT: 'SQLite itself is the problem' for writes only."""

    def __init__(self, real):
        self.real = real

    def execute(self, sql, *args):
        if sql.lstrip().upper().startswith("INSERT"):
            raise sqlite3.OperationalError("disk I/O error")
        return self.real.execute(sql, *args)


def test_cycle_triage_noop_notify_failure_and_crash_fallback(conn, tg, monkeypatch):
    seed(conn)
    monkeypatch.setattr(sync, "sync_all", lambda c: [])
    monkeypatch.setenv("FINNAMON_CLAUDE_BIN", "/nonexistent/claude")
    r = runmod.cycle(conn)  # do_triage=True with nothing untriaged: claude is never invoked
    assert r["error"] is None and store.get_state(conn, "last_run") == r["as_of"]
    # a crash whose alert also cannot be sent: still returns, still recorded, no exception escapes
    monkeypatch.setattr(sync, "sync_all", lambda c: (_ for _ in ()).throw(RuntimeError("plaid down")))
    monkeypatch.setattr(notify, "send_pending", lambda c: (_ for _ in ()).throw(RuntimeError("tg down")))
    r = runmod.cycle(conn, do_triage=False)
    assert r["error"] == "RuntimeError: plaid down" and conn.execute("SELECT count(*) FROM alerts WHERE kind='run_error'").fetchone()[0] == 1
    # do_notify=False: the crash is recorded but nothing is attempted on Telegram
    tg.sent.clear()
    r = runmod.cycle(conn, do_triage=False, do_notify=False)
    assert r["error"] and tg.sent == []
    # the INSERT into alerts itself fails: last resort is one direct Telegram message
    runmod.record_crash(FlakyConn(conn), RuntimeError("disk full"), AS_OF)
    assert len(tg.sent) == 1 and "couldn't record it" in tg.sent[0]["text"] and "disk full" in tg.sent[0]["text"]
    # ... and if even that send fails, record_crash swallows it
    tg.fail_with = RuntimeError("tg down too")
    runmod.record_crash(FlakyConn(conn), RuntimeError("x"), AS_OF)
    # no chat_id: nothing to send, no error
    store.set_state(conn, "chat_id", None)
    tg.fail_with = None
    runmod.record_crash(FlakyConn(conn), RuntimeError("x"), AS_OF)
    assert len(tg.sent) == 1


def test_render_and_send_edge_branches(conn, tg):
    seed(conn)
    cases = {
        "sync_health-err": ("sync_health", {"item_id": "item1", "institution": "Chase", "status": "API_ERROR", "last_error": "upstream"}, "couldn't sync"),
        "sync_health-stale": ("sync_health", {"item_id": "item1", "status": "good", "last_synced_at": "2026-09-18 01:00:00"}, "hasn't synced since Sep 18"),
        "sync_health-noinst": ("sync_health", {"item_id": "item1", "status": "good"}, "item1 hasn't synced since it was linked"),
        "budget-over": ("budget_pace", {"budget": "dining", "spent": 400, "limit": 350, "day": 24, "days": 30, "state": "over"}, "over budget"),
        "anomaly-in": ("anomaly:unmatched_transfer", {"name": "ZELLE FROM X", "amount": -900}, "$900.00 from ZELLE FROM X"),
        "anomaly-noamount": ("anomaly:no_source", {"name": "??"}, "<b> to ??</b>"),
        "anomaly-nullamount": ("anomaly:recurring_changed", {"merchant": "Apple", "amount": None, "last_amount": 12.14, "avg_amount": 11}, "$12.14 to Apple"),   # a null amount is absent
        "anomaly-avgonly": ("anomaly:recurring_changed", {"merchant": "Apple", "avg_amount": 11}, "$11.00 to Apple"),
        "anomaly-textamount": ("anomaly:no_source", {"name": "??", "amount": "abc"}, "<b>abc to ??</b>"),   # a text amount renders instead of raising
        "unknown": ("weird_kind", {"a": 1}, "weird_kind"),
    }
    for key, (kind, p, expect) in cases.items():
        aid = alert(conn, kind, key, payload=p)
        text = notify.render(conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone())
        assert expect in text, (key, text)
    assert "6 days left" in notify.render(conn.execute("SELECT * FROM alerts WHERE key='budget-over'").fetchone())
    assert notify.money("n/a") == "n/a" and notify.money(-5) == "-$5.00" and notify.money("1234.5") == "$1,234.50"
    # rate limit pacing: a 60s pause every 15 messages
    conn.execute("DELETE FROM alerts")
    for i in range(16):
        alert(conn, key=f"rl{i}")
    slept = []
    assert notify.send_pending(conn, sleep=slept.append) == 16 and slept == [60]
    # a roundup with nothing to say says all quiet; forced on a Saturday it leaves Sunday's stamp alone
    assert notify.send_roundup(conn, AS_OF, force=True) and "All quiet" in tg.sent[-1]["text"] and store.get_state(conn, "last_roundup_week") is None
    alert(conn, "anomaly:first_merchant", "low1", "anomaly", {"merchant": "x", "amount": 5}, "promote", "low", "meh")
    store.set_state(conn, "chat_id", None)
    n = len(tg.sent)
    assert notify.send_roundup(conn, AS_OF, force=True) is None and len(tg.sent) == n
    assert notify.send_pending(conn) == 0  # no chat_id: nothing sent, alerts stay pending
    assert conn.execute("SELECT sent_at FROM alerts WHERE key='low1'").fetchone()[0] is None
    # explicit chat_id argument wins over state
    assert notify.send_roundup(conn, AS_OF, chat_id=42, force=True) and tg.sent[-1]["chat_id"] == 42
    # a bare Item summary: no transactions, no recurring, no typical month
    conn.execute("INSERT INTO items (item_id, owner) VALUES ('bare','bill')")
    s = notify.item_linked_summary(conn, "bare")
    assert s.startswith("<b>Linked bare</b> (0 accounts):") and "no transactions yet" in s and "Recurring" not in s and "holdings" not in s


def test_record_crash_with_dead_db_still_messages(conn, tg, monkeypatch):
    """A connection whose every execute raises must not escape record_crash; chat_id read earlier is used."""
    import sqlite3
    from finnamon import run as runmod
    from tests.conftest import seed
    seed(conn)

    class Dead:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    runmod._last_direct_send.clear()
    runmod.record_crash(Dead(), RuntimeError("boom"), "2026-09-19 12:00:00", chat_id=1234567890)
    runmod.record_crash(Dead(), RuntimeError("boom"), "2026-09-19 13:00:00", chat_id=1234567890)  # same day: no second message
    assert len(tg.sent) == 1 and "couldn't record it" in tg.sent[0]["text"]
