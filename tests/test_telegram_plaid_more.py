"""The two urllib wrappers, with urlopen faked: error bodies, form/multipart encoding, credentials guard."""
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from finnamon import config, plaid_api, secrets, telegram
from finnamon.plaid_api import PlaidError
from finnamon.telegram import TelegramError


class FakeHTTP:
    """Replaces urllib.request.urlopen. `responses` is a list of bytes bodies or exceptions, consumed in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(r)


def http_error(code, body: bytes):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


def test_telegram_wrapper(monkeypatch, tmp_path):
    monkeypatch.setenv("FINNAMON_TELEGRAM_BASE", "http://fake")
    fake = FakeHTTP([
        http_error(429, json.dumps({"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 7}}).encode()),
        http_error(502, b"<html>bad gateway</html>"),
        json.dumps({"ok": False, "description": "chat not found"}).encode(),
    ])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(TelegramError) as e:
        telegram.get_me(token="T")
    assert (e.value.code, e.value.description, e.value.retry_after) == (429, "Too Many Requests", 7)
    with pytest.raises(TelegramError) as e:
        telegram.get_me(token="T")
    assert e.value.code == 502 and "502" in e.value.description and e.value.retry_after is None  # non-JSON body
    with pytest.raises(TelegramError) as e:
        telegram.get_me(token="T")
    assert e.value.code == 0 and e.value.description == "chat not found"  # HTTP 200 but ok=false
    assert fake.requests[0].full_url == "http://fake/botT/getMe"

    # --- request encoding -----------------------------------------------------------------
    monkeypatch.setenv("FINNAMON_TELEGRAM_BASE", "http://fake")
    ok = lambda result: json.dumps({"ok": True, "result": result}).encode()  # noqa: E731
    fake = FakeHTTP([ok({"message_id": 7}), ok({"message_id": 70}), ok({"message_id": 8}), ok({"message_id": 9}), ok([{"update_id": 1}])])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    # send_message: form-encoded, None fields dropped, long text split (never truncated), first part's id returned
    assert telegram.send_message(5, "x" * 5000, token="T") == 7
    form = urllib.parse.parse_qs(fake.requests[0].data.decode())
    assert form["chat_id"] == ["5"] and len(form["text"][0]) == 4096 and form["parse_mode"] == ["HTML"] and "reply_to_message_id" not in form
    assert len(urllib.parse.parse_qs(fake.requests[1].data.decode())["text"][0]) == 904
    assert telegram.send_message(5, "hi", reply_to=3, token="T") == 8
    assert urllib.parse.parse_qs(fake.requests[2].data.decode())["reply_to_message_id"] == ["3"]
    assert telegram.split_message("a\nb\n" + "c" * 10, limit=6) == ["a\nb", "cccccc", "cccc"]
    # send_photo: multipart with the file bytes and a chart.png filename
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG-bytes")
    assert telegram.send_photo(5, str(png), caption="cap", token="T") == 9
    body, ctype = fake.requests[3].data, fake.requests[3].get_header("Content-type")
    assert ctype.startswith("multipart/form-data; boundary=") and b"\x89PNG-bytes" in body and b'filename="chart.png"' in body and b"cap" in body
    # get_updates: query string with offset and allowed_updates
    assert telegram.get_updates(offset=42, timeout=1, token="T") == [{"update_id": 1}]
    url = fake.requests[4].full_url
    assert "getUpdates?" in url and "offset=42" in url and "timeout=1" in url and "allowed_updates=" in url
    # default token comes from secrets.toml
    monkeypatch.delenv("FINNAMON_TELEGRAM_BASE")
    assert telegram._base().startswith("https://api.telegram.org/bot") and telegram.esc(None) == "" and telegram.esc("<b>&") == "&lt;b&gt;&amp;"


def test_plaid_call_credentials_and_errors(home, monkeypatch):
    assert config.load_secrets() == {} and config.item_tokens() == {} and config.telegram_token() == ""
    with pytest.raises(PlaidError) as e:
        plaid_api.accounts_get("tok")
    assert e.value.code == "NO_CREDENTIALS"  # no secrets.toml: never hits the network
    secrets.write({"client_id": "cid", "sandbox_secret": "sec"}, {}, {})
    fake = FakeHTTP([
        http_error(400, json.dumps({"error_type": "INVALID_INPUT", "error_code": "INVALID_ACCESS_TOKEN", "error_message": "bad token"}).encode()),
        http_error(500, b"gateway"),
        json.dumps({"link_token": "lt", "hosted_link_url": "https://h", "expiration": "e"}).encode(),
        json.dumps({"link_token": "lt2", "hosted_link_url": "https://h2"}).encode(),
    ])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(PlaidError) as e:
        plaid_api.item_get("tok")
    assert (e.value.code, e.value.type, e.value.http, str(e.value)) == ("INVALID_ACCESS_TOKEN", "INVALID_INPUT", 400, "INVALID_ACCESS_TOKEN: bad token")
    with pytest.raises(PlaidError) as e:
        plaid_api.item_get("tok")
    assert e.value.code == "HTTP_500" and e.value.http == 500  # non-JSON error body
    sent = json.loads(fake.requests[0].data)
    assert fake.requests[0].full_url == "https://sandbox.plaid.com/item/get" and sent["client_id"] == "cid" and sent["secret"] == "sec" and sent["access_token"] == "tok"
    # link_token_create: products in create mode, access_token (and no products) in update mode
    plaid_api.link_token_create("finnamon-bill")
    plaid_api.link_token_create("finnamon-bill", access_token="tok")
    create, update = json.loads(fake.requests[2].data), json.loads(fake.requests[3].data)
    assert create["products"] == ["transactions"] and "access_token" not in create and create["user"] == {"client_user_id": "finnamon-bill"} and create["hosted_link"] == {}
    assert create["optional_products"] == ["investments"] and create["transactions"] == {"days_requested": 730}  # 90-day default is fixed at creation
    assert update["access_token"] == "tok" and "products" not in update and "additional_consented_products" not in update  # re-login changes nothing else
    # explicit env overrides the configured one (sandbox helpers always go to sandbox)
    fake.responses.append(b"{}")
    plaid_api.sandbox_public_token_create()
    assert fake.requests[4].full_url == "https://sandbox.plaid.com/sandbox/public_token/create"
    fake.responses.append(b'{"removed": true}')
    assert plaid_api.item_remove("tok") == {"removed": True}
    body = json.loads(fake.requests[5].data)
    assert fake.requests[5].full_url.endswith("/item/remove") and body["access_token"] == "tok" and body["client_id"] == "cid"
