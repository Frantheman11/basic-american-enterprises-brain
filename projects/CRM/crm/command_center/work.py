"""The work loader — the business's own trees, not the filesystem's.

⚠️ **This module exists because the first build drew the wrong tree.** Every view read
either the file system or an index of it, so the only tree in the data was the directory
tree, and that is what got rendered: departments, then folders, then filenames. A board
built on that is a file browser with department headers, however it is drawn.

What a business actually has is a tree of *decisions available to it* — the shape Beam's
command center draws, where a leaf is a listing that exists or does not, carries revenue,
and has a next action. CTA's equivalents were already on disk and unread:

    clients/<client>/delivery.json        client -> phase -> task     (what is signed)
    data/scan-ledger/scans/*.jsonl        site -> check group -> check (what is broken)
    data/build-map.json                   industry -> stage -> piece   (what we can sell)
    data/approaches/*-notes.json          prospect -> finding          (why they would buy)

So this module loads those, and nothing here knows a business name: every path comes from
the profile's `work` block. An adapter is chosen by a declared `shape`, which is what
lets a second business point at its own files without an engine edit.
"""

import json
from pathlib import Path

SHAPES = ("client_delivery", "site_scan", "build_map", "prospect_notes")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_last_line(path):
    """A `.jsonl` run is an append-only event log; its last line is the current state.

    ⚠️ Reading one of these as `.json` is how 89 questionnaire runs once read as zero.
    """
    try:
        lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            return json.loads(ln)
        except json.JSONDecodeError:
            continue
    return None


def _expand(root, pattern):
    """Every file a profile pattern names, sorted. A pattern with no match yields
    nothing, and a department with nothing renders empty rather than filled."""
    root = Path(root)
    if any(ch in pattern for ch in "*?["):
        return sorted(root.glob(pattern))
    p = root / pattern
    return [p] if p.exists() else []


# ---------------------------------------------------------------------------------
# Adapters. Each returns plain dicts; building nodes is the view's job, not this one's.
# ---------------------------------------------------------------------------------

def _name_from_path(path):
    """clients/<slug>/delivery.json names the client by its folder; a flat file by its stem."""
    p = Path(path)
    return p.parent.name if p.stem == "delivery" else p.stem


def _client_delivery(path, rel):
    d = _read_json(path)
    if not isinstance(d, dict) or not d.get("phases"):
        return None
    phases = []
    for p in d["phases"]:
        tasks = [{
            "id": t.get("id"), "title": t.get("title"), "detail": t.get("detail") or "",
            "status": t.get("status"), "owner": t.get("owner"),
            "done_when": t.get("done_when") or "",
            "maps": list(t.get("maps") or []),
        } for t in (p.get("tasks") or [])]
        phases.append({"id": p.get("id"), "name": p.get("name"), "window": p.get("window"),
                       "intent": p.get("intent") or "", "tasks": tasks})
    return {
        "shape": "client_delivery", "source": rel,
        "client": d.get("client") or d.get("client_short") or _name_from_path(path),
        "short": d.get("client_short") or "",
        "contact": d.get("contact") or "", "day_to_day": d.get("day_to_day") or "",
        "fee": d.get("fee") or "", "ad_budget": d.get("ad_budget") or "",
        "start": d.get("start_date") or "", "end": d.get("end_date") or "",
        "engagement": d.get("engagement") or "",
        "deliverables": dict(d.get("deliverables") or {}),
        "ongoing": dict(d.get("ongoing") or {}),
        "phases": phases,
        "open_questions": list(d.get("open_questions") or []),
        "risks": list(d.get("risks") or []),
    }


def _site_scan(path, rel):
    d = _read_last_line(path)
    if not isinstance(d, dict) or not isinstance(d.get("grades"), dict):
        return None
    groups = []
    for gid, g in d["grades"].items():
        if not isinstance(g, dict):
            continue
        groups.append({"id": gid, "grade": g.get("grade"),
                       "passed": list(g.get("passed") or []),
                       "failed": list(g.get("failed") or []),
                       "skipped": list(g.get("skipped") or [])})
    checks = d.get("checks") if isinstance(d.get("checks"), dict) else {}
    return {
        "shape": "site_scan", "source": rel,
        "domain": checks.get("domain") or Path(path).stem.replace("-com", ".com"),
        "scanned_at": d.get("scanned_at") or checks.get("scanned") or "",
        "worst_finding": d.get("worst_finding") or "",
        "groups": groups,
    }


def _build_map(path, rel):
    d = _read_json(path)
    if not isinstance(d, dict) or not isinstance(d.get("branches"), dict):
        return None
    industries = []
    for iid, branch in d["branches"].items():
        stages = []
        for s in (branch.get("stages") or []):
            stages.append({
                "id": s.get("id"), "name": s.get("h"),
                "sub": list(s.get("sub") or []), "today": list(s.get("today") or []),
                "pieces": [{"kind": pc.get("kind"), "name": pc.get("h"),
                            "sub": pc.get("sub") or "", "gate": bool(pc.get("gate"))}
                           for pc in (s.get("pieces") or [])],
            })
        industries.append({"id": iid, "stages": stages})
    return {"shape": "build_map", "source": rel, "industries": industries,
            "captions": d.get("captions") or {}}


def _prospect_notes(path, rel):
    d = _read_json(path)
    findings = d if isinstance(d, list) else (d or {}).get("findings")
    if not isinstance(findings, list) or not findings:
        return None
    name = Path(path).stem.replace("-notes", "").replace("-", " ").title()
    return {"shape": "prospect_notes", "source": rel, "prospect": name,
            "findings": [str(f) for f in findings if isinstance(f, str)]}


ADAPTERS = {
    "client_delivery": _client_delivery,
    "site_scan": _site_scan,
    "build_map": _build_map,
    "prospect_notes": _prospect_notes,
}


def load(prof, root="."):
    """Everything the profile's `work` block names, grouped by department id.

    Returns {department_id: [item, ...]}. A source that names a missing file, or a file
    the adapter cannot make sense of, contributes nothing and does not raise — a board
    that will not render because one ledger moved is worse than a board with a gap in it,
    and the gap is visible.
    """
    out = {}
    for src in (prof.get("work") or {}).get("sources", []):
        shape, dept = src.get("shape"), src.get("department")
        adapter = ADAPTERS.get(shape)
        if not adapter or not dept:
            continue
        for path in _expand(root, src.get("path", "")):
            rel = str(path).replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]
            # Our own business is in the same registry as the prospects, because the
            # scanner does not care whose site it is. It is drawn as "our own presence"
            # instead, so the profile names it and it is skipped here.
            skip = set((prof.get("work") or {}).get("exclude_ids") or [])
            item = (adapter(path, rel, skip) if shape in ("prospects", "agent_manifest")
                    else adapter(path, rel))
            if not item:
                continue
            item["label"] = src.get("label") or ""
            if shape == "agent_manifest":
                # One manifest, EVERY department — including the ones with nothing in it.
                # ⚠️ A department with no agents is the most useful cell on the board: it
                # is the gap. Filing only to departments the manifest names made Delivery
                # draw no group at all, which reads as "not applicable" rather than
                # "nothing runs here yet", and those are opposite meanings.
                for d in [x["id"] for x in prof.get("departments", [])]:
                    out.setdefault(d, []).append(
                        {**item, "agents": [a for a in item["agents"] if a["department"] == d]})
            else:
                out.setdefault(dept, []).append(item)
    return out


def _prospects(path, rel, skip=()):
    """The discovered-business registry, joined to each one's latest graded scan.

    `data/scan-ledger/registry.json` is written by `auditor/webpresence.py --discover`,
    which walks Google Places for a query and a location and scans what it finds. Both
    halves already existed and nothing drew them, which is why prospecting lived in Sam's
    head instead of on a board.
    """
    reg = _read_json(path)
    if not isinstance(reg, dict) or not reg:
        return None
    root = Path(path).parent
    out = []
    for bid, r in sorted(reg.items()):
        if not isinstance(r, dict) or bid in skip:
            continue
        scan = _read_last_line(root / "scans" / f"{bid}.jsonl")
        grades, failed, passed, skipped = {}, [], [], []
        if isinstance(scan, dict) and isinstance(scan.get("grades"), dict):
            for gid, g in scan["grades"].items():
                if isinstance(g, dict):
                    grades[gid] = g.get("grade")
                    failed += [f"{gid}:{c}" for c in (g.get("failed") or [])]
                    # Carried so a sheet can open with what is WORKING. Without it the
                    # only honest sentence available is a list of what is broken, and a
                    # page that only attacks a stranger does not get read.
                    passed += [f"{gid}:{c}" for c in (g.get("passed") or [])]
                    skipped += [f"{gid}:{c}" for c in (g.get("skipped") or [])]
        out.append({
            "id": bid, "name": r.get("name") or bid, "domain": r.get("domain") or "",
            "url": r.get("url") or "", "first_seen": r.get("first_seen") or "",
            "last_scanned": r.get("last_scanned") or "",
            "consent": r.get("consent") or {},
            "grades": grades, "failed": failed, "passed": passed, "skipped": skipped,
            "worst_finding": (scan or {}).get("worst_finding") or "",
        })
    return {"shape": "prospects", "source": rel, "prospects": out}


ADAPTERS["prospects"] = _prospects
SHAPES = SHAPES + ("prospects",)


def _agent_manifest(path, rel, skip=()):
    """What actually runs for this business, and what is recommended next.

    ⚠️ The manifest exists so a card is never drawn for something that does not run. A
    department with no agents says so; it is not quietly filled. `status: built` requires
    a `source` that points at the file which makes it run.
    """
    d = _read_json(path)
    if not isinstance(d, dict) or not isinstance(d.get("agents"), list):
        return None
    out = []
    for a in d["agents"]:
        if not isinstance(a, dict) or not a.get("id") or not a.get("department"):
            continue
        out.append({
            "id": a["id"], "department": a["department"],
            "label": a.get("label") or a["id"], "lane": a.get("lane") or "",
            "kind": a.get("kind") or "automation", "status": a.get("status") or "recommended",
            "autonomy": a.get("autonomy"), "trigger": a.get("trigger") or "",
            "origin": a.get("source") or rel,
            "can_do": list(a.get("can_do") or []),
            "must_ask": list(a.get("must_ask") or []),
            "never_touch": list(a.get("never_touch") or []),
            "confidence": a.get("confidence"), "rationale": a.get("rationale") or "",
            "mistake_twice": a.get("mistake_twice"), "note": a.get("note") or "",
        })
    return {"shape": "agent_manifest", "source": rel, "agents": out,
            "scale": d.get("autonomy_scale") or {}, "manifest_note": d.get("note") or ""}


ADAPTERS["agent_manifest"] = _agent_manifest
SHAPES = SHAPES + ("agent_manifest",)
