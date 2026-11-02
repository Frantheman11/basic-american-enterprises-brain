"""Prevailing Wage Desk: what it keeps. Imports, cleared flags, officer reviews and recorded filings,
in `pw_*` tables inside the CRM's own SQLite file, with an audit line for every write.

Roles (profile `roles.<role>.can`): `pw_read` sees the desk, `pw_review` imports files and clears flags
with a note, `pw_officer` marks a packet reviewed and records a filing. A packet review also needs the
person's id in `payroll_desk.officers`: the profile names who the client's officers are.

The officer gate: a packet can be marked reviewed only when every flag in it is cleared (fixed by a
corrected import, or cleared with a note). The review holds a digest of the packet's lines; a later
import that changes them puts the packet back to "changed" and the staging file closes again.
Nothing here signs, certifies or submits: recording a filing records what the client's officer did.
"""
import json
from datetime import timedelta

from . import crm_store, payroll_desk as pd

KINDS = ("payroll", "projects", "determinations", "subcontractors")
MAX_BYTES = 2_000_000


def migrate(db):
    db.execute("CREATE TABLE IF NOT EXISTS pw_inputs (kind TEXT PRIMARY KEY, body TEXT NOT NULL, source_name TEXT "
               "NOT NULL DEFAULT '', imported_by TEXT NOT NULL, imported_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pw_lines (key TEXT PRIMARY KEY, week_ending TEXT NOT NULL, project_id TEXT "
               "NOT NULL, body TEXT NOT NULL, imported_by TEXT NOT NULL, imported_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pw_clears (key TEXT PRIMARY KEY, note TEXT NOT NULL, by_id TEXT NOT NULL, "
               "by_name TEXT NOT NULL, at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pw_reviews (project_id TEXT NOT NULL, weeks TEXT NOT NULL, digest TEXT NOT NULL, "
               "officer_id TEXT NOT NULL, officer_name TEXT NOT NULL, officer_title TEXT NOT NULL, statement TEXT NOT NULL, "
               "at TEXT NOT NULL, PRIMARY KEY (project_id, weeks))")
    db.execute("CREATE TABLE IF NOT EXISTS pw_filings (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, "
               "filed_on TEXT NOT NULL, weeks TEXT NOT NULL, recorded_by TEXT NOT NULL, at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pw_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
               "actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)")


STATEMENT = ("I reviewed this packet and every flag in it. I will certify and submit the payroll records myself "
             "on the DIR portal. The desk did not sign or submit anything.")


def _audit(db, person, action, detail):
    db.execute("INSERT INTO pw_audit (at, actor, action, detail) VALUES (?,?,?,?)",
               (crm_store.now(), "human:" + person.id, action, json.dumps(detail, sort_keys=True)))


def _open(store, prof, person, capability):
    pd_cfg = pd.config(prof)
    if pd_cfg is None:
        raise crm_store.CRMError("The Prevailing Wage Desk is not enabled for this business", 404)
    store.authorize(prof, person, capability)
    return pd_cfg


def import_file(store, prof, person, kind, text, source_name=""):
    """One file in: the payroll CSV (lines replace the same week, project, worker and class) or a JSON list."""
    _open(store, prof, person, "pw_review")
    if kind not in KINDS:
        raise crm_store.CRMError("Choose payroll, projects, determinations or subcontractors")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_BYTES:
        raise crm_store.CRMError("The file is empty or larger than 2 MB")
    try:
        parsed = pd.parse_payroll(text) if kind == "payroll" else pd.parse_json(text, kind)
    except pd.InputError as e:
        raise crm_store.CRMError(str(e)) from None
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        if kind == "payroll":
            for line in parsed:
                db.execute("INSERT OR REPLACE INTO pw_lines VALUES (?,?,?,?,?,?)", (pd.line_key(line), line["week_ending"],
                           line["project_id"], json.dumps(line, sort_keys=True), person.id, stamp))
            count = len(parsed)
        else:
            db.execute("INSERT OR REPLACE INTO pw_inputs VALUES (?,?,?,?,?)",
                       (kind, json.dumps(parsed[kind]), str(source_name)[:120], person.id, stamp))
            count = len(parsed[kind])
        _audit(db, person, "import", {"kind": kind, "rows": count, "file": str(source_name)[:120]})
    return {"ok": True, "kind": kind, "rows": count}


def _inputs(db):
    out = {k: [] for k in KINDS[1:]}
    for r in db.execute("SELECT kind, body FROM pw_inputs"):
        out[r["kind"]] = json.loads(r["body"])
    out["lines"] = [json.loads(r["body"]) for r in db.execute("SELECT body FROM pw_lines")]
    return out


def _reviews(db):
    return {(r["project_id"], r["weeks"]): dict(r) for r in db.execute("SELECT * FROM pw_reviews")}


def _filings(db):
    return [{"project_id": r["project_id"], "filed_on": r["filed_on"], "weeks": json.loads(r["weeks"]),
             "recorded_by": r["recorded_by"]} for r in db.execute("SELECT * FROM pw_filings ORDER BY id")]


def _review(db, prof):
    clears = {r["key"]: {"note": r["note"], "by": r["by_name"], "at": r["at"]} for r in db.execute("SELECT * FROM pw_clears")}
    return pd.review(_inputs(db), pd.as_of(prof, crm_store_today(prof)), clears, _reviews(db), _filings(db))


def crm_store_today(prof):
    """Today's date from the engine's clock (crm_store.now), which a sample or a test can move."""
    from datetime import datetime
    return datetime.fromisoformat(crm_store.now()).date()


def state(store, prof, person):
    """The whole desk for the screen, plus what this person may do."""
    _open(store, prof, person, "pw_read")
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        view = _review(db, prof)
        view["audit"] = [dict(r) for r in db.execute("SELECT at, actor, action, detail FROM pw_audit ORDER BY id DESC LIMIT 30")]
    from . import access
    view["can"] = {"review": access.can_do(prof, person, "pw_review"),
                   "officer": access.can_do(prof, person, "pw_officer") and person.id in pd.officers(prof)}
    view["person"] = {"id": person.id, "name": person.name}
    return view


def clear(store, prof, person, key, note):
    """Clear one flag with a note saying why (checked and explained, or fixed outside the file)."""
    _open(store, prof, person, "pw_review")
    note = (note or "").strip() if isinstance(note, str) else ""
    if len(note) < 5:
        raise crm_store.CRMError("Say why in a note (at least a few words)")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        view = _review(db, prof)
        flag = next((f for f in view["flags"] if f["key"] == key), None)
        if flag is None:
            raise crm_store.CRMError("Flag not found", 404)
        db.execute("INSERT OR REPLACE INTO pw_clears VALUES (?,?,?,?,?)", (key, note[:500], person.id, person.name, crm_store.now()))
        _audit(db, person, "clear", {"key": key, "code": flag["code"], "project_id": flag["project_id"], "note": note[:500]})
    return {"ok": True, "key": key}


def officer_review(store, prof, person, project_id, title=""):
    """The gate: a named officer marks the project's open packet reviewed. Refused while any flag is open."""
    _open(store, prof, person, "pw_officer")
    if person.id not in pd.officers(prof):
        raise crm_store.CRMError("Only an officer named in the desk settings can mark a packet reviewed", 403)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        packet = next((p for p in _review(db, prof)["packets"] if p["project_id"] == project_id), None)
        if packet is None or not packet["weeks"]:
            raise crm_store.CRMError("No open weeks to review for this project", 404)
        if packet["open"]:
            raise crm_store.CRMError(f"{packet['open']} flag(s) are still open. Fix or clear each one with a note first.", 409)
        stamp = crm_store.now()
        db.execute("INSERT OR REPLACE INTO pw_reviews VALUES (?,?,?,?,?,?,?,?)",
                   (project_id, ",".join(packet["weeks"]), packet["digest"], person.id, person.name,
                    str(title or "Officer")[:80], STATEMENT, stamp))
        _audit(db, person, "officer_review", {"project_id": project_id, "weeks": packet["weeks"], "digest": packet["digest"]})
    return {"ok": True, "project_id": project_id, "weeks": packet["weeks"], "statement": STATEMENT}


def record_filing(store, prof, person, project_id, filed_on):
    """Record that the client's officer filed the reviewed weeks on the DIR portal (the desk did not)."""
    _open(store, prof, person, "pw_officer")
    try:
        filed = pd.day(filed_on, "filed_on")
    except pd.InputError as e:
        raise crm_store.CRMError(str(e)) from None
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        view = _review(db, prof)
        packet = next((p for p in view["packets"] if p["project_id"] == project_id), None)
        if packet is None or packet["state"] != "reviewed":
            raise crm_store.CRMError("Only a packet the officer marked reviewed can be recorded as filed", 409)
        if filed > pd.day(view["as_of"]) + timedelta(days=1) or filed.isoformat() < packet["weeks"][-1]:
            raise crm_store.CRMError("The filing date must be after the last week and not in the future")
        db.execute("INSERT INTO pw_filings (project_id, filed_on, weeks, recorded_by, at) VALUES (?,?,?,?,?)",
                   (project_id, filed.isoformat(), json.dumps(packet["weeks"]), person.id, crm_store.now()))
        _audit(db, person, "record_filing", {"project_id": project_id, "filed_on": filed.isoformat(), "weeks": packet["weeks"]})
    return {"ok": True}


def staging(store, prof, person, project_id):
    """(csv text, file name) for a reviewed packet; refused before the officer's review."""
    _open(store, prof, person, "pw_read")
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        view = _review(db, prof)
    packet = next((p for p in view["packets"] if p["project_id"] == project_id), None)
    project = next((p for p in view["projects"] if p["id"] == project_id), None)
    if packet is None or project is None:
        raise crm_store.CRMError("Project not found", 404)
    try:
        body = pd.staging_csv(packet, project)
    except pd.InputError as e:
        raise crm_store.CRMError(str(e), 409) from None
    return body, f"ecpr-staging-{project_id}-{packet['weeks'][-1]}-DRAFT.csv"


def packet(store, prof, person, project_id):
    view = state(store, prof, person)
    p = next((p for p in view["packets"] if p["project_id"] == project_id), None)
    if p is None:
        raise crm_store.CRMError("Project not found", 404)
    return view, p
