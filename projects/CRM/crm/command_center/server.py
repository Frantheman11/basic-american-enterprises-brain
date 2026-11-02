"""The Command Center server. Stdlib only, binds loopback, one process.

    python command_center/server.py --business cta --port 8780
    python command_center/server.py --business cta --port 8780 --identity cloudflare_access

Routes
    GET  /                    the first view this person may have
    GET  /<view>              one view — overview | brain | business | agents | records | artifacts
    GET  /api/state           this person's state as JSON, ETagged (304 when nothing changed)
    GET  /api/view?view=<id>  one view's HTML fragment, ETagged — what the page polls
    GET  /brand.css           the business's own stylesheet, named by the profile
    GET  /cc.css  /cc.js      this engine's layout and poll loop
    GET  /board.css /board.js the board canvas — spine, nodes, edges, side panel
    POST /api/decide          record a decision on a draft
    POST /api/queue           put a board node on the list — a create, not an edit
    GET  /healthz             ok — the one route that does not ask who you are
    POST /api/intake/lead     with --crm-db and a profile `crm.intake` block: the website's lead
                              form. The ONLY unsigned write; it adds a lead and answers a receipt
                              (lead_intake.py). OPTIONS answers its CORS preflight.
    GET  /api/crm/research    with --crm-db: the Research pool (industry -> ring -> company)
    GET  /api/crm/leaderboard with --crm-db: ?period=week|month|all, the team board
    GET  /api/crm/research/package?place=   one lead's prep package (email, checklist, document)
    GET  /api/crm/research/printout?place=  the printout, only with a current approval
    GET  /api/crm/calls       with --crm-db and crm.calls: phone calls (?id= one call, with its transcript)
    GET  /api/crm/content     with --crm-db and crm.content: the business's posts and articles (read-only)
    GET  /api/crm/content/image?post=   one post's image
    POST /mcp                 with --crm-db: MCP for Claude and Codex, Authorization: Bearer <API key> (connect.py)
    *    /api/v1/<tool>       with --crm-db: the same tools over plain HTTP, same key; /api/v1/openapi.json is the spec
    *    /oauth/*, /.well-known/oauth-*   the Claude apps' sign-in (oauth.py)
    *    /gym*, /kiosk*       with gym.enabled: the gym screens and the check-in kiosk (gym_routes.py)
    *    /payroll-desk*       with payroll_desk.enabled: the Prevailing Wage Desk (payroll_desk_routes.py)
    *    /quotes, /api/quotes* with quote_desk.enabled: the Quote Desk for a machine shop (quote_desk_routes.py)

It binds loopback ONLY and sends `noindex`. This reads a whole repo, including memory and
client folders — it is an operator tool, not a public page. A non-loopback `--host` is
refused at startup; behind a tunnel, the tunnel is the only way in.

## Who is asking (CRM B1)

Every request except `/healthz` resolves the person FIRST (`identity.resolve`): 401 with
no proof, 403 for a sign-in that is nobody this business named, and either way the answer
renders nothing. The person then decides which views exist (a view outside the role
answers 404) and which records any view is built from (`access.can_see`, inside the
state). Every call from here into `state` or `records` passes `person=`, and a structural
test parses this file to prove none is missing. `by` in a request body is ignored: a
write's provenance is the verified person, built inside `records.py`.

State, view and page responses are `Cache-Control: private, no-store`, because each one
is one person's view of the records.
"""

import argparse
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_center import VERSION, identity, page, profiles, records, state, crm_store, crm_io, crm_page, research, leaderboard, research_package, research_claims, lead_intake, content, call_log, api_keys, connect, mcp, oauth, brain_pages, gym_routes, demo_mode, brand  # noqa: E402
from command_center import order_desk_routes, payroll_desk_routes, quote_desk_routes  # noqa: E402

# Each serves only when its profile flag is on (gym, order_desk, payroll_desk, quote_desk: .enabled).
DESKS = (gym_routes, order_desk_routes, payroll_desk_routes, quote_desk_routes)

WWW = Path(__file__).resolve().parent / "www"

TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml"}

STRATEGY_ASSETS = ("/strategy.css", "/strategy.js", "/strategy-art.js", "/strategy-help.js", "/strategy-help.css", "/strategy-company.js", "/strategy-company.css", "/strategy-products.js", "/strategy-products.css", "/strategy-handoffs.js", "/strategy-handoffs.css", "/strategy-activity.js", "/strategy-activity.css", "/strategy-delivery.js", "/strategy-delivery.css", "/strategy-payments.js", "/strategy-payments.css", "/strategy-connections.js")
# One person's view of the records: never kept by a browser or a proxy.
PRIVATE = {"Cache-Control": "private, no-store"}


class Config:
    """What one server instance serves: one business, one root, one identity mode.

    ⚠️ Carried on the server object, not in module globals. With globals, a second server
    started in the same process (the tests start several) silently re-pointed the first
    one at the second one's business. Harmless while every server read one profile; not
    once two profiles name different people.
    """

    def __init__(self, business, root, mode, crm=None, intake=None):
        self.business, self.root, self.mode = business, root, mode
        self.crm = crm
        self.limiter = api_keys.RateLimit()  # per key, for the machine routes (connect.py)
        # The website lead intake: None unless the profile has a `crm.intake` block.
        self.intake, self.intake_limit = intake, lead_intake.RateLimit()


class Handler(BaseHTTPRequestHandler):

    server_version = f"CommandCenter/{VERSION}"

    @property
    def cc(self):
        return self.server.cc

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # ---- plumbing --------------------------------------------------------------
    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

    def _static(self, name):
        f = (WWW / name).resolve()
        if WWW.resolve() not in f.parents or not f.is_file():
            return self._send(404, "not found", "text/plain; charset=utf-8")
        return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"),
                          {"Cache-Control": "no-cache"})

    def _brand_css(self, prof):
        """The business's own stylesheet, wherever the profile says it is. The engine
        never names a brand file, which is what lets a second instance look like itself."""
        f = Path(self.cc.root) / prof["stylesheet"]
        if not f.is_file():
            return self._send(404, f"/* no stylesheet at {prof['stylesheet']} */", TYPES[".css"])
        return self._send(200, f.read_bytes(), TYPES[".css"], {"Cache-Control": "no-cache"})

    def _printout(self, prof, business_id):
        """The profile names the printout renderer (crm.research.printout): a Python file in
        the repository with `render(business_id, root)`. The engine names no file."""
        import importlib.util
        name = ((prof.get("crm") or {}).get("research") or {}).get("printout")
        root = research.data_root(prof, self.cc.root).resolve()
        f = (root / name).resolve() if isinstance(name, str) and name else None
        if f is None or root not in f.parents or f.suffix != ".py" or not f.is_file():
            raise crm_store.CRMError("No printout is configured for this business", 404)
        spec = importlib.util.spec_from_file_location("cc_printout", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.render(business_id, str(root))

    def _who(self, as_json):
        """(profile, person) for this request, or None after sending the refusal.

        A refusal carries the reason and nothing else: no view, no name, no count.
        """
        def refuse(code, msg):
            if as_json:
                self._send(code, json.dumps({"error": msg}), TYPES[".json"], PRIVATE)
            else:
                self._send(code, msg + "\n", "text/plain; charset=utf-8", PRIVATE)
            return None
        try:
            prof = profiles.load(self.cc.business, self.cc.root)
        except Exception as e:  # a broken profile should say so, not 500 blankly
            return refuse(500, f"profile error: {e}")
        try:
            person = identity.resolve(self.headers, self.client_address, prof,
                                      mode=self.cc.mode)
        except identity.Unauthenticated as e:
            return refuse(401, f"Not signed in: {str(e).rstrip('.')}.")
        except identity.NotAPerson as e:
            return refuse(403, f"Signed in, but not let in: {str(e).rstrip('.')}.")
        except Exception as e:  # noqa: BLE001 — a check that breaks is a closed door
            return refuse(500, f"the sign-in check failed ({type(e).__name__}), so nothing "
                               "is shown")
        return prof, person

    def _if_none_match(self):
        return (self.headers.get("If-None-Match") or "").strip().strip('"')

    # ---- routes ----------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"

        # The only route that answers without knowing who is asking. It says "ok" and
        # nothing else, so a monitor can watch the process without a sign-in.
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if demo_mode.serve(self, "GET", u, b"") or brand.serve(self, path):
            return
        if self._machine_on("/mcp") and (oauth.is_oauth_path(path) or brain_pages.is_brain_path(path)):
            return (oauth if oauth.is_oauth_path(path) else brain_pages).serve(self, "GET", u, b"")
        if self._machine_on(path):
            return self._machine("GET", u, b"")

        if any(d.serve(self, u, b"") for d in DESKS):
            return
        who = self._who(as_json=path.startswith("/api/"))
        if who is None:
            return
        prof, person = who

        if self.cc.crm is not None:
            try:
                self.cc.crm.authorize(prof, person)
                from command_center import receptionist_admin
                prof, admin_page = receptionist_admin.gate(self.cc.crm, prof, person, path)
                if admin_page:
                    return self._send(200, admin_page[1], TYPES[".json"] if admin_page[0] == "json" else "text/html; charset=utf-8", PRIVATE)
                if path in ('/task-editor.js', '/task-editor.css', '/crm-sync.js', '/record-form.js', '/company-editor.js', '/task-create.js', '/workspace-views.js', '/workspace-design.js', '/workspace-design.css', '/workspace-search.js', '/workspace-search.css', '/workspace-daily.js', '/workspace-daily.css', '/workspace-actions.js', '/workspace-shell.js', '/workspace-shell.css', '/workspace-favorites.js', '/workspace-bulk.js', '/crm-io.js'):
                    return self._static(path.lstrip('/'))
                if not getattr(self.cc.crm, 'readonly', False):
                    if path in ('/api/crm/search', '/api/crm/search-note'):
                        from command_center import workspace_search
                        qs = parse_qs(u.query)
                        if path.endswith('/search-note'):
                            body = workspace_search.note(self.cc.crm, prof, person, qs.get('id', [''])[0])
                        else:
                            body = workspace_search.search(self.cc.crm, prof, person,
                                qs.get('q', [''])[0], qs.get('type', ['all'])[0],
                                qs.get('archived', ['0'])[0], qs.get('offset', ['0'])[0])
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path == '/api/crm/daily':
                        from command_center import workspace_daily
                        body = workspace_daily.read(self.cc.crm, prof, person)
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path == '/api/crm/preferences':
                        from command_center import workspace_preferences
                        body = workspace_preferences.read(self.cc.crm, prof, person)
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path == '/api/crm/task-options':
                        from command_center import task_work
                        body = task_work.options(self.cc.crm, prof, person, (parse_qs(u.query).get('contact_id') or [''])[0])
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path == '/api/crm/company':
                        from command_center import company_work
                        body = company_work.read(self.cc.crm, prof, person, (parse_qs(u.query).get('id') or [''])[0])
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path == '/api/crm/task':
                        from command_center import task_work
                        body = task_work.read(self.cc.crm, prof, person, (parse_qs(u.query).get('id') or [''])[0])
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                    if path in ('/api/crm/events', '/api/crm/revision'):
                        from command_center import crm_updates
                        if path.endswith('/events'):
                            return crm_updates.stream(self, prof, person)
                        body = {'revision': crm_updates.revision(self.cc.crm, prof, person)}
                        return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                if getattr(self.cc.crm, 'readonly', False):
                    if path == '/':
                        return self._send(302, '', headers={**PRIVATE, 'Location': self.cc.crm.url + ('?' + u.query if u.query else '')})
                    if path not in ('/strategy', '/api/crm/strategy', '/crm-font.css', *STRATEGY_ASSETS):
                        return self._send(404, json.dumps({'error': 'Use the live CRM for this view'}), TYPES['.json'], PRIVATE)
                if path in ("/crm.css", "/crm.js", *STRATEGY_ASSETS):
                    return self._static(path.lstrip("/"))
                if path == '/api/crm/strategy':
                    from command_center import strategy
                    body = strategy.snapshot(self.cc.crm, prof, person)
                    if self.cc.mode == 'cloudflare_access':
                        body['connection']['mode'] = 'live_hosted'
                    return self._send(200, json.dumps(body), TYPES['.json'], PRIVATE)
                if path == '/strategy':
                    from command_center import strategy, strategy_page
                    strategy.config(prof)
                    return self._send(200, strategy_page.render(prof, self.cc.mode == 'local', getattr(self.cc.crm, 'readonly', False)), headers=PRIVATE)
                if path == "/crm-font.css":
                    return brand.serve_stylesheet(self, prof)
                if path == "/crm-theme.css":
                    return brand.serve_stylesheet(self, prof, "theme_stylesheet")
                if path == "/api/crm":
                    return self._send(200, json.dumps(self.cc.crm.snapshot(prof, person)), TYPES[".json"], PRIVATE)
                if path == "/api/crm/leaderboard":
                    # The whole team sees the same board (Sam, 2026-09-23).
                    period = (parse_qs(u.query).get("period") or ["month"])[0]
                    try:
                        at = datetime.fromisoformat(crm_store.now())
                        body = leaderboard.build(self.cc.crm, prof, person, period, at)
                    except leaderboard.LeaderboardUnavailable as e:
                        return self._send(404, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)
                    return self._send(200, json.dumps(body), TYPES[".json"], PRIVATE)
                if path in ("/api/crm/research/package", "/api/crm/research/printout"):
                    pid = (parse_qs(u.query).get("place") or [""])[0]
                    try:
                        if path.endswith("/package"):
                            body = research_package.read(self.cc.crm, prof, person, self.cc.root, pid)
                            return self._send(200, json.dumps(body), TYPES[".json"], PRIVATE)
                        # A printout renders only with a current approval (brief §6 Q3).
                        bid = research_package.approved_business(self.cc.crm, prof, self.cc.root, pid)
                        return self._send(200, self._printout(prof, bid), headers=PRIVATE)
                    except research.ResearchUnavailable as e:
                        return self._send(404, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)
                if path in ("/api/crm/content", "/api/crm/content/image"):
                    # Read-only: approvals and "posted" are written where the content state lives.
                    try:
                        if path.endswith("/image"):
                            pid = (parse_qs(u.query).get("post") or [""])[0]
                            data, kind = content.image(prof, self.cc.root, pid)
                            return self._send(200, data, kind, PRIVATE)
                        return self._send(200, json.dumps(content.snapshot(prof, self.cc.root)), TYPES[".json"], PRIVATE)
                    except content.ContentUnavailable as e:
                        return self._send(404, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)
                if path == "/api/crm/calls":
                    # Phone calls from the AI receptionist (call_log.py). Off without crm.calls.
                    if call_log.config(prof) is None:
                        return self._send(404, json.dumps({"error": "Calls are not set up"}), TYPES[".json"], PRIVATE)
                    cid = (parse_qs(u.query).get("id") or [""])[0]
                    body = (call_log.one(self.cc.crm, prof, person, cid) if cid
                            else call_log.listing(self.cc.crm, prof, person))
                    return self._send(200, json.dumps(body), TYPES[".json"], PRIVATE)
                if path == "/api/crm/research":
                    # Neutral: every person who may read the CRM sees the same pool.
                    try:
                        body = research.snapshot(prof, self.cc.root)
                        claims = self.cc.crm.research_claims(prof, person)
                        body.update(claims)
                        body.update(self.cc.crm.research_requests(prof, person))
                        research_claims.prune(body, claims)
                        size = ((prof.get("crm") or {}).get("research") or {}).get("up_next")
                        research.up_next(body, claims, size if type(size) is int and size > 0 else 20,
                                         prof, self.cc.root)
                    except research.ResearchUnavailable as e:
                        return self._send(404, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)
                    return self._send(200, json.dumps(body), TYPES[".json"], PRIVATE)
                if path == "/":
                    return self._send(200, crm_page.render(prof, preview=self.cc.mode == "local"), headers=PRIVATE)
                return self._send(404, "Not found", "text/plain", PRIVATE)
            except crm_store.CRMError as e:
                return self._send(e.status, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)

        if path in ("/cc.css", "/cc.js", "/board.css", "/board.js"):
            return self._static(path.lstrip("/"))
        if path == "/brand.css":
            return self._brand_css(prof)

        try:
            st = state.get(self.cc.business, self.cc.root, person=person)
        except Exception as e:
            return self._send(500, f"<pre>{type(e).__name__}: {e}</pre>", headers=PRIVATE)

        tagged = {"ETag": f'"{st["etag"]}"', **PRIVATE}
        if path == "/api/state":
            if self._if_none_match() == st["etag"]:
                return self._send(304, b"", TYPES[".json"], tagged)
            return self._send(200, state.as_json(st), TYPES[".json"], tagged)

        if path == "/api/view":
            vid = (parse_qs(u.query).get("view") or [st["order"][0] if st["order"] else ""])[0]
            # A view outside this person's role is not in their state at all, so it gets
            # the same 404 as a view that does not exist.
            if vid not in st["views"]:
                return self._send(404, json.dumps({"error": "unknown view"}), TYPES[".json"],
                                  PRIVATE)
            if self._if_none_match() == st["etag"]:
                return self._send(304, b"", TYPES[".html"], tagged)
            return self._send(200, page.view_html(st, vid), TYPES[".html"], tagged)

        if not st["order"]:
            return self._send(404, "This role has no views.\n", "text/plain; charset=utf-8",
                              PRIVATE)
        vid = st["order"][0] if path == "/" else path.lstrip("/")
        if vid not in st["views"]:
            return self._send(404, page.render(st, st["order"][0]), TYPES[".html"], PRIVATE)
        return self._send(200, page.render(st, vid), TYPES[".html"], PRIVATE)

    # ---- the public lead intake ---------------------------------------------------
    def _intake_on(self, path):
        return self.cc.crm is not None and self.cc.intake is not None and path == lead_intake.PATH

    # ---- the program doors: /mcp and /api/v1 (connect.py) ---------------------------------
    def _machine_on(self, path):
        return self.cc.crm is not None and not getattr(self.cc.crm, 'readonly', False) and connect.is_machine_path(path)

    def _machine(self, method, u, raw):
        """A program signs in with an API key, or on /mcp with a Claude app's OAuth token (oauth.py),
        never with a person's sign-in and never from a page. Bypassed at the edge, so this is the
        whole check. It fails closed like `_who`."""
        path = u.path.rstrip("/") or "/"

        def refuse(status, msg):
            code = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}.get(status, "refused")
            extra = {"WWW-Authenticate": door.challenge("invalid_token") if door else 'Bearer realm="cta-crm"'} if status == 401 else {}
            return self._send(status, json.dumps({"error": code, "message": msg}), TYPES[".json"], {**PRIVATE, **extra})

        try:
            prof = profiles.load(self.cc.business, self.cc.root)
        except Exception as e:  # noqa: BLE001
            return self._send(500, json.dumps({"error": "profile", "message": str(e)}), TYPES[".json"], PRIVATE)
        door = oauth.door(prof) if path == connect.MCP_PATH else None  # /mcp: a key or an OAuth token
        if self.headers.get("Origin") and self.headers.get("Origin") not in oauth.origins(door):
            return refuse(403, "the program routes answer programs, not pages")
        try:
            grant = oauth.sign_in(self.headers, prof, self.cc.crm, self.cc.limiter, door)
        except api_keys.Refused as e:
            return refuse(e.status, str(e))
        except Exception as e:  # noqa: BLE001 — a check that breaks is a closed door
            return self._send(500, json.dumps({"error": "sign_in_check", "message": f"the key check failed ({type(e).__name__})"}),
                              TYPES[".json"], PRIVATE)
        try:
            self.cc.crm.authorize(prof, grant.person)
        except crm_store.CRMError as e:
            return refuse(403, str(e))
        conn = connect.Connector(self.cc.crm, prof)
        if path == connect.MCP_PATH:
            status, headers, body = mcp.handle(method, self.headers, raw, conn.mcp_server(grant), oauth.origins(door))
            ctype = headers.pop("Content-Type", TYPES[".json"])
            return self._send(status, body, ctype, {**PRIVATE, **headers})
        status, body = conn.http(method, path, u.query, raw, grant)
        return self._send(status, json.dumps(body), TYPES[".json"], PRIVATE)

    def do_OPTIONS(self):
        """The CORS preflight, for the intake route only. Anything else: 404, as before."""
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._intake_on(path):
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        cors = lead_intake.cors_headers(self.cc.intake, self.headers.get("Origin"))
        return self._send(204 if cors else 403, b"", "text/plain; charset=utf-8", cors)

    def _intake(self, raw, too_big=None):
        """The one write that does not ask who you are. It can add a lead and nothing else:
        the answer is a receipt (lead_id, notification), never a record or a list."""
        cfg, store = self.cc.intake, self.cc.crm
        cors = lead_intake.cors_headers(cfg, self.headers.get("Origin"))

        def reply(code, body):
            return self._send(code, json.dumps(body), TYPES[".json"], {**PRIVATE, **cors})
        if too_big:
            return reply(413, {"error": f"A lead is at most {cfg['max_bytes']} bytes"})
        if not cors:
            return reply(403, {"error": "This website may not send leads here"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return reply(415, {"error": "Leads require Content-Type: application/json"})
        key = (self.headers.get("Idempotency-Key") or "").strip()
        if not key or len(key) > 80:
            return reply(400, {"error": "Idempotency-Key is required"})
        try:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return reply(400, {"error": "The lead is not valid JSON"})
            if lead_intake.is_spam(body, cfg):
                # Looks like a success to the bot; nothing is written.
                return reply(202, {"lead_id": str(uuid4()), "notification": "queued"})
            lead = lead_intake.validate(body, key, cfg)
            # A retry of a saved lead is answered before the rate limit is asked.
            found = lead_intake.existing(store, key)
            if found:
                return reply(409, found)
            address = lead_intake.client_address(cfg, self.headers, self.client_address[0])
            if not self.cc.intake_limit.allow(address, cfg["rate_requests"], cfg["rate_window"]):
                return reply(429, {"error": "Too many requests. Please call us or try again later."})
            status, receipt = lead_intake.receive(store, profiles.load(self.cc.business, self.cc.root),
                                                  cfg, lead, raw.decode("utf-8"))
            return reply(status, receipt)
        except lead_intake.Invalid as e:
            return reply(e.status, {"error": str(e)})
        except Exception:  # noqa: BLE001 — the website shows its retry message
            return reply(500, {"error": "The lead was not saved. Please retry."})

    # ---- POST: the only write ---------------------------------------------------
    def do_POST(self):
        # Read the body FIRST, before the route check and before any refusal. Answering
        # without draining it leaves the connection out of sync on a keep-alive socket,
        # and the client sees a broken socket instead of the status that explains what
        # was wrong. ⚠️ This was already true of the 403 path and the drain was put in for
        # it — but the 404 return sat ABOVE the drain, so an unknown POST route had the
        # bug the comment was written about. It surfaced as a test that failed about one
        # run in four: intermittent, because it depends on whether the connection is
        # reused. Every early return in this method must be below this block.
        # The public lead intake (lead_intake.py) has its own, smaller size limit, answers 413
        # with CORS headers, and never reaches the sign-in below. Every other POST is unchanged.
        path = urlparse(self.path).path.rstrip("/") or "/"
        intake = self._intake_on(path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > (self.cc.intake["max_bytes"] if intake else crm_io.body_cap(path)):
                self.close_connection = True  # never read an oversize body
                raise ValueError("body too large")
            raw = self.rfile.read(n)
        except ValueError as e:
            if intake:
                return self._intake(b"", too_big=True)
            return self._send(400, json.dumps({"error": str(e)}), TYPES[".json"])
        if demo_mode.serve(self, "POST", urlparse(self.path), raw):
            return
        if getattr(self.cc.crm, 'readonly', False):
            feedback_route = path == '/api/crm/feedback' and self.cc.crm.feedback_enabled
            company_route = path == '/api/crm/company-action' and self.cc.crm.company_actions
            payment_route = path == '/api/crm/payment' and self.cc.crm.payments_enabled
            if feedback_route or company_route or payment_route:
                who = self._who(as_json=True)
                if who is None:
                    return
                prof, person = who
                host = self.headers.get('Host', '')
                origin = self.headers.get('Origin', '')
                allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
                if host not in allowed_hosts or origin != 'http://' + host or self._reject_cross_origin(prof):
                    return self._send(403, json.dumps({'error':'Save from this command center tab.'}), TYPES['.json'], PRIVATE)
                try:
                    handler = self.cc.crm.submit_payment if payment_route else self.cc.crm.submit_feedback if feedback_route else self.cc.crm.submit_company
                    result = handler(prof, person, json.loads(raw))
                    return self._send(200, json.dumps(result), TYPES['.json'], PRIVATE)
                except crm_store.CRMError as error:
                    return self._send(error.status, json.dumps({'error':str(error)}), TYPES['.json'], PRIVATE)
                except (ValueError, TypeError):
                    return self._send(400, json.dumps({'error':'Invalid submission'}), TYPES['.json'], PRIVATE)
                except Exception:
                    return self._send(503, json.dumps({'error':'The save could not be confirmed. Retry the same request.'}), TYPES['.json'], PRIVATE)
            return self._send(403, json.dumps({'error': 'Read-only live view. Make changes in the live CRM.'}), TYPES['.json'], PRIVATE)
        if intake:
            return self._intake(raw)
        if self._machine_on("/mcp") and (oauth.is_oauth_path(path) or brain_pages.is_brain_path(path)):
            return (oauth if oauth.is_oauth_path(path) else brain_pages).serve(self, "POST", urlparse(self.path), raw)
        if self._machine_on(path):
            return self._machine("POST", urlparse(self.path), raw)
        if any(d.serve(self, urlparse(self.path), raw) for d in DESKS):
            return

        # Who, before what: an unsigned request learns nothing, not even which routes exist.
        who = self._who(as_json=True)
        if who is None:
            return
        prof, person = who

        if self.cc.crm is not None:
            bad = self._reject_cross_origin(prof)
            if bad:
                return self._send(403, json.dumps({"error": bad}), TYPES[".json"], PRIVATE)
            if path in crm_io.PATHS:
                return crm_io.serve(self, path, prof, person, raw)
            if path != "/api/crm/command":
                return self._send(404, json.dumps({"error": "Not found"}), TYPES[".json"], PRIVATE)
            try:
                result = self.cc.crm.execute(prof, person, json.loads(raw))
                return self._send(200, json.dumps(result), TYPES[".json"], PRIVATE)
            except crm_store.CRMError as e:
                return self._send(e.status, json.dumps({"error": str(e)}), TYPES[".json"], PRIVATE)
            except (ValueError, TypeError):
                return self._send(400, json.dumps({"error": "Invalid command data"}), TYPES[".json"], PRIVATE)
            except Exception:
                return self._send(500, json.dumps({"error": "CRM could not save. Your command can be retried."}), TYPES[".json"], PRIVATE)

        if path not in ("/api/decide", "/api/queue"):
            return self._send(404, json.dumps({"error": "not found"}), TYPES[".json"])

        bad = self._reject_cross_origin(prof)
        if bad:
            return self._send(403, json.dumps({"error": bad}), TYPES[".json"])

        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, json.dumps({"error": f"bad body: {e}"}), TYPES[".json"])

        # ⚠️ No `by` is read from the body. Provenance is the verified person, and
        # records.py builds it from `person=`, so nothing a client sends can change it.
        try:
            if path == "/api/queue":
                # Putting a board node on the list. A second ROUTE, never a second writer:
                # it goes through records.py like everything else, so one-writer-per-field
                # still holds and a test can still assert no view touches disk.
                written = records.queue_add(
                    self.cc.business, body.get("node"), body.get("label"), body.get("source"),
                    root=self.cc.root, why=body.get("why") or "", kind=body.get("kind") or "",
                    person=person)
            else:
                written = records.decide(
                    self.cc.business, body.get("record"), body.get("id"), body.get("decision"),
                    root=self.cc.root, person=person)
        except records.WriteRefused as e:
            # Refusals are the point of the module, so they come back verbatim: the
            # message names the field and says who owns it.
            return self._send(403, json.dumps({"error": str(e)}), TYPES[".json"])
        except Exception as e:  # noqa: BLE001 — never 500 silently on a write
            return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}), TYPES[".json"])

        state.invalidate(self.cc.business, self.cc.root)
        return self._send(200, json.dumps({"ok": True, "wrote": written}), TYPES[".json"])

    def _reject_cross_origin(self, prof):
        """Two cheap guards against a page elsewhere making a browser write for it.

        Requiring a JSON content type blocks the simple-form version outright, and checking
        Origin blocks the scripted version, because a cross-origin fetch cannot forge it.
        In local mode the origin must be loopback; behind the tunnel it must be one of the
        profile's `access.origins`. Neither guard is authentication. That is `_who`.
        """
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "writes require Content-Type: application/json"
        origin = self.headers.get("Origin")
        if origin and not identity.origin_allowed(prof, self.cc.mode, origin):
            return f"cross-origin write refused from {origin}"
        return None


def make_server(business="cta", root=".", host="127.0.0.1", port=0, identity_mode=None, crm_db=None, crm_remote=None):
    """A bound, unstarted server. Used by main() and by the route gate in the tests.

    Refuses, before binding anything: a broken profile, a non-loopback host, local mode
    with no local person, cloudflare_access mode with no team domain, no AUD tag or no
    signature library. Loudly here, rather than on the first request.
    """
    prof = profiles.load(business, root)
    mode = identity.check_startup(prof, host, override=identity_mode)
    if crm_remote and (crm_db or mode != 'local'):
        raise identity.StartupRefused('Remote observation is local-only and cannot use a replica database')
    store = crm_store.Store(crm_db, business) if crm_db else None
    demo_mode.check_startup(mode, store)
    gym_routes.check_startup(prof)  # gym billing: test keys only (P2)
    if crm_remote:
        from command_center.remote_crm import RemoteStore
        store = RemoteStore(business, crm_remote)
    try:
        intake = lead_intake.config(prof) if store and not crm_remote else None
    except ValueError as e:
        raise identity.StartupRefused(f"lead intake: {e}") from None
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.cc = Config(business, root, mode, store, intake)
    return srv


def main():
    ap = argparse.ArgumentParser(description="Serve one business's command center.")
    ap.add_argument("--business", default="cta")
    ap.add_argument("--root", default=".")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--identity", choices=identity.MODES, default=None,
                    help="override the profile's access.identity for this one instance")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--crm-db", help="Opt-in CRM workspace database. Does not import or write legacy JSON records.")
    a = ap.parse_args()
    try:
        srv = make_server(a.business, a.root, a.host, a.port, identity_mode=a.identity, crm_db=a.crm_db)
    except identity.StartupRefused as e:
        raise SystemExit(f"command center: {e}")
    prof = profiles.load(a.business, a.root)
    local = (prof.get("access") or {}).get("local_person")
    print(f"Command Center engine v{VERSION}")
    print(f"  business   {prof['name']} ({a.business})")
    print(f"  profile    {prof['_path']}")
    print(f"  reading    {profiles.instance_root(prof, a.root)}")
    print(f"  sign-in    {srv.cc.mode}" + (f" (as {local})" if srv.cc.mode in ("local", "demo") else ""))
    print(f"  serving    http://{a.host}:{srv.server_address[1]}/")
    if srv.cc.intake:
        print(f"  intake     POST {lead_intake.PATH} from {', '.join(srv.cc.intake['origins'])} (public)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
