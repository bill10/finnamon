import sqlite3

from finnamon import store
from finnamon.secrets import write, update
from finnamon import config


def test_fresh_db_has_schema_and_defaults(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("items", "accounts", "transactions", "recurring", "merchant_alias", "category_override", "tx_category_override", "suppressions",
              "settings", "budgets", "alerts", "roundup_items", "feedback", "holdings", "state", "owners", "balances"):
        assert t in tables
    assert store.setting(conn, "dup_min_amount") == "10"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reopen_is_idempotent(home):
    a = store.connect()
    b = store.connect()
    assert [r[0] for r in b.execute("SELECT name FROM schema_migrations")] == ["001_init.sql", "002_properties.sql", "003_properties_nocase.sql", "004_manual_items.sql", "005_tx_now_view.sql", "006_recurring_is_active.sql", "007_suppression_stream.sql", "008_tx_flow.sql", "009_alert_resolution.sql", "010_account_type.sql", "011_tx_category_override.sql", "012_budget_selectors.sql", "013_unmerge_owner.sql", "014_budget_limit_history.sql"]
    assert store.migrate(a) == []


def test_migration_is_transactional(home, tmp_path, monkeypatch):
    bad = tmp_path / "migs"
    bad.mkdir()
    (bad / "001_init.sql").write_text((store.MIGRATIONS / "001_init.sql").read_text())
    (bad / "002_broken.sql").write_text("CREATE TABLE ok_table (x);\nCREATE TABLE ok_table (x);")  # second statement fails
    monkeypatch.setattr(store, "MIGRATIONS", bad)
    try:
        store.connect(tmp_path / "t.db")
    except sqlite3.Error:
        pass
    c = sqlite3.connect(tmp_path / "t.db")
    assert "ok_table" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}  # rolled back
    assert [r[0] for r in c.execute("SELECT name FROM schema_migrations")] == ["001_init.sql"]  # 002 reruns next time


def test_settings_account_override(conn):
    store.set_setting(conn, "low_balance_threshold", 500, "acct1")
    assert store.setting(conn, "low_balance_threshold", "acct1") == "500"
    assert store.setting(conn, "low_balance_threshold", "other") is None
    store.set_setting(conn, "low_balance_threshold", 100)
    assert store.setting(conn, "low_balance_threshold", "other") == "100"
    assert store.setting(conn, "low_balance_threshold", "acct1") == "500"


def test_state_roundtrip(conn):
    assert store.get_state(conn, "x") is None
    store.set_state(conn, "x", 5)
    assert store.get_state(conn, "x") == "5"


def test_secrets_written_0600_and_atomic(home):
    p = write({"client_id": "c", "production_secret": "s"}, {"bot_token": 't"q'}, {})
    assert p == config.secrets_path()
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    s = config.load_secrets()
    assert s["plaid"]["client_id"] == "c" and s["telegram"]["bot_token"] == 't"q'
    update(items={"item1": "access-1"})
    s = config.load_secrets()
    assert s["items"]["item1"] == "access-1" and s["plaid"]["client_id"] == "c"
    assert not list(home.glob(".secrets.*.tmp"))
    assert config.item_token("item1") == "access-1"


def test_migration_006_adds_is_active_to_an_older_db(tmp_path):
    """An existing DB keeps its recurring rows; is_active is NULL (read as active) until the next sync fills it."""
    from pathlib import Path
    db = tmp_path / "old.db"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
    for f in sorted((Path(__file__).resolve().parents[1] / "finnamon/migrations").glob("*.sql"))[:5]:
        c.executescript(f.read_text())
        c.execute("INSERT INTO schema_migrations(name) VALUES (?)", (f.name,))
    c.execute("INSERT INTO owners (owner) VALUES ('bill')"); c.execute("INSERT INTO items (item_id, owner) VALUES ('item1','bill')")
    c.execute("INSERT INTO accounts (account_id, item_id, owner, name) VALUES ('chk','item1','bill','Checking')")
    c.execute("INSERT INTO recurring (stream_id, account_id, direction, first_seen_at) VALUES ('s1','chk','outflow','2026-01-01')")
    c.close()
    conn = store.connect(db)
    assert conn.execute("SELECT stream_id, is_active FROM recurring").fetchone()[:] == ("s1", None)
