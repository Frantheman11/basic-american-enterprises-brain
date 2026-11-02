"""Members file in, every gym record out. Built on `crm_io` (same file reading, same CSV guard, same
admin rule, same backup and request-id rules). Design: docs/products/gym-system-p1-design.md.

Import, three steps like contacts (read, preview, save). One row is one member:
  contact.create -> gym.member.save -> (a plan, created once by name) -> gym.membership.save -> gym.rank.award
each through `Store.execute`, each with a request id from the file's hash, the row and the step, so the
same file twice writes once. **Never overwrites**: a row that matches a contact already here (email,
phone or name, the `crm_io.Finder` rule) is skipped whole. A backup is made before the first write.

Column names follow Zen Planner and Gymdesk style member exports (First Name, Last Name, Mobile Phone,
Birthdate, Membership, Status, Start Date, Rank, Stripes, Emergency Contact). The exact headers are
UNVERIFIED until a real export is in hand; an unknown column is listed as ignored, never guessed.

Export: `files(...)` adds members (in this importer's own columns, so an export imports back), plans,
memberships, attendance, ranks and classes to the CRM's spreadsheet ZIP.
"""
import hashlib
import re
from datetime import date, datetime
from uuid import NAMESPACE_URL, uuid5

from . import crm_io, crm_store, gym, gym_read

PATH = "/api/gym/import"
COLUMNS = {
    "name": ("name", "full name", "member name", "member", "student name", "student", "contact name"),
    "first_name": ("first name", "firstname", "first", "given name"),
    "last_name": ("last name", "lastname", "last", "surname", "family name"),
    "email": ("email", "email address", "e mail", "primary email"),
    "phone": ("phone", "mobile phone", "cell phone", "mobile", "cell", "phone number", "home phone", "primary phone"),
    "birth_date": ("birth date", "birthdate", "date of birth", "dob", "birthday"),
    "program": ("program", "member program", "age group"),
    "plan": ("membership", "membership name", "plan", "plan name", "membership type", "membership plan"),
    "status": ("membership status", "status", "member status"),
    "started_on": ("start date", "membership start", "membership start date", "member since", "join date",
                   "joined", "date joined", "started"),
    "ends_on": ("end date", "membership end", "membership end date", "expiration date", "expires"),
    "rank_system": ("rank system", "rank type", "rank program", "style"),
    "belt": ("belt", "rank", "current rank", "belt rank", "rank name"),
    "stripes": ("stripes", "stripe", "degrees", "current stripes"),
    "awarded_on": ("rank date", "promotion date", "last promotion", "date promoted", "rank awarded"),
    "emergency_name": ("emergency contact", "emergency contact name", "emergency name"),
    "emergency_phone": ("emergency phone", "emergency contact phone", "emergency number"),
}
STATUS = {"active": "active", "current": "active", "trial": "trial", "trialing": "trial", "intro": "trial",
          "hold": "paused", "on hold": "paused", "frozen": "paused", "freeze": "paused", "paused": "paused",
          "suspended": "paused", "inactive": "canceled", "canceled": "canceled", "cancelled": "canceled",
          "expired": "canceled", "terminated": "canceled", "former": "canceled"}
MEMBER_COLUMNS = ["Name", "Email", "Phone", "Birth date", "Program", "Membership", "Membership status", "Start date",
                  "End date", "Rank system", "Belt", "Stripes", "Rank date", "Emergency contact", "Emergency phone",
                  "Last visit", "Visits"]


def mapping(header):
    """Header -> field, each field once; unknown columns are ignored and listed."""
    names = {}
    for field, aliases in COLUMNS.items():
        for a in aliases:
            names.setdefault(crm_io._key(a), field)
    out, used = [], set()
    for h in header:
        field = names.get(crm_io._key(h))
        out.append(field if field and field not in used else None)
        used.add(field)
    return out


def when(value, label):
    """A date from a spreadsheet: 2026-10-27, 10/27/2026 or 10/27/26 (US order)."""
    value = (value or "").strip()
    if not value:
        return ""
    value = value.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise crm_store.CRMError(f"{label} is not a date ({value})")


def _program(prof, value, born, today):
    value = crm_io._key(value)
    for p in gym.programs(prof):
        if value and value in (crm_io._key(p["id"]), crm_io._key(p.get("label", ""))):
            return p["id"]
    if born:  # the profile may say an age band belongs to a program (gym.age_programs)
        age = (today - date.fromisoformat(born)).days // 365
        for band in gym.require(prof).get("age_programs") or []:
            if isinstance(band, dict) and type(band.get("under")) is int and age < band["under"]:
                return band.get("program", "")
    return ""


def _belt(prof, text, system_text, program, stripes_text):
    """(system id, belt id, stripes) from a belt's label, with or without the word "belt" and a
    "2 stripes" suffix. The labels come from the profile's rank systems."""
    text = (text or "").strip()
    if not text:
        return None
    found = re.search(r"(\d+)\s*(stripe|degree)", text, re.I)
    stripes = int(found.group(1)) if found else 0
    if (stripes_text or "").strip():
        if not stripes_text.strip().isdigit():
            raise crm_store.CRMError(f"Stripes is not a whole number ({stripes_text})")
        stripes = int(stripes_text.strip())
    key = crm_io._key(re.sub(r"(\d+)\s*(stripes?|degrees?)|\bbelt\b|[-,()]", " ", text, flags=re.I))
    systems = gym.ranks(prof)
    wanted = crm_io._key(system_text)
    if wanted:
        systems = [s for s in systems if wanted in (crm_io._key(s["id"]), crm_io._key(s["label"]), crm_io._key(s["program"]))]
    hits = [(s, b) for s in systems for b in s["belts"] if key in (crm_io._key(b["id"]), crm_io._key(b["label"]))]
    if program:
        hits.sort(key=lambda h: h[0]["program"] != program)
    if not hits:
        raise crm_store.CRMError(f"Belt {text!r} is not in the gym's rank systems")
    system, belt = hits[0]
    if stripes > belt["stripes"]:
        raise crm_store.CRMError(f"{belt['label']} belt has at most {belt['stripes']} stripes")
    return system["id"], belt["id"], stripes


def row_plan(prof, fields, cells, today):
    """One row as the values each command needs, or raise with the reason."""
    v = {f: (crm_io.unformula((c or "").strip())) for f, c in zip(fields, cells) if f}
    name = v.get("name") or " ".join(x for x in (v.get("first_name"), v.get("last_name")) if x)
    if not name:
        raise crm_store.CRMError("Name is empty")
    born = when(v.get("birth_date"), "Birth date")
    status_text = crm_io._key(v.get("status"))
    plan = v.get("plan", "")
    if status_text and status_text not in STATUS:
        raise crm_store.CRMError(f"Status {v.get('status')!r} is not one the gym system knows")
    status = STATUS.get(status_text, "active" if plan else "")
    started = when(v.get("started_on"), "Start date") or (today.isoformat() if plan else "")
    program = _program(prof, v.get("program", ""), born, today)
    rank = _belt(prof, v.get("belt"), v.get("rank_system"), program, v.get("stripes"))
    lines = [{"kind": k, "label": "", "value": v[k]} for k in ("phone", "email") if v.get(k)]
    contact = {"name": name, "kind": "person", "lifecycle": "customer" if status in gym.LIVE else "lead",
               "source_channel": "Import", "confirm_duplicate": True}
    if lines:
        contact["channels"] = lines
    awarded = when(v.get("awarded_on"), "Rank date") or started or today.isoformat()
    return {"contact": contact, "born": born, "joined": started, "program": program,
            "emergency": (v.get("emergency_name", ""), v.get("emergency_phone", "")),
            "plan": plan, "status": status, "started": started, "ends": when(v.get("ends_on"), "End date"),
            "rank": rank, "awarded": min(awarded, today.isoformat())}


def _key(raw, row, step):
    return str(uuid5(NAMESPACE_URL, f"gym-import:{hashlib.sha256(raw).hexdigest()}:row:{row}:{step}"))


def _steps(plan, plan_ids):
    """The commands for one row, run through `do(action, data, step)` (real or rolled back)."""
    def run(do):
        cid = do("contact.create", plan["contact"], "contact")["id"]
        do("gym.member.save", {"contact_id": cid, "revision": 0, "birth_date": plan["born"], "joined_on": plan["joined"],
                               "emergency_name": plan["emergency"][0], "emergency_phone": plan["emergency"][1],
                               "program": plan["program"]}, "member")
        if plan["plan"]:
            key = " ".join(plan["plan"].casefold().split())
            if key not in plan_ids:
                plan_ids[key] = do("gym.plan.save", {"name": plan["plan"], "interval": "month",
                                                     "program": plan["program"]}, "plan:" + key)["id"]
            do("gym.membership.save", {"contact_id": cid, "plan_id": plan_ids[key], "status": plan["status"],
                                       "started_on": plan["started"], "ends_on": plan["ends"]}, "membership")
        if plan["rank"]:
            system, belt, stripes = plan["rank"]
            do("gym.rank.award", {"contact_id": cid, "system_id": system, "belt": belt, "stripes": stripes,
                                  "awarded_on": plan["awarded"], "note": "Imported"}, "rank")
        return cid
    return run


def _existing_plans(db):
    return {r["name_key"]: r["id"] for r in db.execute("SELECT id, name_key FROM plans")}


def run(store, prof, person, body):
    """One import step. body: {step: read|preview|save, file: base64}."""
    gym.require(prof)
    crm_io.allowed(store, prof, person)
    for capability in ("crm_create", "gym_manage", "gym_promote"):
        store.authorize(prof, person, capability)
    gym.ensure(store, prof)
    if not isinstance(body, dict) or body.get("step") not in ("read", "preview", "save"):
        raise crm_store.CRMError("Choose read, preview or save")
    raw = crm_io.decode(body.get("file"))
    header, rows, _ = crm_io.parse(raw)
    fields = mapping(header)
    used = [{"column": h, "field": f} for h, f in zip(header, fields) if f]
    ignored = [h for h, f in zip(header, fields) if not f]
    if not any(f in ("name", "first_name", "last_name") for f in fields):
        raise crm_store.CRMError("The file needs a Name column, or First Name and Last Name")
    if body["step"] == "read":
        return {"rows": len(rows), "columns": used, "ignored": ignored}
    today = gym.today(prof)
    planned, problems, duplicates, done = [], [], [], 0
    with store.connection() as db:
        finder = crm_io.Finder(db, prof)
        saved = {r[0] for r in db.execute("SELECT id FROM commands")}
        for n, cells in rows:
            if _key(raw, n, "contact") in saved:
                done += 1
                continue
            try:
                plan = row_plan(prof, fields, cells, today)
            except crm_store.CRMError as e:
                problems.append({"row": n, "reason": str(e)})
                continue
            dup = finder.match(plan["contact"])
            finder.add(plan["contact"], n)
            if dup:
                duplicates.append({"row": n, "name": plan["contact"]["name"], "reason": dup})
                continue
            planned.append((n, plan))
    if body["step"] == "preview":
        return _preview(store, prof, person, rows, planned, problems, duplicates, done, used, ignored)
    return _save(store, prof, person, raw, rows, planned, problems, duplicates, done)


def _preview(store, prof, person, rows, planned, problems, duplicates, done, used, ignored):
    """Every row through the real command code, then rolled back: what passes here, saves."""
    ok, new_plans = [], set()
    with store.connection() as db:
        db.execute("BEGIN")
        try:
            plan_ids = _existing_plans(db)
            before = set(plan_ids)
            for n, plan in planned:
                db.execute("SAVEPOINT row")
                try:
                    def do(action, data, step):
                        if action.startswith("gym."):
                            return gym.change(store, db, prof, person, action, data)
                        return store._change(db, prof, person, action, data)
                    _steps(plan, plan_ids)(do)
                    db.execute("RELEASE row")
                    ok.append({"row": n, "name": plan["contact"]["name"], "plan": plan["plan"],
                               "status": plan["status"], "belt": plan["rank"][1] if plan["rank"] else ""})
                except crm_store.CRMError as e:
                    db.execute("ROLLBACK TO row")
                    db.execute("RELEASE row")
                    problems.append({"row": n, "reason": str(e)})
            new_plans = sorted(set(plan_ids) - before)
        finally:
            db.rollback()
    return {"total": len(rows), "new": len(ok), "new_rows": ok[:50], "duplicates": duplicates,
            "problems": sorted(problems, key=lambda p: p["row"]), "already": done, "new_plans": new_plans,
            "columns": used, "ignored": ignored}


def _save(store, prof, person, raw, rows, planned, problems, duplicates, done):
    created, failed, backup = [], list(problems), None
    with store.connection() as db:
        plan_ids = _existing_plans(db)
    for n, plan in planned:
        if backup is None:
            backup = crm_io.backup_path(store)
            store.backup(backup)
        try:
            do = lambda action, data, step: store.execute(prof, person, {"action": action, "data": data,
                                                                        "request_id": _key(raw, n, step)})
            created.append({"row": n, "id": _steps(plan, plan_ids)(do)})
        except crm_store.CRMError as e:
            failed.append({"row": n, "reason": str(e)})
    crm_io._audit(store, person, "import.gym_members", hashlib.sha256(raw).hexdigest()[:16],
                  {"rows": len(rows), "created": len(created), "duplicates": len(duplicates), "failed": len(failed)})
    return {"total": len(rows), "created": len(created), "skipped": duplicates, "failed": failed,
            "already": done, "backup": backup.name if backup else None}


# ------------------------------------------------------------------ export

def files(prof, db, names, zone):
    """The gym's CSV files for the export ZIP, read inside the export's one transaction."""
    gym.migrate(db)
    zone = gym.zone(prof)  # class times and dates are the gym's own
    who = lambda actor: names.get(actor.removeprefix("human:"), actor)
    programs = {p["id"]: p.get("label", p["id"]) for p in gym.programs(prof)}
    systems = {s["id"]: s for s in gym.ranks(prof)}
    contacts = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM contacts WHERE deleted_at IS NULL")}
    rows = gym_read._members(db, prof, None)
    members = []
    for r in rows.values():
        m, a = r["membership"] or {}, r["rank"] or {}
        system = systems.get(a.get("system_id"), {})
        belt = next((b["label"] for b in system.get("belts", []) if b["id"] == a.get("belt")), a.get("belt", ""))
        if not (m or a or r["joined_on"] or r["birth_date"]):
            continue  # a person who is not a gym member (a parent, a lead) stays in contacts.csv only
        members.append([r["name"], r["email"], r["phone"], r["birth_date"] or "", programs.get(r["program"] or "", r["program"] or ""),
                        m.get("plan", ""), m.get("status", ""), m.get("started_on", ""), m.get("ends_on", ""),
                        system.get("label", ""), belt, a.get("stripes", "") if a else "", a.get("awarded_on", ""),
                        r["emergency_name"] or "", r["emergency_phone"] or "", r["last_visit"], r["visits"]])
    plans = [[p["name"], p["interval"], programs.get(p["program"], p["program"]), "yes" if p["active"] else "no"]
             for p in db.execute("SELECT * FROM plans ORDER BY name")]
    memberships = [[contacts.get(m["contact_id"], ""), m["plan"], m["status"], m["started_on"], m["ends_on"]]
                   for m in db.execute("SELECT m.*, p.name AS plan FROM memberships m JOIN plans p ON p.id=m.plan_id "
                                       "ORDER BY m.started_on")]
    attendance = []
    for k in db.execute("SELECT k.*, s.name AS class, s.starts_at FROM checkins k LEFT JOIN sessions s ON s.id=k.session_id "
                        "ORDER BY k.local_date, k.at"):
        start = crm_io._local(k["starts_at"], zone, "%H:%M") if k["starts_at"] else ""
        attendance.append([k["local_date"], k["class"] or "Open mat", start, contacts.get(k["contact_id"], ""),
                           k["method"], who(k["actor"])])
    ranks = []
    for a in db.execute("SELECT * FROM rank_awards ORDER BY awarded_on, created_at"):
        system = systems.get(a["system_id"], {})
        belt = next((b["label"] for b in system.get("belts", []) if b["id"] == a["belt"]), a["belt"])
        ranks.append([contacts.get(a["contact_id"], ""), system.get("label", a["system_id"]), belt, a["stripes"],
                      a["awarded_on"], who(a["awarded_by"]), a["note"]])
    classes = [[t["name"], gym.DAYS[t["weekday"]], t["start"], t["minutes"], t["instructor"], t["room"], t["cap"] or "",
                programs.get(t["program"], t["program"]), "yes" if t["active"] else "no"]
               for t in db.execute("SELECT * FROM class_templates ORDER BY weekday, start")]
    return {
        "members": (MEMBER_COLUMNS, sorted(members, key=lambda x: x[0].casefold())),
        "plans": (["Plan", "Interval", "Program", "Active"], plans),
        "memberships": (["Member", "Plan", "Status", "Start date", "End date"], memberships),
        "attendance": ([f"Date ({getattr(zone, 'key', 'UTC')})", "Class", "Start", "Member", "Method", "Recorded by"], attendance),
        "ranks": (["Member", "Rank system", "Belt", "Stripes", "Awarded on", "Awarded by", "Note"], ranks),
        "classes": (["Class", "Weekday", "Start", "Minutes", "Instructor", "Room", "Cap", "Program", "Active"], classes),
    }
