"""Run every detector, insert new alerts, dedup on alerts.key.

    detect.run(conn, as_of)  → list of new alert ids

A detector is a .sql file under detectors/rules/ (tier 'rule') or detectors/candidates/ (tier
'anomaly'). Each is prepended with detectors/_prelude.sql (so it can SELECT FROM tx, g, sv, sup) and
must return rows (account_id, kind, key, transaction_id, payload). A detector starting with WITH gets
its WITH turned into a comma so it continues the prelude's CTE list.

A detector that raises is skipped, logged, and produces a rule alert health:detector:<name>:<date>
so a bad draft is loud, never silent.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from pathlib import Path

from . import config, store

log = logging.getLogger("finnamon.detect")

DETECTORS = Path(__file__).parent / "detectors"
PRELUDE = DETECTORS / "_prelude.sql"


def drafts_dir() -> Path:
    """Unreviewed drafts live in the household's home, not in the code checkout: a pull never conflicts with them and the
    demo (its own home) does not share them. Created, 0700, on first use."""
    d = config.home() / "detector-drafts"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def migrate_drafts() -> list[str]:
    """Move drafts an earlier version left in <checkout>/detectors/pending/ into drafts_dir(); a name already there stays put."""
    old, moved = DETECTORS / "pending", []
    dest = drafts_dir()
    for f in sorted(old.glob("*.sql")):
        if not (dest / f.name).exists():
            shutil.move(str(f), dest / f.name)
            moved.append(f.name)
    return moved


def assemble(sql_file: Path, kind: str) -> str:
    body = sql_file.read_text()
    body = re.sub(r"^\s*(--[^\n]*\n\s*)*WITH\b", ",", body, count=1, flags=re.IGNORECASE)  # continue the prelude's CTE list
    return PRELUDE.read_text().replace("__KIND__", kind) + "\n" + body


def kind_of(sql_file: Path) -> tuple[str, str]:
    tier = "anomaly" if sql_file.parent.name == "candidates" else "rule"
    kind = f"anomaly:{sql_file.stem}" if tier == "anomaly" else sql_file.stem
    return tier, kind


def detectors() -> list[Path]:
    return sorted((DETECTORS / "rules").glob("*.sql")) + sorted((DETECTORS / "candidates").glob("*.sql"))


def select(conn: sqlite3.Connection, sql_file: Path, as_of: str) -> list:
    """The detector's rows, read outside any write lock: a slow detector must not lock out the dashboard's writes."""
    return conn.execute(assemble(sql_file, kind_of(sql_file)[1]), {"as_of": as_of}).fetchall()


def insert(conn: sqlite3.Connection, sql_file: Path, rows: list, as_of: str) -> list[int]:
    tier, kind = kind_of(sql_file)
    new_ids = []
    for r in rows:
        account_id, row_kind, key, transaction_id, payload = r[0], r[1], r[2], r[3], r[4]
        cur = conn.execute(
            "INSERT OR IGNORE INTO alerts (tier, kind, key, account_id, transaction_id, payload_json, as_of) VALUES (?,?,?,?,?,?,?)",
            (tier, row_kind or kind, key, account_id, transaction_id, payload if isinstance(payload, str) else json.dumps(payload), as_of),
        )
        if cur.rowcount:
            new_ids.append(cur.lastrowid)
            join_family(conn, cur.lastrowid)
    return new_ids


def join_family(conn: sqlite3.Connection, alert_id: int) -> None:
    """One transaction, one verdict: a detector that fires on a transaction triage has already judged, or a person has
    already resolved, takes that verdict and that resolution instead of being judged and shown again on its own."""
    ids = [i for i in store.alert_group(conn, alert_id) if i != alert_id]
    if not ids or conn.execute("SELECT tier FROM alerts WHERE id=?", (alert_id,)).fetchone()[0] != "anomaly":
        return   # a rule (a duplicate charge) is its own signal: an anomaly dismissed on the same charge must not mute it
    q = ",".join("?" * len(ids))
    v = conn.execute(f"SELECT verdict, confidence, reason FROM alerts WHERE id IN ({q}) AND tier='anomaly' AND verdict IS NOT NULL ORDER BY id LIMIT 1", ids).fetchone()
    if v:
        conn.execute("UPDATE alerts SET verdict=?, confidence=?, reason=? WHERE id=? AND verdict IS NULL", (*v, alert_id))
    r = conn.execute(f"SELECT resolved_at, resolution, suppression_id FROM alerts WHERE id IN ({q}) AND resolved_at IS NOT NULL ORDER BY id LIMIT 1", ids).fetchone()
    if r:
        conn.execute("UPDATE alerts SET resolved_at=?, resolution=?, suppression_id=? WHERE id=?", (*r, alert_id))


def run_one(conn: sqlite3.Connection, sql_file: Path, as_of: str) -> list[int]:
    return insert(conn, sql_file, select(conn, sql_file, as_of), as_of)


def run(conn: sqlite3.Connection, as_of: str, only: list[str] | None = None) -> list[int]:
    new_ids: list[int] = []
    for f in detectors():
        if only and f.stem not in only:
            continue
        try:
            rows = select(conn, f, as_of)
            conn.execute("BEGIN IMMEDIATE")   # only the inserts hold the write lock; dedup is alerts.key, so a row written meanwhile is ignored
            ids = insert(conn, f, rows, as_of)
            conn.execute("COMMIT")
            new_ids.extend(ids)
        except Exception as e:  # noqa: BLE001 - any failing detector must be loud, not fatal
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            log.exception("detector %s failed", f.name)
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts (tier, kind, key, payload_json, as_of) VALUES ('rule','detector_error',?,?,?)",
                (f"health:detector:{f.stem}:{as_of[:10]}", json.dumps({"detector": f.name, "error": str(e)[:500], "as_of": as_of}), as_of),
            )
            if cur.rowcount:
                new_ids.append(cur.lastrowid)
    return new_ids


def explain(conn: sqlite3.Connection, sql_file: Path, as_of: str) -> list[str]:
    _, kind = kind_of(sql_file)
    return [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + assemble(sql_file, kind), {"as_of": as_of})]
