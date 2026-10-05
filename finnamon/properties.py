"""Stated assets: a house, a car, anything no bank reports. One row per name; the value is what the household says
it is worth, counted into net worth until they change it."""
from __future__ import annotations

import math
import sqlite3

from . import store

MAX_NAME = 80


def set_value(conn: sqlite3.Connection, name: str, value) -> dict:
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME:
        raise ValueError(f"a property needs a name of 1 to {MAX_NAME} characters")
    try:
        value = store.parse_amount(value, "a property value")
    except ValueError:
        raise ValueError("a property value is a number of dollars, like 21500 or $21,500") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError("a property value is zero or more dollars")
    before = conn.execute("SELECT name, value FROM properties WHERE name=?", (name,)).fetchone()   # NOCASE: "House" is "house"
    if before is not None:
        name = before[0]
    conn.execute("INSERT INTO properties (name, value, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (name, value, store.now_local()))
    out = {"name": name, "value": value}
    if before is not None:
        out["was"] = before[1]
    return out


def remove(conn: sqlite3.Connection, name: str) -> bool:
    name = " ".join(str(name or "").split())
    return conn.execute("DELETE FROM properties WHERE name=?", (name,)).rowcount > 0


def listing(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT name, value, updated_at FROM properties ORDER BY value DESC, name")]


def total(conn: sqlite3.Connection) -> float:
    return conn.execute("SELECT COALESCE(sum(value), 0) FROM properties").fetchone()[0]
