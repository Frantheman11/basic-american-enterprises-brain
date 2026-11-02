"""The gym's public billing routes, P2 (test mode). They answer without a staff sign-in, like the kiosk,
and only when the profile has a `gym.billing` block. Design: docs/products/gym-system-p2-design.md.

    GET  /join                    pick a plan, give a name and email: no card field here
    POST /api/join                a plain form post: answered with a 303 to Stripe Checkout
    GET  /join/thanks             where Stripe sends the member back
    GET  /join.css                the gym's look, public like the kiosk's
    GET  /billing/card?t=...      the member's card link: a 303 to a fresh Stripe customer portal page
    POST /api/gym/stripe/webhook  Stripe's events, signed (gym_billing.webhook)

The card is typed only on Stripe's own pages (Checkout and the customer portal). Nothing here asks for
a card number, an expiry or a CVC, and a test reads every page for one.
"""
import html
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from . import brand, crm_store, gym_billing, identity

WWW = Path(__file__).resolve().parent / "www"
PATHS = ("/join", "/join/thanks", "/join.css", "/api/join", "/billing/card", "/api/gym/stripe/webhook")
JSON = "application/json; charset=utf-8"
PRIVATE = {"Cache-Control": "private, no-store"}


def base(handler, prof):
    """The absolute address Stripe sends people back to: `gym.billing.public_url`, or this loopback
    server while testing. Never a Host header from the internet."""
    cfg = gym_billing.require(prof)
    url = cfg.get("public_url") or ""
    if url:
        u = urlparse(url)
        if not (u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))):
            raise crm_store.CRMError("gym.billing.public_url must be https", 503)
        return url if url.endswith("/") else url + "/"
    host = handler.headers.get("Host") or ""
    if identity._is_loopback_host(urlparse("http://" + host).hostname or ""):
        prefix = (prof.get("crm") or {}).get("path_prefix", "/") or "/"
        return f"http://{host}{prefix if prefix.endswith('/') else prefix + '/'}"
    raise crm_store.CRMError("Set gym.billing.public_url before opening the join page to the internet", 503)


def serve(handler, method, path, query, raw, prof):
    """True when answered. Off (False, a 404 upstream) without a billing block."""
    if path not in PATHS or gym_billing.config(prof) is None:
        return False
    store = handler.cc.crm
    try:
        if method == "POST" and path == "/api/gym/stripe/webhook":
            result = gym_billing.webhook(store, prof, raw, handler.headers.get("Stripe-Signature"))
            return handler._send(200, json.dumps(result), JSON, PRIVATE) or True
        if method == "GET" and path == "/join.css":
            css = b"\n".join([brand.stylesheet(handler.cc.root, prof, "font_stylesheet"),
                              (WWW / "crm.css").read_bytes(), (WWW / "gym.css").read_bytes(),
                              brand.stylesheet(handler.cc.root, prof, "theme_stylesheet")])
            return handler._send(200, css, "text/css; charset=utf-8", {"Cache-Control": "no-cache"}) or True
        if method == "GET" and path == "/join":
            canceled = "canceled" in parse_qs(query)
            note = "You left the payment page. Nothing was charged. Pick a plan to try again." if canceled else ""
            return handler._send(200, join_page(store, prof, note=note), headers=PRIVATE) or True
        if method == "GET" and path == "/join/thanks":
            card = "card" in parse_qs(query)
            return handler._send(200, thanks_page(prof, card), headers=PRIVATE) or True
        if method == "GET" and path == "/billing/card":
            t = (parse_qs(query).get("t") or [""])[0]
            return handler._send(303, "", headers={**PRIVATE, "Location": gym_billing.portal(store, prof, t, base(handler, prof))}) or True
        if method == "POST" and path == "/api/join":
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            origin = handler.headers.get("Origin")
            if origin and origin != "null" and not same_site(handler, prof, origin):
                raise crm_store.CRMError("Join from the gym's own page", 403)
            try:
                url = gym_billing.checkout(store, prof, form, base(handler, prof))
            except crm_store.CRMError as e:
                if e.status >= 500:
                    raise
                return handler._send(e.status, join_page(store, prof, form, str(e)), headers=PRIVATE) or True
            return handler._send(303, "", headers={**PRIVATE, "Location": url}) or True
        return handler._send(405, json.dumps({"error": "Method not allowed"}), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        if path.startswith("/api/gym/"):
            return handler._send(e.status, json.dumps({"error": str(e)}), JSON, PRIVATE) or True
        return handler._send(e.status, message_page(prof, "Something needs a look", str(e)), headers=PRIVATE) or True
    except (ValueError, TypeError, UnicodeDecodeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def same_site(handler, prof, origin):
    public = (gym_billing.require(prof).get("public_url") or "").rstrip("/")
    if public and origin.rstrip("/") == "{0.scheme}://{0.netloc}".format(urlparse(public)):
        return True
    return identity.origin_allowed(prof, handler.cc.mode, origin)


def money(cents, interval):
    amount = f"${cents / 100:,.2f}".replace(".00", "")
    return amount + {"month": " a month", "year": " a year", "once": " once"}.get(interval, "")


def _head(prof, title):
    name = html.escape(prof["name"])
    accent = (prof.get("crm") or {}).get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    prefix = html.escape((prof.get("crm") or {}).get("path_prefix", "/"), quote=True)
    logo = '<img class="gym-logo" src="brand-logo" alt="">' if (prof.get("crm") or {}).get("logo") else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><base href="{prefix}"><meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(title)} | {name}</title><link rel="stylesheet" href="join.css">'
            f'<style>:root{{--accent:{accent}}}</style></head><body class="gym gym-join">'
            f'<header class="gym-top"><span class="gym-brand">{logo}<span><strong>{name}</strong></span></span></header>'
            '<main class="join-main">')


TEST_NOTE = ('<p class="join-test"><b>Test mode.</b> No real money moves. Stripe\'s test cards only.</p>')


def join_page(store, prof, form=None, error="", note=""):
    form = form or {}
    e = html.escape
    plans = gym_billing.plans_for_sale(store, prof)
    picked = form.get("plan_id") or (plans[0]["id"] if plans else "")
    cards = "".join(
        f'<label class="join-plan"><input type="radio" name="plan_id" value="{e(p["id"], quote=True)}"'
        f'{" checked" if p["id"] == picked else ""} required><span><b>{e(p["name"])}</b>'
        f'<span class="join-price">{e(money(p["amount_cents"], p["interval"]))}</span></span></label>' for p in plans)
    field = lambda k, label, kind, auto, req=True: (
        f'<label class="join-field"><span>{label}</span><input name="{k}" type="{kind}" autocomplete="{auto}" '
        f'value="{e(form.get(k, ""), quote=True)}"{" required" if req else ""} maxlength="200"></label>')
    body = (f'<h1>Join {e(prof["name"])}</h1><p class="join-lead">Pick a plan. You pay on a secure Stripe page: '
            'the gym never sees your card.</p>'
            + (f'<p class="join-note">{e(note)}</p>' if note else "")
            + (f'<p class="join-error" role="alert">{e(error)}</p>' if error else "")
            + ('<p class="join-note">Online sign-up is not open yet. Ask at the front desk.</p>' if not plans else
               f'<form method="post" action="api/join" class="join-form"><fieldset><legend>Plan</legend>{cards}</fieldset>'
               + field("name", "Your name", "text", "name") + field("email", "Email", "email", "email")
               + field("phone", "Phone (optional)", "tel", "tel", False)
               + f'<input type="hidden" name="join_id" value="{uuid4()}">'
               '<button class="primary join-go" type="submit">Continue to secure payment</button></form>')
            + TEST_NOTE)
    return _head(prof, "Join") + body + "</main></body></html>"


def thanks_page(prof, card=False):
    if card:
        return message_page(prof, "Thank you", "Your card is updated. Stripe will retry the payment on its own.")
    return message_page(prof, "You're in", f"Welcome to {prof['name']}. Stripe emails your receipt. See you in class.")


def message_page(prof, title, text):
    return (_head(prof, title) + f'<h1>{html.escape(title)}</h1><p class="join-lead">{html.escape(text)}</p>'
            + TEST_NOTE + "</main></body></html>")
