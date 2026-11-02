"""Everything waiting for the owner, in one place (sales rep review 2026-09-27: printouts,
documents and research requests each waited somewhere different, with no notice).

Read-only. Each item says what it is, who asked and when, and which screen settles it:

    print      a rep asked to print a flyer; the printout approvers decide (research card)
    request    a research search past the monthly budget, or a question (Research requests)
    quote      a rep asked for a quote on a deal (the deal's record)
    signed     a client signed; onboarding starts (the deal's record)
    alert      a website lead's alert failed to send; the lead is saved (lead_intake.py)
    lead       the lead screen held a website lead; release it to the client or set it aside
    event      an event could not reach the business's hook; fix it, then retry (event_out.py)

Only a person who can settle an item sees it, so a rep's Today never shows the owner's queue.
"""
from . import access, research_package, research_requests


def read(db, prof, person):
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
    who = lambda actor: names.get((actor or "").removeprefix("human:"), actor or "")
    items = []
    if research_package.approver(prof, person):
        for r in db.execute("SELECT p.place_id, p.requested_by, p.requested_at, c.name FROM research_prints p "
                            "LEFT JOIN research_claims c ON c.place_id=p.place_id WHERE p.status='requested'"):
            items.append({"kind": "print", "place_id": r["place_id"], "title": f"Printout for {r['name'] or 'a lead'}",
                          "by": who(r["requested_by"]), "at": r["requested_at"]})
    if research_requests.approver(prof, person):
        for r in db.execute("SELECT id, kind, industry, ring, body, actor, created_at, status FROM research_requests "
                            "WHERE status='needs_approval' OR (kind='question' AND status='queued')"):
            title = (r["body"] if r["kind"] == "question" else
                     f"Search {r['industry'].replace('_', ' ')}, ring {r['ring']}, past this month's budget")
            items.append({"kind": "request", "id": r["id"], "title": title, "question": r["kind"] == "question",
                          "by": "the research worker" if r["actor"].startswith("agent:") else who(r["actor"]),
                          "at": r["created_at"]})
    if access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign"):
        for r in db.execute("SELECT d.id, d.title, d.handoff, d.handoff_by, d.handoff_at, d.contact_id, c.name "
                            "FROM deals d JOIN contacts c ON c.id=d.contact_id WHERE d.handoff IN ('quote','signed') "
                            "AND d.deleted_at IS NULL AND c.deleted_at IS NULL"):
            items.append({"kind": r["handoff"], "id": r["id"], "contact_id": r["contact_id"],
                          "title": (f"Quote for {r['name']}: {r['title']}" if r["handoff"] == "quote"
                                    else f"{r['name']} signed: start onboarding ({r['title']})"),
                          "by": who(r["handoff_by"]), "at": r["handoff_at"]})
        from . import lead_intake  # a website lead whose alert failed, or one the lead screen held
        items += lead_intake.waiting(db, prof, person)
        from . import event_out  # an outbox event the hook refused for good
        items += event_out.waiting(db)
    return sorted(items, key=lambda i: i["at"] or "")
