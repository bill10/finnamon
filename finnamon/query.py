"""Read-only SQL for Claude (`finnamon query "..."`) and the MCP server. The connection is opened
read-only so a SELECT is the only thing that can happen, whatever the text says."""
from __future__ import annotations

import json
import sqlite3

from . import config, store


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def run(sql: str, limit: int = 200, params: tuple | dict = ()) -> list[dict]:
    s = sql.strip().rstrip(";")
    if not s.lower().startswith(("select", "with", "explain")):
        raise ValueError("query accepts SELECT (or WITH ... SELECT) only")
    store.connect().close()   # a read-only open never migrates, and this SQL may name a view a migration added (tx_now, 005)
    conn = connect_ro()
    try:
        rows = conn.execute(s, params).fetchmany(limit)
        return [dict(r) for r in rows]
    finally:
        conn.close()


def to_json(rows: list[dict]) -> str:
    return json.dumps(rows, default=str, indent=1)

