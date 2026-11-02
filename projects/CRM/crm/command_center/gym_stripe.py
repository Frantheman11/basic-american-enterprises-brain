"""Stripe for the gym module, P2: plain urllib, no SDK. Ported from the ordering engine's stripe.js.

Design: docs/products/gym-system-p2-design.md. TEST MODE ONLY in P2: a key that is not `sk_test_` or
`rk_test_` is refused before any request, and an answer that says `livemode: true` is refused after.

Every call on a gym's own account carries `Stripe-Account: acct_...` (Connect Standard, direct
charges): the charge, the customer and the card live in the gym's account, never in ours. Every POST
carries an Idempotency-Key the caller builds from its own ids, so a retry cannot create twice.

The API host is api.stripe.com and connect.stripe.com. `GYM_STRIPE_API_BASE` moves both to a local
Stripe test double (tools/stripe_double.py) for the scripted run and the tests, and only to a
loopback http address, so no setting can send a key anywhere else.
"""
import hashlib
import hmac
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

API, CONNECT = "https://api.stripe.com", "https://connect.stripe.com"
VERSION = "2024-06-20"  # invoices carry `charge` and `payment_intent` in this version
TEST_KEY = re.compile(r"(sk|rk)_test_[A-Za-z0-9]+")
ACCOUNT = re.compile(r"acct_[A-Za-z0-9]+")


class StripeError(Exception):
    def __init__(self, message, status=502, code=""):
        super().__init__(message)
        self.status, self.code = status, code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise StripeError("Stripe returned an unexpected redirect")


def check_key(key):
    """The P2 rule: a test key or nothing. Says why, and never echoes the key."""
    if not key:
        raise StripeError("The Stripe test key is not set", 503, "no_key")
    if not TEST_KEY.fullmatch(key):
        raise StripeError("Only a Stripe TEST key is allowed in P2 (sk_test_ or rk_test_). This key was refused.",
                          503, "live_key")
    return key


def bases():
    """(api, connect) hosts. The override is loopback-only, for the local test double."""
    override = os.environ.get("GYM_STRIPE_API_BASE", "").rstrip("/")
    if not override:
        return API, CONNECT
    u = urlparse(override)
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or u.path:
        raise StripeError("GYM_STRIPE_API_BASE may only point at a loopback test double", 503)
    return override, override


def encode(obj, prefix="", out=None):
    """Stripe's form encoding: nested dicts as a[b]=c, lists as a[0]=c."""
    out = [] if out is None else out
    for k, v in obj.items():
        if v is None:
            continue
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            encode(v, key, out)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                encode(x, f"{key}[{i}]", out) if isinstance(x, dict) else out.append((f"{key}[{i}]", x))
        else:
            out.append((key, "true" if v is True else "false" if v is False else v))
    return out


class Client:
    """One gym's view of Stripe: the platform's test key, the gym's connected account."""

    def __init__(self, key, account=None, timeout=15):
        self.key = check_key(key)
        if account is not None and not ACCOUNT.fullmatch(account or ""):
            raise StripeError("The connected Stripe account id is not valid", 503)
        self.account, self.timeout = account, timeout
        self.api, self.connect = bases()
        self.opener = build_opener(_NoRedirect)

    def call(self, method, path, params=None, key=None, host=None, on_account=True):
        url = (host or self.api) + path
        headers = {"Authorization": "Bearer " + self.key, "Stripe-Version": VERSION}
        if on_account and self.account:
            headers["Stripe-Account"] = self.account
        data = None
        if method == "GET":
            if params:
                url += "?" + urlencode(encode(params))
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(encode(params or {})).encode()
            if key:
                headers["Idempotency-Key"] = key
        for attempt in range(5):
            try:
                with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=self.timeout) as r:
                    body = json.load(r)
                break
            except HTTPError as e:
                try:
                    err = (json.load(e) or {}).get("error") or {}
                except ValueError:
                    err = {}
                # Two taps at once send one key: Stripe answers the second 409 while the first runs.
                if e.code == 409 and key and attempt < 4:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise StripeError(err.get("message") or f"Stripe answered {e.code}", e.code, err.get("code", "")) from None
            except (OSError, URLError, ValueError):
                raise StripeError("Stripe could not be reached. Try again shortly.", 503) from None
        if isinstance(body, dict) and body.get("livemode") is True:
            raise StripeError("Stripe answered in LIVE mode. P2 is test mode only; nothing was recorded.", 503, "live_mode")
        return body

    def post(self, path, params, key):
        return self.call("POST", path, params, key)

    def get(self, path, params=None):
        return self.call("GET", path, params)

    def listed(self, path, params):
        """Every page of a list, or an error: never a short list that reads as complete."""
        params, rows, seen = dict(params, limit=100), [], set()
        for _ in range(200):
            page = self.get(path, params)
            if not isinstance(page.get("data"), list) or type(page.get("has_more")) is not bool:
                raise StripeError("Stripe returned an incomplete list")
            for row in page["data"]:
                if row["id"] in seen:
                    raise StripeError("Stripe's list changed while reading. Run the check again.")
                seen.add(row["id"])
                rows.append(row)
            if not page["has_more"] or not page["data"]:
                return rows
            params["starting_after"] = page["data"][-1]["id"]
        raise StripeError("Stripe's list is too long to read in one check")

    # ---- Connect onboarding (OAuth, Standard accounts)

    def authorize_url(self, client_id, state, redirect_uri):
        q = urlencode({"response_type": "code", "client_id": client_id, "scope": "read_write",
                       "state": state, "redirect_uri": redirect_uri})
        return f"{self.connect}/oauth/authorize?{q}"

    def oauth_token(self, code):
        """The connected account id for an OAuth code. Its tokens are not kept: every later call
        uses the platform key with `Stripe-Account`, so no secret of the gym's is stored."""
        r = self.call("POST", "/oauth/token", {"grant_type": "authorization_code", "code": code},
                      host=self.connect, on_account=False)
        acct = r.get("stripe_user_id", "")
        if not ACCOUNT.fullmatch(acct):
            raise StripeError("Stripe did not return a connected account")
        return acct


def path(*parts):
    return "/v1/" + "/".join(quote(str(p), safe="") for p in parts)


def sign(payload, secret, t=None):
    """Sign a payload exactly as Stripe does (tests and the local double)."""
    t = int(time.time()) if t is None else int(t)
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(payload, header, secret, now=None, tolerance=300):
    """Stripe-Signature: HMAC-SHA256 of "t.payload", any v1 may match, t within the tolerance.
    Returns the parsed event or raises StripeError(400)."""
    if not secret:
        raise StripeError("The webhook secret is not set", 503)
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    stamps = [v for k, v in parts if k == "t"]
    sigs = [v for k, v in parts if k == "v1"]
    if len(stamps) != 1 or not stamps[0].isdigit() or not sigs:
        raise StripeError("Bad signature header", 400)
    t = int(stamps[0])
    if abs((time.time() if now is None else now) - t) > tolerance:
        raise StripeError("Signature too old", 400)
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(s, expected) for s in sigs):
        raise StripeError("Signature mismatch", 400)
    try:
        event = json.loads(payload)
    except ValueError:
        raise StripeError("The event is not JSON", 400) from None
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
        raise StripeError("The event has no id or type", 400)
    return event
