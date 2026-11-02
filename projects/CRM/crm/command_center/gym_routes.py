"""The gym module's routes, called from server.py. Nothing here answers unless the profile says
`gym.enabled` (and, for the kiosk, has a `gym.kiosk` block): otherwise the caller carries on and
the path is a 404 like any unknown one. Design: docs/products/gym-system-p1-design.md.

Staff routes run after the normal sign-in. Kiosk routes run before it: the kiosk is a device, not a
person, and proves itself with the token its PIN bought (gym_kiosk.py). The join page, the member's
card link and Stripe's webhook also run before it, only with a `gym.billing` block (gym_join.py, P2).
"""
import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from . import (brand, crm_page, crm_store, demo_mode, gym, gym_billing, gym_demo, gym_door, gym_history, gym_io, gym_join,
               gym_kiosk, gym_read, profiles, qr_svg)

WWW = Path(__file__).resolve().parent / "www"
JSON = "application/json; charset=utf-8"
PRIVATE = {"Cache-Control": "private, no-store"}
STAFF = ("/gym", "/gym.js", "/gym.css", "/gym/print", "/api/gym/today", "/api/gym/members", "/api/gym/member",
         "/api/gym/schedule", "/api/gym/roster", "/api/gym/attendance", "/api/gym/billing", "/api/gym/switch",
         "/api/gym/switch/sample.csv", "/api/gym/door", "/gym/billing/connect", "/gym/billing/connected")
POSTS = (gym_io.PATH, "/api/gym/billing")


def _asset(handler, name, ctype):
    return handler._send(200, (WWW / name).read_bytes(), ctype, {"Cache-Control": "no-cache"})


def _error(handler, e):
    return handler._send(e.status, json.dumps({"error": str(e)}), JSON, PRIVATE)


def serve(handler, u, raw):
    """server.py's one call, GET and POST. True when a gym route answered. The kiosk proves itself
    with its token; a staff route signs in here, exactly as server.py would, then checks the role."""
    store = handler.cc.crm
    path = u.path.rstrip("/") or "/"
    kiosk_path, staff_path = path in gym_kiosk.PATHS, path in STAFF or path in POSTS
    public_path, door_path = path in gym_join.PATHS, path in gym_door.PATHS
    if store is None or getattr(store, "readonly", False) or not (kiosk_path or staff_path or public_path or door_path):
        return False
    try:
        prof = profiles.load(handler.cc.business, handler.cc.root)
    except Exception:  # noqa: BLE001  a broken profile is the sign-in's error to report
        return False
    if not gym.enabled(prof):
        return False
    if public_path:
        return gym_join.serve(handler, handler.command, path, u.query, raw, prof)
    if kiosk_path:
        return kiosk(handler, handler.command, path, u.query, raw, prof)
    if door_path:
        return door(handler, handler.command, path, raw, prof)
    who = handler._who(as_json=path.startswith("/api/"))
    if who is None:
        return True
    prof, person = who
    try:
        store.authorize(prof, person)
    except crm_store.CRMError as e:
        return _error(handler, e) or True
    if handler.command == "POST":
        bad = handler._reject_cross_origin(prof)
        if bad:
            return handler._send(403, json.dumps({"error": bad}), JSON, PRIVATE) or True
        return staff_post(handler, path, prof, person, raw)
    return staff_get(handler, path, u.query, prof, person)


def staff_get(handler, path, query, prof, person):
    """True when this answered a gym route; False lets server.py carry on (404)."""
    if path not in STAFF or not gym.enabled(prof):
        return False
    store, q = handler.cc.crm, parse_qs(query)
    one = lambda key: (q.get(key) or [""])[0]
    try:
        if path == "/gym.js":
            return _asset(handler, "gym.js", "text/javascript; charset=utf-8") or True
        if path == "/gym.css":
            return _asset(handler, "gym.css", "text/css; charset=utf-8") or True
        if path == "/gym":
            store.authorize(prof, person, "gym_read")
            return handler._send(200, page(prof), headers=PRIVATE) or True
        if path == "/api/gym/switch/sample.csv":
            store.authorize(prof, person, "gym_read")
            body, name = gym_demo.sample_export(store, prof)
            return handler._send(200, body, "text/csv; charset=utf-8",
                                 {**PRIVATE, "Content-Disposition": f'attachment; filename="{name}"'}) or True
        if path in ("/gym/billing/connect", "/gym/billing/connected"):
            return connect(handler, path, q, prof, person) or True
        if path == "/api/gym/door":
            store.authorize(prof, person, "gym_read")
            return handler._send(200, json.dumps(door_codes(handler, prof)), JSON, PRIVATE) or True
        if path == "/gym/print":
            return handler._send(200, gym_read.printable(store, prof, person, one("date") or None), headers=PRIVATE) or True
        read = {"/api/gym/today": lambda: gym_read.today(store, prof, person),
                "/api/gym/members": lambda: gym_read.members(store, prof, person),
                "/api/gym/member": lambda: gym_read.member(store, prof, person, one("id")),
                "/api/gym/schedule": lambda: gym_read.schedule(store, prof, person, one("from") or None),
                "/api/gym/roster": lambda: gym_read.roster(store, prof, person, one("session")),
                "/api/gym/attendance": lambda: gym_history.attendance(store, prof, person, one("from") or None,
                                                                      one("to") or None, one("program") or None),
                "/api/gym/billing": lambda: gym_read.billing(store, prof, person),
                "/api/gym/switch": lambda: gym_read.switch(store, prof, person)}[path]
        return handler._send(200, json.dumps(read()), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return _error(handler, e) or True


def staff_post(handler, path, prof, person, raw):
    if path not in POSTS or not gym.enabled(prof):
        return False
    try:
        body, store = json.loads(raw), handler.cc.crm
        if path == gym_io.PATH:
            result = gym_io.run(store, prof, person, body)
        else:  # the base address is only needed (and only known) with Stripe billing
            result = gym_billing.act(store, prof, person, body,
                                     gym_join.base(handler, prof) if gym_billing.config(prof) is not None else "")
        return handler._send(200, json.dumps(result), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return _error(handler, e) or True
    except (ValueError, TypeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def connect(handler, path, q, prof, person):
    """Connect onboarding (OAuth, Standard): to Stripe, and back with the account id."""
    base = gym_join.base(handler, prof)
    if path == "/gym/billing/connect":
        return handler._send(302, "", headers={**PRIVATE, "Location": gym_billing.connect_url(handler.cc.crm, prof, person, base)})
    gym_billing.connected_back(handler.cc.crm, prof, person, {k: v[0] for k, v in q.items()}, base)
    return handler._send(302, "", headers={**PRIVATE, "Location": base + "gym#billing"})


def check_startup(prof):
    """server.make_server: gym billing refuses a live key before the server binds."""
    gym_billing.check_startup(prof)


def kiosk(handler, method, path, query, raw, prof):
    """The kiosk's routes. True when answered. The page and its script need no token; every
    /api/kiosk route except unlock needs `Authorization: Bearer <token>`."""
    if path not in gym_kiosk.PATHS or gym_kiosk.config(prof) is None:
        return False
    store = handler.cc.crm
    try:
        if method == "GET" and path == "/kiosk":
            return handler._send(200, gym_kiosk.page(prof), headers=PRIVATE) or True
        if method == "GET" and path == "/kiosk.js":
            return _asset(handler, "kiosk.js", "text/javascript; charset=utf-8") or True
        if method == "GET" and path == "/kiosk.css":  # the CRM's tokens, the gym's layout and its brand, no sign-in
            css = b"\n".join([brand.stylesheet(handler.cc.root, prof, "font_stylesheet"), (WWW / "crm.css").read_bytes(),
                              (WWW / "gym.css").read_bytes(), brand.stylesheet(handler.cc.root, prof, "theme_stylesheet")])
            return handler._send(200, css, "text/css; charset=utf-8", {"Cache-Control": "no-cache"}) or True
        if path == "/api/kiosk/unlock" and method == "POST":
            body = json.loads(raw or b"{}")
            return handler._send(200, json.dumps(gym_kiosk.unlock(store, prof, body.get("pin"))), JSON, PRIVATE) or True
        token = (handler.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not gym_kiosk.check_token(prof, token, datetime.fromisoformat(crm_store.now())):
            raise crm_store.CRMError("Locked. Enter the kiosk PIN.", 401)
        if method == "GET" and path == "/api/kiosk/now":
            return handler._send(200, json.dumps(gym_kiosk.now_classes(store, prof)), JSON, PRIVATE) or True
        if method == "GET" and path == "/api/kiosk/find":
            q = (parse_qs(query).get("q") or [""])[0]
            return handler._send(200, json.dumps(gym_kiosk.find(store, prof, q)), JSON, PRIVATE) or True
        if method == "POST" and path == "/api/kiosk/checkin":
            return handler._send(200, json.dumps(gym_kiosk.checkin(store, prof, json.loads(raw or b"{}"))), JSON, PRIVATE) or True
        return handler._send(405, json.dumps({"error": "Method not allowed"}), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return _error(handler, e) or True
    except (ValueError, TypeError, AttributeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def door(handler, method, path, raw, prof):
    """The door QR pages (gym_door.py): a phone at the gym, no sign-in; every write needs the door code."""
    if gym_door.config(prof) is None:
        return False
    store = handler.cc.crm
    try:
        if method == "GET" and path in ("/checkin", "/waiver"):
            return handler._send(200, gym_door.page(prof, path.strip("/")), headers=PRIVATE) or True
        if method == "GET" and path == "/door.js":
            return _asset(handler, "door.js", "text/javascript; charset=utf-8") or True
        if method == "GET" and path == "/door.css":
            css = b"\n".join([brand.stylesheet(handler.cc.root, prof, "font_stylesheet"), (WWW / "crm.css").read_bytes(),
                              (WWW / "gym.css").read_bytes(), brand.stylesheet(handler.cc.root, prof, "theme_stylesheet")])
            return handler._send(200, css, "text/css; charset=utf-8", {"Cache-Control": "no-cache"}) or True
        if method == "GET" and path == "/api/door/waiver":
            return handler._send(200, json.dumps(gym_door.waiver(prof)), JSON, PRIVATE) or True
        if method != "POST":
            return handler._send(405, json.dumps({"error": "Method not allowed"}), JSON, PRIVATE) or True
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ValueError("not an object")
        if path == "/api/door/find":
            result = gym_door.find(store, prof, body)
        elif path == "/api/door/checkin":
            result = gym_door.checkin(store, prof, body)
        elif path == "/api/door/waiver/sign":
            peer = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or handler.client_address[0]
            result = gym_door.sign(store, prof, body, peer, handler.headers.get("User-Agent") or "")
        else:
            return False
        return handler._send(200, json.dumps(result), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return _error(handler, e) or True
    except (ValueError, TypeError, AttributeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def door_codes(handler, prof):
    """The two printable codes for the staff screen: the links with the door code, as QR SVGs."""
    if gym_door.config(prof) is None:
        raise crm_store.CRMError("Door QR codes are not set up for this gym", 404)
    origins = (prof.get("access") or {}).get("origins") or []
    origin = origins[0] if origins else "http://" + (handler.headers.get("Host") or "localhost")
    prefix = (prof.get("crm") or {}).get("path_prefix", "/")
    code = gym_door.code(prof)
    if len(code) < 8:
        raise crm_store.CRMError("Set the door code first (gym.door.code_env, at least 8 characters)", 503)
    out = {}
    for key, title in (("checkin", "Check in"), ("waiver", "Sign the waiver")):
        if key == "waiver" and gym_door.waiver_config(prof) is None:
            continue
        url = f"{origin.rstrip('/')}{prefix}{key}?d={code}"
        out[key] = {"url": url, "svg": qr_svg.svg(url, 260, f"{prof['name']}: {title}"), "title": title}
    return {"gym": prof["name"], "codes": out}


def brand_head(prof):
    """The business's own look on a gym page: its font and theme, and the demo layer when on."""
    crm = prof.get("crm") or {}
    font = '<link rel="stylesheet" href="crm-font.css">' if crm.get("font_stylesheet") else ""
    theme = '<link rel="stylesheet" href="crm-theme.css">' if crm.get("theme_stylesheet") else ""
    demo = '<link rel="stylesheet" href="demo.css"><script defer src="demo.js"></script>' if demo_mode.config(prof) else ""
    return font, theme, demo


def logo(prof):
    return '<img class="gym-logo" src="brand-logo" alt="">' if (prof.get("crm") or {}).get("logo") else ""


def page(prof):
    """The staff screen: Today, Members, a member, Schedule, Roster, Billing, Switch, Import. One
    script, hash routes. Billing shows when billing is on (simulated in a demo, Stripe test mode in P2); the
    switch guide when the profile carries one."""
    name = html.escape(prof["name"])
    accent = (prof.get("crm") or {}).get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    prefix = html.escape((prof.get("crm") or {}).get("path_prefix", "/"), quote=True)
    font, theme, demo = brand_head(prof)
    cfg = gym.config(prof) or {}
    billing = gym_demo.billing_on(prof) or gym_billing.config(prof) is not None
    tabs = '<a href="gym#billing" data-tab="billing">Billing</a>' if billing else ""
    if isinstance(cfg.get("switch"), dict):  # "Moving from Zen Planner"; an unnamed tool is just "Moving over"
        tool = str(cfg["switch"].get("from_label") or "")
        label = "Moving over" if not tool or tool.lower().startswith("your ") else "From " + tool
        tabs += f'<a href="gym#switch" data-tab="switch">{html.escape(label)}</a>'
    crm = "" if demo_mode.config(prof) else '<a href="./">CRM</a>'
    kiosk = ('<a href="gym#checkin" data-tab="checkin">Check-in</a>' if gym_door.config(prof)
             else '<a href="kiosk">Check-in tablet</a>' if gym_kiosk.config(prof) else "")
    sub = ("Stripe test mode" if gym_billing.config(prof) is not None else
           "" if billing else "Billing not connected")
    return crm_page.version_assets(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix}">
<meta name="robots" content="noindex,nofollow"><title>{name} | Gym</title>
{font}<link rel="stylesheet" href="crm.css"><link rel="stylesheet" href="gym.css"><style>:root{{--accent:{accent}}}</style>{theme}{demo}
<script defer src="gym.js"></script></head><body class="gym"{' data-demo="1"' if demo else ''}>
<header class="gym-top"><a class="gym-brand" href="gym#today">{logo(prof)}<span><strong>{name}</strong>{f'<small>{sub}</small>' if sub else ''}</span></a>
<nav aria-label="Gym"><a href="gym#today" data-tab="today">Today</a><a href="gym#members" data-tab="members">Members</a>
<a href="gym#schedule" data-tab="schedule">Schedule</a><a href="gym#attendance" data-tab="attendance">Attendance</a>{tabs}{kiosk}<a href="gym#data" data-tab="data">Import, export</a>
{crm}</nav></header>
<main id="gym" aria-live="polite"><div class="gym-skel" aria-hidden="true"><i class="h"></i><i class="row"></i><i class="row"></i><i class="card"></i><i class="card"></i></div><p class="gym-sr">Loading</p></main>
<dialog id="gym-dialog"><form method="dialog" id="gym-form"><h2 id="gym-dialog-title"></h2><div id="gym-fields"></div>
<p id="gym-error" role="alert"></p><div class="gym-actions"><button value="cancel" type="button" id="gym-cancel">Cancel</button>
<button class="primary" value="save" id="gym-save">Save</button></div></form></dialog>
<noscript>The gym screens need JavaScript.</noscript></body></html>""")
