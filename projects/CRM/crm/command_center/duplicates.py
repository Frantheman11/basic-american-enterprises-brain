"""A warning before a second copy of the same company (sales rep review, 2026-09-27: a second
"Moyo Tires & Auto Service" with the same phone number was saved with no warning).

Checked on contact.create: the same name (case and punctuation ignored) or the same phone
digits, among every contact in the business and the Research pool. It is a warning, not a
lock: the person confirms and saves anyway (`confirm_duplicate`). The warning names the owner
of the other contact, never its details, so a rep learns only that it is taken and by whom.
"""
import re

from . import crm_store, research


def _name(value):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower()).split())


def _digits(value):
    d = re.sub(r"\D", "", value or "")
    return d[-10:] if len(d) >= 7 else ""


def check(db, prof, name, phone):
    """Raise a 409 naming the likely duplicate, or return quietly."""
    n, p = _name(name), _digits(phone)
    if not n and not p:
        return
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
    for row in db.execute("SELECT name, phone, owner, kind FROM contacts WHERE kind != 'business' AND deleted_at IS NULL"):
        if (n and _name(row["name"]) == n) or (p and _digits(row["phone"]) == p):
            same = "same name" if n and _name(row["name"]) == n else "same phone number"
            raise crm_store.CRMError(f"Possible duplicate: {row['name']} is already a contact of "
                                     f"{names.get(row['owner'], row['owner'])} ({same}). Save anyway?", 409)
    root = research.data_root(prof, _root(prof))
    try:
        snap = research.snapshot(prof, root)
    except research.ResearchUnavailable:
        return
    for i in snap["industries"]:
        for c in i["companies"]:
            if (n and _name(c["name"]) == n) or (p and _digits(c.get("phone")) == p):
                raise crm_store.CRMError(f"Possible duplicate: {c['name']} is in Research ({i['label']}). "
                                         "Take it there so it counts and nobody else works it. Save anyway?", 409)


def _root(prof):
    from pathlib import Path
    return Path(prof["_path"]).resolve().parents[2]
