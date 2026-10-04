import json
import os
import sqlite3
from pathlib import Path

import pytest

from finnamon import store
from finnamon.daemon import bot_configured as REAL_BOT_CONFIGURED  # noqa: N812, F401 - the real guard, before the autouse fixture stubs it
from finnamon.assistant import plugin_dir_problem as REAL_PLUGIN_DIR_PROBLEM  # noqa: N812 - the real guard, before the autouse fixture stubs it
from finnamon.remote import tailscale_bin as REAL_TAILSCALE_BIN  # noqa: N812, F401 - for tests/test_remote.py
from finnamon.scheduler import log_dir as REAL_LOG_DIR, unit_dir as REAL_UNIT_DIR  # noqa: N812 - for the tests of these themselves, under a scratch Path.home

AS_OF = "2026-09-19 12:00:00"


def today() -> str:
    """For tests that exercise code paths bound to the wall clock (date('now'))."""
    from datetime import date
    return date.today().isoformat()


@pytest.fixture(autouse=True)
def _not_inside_claude(monkeypatch, tmp_path):
    """The suite often runs from a Claude Code session; the CLI's human-only gates must see a plain terminal."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("FINNAMON_FROM_CLAUDE", raising=False)
    monkeypatch.delenv("FINNAMON_WEB_TOKEN", raising=False)   # a laptop that uploads to a box exports it; the suite must not inherit it
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("PORT", raising=False)   # doctor's port probe reads PORT (`finnamon remote` must ignore it): a shell with PORT exported must not move it under the tests
    monkeypatch.delenv("FINNAMON_WEB_HOSTS", raising=False)   # a box serving its dashboard over Tailscale exports it; rendered units must not depend on the shell   # `finnamon open` prints instead of opening over SSH; the suite is often run over SSH
    # A test that forgets `fake_claude` must never shell out to the real `claude`: `binary()` falls back to PATH, so the
    # override points at a path that does not exist rather than at nothing (`claude plugin install` in particular would
    # register the Telegram plugin for real, against whatever directory the test used).
    monkeypatch.setenv("FINNAMON_CLAUDE_BIN", str(tmp_path / "no-such-claude"))
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "home"))   # a test that forgets the `home` fixture must still never open the household's real db
    # CLAUDE_CONFIG_DIR: where the trust flag is written (Claude Code's ~/.claude.json), redirected so no test touches
    # the real one; the eval lane, which runs the real claude, puts it back. Then an installed, trusted assistant
    # directory under that scratch home, so the harness check passes and every household spawn has a cwd (written there,
    # never pointed at the bundle's source, which `install` and `update` under test would otherwise write into); a test
    # of the install itself removes it first (tests/test_assistant.py's own_dir).
    monkeypatch.delenv("FINNAMON_ASSISTANT", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    (tmp_path / "claude-config").mkdir(exist_ok=True)
    # Every test's assistant directory is a scratch one, which the plugin guard rightly refuses; the tests of the
    # registration itself need it to pass (the fake claude registers nothing). The guard's own test uses REAL_PLUGIN_DIR_PROBLEM.
    from finnamon import assistant
    monkeypatch.setattr(assistant, "plugin_dir_problem", lambda d: None)
    from finnamon import scheduler   # the heartbeat and daemon rotate ~/Library/Logs/finnamon: never the household's real logs
    monkeypatch.setattr(scheduler, "log_dir", lambda: tmp_path / "Logs")
    monkeypatch.setattr(scheduler, "unit_dir", lambda os_name=None: tmp_path / "units")   # nor read or write the real LaunchAgents / systemd units
    from finnamon import daemon   # every poll-loop test runs a bot; tests/test_demo.py checks the real guard (REAL_BOT_CONFIGURED)
    monkeypatch.setattr(daemon, "bot_configured", lambda: True)
    from finnamon import remote   # doctor asks tailscale; no test may reach the machine's real one (tests/test_remote.py fakes it)
    monkeypatch.setattr(remote, "tailscale_bin", lambda: None)
    scratch_assistant()


def scratch_assistant() -> None:
    """The bundle, installed and trusted under the current FINNAMON_HOME (a scratch directory in every test)."""
    from finnamon import assistant
    assistant.install(); assistant.trust()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path))
    monkeypatch.setenv("FINNAMON_PLAID_ENV", "sandbox")
    scratch_assistant()   # the home moved: the assistant directory moves with it
    return tmp_path


@pytest.fixture
def household_home(tmp_path, monkeypatch):
    """FINNAMON_HOME at the default ~/.finnamon, with ~ a scratch directory: what the household's own jobs are named for."""
    from finnamon import scheduler
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / ".finnamon"))
    return tmp_path / ".finnamon"


@pytest.fixture
def conn(home) -> sqlite3.Connection:
    return store.connect()


def seed(conn: sqlite3.Connection, first_synced_at: str = "2026-06-01 00:00:00", owner: str = "bill") -> dict:
    """One owner, one Item, a checking account and a credit card, baseline set. Returns ids."""
    conn.execute("INSERT OR IGNORE INTO owners (owner, telegram_user_id, display_name) VALUES (?, 111, 'Bill')", (owner,))
    conn.execute("INSERT OR IGNORE INTO items (item_id, institution_id, institution, owner, first_synced_at, last_synced_at, created_at) VALUES ('item1','ins_1','Chase',?,?,?,?)",
                 (owner, first_synced_at, AS_OF, first_synced_at or "2026-06-01 00:00:00"))
    conn.execute("INSERT OR IGNORE INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('chk','item1','Chase Checking','depository','checking','4821',?)", (owner,))
    conn.execute("INSERT OR IGNORE INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES ('cc','item1','Sapphire','credit','credit card','7710',?)", (owner,))
    store.set_state(conn, "chat_id", 1234567890)
    return {"item": "item1", "chk": "chk", "cc": "cc"}


def txn(conn, tid, account, date, amount, name, merchant=None, entity=None, primary="FOOD_AND_DRINK", detailed="FOOD_AND_DRINK_GROCERIES", pending=0, pending_id=None):
    conn.execute(
        "INSERT OR REPLACE INTO transactions (transaction_id, account_id, date, amount, name, merchant_name, merchant_entity_id, pfc_primary, pfc_detailed, pfc_confidence, pending, pending_transaction_id, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,'HIGH',?,?,'{}')",
        (tid, account, date, amount, name, merchant, entity, primary, detailed, pending, pending_id))


def plaid_txn(tid, account, date, amount, name, merchant=None, entity=None, primary="FOOD_AND_DRINK", detailed="FOOD_AND_DRINK_GROCERIES", pending=False, pending_id=None) -> dict:
    return {"transaction_id": tid, "account_id": account, "date": date, "amount": amount, "name": name, "merchant_name": merchant,
            "merchant_entity_id": entity, "personal_finance_category": {"primary": primary, "detailed": detailed, "confidence_level": "HIGH"},
            "pending": pending, "pending_transaction_id": pending_id}


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A fake `claude` binary. Set CLAUDE_FAKE_RESULT / CLAUDE_FAKE_EXIT / CLAUDE_FAKE_SLEEP to steer it."""
    script = tmp_path / "claude"
    script.write_text('''#!/bin/sh
[ -n "$CLAUDE_FAKE_SLEEP" ] && sleep "$CLAUDE_FAKE_SLEEP"
[ -n "$CLAUDE_FAKE_EXIT" ] && { echo "$CLAUDE_FAKE_STDERR" >&2; exit "$CLAUDE_FAKE_EXIT"; }
printf '%s' "$CLAUDE_FAKE_RESULT"
''')
    script.chmod(0o755)
    monkeypatch.setenv("FINNAMON_CLAUDE_BIN", str(script))
    monkeypatch.setenv("CLAUDE_FAKE_RESULT", json.dumps({"result": "ok", "session_id": "sess-1"}))
    monkeypatch.delenv("CLAUDE_FAKE_EXIT", raising=False)
    monkeypatch.delenv("CLAUDE_FAKE_SLEEP", raising=False)
    return script


class FakeTelegram:
    """Records sends; monkeypatched over finnamon.telegram functions."""

    def __init__(self):
        self.sent: list[dict] = []
        self.photos: list[dict] = []
        self.fail_with = None
        self.updates: list[dict] = []
        self.me = {"username": "test_bot", "can_read_all_group_messages": True}
        self._next_id = 100

    def send_message(self, chat_id, text, reply_to=None, token=None):
        if self.fail_with:
            raise self.fail_with
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to, "message_id": self._next_id})
        return self._next_id

    def send_photo(self, chat_id, path, caption="", token=None):
        self._next_id += 1
        self.photos.append({"chat_id": chat_id, "path": path, "message_id": self._next_id})
        return self._next_id

    def get_updates(self, offset=None, timeout=25, token=None):
        ups = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return ups

    def get_me(self, token=None):
        return self.me


@pytest.fixture
def tg(monkeypatch):
    from finnamon import telegram
    f = FakeTelegram()
    for name in ("send_message", "send_photo", "get_updates", "get_me"):
        monkeypatch.setattr(telegram, name, getattr(f, name))
    return f
