"""Prep package state for Research leads: the research document and the printout approval.

- **Document.** A holder asks for it (`research.document.request`). The request is an audit
  row and an outbox event like every command. `tools/research_doc.py` (the worker, or Sam by
  hand until the worker is installed) writes it with a model OUTSIDE the web request and
  saves it here. A document that fails its check (em dash, banned stat, missing section) is
  stored as failed with its problems, and is never shown as ready.
- **Printout.** A holder asks for approval (`research.print.request`). Only a named approver
  (`crm.research.print_approvers`) can approve or revoke (`research.print.review`). The
  approval is bound to the scan it was made on: a newer scan needs a new approval. The
  server refuses to render a printout without a current approval.

Called only from `crm_store.Store`, inside its transaction, and from the server's read routes.
"""
import json
from pathlib import Path
from uuid import uuid4

from . import access, crm_store, prep, research, research_claims

DDL = """
CREATE TABLE IF NOT EXISTS research_documents (
    place_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('requested','ready','failed')),
    body TEXT NOT NULL DEFAULT '', problems TEXT NOT NULL DEFAULT '[]', model TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, written_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS research_prints (
    place_id TEXT PRIMARY KEY, business_id TEXT NOT NULL, basis TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('requested','approved','revoked')),
    requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
    reviewed_by TEXT, reviewed_at TEXT, revision INTEGER NOT NULL DEFAULT 1);
"""


def migrate(db):
    db.executescript(DDL)
    columns = {r["name"] for r in db.execute("PRAGMA table_info(research_documents)")}
    if "script" not in columns:  # version 8: the phone script written for this lead
        db.execute("ALTER TABLE research_documents ADD COLUMN script TEXT NOT NULL DEFAULT ''")


def request_document(db, pid, who, stamp):
    """Ask the worker for this lead's research document and tailored script, unless one is
    already written or waiting. Taking a lead calls this, so the script is there by the call."""
    row = db.execute("SELECT status FROM research_documents WHERE place_id=?", (pid,)).fetchone()
    if row is not None and row["status"] in ("requested", "ready"):
        return False
    db.execute("INSERT INTO research_documents (place_id,status,requested_by,requested_at) "
               "VALUES (?,'requested',?,?) ON CONFLICT(place_id) DO UPDATE SET status='requested',"
               "requested_by=excluded.requested_by,requested_at=excluded.requested_at,"
               "revision=revision+1", (pid, who, stamp))
    return True


def _root(prof):
    return Path(prof["_path"]).resolve().parents[2]


def approver(prof, person):
    names = ((prof.get("crm") or {}).get("research") or {}).get("print_approvers") or []
    return (person is not None and person.id in names and access.sees(prof, person) == "all")


def _holder(db, prof, person, pid, stamp):
    """The person may act on this lead: they hold it, or they are an administrator."""
    from datetime import datetime
    rule = research_claims.policy(prof)
    row = db.execute("SELECT * FROM research_claims WHERE place_id=?", (pid,)).fetchone()
    live = rule is not None and research_claims.live(row, rule, datetime.fromisoformat(stamp))
    if research_claims._admin(prof, person):
        return
    if not live or row["owner"] != person.id:
        raise crm_store.CRMError("Take this lead first. The package is for the person working it.", 403)


def change(store, db, prof, person, action, data, stamp):
    CRMError = crm_store.CRMError
    allowed = {"research.document.request": {"place_id"},
               "research.print.request": {"place_id"},
               "research.print.review": {"place_id", "revision", "decision"}}
    if set(data) - allowed[action]:
        raise CRMError("Unexpected fields in command")
    pid = crm_store.text(data.get("place_id"), "Lead", 200, True)
    company = research.find(prof, _root(prof), pid)
    if company is None:
        raise CRMError("Lead not found in the research pool", 404)

    if action == "research.document.request":
        _holder(db, prof, person, pid, stamp)
        db.execute("INSERT INTO research_documents (place_id,status,requested_by,requested_at) "
                   "VALUES (?,'requested',?,?) ON CONFLICT(place_id) DO UPDATE SET status='requested',"
                   "requested_by=excluded.requested_by,requested_at=excluded.requested_at,"
                   "revision=revision+1", (pid, person.id, stamp))
        return pid

    if action == "research.print.request":
        _holder(db, prof, person, pid, stamp)
        if not company["business_id"] or not company["scanned_at"]:
            raise CRMError("A printout is built from a scan of their website. This lead has none.", 409)
        db.execute("INSERT INTO research_prints (place_id,business_id,basis,status,requested_by,requested_at) "
                   "VALUES (?,?,?,'requested',?,?) ON CONFLICT(place_id) DO UPDATE SET "
                   "business_id=excluded.business_id,basis=excluded.basis,status='requested',"
                   "requested_by=excluded.requested_by,requested_at=excluded.requested_at,"
                   "reviewed_by=NULL,reviewed_at=NULL,revision=revision+1",
                   (pid, company["business_id"], company["scanned_at"], person.id, stamp))
        return pid

    # research.print.review
    if not approver(prof, person):
        raise CRMError("Only a named approver can approve a printout", 403)
    row = db.execute("SELECT * FROM research_prints WHERE place_id=?", (pid,)).fetchone()
    if row is None:
        raise CRMError("Nobody has asked for this printout yet", 404)
    store._revision(row, data)
    decision = data.get("decision")
    if decision not in ("approved", "revoked"):
        raise CRMError("Choose approved or revoked")
    if decision == "approved" and row["basis"] != company["scanned_at"]:
        raise CRMError("The site was scanned again since this was requested. Ask for it again.", 409)
    db.execute("UPDATE research_prints SET status=?,reviewed_by=?,reviewed_at=?,revision=revision+1 "
               "WHERE place_id=?", (decision, person.id, stamp, pid))
    return pid


def save_document(store, place_id, body, model, cfg, script=None):
    """The worker's write. Checked here, so no caller can store an unchecked document.
    `script` is the model's tailored phone script (raw text); stored only if it passes."""
    problems = prep.check_document(body, cfg)
    tailored, script_problems = prep.parse_script(script, cfg) if script is not None else (None, [])
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM research_documents WHERE place_id=?", (place_id,)).fetchone()
        if row is None:
            raise crm_store.CRMError("No document was requested for this lead", 404)
        db.execute("UPDATE research_documents SET status=?,body=?,problems=?,model=?,written_at=?,script=?,"
                   "revision=revision+1 WHERE place_id=?",
                   ("failed" if problems else "ready", body, json.dumps(problems + [f"script: {p}" for p in script_problems]),
                    model, stamp, json.dumps(tailored) if tailored else "", place_id))
        payload = json.dumps({"entity_id": place_id, "result": {"status": "failed" if problems else "ready",
                                                                "problems": problems, "model": model}})
        db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)",
                   (str(uuid4()), str(uuid4()), "agent:research-document", "research.document.save",
                    place_id, payload, stamp))
    return problems


def holder_name(store, prof, place_id):
    """Who holds the lead now, for the name in the script. Empty when nobody does."""
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
    with store.connection() as db:
        row = db.execute("SELECT owner FROM research_claims WHERE place_id=?", (place_id,)).fetchone()
    return names.get(row["owner"], "") if row else ""


def pending_documents(store):
    with store.connection() as db:
        return [r["place_id"] for r in db.execute(
            "SELECT place_id FROM research_documents WHERE status='requested' ORDER BY requested_at")]


def read(store, prof, person, root, place_id):
    """One lead's package, for the company card."""
    company = research.find(prof, root, place_id)
    if company is None:
        raise crm_store.CRMError("Lead not found in the research pool", 404)
    cfg = research.industry_file(prof, root)
    industry = next((i for i in cfg["industries"] if i["id"] == company["industry"]), {})
    lane = next((l for l in cfg.get("lanes") or [] if l["id"] == company["lane"]), {})
    sender = ((prof.get("crm") or {}).get("research") or {}).get("sender") or {}
    with store.connection() as db:
        db.execute("BEGIN")
        doc = db.execute("SELECT * FROM research_documents WHERE place_id=?", (place_id,)).fetchone()
        pr = db.execute("SELECT * FROM research_prints WHERE place_id=?", (place_id,)).fetchone()
    doc = dict(doc) if doc else None
    tailored = None
    if doc:
        doc["problems"] = json.loads(doc["problems"] or "[]")
        tailored = json.loads(doc.pop("script") or "null")
    pr = dict(pr) if pr else None
    if pr:
        pr["current"] = pr["status"] == "approved" and pr["basis"] == company["scanned_at"]
    return {
        "place_id": place_id,
        "email": prep.email_draft(company, lane, sender, person.name, prep.banned_phrases(cfg)),
        "checklist": prep.checklist(cfg, company["lane"]),
        "script": prep.phone_script(cfg, company, company["lane"], person.name),
        "tailored_script": tailored,
        "document": doc,
        "print": pr,
        "printable": bool(company["business_id"] and company["scanned_at"]),
        "can_approve": approver(prof, person),
    }


def approved_business(store, prof, root, place_id):
    """The business_id to print, only with a current approval. Otherwise raises 403."""
    company = research.find(prof, root, place_id)
    with store.connection() as db:
        row = db.execute("SELECT * FROM research_prints WHERE place_id=?", (place_id,)).fetchone()
    if (company is None or row is None or row["status"] != "approved"
            or row["basis"] != company["scanned_at"]):
        raise crm_store.CRMError("This printout is not approved. Ask Sam to approve it first.", 403)
    return row["business_id"]
