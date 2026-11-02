"""The brain indexer — walk the repo, emit what the second brain actually contains.

    python command_center/brain_index.py --business cta

writes `data/command-center/brain-index.<business_id>.json`.

This is the new thing, and it is worth building rather than merely nice to have for two
reasons:

  1. **The taxonomy becomes self-policing.** A skill with no declared kind is visibly
     wrong on screen instead of wrong in somebody's memory.
  2. **"Recommended" becomes computable.** A recommendation is a gap the index can see:
     a rule written in memory that no machinery enforces, a script the routing table
     does not name, a file the index has lost track of. Those are rendered as ghost
     nodes, which is Beam's proven pattern — dimmed, with a confidence, a rationale and
     the files the claim was computed from.

The knowledge base agrees, from a different direction: "maintain index.md, one line per
page — skip this and hundreds of files make your AI dumber"
(`knowledge/techniques/professor-glitch-kb.md` §5.2). This index is that rule with a screen attached.

Nothing here is authored. Every entry names the file it came from, and the recommender
cites its evidence. Stdlib only.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_center import VERSION  # noqa: E402
from command_center import profiles  # noqa: E402

SKILL_KINDS = ("engine", "profile", "client", "house", "vendored")
# `type` belongs to Claude Code's auto-memory (memory/ is also its folder): user, feedback,
# project, reference. The register is a SECOND axis beside it, never a rewrite of it.
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MEMORY_KINDS = ("decision", "rule", "reference", "context")
MEMORY_STATUSES = ("active", "superseded")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RED_CIRCLE = "\U0001F534"   # the mark this repo uses for an open call awaiting Sam
RULE_MARK = "★RULE"    # the marker a standing rule carries in its description

# The two quote runs a Python docstring can open with, spelled without typing either one
# literally in this file's own source.
_TRIPLE = ('"' * 3, "'" * 3)


# ---------------------------------------------------------------------------------
# Frontmatter. A deliberately small parser: flat `key: value` lines plus one level of
# nesting under `metadata:`. That is the whole shape this repo uses, in skills and in
# memory notes alike, and a YAML dependency for it would fail the standing
# "do we have the problem it solves" test.
# ---------------------------------------------------------------------------------

def frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    out, parent = {}, None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        m = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), _unquote(m.group(2).strip())
        if indented and parent:
            out.setdefault(parent, {})
            if isinstance(out[parent], dict):
                out[parent][key] = val
        elif val in ("", ">", "|"):
            out[key] = {} if val == "" else ""
            parent = key
        else:
            out[key], parent = val, None
    return out, body


def _unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _rel(path, root):
    return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")


def _excluded(rel, patterns):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.rstrip("/*"))
               for p in patterns)


# Secrets by SHAPE, the same shapes .gitignore keeps out of git. The engine skips them
# whatever a profile says: a client's profile forgetting one must not put a token on a page.
# Found 2026-10-21 when two local OAuth tokens surfaced as "files no department claims".
SECRET_SHAPES = (".env", "*.env", "*token*.json")


def iter_files(root, patterns_exclude, subdir=None):
    """Every file under root (or root/subdir), minus secrets and what the profile excludes.

    ⚠️ The exclude list is load-bearing: `.claude/worktrees/` holds a second checkout of
    this same repo, and walking into it would double every count on the page.
    """
    base = Path(root) / subdir if subdir else Path(root)
    if not base.exists():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = "" if Path(dirpath).resolve() == Path(root).resolve() else _rel(dirpath, root)
        dirnames[:] = [d for d in dirnames
                       if not _excluded((f"{rel_dir}/{d}" if rel_dir else d) + "/*", patterns_exclude)
                       and not _excluded(f"{rel_dir}/{d}" if rel_dir else d, patterns_exclude)]
        for f in filenames:
            if any(fnmatch.fnmatch(f.lower(), s) for s in SECRET_SHAPES):
                continue
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if not _excluded(rel, patterns_exclude):
                yield Path(dirpath) / f, rel


# ---------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------

def index_skills(root, prof):
    """Skills, with their kind.

    Vendored is decided by the lock file, never by editing a file we do not own — an
    edit to a vendored SKILL.md is lost on the next reinstall. Every other kind is
    declared in that skill's own frontmatter.
    """
    b = prof["brain"]
    lock_path = Path(root) / b.get("lock_file", "skills-lock.json")
    vendored = set()
    if lock_path.is_file():
        vendored = set(json.loads(lock_path.read_text(encoding="utf-8")).get("skills", {}))
    skills_dir = Path(root) / b["skills_path"]
    out = []
    if not skills_dir.is_dir():
        return out
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        fm, _body = frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        kind = "vendored" if d.name in vendored else (meta.get("kind") or "")
        refs = (sorted(x.name for x in (d / "references").iterdir())
                if (d / "references").is_dir() else [])
        out.append({
            "name": fm.get("name") or d.name,
            "kind": kind if kind in SKILL_KINDS else "",
            "kind_why": meta.get("kind_why", ""),
            "description": fm.get("description", ""),
            "source": _rel(f, root),
            "bytes": f.stat().st_size,
            "references": refs,
            "locked": d.name in vendored,
        })
    return out


def index_memory(root, prof):
    """Memory notes, their declared type, and the [[wikilink]] graph between them."""
    mdir = Path(root) / prof["brain"]["memory_path"]
    index_file = prof["brain"].get("index_file", "")
    indexed = ""
    ip = Path(root) / index_file if index_file else None
    if ip is not None and ip.is_file():
        indexed = ip.read_text(encoding="utf-8", errors="replace")
    out = []
    if not mdir.is_dir():
        return out
    for f in sorted(mdir.glob("*.md")):
        if index_file and f.name == Path(index_file).name:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body = frontmatter(text)
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        out.append({
            "name": fm.get("name") or f.stem,
            "type": meta.get("type", ""),
            "kind": meta.get("kind", ""),
            "status": meta.get("status", ""),
            "supersedes": _names(meta.get("supersedes", "")),
            "superseded_by": meta.get("superseded_by", ""),
            "review_by": meta.get("review_by", ""),
            "description": fm.get("description", ""),
            "source": _rel(f, root),
            "links": sorted(set(re.findall(r"\[\[([^\]]+)\]\]", body))),
            "open_items": text.count(RED_CIRCLE),
            "in_index": (f.name in indexed) or (f.stem in indexed),
            "bytes": f.stat().st_size,
        })
    return out


def _names(value):
    """`supersedes: a, b` names one or more notes."""
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def register_problems(memory):
    """What makes the decision register untrustworthy, one line each. Empty means sound.

    A note needs a kind and a status. A superseded note names its successor, and that
    successor says so back (`supersedes:`), so neither side can drift alone. Without this
    a reader cannot tell a live decision from a replaced one, and an old price list reads
    exactly like the current one.
    """
    names = {m["name"] for m in memory}
    claimed = {}
    for m in memory:
        for old in m.get("supersedes") or []:
            claimed.setdefault(old, []).append(m["name"])
    out = []
    for m in memory:
        n = m["name"]
        if m.get("kind") not in MEMORY_KINDS:
            out.append(f"{n}: kind must be one of {', '.join(MEMORY_KINDS)}"
                       f" (has {m.get('kind') or 'none'})")
        if m.get("status") not in MEMORY_STATUSES:
            out.append(f"{n}: status must be active or superseded (has {m.get('status') or 'none'})")
        for old in m.get("supersedes") or []:
            if old not in names:
                out.append(f"{n}: supersedes {old}, which is not a note")
        succ = m.get("superseded_by", "")
        if m.get("status") == "superseded":
            if not succ:
                out.append(f"{n}: superseded with no successor (add superseded_by)")
            elif succ not in names:
                out.append(f"{n}: superseded_by {succ}, which is not a note")
            elif succ not in claimed.get(n, []):
                out.append(f"{n}: {succ} does not list it under supersedes")
        elif succ:
            out.append(f"{n}: names a successor but its status is {m.get('status') or 'none'}")
        if n in claimed and m.get("status") != "superseded":
            out.append(f"{n}: {', '.join(claimed[n])} supersedes it, but its status is "
                       f"{m.get('status') or 'none'}")
        if m.get("review_by") and not ISO_DATE.match(m["review_by"]):
            out.append(f"{n}: review_by must be YYYY-MM-DD (has {m['review_by']})")
    return out


def index_knowledge(root, prof):
    """Every .md under the knowledge path, subfolders included.

    ⚠️ It walked the top level only until 2026-10-22 and counted 6 of 83 files: the intake
    lands in `sources/<drive id>/`, `teaching/`, `processes/` and the rest. The name is the
    path under the knowledge folder, because `index.md` repeats in several subfolders.
    """
    kdir = Path(root) / prof["brain"]["knowledge_path"]
    out = []
    if not kdir.is_dir():
        return out
    for f in sorted(kdir.rglob("*.md"), key=lambda p: p.relative_to(kdir).as_posix()):
        text = f.read_text(encoding="utf-8", errors="replace")
        heads = re.findall(r"^##\s+(.+)$", text, re.M)
        title = re.search(r"^#\s+(.+)$", text, re.M)
        rel = f.relative_to(kdir).with_suffix("").as_posix()
        # The pipeline state from the note's frontmatter (raw, vetted, topic, used, rejected).
        # F06, 2026-10-22: knowledge/index.md and foundation_check count these.
        state = ""
        if text.startswith("---"):
            m = re.search(r"^state:\s*(\S+)", text[3:text.find("\n---", 3)], re.M)
            state = m.group(1).strip("'\"") if m else ""
        out.append({
            "name": rel,
            "title": title.group(1).strip() if title else f.stem,
            "folder": rel.split("/")[0] if "/" in rel else "",
            "source": _rel(f, root),
            "sections": len(heads),
            "headings": heads[:14],
            "bytes": f.stat().st_size,
            "state": state,
        })
    return out


def _first_docstring_line(text):
    """The first line of a module docstring, without typing a triple quote in this file."""
    for q in _TRIPLE:
        i = text.find(q)
        if i == -1:
            continue
        j = text.find(q, i + 3)
        if j == -1:
            continue
        # Only a real module docstring: nothing but comments, blank lines or a shebang before it.
        before = text[:i]
        if before.strip() and not all(ln.strip() == "" or ln.lstrip().startswith("#")
                                      for ln in before.splitlines()):
            return ""
        inner = text[i + 3:j].strip()
        return inner.splitlines()[0] if inner else ""
    return ""


def index_tools(root, prof):
    """Every script an operator can run, the first line of its docstring, and whether
    the routing table names it."""
    routing = ""
    rt = Path(root) / ".claude" / "skills" / "use-existing-system" / "SKILL.md"
    if rt.is_file():
        routing = rt.read_text(encoding="utf-8", errors="replace")
    scripts_index = ""
    si = Path(root) / "SCRIPTS.md"
    if si.is_file():
        scripts_index = si.read_text(encoding="utf-8", errors="replace")
    out = []
    for folder in prof["brain"]["tools_paths"]:
        d = Path(root) / folder
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name == "__init__.py":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            out.append({
                "name": f.stem,
                "folder": folder,
                "source": _rel(f, root),
                "summary": _first_docstring_line(text),
                "lines": text.count("\n") + 1,
                "in_routing_table": (f.name in routing) or (f.stem in routing),
                "in_scripts_index": f.name in scripts_index,
            })
    return out


def index_workflows(root, prof):
    """Workflow manifests — the unit of sale.

    Empty is the honest answer today: no manifest.json exists in this repo yet, and the
    view says so rather than inventing one.
    """
    wdir = Path(root) / prof.get("workflows_path", "data/command-center/workflows")
    out = []
    if not wdir.is_dir():
        return out
    for f in sorted(wdir.glob("*/manifest.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": m.get("name", f.parent.name), "version": m.get("version"),
                    "source": _rel(f, root), "trigger": m.get("trigger"),
                    "department": m.get("department")})
    return out


def index_stack(root, prof):
    """The tech stack, derived rather than declared: what the code actually imports from
    outside the standard library, and how much of the repo is stdlib-only."""
    third_party, py_files, stdlib_only = {}, 0, 0
    std = set(sys.stdlib_module_names)
    # Anything that resolves to a file or package IN this repo is local, not a dependency.
    # Computed, not listed: a hand-kept list goes stale the first time a module is added.
    python_files = [(p, rel) for p, rel in iter_files(root, prof.get("exclude", [])) if p.suffix == ".py"]
    local = {p.stem for p, _ in python_files} | {
        p.name for p in Path(root).iterdir() if p.is_dir()}
    for path, rel in python_files:
        py_files += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        # A real import line, not prose in a docstring that happens to start with "import".
        # `import x` / `import x.y` / `import x as y` / `from x import ...` and nothing else.
        mods = set(re.findall(
            r"^[ \t]*(?:import[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?:[.][A-Za-z0-9_.]+)?"
            r"(?:[ \t]+as[ \t]+[A-Za-z_]\w*)?[ \t]*(?:#.*)?$"
            r"|from[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?:[.][A-Za-z0-9_.]+)?[ \t]+import[ \t])",
            text, re.M))
        mods = {a or b for a, b in mods}
        outside = {m for m in mods if m not in std and m not in local}
        if not outside:
            stdlib_only += 1
        for m in outside:
            third_party.setdefault(m, []).append(rel)
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "py_files": py_files,
        "stdlib_only_files": stdlib_only,
        "third_party": [{"name": k, "used_by": sorted(v)[:8], "count": len(v)}
                        for k, v in sorted(third_party.items(), key=lambda kv: -len(kv[1]))],
        "source": "computed from the import lines of every .py file in the repo",
    }


# ---------------------------------------------------------------------------------
# Recommendations. A recommendation is a GAP the index can see, never a hunch. Each one
# carries what it is, how sure it is, why, and the files it was computed from.
# ---------------------------------------------------------------------------------

def recommend(root, sections):
    out = []

    for s in sections["skills"]:
        if s["kind"]:
            continue
        out.append({
            "id": f"kind-{s['name']}",
            "label": f"Declare a kind for {s['name']}",
            "kind": "skill", "confidence": 0.95,
            "rationale": ("This skill declares no kind, so the five-kind taxonomy depends on "
                          "somebody remembering it. One frontmatter line makes it data."),
            "evidence": [s["source"], "docs/brain/skill-taxonomy-2026-09-20.md"],
            "source": "computed: skills with no metadata.kind and no lock entry",
        })

    # A rule written down but enforced by nothing is the exact failure that
    # memory/extract-when-documented.md exists to catch.
    skill_text = " ".join((s["description"] or "") + " " + s["source"] for s in sections["skills"])
    for m in sections["memory"]:
        # The repo's own marker for a standing rule, matched literally. Matching the word
        # "rule" case-insensitively fired on every note that merely mentioned rules.
        if RULE_MARK not in (m["description"] or ""):
            continue
        if m["name"] in skill_text:
            continue
        out.append({
            "id": f"rule-{m['name']}",
            "label": f"No machinery enforces: {m['name']}",
            "kind": "memory", "confidence": 0.55,
            "rationale": ("A standing rule that no skill names. It holds only while somebody "
                          "remembers it. Extract when documented."),
            "evidence": [m["source"], "memory/extract-when-documented.md"],
            "source": "computed: memory notes marked RULE with no skill naming them",
        })

    for t in sections["tools"]:
        if t["in_routing_table"] or t["in_scripts_index"]:
            continue
        out.append({
            "id": f"route-{t['name']}",
            "label": f"Route {t['folder']}/{t['name']}.py in the routing table",
            "kind": "tool", "confidence": 0.7,
            "rationale": ("A script that neither the routing table nor SCRIPTS.md names. The "
                          "next session will not find it and will write a second one. That is "
                          "the mistake use-existing-system exists to prevent."),
            "evidence": [t["source"], ".claude/skills/use-existing-system/SKILL.md", "SCRIPTS.md"],
            "source": "computed: scripts absent from both the routing table and SCRIPTS.md",
        })

    for m in sections["memory"]:
        if m["in_index"]:
            continue
        out.append({
            "id": f"index-{m['name']}",
            "label": f"Index the note: {m['name']}",
            "kind": "memory", "confidence": 0.8,
            "rationale": ("A memory note that MEMORY.md does not list. The knowledge base is "
                          "blunt about this one: skip the index and hundreds of files make the "
                          "system dumber, not smarter."),
            "evidence": [m["source"], "memory/MEMORY.md", "knowledge/techniques/professor-glitch-kb.md"],
            "source": "computed: memory notes absent from the index",
        })

    for i, problem in enumerate(register_problems(sections["memory"])):
        note = problem.split(":", 1)[0]
        src = next((m["source"] for m in sections["memory"] if m["name"] == note), "memory/")
        out.append({
            "id": f"register-{note}-{i}",
            "label": f"Fix the decision register: {problem}",
            "kind": "memory", "confidence": 1.0,
            "rationale": ("Every note says what it is (kind) and whether it still holds "
                          "(status); a replaced one names what replaced it. Otherwise an old "
                          "decision reads exactly like a live one."),
            "evidence": [src],
            "source": "computed: memory metadata kind / status / supersedes / superseded_by",
        })

    if not sections["workflows"]:
        out.append({
            "id": "workflows-none",
            "label": "No workflow manifests exist yet",
            "kind": "workflow", "confidence": 0.9,
            "rationale": ("The workflow is the unit of sale and the architecture specifies a "
                          "manifest per workflow, but no manifest.json is on disk. Until one "
                          "is, the Agents view can only show the run ledgers that exist."),
            "evidence": ["docs/products/cta-buildout-architecture-v1.md", "data/command-center/workflows"],
            "source": "computed: no manifest.json under the profile workflows_path",
        })

    return out


# ---------------------------------------------------------------------------------

def build(business_id="cta", root="."):
    prof = profiles.load(business_id, root)
    sections = {
        "skills": index_skills(root, prof),
        "memory": index_memory(root, prof),
        "knowledge": index_knowledge(root, prof),
        "tools": index_tools(root, prof),
        "workflows": index_workflows(root, prof),
    }
    return {
        "business_id": business_id,
        "engine_version": VERSION,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "generated_date": date.today().isoformat(),
        "profile": prof["_path"],
        "sections": sections,
        "stack": index_stack(root, prof),
        "recommendations": recommend(root, sections),
        "counts": {k: len(v) for k, v in sections.items()},
        "register": {
            "kinds": {k: sum(m["kind"] == k for m in sections["memory"]) for k in MEMORY_KINDS},
            "statuses": {s: sum(m["status"] == s for m in sections["memory"])
                         for s in MEMORY_STATUSES},
            "problems": register_problems(sections["memory"]),
        },
    }


def out_path(business_id, root="."):
    return Path(root) / "data" / "command-center" / f"brain-index.{business_id}.json"


def load_index(business_id="cta", root="."):
    """The written index, or None when it has never been built."""
    p = out_path(business_id, root)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write(business_id="cta", root="."):
    doc = build(business_id, root)
    p = out_path(business_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p, doc


def main():
    ap = argparse.ArgumentParser(description="Index the second brain onto disk.")
    ap.add_argument("--business", default="cta")
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p, doc = write(a.business, a.root)
    print(f"wrote {p}")
    for k, v in doc["counts"].items():
        print(f"  {k:12s} {v}")
    print(f"  {'recommends':12s} {len(doc['recommendations'])}")
    reg = doc["register"]
    print("  register     " + " · ".join(f"{k} {v}" for k, v in {**reg["kinds"], **reg["statuses"]}.items()))
    # The index is still written, so the page shows the gap; the exit code is what fails.
    for problem in reg["problems"]:
        print(f"  ⚠️ {problem}")
    return 1 if reg["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
