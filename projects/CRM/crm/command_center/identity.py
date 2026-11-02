"""Who is this? The one place the command center learns who is using it.

`resolve(headers, client_address, prof)` runs on every request before anything else. It
returns a `Person` from the profile, or raises:

    Unauthenticated   401   no proof of who this is, or proof that does not check out
    NotAPerson        403   a verified sign-in that is not one of this business's people

Two modes, chosen by the profile's `access.identity`. The server's `--identity` flag can
override it for one instance, which is how one profile runs as `local` on the owner's
machine and as `cloudflare_access` on the host behind the tunnel.

  local              the person is `access.local_person`. For the machine the owner sits at.
  cloudflare_access  Cloudflare Access checks who you are before the request reaches us and
                     passes a signed token in the `Cf-Access-Jwt-Assertion` header. We verify
                     the signature and the claims, then match the email to one person.
  demo               every visitor is `access.local_person`, through a proxy too. Only for a
                     profile marked `demo.enabled` and `demo.fictional`, on a database the demo
                     builder stamped as fictional (server.make_server checks the stamp), with the
                     demo route list in front (demo_mode.py). A sales demo on made-up members.

⭐ **Fail closed, every time.** No token, a bad token, an unknown email, keys that cannot be
fetched: each one is a refusal, and a refusal renders nothing. There is no default person
and no guess. An email that matches nobody is refused by name, never mapped to somebody.

## Local mode cannot be exposed by accident. Two guards, because one is not enough

1. The server refuses to START on any host but loopback (`check_startup`).
2. ⚠️ **That alone does not close it.** A tunnel (cloudflared) or a reverse proxy also
   connects to 127.0.0.1, so a local-mode server behind one would hand every visitor the
   local person, who is the owner and sees everything. So local mode also refuses any
   request that carries a proxy's fingerprints (`Cf-*`, `X-Forwarded-*`, `Forwarded`,
   `X-Real-Ip`). A browser on the same machine sends none of them. Forgetting
   `--identity cloudflare_access` behind a tunnel therefore locks everybody out instead of
   letting everybody in.

## The signature library: `cryptography`, imported only in cloudflare_access mode

Decided 2026-09-23 (B1 brief §1.1). RS256 by hand (`pow(sig, e, n)`, then compare the whole
expected encoding byte for byte) is short, but it makes us the owners of crypto code, and a
signature check is the one place in this package where a subtle slip is an open door.
`cryptography` is checked by far more people than us, and it lets the tests SIGN tokens as
easily as we verify them.

The cost is the engine's first third-party dependency. It is imported lazily, so `local`
mode and the rest of the engine stay stdlib-only. `deploy/preflight.sh` checks for it, and
`check_startup` refuses cloudflare_access mode without it rather than failing on the first
request.

Licence, read 2026-09-23 from the installed package's own files (cryptography 50.0.1,
`licenses/LICENSE`): made available under "either of the licenses found in LICENSE.APACHE
or LICENSE.BSD", `License-Expression: Apache-2.0 OR BSD-3-Clause`. Both are permissive. That
matters because this engine is copied into client repos (design §9, constraint 5). We copy
none of its code into ours; a host installs it.

## What Cloudflare's docs say, and what this module does about each

- Read the `Cf-Access-Jwt-Assertion` HEADER, never the `CF_Authorization` cookie, "since the
  cookie is not guaranteed to be passed". The cookie is never read here.
- Match the token's `kid` against the published key SET. Never use the single
  `public_cert`: a cached copy of it can be stale.
- Keys rotate about every six weeks. `KeySet` caches by `kid`, refetches once when a token
  names a `kid` it does not hold, and refuses (401) when the keys cannot be fetched.
- `aud` must contain the application's AUD tag; `iss` must equal the team domain; `exp` must
  be in the future, with 60 seconds of clock skew and no more. Claims are read only AFTER
  the signature verifies. An unverified claim is never acted on.
- Only `RS256` is accepted. A token that asks for `none` or `HS256` is refused, because the
  classic JWT forgery is a token that chooses its own algorithm.

## The seam for a third mode

`oidc` (Google or Microsoft sign-in inside the app, design §4.1 (c)) is not built. It slots
in as one more branch of `resolve` that returns a `Person` from a verified ID token, and it
can reuse `KeySet` and `_verify_rs256`. The profile validator refuses `oidc` by name until
then, so a profile cannot switch it on and fall through to something weaker.
"""

import base64
import ipaddress
import json
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schema import SchemaError

HEADER = "Cf-Access-Jwt-Assertion"
CERTS_PATH = "/cdn-cgi/access/certs"

SKEW_SECONDS = 60           # Cloudflare's clocks and ours; the brief allows 60 and no more
MAX_TOKEN_BYTES = 16_384    # a real Access token is ~1KB; anything this big is not one
MIN_RSA_BITS = 2048
FETCH_TIMEOUT_SECONDS = 5
KEY_TTL_SECONDS = 3600      # re-read the key set hourly, so a withdrawn key stops working

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# A browser on the same machine sends none of these. A tunnel or a proxy sends at least one.
PROXY_PREFIXES = ("cf-", "x-forwarded-")
PROXY_HEADERS = ("forwarded", "x-real-ip")

MODES = ("local", "cloudflare_access", "demo")


class Unauthenticated(Exception):
    """401. No proof of who this is, or proof that does not check out."""
    status = 401


class NotAPerson(Exception):
    """403. The sign-in is real, but it is nobody this business has named."""
    status = 403


class StartupRefused(SchemaError):
    """The server will not start like this, and says why."""


@dataclass(frozen=True)
class Person:
    """One of the business's people, as the profile names them, verified for one request."""
    id: str
    name: str
    role: str
    title: str = ""
    email: str = ""
    via: str = ""

    @property
    def by(self):
        """Provenance for a write. Built from the verified identity and nothing else."""
        return f"human:{self.id}"

    def public(self):
        """What the page may say about who is signed in."""
        return {"id": self.id, "name": self.name, "title": self.title, "role": self.role}


# ---------------------------------------------------------------------------------
# The profile side
# ---------------------------------------------------------------------------------

def _members(prof):
    return [m for m in (prof.get("people") or {}).get("members", [])
            if isinstance(m, dict) and m.get("id")]


def person(prof, person_id, email="", via=""):
    """The member with this id, as a Person, or None."""
    for m in _members(prof):
        if m["id"] == person_id:
            return Person(id=m["id"], name=m.get("name") or m["id"], role=m.get("role") or "",
                          title=m.get("title") or "", email=email, via=via)
    return None


def mode(prof, override=None):
    """The identity mode this instance runs in: the override, else the profile's."""
    m = override or (prof.get("access") or {}).get("identity")
    if m not in MODES:
        raise StartupRefused(
            f"identity mode {m!r} is not one of {', '.join(MODES)}. The profile needs an "
            "`access` block that says how people sign in; the server will not guess.")
    return m


def _is_loopback_host(host):
    h = (host or "").strip().strip("[]").lower()
    if h in LOOPBACK_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def check_startup(prof, host, override=None):
    """Refuse to start in a shape that could expose the records. Returns the mode.

    Loopback only, in BOTH modes: the tunnel is the only way in (brief §5). In local mode
    the local person must exist. In cloudflare_access mode the team domain and the AUD tag
    must be filled in and the signature library must import, so a missing piece is an
    error at boot rather than a 401 for everybody on the first request.
    """
    m = mode(prof, override)
    if not _is_loopback_host(host):
        why = (" Local mode trusts the machine it runs on, so it would give every visitor "
               "the local person." if m == "local" else "")
        raise StartupRefused(
            f"refusing to bind {host!r}: this server binds loopback only (127.0.0.1, "
            f"localhost or ::1). The tunnel is the only way in.{why}")
    acc = prof.get("access") or {}
    demo = prof.get("demo") if isinstance(prof.get("demo"), dict) else {}
    if m == "demo" and not (demo.get("enabled") is True and demo.get("fictional") is True):
        raise StartupRefused(
            "demo mode hands every visitor the owner's seat, so it runs only on a profile with "
            "demo.enabled and demo.fictional both true (made-up members, nothing real)")
    if m in ("local", "demo"):
        lp = acc.get("local_person") or ""
        if not person(prof, lp):
            raise StartupRefused(
                f"local mode needs access.local_person to name one of the people; it is {lp!r}")
    else:
        missing = [f for f in ("team_domain", "aud") if not acc.get(f)]
        if missing:
            raise StartupRefused(
                f"cloudflare_access mode needs access.{' and access.'.join(missing)} in the "
                "profile: the team domain and the Access application's AUD tag")
        try:
            import cryptography  # noqa: F401
        except ImportError as e:
            raise StartupRefused(
                "cloudflare_access mode verifies signatures with the `cryptography` package, "
                "which is not installed. pip install cryptography") from e
    return m


# ---------------------------------------------------------------------------------
# The request side
# ---------------------------------------------------------------------------------

def _header_values(headers, name):
    """Every value of one header, case-insensitively, from an HTTPMessage or a dict."""
    get_all = getattr(headers, "get_all", None)
    if get_all is not None:
        return list(get_all(name) or [])
    return [v for k, v in (headers or {}).items() if k.lower() == name.lower()]


def _header_names(headers):
    return [k.lower() for k in (headers.keys() if hasattr(headers, "keys") else [])]


def resolve(headers, client_address, prof, mode=None):
    """The person making this request. Raises Unauthenticated (401) or NotAPerson (403)."""
    m = mode or (prof.get("access") or {}).get("identity")
    if m == "local":
        return _resolve_local(headers, client_address, prof)
    if m == "demo":  # check_startup proved the profile is a fictional demo
        lp = (prof.get("access") or {}).get("local_person") or ""
        p = person(prof, lp, via="demo")
        if p is None:
            raise NotAPerson(f"demo mode names {lp!r}, who is not one of this business's people")
        return p
    if m == "cloudflare_access":
        return _resolve_cloudflare(headers, prof)
    # A profile with no access block, or a mode nobody built. Refuse, never default.
    raise Unauthenticated("this instance has no sign-in configured, so it shows nothing")


def _resolve_local(headers, client_address, prof):
    addr = (client_address or ("",))[0]
    if not _is_loopback_host(addr):
        raise Unauthenticated("local mode answers this machine only")
    for name in _header_names(headers):
        if name.startswith(PROXY_PREFIXES) or name in PROXY_HEADERS:
            raise Unauthenticated(
                f"this server is in local mode, and this request came through a proxy or a "
                f"tunnel (it carries {name}). Local mode trusts the machine it runs on, so it "
                "refuses anything forwarded. Behind a tunnel, run it with "
                "--identity cloudflare_access.")
    lp = (prof.get("access") or {}).get("local_person") or ""
    p = person(prof, lp, via="local")
    if p is None:
        raise NotAPerson(f"local mode names {lp!r}, who is not one of this business's people")
    return p


def _resolve_cloudflare(headers, prof):
    acc = prof.get("access") or {}
    team, aud = acc.get("team_domain") or "", acc.get("aud") or ""
    if not team or not aud:
        raise Unauthenticated("sign-in is not configured (no team domain or AUD tag)")
    values = _header_values(headers, HEADER)
    if not values:
        raise Unauthenticated("this request carries no sign-in token")
    if len(values) > 1:
        raise Unauthenticated("more than one sign-in token on one request")
    claims = verify(values[0], team, aud, keyset(team))
    return match_email(prof, claims.get("email"), via="cloudflare_access")


def match_email(prof, email, via=""):
    """Exactly one person whose `emails` holds this address, compared lower-case."""
    if not isinstance(email, str) or not email.strip():
        raise NotAPerson("the sign-in carries no email, so it is not a person "
                         "(a service token is not one of this business's people)")
    want = email.strip().lower()
    hits = [m for m in _members(prof)
            if want in [e.lower() for e in (m.get("emails") or []) if isinstance(e, str)]]
    if not hits:
        raise NotAPerson(f"signed in as {email}, but {email} is not one of this "
                         "business's people")
    if len(hits) > 1:
        raise NotAPerson(f"{email} belongs to more than one person, so it is refused "
                         "rather than guessed")
    return person(prof, hits[0]["id"], email=want, via=via)


# ---------------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------------

_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")


def _b64(s):
    if not isinstance(s, str) or not _B64URL.match(s):
        raise Unauthenticated("the sign-in token is malformed")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError):
        raise Unauthenticated("the sign-in token is malformed") from None


def _json(raw):
    try:
        out = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise Unauthenticated("the sign-in token is malformed") from None
    if not isinstance(out, dict):
        raise Unauthenticated("the sign-in token is malformed")
    return out


def _b64int(s):
    return int.from_bytes(_b64(s), "big")


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def verify(token, team_domain, aud, keys, now=None):
    """The token's claims, if and only if every check passes. Raises Unauthenticated."""
    if not isinstance(token, str) or not token.strip():
        raise Unauthenticated("this request carries no sign-in token")
    token = token.strip()
    if len(token) > MAX_TOKEN_BYTES:
        raise Unauthenticated("the sign-in token is too large to be one")
    parts = token.split(".")
    if len(parts) != 3:
        raise Unauthenticated("the sign-in token is malformed")
    h_b, p_b, s_b = parts
    header = _json(_b64(h_b))
    if header.get("alg") != "RS256":
        raise Unauthenticated(f"token algorithm {header.get('alg')!r} refused; only RS256 "
                              "is accepted")
    if "crit" in header:
        raise Unauthenticated("the sign-in token demands extensions this server does not know")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise Unauthenticated("the sign-in token does not say which key signed it")

    _verify_rs256(keys.key(kid), f"{h_b}.{p_b}".encode("ascii"), _b64(s_b))

    # Only now is anything in the payload believed.
    claims = _json(_b64(p_b))
    now = time.time() if now is None else now
    if claims.get("iss") != team_domain:
        raise Unauthenticated("the sign-in token was issued by somebody else")
    got = claims.get("aud")
    auds = got if isinstance(got, list) else [got]
    if aud not in auds:
        raise Unauthenticated("the sign-in token is for a different application")
    exp = claims.get("exp")
    if not _number(exp):
        raise Unauthenticated("the sign-in token has no expiry")
    if now >= exp + SKEW_SECONDS:
        raise Unauthenticated("the sign-in has expired; sign in again")
    nbf = claims.get("nbf")
    if nbf is not None and (not _number(nbf) or now + SKEW_SECONDS < nbf):
        raise Unauthenticated("the sign-in token is not valid yet")
    return claims


def _verify_rs256(public_numbers, signing_input, signature):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    n, e = public_numbers
    key = rsa.RSAPublicNumbers(e, n).public_key()
    try:
        key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise Unauthenticated("the sign-in token's signature does not verify") from None


# ---------------------------------------------------------------------------------
# The published keys
# ---------------------------------------------------------------------------------

def _http_get(url):
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "command-center-identity"})
    with urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as r:
        if r.status != 200:
            raise ValueError(f"HTTP {r.status}")
        return r.read(1_000_000)


class KeySet:
    """The team's published signing keys, cached by `kid`.

    Refetched when the cache is older than an hour, and once more when a token names a
    `kid` the cache does not hold (a rotation). If the keys cannot be fetched, every token
    is refused: a sign-in check that cannot check is a closed door, not an open one.
    """

    def __init__(self, certs_url, fetch=_http_get, clock=time.monotonic, ttl=KEY_TTL_SECONDS):
        self.url = certs_url
        self._fetch, self._clock, self._ttl = fetch, clock, ttl
        self._keys, self._at = None, None
        self._lock = threading.Lock()

    def key(self, kid):
        with self._lock:
            fresh = False
            if self._keys is None or self._clock() - self._at > self._ttl:
                self._refresh()
                fresh = True
            k = self._keys.get(kid)
            if k is None and not fresh:
                self._refresh()
                k = self._keys.get(kid)
            if k is None:
                raise Unauthenticated("the sign-in token was signed by a key this "
                                      "business's sign-in does not publish")
            return k

    def _refresh(self):
        try:
            data = json.loads(self._fetch(self.url))
        except Exception as e:  # noqa: BLE001 — any failure to fetch is a refusal
            raise Unauthenticated(f"could not fetch the sign-in keys ({type(e).__name__}), "
                                  "so nobody is let in until they can be") from None
        keys = {}
        # The key SET. Never the single `public_cert`: Cloudflare's docs say a cached copy
        # of it can be stale.
        for jwk in (data.get("keys") if isinstance(data, dict) else None) or []:
            if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
                continue
            kid = jwk.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            if jwk.get("use") not in (None, "sig") or jwk.get("alg") not in (None, "RS256"):
                continue
            try:
                n, e = _b64int(jwk["n"]), _b64int(jwk["e"])
            except (KeyError, Unauthenticated):
                continue
            if n.bit_length() < MIN_RSA_BITS:
                continue
            keys[kid] = (n, e)
        if not keys:
            raise Unauthenticated("the sign-in key set is empty, so nobody is let in")
        self._keys, self._at = keys, self._clock()


_KEYSETS = {}
_KEYSETS_LOCK = threading.Lock()


def keyset(team_domain):
    """One cached KeySet per team domain, for the life of the process."""
    url = team_domain + CERTS_PATH
    with _KEYSETS_LOCK:
        ks = _KEYSETS.get(url)
        if ks is None:
            ks = _KEYSETS[url] = KeySet(url)
        return ks


def forget_keys():
    """Drop every cached key set. For tests, and for nothing else."""
    with _KEYSETS_LOCK:
        _KEYSETS.clear()


def origin_allowed(prof, mode, origin):
    """Whether a write may come from this browser origin (the cross-site guard).

    Local mode: a loopback origin only, as before. Cloudflare mode: exactly one of the
    profile's `access.origins`. This is not authentication; it stops a page elsewhere from
    making a signed-in person's browser write for it.
    """
    if mode == "local":
        return _is_loopback_host(urlparse(origin).hostname or "")
    if mode == "demo" and _is_loopback_host(urlparse(origin).hostname or ""):
        return True
    allowed = [o.rstrip("/") for o in (prof.get("access") or {}).get("origins", [])
               if isinstance(o, str)]
    return origin.rstrip("/") in allowed
