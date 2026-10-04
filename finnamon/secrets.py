"""The only writer of ~/.finnamon/secrets.toml. Atomic (temp + rename), mode 0600.
Called by `init` (plaid keys, bot token) and `link` (per-Item access tokens). Nothing else."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import config

_HEAD = """# Finnamon secrets. Never commit. Mode 0600.
# Everything else (settings, budgets, thresholds) lives in the SQLite DB, not here.
"""


def _q(s: str | None) -> str:
    s = s or ""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _section(name: str, kv: dict) -> str:
    """A blank value is left out, not written as "": a key that was skipped is missing, never an empty string that later
    reads as a rejected one (#111)."""
    return f"\n[{name}]\n" + "".join(f"{k} = {_q(v)}\n" for k, v in kv.items() if v)


def write(plaid: dict, telegram: dict, items: dict[str, str]) -> Path:
    p = config.secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    body = (_HEAD
            + _section("plaid", {k: plaid.get(k) for k in ("client_id", "sandbox_secret", "production_secret")})
            + _section("telegram", {"bot_token": telegram.get("bot_token")})
            + "\n[items]\n" + "".join(f"{_q(k)} = {_q(v)}\n" for k, v in sorted(items.items())))
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".secrets.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def remove_item(item_id: str) -> Path:
    """Forget one Item's access token; everything else is kept as is."""
    cur = config.load_secrets()
    items = {k: v for k, v in cur.get("items", {}).items() if k != item_id}
    return write(dict(cur.get("plaid", {})), dict(cur.get("telegram", {})), items)


def update(**sections) -> Path:
    """Merge the given sections into the existing file. update(items={...}) adds tokens."""
    cur = config.load_secrets()
    plaid = {**cur.get("plaid", {}), **sections.get("plaid", {})}
    telegram = {**cur.get("telegram", {}), **sections.get("telegram", {})}
    items = {**cur.get("items", {}), **sections.get("items", {})}
    return write(plaid, telegram, items)
