"""API keys: how a program (Claude Code, Codex, a script, another system) signs in (2026-10-11).

People sign in through Cloudflare Access or local mode (`identity.py`). A program cannot: it has
no browser and no email. A key is a person's credential for programs: it acts AS that person, with
what that person can already do on the screen, and nothing more. Same rule as the Social Studio
connector (docs/content/social-studio-mcp-connector-2026-10-10.md): a key never gains a power the person
does not have, and the person's role still decides every read and write.

    Authorization: Bearer cta_<secret>

Only the machine routes read it (`/mcp`, `/api/v1/*`). Everywhere else a bearer is ignored and the
request gets the usual sign-in check. A bearer that is missing, malformed, unknown, expired or
revoked is 401; a key whose person left the profile is 403. Fail closed, every time.

What the database keeps: the SHA-256 of the secret, never the secret. The secret is shown once, at
creation, by whoever runs the command. `prefix` (the first 8 characters after `cta_`) is for telling
keys apart on a screen; it is not enough to sign in with.

Scopes, three of them, closed:

    crm:read     search and read contacts, tasks, deals, notes
    crm:write    add contacts, notes and tasks, complete tasks (the same commands as the screen)
    brain:read   search and read the business's own brain repo (brain.py), read only

Until 2026-10-20 the rule was "two scopes, nothing else". `brain:read` is its own scope on purpose
(brief crm-assistant/03): a person can grant the CRM without the brain. A new key gets the two CRM
scopes unless `--scopes` names `brain:read`, so no existing habit widens by itself.

    python -m command_center.api_keys create --business <id> --crm-db <db> --person sam \\
        --label "Sam's Claude Code" [--scopes crm:read,crm:write,brain:read] [--days 365]
    python -m command_center.api_keys list   --business <id> --crm-db <db>
    python -m command_center.api_keys revoke --business <id> --crm-db <db> --key <id or prefix>

Every use is counted (`last_used_at`); every create, revoke, refusal and rate limit is one row in
`api_key_events` (no bodies, no secrets). 120 requests a minute per key, like the Studio.
"""
import argparse
import hashlib
import json
import secrets
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

SCOPES = ("crm:read", "crm:write", "brain:read")
DEFAULT_SCOPES = ("crm:read", "crm:write")
PREFIX = "cta_"
RATE_LIMIT = (120, 60)  # requests, seconds


class Refused(Exception):
    """A bearer that does not sign in. `status` is 401 (no proof) or 403 (a real key, but the
    person behind it is no longer one of this business's people)."""

    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Grant:
    """One verified key, for one request: who it acts as and what it may do."""
    key_id: str
    label: str
    person: object      # identity.Person, via="api_key:<key id>"
    scopes: tuple

    def allows(self, scope):
        return scope in self.scopes


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def migrate(db):
    """Version 17: keys for programs, and what they did."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, person_id TEXT NOT NULL,
            scopes TEXT NOT NULL, secret_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
            revoked_at TEXT, last_used_at TEXT);
        CREATE TABLE IF NOT EXISTS api_key_events (
            id TEXT PRIMARY KEY, key_id TEXT NOT NULL, action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS api_key_events_key ON api_key_events(key_id, created_at);
    """)


def _hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _event(db, key_id, action, detail=""):
    db.execute("INSERT INTO api_key_events VALUES (?,?,?,?,?)",
               (str(uuid4()), key_id, action, str(detail)[:500], now()))


def parse_scopes(value):
    """A list or a comma/space string -> a sorted tuple of known scopes. Unknown ones are refused."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").replace(",", " ").split()
    scopes = sorted({s.strip() for s in items if s.strip()})
    bad = [s for s in scopes if s not in SCOPES]
    if bad or not scopes:
        raise ValueError(f"scopes must be some of {', '.join(SCOPES)}; got {value!r}")
    return tuple(scopes)


# ---- creating and managing keys ----------------------------------------------------------
def create(store, prof, person_id, label, scopes=DEFAULT_SCOPES, days=365, created_by="cli"):
    """A new key for `person_id`. Returns (row, secret). The secret is returned once and never
    stored. The person must be one of the profile's members today."""
    from . import identity
    if identity.person(prof, person_id) is None:
        raise ValueError(f"{person_id!r} is not one of this business's people")
    label = str(label or "").strip()
    if not 1 <= len(label) <= 80:
        raise ValueError("label must be 1 to 80 characters")
    scopes = parse_scopes(scopes)
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be 1 to 3650")
    secret = PREFIX + secrets.token_urlsafe(32)
    key_id = str(uuid4())
    stamp = now()
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="microseconds")
    with store.connection() as db:
        db.execute("INSERT INTO api_keys (id,label,person_id,scopes,secret_hash,prefix,created_by,created_at,expires_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (key_id, label, person_id, " ".join(scopes), _hash(secret), secret[len(PREFIX):len(PREFIX) + 8],
                    created_by, stamp, expires))
        _event(db, key_id, "create", f"{label} for {person_id}: {' '.join(scopes)}")
        row = dict(db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone())
    return row, secret


def listing(store):
    with store.connection() as db:
        return [dict(r) for r in db.execute("SELECT id,label,person_id,scopes,prefix,created_by,created_at,"
                                            "expires_at,revoked_at,last_used_at FROM api_keys ORDER BY created_at")]


def revoke(store, key, revoked_by="cli"):
    """Revoke by id or by prefix. A revoked key is refused from the next request on."""
    with store.connection() as db:
        rows = db.execute("SELECT id,revoked_at FROM api_keys WHERE id=? OR prefix=? OR id LIKE ?",
                          (key, key, key + "%")).fetchall()
        if len(rows) != 1:
            raise ValueError("no such key" if not rows else "more than one key matches; use the full id")
        if rows[0]["revoked_at"]:
            return dict(rows[0])
        db.execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (now(), rows[0]["id"]))
        _event(db, rows[0]["id"], "revoke", f"by {revoked_by}")
        return dict(db.execute("SELECT * FROM api_keys WHERE id=?", (rows[0]["id"],)).fetchone())


def events(store, key_id=None, limit=100):
    with store.connection() as db:
        if key_id:
            rows = db.execute("SELECT * FROM api_key_events WHERE key_id=? ORDER BY created_at DESC LIMIT ?",
                              (key_id, limit))
        else:
            rows = db.execute("SELECT * FROM api_key_events ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# ---- signing in with one ------------------------------------------------------------------
class RateLimit:
    """Per key, in memory, per process. A restart forgets; that is fine for a limit."""

    def __init__(self, requests=RATE_LIMIT[0], window=RATE_LIMIT[1]):
        self.requests, self.window, self.seen = requests, window, {}

    def allow(self, key_id, clock=time.monotonic):
        t = clock()
        q = self.seen.setdefault(key_id, deque())
        while q and q[0] <= t - self.window:
            q.popleft()
        if len(q) >= self.requests:
            return False
        q.append(t)
        return True


def bearer(headers):
    """The bearer secret on this request, or None. Two Authorization headers = none."""
    values = [v for k, v in headers.items() if k.lower() == "authorization"] if hasattr(headers, "items") else []
    if len(values) != 1:
        return None
    parts = values[0].strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def authenticate(headers, prof, store, limiter=None):
    """The Grant for this request, or Refused (401/403). Counts the use."""
    from . import identity
    secret = bearer(headers)
    if not secret:
        raise Refused("this request carries no API key (Authorization: Bearer cta_...)")
    if not secret.startswith(PREFIX) or len(secret) > 200:
        raise Refused("the API key is malformed")
    refusal = None  # (status, message, event detail): raised AFTER the block, so the event row commits
    with store.connection() as db:
        row = db.execute("SELECT * FROM api_keys WHERE secret_hash=?", (_hash(secret),)).fetchone()
        if row is None:
            raise Refused("the API key is not one this business issued")
        row = dict(row)
        person = identity.person(prof, row["person_id"], via=f"api_key:{row['id']}")
        if row["revoked_at"]:
            refusal = (401, "the API key was revoked", "revoked")
        elif row["expires_at"] and row["expires_at"] <= now():
            refusal = (401, "the API key expired", "expired")
        elif person is None:
            refusal = (403, f"the key belongs to {row['person_id']}, who is no longer one of this business's people",
                       f"{row['person_id']} is no longer a member")
        elif limiter is not None and not limiter.allow(row["id"]):
            refusal = (429, "too many requests for this key; wait a minute", "rate_limit")
        if refusal:
            _event(db, row["id"], "refused" if refusal[0] != 429 else "rate_limit", refusal[2])
        else:
            db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now(), row["id"]))
    if refusal:
        raise Refused(refusal[1], refusal[0])
    return Grant(key_id=row["id"], label=row["label"], person=person, scopes=parse_scopes(row["scopes"]))


def record(store, grant, action, detail=""):
    """A line in the key's activity: what a program did, never what it sent."""
    with store.connection() as db:
        _event(db, grant.key_id, action, detail)


# ---- command line --------------------------------------------------------------------------
def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from command_center import crm_store, profiles
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["create", "list", "revoke", "events"])
    ap.add_argument("--business", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--person")
    ap.add_argument("--label")
    ap.add_argument("--scopes", default=",".join(DEFAULT_SCOPES))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--key")
    a = ap.parse_args(argv)
    prof = profiles.load(a.business, a.root)
    store = crm_store.Store(a.crm_db, a.business)
    if a.command == "create":
        if not a.person or not a.label:
            raise SystemExit("create needs --person and --label")
        row, secret = create(store, prof, a.person, a.label, a.scopes, a.days)
        print(f"key {row['id']} ({row['prefix']}…) for {row['person_id']}: {row['scopes']}, expires {row['expires_at'][:10]}")
        print("The secret, shown once. Put it in the program's environment, never in a file in git:")
        print(secret)
    elif a.command == "list":
        for r in listing(store):
            state = "revoked" if r["revoked_at"] else ("expired" if r["expires_at"] and r["expires_at"] <= now() else "active")
            print(f"{r['id']}  {r['prefix']}…  {state:8}  {r['person_id']:12}  {r['scopes']:22}  {r['label']}"
                  f"  last used {(r['last_used_at'] or 'never')[:16]}")
    elif a.command == "revoke":
        if not a.key:
            raise SystemExit("revoke needs --key")
        row = revoke(store, a.key)
        print(f"revoked {row['id']} ({row['label']}) at {row['revoked_at']}")
    else:
        print(json.dumps(events(store, a.key), indent=1))


if __name__ == "__main__":
    main()
