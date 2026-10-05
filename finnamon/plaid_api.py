"""Plaid over urllib. Thin wrappers over the handful of endpoints we use; the official client's
generated models aren't worth the dependency for that. Every call returns the parsed JSON body; Plaid errors raise PlaidError."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import __version__, config


class PlaidError(Exception):
    def __init__(self, body: dict, http: int | None = None):
        self.code = body.get("error_code", "UNKNOWN")
        self.type = body.get("error_type")
        self.message = body.get("error_message", "")
        self.http = http
        super().__init__(f"{self.code}: {self.message}")


def call(endpoint: str, body: dict, env: str | None = None, timeout: int = 60) -> dict:
    env = env or config.plaid_env()
    auth = config.plaid_auth(env)
    if not auth["client_id"] or not auth["secret"]:
        raise PlaidError({"error_code": "NO_CREDENTIALS", "error_message": f"no Plaid {env} keys yet: run `finnamon init --plaid` to add them"})
    req = urllib.request.Request(
        f"https://{env}.plaid.com{endpoint}",
        data=json.dumps({**auth, **body}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": f"finnamon/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except Exception:
            payload = {"error_code": f"HTTP_{e.code}", "error_message": str(e)}
        raise PlaidError(payload, e.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:  # DNS, TLS, timeouts, non-JSON body
        raise PlaidError({"error_code": "NETWORK_ERROR", "error_message": f"{type(e).__name__}: {e}"}) from None


# --- thin wrappers, named after the endpoint --------------------------------------------------

def accounts_get(access_token: str) -> dict:
    return call("/accounts/get", {"access_token": access_token})


def transactions_sync(access_token: str, cursor: str | None, count: int = 500) -> dict:
    body = {"access_token": access_token, "count": count, "options": {"include_personal_finance_category": True}}
    if cursor:
        body["cursor"] = cursor
    return call("/transactions/sync", body)


def recurring_get(access_token: str) -> dict:
    return call("/transactions/recurring/get", {"access_token": access_token})


def item_get(access_token: str) -> dict:
    return call("/item/get", {"access_token": access_token})


def institution_get(institution_id: str, env: str | None = None) -> dict:
    return call("/institutions/get_by_id", {"institution_id": institution_id, "country_codes": ["US"]}, env=env)


HISTORY_DAYS = 730          # fixed at Item creation; Plaid's default is 90, too short for `budget suggest`
OPTIONAL_PRODUCTS = ["investments"]   # best effort: initialised at Link where the institution supports it (Investments is billed per Item that has it)


def link_token_create(user_id: str, access_token: str | None = None, products=("transactions",), redirect_uri: str | None = None,
                      hosted: bool = True) -> dict:
    """Hosted Link by default (a URL to open anywhere). hosted=False: a plain token for Plaid Link JS in the web dashboard;
    OAuth banks then open the bank in a popup, or return to redirect_uri when one is given (Plaid accepts HTTPS only
    outside Sandbox, so the page sends one only when it is served over HTTPS; it must be registered in the Plaid dashboard)."""
    body = {
        "user": {"client_user_id": user_id},
        "client_name": "Finnamon",
        "language": "en",
        "country_codes": ["US"],
    }
    if redirect_uri and hosted:
        raise ValueError("a redirect_uri is for Plaid Link JS (hosted=False); Hosted Link handles OAuth itself")
    if redirect_uri:
        body["redirect_uri"] = redirect_uri
    elif hosted:
        body["hosted_link"] = {}
    if access_token:
        body["access_token"] = access_token  # update mode (re-login) changes nothing else; to add holdings to an old Item, --remove and link again
    else:
        body["products"] = list(products)
        body["optional_products"] = OPTIONAL_PRODUCTS
        body["transactions"] = {"days_requested": HISTORY_DAYS}
    return call("/link/token/create", body)


def item_remove(access_token: str) -> dict:
    return call("/item/remove", {"access_token": access_token})


def link_token_get(link_token: str) -> dict:
    return call("/link/token/get", {"link_token": link_token})


def public_token_exchange(public_token: str) -> dict:
    return call("/item/public_token/exchange", {"public_token": public_token})


def investments_holdings_get(access_token: str) -> dict:
    return call("/investments/holdings/get", {"access_token": access_token})


def sandbox_public_token_create(institution_id: str = "ins_109508", products=("transactions",)) -> dict:
    return call("/sandbox/public_token/create", {"institution_id": institution_id, "initial_products": list(products)}, env="sandbox")


def sandbox_item_reset_login(access_token: str) -> dict:
    return call("/sandbox/item/reset_login", {"access_token": access_token}, env="sandbox")
