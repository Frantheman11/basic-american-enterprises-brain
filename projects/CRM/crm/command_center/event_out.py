"""Event out: every outbox event, posted to one URL the business sets (2026-10-02).

The other half of the universal connector. Lead in is `lead_intake.py`; this is the CRM
telling the outside world what changed, so a Zapier or Make catch hook (or any small server)
can act on a new lead, a new task or a note. Level 1 of the slot (docs/module-system-game-plan).

Off by default. A profile with no `crm.event_out` block posts nothing. The block:

    "event_out": {
      "url": "https://hooks.example.com/catch/123/abc",
      "secret_env": "EVENT_OUT_SECRET",       # the NAME of the variable, never the secret
      "header": "X-CRM-Secret",               # optional; the header that carries it
      "events": ["lead.alert", "contact.create"],   # optional; default every event
      "max_attempts": 5,                      # optional; then the row waits for a person
      "backoff_seconds": 60                   # optional; doubles after each failure
    }

Each post is JSON {id, type, created_at, business, data} with `Idempotency-Key: <id>`, so a
receiver that sees an event twice can drop the second. 2xx = delivered. 429, 408 and 5xx and
a dropped connection retry with backoff (a 429's Retry-After wins). Any other 4xx is a wrong
URL or a deleted hook: it fails at once. A row that fails for good shows in the
administrator's "Waiting for you"; `retry` puts it back in the queue after the URL is fixed.

Delivery state lives in its own table (`event_deliveries`), never in `outbox.status`: the lead
alert already owns that column, and two workers on one status would hide each other's failures.
Events older than the day the block was switched on are never sent: a new hook does not get
the whole history.

    python -m command_center.event_out run --business <id> --crm-db <db> [--once]
    python -m command_center.event_out status --business <id> --crm-db <db>
    python -m command_center.event_out retry --business <id> --crm-db <db> --event <id>
"""
import argparse
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEADER = "X-CRM-Secret"
SINCE_KEY = "event_out_since"


def config(prof, environ=None):
    """The checked block, or None. A block that is present but wrong raises ValueError, so a
    worker refuses to start instead of dropping events."""
    block = (prof.get("crm") or {}).get("event_out")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("crm.event_out must be an object")
    url = block.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ValueError("crm.event_out.url must be an https address")
    name = block.get("secret_env")
    if not isinstance(name, str) or not name:
        raise ValueError("crm.event_out.secret_env must name the variable that holds the shared secret")
    secret = (environ if environ is not None else os.environ).get(name, "")
    if len(secret) < 16:
        raise ValueError(f"Set {name} to a shared secret of at least 16 characters")
    events = block.get("events")
    if events is not None and (not isinstance(events, list) or not all(isinstance(e, str) for e in events)):
        raise ValueError("crm.event_out.events must list event types")
    number = lambda v, d: v if type(v) is int and v > 0 else d
    return {"url": url, "secret": secret, "header": block.get("header") or HEADER,
            "events": events, "max_attempts": number(block.get("max_attempts"), 5),
            "backoff": number(block.get("backoff_seconds"), 60)}


def authentic(cfg, headers):
    """A receiver's check: the shared secret, compared in constant time."""
    got = next((v for k, v in headers.items() if k.lower() == cfg["header"].lower()), "")
    return bool(got) and hmac.compare_digest(got, cfg["secret"])


def migrate(db):
    """Version 13: one row per event the worker has tried."""
    db.execute("""CREATE TABLE IF NOT EXISTS event_deliveries (
        id TEXT PRIMARY KEY REFERENCES outbox(id), status TEXT NOT NULL
        CHECK(status IN ('pending','sent','failed')), attempts INTEGER NOT NULL DEFAULT 0,
        next_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def post(url, body, headers, timeout=10):
    """(status, retry_after_seconds). A dropped connection is status 0."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        after = e.headers.get("Retry-After") if e.headers else None
        return e.code, int(after) if after and after.isdigit() else None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, None


def due(store, cfg, at=None):
    """Outbox events the worker should try now, oldest first."""
    at = at or _now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        since = db.execute("SELECT value FROM metadata WHERE key=?", (SINCE_KEY,)).fetchone()
        if since is None:  # switched on now: nothing older is sent
            db.execute("INSERT INTO metadata VALUES (?,?)", (SINCE_KEY, at))
            since = (at,)
        rows = db.execute(
            "SELECT o.id, o.event_type, o.payload, o.created_at, d.attempts FROM outbox o "
            "LEFT JOIN event_deliveries d ON d.id=o.id WHERE o.created_at>=? AND "
            "(d.id IS NULL OR (d.status='pending' AND d.next_at<=?)) ORDER BY o.created_at",
            (since[0], at)).fetchall()
    return [dict(r) for r in rows if cfg["events"] is None or r["event_type"] in cfg["events"]]


def _save(store, eid, status, attempts, next_at, error):
    with store.connection() as db:
        db.execute("INSERT INTO event_deliveries VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "status=excluded.status, attempts=excluded.attempts, next_at=excluded.next_at, "
                   "last_error=excluded.last_error, updated_at=excluded.updated_at",
                   (eid, status, attempts, next_at, error[:500], _now()))


def envelope(event_id, event_type, created_at, business, payload):
    """The body every receiver gets. One shape, so a Zap built on one event reads them all."""
    return json.dumps({"id": event_id, "type": event_type, "created_at": created_at,
                       "business": business, "data": payload}).encode()


def run_once(store, prof, cfg, send=post, clock=None):
    """Try every due event once. Returns {"sent", "retry", "failed"} counts."""
    counts = {"sent": 0, "retry": 0, "failed": 0}
    at = clock() if clock else datetime.now(timezone.utc)
    for row in due(store, cfg, at.isoformat(timespec="microseconds")):
        body = envelope(row["id"], row["event_type"], row["created_at"], prof["business_id"],
                        json.loads(row["payload"]))
        status, after = send(cfg["url"], body, {"Content-Type": "application/json", "Idempotency-Key": row["id"],
                                                cfg["header"]: cfg["secret"]})
        attempts = (row["attempts"] or 0) + 1
        if 200 <= status < 300:
            _save(store, row["id"], "sent", attempts, at.isoformat(), "")
            counts["sent"] += 1
            continue
        error = f"HTTP {status}" if status else "no answer from the receiver"
        if (status in (0, 408, 429) or status >= 500) and attempts < cfg["max_attempts"]:
            wait = after if after is not None else cfg["backoff"] * 2 ** (attempts - 1)
            _save(store, row["id"], "pending", attempts, (at + timedelta(seconds=wait)).isoformat(), error)
            counts["retry"] += 1
        else:
            _save(store, row["id"], "failed", attempts, at.isoformat(), error)
            counts["failed"] += 1
    return counts


def failed(db):
    return [dict(r) for r in db.execute(
        "SELECT d.id, d.attempts, d.last_error, d.updated_at, o.event_type, o.payload FROM event_deliveries d "
        "JOIN outbox o ON o.id=d.id WHERE d.status='failed' ORDER BY d.updated_at")]


def waiting(db):
    """Failed deliveries, for the administrator's "Waiting for you" list (approvals.py)."""
    out = []
    for r in failed(db):
        contact = (json.loads(r["payload"]).get("data") or {}).get("contact_id")
        out.append({"kind": "event", "id": r["id"], "contact_id": contact,
                    "title": f"Event not delivered ({r['event_type']}, {r['last_error']}): "
                             "fix the hook address, then retry",
                    "by": "Event out", "at": r["updated_at"]})
    return out


def retry(store, eid):
    with store.connection() as db:
        n = db.execute("UPDATE event_deliveries SET status='pending', attempts=0, next_at=?, updated_at=? "
                       "WHERE id=? AND status='failed'", (_now(), _now(), eid)).rowcount
    return bool(n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Post CRM outbox events to the profile's hook.")
    ap.add_argument("command", choices=("run", "status", "retry"))
    ap.add_argument("--business", required=True)
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--once", action="store_true", help="one pass, then exit (a timer's way)")
    ap.add_argument("--every", type=int, default=30, help="seconds between passes")
    ap.add_argument("--event")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from command_center import crm_store, profiles
    prof = profiles.load(a.business, str(root))
    store = crm_store.Store(a.crm_db, a.business)
    if a.command == "status":
        with store.connection() as db:
            rows = db.execute("SELECT status, count(*) n FROM event_deliveries GROUP BY status").fetchall()
            print("deliveries: " + (", ".join(f"{r['status']} {r['n']}" for r in rows) or "none yet"))
            for r in failed(db):
                print(f"  failed {r['id']}  {r['event_type']}  {r['last_error']}")
        return
    if a.command == "retry":
        print("queued again" if retry(store, a.event or "") else "no failed event with that id")
        return
    cfg = config(prof)
    if cfg is None:
        print("crm.event_out is not set in this profile: nothing to post")
        return
    while True:
        counts = run_once(store, prof, cfg)
        if any(counts.values()):
            print(f"{_now()}  sent {counts['sent']}  retry {counts['retry']}  failed {counts['failed']}", flush=True)
        if a.once:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
