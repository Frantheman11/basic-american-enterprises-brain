"""The shared tech-tree catalog a profile names (`crm.tech_tree_catalog`): layers and builds,
checked for unknown layers, duplicate ids and prerequisite cycles before anything reads it.
Moved out of crm_store.py to keep the store one readable file; still importable from there."""
import json
from pathlib import Path


def catalog(prof):
    """The profile chooses the shared catalog; no build names live in the engine."""
    from . import crm_store
    name = (prof.get("crm") or {}).get("tech_tree_catalog")
    if not name:
        return {"layers": [], "nodes": []}
    root = Path(prof["_path"]).resolve().parents[2]
    path = (root / name).resolve()
    if root not in path.parents or path.suffix != ".json":
        raise crm_store.CRMError("Catalog must be a JSON file inside the repository", 503)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        layers = {r["id"] for r in data["layers"]}
        nodes = {r["id"]: r for r in data["nodes"]}
        if len(layers) != len(data["layers"]) or len(nodes) != len(data["nodes"]):
            raise ValueError()
        for row in data["nodes"]:
            if row["layer"] not in layers or not row["label"] or not row["value"]:
                raise ValueError()
            if any(n not in nodes or n == row["id"] for n in row["prerequisites"]):
                raise ValueError()
        def visit(key, trail):
            if key in trail:
                raise ValueError()
            for parent in nodes[key]["prerequisites"]:
                visit(parent, trail | {key})
        for key in nodes:
            visit(key, set())
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise crm_store.CRMError("Tech tree catalog is invalid or unavailable", 503) from None


def client_view(store, prof, person):
    """Allowlisted client read model, never an internal snapshot with fields hidden."""
    from . import access, crm_store
    members = (prof.get("people") or {}).get("members", [])
    if (prof.get("business_id") != store.business or person is None or
            not any(m.get("id") == person.id and m.get("role") == person.role for m in members) or
            not access.can_do(prof, person, "buildout_read")):
        raise crm_store.CRMError("Build-out access denied", 403)
    # The server-owned mapping, not an owner assignment or a request parameter, binds tenancy.
    cid = (prof.get("client_buildout") or {}).get("readers", {}).get(person.id)
    if not isinstance(cid, str) or not cid:
        raise crm_store.CRMError("Build-out access denied", 403)
    cat = catalog(prof)
    nodes = {n["id"]: n for n in cat["nodes"]}
    visible = []
    with store.connection() as db:
        db.execute("BEGIN")
        contact = db.execute("SELECT name FROM contacts WHERE id=? AND deleted_at IS NULL", (cid,)).fetchone()
        if contact is None:
            raise crm_store.CRMError("Build-out access denied", 403)
        for row in db.execute("SELECT n.*, r.decision, r.node_revision, r.public_label, "
                              "r.public_value, r.public_reason FROM client_nodes n "
                              "LEFT JOIN node_reviews r ON r.id=n.id WHERE n.contact_id=?", (cid,)):
            node = nodes.get(row["node_id"])
            if node is None:
                continue
            if row["state"] == "built":
                label, value, reason = node["label"], node["value"], ""
            elif (row["state"] == "recommended" and row["decision"] == "approved"
                  and row["node_revision"] == row["revision"]):
                label, value, reason = row["public_label"], row["public_value"], row["public_reason"]
            else:
                continue
            visible.append({"id":node["id"], "layer":node["layer"], "label":label,
                            "value":value, "state":row["state"], "reason":reason})
    layers = [{"id":l["id"], "label":l["label"]} for l in cat["layers"]
              if any(n["layer"] == l["id"] for n in visible)]
    title = (prof.get("client_buildout") or {}).get("title") or "Your Business Tech Tree"
    return {"title":title, "client":contact["name"], "layers":layers, "nodes":visible}


NODE_FIELDS = {"node.set": {"contact_id", "node_id", "revision", "state", "brief_path", "location", "evidence"},
               "node.review": {"contact_id", "node_id", "node_revision", "revision", "decision", "reason"}}


def change(store, db, prof, person, action, data, stamp, rid):
    """node.set and node.review, moved from crm_store.py unchanged. Returns the record id."""
    from . import crm_store
    if set(data) - NODE_FIELDS[action]:
        raise crm_store.CRMError("Unexpected fields in command")
    if action == "node.review":
        contact = store.contact(db, prof, person, data.get("contact_id"))
        nid = crm_store.text(data.get("node_id"), "Build", 80, True)
        node = db.execute("SELECT * FROM client_nodes WHERE contact_id=? AND node_id=?",
                          (contact["id"], nid)).fetchone()
        if not node:
            raise crm_store.CRMError("Record not found", 404)
        store._revision(node, {"revision": data.get("node_revision")})
        rid = node["id"]
        review = db.execute("SELECT * FROM node_reviews WHERE id=?", (rid,)).fetchone()
        store._revision(review or {"revision":0}, data)
        decision = data.get("decision")
        if decision not in ("approved", "revoked"):
            raise crm_store.CRMError("Choose approved or revoked")
        spec = next((n for n in catalog(prof)["nodes"] if n["id"] == nid), None)
        if decision == "approved" and (node["state"] != "recommended" or spec is None):
            raise crm_store.CRMError("Only a current recommended build can be approved")
        reason = crm_store.text(data.get("reason", ""), "Client-facing reason", 600, decision == "approved")
        label, value = (spec["label"], spec["value"]) if decision == "approved" else ("", "")
        db.execute("INSERT INTO node_reviews VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "decision=excluded.decision,actor=excluded.actor,reviewed_at=excluded.reviewed_at,"
                   "node_revision=excluded.node_revision,revision=excluded.revision,"
                   "public_label=excluded.public_label,public_value=excluded.public_value,"
                   "public_reason=excluded.public_reason",
                   (rid, decision, person.by, stamp, node["revision"], data["revision"]+1, label, value, reason))
    elif action == "node.set":
        contact = store.contact(db, prof, person, data.get("contact_id"))
        node_id = crm_store.text(data.get("node_id"), "Build", 80, True)
        if node_id not in {n["id"] for n in catalog(prof)["nodes"]}:
            raise crm_store.CRMError("Choose a configured build")
        found = db.execute("SELECT * FROM client_nodes WHERE contact_id=? AND node_id=?",
                           (contact["id"], node_id)).fetchone()
        row = dict(found) if found else {"revision": 0}
        store._revision(row, data)
        status = data.get("state")
        if status not in ("built", "in_progress", "recommended"):
            raise crm_store.CRMError("Choose built, in progress or recommended")
        brief = crm_store.text(data.get("brief_path", row.get("brief_path", "")), "Brief path", 2000)
        if brief and (not crm_store.BRIEF.fullmatch(brief) or ".." in brief.split("/")):
            raise crm_store.CRMError("Brief path must be a relative .md path inside the repository")
        location = crm_store.text(data.get("location", row.get("location", "")), "Build location", 2000)
        evidence = crm_store.text(data.get("evidence", row.get("evidence", "")), "Evidence", 4000)
        if status == "built" and (not location or not evidence):
            raise crm_store.CRMError("A built node needs evidence and its location in the client's own repository")
        if found:
            rid = row["id"]
            db.execute("UPDATE client_nodes SET state=?,brief_path=?,location=?,evidence=?,"
                       "revision=revision+1,updated_at=? WHERE id=?",
                       (status, brief, location, evidence, stamp, rid))
        else:
            db.execute("INSERT INTO client_nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (rid, contact["id"], node_id, status, brief, location, evidence, 1, stamp, stamp))
    return rid
