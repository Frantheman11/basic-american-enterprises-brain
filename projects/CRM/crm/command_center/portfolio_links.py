"""Linked people (Portfolio CRM P4): one person across several businesses, records kept apart.

"Contact A in BAE is contact B in BAI" lives in one small portfolio-level file,
`<root>/<database_dir>/<portfolio_id>-links.sqlite3`. Nothing is copied and no business database
is written: each record keeps its own history, owner, consent and unsubscribe. A link is only a
pointer, so unlinking leaves both records whole.

Who sees a link: the person must be able to read BOTH records in their own tabs, by the same rule
as the tab (`Store.contact`, which applies own, territory and all). A record the person cannot
see is left out, never named. Who changes links: portfolio owners only. Suggestions come from the
same email or phone, or the same name; nothing is linked automatically.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import crm_store, duplicates, profiles

DDL = """
CREATE TABLE IF NOT EXISTS links (
    business_id TEXT NOT NULL, contact_id TEXT NOT NULL, group_id TEXT NOT NULL,
    linked_by TEXT NOT NULL, linked_at TEXT NOT NULL, PRIMARY KEY (business_id, contact_id));
CREATE INDEX IF NOT EXISTS links_group ON links(group_id);
CREATE TABLE IF NOT EXISTS link_audit (
    id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
"""
SUGGEST_LIMIT = 10


class LinkError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def path(pf):
    return Path(pf.root) / pf.data.get("database_dir", "private") / f"{pf.id}-links.sqlite3"


def _open(pf):
    f = path(pf)
    f.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f, timeout=5)
    db.row_factory = sqlite3.Row
    db.executescript(DDL)
    return db


def groups(pf):
    """{group_id: [(business_id, contact_id), ...]} for the whole portfolio."""
    if not path(pf).is_file():
        return {}
    out = {}
    with closing(_open(pf)) as db:
        for r in db.execute("SELECT * FROM links ORDER BY linked_at"):
            out.setdefault(r["group_id"], []).append((r["business_id"], r["contact_id"]))
    return out


def _group_of(db, bid, cid):
    row = db.execute("SELECT group_id FROM links WHERE business_id=? AND contact_id=?", (bid, cid)).fetchone()
    return row["group_id"] if row else None


# ---------------------------------------------------------------------------------
# Reading, as one person
# ---------------------------------------------------------------------------------

def _seen(pf, seen):
    """{business_id: (entry, person)} for the businesses this sign-in reaches."""
    return {e["business_id"]: (e, p) for e, p in seen}


def _read(pf, seen, bid, cid):
    """The contact row as this person sees it in its own tab, or None (not there, or not theirs)."""
    if bid not in seen or not isinstance(cid, str) or not cid:
        return None
    entry, person = seen[bid]
    store, prof = pf.configs[bid].crm, profiles.load(bid, pf.root)
    try:
        store.authorize(prof, person)
        with store.connection() as db:
            return store.contact(db, prof, person, cid)
    except crm_store.CRMError:
        return None


def also_in(pf, seen, bid, cid):
    """The other records of this person, each one only where the signed-in person may read it.
    Raises 404 when the asking record itself is not one they can see (no probing)."""
    seen = _seen(pf, seen)
    if _read(pf, seen, bid, cid) is None:
        raise LinkError("Record not found", 404)
    out = []
    if not path(pf).is_file():
        return out
    with closing(_open(pf)) as db:
        gid = _group_of(db, bid, cid)
        members = [] if gid is None else db.execute(
            "SELECT business_id, contact_id FROM links WHERE group_id=? ORDER BY linked_at", (gid,)).fetchall()
    for m in members:
        if (m["business_id"], m["contact_id"]) == (bid, cid):
            continue
        row = _read(pf, seen, m["business_id"], m["contact_id"])
        if row is None:
            continue
        entry = seen[m["business_id"]][0]
        out.append({"business": entry["business_id"], "label": entry["label"], "color": entry["color"],
                    "contact_id": row["id"], "name": row["name"],
                    "url": f"{pf.prefix()}b/{entry['business_id']}/?view=contacts&contact={row['id']}"})
    return out


def count_linked(pf, visible):
    """People in two or more businesses, counting only records in `visible` ({business: {ids}})."""
    n = 0
    for members in groups(pf).values():
        here = {b for b, c in members if c in visible.get(b, set())}
        n += len(here) > 1
    return n


def _keys(db, cid):
    row = db.execute("SELECT name FROM contacts WHERE id=?", (cid,)).fetchone()
    chans = db.execute("SELECT kind, value FROM contact_channels WHERE contact_id=?", (cid,)).fetchall()
    emails = {c["value"].strip().lower() for c in chans if c["kind"] == "email" and c["value"].strip()}
    phones = {duplicates._digits(c["value"]) for c in chans if c["kind"] == "phone"} - {""}
    return duplicates._name(row["name"] if row else ""), emails, phones


def suggest(pf, seen, bid, cid):
    """Owner only: records in the other businesses with the same email, phone or name."""
    seen = _seen(pf, seen)
    me = _read(pf, seen, bid, cid)
    if me is None:
        raise LinkError("Record not found", 404)
    with pf.configs[bid].crm.connection() as db:
        name, emails, phones = _keys(db, cid)
    linked = set()
    if path(pf).is_file():
        with closing(_open(pf)) as db:
            gid = _group_of(db, bid, cid)
            if gid:
                linked = {(r["business_id"], r["contact_id"]) for r in
                          db.execute("SELECT business_id, contact_id FROM links WHERE group_id=?", (gid,))}
    out = []
    for other, (entry, person) in seen.items():
        if other == bid:
            continue
        with pf.configs[other].crm.connection() as db:
            for c in db.execute("SELECT id FROM contacts WHERE kind=? AND deleted_at IS NULL", (me["kind"],)):
                if (other, c["id"]) in linked:
                    continue
                n, e, p = _keys(db, c["id"])
                why = ("same email" if emails & e else "same phone" if phones & p
                       else "same name" if name and n == name else "")
                if why and _read(pf, seen, other, c["id"]) is not None:
                    row = _read(pf, seen, other, c["id"])
                    out.append({"business": other, "label": entry["label"], "color": entry["color"],
                                "contact_id": c["id"], "name": row["name"], "why": why})
    order = {"same email": 0, "same phone": 1, "same name": 2}
    return sorted(out, key=lambda s: order[s["why"]])[:SUGGEST_LIMIT]


# ---------------------------------------------------------------------------------
# Writing: owners only
# ---------------------------------------------------------------------------------

def _audit(db, actor, action, payload, stamp):
    db.execute("INSERT INTO link_audit VALUES (?,?,?,?,?)", (str(uuid4()), stamp, actor, action, json.dumps(payload)))


def link(pf, seen, actor, a, b):
    """Join two records (and the groups they are already in). Both must be readable by `actor`."""
    seen = _seen(pf, seen)
    pairs = []
    for side in (a, b):
        if not isinstance(side, dict):
            raise LinkError("Name a business and a contact for each side")
        bid, cid = side.get("business"), side.get("contact")
        row = _read(pf, seen, bid, cid)
        if row is None:
            raise LinkError("Record not found", 404)
        if row["kind"] == "business":
            raise LinkError("A business's own record cannot be linked")
        pairs.append((bid, cid, row["kind"]))
    if pairs[0][0] == pairs[1][0]:
        raise LinkError("Both records are in the same business. Link records in two businesses.")
    if pairs[0][2] != pairs[1][2]:
        raise LinkError("Link a person to a person, or a company to a company")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_open(pf)) as db, db:
        ga, gb = (_group_of(db, p[0], p[1]) for p in pairs)
        if ga and ga == gb:
            return {"group": ga}  # already linked: nothing to do
        businesses = lambda gid, own: ({r[0] for r in db.execute("SELECT business_id FROM links WHERE group_id=?", (gid,))}
                                       if gid else {own})
        if businesses(ga, pairs[0][0]) & businesses(gb, pairs[1][0]):
            raise LinkError("That would put two records from the same business on one person. Unlink one first.", 409)
        target = ga or gb or str(uuid4())
        for gid in {g for g in (ga, gb) if g and g != target}:
            db.execute("UPDATE links SET group_id=? WHERE group_id=?", (target, gid))
        for bid, cid, _ in pairs:
            if _group_of(db, bid, cid) is None:
                db.execute("INSERT INTO links VALUES (?,?,?,?,?)", (bid, cid, target, actor, stamp))
        _audit(db, actor, "link", {"a": [pairs[0][0], pairs[0][1]], "b": [pairs[1][0], pairs[1][1]], "group": target}, stamp)
    return {"group": target}


def unlink(pf, seen, actor, bid, cid):
    """Take one record out of its group. A group left with one record is removed."""
    seen = _seen(pf, seen)
    if _read(pf, seen, bid, cid) is None:
        raise LinkError("Record not found", 404)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_open(pf)) as db, db:
        gid = _group_of(db, bid, cid)
        if gid is None:
            raise LinkError("This record is not linked", 409)
        db.execute("DELETE FROM links WHERE business_id=? AND contact_id=?", (bid, cid))
        left = db.execute("SELECT count(*) FROM links WHERE group_id=?", (gid,)).fetchone()[0]
        if left < 2:
            db.execute("DELETE FROM links WHERE group_id=?", (gid,))
        _audit(db, actor, "unlink", {"record": [bid, cid], "group": gid}, stamp)
    return {"ok": True}


def owner_only(pf, person):
    if person is None or person.id not in pf.data["owners"]:
        raise LinkError("Only the owner can link or unlink people", 403)

