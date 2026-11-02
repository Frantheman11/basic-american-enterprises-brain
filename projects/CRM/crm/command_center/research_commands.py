"""Every research.* command, routed to the module that owns it, inside the store's transaction.

    research.claim / release / outcome / reassign   research_claims.py (who works a lead)
    research.document.* / research.print.*          research_package.py (the prep package)
    research.request / research.request.decide      research_requests.py (ask for research)

Kept out of crm_store.py so the store stays one readable file (tests.test_command_center
Foundation holds it under 40 KB).
"""
from . import crm_store

FIELDS = {
    "research.claim": {"place_id"},
    "research.release": {"place_id", "revision"},
    "research.outcome": {"place_id", "revision", "outcome", "channel", "offer", "notes", "next_at", "next_title"},
    "research.reassign": {"place_id", "revision", "owner"},
    "research.release.undo": {"place_id", "revision"},
    "research.outcome.edit": {"id", "revision", "notes"},
    "research.request": {"kind", "industry", "ring", "body"},
    "research.request.decide": {"id", "revision", "decision", "note"},
}


def migrate(db):
    """Version 7: prep package documents and printouts. Version 8: a taken lead's pipeline
    card and its rest, and research requests. Each only adds; every row is kept."""
    from . import research_claims, research_edits, research_package, research_requests
    research_edits.migrate(db)  # version 10: outcome notes can be corrected
    research_package.migrate(db)
    research_claims.migrate(db)
    research_requests.migrate(db)


def change(store, db, prof, person, action, data, stamp):
    if action.startswith(("research.document.", "research.print.")):
        from . import research_package
        return {"ok": True, "id": research_package.change(store, db, prof, person, action, data, stamp)}
    if set(data) - FIELDS[action]:
        raise crm_store.CRMError("Unexpected fields in command")
    if action == "research.outcome.edit":
        from . import research_edits
        return research_edits.change(store, db, prof, person, data, stamp)
    if action.startswith("research.request"):
        from . import research_requests
        return {"ok": True, "id": research_requests.change(store, db, prof, person, action, data, stamp)}
    from . import research_claims
    rid, created_company = research_claims.change(store, db, prof, person, action, data, stamp)
    result = {"ok": True, "id": rid}
    if created_company:
        result["created_company"] = created_company
    return result
