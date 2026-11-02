"""What a signed-in person may see and do. The one rule, in one place.

⭐ **The rule lives where records are read.** Never only in a page, a filter box or a
hidden button. `can_see` is the only function that decides whether a person may read a
record, and everything that reads records reaches it: the list and the single record
(`Reader`), the approvals queue (`records.pending`), `/api/state`, `/api/view`, every view
and every count on Today. That is Frappe CRM's "one condition, two call sites": the list
and the single-record check cannot disagree, because they are the same function
(research §6.2, pattern P1). Odoo puts the same rule inside its ORM (research §4.2).

The rules, from design §4.4:

    sees: all                         everything
    sees: own                         a record whose `owner` is this person
    sees: territory                   own, plus a CRM contact in one of this person's states
                                      (territory.py); a file record: own
    an unowned lead                   admins only. Sam's inbox, not Odoo's shared pool
    a conversation                    visible when the record it is about is visible
    a record type with no owner      invisible to `own`. EspoCRM's fail-closed OnlyOwn:
                                      a record the rule cannot judge shows nothing

⭐ **Visibility is computed from the CURRENT owner, every time.** There is no grant to
store, so there is no grant to forget to revoke. Frappe CRM, as read, leaves a reassigned
rep with access until someone closes the old assignment by hand (research §8.2). Here a
reassignment moves access at once, because nothing else was ever written.

`person=None` is the **in-process operator**: a Python caller on this machine (a test, a
tool, the scanner). It sees and writes everything, with `by` as given. The HTTP server
never passes None, and a structural test parses `server.py` to prove it.

Roles are profile data (`roles` in `data/command-center/<id>.json`). This module reads
`sees`, `views`, `can` and `fields`, and never a role's NAME, so a business can call its
roles anything.
"""

from .profiles import OWNER_FIELD, RECORD_VIEWS


# ---------------------------------------------------------------------------------
# The role
# ---------------------------------------------------------------------------------

def role_of(prof, person):
    """The role's rules for this person, or None when the profile does not define it."""
    if person is None or str(person.role).startswith("$"):
        return None
    r = (prof.get("roles") or {}).get(person.role)
    return r if isinstance(r, dict) else None


def sees(prof, person):
    """`all`, `own`, `territory`, or None (sees nothing). The operator sees all.

    Anything but `all` is a restricted level: code that only knows `all` and `own` treats
    `territory` as `own`, which shows less, never more."""
    if person is None:
        return "all"
    r = role_of(prof, person)
    level = (r or {}).get("sees")
    return level if level in ("all", "own", "territory") else None


# ---------------------------------------------------------------------------------
# CRM contacts: the one rule every CRM reader uses
# ---------------------------------------------------------------------------------

def contact_scope(prof, person, alias="c"):
    """(sql, params) to AND onto a query over `contacts AS alias`: the contacts this person sees.

    all: no condition. own: the owner is this person. territory: own, or the contact's state is one
    of this person's, or the contact has no state and its company's is. Its own state wins: a person
    moved to Illinois leaves Wisconsin even when the company stays there. No state anywhere: no match.
    """
    level = sees(prof, person)
    if level == "all":
        return "", ()
    pid = person.id if person is not None else ""
    if level == "territory":
        from . import territory
        states = sorted(territory.states_of(prof, person))
        if states:
            marks = ",".join("?" * len(states))
            return (f" AND ({alias}.owner=? OR {alias}.territory IN ({marks}) OR ({alias}.territory='' AND {alias}.company_id IN "
                    f"(SELECT tc.id FROM contacts tc WHERE tc.territory IN ({marks}))))", (pid, *states, *states))
    return f" AND {alias}.owner=?", (pid,)


def sees_contact(prof, person, row, company=None):
    """The same rule for one contact row in Python. `company` is its company's row, if any."""
    level = sees(prof, person)
    if level == "all":
        return True
    if level is None or person is None:
        return False
    if row.get("owner") == person.id:
        return True
    if level != "territory":
        return False
    from . import territory
    states = territory.states_of(prof, person)
    own = row.get("territory") or ""
    return bool(states) and (own in states or not own and bool(company) and company.get("territory") in states)


def can_do(prof, person, action):
    """Whether this person's role is allowed an action at all. The operator can do all."""
    if person is None:
        return True
    return action in ((role_of(prof, person) or {}).get("can") or [])


def views_for(prof, person):
    """The views this person gets: the role's views, intersected with the profile's.

    A view the profile does not offer is never offered to a role, and a role with no
    views gets none. An own-records role gets record-built views only, even if a profile
    slipped past the validator.
    """
    offered = list(prof.get("views") or [])
    if person is None:
        return offered
    r = role_of(prof, person)
    if not r:
        return []
    mine = set(r.get("views") or [])
    out = [v for v in offered if v in mine]
    if sees(prof, person) != "all":
        out = [v for v in out if v in RECORD_VIEWS]
    return out


def _spec(prof, record_type):
    spec = (prof.get("records") or {}).get(record_type)
    return spec if isinstance(spec, dict) else None


def ownable(prof, record_type):
    """Whether this record type carries an owner the rule can judge by."""
    spec = _spec(prof, record_type)
    return bool(spec) and OWNER_FIELD in spec.get("human_owned", [])


def _get(rec, path):
    node = rec
    for p in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


# ---------------------------------------------------------------------------------
# See
# ---------------------------------------------------------------------------------

def can_see(prof, person, record_type, rec, root=".", _memo=None, _depth=0):
    """Whether this person may read this record. False whenever the rule cannot judge."""
    level = sees(prof, person)
    if level == "all":
        return True
    if level not in ("own", "territory") or not isinstance(rec, dict):  # a file record has no state
        return False
    spec = _spec(prof, record_type)
    if spec is None:
        return False
    if ownable(prof, record_type):
        # An empty owner is nobody's, which makes it an admin's (the inbox).
        return bool(person.id) and (rec.get(OWNER_FIELD) or "") == person.id
    through = spec.get("visible_through") or {}
    if not through or _depth:
        return False              # no owner and nothing to follow: fail closed
    from . import records         # the reader lives there; imported late to avoid a cycle
    for path, target in through.items():
        ref = _get(rec, path)
        if not isinstance(ref, str) or not records.SAFE_ID.match(ref):
            continue
        key = (target, ref)
        if _memo is not None and key in _memo:
            if _memo[key]:
                return True
            continue
        try:
            target_rec = records.read(prof, target, ref, root)
        except records.WriteRefused:
            target_rec = None
        ok = bool(target_rec) and can_see(prof, person, target, target_rec, root,
                                          _memo, _depth + 1)
        if _memo is not None:
            _memo[key] = ok
        if ok:
            return True
    return False


# ---------------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------------

def why_not_write(prof, person, record_type, rec, field, action="set_field", root="."):
    """None when this person may make this change, else the reason, in words.

    The caller has already checked that the field is human-owned at all (one writer per
    field) and that the record is visible; this adds what the ROLE allows on top.
    """
    if person is None:
        return None
    r = role_of(prof, person)
    if not r:
        return (f"{person.name} has the role {person.role!r}, which this business's profile "
                "does not define, so they can change nothing")
    can = r.get("can") or []
    if action not in can:
        return (f"a {person.role} cannot {action.replace('_', ' ')}. "
                f"This role can: {', '.join(can) or 'nothing'}")
    if not can_see(prof, person, record_type, rec, root):
        return f"this {record_type} is not one {person.name} can see"
    fields = r.get("fields")
    if fields is not None:
        mine = fields.get(record_type) or []
        if field not in mine:
            return (f"a {person.role} may not set {field!r} on a {record_type}. "
                    f"Fields this role can set: {', '.join(mine) or 'none'}")
    return None


def can_write(prof, person, record_type, rec, field, action="set_field", root="."):
    """The person can see the record, the role can do the action, and the field is theirs."""
    return why_not_write(prof, person, record_type, rec, field, action, root) is None


def why_not_create(prof, person, record_type, action, owner=None):
    """None when this person may create this record, else the reason.

    An own-records role may only create a record it will be able to see afterwards, which
    means an ownable type, owned by itself. Anything else would be a record they wrote and
    then cannot read.
    """
    if person is None:
        return None
    r = role_of(prof, person)
    if not r:
        return f"{person.name} has the role {person.role!r}, which this profile does not define"
    can = r.get("can") or []
    if action not in can:
        return (f"a {person.role} cannot {action.replace('_', ' ')}. "
                f"This role can: {', '.join(can) or 'nothing'}")
    if sees(prof, person) != "all":
        if not ownable(prof, record_type) or owner != person.id:
            return (f"a {person.role} can only create a {record_type} they will own, "
                    "and this one would not be theirs")
    return None


# ---------------------------------------------------------------------------------
# Who did it
# ---------------------------------------------------------------------------------

def actor_label(prof, by):
    """`by` in words. `human:operator` predates sign-in and says so (design §4.5)."""
    kind, _, ident = str(by or "").partition(":")
    if kind == "human":
        if ident in ("", "operator"):
            return "operator (before sign-in)"
        for m in (prof.get("people") or {}).get("members", []):
            if isinstance(m, dict) and m.get("id") == ident:
                return m.get("name") or ident
        return ident
    if kind in ("run", "rule") and ident:
        return f"{kind} {ident}"
    return str(by or "unknown")


# ---------------------------------------------------------------------------------
# The reader every view uses
# ---------------------------------------------------------------------------------

class Reader:
    """Records as ONE person may see them. The list and the single read share `can_see`.

    A view never calls `records.read` or `records.ids` itself; it reads `ctx["records"]`,
    which is one of these, bound to the person the request resolved to. A test asserts no
    view module reaches past it.
    """

    def __init__(self, prof, person, root="."):
        self.prof, self.person, self.root = prof, person, root
        self._memo = {}

    def can_see(self, record_type, rec):
        return can_see(self.prof, self.person, record_type, rec, self.root, self._memo)

    def read(self, record_type, record_id):
        """One record, or None when it is missing OR hidden. The two look the same on
        purpose: a rep cannot learn that a peer's lead exists by asking for it."""
        from . import records
        try:
            rec = records.read(self.prof, record_type, record_id, self.root)
        except records.WriteRefused:
            return None
        return rec if rec and self.can_see(record_type, rec) else None

    def all(self, record_type):
        """[(id, record)] for every record of this type the person can see, by id."""
        from . import records
        try:
            rids = records.ids(self.prof, record_type, self.root)
        except records.WriteRefused:
            return []
        out = []
        for rid in rids:
            try:
                rec = records.read(self.prof, record_type, rid, self.root)
            except records.WriteRefused:
                continue
            if rec and self.can_see(record_type, rec):
                out.append((rid, rec))
        return out

    def ids(self, record_type):
        return [rid for rid, _ in self.all(record_type)]

    def pending(self):
        from . import records
        return records.pending(self.prof, self.root, person=self.person)
