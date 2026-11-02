"""The morning brief: what each person has today, in a few plain lines (sales rep review
2026-09-27: "3 callbacks today, 2 leads return to the pool this week").

Pure reading: the same facts as My day on the Today screen, so the brief never says something
the CRM does not show. `collect` gathers them; `brief` turns them into a subject and plain
lines; `tools/morning_brief.py` prints them or emails them (with a visual HTML version).
Since 2026-10-02 it reads through the slot (`slot.py`), never the store, so the same brief
runs on our CRM or on a CRM the business already uses (a HubSpot account, for one).
"""
from datetime import datetime, timedelta

from . import slot as slots


def collect(store, prof, person, at):
    """Today's facts for one person at time `at` (an aware datetime), or None when empty."""
    return collect_from(slots.CRMSlot(store, prof), person, at)


def collect_from(slot, person, at):
    """The same facts from any slot. A slot that cannot be read says so in "waiting"."""
    try:
        snap = slot.read(person)
    except slots.SlotError as e:
        snap = {"contacts": [], "tasks": [], "research_links": {},
                "waiting": [{"kind": "slot", "title": f"Could not read {slot.name}: {e.reason}", "at": ""}]}
    by_id = {c["id"]: c for c in snap["contacts"]}
    mine = {cid: c for cid, c in by_id.items() if c["owner"] == person.id and c["kind"] != "business"}
    client = lambda c: by_id.get(c.get("company_id")) or c
    end = at.replace(hour=23, minute=59, second=59)
    soon = at + timedelta(days=3)
    tasks = [t for t in snap["tasks"] if t["status"] == "open" and t["contact_id"] in mine]

    def row(t):
        c = mine[t["contact_id"]]
        return {"title": t["title"], "company": client(c)["name"], "phone": c.get("phone") or client(c).get("phone") or "",
                "due": datetime.fromisoformat(t["due_at"]).astimezone(at.tzinfo)}
    late = [row(t) for t in sorted(tasks, key=lambda t: t["due_at"]) if datetime.fromisoformat(t["due_at"]) < at]
    today = [row(t) for t in sorted(tasks, key=lambda t: t["due_at"]) if at <= datetime.fromisoformat(t["due_at"]) <= end]
    with_task = {t["contact_id"] for t in tasks} | {mine[t["contact_id"]].get("company_id") for t in tasks}
    expiring = sorted(({"company": mine[cid]["name"], "phone": mine[cid].get("phone") or "",
                        "until": datetime.fromisoformat(l["expires_at"]).astimezone(at.tzinfo)}
                       for cid, l in (snap.get("research_links") or {}).items()
                       if cid in mine and l["live"] and l["status"] == "open" and l["expires_at"]
                       and datetime.fromisoformat(l["expires_at"]) <= soon and cid not in with_task),
                      key=lambda x: x["until"])
    waiting = snap.get("waiting") or []
    if not (today or late or expiring or waiting):
        return None
    return {"name": person.name, "at": at, "today": today, "late": late, "expiring": expiring, "waiting": waiting}


def subject(d):
    n = len(d["today"])
    parts = [f"{n} callback{'s' if n != 1 else ''}"] + ([f"{len(d['late'])} late"] if d["late"] else []) + \
            ([f"{len(d['expiring'])} leads expiring"] if d["expiring"] else []) + \
            ([f"{len(d['waiting'])} waiting for you"] if d["waiting"] else [])
    return f"Your day: {', '.join(parts)}"


def lines(d):
    out = []
    if d["today"]:
        out.append(f"{len(d['today'])} callback{'s' if len(d['today']) != 1 else ''} today:")
        out += [f"  {t['due']:%I:%M %p}  {t['title']} ({t['company']})" for t in d["today"]]
    if d["late"]:
        out.append(f"{len(d['late'])} late: " + "; ".join(t["title"] for t in d["late"][:5])
                   + (" and more" if len(d["late"]) > 5 else ""))
    if d["expiring"]:
        out.append(f"{len(d['expiring'])} of your leads go back to the pool in the next 3 days with no "
                   "callback set: " + ", ".join(x["company"] for x in d["expiring"][:5]))
    if d["waiting"]:
        out.append(f"{len(d['waiting'])} item{'s' if len(d['waiting']) != 1 else ''} waiting for you: "
                   + "; ".join(w["title"] for w in d["waiting"][:5]))
    return out


def brief(store, prof, person, at):
    """(subject, lines) for one person at time `at`, or None when there is nothing to say."""
    d = collect(store, prof, person, at)
    return (subject(d), lines(d)) if d else None
