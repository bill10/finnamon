"""Paths and secrets. Secrets come from ~/.finnamon/secrets.toml (0600), written only by
`finnamon init` and `finnamon link` through finnamon.secrets, and the dashboard's key is ~/.finnamon/web-token
(0600, web_token() below). Everything else lives in SQLite.

FINNAMON_HOME overrides the directory (tests use a temp dir)."""
from __future__ import annotations

import os
import re
import secrets as _secrets
import tempfile
import tomllib
from pathlib import Path

# Set on every assistant run Finnamon starts (agent_runner.run); the CLI's human-only gates refuse a command that carries
# either. FINNAMON_FROM_CLAUDE is the old name, still honoured so a run started by an older release is still caught.
AGENT_MARKERS = ("FINNAMON_FROM_AGENT", "FINNAMON_FROM_CLAUDE")


def home() -> Path:
    return Path(os.environ.get("FINNAMON_HOME", Path.home() / ".finnamon"))


INTERCOM_FILE = "intercom.json"   # the dashboard's Claude session id; web/server.js writes it, `install` retires it
DASHBOARD_PORT = 8888   # the dashboard's default port; web/server.js DEFAULT_PORT is the same number (a test holds them together)
WEB_PORT_FILE = "web-port"   # the port the household last was told; `install`/`update` announce a move once
WEB_TOKEN_FILE = "web-token"   # the dashboard's key; web/server.js reads the same file on every request (and makes it on a fresh box)
WEB_HOSTS_FILE = "web-hosts"   # names a proxy serves the dashboard on (`finnamon remote`: the Tailscale name); web/server.js HOSTS_FILE reads it on every request
WEB_PAIR_FILE = "web-pair"   # the one pending pairing code `finnamon remote` shows as a QR (its sha256 and expiry, never the code); web/server.js PAIR_FILE redeems it
WEB_TOKEN = re.compile(r"[0-9a-f]{64}")   # what both minters write; anything else in the file is a mistake to report, not a key to serve


def db_path() -> Path:
    return home() / "finnamon.db"


def secrets_path() -> Path:
    return home() / "secrets.toml"


def charts_dir() -> Path:
    return home() / "charts"


def lock_path() -> Path:
    return home() / "run.lock"


def load_secrets() -> dict:
    p = secrets_path()
    if not p.exists():
        return {}
    return tomllib.loads(p.read_text())


def plaid_env() -> str:
    """'production' unless FINNAMON_PLAID_ENV says otherwise (tests use 'sandbox')."""
    return os.environ.get("FINNAMON_PLAID_ENV", "production")


def plaid_auth(env: str | None = None) -> dict:
    s = load_secrets().get("plaid", {})
    secret = s.get(f"{env or plaid_env()}_secret") or ""
    return {"client_id": s.get("client_id", ""), "secret": secret}


def item_token(item_id: str) -> str | None:
    return load_secrets().get("items", {}).get(item_id)


def item_tokens() -> dict[str, str]:
    return dict(load_secrets().get("items", {}))


def telegram_token() -> str:
    return load_secrets().get("telegram", {}).get("bot_token", "")


def web_hosts_path() -> Path:
    return home() / WEB_HOSTS_FILE


def web_token_path() -> Path:
    return home() / WEB_TOKEN_FILE


def web_token(rotate: bool = False) -> str:
    """The dashboard's key: 32 random bytes as hex, made on first use and kept across restarts, 0600 like secrets.toml.
    `finnamon open` puts it in the address once and the page swaps it for a cookie. rotate=True mints a new one, and the
    server, which reads this file on every request, locks every old cookie and copied key out at once. Written atomically
    (temp + rename), so a request landing mid-write reads the old key or the new one; a blank file can still come from the
    server's own first-run write dying halfway, and both readers treat that as missing."""
    p = web_token_path()
    if not rotate:
        try:
            if tok := p.read_text().strip():   # blank counts as missing: a blank key would lock everyone out
                if not WEB_TOKEN.fullmatch(tok):
                    raise ValueError(f"{p} is not a hex key; remove it and run `finnamon open`")
                return tok
        except FileNotFoundError:
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tok = _secrets.token_hex(32)
    if not rotate:   # first run: exclusive create, so the server minting at the same instant and this call cannot both hand out a key
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if have := p.read_text().strip():
                if not WEB_TOKEN.fullmatch(have):
                    raise ValueError(f"{p} is not a hex key; remove it and run `finnamon open`")
                return have
        else:
            with os.fdopen(fd, "w") as f:
                f.write(tok)
            return tok
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".web-token.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(tok)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return tok
