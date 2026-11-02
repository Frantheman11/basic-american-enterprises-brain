"""Changing what was already saved (CRM feedback batch 1, Sam 2026-09-28).

    activity.update   fix a note, call, meeting or email: the body and its kind
    activity.delete   remove one written on the wrong record (a soft delete)
    task.archive      "Archive checked": checked to-dos leave their list

A note is a record of a conversation, so an edit must not look like the original: the note
shows "Edited <date>", and the text before the change goes into the audit with the command
(`before` in its result). Only the person who wrote it, or an administrator, can change it.
Each note has a revision, so two people editing at once get the normal conflict error.

A checked to-do stays on its list, struck through, until it is archived; it is then in the
"Done / archived" view, and unchecking it there (`task.update`, status open) puts it back.
"""
from . import crm_store

EDITABLE = ("note", "call", "meeting", "email")
ALLOWED = {"activity.update": {"id", "revision", "kind", "body"},
           "activity.delete": {"id", "revision"},
           "task.archive": {"ids"}}


def migrate(db):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(activities)")}
    for column, kind in (("revision", "INTEGER NOT NULL DEFAULT 1"), ("edited_at", "TEXT"),
                         ("edited_by", "TEXT"), ("deleted_at", "TEXT")):
        if column not in columns:
            db.execute(f"ALTER TABLE activities ADD COLUMN {column} {kind}")
    if "archived_at" not in {r["name"] for r in db.execute("PRAGMA table_info(tasks)")}:
        db.execute("ALTER TABLE tasks ADD COLUMN archived_at TEXT")
        # Before this, a checked to-do left its list at once. Those stay off it: archived once, here.
        db.execute("UPDATE tasks SET archived_at=COALESCE(completed_at,updated_at) WHERE status='completed'")


def change(store, db, prof, person, action, data, stamp):
    if set(data) - ALLOWED[action]:
        raise crm_store.CRMError("Unexpected fields in command")
    if action == "task.archive":
        return archive(store, db, prof, person, data, stamp)
    rid = crm_store.text(data.get("id"), "Record ID", 80, True)
    found = db.execute("SELECT * FROM activities WHERE id=? AND deleted_at IS NULL", (rid,)).fetchone()
    if not found:
        raise crm_store.CRMError("Record not found", 404)
    row = dict(found)
    store.contact(db, prof, person, row["contact_id"])
    if row["kind"] not in EDITABLE:
        raise crm_store.CRMError("Only notes, calls, meetings and emails can be changed")
    if row["actor"] != "human:" + person.id and not store.is_admin(prof, person):
        raise crm_store.CRMError("Only the person who wrote it, or an administrator, can change it", 403)
    store._revision(row, data)
    if action == "activity.delete":
        db.execute("UPDATE activities SET deleted_at=?,edited_by=?,revision=revision+1 WHERE id=?",
                   (stamp, "human:" + person.id, rid))
    else:
        kind = data.get("kind", row["kind"])
        if kind not in EDITABLE:
            raise crm_store.CRMError("Choose a supported activity type")
        body = crm_store.text(data.get("body", row["body"]), "Activity note", 10000, True)
        db.execute("UPDATE activities SET kind=?,body=?,edited_at=?,edited_by=?,revision=revision+1 WHERE id=?",
                   (kind, body, stamp, "human:" + person.id, rid))
    return {"ok": True, "id": rid, "before": {"kind": row["kind"], "body": row["body"]}}


def archive(store, db, prof, person, data, stamp):
    ids = data.get("ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
        raise crm_store.CRMError("Choose the checked to-dos to archive")
    for tid in ids:
        found = db.execute("SELECT * FROM tasks WHERE id=?", (crm_store.text(tid, "Record ID", 80, True),)).fetchone()
        if not found:
            raise crm_store.CRMError("Record not found", 404)
        store.contact(db, prof, person, found["contact_id"])
        if found["status"] != "completed":
            raise crm_store.CRMError("Only a checked to-do can be archived. Refresh and try again.", 409)
        db.execute("UPDATE tasks SET archived_at=?,revision=revision+1,updated_at=? WHERE id=? AND archived_at IS NULL",
                   (stamp, stamp, found["id"]))
    return {"ok": True, "id": ids[0], "count": len(ids)}
