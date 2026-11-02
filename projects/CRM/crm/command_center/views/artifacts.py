"""Artifacts — interactive pages, filed into the department they belong to.

A registry file, `data/command-center/artifacts.<business_id>.json`, one row per
artifact: title, url, department, kind, owner, last_touched, state.

🔴 **The embedding question, answered with evidence rather than assumed.** Whether a
claude.ai artifact can be framed inside a local page is recorded in the registry's
`embedding` block by whoever checked, with the date and what they observed. Until that
block says otherwise this view renders a filed, searchable, click-to-open registry —
which delivers most of the value either way, and does not build a design on a guess.

Note also: an artifact that holds a working checklist needs a real store behind it. That
is an artifact-capability question, not a command centre one.
"""

import json
from pathlib import Path

from ..schema import node

ID = "artifacts"
TITLE = "Artifacts"
BLURB = "Interactive pages, filed to a department and openable from here."


def registry_path(prof, root):
    return Path(root) / "data" / "command-center" / f"artifacts.{prof['business_id']}.json"


def build(ctx):
    prof, root = ctx["profile"], ctx["root"]
    p = registry_path(prof, root)
    if not p.is_file():
        return {"nodes": [], "stats": [],
                "notes": [f"No registry at {p.name}. One row per artifact, and the file is the "
                          "only place an artifact's filing lives."]}
    reg = json.loads(p.read_text(encoding="utf-8"))
    rel = str(p.relative_to(Path(root))).replace("\\", "/")
    rows = reg.get("artifacts", [])
    dept_ids = [d["id"] for d in prof["departments"]]

    by_dept = {}
    for a in rows:
        by_dept.setdefault(a.get("department") or "unfiled", []).append(a)

    nodes = []
    for dept in dept_ids + [k for k in sorted(by_dept) if k not in dept_ids]:
        mine = by_dept.get(dept, [])
        if not mine:
            continue
        kids = []
        for a in mine:
            links = [("open", a["url"])] if a.get("url") else []
            kids.append(node(
                f"artifacts.{dept}.{_slug(a['title'])}", a["title"], "artifact",
                status=a.get("state", "ready"),
                source=rel,
                detail={"summary": a.get("summary", ""),
                        "fields": _clean({"kind": a.get("kind"), "owner": a.get("owner"),
                                          "last touched": a.get("last_touched")}),
                        "links": links}))
        nodes.append(node(f"artifacts.{dept}", dept.title(), "section", source=rel,
                          children=kids))

    emb = reg.get("embedding") or {}
    nodes.append(node(
        "artifacts.embedding", "Can these be embedded?", "section",
        status="done" if emb.get("checked") else "blocked",
        source=rel,
        detail={"summary": emb.get("finding", "Not checked yet. Until somebody checks and "
                                               "writes what they observed here, this stays a "
                                               "registry of links, which is the honest design."),
                "fields": _clean({"checked": emb.get("checked"),
                                  "by": emb.get("by"),
                                  "evidence": emb.get("evidence")})}))

    stats = [("artifacts", len(rows), rel),
             ("filed", sum(1 for a in rows if a.get("department") in dept_ids), rel),
             ("unfiled", sum(1 for a in rows if a.get("department") not in dept_ids), rel)]
    return {"nodes": nodes, "stats": stats, "notes": []}


def _slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")[:48]


def _clean(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
