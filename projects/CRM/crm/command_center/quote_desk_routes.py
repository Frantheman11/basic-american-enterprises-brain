"""The Quote Desk's routes, called from server.py. Nothing here answers unless the profile says
`quote_desk.enabled`: otherwise the caller carries on and the path is a 404 like any unknown one.

Every route runs after the normal sign-in and checks the role: `quote_read` to look,
`quote_edit` to add an RFQ, fill a gap or change a number, `quote_approve` to approve, send,
ask the buyer, send a follow-up, decline or record won and lost. The approval gate itself is in
quote_desk_store.py, so no route (and no future one) can send around it.
"""
import html
import json
from pathlib import Path
from urllib.parse import parse_qs

from . import crm_page, crm_store, demo_mode, profiles, quote_desk, quote_desk_store as store_

WWW = Path(__file__).resolve().parent / "www"
JSON = "application/json; charset=utf-8"
PRIVATE = {"Cache-Control": "private, no-store"}
PAGE, ASSETS = "/quotes", ("/quote-desk.js", "/quote-desk.css")
READS = {"/api/quotes/inbox", "/api/quotes/rfq", "/api/quotes/log", "/api/quotes/followups", "/api/quotes/kpis"}
EXPORT = "/api/quotes/export.csv"  # approved quotes as a flat table for the shop's own quoting tool or ERP
POST = "/api/quotes"
PATHS = {PAGE, *ASSETS, *READS, EXPORT, POST}
# action -> (capability, call)
ACTIONS = {
    "rfq.add": ("quote_edit", lambda s, p, who, b: {"id": store_.receive(
        s, p, {"sender": b.get("sender"), "subject": b.get("subject", ""), "body": b.get("body"),
               "attachments": b.get("attachments") or [], "source": "pasted"}, who)}),
    "rfq.ask": ("quote_approve", lambda s, p, who, b: store_.ask(s, p, who, b.get("rfq_id"), b.get("text"))),
    "rfq.decline": ("quote_approve", lambda s, p, who, b: store_.decline(s, p, who, b.get("rfq_id"), b.get("reason"))),
    "quote.edit": ("quote_edit", lambda s, p, who, b: store_.edit(s, p, who, b.get("quote_id"), b.get("changes") or {})),
    "quote.approve": ("quote_approve", lambda s, p, who, b: store_.approve(s, p, who, b.get("quote_id"))),
    "quote.send": ("quote_approve", lambda s, p, who, b: store_.send(s, p, who, b.get("quote_id"))),
    "quote.outcome": ("quote_approve", lambda s, p, who, b: store_.outcome(s, p, who, b.get("quote_id"),
                                                                           b.get("outcome"), b.get("reason", ""))),
    "followup.send": ("quote_approve", lambda s, p, who, b: store_.followup_send(s, p, who, b.get("followup_id"),
                                                                                  b.get("text"))),
}


def serve(handler, u, raw):
    """server.py's one call, GET and POST. True when a quote route answered."""
    path = u.path.rstrip("/") or "/"
    store = handler.cc.crm
    if path not in PATHS or store is None or getattr(store, "readonly", False):
        return False
    try:
        if not quote_desk.enabled(profiles.load(handler.cc.business, handler.cc.root)):
            return False
    except Exception:  # noqa: BLE001  a broken profile is the sign-in's error to report
        return False
    who = handler._who(as_json=path.startswith("/api/"))
    if who is None:
        return True
    prof, person = who
    try:
        store.authorize(prof, person, "quote_read")
        if handler.command == "POST":
            if path != POST:
                return handler._send(405, json.dumps({"error": "Method not allowed"}), JSON, PRIVATE) or True
            bad = handler._reject_cross_origin(prof)
            if bad:
                return handler._send(403, json.dumps({"error": bad}), JSON, PRIVATE) or True
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict) or body.get("action") not in ACTIONS:
                raise ValueError("unknown action")
            cap, call = ACTIONS[body["action"]]
            store.authorize(prof, person, cap)
            return handler._send(200, json.dumps(call(store, prof, person.id, body)), JSON, PRIVATE) or True
        if path in ASSETS:
            ctype = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            return handler._send(200, (WWW / path.lstrip("/")).read_bytes(),
                                 ctype, {"Cache-Control": "no-cache"}) or True
        if path == PAGE:
            return handler._send(200, page(prof), headers=PRIVATE) or True
        if path == EXPORT:
            return handler._send(200, store_.export_csv(store, prof), "text/csv; charset=utf-8",
                                 {**PRIVATE, "Content-Disposition": 'attachment; filename="quotes.csv"'}) or True
        q = parse_qs(u.query)
        one = lambda k: (q.get(k) or [""])[0]
        read = {"/api/quotes/inbox": lambda: store_.inbox(store, prof),
                "/api/quotes/rfq": lambda: store_.rfq(store, prof, one("id")),
                "/api/quotes/log": lambda: store_.log(store, prof),
                "/api/quotes/followups": lambda: store_.followups(store, prof),
                "/api/quotes/kpis": lambda: store_.kpis(store, prof, max(1, min(int(one("weeks") or 4), 26)))}[path]
        return handler._send(200, json.dumps(read()), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return handler._send(e.status, json.dumps({"error": str(e)}), JSON, PRIVATE) or True
    except (ValueError, TypeError, KeyError, AttributeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def page(prof):
    """The desk: Inbox, one RFQ, Follow-ups, Quote log, Numbers. One script, hash routes."""
    name = html.escape(prof["name"])
    crm = prof.get("crm") or {}
    accent = crm.get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    prefix = html.escape(crm.get("path_prefix", "/"), quote=True)
    demo = '<link rel="stylesheet" href="demo.css"><script defer src="demo.js"></script>' if demo_mode.config(prof) else ""
    reader = "Demo reader, no AI key" if (quote_desk.config(prof) or {}).get("extractor", "demo") == "demo" else "AI reader"
    return crm_page.version_assets(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix}">
<meta name="robots" content="noindex,nofollow"><title>{name} | Quote Desk</title>
<link rel="stylesheet" href="crm.css"><link rel="stylesheet" href="quote-desk.css"><style>:root{{--accent:{accent}}}</style>{demo}
<script defer src="quote-desk.js"></script></head><body class="qd">
<header class="qd-top"><a class="qd-brand" href="quotes#inbox"><span><strong>{name}</strong><small>Quote Desk · {reader} · a person approves every quote</small></span></a>
<nav aria-label="Quote Desk"><a href="quotes#inbox" data-tab="inbox">Inbox</a><a href="quotes#followups" data-tab="followups">Follow-ups</a>
<a href="quotes#log" data-tab="log">Quote log</a><a href="quotes#numbers" data-tab="numbers">Numbers</a><a href="quotes#new" data-tab="new">Paste an RFQ</a></nav></header>
<main id="qd" aria-live="polite"><p class="qd-sub">Loading</p></main>
<noscript>The Quote Desk needs JavaScript.</noscript></body></html>""")
