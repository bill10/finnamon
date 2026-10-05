"""Anomaly triage: a Claude Code skill (/triage) does the judging; this module runs it and checks
that every untriaged group got a verdict. Three consecutive failures → suppress the group with
reason 'triage_unavailable' and raise a health:triage alert so the failure is loud.

    finnamon alerts --untriaged --json      what the skill reads
    finnamon triage set <txn|key> ...       what the skill writes
"""
from __future__ import annotations

import json
import logging
import sqlite3

from . import claude_runner, store

log = logging.getLogger("finnamon.triage")
MAX_ATTEMPTS = 3


def untriaged(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT *, {store.ALERT_GROUP} AS grp FROM alerts WHERE tier='anomaly' AND verdict IS NULL ORDER BY grp, id").fetchall()


def groups(conn: sqlite3.Connection) -> list[dict]:
    """Candidates grouped by transaction (stream candidates are their own group). This is the JSON the skill reads."""
    out: dict[str, dict] = {}
    for a in untriaged(conn):
        gid = a["grp"]
        g = out.setdefault(gid, {"group": gid, "transaction_id": a["transaction_id"], "candidates": []})
        g["candidates"].append({"alert_id": a["id"], "kind": a["kind"], "key": a["key"], "payload": json.loads(a["payload_json"])})
    return list(out.values())


def set_verdict(conn: sqlite3.Connection, group: str, verdict: str, confidence: str, reason: str, repair: bool = False) -> int:
    """Stamp every untriaged candidate in the group. `group` is a transaction_id, a group id, or any one candidate's key,
    which stamps its whole transaction: two detectors on one transaction get one verdict, never a promote and a suppress.

    With `repair` (a person, never a Claude session) and nothing left untriaged in the group, the candidates that
    already have this verdict get the new reason instead (a damaged sentence, #63); verdict, confidence and sent state stay."""
    if verdict not in ("promote", "suppress") or confidence not in ("high", "low"):
        raise ValueError("verdict must be promote|suppress and confidence high|low")
    group_rows = f"tier='anomaly' AND {store.ALERT_GROUP} IN (?, (SELECT {store.ALERT_GROUP} FROM alerts WHERE key=?))"
    if repair and not conn.execute(f"SELECT 1 FROM alerts WHERE {group_rows} AND verdict IS NULL", (group, group)).fetchone():
        cur = conn.execute(f"UPDATE alerts SET reason=? WHERE {group_rows} AND verdict=?", (reason.strip()[:500], group, group, verdict))
    else:
        cur = conn.execute(f"UPDATE alerts SET verdict=?, confidence=?, reason=? WHERE {group_rows} AND verdict IS NULL",
                           (verdict, confidence, reason.strip()[:500], group, group))
    return cur.rowcount


def run_if_needed(conn: sqlite3.Connection, timeout: int = 300) -> dict:
    before = groups(conn)
    if not before:
        return {"groups": 0, "ran": False}
    problems = claude_runner.harness_problems()
    if problems:
        res = claude_runner.Result(False, "", None, error="harness: " + "; ".join(problems))
    else:
        # --disallowedTools is prefix-matched and argparse accepts options anywhere, so the CLI also refuses writes under FINNAMON_TRIAGE
        res = claude_runner.run("/triage", timeout=timeout, disallowed=claude_runner.TRIAGE_DISALLOWED, env_extra={"FINNAMON_TRIAGE": "1"})
    after = groups(conn)
    if not res.ok:
        log.warning("triage run failed: %s", res.error)
    remaining = {g["group"] for g in after}
    with store.tx(conn):
        gave_up = _bump_attempts(conn, res.error, [g["group"] for g in before])
    return {"groups": len(before), "ran": True, "ok": res.ok, "remaining": len(remaining), "gave_up": gave_up, "error": res.error}


def _bump_attempts(conn: sqlite3.Connection, last_error: str | None, groups_seen: list[str]) -> int:
    """Count a failed attempt for every group Claude was shown and left untriaged; give up loudly on the third."""
    for gid in groups_seen:
        conn.execute(f"UPDATE alerts SET triage_attempts = triage_attempts + 1 WHERE tier='anomaly' AND verdict IS NULL AND {store.ALERT_GROUP}=?", (gid,))
    exhausted = conn.execute(f"SELECT DISTINCT {store.ALERT_GROUP} FROM alerts WHERE tier='anomaly' AND verdict IS NULL AND triage_attempts >= ?",
                             (MAX_ATTEMPTS,)).fetchall()
    for (gid,) in exhausted:
        conn.execute(f"UPDATE alerts SET verdict='suppress', confidence='low', reason='triage_unavailable' WHERE tier='anomaly' AND verdict IS NULL AND {store.ALERT_GROUP}=?", (gid,))
    if exhausted:
        as_of = store.now_local()
        conn.execute("INSERT OR IGNORE INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','triage_error',?,?,?)",
                     (f"health:triage:{as_of[:10]}", json.dumps({"groups_given_up": len(exhausted), "last_error": last_error, "as_of": as_of}), as_of))
    return len(exhausted)


def suppressed(conn: sqlite3.Connection, since_days: int = 30) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, kind, created_at, reason, payload_json FROM alerts WHERE tier='anomaly' AND verdict='suppress' "
                        "AND created_at >= datetime('now','localtime', ?) ORDER BY id DESC", (f"-{since_days} days",)).fetchall()
