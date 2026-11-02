"""Order Desk routes, called from server.py. Nothing here answers unless the profile says
`order_desk.enabled`: otherwise the caller carries on and the path is a 404 like any unknown one.
Every route runs after the normal sign-in; the right ("read", "approve", "admin") comes from the
profile's `order_desk.rights` for the person's CRM role (order_desk.py)."""
import html
import json
from pathlib import Path
from urllib.parse import parse_qs

from . import crm_page, crm_store, order_desk, profiles

WWW = Path(__file__).resolve().parent / "www"
JSON = "application/json; charset=utf-8"
PRIVATE = {"Cache-Control": "private, no-store"}
GETS = ("/orders", "/order-desk.js", "/order-desk.css", "/api/orders/items", "/api/orders/queue", "/api/orders/order", "/api/orders/message",
        "/api/orders/file", "/api/orders/kpis", "/api/orders/xref.csv", "/api/orders/import")
POST = "/api/orders/act"


def serve(handler, u, raw):
    """server.py's one call, GET and POST. True when an order desk route answered."""
    store = handler.cc.crm
    path = u.path.rstrip("/") or "/"
    if store is None or getattr(store, "readonly", False) or not (path in GETS or path == POST):
        return False
    try:
        prof = profiles.load(handler.cc.business, handler.cc.root)
    except Exception:  # noqa: BLE001  a broken profile is the sign-in's error to report
        return False
    if not order_desk.enabled(prof):
        return False
    who = handler._who(as_json=path.startswith("/api/"))
    if who is None:
        return True
    prof, person = who
    desk = order_desk.Desk(store, prof, handler.cc.root)
    try:
        store.authorize(prof, person)
        if handler.command == "POST":
            if path != POST:
                return handler._send(405, json.dumps({"error": "Method not allowed"}), JSON, PRIVATE) or True
            bad = handler._reject_cross_origin(prof)
            if bad:
                return handler._send(403, json.dumps({"error": bad}), JSON, PRIVATE) or True
            return handler._send(200, json.dumps(desk.act(person, json.loads(raw or b"{}"))), JSON, PRIVATE) or True
        return get(handler, desk, path, parse_qs(u.query), prof, person) or True
    except crm_store.CRMError as e:
        return handler._send(e.status, json.dumps({"error": str(e)}), JSON, PRIVATE) or True
    except (ValueError, TypeError, KeyError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def get(handler, desk, path, q, prof, person):
    one = lambda k: (q.get(k) or [""])[0]
    if path in ("/order-desk.js", "/order-desk.css"):
        ctype = "text/javascript" if path.endswith(".js") else "text/css"
        return handler._send(200, (WWW / path[1:]).read_bytes(), ctype + "; charset=utf-8", {"Cache-Control": "no-cache"})
    if path == "/orders":
        order_desk.check(prof, person, "read")
        return handler._send(200, page(prof), headers=PRIVATE)
    if path == "/api/orders/file":
        data, ctype, name = desk.file(person, one("m"), int(one("n") or -1))
        safe = "".join(c for c in name if c.isalnum() or c in "._-") or "file"
        # An attachment is a stranger's file: only a real PDF shows inline, and nothing it carries may run here.
        pdf = ctype == "application/pdf" and data[:5] == b"%PDF-"
        return handler._send(200, data, "application/pdf" if pdf else "application/octet-stream",
                             {**PRIVATE, "Content-Disposition": f'{"inline" if pdf else "attachment"}; filename="{safe}"',
                              "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})
    if path in ("/api/orders/xref.csv", "/api/orders/import"):
        if path.endswith("xref.csv"):
            data, name = desk.xref_csv(person), "customer-part-cross-reference.csv"
        else:
            data, name = desk.output(person, one("id"), one("layout") or "generic")
        return handler._send(200, data, "text/csv; charset=utf-8", {**PRIVATE, "Content-Disposition": f'attachment; filename="{name}"'})
    read = {"/api/orders/queue": lambda: desk.queue(person), "/api/orders/order": lambda: desk.order(person, one("id")),
            "/api/orders/message": lambda: desk.message(person, one("id")),
            "/api/orders/items": lambda: desk.items(person, one("q"), one("customer")), "/api/orders/kpis": lambda: desk.kpis(person)}[path]
    return handler._send(200, json.dumps(read()), JSON, PRIVATE)


def page(prof):
    """The rep's screen: the queue, one order beside its PO, status replies, the owner's numbers."""
    cfg = order_desk.require(prof)
    name = html.escape(cfg.get("name") or prof["name"])
    accent = (prof.get("crm") or {}).get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    prefix = html.escape((prof.get("crm") or {}).get("path_prefix", "/"), quote=True)
    sample = ('<p class="od-sample" role="note">Sample distributor: every customer, part and order here is made up. '
              'Nothing is sent and no ERP is connected.</p>') if cfg.get("sample") else ""
    reader = "Demo reader (no AI key)" if cfg.get("reader", "demo") == "demo" else "Claude reader"
    return crm_page.version_assets(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix}">
<meta name="robots" content="noindex,nofollow"><title>{name} | Order Desk</title>
<link rel="stylesheet" href="crm.css"><link rel="stylesheet" href="order-desk.css"><style>:root{{--accent:{accent}}}</style>
<script defer src="order-desk.js"></script></head><body class="od">
<header class="od-top"><a class="od-brand" href="orders#queue"><strong>{name}</strong><small>Order Desk &middot; {reader}</small></a>
<nav aria-label="Order desk"><a href="orders#queue" data-tab="queue">Inbox</a><a href="orders#kpis" data-tab="kpis">Numbers</a>
<a href="orders#xref" data-tab="xref">Part numbers</a></nav></header>{sample}
<main id="od" aria-live="polite"><p class="od-empty">Loading</p></main>
<noscript>The order desk needs JavaScript.</noscript></body></html>""")
