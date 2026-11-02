"""The OAuth door: how claude.ai, Claude Desktop and the Claude phone app sign in (2026-10-17).

Claude Code and Codex send an API key (`api_keys.py`). The Claude apps cannot: a person adds the
CRM's address as a custom connector, signs in, presses Allow, and Claude gets a token. This file is
that sign-in, an OAuth 2.1 authorization server for exactly one resource, this CRM's `/mcp`.
Decided 2026-10-17 (`docs/crm/crm-oauth-door-design-2026-10-17.md`, option B): the door lives in the
engine, so every client CRM carries it, and every secret stays in that client's own database.

Same rules as the Social Studio's connector (docs/content/social-studio-mcp-connector-2026-10-10.md):

    discovery    RFC 9728 (protected resource) + RFC 8414 (authorization server), path-inserted
    clients      dynamic registration (RFC 7591); public ("none") or a client secret
    codes        PKCE S256 only, 5 minutes, once; `resource` must name this /mcp (RFC 8707)
    tokens       access 1 hour; refresh 30 days idle, 90 days from Allow at most, rotated on use
    revoke       one row (`mcp_connections`) per Allow; removing it kills both tokens at once
    scopes       crm:read, crm:write and brain:read, the API key's three. Nothing else, ever.
                 Until 2026-10-20 it was the two CRM scopes; brain:read is its own consent box so a
                 person can grant the CRM without the brain (brief crm-assistant/03). It is offered and
                 advertised only when the profile names a brain repo (`crm.brain.repo`)

Who the person is comes from the CRM's own sign-in. `/oauth/authorize` is NOT a public path: it
sits behind Cloudflare Access like every page, so `identity.resolve` names the person before the
consent page renders. Only discovery, `/oauth/register`, `/oauth/token` and `/oauth/revoke` are
bypassed at the edge (`deploy/cloudflare_access.py bypass --oauth`), and none of them can sign
anybody in on its own.

A token acts AS the person who pressed Allow, with what their role can do on the screen and
nothing more. `authenticate` returns the same `api_keys.Grant` a key does, so `connect.py` and
every tool are unchanged. `key_id` is the connection id, so its activity lands in `api_key_events`.

What the database keeps: SHA-256 hashes of every token, code, client secret and consent handle,
never the secret itself. Framework-free like `mcp.py`: functions take parsed input and return
(status, headers, body), and `server.py` mounts them.

Opt-in per profile: `crm.oauth = {"enabled": true}`. The public address is `crm.oauth.origin` or
else the first `access.origins`, plus `crm.path_prefix`. No origin: the door stays shut (404).
"""
import base64
import hashlib
import json
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from uuid import uuid4

from . import api_keys
from .oauth_pages import _consent_html, apps_page, page, problem

SCOPES = api_keys.SCOPES
ACCESS_PREFIX, REFRESH_PREFIX = "cta.at.", "cta.rt."   # never "cta_": that is an API key
ACCESS_TTL = 3600
REFRESH_IDLE = timedelta(days=30)
REFRESH_MAX = timedelta(days=90)
CODE_TTL = 300
CONSENT_TTL = 600
CLIENT_IDLE = timedelta(days=90)
CLIENT_ORIGINS = ("https://claude.ai", "https://claude.com")  # may call /mcp with an Origin
REGISTER_LIMIT = (30, 60)  # registrations per minute, per process
COOKIE = "cta_consent"

AUTHORIZE, TOKEN, REGISTER, REVOKE, APPS = "/oauth/authorize", "/oauth/token", "/oauth/register", "/oauth/revoke", "/oauth/apps"
PRM, ASM = "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"


def now():
    return datetime.now(timezone.utc)


def stamp(at=None):
    return (at or now()).isoformat(timespec="microseconds")


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def migrate(db):
    """Version 23: Claude apps signed in by OAuth, one row per Allow."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, redirect_uris TEXT NOT NULL,
            auth_method TEXT NOT NULL, secret_hash TEXT, created_at TEXT NOT NULL, last_used_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mcp_connections (
            id TEXT PRIMARY KEY, person_id TEXT NOT NULL, client_id TEXT NOT NULL, client_name TEXT NOT NULL,
            redirect_host TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL,
            last_used_at TEXT, revoked_at TEXT, revoked_by TEXT);
        CREATE INDEX IF NOT EXISTS mcp_connections_person ON mcp_connections(person_id, created_at);
        CREATE TABLE IF NOT EXISTS oauth_consents (
            handle_hash TEXT PRIMARY KEY, person_id TEXT NOT NULL, client_id TEXT NOT NULL,
            request TEXT NOT NULL, browser_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
            done_redirect TEXT, done_at TEXT);
        CREATE TABLE IF NOT EXISTS oauth_codes (
            code_hash TEXT PRIMARY KEY, connection_id TEXT NOT NULL, client_id TEXT NOT NULL,
            redirect_uri TEXT NOT NULL, challenge TEXT NOT NULL, resource TEXT NOT NULL,
            expires_at TEXT NOT NULL, used_at TEXT);
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            token_hash TEXT PRIMARY KEY, connection_id TEXT NOT NULL, kind TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT);
        CREATE INDEX IF NOT EXISTS oauth_tokens_connection ON oauth_tokens(connection_id);
    """)


# ---- configuration -------------------------------------------------------------------------
class Door:
    """The public addresses of one CRM's OAuth door, from its profile."""

    def __init__(self, origin, prefix, scopes=api_keys.DEFAULT_SCOPES):
        self.scopes = tuple(scopes)  # what this CRM can grant: the brain scope only with a brain named
        self.origin = origin.rstrip("/")
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""
        self.issuer = self.origin + self.prefix
        self.resource = self.issuer + "/mcp"

    def url(self, path):
        return self.issuer + path

    def path(self, path):
        """A path as the browser sees it (with the mount prefix)."""
        return self.prefix + path

    @property
    def resource_metadata(self):
        return self.origin + PRM + self.prefix + "/mcp"

    def challenge(self, error=None):
        parts = ['Bearer realm="cta-crm"']
        if error:
            parts.append(f'error="{error}"')
        parts += [f'resource_metadata="{self.resource_metadata}"', f'scope="{" ".join(self.scopes)}"']
        return ", ".join(parts)


def door(prof):
    """The Door for this profile, or None when OAuth is off or no public origin is known."""
    crm = prof.get("crm") or {}
    cfg = crm.get("oauth")
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    origin = cfg.get("origin") or next(iter((prof.get("access") or {}).get("origins") or []), "")
    u = urlparse(origin or "")
    if u.scheme not in ("https", "http") or not u.netloc or u.path not in ("", "/") or u.query:
        return None
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    from . import brain
    scopes = [s for s in SCOPES if s != brain.SCOPE or brain.config(prof)]
    return Door(f"{u.scheme}://{u.netloc}", crm.get("path_prefix") or "/", scopes)


def is_public_path(path):
    """Paths that answer without a person's sign-in. /oauth/authorize and /oauth/apps are not."""
    return path in (TOKEN, REGISTER, REVOKE) or path.startswith(PRM) or path.startswith(ASM)


def is_oauth_path(path):
    return is_public_path(path) or path in (AUTHORIZE, APPS)


# ---- answers -------------------------------------------------------------------------------
JSON_HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store", "Pragma": "no-cache"}


def _json(status, body, extra=None):
    return status, {**JSON_HEADERS, **(extra or {})}, json.dumps(body).encode("utf-8")


def _oauth_error(status, error, description, extra=None):
    return _json(status, {"error": error, "error_description": description}, extra)


def metadata(d, path):
    """The two discovery documents. Anything under either well-known name gets its document."""
    if path.startswith(PRM):
        return _json(200, {"resource": d.resource, "authorization_servers": [d.issuer],
                           "scopes_supported": list(d.scopes), "bearer_methods_supported": ["header"],
                           "resource_name": "CRM"})
    return _json(200, {
        "issuer": d.issuer,
        "authorization_endpoint": d.url(AUTHORIZE), "token_endpoint": d.url(TOKEN),
        "registration_endpoint": d.url(REGISTER), "revocation_endpoint": d.url(REVOKE),
        "response_types_supported": ["code"], "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": list(d.scopes),
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": False,
    })


# ---- dynamic client registration -----------------------------------------------------------
class _Limit:
    def __init__(self, requests, window):
        self.requests, self.window, self.seen = requests, window, deque()

    def allow(self, clock=time.monotonic):
        t = clock()
        while self.seen and self.seen[0] <= t - self.window:
            self.seen.popleft()
        if len(self.seen) >= self.requests:
            return False
        self.seen.append(t)
        return True


def _redirect_ok(uri):
    """https anywhere, or http on loopback (Claude Code's local callback). No fragment, no userinfo."""
    if not isinstance(uri, str) or len(uri) > 500:
        return False
    u = urlparse(uri)
    if u.fragment or u.username or u.password or not u.hostname:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


def register(store, raw, limit):
    if not limit.allow():
        return _oauth_error(429, "too_many_requests", "Too many registrations. Wait a minute.", {"Retry-After": "60"})
    try:
        body = json.loads(raw or b"")
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return _oauth_error(400, "invalid_client_metadata", "The registration must be a JSON object")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10 or not all(_redirect_ok(u) for u in uris):
        return _oauth_error(400, "invalid_redirect_uri", "redirect_uris: 1 to 10 https addresses (http only on this computer)")
    method = body.get("token_endpoint_auth_method") or "client_secret_basic"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return _oauth_error(400, "invalid_client_metadata", "token_endpoint_auth_method must be none, client_secret_post or client_secret_basic")
    for field, allowed in (("grant_types", {"authorization_code", "refresh_token"}), ("response_types", {"code"})):
        v = body.get(field)
        if v is not None and (not isinstance(v, list) or not set(v) <= allowed):
            return _oauth_error(400, "invalid_client_metadata", f"{field} may only be {', '.join(sorted(allowed))}")
    name = body.get("client_name")
    name = " ".join(name.split())[:100] if isinstance(name, str) and name.strip() else "An unnamed app"
    client_id, at = str(uuid4()), stamp()
    secret = secrets.token_urlsafe(32) if method != "none" else None
    with store.connection() as db:
        _purge(db)
        db.execute("INSERT INTO oauth_clients VALUES (?,?,?,?,?,?,?)",
                   (client_id, name, json.dumps(uris), method, _hash(secret) if secret else None, at, at))
    out = {"client_id": client_id, "client_id_issued_at": int(time.time()), "client_name": name,
           "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "token_endpoint_auth_method": method}
    if secret:
        out.update(client_secret=secret, client_secret_expires_at=0)
    return _json(201, out)


def _client(db, client_id):
    if not isinstance(client_id, str) or len(client_id) > 80:
        return None
    row = db.execute("SELECT * FROM oauth_clients WHERE id=?", (client_id,)).fetchone()
    if row is None or row["last_used_at"] <= stamp(now() - CLIENT_IDLE):
        return None
    return dict(row)


def _purge(db):
    """Expired consents, codes and tokens, and clients unused for 90 days. Cheap; runs on writes."""
    t = stamp()
    db.execute("DELETE FROM oauth_consents WHERE expires_at<=?", (t,))
    db.execute("DELETE FROM oauth_codes WHERE expires_at<=?", (t,))
    db.execute("DELETE FROM oauth_tokens WHERE expires_at<=?", (t,))
    db.execute("DELETE FROM oauth_clients WHERE last_used_at<=? AND id NOT IN "
               "(SELECT client_id FROM mcp_connections WHERE revoked_at IS NULL)", (stamp(now() - CLIENT_IDLE),))


# ---- the consent page (its HTML lives in oauth_pages.py) -------------------------------------
def _redirect(uri, params):
    sep = "&" if urlparse(uri).query else "?"
    return uri + sep + urlencode({k: v for k, v in params.items() if v is not None})


def _found(location, extra=None):
    return 302, {"Location": location, "Cache-Control": "no-store", **(extra or {})}, b""


def _cookie(d, value, max_age):
    secure = "; Secure" if d.origin.startswith("https:") else ""
    return f"{COOKIE}={value}; Path={d.path('/oauth/')}; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"


def _read_cookie(headers):
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return ""


def authorize_get(store, prof, d, person, query):
    """The consent page, for a person the CRM's sign-in already named."""
    q = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items() if len(v) == 1}
    multi = [k for k, v in parse_qs(query, keep_blank_values=True).items() if len(v) > 1]
    with store.connection() as db:
        client = _client(db, q.get("client_id"))
    if client is None:
        return problem(prof, "Cannot connect", "This app is not registered here, or its registration expired.")
    redirect_uri = q.get("redirect_uri") or ""
    uris = json.loads(client["redirect_uris"])
    if not redirect_uri and len(uris) == 1:
        redirect_uri = uris[0]
    if redirect_uri not in uris:
        return problem(prof, "Cannot connect", "This app asked to send access to an address it did not register.")
    state = q.get("state")

    def back(error, description):
        return _found(_redirect(redirect_uri, {"error": error, "error_description": description, "state": state, "iss": d.issuer}))

    if multi:
        return back("invalid_request", f"repeated parameter: {', '.join(sorted(multi))}")
    if q.get("response_type") != "code":
        return back("unsupported_response_type", "response_type must be code")
    challenge, method = q.get("code_challenge") or "", q.get("code_challenge_method") or ""
    if method != "S256" or not 43 <= len(challenge) <= 128:
        return back("invalid_request", "PKCE with S256 is required")
    resource = q.get("resource") or d.resource
    if resource.rstrip("/") != d.resource:
        return back("invalid_target", "resource must be " + d.resource)
    offered = list(d.scopes)  # no brain named in the profile: no brain box
    asked = [s for s in offered if s in (q.get("scope") or "").split()]
    req = {"redirect_uri": redirect_uri, "state": state, "challenge": challenge, "resource": d.resource,
           "scopes": asked or offered, "person_name": person.name}
    handle, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with store.connection() as db:
        _purge(db)
        db.execute("INSERT INTO oauth_consents VALUES (?,?,?,?,?,?,NULL,NULL)",
                   (_hash(handle), person.id, client["id"], json.dumps(req), _hash(browser),
                    stamp(now() + timedelta(seconds=CONSENT_TTL))))
    status, headers, body = page("Connect an app", _consent_html(prof, d, req, client["name"], handle),
                                 form_target=_origin_of(redirect_uri))
    headers["Set-Cookie"] = _cookie(d, browser, CONSENT_TTL)
    return status, headers, body


def _origin_of(uri):
    u = urlparse(uri)
    return f"{u.scheme}://{u.netloc}"


def authorize_post(store, prof, d, person, headers, raw):
    """Allow or Deny. The form must come from this CRM's own page, in the browser that opened it,
    signed in as the same person, within ten minutes, once."""
    expired = ("This page expired", "Consent pages work once, for ten minutes, in the browser that opened them.")
    if (headers.get("Origin") or "") != d.origin:
        return problem(prof, "Not allowed", "This form must be sent from the CRM's own page.", 403)
    form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    handle = (form.get("handle") or [""])[0]
    if not handle or len(handle) > 100:
        return problem(prof, *expired)
    with store.connection() as db:
        row = db.execute("SELECT * FROM oauth_consents WHERE handle_hash=?", (_hash(handle),)).fetchone()
        row = dict(row) if row else None
    if row is None or row["expires_at"] <= stamp() or row["person_id"] != person.id:
        return problem(prof, *expired)
    if row["done_redirect"]:
        # A second press of Allow gets the first press's answer, never "expired" (Studio fix 39280a9f).
        return _found(row["done_redirect"])
    if _hash(_read_cookie(headers) or "-") != row["browser_hash"]:
        return problem(prof, *expired)
    req = json.loads(row["request"])
    clear = {"Set-Cookie": _cookie(d, "", 0)}
    if (form.get("decision") or [""])[0] != "approve":
        with store.connection() as db:
            db.execute("DELETE FROM oauth_consents WHERE handle_hash=?", (row["handle_hash"],))
        return _found(_redirect(req["redirect_uri"], {"error": "access_denied", "error_description": "The person pressed Deny",
                                                      "state": req["state"], "iss": d.issuer}), clear)
    chosen = [s for s in req["scopes"] if s in (form.get("scope") or [])]
    with store.connection() as db:
        client = _client(db, row["client_id"])
    if client is None:
        return problem(prof, "Cannot connect", "This app's registration expired. Start again from your Claude app.")
    if not chosen:
        status, h, body = page("Connect an app", _consent_html(prof, d, req, client["name"], handle,
                                                                 "Choose at least one thing it can do, or press Deny."),
                               400, form_target=_origin_of(req["redirect_uri"]))
        return status, h, body
    code, connection_id, at = secrets.token_urlsafe(32), str(uuid4()), stamp()
    location = _redirect(req["redirect_uri"], {"code": code, "state": req["state"], "iss": d.issuer})
    with store.connection() as db:
        claimed = db.execute("UPDATE oauth_consents SET done_redirect=?, done_at=?, expires_at=? "
                             "WHERE handle_hash=? AND done_redirect IS NULL",
                             (location, at, stamp(now() + timedelta(seconds=120)), row["handle_hash"])).rowcount
        if claimed != 1:  # two presses at once: the other one won
            again = db.execute("SELECT done_redirect FROM oauth_consents WHERE handle_hash=?", (row["handle_hash"],)).fetchone()
            return _found(again["done_redirect"]) if again and again["done_redirect"] else problem(prof, *expired)
        db.execute("INSERT INTO mcp_connections (id,person_id,client_id,client_name,redirect_host,scopes,created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (connection_id, person.id, client["id"], client["name"], urlparse(req["redirect_uri"]).hostname or "",
                    " ".join(chosen), at))
        db.execute("INSERT INTO oauth_codes VALUES (?,?,?,?,?,?,?,NULL)",
                   (_hash(code), connection_id, client["id"], req["redirect_uri"], req["challenge"], req["resource"],
                    stamp(now() + timedelta(seconds=CODE_TTL))))
        db.execute("UPDATE oauth_clients SET last_used_at=? WHERE id=?", (at, client["id"]))
        api_keys._event(db, connection_id, "mcp.connect", f"{client['name']} for {person.id}: {' '.join(chosen)}")
    return _found(location, clear)


# ---- the token endpoint --------------------------------------------------------------------
def _client_auth(db, headers, form):
    """The registered client making this request, or an error tuple."""
    basic = (headers.get("Authorization") or "").strip()
    client_id, secret = (form.get("client_id") or [None])[0], (form.get("client_secret") or [None])[0]
    if basic.lower().startswith("basic "):
        try:
            raw_id, _, raw_secret = base64.b64decode(basic[6:].strip(), validate=True).decode("utf-8").partition(":")
            client_id, secret = unquote(raw_id), unquote(raw_secret)
        except (ValueError, UnicodeDecodeError):
            return None, _oauth_error(401, "invalid_client", "The client credentials are malformed")
    client = _client(db, client_id)
    if client is None:
        return None, _oauth_error(401, "invalid_client", "This app is not registered here, or its registration expired")
    if client["auth_method"] != "none":
        if not secret or not client["secret_hash"] or not secrets.compare_digest(_hash(secret), client["secret_hash"]):
            return None, _oauth_error(401, "invalid_client", "The client secret is missing or wrong")
    return client, None


def _issue(db, connection_id, scopes, refresh_expires):
    access, refresh, at = ACCESS_PREFIX + secrets.token_urlsafe(32), REFRESH_PREFIX + secrets.token_urlsafe(32), now()
    db.execute("INSERT INTO oauth_tokens VALUES (?,?,?,?,?,NULL)",
               (_hash(access), connection_id, "access", stamp(at), stamp(at + timedelta(seconds=ACCESS_TTL))))
    db.execute("INSERT INTO oauth_tokens VALUES (?,?,?,?,?,NULL)",
               (_hash(refresh), connection_id, "refresh", stamp(at), stamp(refresh_expires)))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
            "refresh_token": refresh, "scope": scopes}


def _verifier_ok(verifier, challenge):
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii", "replace")).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(digest, challenge)


def token(store, prof, d, headers, raw):
    if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        return _oauth_error(400, "invalid_request", "The token request must be form-encoded")
    form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    if any(len(v) > 1 for v in form.values()):
        return _oauth_error(400, "invalid_request", "A parameter was repeated")
    one = {k: v[0] for k, v in form.items()}
    resource = one.get("resource")
    if resource is not None and resource.rstrip("/") != d.resource:
        return _oauth_error(400, "invalid_target", "resource must be " + d.resource)
    from . import identity
    with store.connection() as db:
        _purge(db)
        client, err = _client_auth(db, headers, form)
        if err:
            return err
        grant = one.get("grant_type")
        if grant == "authorization_code":
            row = db.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (_hash(one.get("code") or "-"),)).fetchone()
            if row is None or row["client_id"] != client["id"]:
                return _oauth_error(400, "invalid_grant", "The code is not valid")
            if row["used_at"]:
                # A code used twice: someone else may hold it. End the connection it made.
                _revoke_connection(db, row["connection_id"], "code reused")
                return _oauth_error(400, "invalid_grant", "The code was already used")
            db.execute("UPDATE oauth_codes SET used_at=? WHERE code_hash=?", (stamp(), row["code_hash"]))
            if row["expires_at"] <= stamp():
                return _oauth_error(400, "invalid_grant", "The code expired")
            if one.get("redirect_uri") != row["redirect_uri"]:
                return _oauth_error(400, "invalid_grant", "redirect_uri does not match the authorization request")
            if not _verifier_ok(one.get("code_verifier"), row["challenge"]):
                return _oauth_error(400, "invalid_grant", "The PKCE code_verifier does not match")
            conn = db.execute("SELECT * FROM mcp_connections WHERE id=?", (row["connection_id"],)).fetchone()
            if conn is None or conn["revoked_at"]:
                return _oauth_error(400, "invalid_grant", "This connection was removed")
            limit = datetime.fromisoformat(conn["created_at"]) + REFRESH_MAX
            body = _issue(db, conn["id"], conn["scopes"], min(now() + REFRESH_IDLE, limit))
        elif grant == "refresh_token":
            row = db.execute("SELECT t.*, c.revoked_at, c.created_at AS connected_at, c.scopes, c.person_id, c.client_id "
                             "FROM oauth_tokens t JOIN mcp_connections c ON c.id=t.connection_id "
                             "WHERE t.token_hash=? AND t.kind='refresh'", (_hash(one.get("refresh_token") or "-"),)).fetchone()
            if row is None or row["client_id"] != client["id"] or row["revoked_at"] or row["used_at"] or row["expires_at"] <= stamp():
                return _oauth_error(400, "invalid_grant", "The refresh token is not valid")
            if identity.person(prof, row["person_id"]) is None:
                return _oauth_error(400, "invalid_grant", "The person who connected this app is no longer here")
            db.execute("UPDATE oauth_tokens SET used_at=? WHERE token_hash=?", (stamp(), row["token_hash"]))
            limit = datetime.fromisoformat(row["connected_at"]) + REFRESH_MAX
            if now() >= limit:
                return _oauth_error(400, "invalid_grant", "This connection is 90 days old. Connect again.")
            body = _issue(db, row["connection_id"], row["scopes"], min(now() + REFRESH_IDLE, limit))
        else:
            return _oauth_error(400, "unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
        db.execute("UPDATE oauth_clients SET last_used_at=? WHERE id=?", (stamp(), client["id"]))
    return _json(200, body)


def revoke_token(store, headers, raw):
    """RFC 7009. Either token ends the whole connection. Always 200, so it reveals nothing."""
    form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    value = (form.get("token") or [""])[0]
    with store.connection() as db:
        client, err = _client_auth(db, headers, form)
        if err:
            return err
        row = db.execute("SELECT t.connection_id FROM oauth_tokens t JOIN mcp_connections c ON c.id=t.connection_id "
                         "WHERE t.token_hash=? AND c.client_id=?", (_hash(value or "-"), client["id"])).fetchone()
        if row:
            _revoke_connection(db, row["connection_id"], "the app")
    return _json(200, {})


def _revoke_connection(db, connection_id, by):
    if db.execute("UPDATE mcp_connections SET revoked_at=?, revoked_by=? WHERE id=? AND revoked_at IS NULL",
                  (stamp(), by, connection_id)).rowcount:
        db.execute("DELETE FROM oauth_tokens WHERE connection_id=?", (connection_id,))
        db.execute("DELETE FROM oauth_codes WHERE connection_id=?", (connection_id,))
        api_keys._event(db, connection_id, "mcp.revoke", f"by {by}")


# ---- signing in at /mcp --------------------------------------------------------------------
def is_token(secret):
    return isinstance(secret, str) and secret.startswith(ACCESS_PREFIX)


def authenticate(headers, prof, store, limiter=None):
    """The Grant for an OAuth access token, or api_keys.Refused. Same shape as a key's Grant."""
    from . import identity
    secret = api_keys.bearer(headers)
    if not is_token(secret) or len(secret) > 200:
        raise api_keys.Refused("the access token is malformed")
    refusal = None
    with store.connection() as db:
        row = db.execute("SELECT t.expires_at, c.* FROM oauth_tokens t JOIN mcp_connections c ON c.id=t.connection_id "
                         "WHERE t.token_hash=? AND t.kind='access'", (_hash(secret),)).fetchone()
        if row is None:
            raise api_keys.Refused("the access token is not one this CRM issued, or it was removed")
        row = dict(row)
        person = identity.person(prof, row["person_id"], via=f"oauth:{row['id']}")
        if row["revoked_at"]:
            refusal = (401, "this connection was removed", "revoked")
        elif row["expires_at"] <= stamp():
            refusal = (401, "the access token expired", "expired")
        elif person is None:
            refusal = (403, f"the connection belongs to {row['person_id']}, who is no longer one of this business's people",
                       f"{row['person_id']} is no longer a member")
        elif limiter is not None and not limiter.allow(row["id"]):
            refusal = (429, "too many requests for this connection; wait a minute", "rate_limit")
        if refusal:
            if refusal[0] != 401:
                api_keys._event(db, row["id"], "mcp.refused" if refusal[0] != 429 else "mcp.rate_limit", refusal[2])
        else:
            db.execute("UPDATE mcp_connections SET last_used_at=? WHERE id=?", (stamp(), row["id"]))
    if refusal:
        raise api_keys.Refused(refusal[1], refusal[0])
    return api_keys.Grant(key_id=row["id"], label=row["client_name"], person=person,
                          scopes=api_keys.parse_scopes(row["scopes"]))


# ---- connected apps ------------------------------------------------------------------------
def _sees_all(prof, person):
    return ((prof.get("roles") or {}).get(person.role) or {}).get("sees") == "all"


def connections(store, prof, person):
    """Live connections this person may see: their own, or everyone's for a role that sees all."""
    with store.connection() as db:
        rows = [dict(r) for r in db.execute("SELECT id,person_id,client_name,redirect_host,scopes,created_at,last_used_at "
                                            "FROM mcp_connections WHERE revoked_at IS NULL ORDER BY created_at DESC")]
    return [r for r in rows if r["person_id"] == person.id or _sees_all(prof, person)]


def remove(store, prof, person, connection_id):
    """A person removes their own app; a role that sees all removes anyone's. True when removed."""
    with store.connection() as db:
        row = db.execute("SELECT person_id, revoked_at FROM mcp_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None or row["revoked_at"] or (row["person_id"] != person.id and not _sees_all(prof, person)):
            return False
        _revoke_connection(db, connection_id, person.id)
        return True


def apps_post(store, prof, d, person, headers, raw):
    if (headers.get("Origin") or "") != d.origin:
        return problem(prof, "Not allowed", "This form must be sent from the CRM's own page.", 403)
    connection_id = (parse_qs(raw.decode("utf-8", "replace")).get("id") or [""])[0]
    done = remove(store, prof, person, connection_id[:80])
    return apps_page(prof, d, person, connections(store, prof, person),
                     "Removed. That app can no longer use the CRM." if done else "That app was already removed, or is not yours.")


# ---- mounting: what server.py calls ---------------------------------------------------------
def origins(door):
    """Origins /mcp accepts: none for a key-only CRM. With the door open, this CRM's own and
    Claude's (Claude's servers may name claude.ai). A page anywhere else is refused."""
    return (door.origin, *CLIENT_ORIGINS) if door else ()


def sign_in(headers, prof, store, limiter, door):
    """The Grant for a program route: an OAuth access token on /mcp when the door is open, else a key."""
    if door and is_token(api_keys.bearer(headers)):
        return authenticate(headers, prof, store, limiter)
    return api_keys.authenticate(headers, prof, store, limiter)


def serve(handler, method, u, raw):
    """Every /oauth/* and /.well-known/oauth-* request. Discovery, registration and tokens answer
    anyone (each checks its own secret). The consent page and Connected apps ask who you are first,
    like every page. Off in the profile: 404, as if the paths did not exist. Fails closed."""
    from . import crm_store, profiles
    cc, path = handler.cc, u.path.rstrip("/") or "/"
    js, private = "application/json; charset=utf-8", {"Cache-Control": "private, no-store"}
    try:
        prof = profiles.load(cc.business, cc.root)
    except Exception as e:  # noqa: BLE001
        return handler._send(500, json.dumps({"error": "profile", "message": str(e)}), js, private)
    d = door(prof)
    if d is None:
        return handler._send(404, json.dumps({"error": "Not found"}), js, private)

    def answer(result):
        status, headers, body = result
        headers = dict(headers)
        return handler._send(status, body, headers.pop("Content-Type", js), headers)

    well_known = path.startswith((PRM, ASM))
    allowed = "GET" if well_known else "POST" if is_public_path(path) else "GET, POST"
    if method not in allowed.split(", "):
        return handler._send(405, json.dumps({"error": "method not allowed"}), js, {**private, "Allow": allowed})
    try:
        if well_known:
            return answer(metadata(d, path))
        if path == REGISTER:
            limit = cc.__dict__.setdefault("oauth_register_limit", _Limit(*REGISTER_LIMIT))  # per server
            return answer(register(cc.crm, raw, limit))
        if path == TOKEN:
            return answer(token(cc.crm, prof, d, handler.headers, raw))
        if path == REVOKE:
            return answer(revoke_token(cc.crm, handler.headers, raw))
        who = handler._who(as_json=False)
        if who is None:
            return None
        prof, person = who
        try:
            cc.crm.authorize(prof, person)
        except crm_store.CRMError as e:
            return answer(problem(prof, "Not available", str(e), 403))
        if path == AUTHORIZE:
            if method == "GET":
                return answer(authorize_get(cc.crm, prof, d, person, u.query))
            return answer(authorize_post(cc.crm, prof, d, person, handler.headers, raw))
        if method == "GET":
            return answer(apps_page(prof, d, person, connections(cc.crm, prof, person)))
        return answer(apps_post(cc.crm, prof, d, person, handler.headers, raw))
    except Exception as e:  # noqa: BLE001 — a sign-in step that breaks is a closed door
        return handler._send(500, json.dumps({"error": "server_error", "error_description": type(e).__name__}), js, private)
