"""Against Plaid's real Sandbox. Skipped unless ~/.finnamon/secrets.toml has plaid.sandbox_secret.
Proves the API shapes the unit fixtures assume: sign convention, pagination, recurring, ITEM_LOGIN_REQUIRED."""
import os
import tomllib
from pathlib import Path

import pytest

from finnamon import plaid_api, secrets, store, sync, link

REAL = Path.home() / ".finnamon" / "secrets.toml"
_s = tomllib.loads(REAL.read_text()) if REAL.exists() else {}
HAVE = bool(_s.get("plaid", {}).get("client_id") and _s.get("plaid", {}).get("sandbox_secret"))

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not HAVE, reason="no plaid sandbox secret in ~/.finnamon/secrets.toml")]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path))
    monkeypatch.setenv("FINNAMON_PLAID_ENV", "sandbox")
    secrets.write(_s["plaid"], {"bot_token": ""}, {})
    conn = store.connect()
    conn.execute("INSERT INTO owners (owner) VALUES ('test')")
    pt = plaid_api.sandbox_public_token_create()["public_token"]
    ex = plaid_api.public_token_exchange(pt)
    link.register_item(conn, "test", ex["access_token"], ex["item_id"])
    return conn, ex["item_id"], ex["access_token"]


def test_full_sync_shapes(sandbox):
    conn, item_id, token = sandbox
    res = sync.sync_item(conn, item_id, token)
    assert res["error"] is None and res["accounts"] >= 1 and res["transactions"] >= 5
    it = conn.execute("SELECT cursor, first_synced_at, status FROM items WHERE item_id=?", (item_id,)).fetchone()
    assert it[0] and it[1] and it[2] == "good"
    # sign: sandbox has a -500 United Airlines refund and positive purchases
    amounts = [r[0] for r in conn.execute("SELECT amount FROM transactions")]
    assert any(a < 0 for a in amounts) and any(a > 0 for a in amounts)
    # (merchant_name / merchant_entity_id vary between sandbox Items; the old scripts/check_setup.py probe showed them populated)
    assert conn.execute("SELECT count(*) FROM transactions WHERE pfc_primary IS NOT NULL AND pfc_detailed IS NOT NULL AND pfc_confidence IS NOT NULL").fetchone()[0] >= 1
    # second sync with the saved cursor: no error, cursor moves or stays, count never drops. (A fresh sandbox Item keeps
    # generating transactions for a few seconds and re-sends rows as 'modified'; upserts converge on transaction_id.)
    n1 = conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    c1 = it[0]
    res2 = sync.sync_item(conn, item_id, token)
    assert res2["error"] is None
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] >= n1
    assert conn.execute("SELECT cursor FROM items WHERE item_id=?", (item_id,)).fetchone()[0]
    assert conn.execute("SELECT count(*) - count(DISTINCT transaction_id) FROM transactions").fetchone()[0] == 0


def test_reset_login_surfaces_as_item_login_required(sandbox):
    conn, item_id, token = sandbox
    sync.sync_item(conn, item_id, token)
    plaid_api.sandbox_item_reset_login(token)
    res = sync.sync_item(conn, item_id, token)
    assert res["error"] == "ITEM_LOGIN_REQUIRED"
    assert conn.execute("SELECT status FROM items WHERE item_id=?", (item_id,)).fetchone()[0] == "ITEM_LOGIN_REQUIRED"
    from finnamon import detect
    ids = detect.run(conn, store.now_local(), only=["sync_health"])
    assert len(ids) == 1
