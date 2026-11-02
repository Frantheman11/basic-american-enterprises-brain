"""Context assembly and freshness.

**Freshness, not "real time".** The server exposes `/api/state`, the page asks with
`If-None-Match`, and the server answers `304 Not Modified` when nothing has changed.
Stdlib only, no dependency, no websocket, and it cannot wedge. It is real time enough
for a file-backed system, and the knowledge base's rule applies: do not build
complicated until a simple version has actually failed.

The ETag is a hash over the size and mtime of every file the instance reads. Touching a
memory note changes it; a poll that changes nothing costs one `304` and no rendering.

⭐ There is no second store and no sync job. The CRM and the visual are two renderings of
one set of files, which is why the question "how do they stay in sync" has no answer
here — it dissolves.

## State is built PER PERSON (CRM B1)

`get`, `context` and `build_state` take `person=`. The cache key and the ETag include the
person's id, so an admin's warm state can never be served to a rep (design §4.4, defect 2).

⭐ **A person who sees only their own records never gets the whole repo.** Their context
has no `files` scan, no brain index and no work trees: those read memory, client folders
and pricing, which is exactly what keeps the marketing partner out of this repo. They get
records filtered by `access.can_see`, and only the views their role allows. Their ETag is
a hash over the RECORD folders and the profile, not the repo, and it is checked on every
request with no time window, so a reassignment is felt on the very next request.

`person=None` is the in-process operator (tests, tools): everything, as before.
"""

import hashlib
import json
import time
from pathlib import Path

from . import access, brain_index, profiles, records, work
from .views import MODULES

# A rendered state is reused for this long before the files are re-stat'ed. It keeps a
# tight poll from walking the tree on every request without making the page feel stale.
# ⚠️ Only for a person who sees everything. For anyone scoped to their own records the
# records are fingerprinted on every request, so access moves at once when an owner does.
CACHE_SECONDS = 2.0

OPERATOR = "__operator__"

_cache = {}


def scan_files(root, exclude):
    """(rel, size) for every file the instance can see, in one walk, sorted."""
    return sorted((rel, p.stat().st_size) for p, rel in brain_index.iter_files(root, exclude))


def fingerprint(root, exclude):
    """A hash over the size and mtime of everything the instance reads."""
    h = hashlib.sha256()
    for p, rel in sorted(brain_index.iter_files(root, exclude), key=lambda t: t[1]):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(rel.encode("utf-8"))
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()[:32]


def records_fingerprint(prof, root):
    """A hash over the record folders and the profile, and nothing else in the repo.

    What an own-records person's state is built from, so it is all their ETag needs.
    Every write is a new file, so a write always changes the set of paths hashed here.
    """
    h = hashlib.sha256()
    base = Path(root)
    paths = [Path(prof["_path"])]
    for spec in (prof.get("records") or {}).values():
        if isinstance(spec, dict) and spec.get("path"):
            d = base / spec["path"]
            if d.is_dir():
                paths.extend(p for p in d.rglob("*") if p.is_file())
    for p in sorted(set(paths)):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(str(p).replace("\\", "/").encode("utf-8"))
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()[:32]


def _who(person):
    return person.id if person is not None else OPERATOR


def etag(prof, root, person=None):
    """The ETag for one person's state. Their id is in it, so two people never share one."""
    if access.sees(prof, person) == "all":
        fp = fingerprint(root, prof.get("exclude", []))
    else:
        fp = records_fingerprint(prof, root)
    return hashlib.sha256(f"{_who(person)}\n{fp}".encode("utf-8")).hexdigest()[:32]


def waiting_on_human(prof, root, person=None):
    """The approvals queue: records a human has to decide on before the next run can act.

    This is the durable answer to "what is waiting on me" — the one that survives a
    session, unlike a chat message. The queue lives in `records.py`, which is also the
    only thing that writes it, so the read and the write cannot drift apart. It is
    filtered by `access.can_see` like every other read.
    """
    return records.pending(prof, root, person=person)


def context(business_id, root=".", person=None):
    prof = profiles.load(business_id, root)
    exclude = prof.get("exclude", [])
    # The repo-wide inputs are built only for a person who sees everything. A rep's
    # context never walks memory, client folders or pricing, so no view can leak them.
    whole = access.sees(prof, person) == "all"
    return {
        "profile": prof,
        "root": root,
        "business_id": business_id,
        "person": person,
        "sees": access.sees(prof, person),
        # The ONLY way a view reads a record: bound to this person, filtered by can_see.
        "records": access.Reader(prof, person, root),
        "brain_index": brain_index.load_index(business_id, root) if whole else None,
        "files": scan_files(root, exclude) if whole else None,
        # "Waiting on you" means waiting on YOUR decision. For a role that cannot decide,
        # it is None, not a list: driving B1 found a rep's Today saying a draft was waiting
        # on her when only an admin could clear it.
        "waiting_on_human": (waiting_on_human(prof, root, person)
                             if access.can_do(prof, person, "decide") else None),
        # The business's OWN trees, keyed by department. A view never opens a file,
        # so this is the only place that knows the work lives anywhere.
        "work": work.load(prof, root) if whole else None,
        "exclude": exclude,
    }


def build_state(business_id, root=".", person=None):
    """Every view this person may have, built once. The views read `ctx` and never open
    a file themselves."""
    ctx = context(business_id, root, person)
    prof = ctx["profile"]
    allowed = access.views_for(prof, person)
    views = {}
    for m in MODULES:
        if m.ID not in allowed:
            continue
        out = m.build(ctx)
        views[m.ID] = {"id": m.ID, "title": m.TITLE, "blurb": m.BLURB, **out}
    return {
        "business_id": business_id,
        "business_name": prof["name"],
        "tagline": prof.get("tagline", ""),
        "person": person.public() if person is not None else None,
        "views": views,
        "order": [m.ID for m in MODULES if m.ID in views],
        "etag": etag(prof, root, person),
        "brain_index_built": ctx["brain_index"] is not None,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def get(business_id, root=".", max_age=CACHE_SECONDS, person=None):
    """This person's current state, rebuilt only when their files have actually changed.

    Keyed by business, root, person and role, so no one is ever served another person's
    state, and a role change in the profile starts a fresh entry.
    """
    prof = profiles.load(business_id, root)
    whole = access.sees(prof, person) == "all"
    key = (business_id, str(root), _who(person), person.role if person is not None else "")
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and whole and hit["whole"] and now - hit["checked"] < max_age:
        return hit["state"]
    tag = etag(prof, root, person)
    if hit and hit["state"]["etag"] == tag:
        hit["checked"] = now
        return hit["state"]
    state = build_state(business_id, root, person=person)
    _cache[key] = {"checked": now, "state": state, "whole": whole}
    return state


def as_json(state):
    """The state as the API returns it — nodes included, so a phone or another tool can
    read the same tree the page draws."""
    return json.dumps(state, indent=None, separators=(",", ":")).encode("utf-8")


def invalidate(business_id, root="."):
    """Drop the cached state for one instance.

    A write through `records.py` changes a file, so the fingerprint would catch it on the
    next poll anyway — but the caller that just wrote should not have to wait out the
    cache window to see its own change. Every person's entry goes, because one person's
    write (a reassignment) changes what another person sees.
    """
    for key in [k for k in _cache if k[:2] == (business_id, str(root))]:
        _cache.pop(key, None)
