"""One transaction, one alert: an HSA deposit tripped no_source and unmatched_transfer, and the chat got the same line twice
(and an earlier one was promoted by one detector, suppressed by the other). Delivery, triage, the alerts list and
resolution all treat the alerts on one transaction as one."""
import json

from finnamon import budgets, cli, daemon, detect, notify, store, triage
from tests.conftest import AS_OF, seed, txn

RULES = {p.stem: p for p in detect.detectors()}


def hsa(conn):
    seed(conn)
    txn(conn, "hsa", "chk", "2026-09-17", -2090, "HSAWCSPCUSTODIAN DES:HSADISTRIB ID:1 INDN:B CO ID:X PPD", None, None, "TRANSFER_IN", "TRANSFER_IN_DEPOSIT")   # a deposit, not an own-account transfer (no_source skips those)


def fire(conn, *names):
    return [i for n in names for i in detect.run_one(conn, RULES[n], AS_OF)]


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT id, kind, transaction_id, verdict, sent_at, telegram_message_id, resolved_at, resolution FROM alerts ORDER BY id")]


def test_two_detectors_one_message(conn, tg):
    hsa(conn)
    ids = fire(conn, "no_source", "unmatched_transfer")
    assert len(ids) == 2 and {r["transaction_id"] for r in rows(conn)} == {"hsa"}   # every per-transaction detector records the id
    assert [g["group"] for g in triage.groups(conn)] == ["hsa"]                     # triaged together
    assert triage.set_verdict(conn, "anom:xfer:hsa", "promote", "high", "Money in with no source I know.") == 2   # one key stamps the transaction
    assert notify.send_pending(conn) == 1 and len(tg.sent) == 1 and "HSAWCSPCUSTODIAN" in tg.sent[0]["text"]
    assert {(r["sent_at"] is not None, r["telegram_message_id"]) for r in rows(conn)} == {(True, 101)}   # both stamped with the one message
    assert notify.send_pending(conn) == 0
    note, aid = daemon.Daemon.note_for(None, conn, {"chat_id": 1234567890, "reply_to": 101})
    assert aid == ids[0] and note == f"replying to alert {ids[0]}"   # a reply to that message is a reply to one alert


def test_late_detector_takes_the_verdict_and_the_message(conn, tg):
    hsa(conn)
    fire(conn, "no_source")
    triage.set_verdict(conn, "anom:nosrc:hsa", "promote", "high", "Money in with no source I know.")
    notify.send_pending(conn)
    fire(conn, "unmatched_transfer")   # fires on a later run, after the transaction was judged and told
    assert {r["verdict"] for r in rows(conn)} == {"promote"} and triage.groups(conn) == []
    assert notify.send_pending(conn) == 1 and len(tg.sent) == 1   # folded into the message already sent, not a second one
    assert {r["telegram_message_id"] for r in rows(conn)} == {101}


def test_late_detector_after_suppress_is_suppressed(conn, tg):
    hsa(conn)
    fire(conn, "no_source")
    triage.set_verdict(conn, "anom:nosrc:hsa", "suppress", "low", "The HSA paying back a claim.")
    fire(conn, "unmatched_transfer")
    assert {r["verdict"] for r in rows(conn)} == {"suppress"} and notify.send_pending(conn) == 0 and tg.sent == []


def test_best_reason_and_fallback_group(conn, tg):
    seed(conn)
    p = json.dumps({"name": "HSA", "amount": -209.0, "date": "2026-09-30", "account": "Adv Plus Banking", "mask": "9665"})
    for kind, conf, reason in (("anomaly:no_source", "low", "Short."), ("anomaly:unmatched_transfer", "high", "Money in from the HSA custodian.")):
        conn.execute("INSERT INTO alerts (tier, kind, key, account_id, payload_json, as_of, verdict, confidence, reason) VALUES ('anomaly',?,?,'chk',?,?,'promote',?,?)",
                     (kind, kind, p, AS_OF, conf, reason))   # no transaction_id: grouped on account + date + amount + name
    assert notify.send_pending(conn) == 1 and "HSA custodian" in tg.sent[0]["text"]
    assert conn.execute("SELECT count(*) FROM alerts WHERE sent_at IS NOT NULL").fetchone()[0] == 2


def test_list_folds_and_resolution_covers_the_transaction(conn, tg, capsys):
    hsa(conn)
    a, b = fire(conn, "no_source", "unmatched_transfer")
    triage.set_verdict(conn, "hsa", "promote", "high", "Money in with no source I know.")
    notify.send_pending(conn)
    cli.main(["alerts", "--sent", "--open"])
    lst = json.loads(capsys.readouterr().out)
    assert len(lst) == 1 and lst[0]["folded"] and {lst[0]["id"], *lst[0]["folded"]} == {a, b}   # the dashboard shows one
    budgets.dismiss(conn, b)
    assert {r["resolution"] for r in rows(conn)} == {"dismissed"}
    budgets.undo(conn, a)
    assert {r["resolved_at"] for r in rows(conn)} == {None}
    rule = budgets.normal(conn, alert_id=a)["id"]
    assert {r["resolution"] for r in rows(conn)} == {"normal"}
    assert {r[0] for r in conn.execute("SELECT suppression_id FROM alerts")} == {rule}
    assert budgets.undo(conn, b)["removed_rule"]["id"] == rule and conn.execute("SELECT count(*) FROM suppressions").fetchone()[0] == 0
    assert {r["resolved_at"] for r in rows(conn)} == {None}


def test_every_transaction_candidate_records_its_transaction():
    for p in (RULES[n] for n in RULES if detect.kind_of(RULES[n])[0] == "anomaly" and n != "recurring_changed"):   # a stream has no transaction
        assert "t.transaction_id AS key, t.transaction_id," in p.read_text().replace("\n", " "), p.name


def test_a_rule_on_the_same_transaction_is_still_told(conn, tg):
    hsa(conn)
    a, b = fire(conn, "no_source", "unmatched_transfer")
    triage.set_verdict(conn, "hsa", "promote", "high", "Money in with no source I know.")
    notify.send_pending(conn)
    budgets.dismiss(conn, a)
    rid = conn.execute("INSERT INTO alerts (tier, kind, key, account_id, transaction_id, payload_json, as_of) VALUES ('rule','duplicate_charge','dup:x:hsa','chk','hsa',?,?)",
                       (json.dumps({"merchant": "HSA", "amount": -2090, "date_a": "2026-09-16", "date_b": "2026-09-17"}), AS_OF)).lastrowid
    detect.join_family(conn, rid)   # a duplicate charge is not muted by the anomaly dismissed on it, nor folded into that old message
    assert conn.execute("SELECT resolved_at FROM alerts WHERE id=?", (rid,)).fetchone()[0] is None
    assert notify.send_pending(conn) == 1 and len(tg.sent) == 2 and "duplicate" in tg.sent[1]["text"]
