"""Prevailing Wage Desk routes, called from server.py right after the gym's. Nothing here answers unless
the profile says `payroll_desk.enabled`: otherwise the caller carries on and the path is a 404.
Every route signs in first, exactly as server.py would, then checks the role (payroll_desk_store.py).
"""
import json
from pathlib import Path
from urllib.parse import parse_qs

from . import crm_store, payroll_desk, payroll_desk_page, payroll_desk_store as pws, profiles

WWW = Path(__file__).resolve().parent / "www"
JSON = "application/json; charset=utf-8"
PRIVATE = {"Cache-Control": "private, no-store"}
GETS = ("/payroll-desk", "/payroll-desk.css", "/payroll-desk.js", "/payroll-desk/packet", "/payroll-desk/staging.csv",
        "/api/payroll-desk/state")
POSTS = ("/api/payroll-desk/import", "/api/payroll-desk/clear", "/api/payroll-desk/review", "/api/payroll-desk/filed")


def serve(handler, u, raw):
    """server.py's one call, GET and POST. True when a desk route answered."""
    store = handler.cc.crm
    path = u.path.rstrip("/") or "/"
    if store is None or getattr(store, "readonly", False) or path not in GETS + POSTS:
        return False
    try:
        prof = profiles.load(handler.cc.business, handler.cc.root)
    except Exception:  # noqa: BLE001  a broken profile is the sign-in's error to report
        return False
    if not payroll_desk.enabled(prof):
        return False
    if path in ("/payroll-desk.css", "/payroll-desk.js"):
        ctype = "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"
        return handler._send(200, (WWW / path[1:]).read_bytes(), ctype, {"Cache-Control": "no-cache"}) or True
    who = handler._who(as_json=path.startswith("/api/"))
    if who is None:
        return True
    prof, person = who
    try:
        if handler.command == "POST":
            if path not in POSTS:
                return False
            bad = handler._reject_cross_origin(prof)
            if bad:
                return handler._send(403, json.dumps({"error": bad}), JSON, PRIVATE) or True
            return handler._send(200, json.dumps(post(store, prof, person, path, raw)), JSON, PRIVATE) or True
        if path not in GETS:
            return False
        q = parse_qs(u.query)
        project = (q.get("project") or [""])[0]
        if path == "/payroll-desk":
            return handler._send(200, payroll_desk_page.desk(prof, pws.state(store, prof, person)), headers=PRIVATE) or True
        if path == "/payroll-desk/packet":
            view, p = pws.packet(store, prof, person, project)
            return handler._send(200, payroll_desk_page.packet_page(prof, view, p), headers=PRIVATE) or True
        if path == "/payroll-desk/staging.csv":
            body, name = pws.staging(store, prof, person, project)
            return handler._send(200, body, "text/csv; charset=utf-8",
                                 {**PRIVATE, "Content-Disposition": f'attachment; filename="{name}"'}) or True
        return handler._send(200, json.dumps(pws.state(store, prof, person)), JSON, PRIVATE) or True
    except crm_store.CRMError as e:
        return handler._send(e.status, json.dumps({"error": str(e)}), JSON, PRIVATE) or True
    except (ValueError, TypeError, AttributeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), JSON, PRIVATE) or True


def post(store, prof, person, path, raw):
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise crm_store.CRMError("Invalid request")
    if path == "/api/payroll-desk/import":
        return pws.import_file(store, prof, person, body.get("kind"), body.get("text"), body.get("name", ""))
    if path == "/api/payroll-desk/clear":
        return pws.clear(store, prof, person, body.get("key"), body.get("note"))
    if path == "/api/payroll-desk/review":
        return pws.officer_review(store, prof, person, body.get("project_id"), body.get("title", ""))
    return pws.record_filing(store, prof, person, body.get("project_id"), body.get("filed_on"))
