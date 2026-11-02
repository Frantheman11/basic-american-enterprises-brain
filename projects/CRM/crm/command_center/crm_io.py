"""Spreadsheet export and contact import for the working CRM (Sam, 2026-10-23; first built 2026-10-02
on an unmerged branch, rebuilt here on schema 26).

Who: an administrator only, meaning a person who sees all records AND holds `crm_assign`
(`Store.is_admin`). A rep gets neither button and the routes answer 403, because an export takes
the whole customer list out of the CRM. `allowed()` is the one place that rule lives: change it
there if Sam decides reps may export their own records.

Export (`export`): one ZIP with three CSV files a normal owner opens in Excel: contacts, deals
and open to-dos. UTF-8 with a BOM, so Excel shows every character. A cell that starts like a
formula gets a leading apostrophe (CSV injection); the import takes it off again. Deleted records
are never exported. Each export writes one audit line: who, when and how many rows of each.

Import (`run`), contacts only, in three steps:
  read     the columns and a guessed mapping; nothing is written
  preview  new contacts vs likely duplicates vs rows with a problem; nothing is written
  save     every row is an ordinary `contact.create` through `Store.execute`, with its audit
           line, outbox event and request ID. Then one audit line for the whole file.

- Nothing is overwritten. An import only creates records; it never changes an existing one.
- A likely duplicate is a row with the same email, phone or name as a contact already here (or as
  an earlier row of the same file). The person picks one choice for all of them: skip (default)
  or create anyway. There is no merge.
- The request ID of a row is a hash of the file and the row number, so the same file uploaded
  twice writes nothing the second time.
- A backup (`Store.backup`) is made before the first write of each import.
- Too big (bytes or rows) is refused whole; there is no partial import nobody was told about.
- The preview runs each row through the real command code inside a transaction that is rolled
  back, so a row the preview passes is a row the save accepts.
"""
import base64
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import crm_store, duplicates

EXPORT_PATH, IMPORT_PATH = "/api/crm/export", "/api/crm/import"
PATHS = (EXPORT_PATH, IMPORT_PATH)
MAX_BYTES = 5_000_000
MAX_ROWS = 5_000
BODY_LIMIT = MAX_BYTES * 4 // 3 + 64_000  # the file as base64 plus the mapping, for server.py
FORMULA = ("=", "+", "-", "@", "\t", "\r")

# Contact columns, in export order. Each has the aliases an import header is matched by.
FIXED = [
    ("name", "Name", ("full name", "contact name", "contact", "person", "first and last name")),
    ("kind", "Type", ("contact type", "record type")),
    ("email", "Email", ("email address", "e-mail", "e mail", "mail")),
    ("phone", "Phone", ("phone number", "telephone", "tel", "mobile", "cell", "main phone")),
    ("more", "Other phones and emails", ()),
    ("role", "Role", ("title", "job title", "position")),
    ("address", "Address", ("street address", "mailing address", "location")),
    ("website", "Website", ("web site", "url", "site", "web")),
    ("lifecycle", "Relationship", ("lifecycle", "status", "stage of relationship")),
    ("source_channel", "Source channel", ("source", "channel", "lead source")),
    ("source_campaign", "Source campaign", ("campaign",)),
    ("source_ad", "Source ad", ("ad", "ad name", "ad or detail")),
    ("company", "Company", ("company name", "business", "business name", "organization", "organisation", "account")),
]
EXPORT_ONLY = ["Owner", "Created", "Last activity"]
DEAL_COLUMNS = ["Title", "Contact", "Stage", "Offer", "Setup amount", "Monthly", "Currency", "Owner",
                "Won date", "Lost reason", "Created"]
TODO_COLUMNS = ["To-do", "Record", "Main task", "Due", "Category", "Department", "Assigned to",
                "Follow-up", "Waiting on", "Blocked because"]


def allowed(store, prof, person):
    """Who may export and import: an administrator. The decision Sam can change, in one place."""
    store.authorize(prof, person)
    if not store.is_admin(prof, person):
        raise crm_store.CRMError("Only an administrator can export or import CRM data", 403)


def limits():
    return {"max_rows": MAX_ROWS, "max_mb": MAX_BYTES // 1_000_000}


def _key(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def targets():
    """Where a file column can go: a contact field, or skip."""
    return [{"id": fid, "label": label} for fid, label, _ in FIXED]


def guess(headers):
    """A first mapping from the headers. The person confirms it. Each target is used once."""
    names = {}
    for fid, label, aliases in FIXED:
        for word in (label, fid, *aliases):
            names.setdefault(_key(word), fid)
    out, used = [], set()
    for h in headers:
        target = names.get(_key(h), "skip")
        if target in used:
            target = "skip"
        used.add(target)
        out.append(target)
    return out


# ------------------------------------------------------------------ reading the file

def decode(file_b64):
    try:
        raw = base64.b64decode(file_b64 or "", validate=True)
    except (ValueError, TypeError):
        raise crm_store.CRMError("The file could not be read. Choose the .csv file again.") from None
    if not raw:
        raise crm_store.CRMError("The file is empty")
    if len(raw) > MAX_BYTES:
        raise crm_store.CRMError(f"The file is {len(raw) / 1e6:.1f} MB. The limit is {MAX_BYTES // 1_000_000} MB: "
                                 "split it into smaller files. Nothing was imported.", 413)
    return raw


def parse(raw):
    """Header and rows. UTF-8 (with or without a BOM), else Windows-1252 (an Excel 'CSV' save).
    Comma or semicolon, whichever the header line uses more."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as e:
        raise crm_store.CRMError(f"The file could not be read as CSV ({e}). Nothing was imported.") from None
    if not rows or not any(c.strip() for c in rows[0]):
        raise crm_store.CRMError("The first row must name the columns")
    # (row number as a spreadsheet counts it, cells). Row 1 is the header; blank rows are dropped
    # but keep their numbers, so "row 7" in a result is row 7 in the person's own sheet.
    header = [h.strip() for h in rows[0]]
    body = [(n, r) for n, r in enumerate(rows[1:], start=2) if any(c.strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise crm_store.CRMError(f"The file has {len(body):,} rows. The limit is {MAX_ROWS:,}: split it into "
                                 "smaller files. Nothing was imported.", 413)
    if not body:
        raise crm_store.CRMError("The file has a header but no rows")
    return header, body, delimiter


def unformula(value):
    """Undo the export's guard: a leading apostrophe before a formula character."""
    return value[1:] if len(value) > 1 and value[0] == "'" and value[1] in FORMULA else value


def _more(value):
    """'Cell: 555 0101; Direct: a@b.com' -> channel lines. The type comes from the '@'."""
    out = []
    for part in [p.strip() for p in value.split(";") if p.strip()]:
        label, _, item = part.rpartition(":") if ":" in part and "://" not in part else ("", "", part)
        item = item.strip()
        out.append({"kind": "email" if "@" in item else "phone", "label": label.strip()[:40], "value": item})
    return out


def command_data(mapping, cells, source):
    """One row as `contact.create` data, or raise with the reason."""
    picked = {}
    for target, cell in zip(mapping, cells):
        if target and target != "skip":
            picked[target] = unformula((cell or "").strip())
    name, company = picked.get("name", ""), picked.get("company", "")
    kind = _key(picked.get("kind", "")) or ("person" if name else "company")
    if kind not in ("person", "company"):
        raise crm_store.CRMError("Type must be person or company")
    if kind == "company":
        name, company = name or company, ""
    if not name:
        raise crm_store.CRMError("Name is empty")
    data = {"name": name, "kind": kind}
    if company:
        data["company_name"] = company
    lines = []
    if picked.get("phone"):
        lines.append({"kind": "phone", "label": "", "value": picked["phone"]})
    if picked.get("email"):
        lines.append({"kind": "email", "label": "", "value": picked["email"]})
    lines += _more(picked.get("more", ""))
    if lines:
        data["channels"] = lines
    for k in ("role", "address", "website"):
        if picked.get(k):
            data[k] = picked[k]
    if picked.get("lifecycle"):
        data["lifecycle"] = picked["lifecycle"].lower()
    for k in crm_store.SOURCE:
        value = picked.get(k) or source.get(k, "")
        if value:
            data[k] = value
    data["confirm_duplicate"] = True  # checked once per file, below
    return data


def _source(body):
    src = body.get("source") or {}
    if not isinstance(src, dict):
        raise crm_store.CRMError("Source must be an object")
    return {k: crm_store.text(src.get(k, ""), "Where they came from", 120) for k in crm_store.SOURCE}


def _mapping(header, mapping):
    allowed_ids = {t["id"] for t in targets()} | {"skip"}
    if not isinstance(mapping, list) or len(mapping) != len(header) or not all(m in allowed_ids for m in mapping):
        raise crm_store.CRMError("Map every column to a field or to skip")
    used = [m for m in mapping if m != "skip"]
    if len(used) != len(set(used)):
        raise crm_store.CRMError("Two columns go to the same field. Pick one of them and skip the other.")
    if "name" not in used and "company" not in used:
        raise crm_store.CRMError("Map a column to Name or to Company")
    return mapping


# ------------------------------------------------------------------ duplicates, once per file

def _email(value):
    return (value or "").strip().lower()


class Finder:
    """Same email, same phone digits or same name as a contact already here, loaded once per file.
    It names the owner of the other contact, never its details (the `duplicates.py` rule)."""

    def __init__(self, db, prof):
        names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
        self.seen = []  # (name key, {phone digits}, {emails}, is a company, why)
        lines = {}
        for r in db.execute("SELECT contact_id, kind, value FROM contact_channels"):
            lines.setdefault(r["contact_id"], []).append((r["kind"], r["value"]))
        for r in db.execute("SELECT id, name, phone, email, owner, kind FROM contacts "
                            "WHERE kind != 'business' AND deleted_at IS NULL"):
            mine = lines.get(r["id"], []) + [("phone", r["phone"]), ("email", r["email"])]
            self.seen.append(self._entry(r["name"], mine, r["kind"] == "company",
                                         f"{r['name']} is already a contact of {names.get(r['owner'], r['owner'])}"))

    @staticmethod
    def _entry(name, lines, company, why):
        phones = {duplicates._digits(v) for k, v in lines if k == "phone"} - {""}
        emails = {_email(v) for k, v in lines if k == "email"} - {""}
        return duplicates._name(name), phones, emails, company, why

    @staticmethod
    def _lines(data):
        return [(x["kind"], x["value"]) for x in data.get("channels", [])]

    def match(self, data):
        n, phones, emails, _, _ = self._entry(data["name"], self._lines(data), False, "")
        works_at = duplicates._name(data.get("company_name", ""))
        for name, their_phones, their_emails, company, why in self.seen:
            if emails & their_emails:
                return why + " (same email)"
            # A person on their own company's main line is not a second copy of that company.
            if phones & their_phones and not (company and works_at and name == works_at):
                return why + " (same phone number)"
            if n and name == n:
                return why + " (same name)"
        return None

    def add(self, data, row):
        self.seen.append(self._entry(data["name"], self._lines(data), data["kind"] == "company",
                                     f"{data['name']} is row {row} of this file"))


# ------------------------------------------------------------------ the three steps

def _file_key(raw, row):
    return str(uuid5(NAMESPACE_URL, f"crm-import:{hashlib.sha256(raw).hexdigest()}:row:{row}"))


def run(store, prof, person, body):
    """One import step. `body`: {step, file (base64), mapping?, duplicates?, source?}."""
    allowed(store, prof, person)
    store.authorize(prof, person, "crm_create")
    if not isinstance(body, dict) or body.get("step") not in ("read", "preview", "save"):
        raise crm_store.CRMError("Choose read, preview or save")
    raw = decode(body.get("file"))
    header, rows, delimiter = parse(raw)
    if body["step"] == "read":
        return {"columns": header, "rows": len(rows), "sample": [r[:len(header)] for _, r in rows[:3]],
                "guess": guess(header), "targets": targets(), "delimiter": delimiter}
    mapping = _mapping(header, body.get("mapping"))
    source = _source(body)
    on_duplicate = body.get("duplicates", "skip")
    if on_duplicate not in ("skip", "create"):
        raise crm_store.CRMError("Choose skip or create for duplicates")
    step = preview if body["step"] == "preview" else save
    return step(store, prof, person, raw, header, rows, mapping, source, on_duplicate)


def _plan(store, prof, raw, rows, mapping, source):
    """Each row as (number, data or None, reason, duplicate-of, already saved)."""
    out = []
    with store.connection() as db:
        finder = Finder(db, prof)
        done = {r[0] for r in db.execute("SELECT id FROM commands")}
        for n, cells in rows:
            if _file_key(raw, n) in done:
                out.append((n, None, "Already imported from this file", None, True))
                continue
            try:
                data = command_data(mapping, cells, source)
            except crm_store.CRMError as e:
                out.append((n, None, str(e), None, False))
                continue
            dup = finder.match(data)
            finder.add(data, n)
            out.append((n, data, "", dup, False))
    return out


def preview(store, prof, person, raw, header, rows, mapping, source, on_duplicate):
    plan = _plan(store, prof, raw, rows, mapping, source)
    errors, dups, new, already = [], [], [], 0
    with store.connection() as db:
        db.execute("BEGIN")
        try:
            for n, data, reason, dup, saved in plan:
                if saved:
                    already += 1
                    continue
                if data is None:
                    errors.append({"row": n, "reason": reason})
                    continue
                db.execute("SAVEPOINT row")
                try:  # the real command code, rolled back: what passes here, saves
                    store._change(db, prof, person, "contact.create", data)
                    db.execute("RELEASE row")
                except crm_store.CRMError as e:
                    db.execute("ROLLBACK TO row")
                    db.execute("RELEASE row")
                    errors.append({"row": n, "reason": str(e)})
                    continue
                (dups if dup else new).append({"row": n, "name": data["name"], "reason": dup or ""})
        finally:
            db.rollback()
    return {"total": len(rows), "new": len(new), "new_rows": new[:50], "duplicates": dups, "errors": errors,
            "already": already, "will_create": len(new) + (len(dups) if on_duplicate == "create" else 0)}


def backup_path(store):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = store.path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{store.path.stem}-{stamp}-before-import.sqlite3"


def save(store, prof, person, raw, header, rows, mapping, source, on_duplicate):
    plan = _plan(store, prof, raw, rows, mapping, source)
    created, skipped, failed, backup = [], [], [], None
    for n, data, reason, dup, saved in plan:
        if saved:
            skipped.append({"row": n, "reason": reason})
            continue
        if data is None:
            failed.append({"row": n, "reason": reason})
            continue
        if dup and on_duplicate == "skip":
            skipped.append({"row": n, "reason": "Possible duplicate: " + dup})
            continue
        if backup is None:
            backup = backup_path(store)
            store.backup(backup)
        try:
            result = store.execute(prof, person, {"action": "contact.create", "data": data,
                                                  "request_id": _file_key(raw, n)})
            created.append({"row": n, "id": result["id"]})
        except crm_store.CRMError as e:
            failed.append({"row": n, "reason": str(e)})
    _audit(store, person, "import.contacts", hashlib.sha256(raw).hexdigest()[:16],
           {"rows": len(rows), "created": len(created), "skipped": len(skipped), "failed": len(failed),
            "duplicates": on_duplicate})
    bad = {f["row"]: f["reason"] for f in failed}
    out = io.StringIO()
    if bad:
        w = csv.writer(out, lineterminator="\r\n")
        w.writerow([*header, "Why it failed"])
        for n, cells in rows:
            if n in bad:
                w.writerow([*[guard(c) for c in cells], bad[n]])
    return {"total": len(rows), "created": len(created), "skipped": skipped, "failed": failed,
            "failed_csv": out.getvalue(), "backup": backup.name if backup else None}


# ------------------------------------------------------------------ export

def guard(value):
    """A cell that a spreadsheet would run as a formula goes out as text."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in FORMULA else value


def _csv(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([guard(c) for c in r])
    return "﻿" + out.getvalue()


def _audit(store, person, action, entity, payload, db=None):
    """One audit line, no outbox event: an export or a file summary is not a record change."""
    stamp, eid = crm_store.now(), str(uuid4())
    row = (eid, eid, "human:" + person.id, action, entity, json.dumps(payload), stamp)
    if db is not None:
        db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", row)
        return
    with store.connection() as own:
        own.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", row)


def _zone(prof):
    crm = prof.get("crm") or {}
    for name in (crm.get("timezone"), (crm.get("payments") or {}).get("timezone"),
                 (crm.get("leaderboard") or {}).get("timezone")):
        if isinstance(name, str) and name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return timezone.utc


def _local(value, zone, fmt="%Y-%m-%d %H:%M"):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone(zone).strftime(fmt)
    except ValueError:
        return value[:16]


def _contacts(db, names, zone):
    header = [label for fid, label, _ in FIXED] + EXPORT_ONLY
    last = {r[0]: r[1] for r in db.execute("SELECT contact_id, max(created_at) FROM activities "
                                           "WHERE deleted_at IS NULL GROUP BY contact_id")}
    companies = {r[0]: r[1] for r in db.execute("SELECT id, name FROM contacts WHERE kind='company'")}
    lines = {}
    for r in db.execute("SELECT contact_id, kind, label, value FROM contact_channels ORDER BY contact_id, position"):
        lines.setdefault(r["contact_id"], []).append(dict(r))
    rows = []
    for c in db.execute("SELECT * FROM contacts WHERE deleted_at IS NULL AND kind != 'business' ORDER BY kind, name"):
        mine = lines.get(c["id"], [])
        main = {k: next((x for x in mine if x["kind"] == k), None) for k in ("phone", "email")}
        more = "; ".join((x["label"] + ": " if x["label"] else "") + x["value"]
                         for x in mine if x is not main["phone"] and x is not main["email"])
        rows.append([c["name"], c["kind"], c["email"], c["phone"], more, c["role"], c["address"], c["website"],
                     c["lifecycle"], c["source_channel"], c["source_campaign"], c["source_ad"],
                     companies.get(c["company_id"], ""), names.get(c["owner"], c["owner"]),
                     _local(c["created_at"], zone, "%Y-%m-%d"), _local(last.get(c["id"]), zone, "%Y-%m-%d")])
    return header, rows


def _deals(store, prof, db, names, zone):
    stages = {s["id"]: s["label"] for s in store.stages(prof, db)}
    from . import handoff
    offers = {o["id"]: o.get("label", o["id"]) for o in handoff.offers(prof)}
    rows = [[d["title"], d["contact"], stages.get(d["stage"], d["stage"]), offers.get(d["offer"] or "", d["offer"] or ""),
             f"{(d['amount_cents'] or 0) / 100:.2f}", f"{(d['monthly_cents'] or 0) / 100:.2f}", d["currency"],
             names.get(d["owner"], d["owner"]), _local(d["won_at"], zone, "%Y-%m-%d"), d["lost_reason"],
             _local(d["created_at"], zone, "%Y-%m-%d")]
            for d in db.execute("SELECT r.*, c.name AS contact, c.owner FROM deals r JOIN contacts c ON c.id=r.contact_id "
                                "WHERE r.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY r.created_at")]
    return DEAL_COLUMNS, rows


def _todos(prof, db, names, zone):
    labels = {c["id"]: c["label"] for c in crm_store.categories(prof) + crm_store.categories(prof, "business")}
    followup = {"next": "Ready to work", "client": "Waiting on someone", "blocked": "Blocked",
                "decisions": "Decision needed", "parked": "Parked for later"}
    titles = {r[0]: r[1] for r in db.execute("SELECT id, title FROM tasks WHERE is_main=1")}
    header = TODO_COLUMNS[:3] + [f"Due ({getattr(zone, 'key', 'UTC')})"] + TODO_COLUMNS[4:]
    rows = [[t["title"], t["record"], titles.get(t["parent_id"], "") if not t["is_main"] else "(main task)",
             _local(t["due_at"], zone), labels.get(t["category"], t["category"]), t["department"],
             names.get(t["assignee"] or t["owner"], t["assignee"] or t["owner"]),
             followup.get(t["followup_state"], t["followup_state"] or ""), t["waiting_on"], t["blocked_reason"]]
            for t in db.execute("SELECT t.*, c.name AS record, c.owner FROM tasks t JOIN contacts c ON c.id=t.contact_id "
                                "WHERE t.status='open' AND t.archived_at IS NULL AND c.deleted_at IS NULL "
                                "ORDER BY t.due_at")]
    return header, rows


def export(store, prof, person):
    """(filename, ZIP bytes): contacts, deals and open to-dos as CSV. One audit line per export."""
    allowed(store, prof, person)
    names = {m["id"]: m.get("name", m["id"]) for m in prof["people"]["members"]}
    zone = _zone(prof)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")  # one consistent moment for all three files
        files = {"contacts": _contacts(db, names, zone), "deals": _deals(store, prof, db, names, zone),
                 "open-todos": _todos(prof, db, names, zone)}
        if (prof.get("gym") or {}).get("enabled") is True:  # members, plans, memberships, attendance, ranks, classes
            from . import gym_io
            files.update(gym_io.files(prof, db, names, zone))
        counts = {k: len(rows) for k, (_, rows) in files.items()}
        _audit(store, person, "export.csv", "export", {"rows": counts}, db)
    day = datetime.now(zone).strftime("%Y-%m-%d")
    stem = f"{prof['business_id']}-crm-{day}"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for what, (header, rows) in files.items():
            z.writestr(f"{stem}/{what}.csv", _csv(header, rows).encode("utf-8"))
    return stem + ".zip", out.getvalue(), counts


# ------------------------------------------------------------------ the two routes (server.py)

def body_cap(path):
    """The largest POST body server.py reads. A contact import carries its CSV file: the one larger
    body a person sends. Every other route keeps its own limit (brain_pages.body_cap)."""
    from . import brain_pages
    return BODY_LIMIT if path in (IMPORT_PATH, "/api/gym/import") else brain_pages.body_cap(path)


def serve(handler, path, prof, person, raw):
    """Answer POST /api/crm/export (a ZIP download) or /api/crm/import (JSON), after sign-in and
    the same-origin check in server.py."""
    json_type, private = "application/json; charset=utf-8", {"Cache-Control": "private, no-store"}  # server.PRIVATE
    try:
        if path == EXPORT_PATH:
            name, data, _ = export(handler.cc.crm, prof, person)
            return handler._send(200, data, "application/zip",
                                 {**private, "Content-Disposition": f'attachment; filename="{name}"'})
        result = run(handler.cc.crm, prof, person, json.loads(raw))
        return handler._send(200, json.dumps(result), json_type, private)
    except crm_store.CRMError as e:
        return handler._send(e.status, json.dumps({"error": str(e)}), json_type, private)
    except (ValueError, TypeError):
        return handler._send(400, json.dumps({"error": "Invalid request"}), json_type, private)
