"""Territory: who sees which records by US state (Portfolio CRM P5, 2026-10-29).

A master agent sees his state's records; a regional lead sees his region's. Both still see their
own records anywhere, as a rep does. The owner sees everything, as before.

Three pieces of data, all optional:
- a contact's `territory`: a two-letter state code ('' = none). A lead from the website brings its
  own (`contact.state`); a person at a company is also judged by the company's state.
- the profile's regions: `crm.territories.regions: [{"id": "west", "label": "West", "states": [...]}]`
- a person's territory: `people.members[].territory: ["WI"]` or `["region:west"]`, used by a role
  with `sees: "territory"`.

The rule itself lives in `access.contact_scope` and `access.sees_contact`, so every reader asks the
same question. A record with no state is never a territory record: only its owner and the people who
see everything see it. A territory person with no territory sees only their own records.
"""
import re

from .schema import SchemaError

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_BY_NAME = {name.casefold(): code for code, name in STATES.items()}
REGION = re.compile(r"[a-z0-9][a-z0-9_-]{0,30}")


def migrate(db):
    """Schema 28: a contact's state. '' for every existing contact."""
    if "territory" not in {r[1] for r in db.execute("PRAGMA table_info(contacts)")}:
        db.execute("ALTER TABLE contacts ADD COLUMN territory TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS contacts_territory ON contacts(territory)")


def code(value):
    """'WI', 'wi' or 'Wisconsin' -> 'WI'. '' for empty. None when it is not a US state."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return ""
    if v.upper() in STATES:
        return v.upper()
    return _BY_NAME.get(v.casefold())


def regions(prof):
    spec = (prof.get("crm") or {}).get("territories") or {}
    return spec.get("regions") or []


def enabled(prof):
    return isinstance((prof.get("crm") or {}).get("territories"), dict)


def states_of(prof, person):
    """The state codes this person covers (regions expanded). Empty when none."""
    if person is None:
        return frozenset()
    member = next((m for m in (prof.get("people") or {}).get("members", []) if m.get("id") == person.id), None)
    wanted = (member or {}).get("territory") or []
    by_id = {r["id"]: r for r in regions(prof)}
    out = set()
    for item in wanted:
        if isinstance(item, str) and item.startswith("region:"):
            out |= set((by_id.get(item[7:]) or {}).get("states") or [])
        elif item in STATES:
            out.add(item)
    return frozenset(out)


def options(prof):
    """For the screen: states in name order, each with its region's label."""
    of = {s: r["label"] for r in regions(prof) for s in r.get("states") or []}
    return [{"id": c, "label": n, "region": of.get(c, "")} for c, n in sorted(STATES.items(), key=lambda x: x[1])]


def visible(db, prof, person, row):
    """access.sees_contact for one contacts row, with its company read from the same database."""
    from . import access
    company = row.get("company_id") and db.execute("SELECT * FROM contacts WHERE id=?", (row["company_id"],)).fetchone()
    return access.sees_contact(prof, person, row, dict(company) if company else None)


def screen(prof, person):
    """What the CRM page needs: the state list (only when territories are on) and the
    signed-in person's own states (only for a territory role)."""
    from . import access
    return {"territories": options(prof) if enabled(prof) else [],
            "my_states": sorted(states_of(prof, person)) if access.sees(prof, person) == "territory" else []}


def validate(prof, where):
    """Profile check: the regions, each person's territory, and roles that see by territory."""
    crm = prof.get("crm") or {}
    spec = crm.get("territories")
    seeing = [rid for rid, r in (prof.get("roles") or {}).items() if isinstance(r, dict) and r.get("sees") == "territory"]
    if spec is None:
        if seeing:
            raise SchemaError(f"{where}: role {seeing[0]!r} sees by territory, but crm.territories is not set")
        for m in (prof.get("people") or {}).get("members", []):
            if m.get("territory"):
                raise SchemaError(f"{where}: {m.get('id')} has a territory, but crm.territories is not set")
        return
    if not isinstance(spec, dict) or not isinstance(spec.get("regions", []), list):
        raise SchemaError(f"{where}: crm.territories must be an object with a regions list")
    ids, taken = set(), {}
    for r in spec.get("regions", []):
        rid = r.get("id") if isinstance(r, dict) else None
        if not isinstance(rid, str) or not REGION.fullmatch(rid) or rid in ids:
            raise SchemaError(f"{where}: region id {rid!r} must be short, lowercase and unique")
        if not isinstance(r.get("label"), str) or not r["label"].strip():
            raise SchemaError(f"{where}: region {rid} needs a label")
        states = r.get("states")
        if not isinstance(states, list) or not states:
            raise SchemaError(f"{where}: region {rid} needs a list of states")
        for s in states:
            if s not in STATES:
                raise SchemaError(f"{where}: region {rid} names {s!r}, which is not a US state code")
            if s in taken:
                raise SchemaError(f"{where}: {s} is in two regions ({taken[s]} and {rid})")
            taken[s] = rid
        ids.add(rid)
    for m in (prof.get("people") or {}).get("members", []):
        t = m.get("territory")
        if t is None:
            continue
        if not isinstance(t, list):
            raise SchemaError(f"{where}: {m.get('id')} territory must be a list like [\"WI\"] or [\"region:west\"]")
        for item in t:
            ok = item in STATES or (isinstance(item, str) and item.startswith("region:") and item[7:] in ids)
            if not ok:
                raise SchemaError(f"{where}: {m.get('id')} territory {item!r} is not a state code or a region:<id>")
