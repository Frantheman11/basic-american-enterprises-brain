"""Fixing a Research outcome's notes after saving (CRM feedback batch 2, 2026-09-29).

Reps write most of their notes on the Research card ("Record outcome"). Those notes could not
be corrected. `research.outcome.edit` changes the NOTES only: the outcome itself moved the lead
(its card, its rest, its clock), so changing it would rewrite history that other things read.
To correct an outcome, record a new one.

Same rules as a note on a record (record_edits.py): only the person who wrote it, or an
administrator; the line shows "edited"; the old text goes into the audit (`before`); a revision
gives two people editing at once the normal conflict error. The matching line in the contact's
Relationship history (written in the same save, with the same time) is corrected with it.
"""
from . import crm_store


def migrate(db):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(research_outcomes)")}
    for column, kind in (("revision", "INTEGER NOT NULL DEFAULT 1"), ("edited_at", "TEXT"), ("edited_by", "TEXT")):
        if column not in columns:
            db.execute(f"ALTER TABLE research_outcomes ADD COLUMN {column} {kind}")


def change(store, db, prof, person, data, stamp):
    oid = crm_store.text(data.get("id"), "Outcome", 80, True)
    found = db.execute("SELECT * FROM research_outcomes WHERE id=?", (oid,)).fetchone()
    if not found:
        raise crm_store.CRMError("Record not found", 404)
    row = dict(found)
    if row["actor"] != "human:" + person.id and not store.is_admin(prof, person):
        raise crm_store.CRMError("Only the person who wrote it, or an administrator, can change it", 403)
    store._revision(row, data)
    notes = crm_store.text(data.get("notes", ""), "Notes", 4000)
    db.execute("UPDATE research_outcomes SET notes=?,edited_at=?,edited_by=?,revision=revision+1 WHERE id=?",
               (notes, stamp, "human:" + person.id, oid))
    claim = db.execute("SELECT contact_id FROM research_claims WHERE place_id=?", (row["place_id"],)).fetchone()
    from .research_claims import WORDS
    lines = db.execute(
        "SELECT id,body FROM activities WHERE contact_id=? AND kind='outcome' AND actor=? AND created_at=?",
        (claim["contact_id"], row["actor"], row["created_at"])).fetchall() if claim and claim["contact_id"] else []
    line = next((a for a in lines if a["body"].startswith(WORDS.get(row["outcome"], "?") + " (")), None)
    if line:
        old = row["notes"]
        head = line["body"][:-len(" " + old)] if old and line["body"].endswith(" " + old) else line["body"]
        db.execute("UPDATE activities SET body=?,edited_at=?,edited_by=?,revision=revision+1 WHERE id=?",
                   (head + (" " + notes if notes else ""), stamp, "human:" + person.id, line["id"]))
    return {"ok": True, "id": oid, "before": {"notes": row["notes"]}}
