"""Research requests: anyone who works leads asks for more research from the Research screen.

Two kinds (docs/crm/crm-cta-go-live-and-rebrand-brief-2026-09-27.md §5):

    discover   find more companies: one industry, one ring. The research worker
               (tools/research_worker.py) counts its searches first. Inside the month's
               budget (`monthly_search_cap`, Sam: 800, 2026-09-27) it runs by itself; one that
               could pass the budget waits for a person named in `request_approvers`.
               The worker also asks by itself (actor agent:research-worker) when an industry
               that is already being worked runs low on open leads (`auto_refill_below`).
    question   a free question ("which sign shops near Orange run ads?"). A person answers it
               in the CRM or in a work session. Nothing runs by itself.

The CRM only records the request. It never calls a paid API: the worker does, on the server,
and writes its result back here with the actor `agent:research-worker`.
Called only from `crm_store.Store`, inside its transaction, except the worker functions at
the end, which open their own transaction.
"""
import json
from datetime import datetime, timedelta
from uuid import uuid4

from . import access, crm_store, research

KINDS = ("discover", "question")
OPEN = ("queued", "needs_approval", "approved", "running")
WORKER = "agent:research-worker"

DDL = """
CREATE TABLE IF NOT EXISTS research_requests (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('discover','question')),
    industry TEXT NOT NULL DEFAULT '', ring INTEGER, body TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','needs_approval','approved','running','done',
                                           'failed','declined')),
    estimate_usd REAL, calls INTEGER, result TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT '', lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_requests_status ON research_requests(status, created_at);
"""


def migrate(db):
    db.executescript(DDL)


def policy(prof):
    spec = (prof.get("crm") or {}).get("research") or {}
    cap = spec.get("monthly_search_cap")
    return {"monthly_cap": cap if type(cap) is int and cap >= 0 else 0,
            "approvers": [a for a in spec.get("request_approvers") or [] if isinstance(a, str)]}


def month(stamp=None):
    """The calendar month the cap counts, in the business's own time zone (Pacific here)."""
    from datetime import timezone
    zone = timezone(timedelta(hours=-7))
    at = datetime.fromisoformat(stamp or crm_store.now()).astimezone(zone)
    return at.isoformat()[:7]


def approver(prof, person):
    return (person is not None and person.id in policy(prof)["approvers"]
            and access.sees(prof, person) == "all")


def read(store, prof, person):
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
    with store.connection() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM research_requests ORDER BY created_at DESC LIMIT 40")]
    for r in rows:
        who = r["actor"].removeprefix("human:")
        r["actor_name"] = names.get(who, who)
        r["mine"] = who == person.id
        r.pop("lease_until", None)
    used = research.searches_this_month(prof, _root(prof), month())
    return {"requests": rows, "request_policy": {**policy(prof), "used_this_month": used,
                                                 "can_decide": approver(prof, person)}}


def change(store, db, prof, person, action, data, stamp):
    """research.request or research.request.decide. Returns the request id."""
    CRMError = crm_store.CRMError
    if action == "research.request":
        kind = data.get("kind")
        if kind not in KINDS:
            raise CRMError("Choose find more companies or ask a question")
        body = crm_store.text(data.get("body", ""), "Question", 1000, kind == "question")
        industry, ring = "", None
        if kind == "discover":
            cfg = research.industry_file(prof, _root(prof))
            ids = [i["id"] for i in cfg["industries"]]
            industry = data.get("industry")
            if industry not in ids:
                raise CRMError("Choose an industry from the list")
            ring = data.get("ring")
            if type(ring) is not int or not 0 <= ring < len(cfg.get("rings_miles") or []):
                raise CRMError("Choose a ring from the list")
            twin = db.execute("SELECT 1 FROM research_requests WHERE kind='discover' AND industry=? "
                              "AND ring=? AND status IN ('queued','needs_approval','approved','running')",
                              (industry, ring)).fetchone()
            if twin:
                raise CRMError("Someone already asked for this. It is in the list below.", 409)
        rid = str(uuid4())
        db.execute("INSERT INTO research_requests (id,kind,industry,ring,body,actor,status,created_at,"
                   "updated_at) VALUES (?,?,?,?,?,?,'queued',?,?)",
                   (rid, kind, industry, ring, body, "human:" + person.id, stamp, stamp))
        return rid

    # research.request.decide
    if not approver(prof, person):
        raise CRMError("Only an approver can decide research requests", 403)
    rid = crm_store.text(data.get("id"), "Request", 80, True)
    row = db.execute("SELECT * FROM research_requests WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise CRMError("Request not found", 404)
    store._revision(row, data)
    decision = data.get("decision")
    note = crm_store.text(data.get("note", ""), "Note", 4000, decision == "answered")
    if row["kind"] == "discover":
        if decision not in ("approved", "declined") or row["status"] != "needs_approval":
            raise CRMError("Approve or decline a request that is waiting for approval", 409)
        status = decision
    else:
        if decision not in ("answered", "declined") or row["status"] not in ("queued",):
            raise CRMError("Answer or decline an open question", 409)
        status = "done" if decision == "answered" else "declined"
    db.execute("UPDATE research_requests SET status=?,result=?,decided_by=?,revision=revision+1,"
               "updated_at=? WHERE id=?",
               (status, note or row["result"], person.id, stamp, rid))
    return rid


def _root(prof):
    from pathlib import Path
    return Path(prof["_path"]).resolve().parents[2]


# ---- the worker's side: its own transaction, actor agent:research-worker ------------------

def _audit(db, action, rid, result, stamp):
    payload = json.dumps({"entity_id": rid, "result": result})
    eid = str(uuid4())
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)",
               (eid, str(uuid4()), WORKER, action, rid, payload, stamp))


def lease(store, minutes=30, attempts=3):
    """The oldest discover request that may run: queued, or approved by a person, or a run
    whose lease ran out (the worker stopped mid-run). A request that failed `attempts` times
    is marked failed instead of retried forever."""
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        stale = [dict(r) for r in db.execute(
            "SELECT * FROM research_requests WHERE status='running' AND lease_until<?", (stamp,))]
        for r in stale:
            if r["attempts"] >= attempts:
                db.execute("UPDATE research_requests SET status='failed',result=?,lease_until=NULL,"
                           "revision=revision+1,updated_at=? WHERE id=?",
                           (f"Stopped {r['attempts']} times before it finished.", stamp, r["id"]))
                _audit(db, "research.request.failed", r["id"], {"attempts": r["attempts"]}, stamp)
        row = db.execute(
            "SELECT * FROM research_requests WHERE kind='discover' AND (status IN ('queued','approved') "
            "OR (status='running' AND lease_until<?)) ORDER BY created_at LIMIT 1", (stamp,)).fetchone()
        if row is None:
            return None
        until = (datetime.fromisoformat(stamp) + timedelta(minutes=minutes)).isoformat()
        approved = row["status"] == "approved" or (row["status"] == "running" and bool(row["decided_by"]))
        db.execute("UPDATE research_requests SET status='running',lease_until=?,attempts=attempts+1,"
                   "revision=revision+1,updated_at=? WHERE id=?", (until, stamp, row["id"]))
        return {**dict(row), "approved": approved}


def ask(store, industry, ring, note):
    """The worker asks by itself: an industry being worked is running low. Never twice."""
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM research_requests WHERE kind='discover' AND industry=? "
                      "AND status IN ('queued','needs_approval','approved','running')", (industry,)).fetchone():
            return None
        rid = str(uuid4())
        db.execute("INSERT INTO research_requests (id,kind,industry,ring,body,actor,status,created_at,"
                   "updated_at) VALUES (?,'discover',?,?,?,?,'queued',?,?)",
                   (rid, industry, ring, note, WORKER, stamp, stamp))
        _audit(db, "research.request", rid, {"industry": industry, "ring": ring, "note": note}, stamp)
        return rid


def hold(store, rid, estimate_usd, calls, note=""):
    """Over the limit and not approved: wait for a person."""
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE research_requests SET status='needs_approval',estimate_usd=?,calls=?,result=?,"
                   "lease_until=NULL,revision=revision+1,updated_at=? WHERE id=?",
                   (estimate_usd, calls, note, stamp, rid))
        _audit(db, "research.request.hold", rid, {"estimate_usd": estimate_usd, "calls": calls}, stamp)


def finish(store, rid, ok, result, estimate_usd=None, calls=None):
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE research_requests SET status=?,result=?,estimate_usd=COALESCE(?,estimate_usd),"
                   "calls=COALESCE(?,calls),lease_until=NULL,revision=revision+1,updated_at=? WHERE id=?",
                   ("done" if ok else "failed", result, estimate_usd, calls, stamp, rid))
        _audit(db, "research.request.done" if ok else "research.request.failed", rid,
               {"result": result, "estimate_usd": estimate_usd, "calls": calls}, stamp)
