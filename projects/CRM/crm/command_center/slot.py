"""The slot: the five verbs every module speaks, whichever CRM holds the records (2026-10-02).

A module that uses only these five verbs runs on our CRM or on the CRM a business already
uses. The second brain is the motherboard, a module is a card, and this file is the slot.

    upsert_contact   add a contact, or update the one with the same email
    add_note         put a note on a contact's history
    make_task        give a contact a follow-up with a due time
    read             one person's contacts and open tasks, plus what waits on them
    lead_alert       turn the CRM's own "new lead" push into contact ids, after checking it

Our CRM is the first card behind the slot (`CRMSlot`). Another CRM is an adapter that
implements the same five methods (`slot_hubspot.py`), never a fork of a module. A module
never imports a store or a vendor client: it asks `for_profile` for a slot.

Every external call has an error path (docs/products/cta-buildout-architecture-v1.md, R4). An adapter
raises `SlotError` with a plain reason; `deliver_lead` and the morning brief put the failure in
"Waiting for you" instead of losing it. Nothing in this file sends email.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

VERBS = ("contact", "note", "task", "read", "new_lead_alert")  # the catalog's `connector.verbs`


class SlotError(Exception):
    """A verb that could not finish. `kind` is one of auth, rate_limit, invalid, not_found,
    unavailable; `reason` is a sentence a business owner can act on."""

    def __init__(self, kind, reason, detail=""):
        super().__init__(reason)
        self.kind, self.reason, self.detail = kind, reason, str(detail)[:2000]


class Slot:
    """The interface. Each verb returns plain dicts and ids, never a vendor object."""
    name = "slot"

    def upsert_contact(self, contact):
        """`contact`: {name, email, phone, company}. Returns the contact id."""
        raise NotImplementedError

    def add_note(self, contact_id, body):
        raise NotImplementedError

    def make_task(self, contact_id, title, due_at):
        """`due_at`: an aware datetime. Returns the task id."""
        raise NotImplementedError

    def read(self, person):
        """{"contacts": [...], "tasks": [...], "research_links": {}, "waiting": [...]}.

        contacts: id, name, phone, email, owner, kind, company_id. tasks: id, contact_id,
        title, due_at (ISO, with offset), status ("open" or other). `owner` is compared with
        `person.id`, so an adapter maps its own owner ids to the profile's people."""
        raise NotImplementedError

    def lead_alert(self, headers, raw, method="POST", uri=""):
        """Check a pushed "new lead" event and return the new contact ids. Raises SlotError
        ("auth") when the push is not from the CRM it claims to be."""
        raise NotImplementedError


# ---- our CRM -------------------------------------------------------------------------
class CRMSlot(Slot):
    """Our CRM behind the slot. Writes go through `Store.execute` as `person`, so the role,
    ownership, audit and outbox rules all still apply; reads are the person's own snapshot."""
    name = "crm"

    def __init__(self, store, prof, person=None, environ=None):
        self.store, self.prof, self.person, self.environ = store, prof, person, environ

    def _run(self, action, data):
        if self.person is None:
            raise SlotError("auth", "A CRM write needs the person it is done for")
        from . import crm_store
        try:
            return self.store.execute(self.prof, self.person,
                                      {"action": action, "data": data, "request_id": str(uuid4())})
        except crm_store.CRMError as e:
            kind = {401: "auth", 403: "auth", 404: "not_found", 409: "invalid", 503: "unavailable"}
            raise SlotError(kind.get(e.status, "invalid"), str(e)) from None

    def upsert_contact(self, contact):
        email = (contact.get("email") or "").strip()
        if email:
            snap = self.store.snapshot(self.prof, self.person)
            found = next((c for c in snap["contacts"] if c["email"].casefold() == email.casefold()), None)
            if found:
                data = {k: contact[k] for k in ("name", "phone") if contact.get(k)}
                if data:
                    self._run("contact.update", {"id": found["id"], "revision": found["revision"], **data})
                return found["id"]
        data = {"name": contact.get("name") or email, "confirm_duplicate": True}
        data.update({k: contact[k] for k in ("email", "phone") if contact.get(k)})
        if contact.get("company"):
            data["company_name"] = contact["company"]
        return self._run("contact.create", data)["id"]

    def add_note(self, contact_id, body):
        return self._run("activity.add", {"contact_id": contact_id, "kind": "note", "body": body})["id"]

    def make_task(self, contact_id, title, due_at):
        return self._run("task.create", {"contact_id": contact_id, "title": title,
                                         "due_at": due_at.isoformat()})["id"]

    def read(self, person):
        snap = self.store.snapshot(self.prof, person)
        return {"contacts": snap["contacts"], "tasks": snap["tasks"],
                "research_links": snap["research_links"], "waiting": snap["waiting"]}

    def lead_alert(self, headers, raw, method="POST", uri=""):
        """Our CRM pushes through the event-out worker; its shared secret is the check."""
        from . import event_out
        cfg = event_out.config(self.prof, self.environ)
        if not cfg or not event_out.authentic(cfg, headers):
            raise SlotError("auth", "This push did not carry the CRM's shared secret")
        event = json.loads(raw)
        payload = event.get("data") or {}           # the outbox row: {entity_id, data}
        inner = payload.get("data") or {}
        if event.get("type") == "lead.alert":
            return [inner["contact_id"]] if inner.get("contact_id") else []
        if event.get("type") == "contact.create" and inner.get("kind", "person") == "person":
            return [payload["entity_id"]] if payload.get("entity_id") else []
        return []


# ---- failures, for a slot with no CRM of ours behind it ---------------------------------
class FailureLog:
    """What an adapter could not do, kept so the morning brief can say so. One small SQLite
    file per business (`crm.slot.state_db`). Our own CRM does not need it: its failures are
    outbox rows, already in "Waiting for you"."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS slot_failures (
                id TEXT PRIMARY KEY, at TEXT NOT NULL, slot TEXT NOT NULL, verb TEXT NOT NULL,
                kind TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL,
                resolved_at TEXT)""")

    def record(self, slot, verb, error):
        fid = str(uuid4())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO slot_failures VALUES (?,?,?,?,?,?,?,NULL)",
                       (fid, datetime.now(timezone.utc).isoformat(timespec="seconds"), slot, verb,
                        error.kind, error.reason, error.detail))
        return fid

    def open(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM slot_failures WHERE resolved_at IS NULL ORDER BY at").fetchall()
        return [{"kind": "slot", "id": r["id"], "title": f"{r['slot']} {r['verb']} failed: {r['reason']}",
                 "by": r["slot"], "at": r["at"], "error": r["kind"]} for r in rows]

    def resolve(self, fid):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE slot_failures SET resolved_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(timespec="seconds"), fid))


def deliver_lead(slot, lead, task_title="Call back", due_minutes=60, failures=None, at=None):
    """One lead into any CRM: a contact, a note with what they sent, and a follow-up task.
    The same three things our intake writes. A failure is recorded and returned, never raised,
    so a website lead is never lost to a vendor's bad minute: {"ok", "contact_id", "error"}."""
    at = at or datetime.now(timezone.utc)
    step, cid = "contact", None
    try:
        if not (lead.get("email") or "").strip() and not (lead.get("phone") or "").strip():
            raise SlotError("invalid", "The lead has no email and no phone, so nobody can call it back")
        cid = slot.upsert_contact(lead)
        step = "note"
        slot.add_note(cid, lead.get("note") or lead.get("message") or "New lead")
        step = "task"
        slot.make_task(cid, f"{task_title}: {lead.get('name') or lead.get('email')}"[:200],
                       at + timedelta(minutes=due_minutes))
        return {"ok": True, "contact_id": cid, "error": None}
    except SlotError as e:
        if failures is not None:
            failures.record(slot.name, step, e)
        return {"ok": False, "contact_id": cid, "error": f"{e.kind}: {e.reason}"}


def for_profile(prof, root, store=None, person=None):
    """The slot a profile names in `crm.slot.kind`: "crm" (default) or "hubspot"."""
    cfg = (prof.get("crm") or {}).get("slot") or {}
    kind = cfg.get("kind", "crm")
    if kind == "crm":
        if store is None:
            raise ValueError("crm.slot.kind is crm, so the slot needs the CRM database")
        return CRMSlot(store, prof, person)
    if kind == "hubspot":
        from . import slot_hubspot
        return slot_hubspot.from_profile(prof, root)
    raise ValueError(f"crm.slot.kind {kind!r} has no adapter yet")
