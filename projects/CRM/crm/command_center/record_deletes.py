"""Deleting a duplicate contact or a wrong deal, and putting it back (CRM feedback batch 3, 2026-09-29).

    contact.delete    the record's owner or an administrator
    deal.delete       the same; a WON deal only by an administrator (it moves the leaderboard)
    contact.restore   an administrator only
    deal.restore      an administrator only

A delete is soft: the row stays with `deleted_at` and `deleted_by`, and every reader skips it
(the snapshot, the leaderboard's wins, "Waiting for you", the duplicate check, Research's Take).
Nothing else is touched, so a restore brings the record back exactly as it was, with its deals,
to-dos and notes. Calls and visits logged on it still count on the leaderboard: the work happened.

Refused, with the reason, where a delete would leave something broken:
- the business record;
- a company with people still linked (delete or move them first);
- a record or card that a live Research lead points at (release the lead, or record an outcome).
"""
from datetime import datetime

from . import crm_store, research_claims

ALLOWED = {"contact.delete": {"id", "revision"}, "contact.restore": {"id"},
           "deal.delete": {"id", "revision"}, "deal.restore": {"id"}}


def migrate(db):
    for table in ("contacts", "deals"):
        columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for column in ("deleted_at", "deleted_by"):
            if column not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def listing(db):
    """What an administrator can restore: newest first."""
    return {"contacts": [dict(r) for r in db.execute(
                "SELECT id,name,kind,owner,company_id,deleted_at,deleted_by FROM contacts "
                "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")],
            "deals": [dict(r) for r in db.execute(
                "SELECT d.id,d.title,d.contact_id,d.stage,d.amount_cents,d.currency,d.deleted_at,d.deleted_by,"
                "c.name AS contact_name FROM deals d JOIN contacts c ON c.id=d.contact_id "
                "WHERE d.deleted_at IS NOT NULL AND c.deleted_at IS NULL ORDER BY d.deleted_at DESC")]}


def _held(db, prof, stamp, **where):
    """The live Research claim on this contact or card, if any."""
    rule = research_claims.policy(prof)
    if rule is None:
        return None
    (column, value), = where.items()
    at = datetime.fromisoformat(stamp)
    return next((r for r in db.execute(f"SELECT * FROM research_claims WHERE {column}=?", (value,))
                 if research_claims.live(r, rule, at)), None)


def change(store, db, prof, person, action, data, stamp):
    CRMError = crm_store.CRMError
    if set(data) - ALLOWED[action]:
        raise CRMError("Unexpected fields in command")
    rid = crm_store.text(data.get("id"), "Record ID", 80, True)
    admin = store.is_admin(prof, person)
    by = "human:" + person.id
    if action.endswith(".restore") and not admin:
        raise CRMError("Only an administrator can restore a deleted record", 403)

    if action.startswith("contact."):
        row = db.execute("SELECT * FROM contacts WHERE id=?", (rid,)).fetchone()
        if row is None or (not admin and row["owner"] != person.id):
            raise CRMError("Record not found", 404)
        if action == "contact.restore":
            if not row["deleted_at"]:
                raise CRMError("This record is not deleted", 409)
            company = row["company_id"] and db.execute(
                "SELECT kind, deleted_at FROM contacts WHERE id=?", (row["company_id"],)).fetchone()
            keep = row["company_id"] if company and company["kind"] == "company" and not company["deleted_at"] else None
            db.execute("UPDATE contacts SET deleted_at=NULL,deleted_by=NULL,company_id=?,revision=revision+1,"
                       "updated_at=? WHERE id=?", (keep, stamp, rid))
            return {"ok": True, "id": rid}
        if row["deleted_at"]:
            raise CRMError("Record not found", 404)
        store._revision(row, data)
        if row["kind"] == "business":
            raise CRMError("The business record cannot be deleted")
        people = db.execute("SELECT count(*) FROM contacts WHERE company_id=? AND deleted_at IS NULL", (rid,)).fetchone()[0]
        if people:
            raise CRMError(f"{people} {'person is' if people == 1 else 'people are'} linked to this company. "
                           "Delete or move them first.", 409)
        if _held(db, prof, stamp, contact_id=rid):
            raise CRMError("This is a live Research lead. Release it, or record an outcome, first.", 409)
        db.execute("UPDATE contacts SET deleted_at=?,deleted_by=?,revision=revision+1,updated_at=? WHERE id=?",
                   (stamp, by, stamp, rid))
        return {"ok": True, "id": rid}

    row = db.execute("SELECT * FROM deals WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise CRMError("Record not found", 404)
    contact = db.execute("SELECT * FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
    if contact is None or (not admin and contact["owner"] != person.id):
        raise CRMError("Record not found", 404)
    if action == "deal.restore":
        if not row["deleted_at"]:
            raise CRMError("This deal is not deleted", 409)
        if contact["deleted_at"]:
            raise CRMError(f"Restore {contact['name']} first", 409)
        stages = store.stages(prof, db)
        stage = row["stage"] if any(s["id"] == row["stage"] for s in stages) else \
            next(s["id"] for s in stages if not s["outcome"])  # its stage was removed meanwhile
        db.execute("UPDATE deals SET deleted_at=NULL,deleted_by=NULL,stage=?,revision=revision+1,updated_at=? WHERE id=?",
                   (stage, stamp, rid))
        return {"ok": True, "id": rid}
    if row["deleted_at"] or contact["deleted_at"]:
        raise CRMError("Record not found", 404)
    store._revision(row, data)
    if row["won_at"] and not admin:
        raise CRMError("A won deal counts on the leaderboard. Ask an administrator to delete it.", 403)
    if _held(db, prof, stamp, deal_id=rid):
        raise CRMError("This card belongs to a live Research lead. Release it, or record an outcome, first.", 409)
    db.execute("UPDATE deals SET deleted_at=?,deleted_by=?,revision=revision+1,updated_at=? WHERE id=?",
               (stamp, by, stamp, rid))
    return {"ok": True, "id": rid}
