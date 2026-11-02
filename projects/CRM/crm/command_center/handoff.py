"""What a deal is, and the hand-off between the rep and the owner (sales rep review 2026-09-27).

A deal says which offer it is and splits the money the way CTA sells it: a one-time setup
(`amount_cents`, as before) and a monthly fee (`monthly_cents`). Offers come from the profile
(`crm.offers`); the engine names none.

The hand-off is one field on the deal, moved by one command, `deal.handoff`:

    quote     the rep asks the owner for a quote              (the deal's owner or an admin)
    signed    the client signed: start onboarding             (the deal's owner or an admin)
    done      the owner has sent the quote / started onboarding (an admin only)
    reopen    undo a "done": back in the owner's queue as it was   (an admin only; batch 2)

The owner's queue (Today, "Waiting for you") lists every deal at `quote` or `signed`.
Nothing is sent to anyone: the hand-off is a flag and an audit line.
"""
import json

from . import access, crm_store

STEPS = ("quote", "signed", "done", "reopen")


def migrate(db):
    columns = {r["name"] for r in db.execute("PRAGMA table_info(deals)")}
    for column, kind in (("offer", "TEXT NOT NULL DEFAULT ''"), ("monthly_cents", "INTEGER NOT NULL DEFAULT 0"),
                         ("handoff", "TEXT NOT NULL DEFAULT ''"), ("handoff_at", "TEXT"), ("handoff_by", "TEXT")):
        if column not in columns:
            db.execute(f"ALTER TABLE deals ADD COLUMN {column} {kind}")


def offers(prof):
    rows = (prof.get("crm") or {}).get("offers") or []
    return [{"id": o["id"], "label": o.get("label", o["id"])} for o in rows
            if isinstance(o, dict) and isinstance(o.get("id"), str)]


def fields(prof, data, row):
    """(offer, monthly_cents) for a deal write, checked. Missing keys keep the stored values."""
    offer = data.get("offer", row.get("offer", ""))
    if offer and offer not in {o["id"] for o in offers(prof)}:
        raise crm_store.CRMError("Choose a configured offer")
    monthly = data.get("monthly_cents", row.get("monthly_cents", 0))
    if type(monthly) is not int or not 0 <= monthly <= 10**10:
        raise crm_store.CRMError("Monthly must be a non-negative whole number of cents")
    return offer, monthly


def change(store, db, prof, person, data, stamp):
    CRMError = crm_store.CRMError
    if set(data) - {"id", "revision", "step"}:
        raise CRMError("Unexpected fields in command")
    rid = crm_store.text(data.get("id"), "Deal", 80, True)
    row = db.execute("SELECT * FROM deals WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise CRMError("Record not found", 404)
    store.contact(db, prof, person, row["contact_id"])  # may this person see it at all
    store._revision(row, data)
    step = data.get("step")
    if step not in STEPS:
        raise CRMError("Choose quote, signed, done or reopen")
    admin = access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")
    if step in ("done", "reopen") and not admin:
        raise CRMError("Only the owner of the business closes a hand-off", 403)
    if step == "done" and row["handoff"] not in ("quote", "signed"):
        raise CRMError("Nothing is waiting on this deal", 409)
    if step == "reopen":
        # Undo a "done": the step it closed is the last quote/signed step in the audit. "done"
        # keeps who asked and when (who closed it is in the audit), so the queue shows it as before.
        if row["handoff"] != "done":
            raise CRMError("Nothing to undo on this deal", 409)
        step = next((json.loads(a["payload"])["data"]["step"] for a in db.execute(
            "SELECT payload FROM audit WHERE action='deal.handoff' AND entity_id=? ORDER BY created_at DESC", (rid,))
            if json.loads(a["payload"])["data"].get("step") in ("quote", "signed")), None)
        if step is None:
            raise CRMError("Nothing to undo on this deal", 409)
    if data.get("step") in ("done", "reopen"):
        db.execute("UPDATE deals SET handoff=?,revision=revision+1,updated_at=? WHERE id=?", (step, stamp, rid))
    else:
        db.execute("UPDATE deals SET handoff=?,handoff_at=?,handoff_by=?,revision=revision+1,updated_at=? WHERE id=?",
                   (step, stamp, person.id, stamp, rid))
    return rid
