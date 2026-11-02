"""More than one phone and email on a contact, each with a label (CRM feedback batch 1, Sam 2026-09-28).

Ryan took Moyo Tires from Research and reached Manny, the manager, on his direct line. A contact
held one phone and one email, so the second number had nowhere to go.

`contact_channels` holds every number and address. The FIRST phone and the FIRST email are also
kept in `contacts.phone` / `contacts.email`, so every reader of those two columns (Call buttons,
My day, email drafts, the duplicate check, Research) keeps working unchanged. This module is the
one writer of both: a command sends the whole list (`channels`), or only `phone` / `email` as
before, and the list and the two columns are written together in the same transaction.

The rest of a contact's details live here too: a person's role (batch 1), and its address and
website, its company link, and a change of type between person and company (batch 2, 2026-09-29).

A contact made by another writer (Research's Take, an import) has its columns and no list yet.
`migrate` copies those on every start, and the screen falls back to the columns until then.
"""
import re
from uuid import uuid4

from . import crm_store

KINDS = ("phone", "email")
LABELS = ("Main line", "Direct", "Cell", "Office", "Other")  # suggestions; free text is fine
LIMIT = 12
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS contact_channels (
        id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
        kind TEXT NOT NULL CHECK(kind IN ('phone','email')), label TEXT NOT NULL DEFAULT '',
        value TEXT NOT NULL, position INTEGER NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS contact_channels_contact ON contact_channels(contact_id, position)")
    columns = {r["name"] for r in db.execute("PRAGMA table_info(contacts)")}
    for column in ("role", "address", "website"):  # role: Owner, Manager, Front desk
        if column not in columns:
            db.execute(f"ALTER TABLE contacts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    for kind in KINDS:
        rows = db.execute(f"SELECT id,{kind} FROM contacts c WHERE {kind}!='' AND NOT EXISTS "
                          "(SELECT 1 FROM contact_channels x WHERE x.contact_id=c.id AND x.kind=?)", (kind,)).fetchall()
        for row in rows:
            db.execute("INSERT INTO contact_channels VALUES (?,?,?,?,?,?)",
                       (str(uuid4()), row["id"], kind, "Main line" if kind == "phone" else "Main", row[kind], 0))


def current(db, cid):
    return [{"kind": r["kind"], "label": r["label"], "value": r["value"]} for r in db.execute(
        "SELECT kind,label,value FROM contact_channels WHERE contact_id=? ORDER BY position", (cid,))]


def clean(items):
    """The checked list. Blank values are dropped, so an empty row on the form is harmless."""
    if not isinstance(items, list) or len(items) > LIMIT:
        raise crm_store.CRMError(f"A contact holds at most {LIMIT} phone numbers and emails")
    out = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"kind", "label", "value"} or item.get("kind") not in KINDS:
            raise crm_store.CRMError("Each line needs a type: phone or email")
        kind = item["kind"]
        value = crm_store.text(item.get("value", ""), "Email" if kind == "email" else "Phone", 254 if kind == "email" else 60)
        if not value:
            continue
        if kind == "email" and not EMAIL.fullmatch(value):
            raise crm_store.CRMError("Enter a valid email address")
        out.append({"kind": kind, "label": crm_store.text(item.get("label", ""), "Label", 40), "value": value})
    return out


def legacy(db, cid, data):
    """The list after an old-style write of `phone` and/or `email`: that value replaces the
    first of its kind (or is added first), and a blank one removes the first of its kind."""
    items = current(db, cid)
    if not items:  # columns written by another writer, not copied yet
        row = db.execute("SELECT phone,email FROM contacts WHERE id=?", (cid,)).fetchone()
        items = [{"kind": k, "label": "", "value": row[k]} for k in KINDS if row and row[k]]
    for kind in KINDS:
        if kind not in data:
            continue
        value = data[kind]
        at = next((i for i, x in enumerate(items) if x["kind"] == kind), None)
        if at is None:
            if value:
                items.insert(0, {"kind": kind, "label": "", "value": value})
        elif value:
            items[at] = {**items[at], "value": value}
        else:
            items.pop(at)
    return items


def write(db, cid, items, stamp):
    """Replace the list and set the two columns to the first phone and first email."""
    db.execute("DELETE FROM contact_channels WHERE contact_id=?", (cid,))
    for position, item in enumerate(items):
        db.execute("INSERT INTO contact_channels VALUES (?,?,?,?,?,?)",
                   (str(uuid4()), cid, item["kind"], item["label"], item["value"], position))
    first = {k: next((x["value"] for x in items if x["kind"] == k), "") for k in KINDS}
    db.execute("UPDATE contacts SET phone=?,email=?,updated_at=? WHERE id=?", (first["phone"], first["email"], stamp, cid))


def first_phone(data):
    """The phone the duplicate check compares: `phone`, else the first phone in the list."""
    return data.get("phone") or next((x.get("value") for x in data.get("channels") or []
                                      if isinstance(x, dict) and x.get("kind") == "phone"), None)


def apply(db, row, data, checked, stamp):
    """The contact write's part: the role, then the list (`channels`, or old-style phone/email)."""
    role = crm_store.text(data.get("role", row.get("role", "")), "Role", 60)
    if role and row["kind"] != "person":
        raise crm_store.CRMError("Only a person has a role")
    address = crm_store.text(data.get("address", row.get("address", "")), "Address", 300)
    website = crm_store.text(data.get("website", row.get("website", "")), "Website", 300)
    if website:
        website = website if re.match(r"https?://", website, re.I) else "https://" + website
        host = website.split("://", 1)[1].split("/", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(:\d+)?", host):
            raise crm_store.CRMError("Enter a website like example.com")
    from . import territory
    state = territory.code(data.get("territory", row.get("territory", "")))
    if state is None:
        raise crm_store.CRMError("Choose a US state, or leave the state empty")
    db.execute("UPDATE contacts SET role=?,address=?,website=?,territory=? WHERE id=?",
               (role, address, website, state, row["id"]))
    if "channels" in data:
        write(db, row["id"], clean(data["channels"]), stamp)
    elif "email" in data or "phone" in data:
        given = {k: checked[k] for k in KINDS if k in data}
        write(db, row["id"], clean(legacy(db, row["id"], given)), stamp)


def link_company(store, db, prof, person, row, data, stamp):
    """Apply a company link inside Store.execute's transaction; return any new company
    for its audit/outbox entries. The store retains command authorization and revision checks.
    A named company is reused only within the contact owner's scope.
    """
    if "company_id" not in data and "company_name" not in data:
        return None
    company_id = data.get("company_id") or None
    company_name = crm_store.text(data.get("company_name", ""), "Company name")
    if row["kind"] != "person" and (company_id or company_name):
        raise crm_store.CRMError("Only people can be linked to a company")
    if company_id and company_name:
        raise crm_store.CRMError("Choose an existing company or enter a new company name")
    created = None
    if company_name:
        store.authorize(prof, person, "crm_create")
        existing = db.execute("SELECT id,name FROM contacts WHERE kind='company' AND owner=? AND deleted_at IS NULL",
                              (row["owner"],)).fetchall()
        match = next((c for c in existing if c["name"].casefold() == company_name.casefold()), None)
        company_id = match["id"] if match else str(uuid4())
        if not match:
            from . import territory  # a new company starts in its first person's state
            db.execute("INSERT INTO contacts (id,owner,kind,name,created_at,updated_at,territory) VALUES (?,?,'company',?,?,?,?)",
                       (company_id, row["owner"], company_name, stamp, stamp, territory.code(data.get("territory")) or ""))
            created = {"id": company_id, "name": company_name, "kind": "company", "owner": row["owner"]}
    if company_id:
        company = store.contact(db, prof, person, company_id)
        if company["kind"] != "company" or company["owner"] != row["owner"]:
            raise crm_store.CRMError("Choose a company with the same relationship owner")
    db.execute("UPDATE contacts SET company_id=? WHERE id=?", (company_id, row["id"]))
    row["company_id"] = company_id
    return created


def change_kind(db, row, kind):
    """Person to company, or back, when a contact was saved as the wrong type. A company with
    people linked keeps its type until they move; a person who becomes a company leaves their
    company and drops their role. Deals, to-dos, notes and the Research link stay on it."""
    if row["kind"] == "business" or kind not in ("person", "company"):
        raise crm_store.CRMError("Choose person or company")
    if kind == row["kind"]:
        return
    if kind == "person":
        n = db.execute("SELECT count(*) FROM contacts WHERE company_id=? AND deleted_at IS NULL", (row["id"],)).fetchone()[0]
        if n:
            raise crm_store.CRMError(f"{n} {'person is' if n == 1 else 'people are'} linked to this company. "
                                     "Move or unlink them first.", 409)
    db.execute("UPDATE contacts SET kind=?,company_id=NULL,role='' WHERE id=?", (kind, row["id"]))
    row.update(kind=kind, company_id=None, role="")
