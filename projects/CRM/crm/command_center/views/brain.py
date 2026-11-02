"""The Brain — skills, notes, knowledge, tools, stack, and what the index thinks is missing.

This is the view that makes the taxonomy self-policing. Skills are grouped by their five
kinds, and a skill with no declared kind lands in an "undeclared" group wearing a badge,
where it is visibly wrong rather than wrong in somebody's memory.

Everything here is derived from `data/command-center/brain-index.<id>.json`, which the
indexer writes from the files themselves. Nothing on this screen was typed.
"""

from ..schema import ghost, node

ID = "brain"
TITLE = "Brain"
BLURB = "What the business knows, written down where every agent can read it."

# The five kinds, in the order the taxonomy lists them, plus the bucket that must stay
# empty. Ordering is fixed here rather than sorted so the page reads the same every time.
KIND_ORDER = ("engine", "profile", "client", "house", "vendored", "")
KIND_BLURB = {
    "engine": "One job, any business. Takes a business_id.",
    "profile": "The facts of one business. Data wearing a skill's clothes.",
    "client": "Delivery machinery for the services business.",
    "house": "How we work in this repo, not a job we do.",
    "vendored": "Installed from someone else's source. Re-install, never hand-edit.",
    "": "No kind declared. The taxonomy is depending on memory here.",
}


def _skills(idx):
    src = "data/command-center/brain-index"
    groups = []
    skills = idx["sections"]["skills"]
    for kind in KIND_ORDER:
        members = [s for s in skills if s["kind"] == kind]
        if not members:
            continue
        label = kind.title() if kind else "No kind declared"
        kids = []
        for s in members:
            kids.append(node(
                f"brain.skills.{kind or 'none'}.{s['name']}", s["name"], "skill",
                status="blocked" if not kind else "ready",
                source=s["source"],
                badges=([kind] if kind in ("engine", "profile", "house", "vendored", "client")
                        and kind != "profile" else []) + ([] if kind else ["unknown_kind"]),
                detail={"summary": s["description"],
                        "fields": _clean({"why this kind": s["kind_why"],
                                          "reference files": len(s["references"]) or "",
                                          "locked by": "skills-lock.json" if s["locked"] else ""})}))
        groups.append(node(
            f"brain.skills.{kind or 'none'}", label, "section",
            status="blocked" if not kind else "ready",
            source=s["source"].rsplit("/", 2)[0] if members else src,
            detail={"summary": KIND_BLURB[kind]}, children=kids))
    return node("brain.skills", "Skills", "section", source=".claude/skills/",
                detail={"summary": "One engine per job, one profile per business, never a "
                                   "fork per business."},
                children=groups)


def _memory(idx):
    mem = idx["sections"]["memory"]
    types = {}
    for m in mem:
        types.setdefault(m["type"] or "untyped", []).append(m)
    groups = []
    for t in sorted(types, key=lambda k: (k == "untyped", k)):
        kids = []
        for m in sorted(types[t], key=lambda x: x["name"]):
            fields = _clean({
                "links to": ", ".join(m["links"]) if m["links"] else "",
                "open calls": m["open_items"] or "",
                "in the index": "" if m["in_index"] else "NO — MEMORY.md does not list it",
            })
            kids.append(node(
                f"brain.memory.{t}.{m['name']}", m["name"], "memory",
                status="blocked" if not m["in_index"] else "ready",
                source=m["source"],
                badges=[] if m["in_index"] else ["stale"],
                detail={"summary": m["description"], "fields": fields}))
        groups.append(node(f"brain.memory.{t}", t.title(), "section", source="memory/",
                           children=kids))
    return node("brain.memory", "Notes", "section", source="memory/",
                detail={"summary": "What we decided and why, one fact per file. "
                                   "MEMORY.md is the index that keeps it usable."},
                children=groups)


def _knowledge(idx):
    def leaf(k, parent="brain.knowledge"):
        rest = k["name"][len(k["folder"]) + 1:] if k.get("folder") else k["name"]
        return node(f"{parent}.{rest}", k.get("title") or k["name"], "knowledge",
                    source=k["source"],
                    detail={"summary": f"{k['sections']} sections.",
                            "fields": {"topics": ", ".join(k["headings"][:8])}})
    rows = idx["sections"]["knowledge"]
    kids = [leaf(k) for k in rows if not k.get("folder")]
    for folder in sorted({k["folder"] for k in rows if k.get("folder")}):
        fid = f"brain.knowledge.{folder}"
        inside = [leaf(k, fid) for k in rows if k.get("folder") == folder]
        kids.append(node(fid, f"{folder}/ ({len(inside)})", "section",
                         source=f"knowledge/{folder}/", children=inside))
    return node("brain.knowledge", "Knowledge", "section", source="knowledge/",
                detail={"summary": "What we learned from outside, distilled. Read it before "
                                   "building, then say which won and why."},
                children=kids)


def _tools(idx):
    tools = idx["sections"]["tools"]
    folders = {}
    for t in tools:
        folders.setdefault(t["folder"], []).append(t)
    groups = []
    for f in sorted(folders):
        kids = []
        for t in sorted(folders[f], key=lambda x: x["name"]):
            routed = t["in_routing_table"] or t["in_scripts_index"]
            kids.append(node(
                f"brain.tools.{f}.{t['name']}", f"{t['name']}.py", "tool",
                status="ready" if routed else "blocked",
                source=t["source"],
                badges=[] if routed else ["stale"],
                detail={"summary": t["summary"],
                        "fields": _clean({"lines": t["lines"],
                                          "findable": "" if routed else
                                          "not in the routing table or SCRIPTS.md"})}))
        groups.append(node(f"brain.tools.{f}", f"{f}/", "section", source=f"{f}/", children=kids))
    return node("brain.tools", "Tools", "section", source="tools/, auditor/",
                detail={"summary": "Every script an operator can run. A script the routing "
                                   "table cannot find is a script somebody writes twice."},
                children=groups)


def _stack(idx):
    s = idx["stack"]
    kids = [node("brain.stack.python", f"Python {s['python']}", "metric", source=s["source"]),
            node("brain.stack.stdlib",
                 f"{s['stdlib_only_files']} of {s['py_files']} files need nothing installed",
                 "metric", source=s["source"],
                 detail={"summary": "Stdlib-only is a deliberate property here: the site "
                                    "server, the scanner and this engine all run with no "
                                    "dependency to break."})]
    for t in s["third_party"]:
        kids.append(node(f"brain.stack.dep-{t['name']}", t["name"], "tool", source=s["source"],
                         detail={"summary": f"Imported by {t['count']} file(s).",
                                 "fields": {"used by": ", ".join(t["used_by"])}}))
    return node("brain.stack", "Tech stack", "section", source=s["source"],
                detail={"summary": "Derived from what the code actually imports, not from a "
                                   "list somebody keeps up to date."},
                children=kids)


def _workflows(idx):
    wf = idx["sections"]["workflows"]
    kids = [node(f"brain.workflows.{w['name']}", w["name"], "workflow", source=w["source"],
                 detail={"fields": _clean({"version": w.get("version"),
                                           "trigger": w.get("trigger"),
                                           "department": w.get("department")})})
            for w in wf]
    return node("brain.workflows", "Workflows", "section",
                status="ready" if kids else "blocked",
                source="data/command-center/workflows/",
                detail={"summary": "The workflow is the unit of sale: one manifest each."
                                   if kids else
                                   "None on disk yet. The architecture specifies a manifest "
                                   "per workflow; nothing has been stamped out."},
                children=kids)


def _recommended(idx):
    kids = [ghost(f"brain.recommended.{r['id']}", r["label"], r["kind"],
                  r["confidence"], r["rationale"], r["evidence"], r["source"])
            for r in idx["recommendations"]]
    # The container is a plain section, not a ghost. Its children are the ghosts, and
    # making the wrapper one too printed the rationale block twice on the same row.
    return node("brain.recommended", "Recommended", "section",
                status="ready" if kids else "done",
                source="computed from the index, never authored",
                detail={"summary": "A recommendation is a gap the index can see, not a hunch. "
                                   "Each one carries how sure it is, why, and the files it was "
                                   "computed from."},
                children=kids)


def _clean(d):
    return {k: v for k, v in d.items() if v not in ("", None)}


def build(ctx):
    idx = ctx["brain_index"]
    if idx is None:
        return {"nodes": [], "stats": [],
                "notes": ["The brain index has not been built yet. Run "
                          "`python command_center/brain_index.py --business "
                          f"{ctx['profile']['business_id']}`."]}
    c = idx["counts"]
    stats = [
        ("skills", c["skills"], ".claude/skills/"),
        ("notes", c["memory"], "memory/"),
        ("tools", c["tools"], "tools/, auditor/"),
        ("knowledge files", c["knowledge"], "knowledge/"),
        ("workflows", c["workflows"], "data/command-center/workflows/"),
        ("recommended", len(idx["recommendations"]), "computed"),
    ]
    nodes = [_recommended(idx), _skills(idx), _memory(idx), _knowledge(idx),
             _tools(idx), _stack(idx), _workflows(idx)]
    return {"nodes": nodes, "stats": stats,
            "notes": [f"Indexed {idx['generated']} by engine {idx['engine_version']}."]}
