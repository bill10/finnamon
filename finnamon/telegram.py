"""Telegram Bot API over urllib. Four calls. Every merchant string is HTML-escaped before it goes out.

FINNAMON_TELEGRAM_BASE overrides the API base (tests point it at a fake server)."""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import config


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int | None = None):
        self.code, self.description, self.retry_after = code, description, retry_after
        super().__init__(f"{code}: {description}")


def _base(token: str | None = None) -> str:
    token = token or config.telegram_token()
    return os.environ.get("FINNAMON_TELEGRAM_BASE", "https://api.telegram.org") + f"/bot{token}"


def _call(method: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30, token: str | None = None) -> dict:
    req = urllib.request.Request(f"{_base(token)}/{method}", data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {"ok": False, "description": str(e)}
        raise TelegramError(e.code, body.get("description", ""), (body.get("parameters") or {}).get("retry_after")) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:  # DNS, TLS, timeouts, non-JSON body
        raise TelegramError(0, f"{type(e).__name__}: {e}") from None
    if not body.get("ok"):
        raise TelegramError(0, body.get("description", "unknown"))
    return body["result"]


def _form(fields: dict) -> tuple[bytes, dict]:
    return urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode(), {"Content-Type": "application/x-www-form-urlencoded"}


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def get_me(token: str | None = None) -> dict:
    return _call("getMe", token=token)


MAX_MESSAGE = 4096


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on newlines (then hard) so no chunk exceeds the limit. Escaped HTML is never cut mid-entity by a newline split."""
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single over-long line: flush what we have, then hard-split it
            if buf:
                out.append(buf); buf = ""
            out.append(line[:limit]); line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            out.append(buf); buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out


def send_message(chat_id: int | str, text: str, reply_to: int | None = None, token: str | None = None) -> int:
    """Returns the message_id (of the first part, if split). `text` is HTML; callers escape user data with esc()."""
    first = None
    for part in split_message(text):
        data, headers = _form({"chat_id": chat_id, "text": part, "parse_mode": "HTML",
                               "reply_to_message_id": reply_to if first is None else None, "disable_web_page_preview": True})
        mid = _call("sendMessage", data, headers, token=token)["message_id"]
        first = first if first is not None else mid
    return first


def send_photo(chat_id: int | str, path: str, caption: str = "", token: str | None = None) -> int:
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        img = f.read()
    parts = []
    for k, v in (("chat_id", str(chat_id)), ("caption", caption[:1024]), ("parse_mode", "HTML")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + img + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return _call("sendPhoto", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=60, token=token)["message_id"]


def pending_updates(token: str | None = None, timeout: int = 30) -> int:
    """How many updates Telegram is holding for a consumer that has not collected them.

    getWebhookInfo reads; it never consumes, so it is safe to call while the channel plugin owns the one getUpdates
    slot this bot has. A count that stays above zero means nobody is reading the chat."""
    # max(0,...): a negative count would be truthy, and the caller reads truthy as "nobody is reading", which never clears.
    return max(0, int(_call("getWebhookInfo", timeout=timeout, token=token).get("pending_update_count") or 0))


def get_updates(offset: int | None = None, timeout: int = 25, token: str | None = None) -> list[dict]:
    q = urllib.parse.urlencode({k: v for k, v in {"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(["message"])}.items() if v is not None})
    return _call(f"getUpdates?{q}", timeout=timeout + 10, token=token)
