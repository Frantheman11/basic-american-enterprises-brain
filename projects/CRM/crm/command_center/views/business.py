"""The Business — the departments, and what is actually filed in each one.

Departments come from the profile and are never hardcoded, because the engine must draw
the next client's model just as readily as this one. No engine file names a business, and
a test greps for it — including in the prose, so the rule stays a plain grep rather than
something clever enough to be argued with.

Two properties make this more than a folder listing, and both are computed:

  * **A file claimed by no department is homeless**, and homeless files are how a whole
    function hides. That is literally how Research earned its place here: eleven research
    and comps documents scattered across `docs/` by filename convention alone.
  * **An open call is counted where it lives.** This repo marks a decision awaiting Sam
    with a red circle; counting them per department turns a convention into a number, and
    the number has a file behind it.

The site's v1.0 direction calls this same idea the *departments lens* and sets one rule
that carries over exactly: **the board draws what the file says and never fills a room to
look full.** An empty department renders empty.
"""

import re
from pathlib import Path

from ..schema import node

ID = "business"
TITLE = "Business"
BLURB = "The business by department: what each one owns, and what is filed in it."

RED_CIRCLE = "\U0001F534"

# Folders whose contents are listed as one line rather than one line per file. A
# department that owns 87 questionnaire runs should say so, not print 87 rows.
ROLLUP_AT = 12


_COMPILED = {}


def matches(rel, pattern):
    """Glob a path the way the person writing the profile expects.

    `*` stays inside one path segment and `**` crosses them, as in .gitignore and every
    other tool that matches paths. `fnmatch` does NOT do this — its `*` crosses `/` — and
    that difference quietly made `data/scan-ledger/*` claim all 31 lead files that were
    meant to belong to sales. A predictable matcher is cheaper than remembering the quirk.
    """
    rx = _COMPILED.get(pattern)
    if rx is None:
        out, i = [], 0
        while i < len(pattern):
            c = pattern[i]
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
            elif c == "*":
                out.append("[^/]*")
                i += 1
            elif c == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(c))
                i += 1
        rx = _COMPILED[pattern] = re.compile("".join(out) + r"\Z")
    return bool(rx.match(rel))


def claim(rel, departments, cross_cutting=True):
    """Which departments claim a path.

    A cross-cutting department (the brain) is a lens over the whole repo rather than a
    bucket beside the others, so it counts for "is this filed at all" but never for "do
    two departments both claim this" — otherwise it would contest half the repo.

    A department may also carry `except`: patterns it does NOT claim despite one of its
    `sources` matching. That is how a broad, durable glob survives a filing decision about
    two files inside it — marketing owns `docs/site-*`, but the build brief and the launch
    runbook are operations. Narrowing the glob instead would make every future site doc
    homeless, which turns the tripwire into noise; an `except` line says the decision
    out loud and leaves the general rule intact.
    """
    out = []
    for d in departments:
        if not (cross_cutting or not d.get("cross_cutting")):
            continue
        if not any(matches(rel, pat) for pat in d.get("sources", [])):
            continue
        if any(matches(rel, pat) for pat in d.get("except", [])):
            continue
        out.append(d["id"])
    return out


def _open_calls(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").count(RED_CIRCLE)
    except OSError:
        return 0


# What a file IS, by extension. `schema.KINDS` defines `tool` as "a script an operator
# runs", and this used to call everything that was not .md/.html/.pdf one — so a PNG was a
# tool, and Marketing's rollup read "95 tools" for a department that owns almost no
# scripts. The map is explicit rather than clever: an unrecognised file falls to `data`,
# because an unknown file in a repo is far more likely to be something a program reads
# than something a person runs, and `data` is the least-claiming of the four.
FILE_KIND = {
    "doc":   (".md", ".html", ".htm", ".pdf", ".txt", ".rst"),
    "tool":  (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ps1", ".bat", ".service", ".timer"),
    "asset": (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".mp4", ".mov",
              ".webm", ".wav", ".m4a", ".mp3", ".woff", ".woff2", ".ttf", ".otf", ".css"),
    "data":  (".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".toml", ".ini", ".env"),
}
BY_SUFFIX = {suffix: kind for kind, suffixes in FILE_KIND.items() for suffix in suffixes}


def file_kind(path):
    """The node kind for one file. Lowercased because `.PNG` is a PNG."""
    return BY_SUFFIX.get(Path(path).suffix.lower(), "data")


def _folder_children(dept_id, files, root):
    """Files grouped by their folder, rolled up when a folder is large."""
    folders = {}
    for rel in files:
        folders.setdefault(str(Path(rel).parent).replace("\\", "/"), []).append(rel)
    out = []
    for folder in sorted(folders):
        members = sorted(folders[folder])
        fid = f"business.{dept_id}.{folder.replace('/', '-').replace('.', 'root')}"
        if len(members) > ROLLUP_AT:
            # The count goes in the LABEL, not only in the body: a rolled-up folder has no
            # children, so nothing would compute a rollup for it and the collapsed row
            # would say only "docs/" — which answers nothing, which is the failure.
            # `rollup_counts` carries what was summarised away up to the parent, so the
            # department's own rollup still reconciles with its files-filed figure.
            counts = {}
            for rel in members:
                k = file_kind(rel)
                counts[k] = counts.get(k, 0) + 1
            out.append(node(fid, f"{folder}/ — {len(members)} files", "section", source=folder,
                            rollup_counts=counts,
                            detail={"summary": f"{len(members)} files. Listed as a count "
                                               "because a department is not a file browser.",
                                    "fields": {k: str(v) for k, v in sorted(counts.items())}}))
            continue
        kids = []
        for rel in members:
            p = Path(root) / rel
            opens = _open_calls(p) if p.suffix == ".md" else 0
            kids.append(node(
                f"{fid}.{Path(rel).name.replace('.', '-')}", Path(rel).name,
                file_kind(rel),
                status="blocked" if opens else "ready",
                source=rel,
                detail={"summary": f"{opens} open call(s) awaiting a decision."} if opens else {}))
        out.append(node(fid, f"{folder}/", "section", source=folder, children=kids))
    return out


def build(ctx):
    prof, root = ctx["profile"], ctx["root"]
    depts = prof["departments"]
    files = ctx["files"]          # [(rel, size)] — every non-excluded file in the instance

    claims = {rel: claim(rel, depts) for rel, _ in files}
    exclusive = {rel: claim(rel, depts, cross_cutting=False) for rel, _ in files}
    homeless = sorted(r for r, c in claims.items() if not c)
    contested = sorted(r for r, c in exclusive.items() if len(c) > 1)

    nodes = []
    for d in depts:
        mine = sorted(r for r, c in claims.items() if d["id"] in c)
        opens = sum(_open_calls(Path(root) / r) for r in mine if r.endswith(".md"))
        kids = []
        for sec in d.get("sections", []):
            kids.append(node(f"business.{d['id']}.{sec['id']}", sec["label"], "section",
                             status="ready", source=prof["_path"],
                             detail={"summary": sec.get("plain_english", ""),
                                     "fields": _clean({"note": sec.get("note", "")})}))
        kids += _folder_children(d["id"], mine, root)
        # An open call is information, not a blockage. If every department renders
        # `blocked` the chip stops meaning anything, so the count goes in the fields and
        # the status says only whether the department has anything in it at all.
        nodes.append(node(
            f"business.{d['id']}", d["label"], "department",
            status="ready" if mine else "locked",
            source=prof["_path"],
            detail={"summary": d["plain_english"],
                    "fields": _clean({"owns": d["owns"],
                                      "files filed here": len(mine),
                                      "open calls in them": opens or "",
                                      "note": d.get("note", "")})},
            children=kids))

    if homeless:
        nodes.append(node(
            "business.homeless", "Filed nowhere", "section", status="blocked",
            source=prof["_path"],
            detail={"summary": "Files that no department claims. A function with no home "
                               "looks exactly like this — it is how Research earned its place."},
            children=[node(f"business.homeless.{i}", r, "doc", status="blocked", source=r)
                      for i, r in enumerate(homeless[:200])]))
    if contested:
        nodes.append(node(
            "business.contested", "Claimed twice", "section", status="blocked",
            source=prof["_path"],
            detail={"summary": "Files two departments both claim. Not fatal, but every one is "
                               "a filing question somebody will have to answer twice."},
            children=[node(f"business.contested.{i}", f"{r}  →  {', '.join(claims[r])}",
                           "doc", status="blocked", source=r)
                      for i, r in enumerate(contested[:200])]))

    stats = [
        ("departments", len([d for d in depts if not d.get("cross_cutting")]), prof["_path"]),
        ("files filed", len(files) - len(homeless), "computed from the profile source globs"),
        ("filed nowhere", len(homeless), "computed from the profile source globs"),
        ("claimed twice", len(contested), "computed from the profile source globs"),
    ]
    return {"nodes": nodes, "stats": stats, "notes": [
        "A department draws what the files say. An empty department renders empty — never "
        "filled to look full."]}


def _clean(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
