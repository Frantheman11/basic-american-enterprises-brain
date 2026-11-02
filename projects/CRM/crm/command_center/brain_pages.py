"""The brain's pages and its webhook route: what server.py mounts for brain.py (2026-10-20).

`/brain-repo/webhook` answers GitHub, signed with the App's webhook secret (brain.Brain.webhook).
The pages (`/brain-repo`, `/connect`, `/setup`, `/disconnect` under it) ask who you are first, like
every page, and only an admin may connect or disconnect. A form cannot send JSON, so a POST must
carry an Origin this CRM allows. Plain HTML from oauth_pages, nothing from another host.
"""
import json
from urllib.parse import parse_qs

from .brain import (COOKIE, CONNECT, DISCONNECT, PAGE, SETUP, STATE_TTL, WEBHOOK, Brain, Error)
from . import brain as _brain


def body_cap(path):
    """The largest POST body server.py reads: GitHub's push payloads on the webhook, 64 KB elsewhere."""
    return _brain.WEBHOOK_MAX if path == WEBHOOK else 64_000


def is_brain_path(path):
    return path in (PAGE, CONNECT, SETUP, DISCONNECT, WEBHOOK)


def serve(handler, method, u, raw):
    """`/brain-repo/webhook` answers GitHub (signed); the `/brain-repo` pages ask who you are first, like every
    page, and only an admin may connect or disconnect. Fails closed."""
    from . import crm_store, identity, profiles
    from .oauth_pages import _e, page
    cc, path = handler.cc, u.path.rstrip("/") or "/"
    js, private = "application/json; charset=utf-8", {"Cache-Control": "private, no-store"}
    try:
        prof = profiles.load(cc.business, cc.root)
    except Exception as e:  # noqa: BLE001
        return handler._send(500, json.dumps({"error": "profile", "message": str(e)}), js, private)
    b = Brain(cc.crm, prof)
    if path == WEBHOOK:
        if method != "POST":
            return handler._send(405, json.dumps({"error": "method not allowed"}), js, {**private, "Allow": "POST"})
        try:
            status, body = b.webhook(handler.headers, raw, _brain.settings().webhook_secret, _brain.github_client())
        except Exception as e:  # noqa: BLE001 — GitHub retries; the nightly sync catches up
            status, body = 500, {"error": "server_error", "message": type(e).__name__}
        return handler._send(status, json.dumps(body), js, private)
    who = handler._who(as_json=False)
    if who is None:
        return None
    prof, person = who
    try:
        cc.crm.authorize(prof, person)
    except crm_store.CRMError as e:
        return handler._send(403, str(e), "text/plain; charset=utf-8", private)
    admin = crm_store.Store.is_admin(prof, person)
    prefix = "/" + ((prof.get("crm") or {}).get("path_prefix") or "").strip("/")
    prefix = "" if prefix == "/" else prefix
    app = _brain.settings()

    def answer(result):
        status, headers, body = result
        headers = dict(headers)
        return handler._send(status, body, headers.pop("Content-Type"), headers)

    def view(notice="", status=200):
        st = b.status()
        rows = (f'<dt>Brain repo</dt><dd>{_e(st["configured_repo"] or "none named in the profile")}</dd>'
                f'<dt>Status</dt><dd>{"Connected" if st["connected"] else "Not connected"}</dd>'
                + (f'<dt>Copy of commit</dt><dd>{_e((st["commit"] or "")[:12])}</dd><dt>Last refreshed</dt>'
                   f'<dd>{_e((st["synced_at"] or "")[:16])}</dd>' if st["connected"] else ''))
        if st["last_error"] and not st["connected"]:
            rows += f'<dt>Last note</dt><dd>{_e(st["last_error"])}</dd>'
        action = ""
        if admin and st["configured_repo"] and app.can_connect and not st["connected"]:
            action = (f'<form method="post" action="{_e(prefix + CONNECT)}"><div class="actions">'
                      '<button class="allow">Connect your brain</button></div></form>'
                      f'<p class="muted">GitHub opens. Choose <strong>Only select repositories</strong> and pick '
                      f'<strong>{_e(st["configured_repo"])}</strong>, nothing else.</p>')
        elif admin and st["connected"]:
            action = (f'<form method="post" action="{_e(prefix + DISCONNECT)}"><div class="actions">'
                      '<button class="deny">Disconnect</button></div></form>'
                      '<p class="muted">Disconnect deletes the CRM\'s copy at once. To end GitHub access too, uninstall '
                      '"CTA Brain Reader" in GitHub, Settings, Applications.</p>')
        elif not admin:
            action = '<p class="muted">An admin connects the brain.</p>'
        elif not app.can_connect:
            action = '<p class="muted">The brain app is not set up on this server yet.</p>'
        return answer(page("Your brain", f'<p class="eyebrow">{_e(prof.get("name", "CRM"))}</p><h1>Your brain</h1>'
                           + (f'<p class="warn" role="status">{_e(notice)}</p>' if notice else '')
                           + '<p>When connected, Claude can answer from your brain repo: your rules, prices and how '
                             'you do things. It reads only. The CRM keeps a working copy on your CRM server, deleted '
                             'when you disconnect.</p>'
                           f'<dl>{rows}</dl>{action}<p><a href="{_e(prefix + "/")}">Back to the CRM</a></p>', status,
                           form_target="https://github.com"))

    if path == PAGE and method == "GET":
        return view()
    if path == SETUP and method == "GET":
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        browser = next((part.strip().partition("=")[2] for part in (handler.headers.get("Cookie") or "").split(";")
                        if part.strip().startswith(COOKIE + "=")), "")
        if not admin:
            return view("Only an admin can connect the brain.", 403)
        try:
            st = b.finish_connect(person, q.get("state"), browser, q.get("installation_id"), _brain.github_client())
        except Error as e:
            return view(str(e), e.status)
        return view("Connected. Claude can now answer from your brain." if st["connected"]
                    else f"Saved, but the first copy failed: {st['last_error']}")
    if method != "POST" or path not in (CONNECT, DISCONNECT):
        return handler._send(405 if path in (CONNECT, DISCONNECT, PAGE, SETUP) else 404, "Not allowed",
                             "text/plain; charset=utf-8", private)
    origin = handler.headers.get("Origin") or ""  # a form cannot send JSON: the Origin check is the guard
    if not origin or not identity.origin_allowed(prof, cc.mode, origin):
        return handler._send(403, "This form must be sent from the CRM's own page.", "text/plain; charset=utf-8", private)
    if not admin:
        return view("Only an admin can change the brain connection.", 403)
    if path == DISCONNECT:
        b.disconnect("disconnected in the CRM", person.id)
        return view("Disconnected. The CRM's copy of your brain is deleted.")
    if not app.can_connect:
        return view("The brain app is not set up on this server yet.", 409)
    try:
        state, browser = b.begin_connect(person)
    except Error as e:
        return view(str(e), e.status)
    secure = "; Secure" if (handler.headers.get("X-Forwarded-Proto") == "https") else ""
    return handler._send(302, b"", "text/plain; charset=utf-8",
                         {**private, "Location": app.install_url(state),
                          "Set-Cookie": f"{COOKIE}={browser}; Path={prefix}{PAGE}; Max-Age={STATE_TTL}; HttpOnly; SameSite=Lax{secure}"})
