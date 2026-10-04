import json

import pytest

from finnamon import assistant

from finnamon import cli, investments, properties
from tests.conftest import seed


def test_property_set_list_remove_and_net_worth(conn):
    seed(conn)
    before = investments.net_worth(conn)["net_worth"]
    assert properties.set_value(conn, "  Cabin,  Lake Tahoe ", "$44,000") == {"name": "Cabin, Lake Tahoe", "value": 44000.0}
    assert properties.set_value(conn, "Cabin, Lake Tahoe", 40000) == {"name": "Cabin, Lake Tahoe", "value": 40000.0, "was": 44000.0}
    properties.set_value(conn, "Car", 12000)
    assert [(r["name"], r["value"]) for r in properties.listing(conn)] == [("Cabin, Lake Tahoe", 40000.0), ("Car", 12000.0)]
    nw = investments.net_worth(conn)
    assert nw["property"] == 52000 and nw["net_worth"] == before + 52000
    assert nw["cash"] + nw["investments"] == nw["assets"]      # assets stay what the banks report
    assert properties.remove(conn, "Car") and not properties.remove(conn, "Car")
    assert investments.net_worth(conn)["property"] == 40000


@pytest.mark.parametrize("name,value", [("", 1), ("x" * 81, 1), ("House", "lots"), ("House", -5), ("House", "nan"), ("House", None)])
def test_property_rejects_bad_input(conn, name, value):
    seed(conn)
    with pytest.raises(ValueError):
        properties.set_value(conn, name, value)
    assert properties.listing(conn) == []


def test_property_cli(home, conn, capsys, monkeypatch):
    seed(conn)
    cli.main(["property", "set", "House on Elm St", "850000"])
    assert json.loads(capsys.readouterr().out) == {"name": "House on Elm St", "value": 850000.0}
    cli.main(["property"]); assert json.loads(capsys.readouterr().out)[0]["name"] == "House on Elm St"
    cli.main(["networth"]); assert json.loads(capsys.readouterr().out)["property"] == 850000.0
    for argv in (["property", "set"], ["property", "set", "House"], ["property", "remove"], ["property", "set", "House", "soon"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
        capsys.readouterr()
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")                 # triage is read-only
    with pytest.raises(SystemExit):
        cli.main(["property", "remove", "House on Elm St"])
    monkeypatch.delenv("FINNAMON_TRIAGE")
    cli.main(["property", "remove", "House on Elm St"]); assert json.loads(capsys.readouterr().out) == {"removed": True}
    cli.main(["status"]); assert '"daemon_alive": false' in capsys.readouterr().out


def test_claude_may_set_and_remove_properties():
    perms = json.loads((assistant.BUNDLE / ".claude/settings.json").read_text())["permissions"]
    assert {"Bash(finnamon property *)", "Bash(finnamon property)"} <= set(perms["allow"])
    assert not any(d.startswith("Bash(finnamon property") for d in perms["deny"])


def test_property_edges(home, conn, capsys, monkeypatch):
    seed(conn)
    with pytest.raises(ValueError):
        properties.set_value(conn, "House", "inf")
    properties.set_value(conn, "Lake  cabin", 1)
    assert properties.remove(conn, "  Lake cabin ") and properties.listing(conn) == []   # names are whitespace-normalised on both sides
    monkeypatch.setenv("FINNAMON_TRIAGE", "1")
    with pytest.raises(SystemExit):
        cli.main(["property", "set", "House", "1"])
    capsys.readouterr()
    assert properties.listing(conn) == []


def test_property_names_are_one_row_whatever_the_case(conn):
    seed(conn)
    properties.set_value(conn, "House", 100)
    assert properties.set_value(conn, "house", 120) == {"name": "House", "value": 120.0, "was": 100.0}
    assert [(r["name"], r["value"]) for r in properties.listing(conn)] == [("House", 120.0)]
    assert properties.remove(conn, "HOUSE") and properties.listing(conn) == []


def test_migration_003_merges_case_variants_from_an_older_db(tmp_path):
    """A DB that ran 002 as first shipped (case-sensitive names) gets one NOCASE row per name; the newest value wins."""
    import sqlite3
    from finnamon import store
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    for f in ("001_init.sql", "002_properties.sql"):
        c.executescript((store.MIGRATIONS / f).read_text())
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
    c.executemany("INSERT INTO schema_migrations(name) VALUES (?)", [("001_init.sql",), ("002_properties.sql",)])
    c.executemany("INSERT INTO properties (name, value, updated_at) VALUES (?,?,?)", [("House", 100, "2026-01-01 00:00:00"), ("house", 120, "2026-02-01 00:00:00"), ("Car", 9, "2026-01-01 00:00:00")])
    c.commit(); c.close()
    conn = store.connect(db)
    assert sorted((r["name"], r["value"]) for r in properties.listing(conn)) == [("Car", 9.0), ("house", 120.0)]
    assert properties.set_value(conn, "HOUSE", 130)["was"] == 120.0 and len(properties.listing(conn)) == 2
