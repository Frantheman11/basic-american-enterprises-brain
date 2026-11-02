"""The Board — the business drawn as the decisions available to it.

⚠️ **This is the view the other six were mistaken for.** They answer *what is on disk*.
This one answers *what is happening, what is stuck, and what to do next* — which is the
question a command center exists for.

The shape is Beam's, because Beam's is proved: a spine of roots down the left, one of them
expanded into a left-to-right tree on a canvas, money and status on the nodes themselves,
AI recommendations drawn as ghost branches on the same tree rather than in a separate list,
and a side panel whose last element is an action.

What changes here is only the content of the tree. Beam's is
`product -> variation -> audience/listing`. CTA's spine is its **departments** (Sam's call,
2026-09-21), and under each one is that department's real work:

    Delivery    client   -> phase       -> task          (from the signed delivery plan)
    Marketing   our site -> check group -> check         (from the live scan ledger)
    Sales       prospect -> finding                      (from the approach notes)
    Research    industry -> stage       -> piece         (from the build map)
    Brain       the index that already exists

⭐ Nothing here opens a file. `command_center/work.py` loads the trees and the profile says
where they are, so this module has no path in it and no business name — which is what lets
the same board draw a client's business from their own repo.
"""

from ..schema import ghost, node

ID = "board"
TITLE = "Board"
BLURB = "The business at a glance: what is running, what is stuck, and what is recommended next."

# A task/check state -> a node status in the closed vocabulary. Anything unrecognised
# stays `ready` rather than guessing, because a wrong status is a wrong colour.
TASK_STATUS = {"done": "done", "complete": "done", "in_progress": "in_progress",
               "doing": "in_progress", "blocked": "blocked", "todo": "ready",
               "ready": "ready", "waiting": "blocked"}

GRADE_STATUS = {"A": "done", "B": "done", "C": "in_progress", "D": "blocked", "F": "blocked"}

# A build-map piece kind -> what it is in the node vocabulary.
PIECE_KIND = {"agent": "agent", "automation": "workflow", "quick-win": "task"}


def _clean(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _slug(path):
    """A node id fragment from a source path. Ids must be unique across a tree and a
    child must be prefixed by its parent — both are validator rules, not conventions."""
    stem = str(path).replace("\\", "/").rsplit("/", 1)[-1].split(".")[0]
    return "".join(c if c.isalnum() else "-" for c in stem).strip("-").lower() or "item"


# ---------------------------------------------------------------------------------
# Delivery — the signed work
# ---------------------------------------------------------------------------------

def _delivery(item, nid):
    src = item["source"]
    phases = []
    done = total = blocked = 0
    for p in item["phases"]:
        pid = f"{nid}.{p['id']}"
        kids = []
        for t in p["tasks"]:
            st = TASK_STATUS.get(t["status"], "ready")
            total += 1
            done += st == "done"
            blocked += st == "blocked"
            kids.append(node(
                f"{pid}.{(t['id'] or '').replace('.', '-')}", t["title"], "task",
                status=st, source=src,
                badge=t["owner"] or "", meta=t["id"] or "",
                detail=_clean({
                    "summary": t["detail"],
                    "fields": _clean({"owner": t["owner"], "done when": t["done_when"],
                                      "delivers": ", ".join(t["maps"])}),
                })))
        pdone = sum(1 for k in kids if k["status"] == "done")
        pst = ("done" if kids and pdone == len(kids)
               else "blocked" if any(k["status"] == "blocked" for k in kids)
               else "in_progress" if pdone else "ready")
        phases.append(node(
            pid, p["name"], "section", status=pst, source=src,
            meta=p["window"] or "", badge=f"{pdone}/{len(kids)}",
            detail=_clean({"summary": p["intent"],
                          "fields": _clean({"window": p["window"]})}),
            children=kids))

    # Open questions and risks are already written down as things needing a decision.
    # They belong ON the tree, not in a list somewhere else — but as two GROUPS, not as
    # ten more siblings of the phases. Seventeen rows at the top level is a wall; nine
    # rows where two of them say "5 waiting on you" is a board.
    qs = [node(f"{nid}.questions.{i}", q.get("q") or "Open question", "task",
               status="blocked", source=src, badge=q.get("owner") or "",
               meta="needs an answer",
               detail=_clean({"summary": q.get("detail") or "",
                              "fields": _clean({"owner": q.get("owner")})}))
          for i, q in enumerate(item["open_questions"])]
    if qs:
        phases.append(node(f"{nid}.questions", "Needs a decision from you", "section",
                           status="blocked", source=src, badge=str(len(qs)),
                           meta="nobody can start these without you",
                           children=qs))

    rs = [ghost(f"{nid}.risks.{i}", r.get("risk") or "Risk", "task", 0.5,
                r.get("impact") or "", [src], src, meta="risk",
                detail=_clean({"summary": r.get("impact") or "",
                               "fields": _clean({"mitigation": r.get("mitigation")})}))
          for i, r in enumerate(item["risks"])]
    if rs:
        phases.append(node(f"{nid}.risks", "Risks worth watching", "section",
                           status="ready", source=src, badge=str(len(rs)),
                           meta="each one has a written mitigation",
                           children=rs))

    window = " → ".join(x for x in (item["start"], item["end"]) if x)
    return node(
        nid, item["client"], "record", status="active", source=src,
        money=item["fee"], meta=window, badge=item["label"] or "client",
        detail=_clean({
            "summary": item["engagement"],
            "fields": _clean({"fee": item["fee"], "ad budget": item["ad_budget"],
                              "contact": item["contact"], "day to day": item["day_to_day"],
                              "window": window,
                              "tasks": f"{done} done · {total - done} open"
                                       + (f" · {blocked} blocked" if blocked else "")}),
            "bullets": [f"{k} — {v}" for k, v in item["deliverables"].items()],
        }),
        children=phases)


# ---------------------------------------------------------------------------------
# Marketing — prospects, banded by which offer fits them
# ---------------------------------------------------------------------------------

WEAK = ("C", "D", "F")
PRESENCE = ("website", "seo_basics", "lead_capture")


def _band(p):
    """Which offer fits, and the evidence for saying so.

    ⚠️ A band is a computed OPINION, not a measurement, so it returns its reasons and a
    confidence and the view labels it as one. The profile lists only bands this data can
    actually support: "too small" and "out of area" are not computable from a scan, so
    they are absent rather than guessed.
    """
    g = p["grades"]
    if not g:
        return "look", 0.0, ["nothing scanned yet"]
    weak = [k for k in PRESENCE if g.get(k) in WEAK]
    if weak:
        why = [f"{k.replace('_', ' ')} graded {g[k]}" for k in weak]
        return "foundation", min(0.55 + 0.15 * len(weak), 0.95), why
    strong = [f"{k.replace('_', ' ')} {g[k]}" for k in PRESENCE if g.get(k)]
    return "build", 0.6, strong


def _prospects(item, nid, bands):
    src = item["source"]
    groups = {}
    for p in item["prospects"]:
        bid, conf, why = _band(p)
        spec = bands.get(bid, {})
        gid = f"{nid}.{bid}"
        grades = " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in p["grades"].items() if v)
        # ⚠️ `ready`, never `blocked`. A prospect whose site grades D is an OPPORTUNITY,
        # not a blockage — nothing about them is stuck waiting on us. Marking the band
        # red took the board's "stuck" count from 9 to 48 and made the one number that
        # means "go and unblock this" mean "we found some leads".
        groups.setdefault(bid, []).append(node(
            f"{gid}.{p['id']}", p["name"], "record", status="ready", source=src,
            meta=p["domain"], badge=f"{len(p['failed'])} failing" if p["failed"] else "",
            confidence=round(conf, 2),
            detail=_clean({
                "summary": p["worst_finding"],
                "fields": _clean({
                    "site": p["url"], "last scanned": p["last_scanned"],
                    "grades": grades,
                    "why this band": "; ".join(why),
                    "confidence": f"{int(conf * 100)}%" if conf else "not scored",
                }),
                "bullets": [f.replace(":", " — ").replace("_", " ") for f in p["failed"][:10]],
            })))

    out = []
    for spec in bands_order(bands):
        kids = groups.get(spec["id"])
        if not kids:
            continue
        out.append(node(
            f"{nid}.{spec['id']}", spec.get("label") or spec["id"], "section",
            status="ready", source=src,
            meta=spec.get("offer", ""), badge=str(len(kids)),
            detail=_clean({"summary": spec.get("why", ""),
                           "fields": _clean({"banded when": spec.get("when", ""),
                                             "the offer": spec.get("offer", "")})}),
            children=kids))
    return node(
        nid, "Prospects in the area", "section", status="active", source=src,
        meta=f"{len(item['prospects'])} discovered and scanned",
        badge=item["label"] or "prospects",
        detail=_clean({
            "summary": ("Found with the Places API and scanned by the web-presence engine. "
                        "Banding is computed from the scan, and every band carries its "
                        "reasons."),
            "fields": {"registry": src}}),
        children=out)


def bands_order(bands):
    return [b for b in bands.values() if isinstance(b, dict) and b.get("id")]


# ---------------------------------------------------------------------------------
# Marketing — our own site, as the scanner actually found it
# ---------------------------------------------------------------------------------

def _our_site(item, nid, recommend):
    src = item["source"]
    groups, failed_all = [], []
    for g in item["groups"]:
        gid = f"{nid}.{g['id']}"
        kids = []
        for c in g["failed"]:
            failed_all.append(c)
            kids.append(node(f"{gid}.{c}", c.replace("_", " "), "metric",
                             status="blocked", source=src, meta="failing"))
        for c in g["passed"]:
            kids.append(node(f"{gid}.{c}", c.replace("_", " "), "metric",
                             status="done", source=src, meta="passing"))
        for c in g["skipped"]:
            kids.append(node(f"{gid}.{c}", c.replace("_", " "), "metric",
                             status="locked", source=src, meta="not checked"))
        groups.append(node(
            gid, g["id"].replace("_", " "), "section",
            status=GRADE_STATUS.get(g["grade"], "ready"), source=src,
            badge=g["grade"] or "", meta=f"{len(g['failed'])} failing",
            children=kids))

    # ⭐ The computed recommendation, and the whole point of a ghost branch: a check that
    # is failing here, matched to the thing that closes it. The mapping is DATA in the
    # profile, never a branch in the engine — a client's board recommends from their own.
    for c in failed_all:
        rec = recommend.get(c)
        if not rec:
            continue
        groups.append(ghost(
            f"{nid}.rec-{c}", rec.get("build") or c, PIECE_KIND.get(rec.get("kind"), "task"),
            rec.get("confidence", 0.6), rec.get("why") or "", [src], src,
            meta="recommended", badge="closes " + c.replace("_", " ")))

    return node(
        nid, item["domain"], "system", status="active", source=src,
        meta=f"scanned {item['scanned_at'][:10]}" if item["scanned_at"] else "",
        badge=item["label"] or "site",
        detail=_clean({"summary": item["worst_finding"],
                      "fields": _clean({"failing checks": str(len(failed_all)),
                                        "scanned": item["scanned_at"]})}),
        children=groups)


# ---------------------------------------------------------------------------------
# Sales — what we found in a prospect's business
# ---------------------------------------------------------------------------------

def _prospect(item, nid):
    src = item["source"]
    kids = [node(f"{nid}.f{i}", f.split(" - ")[0][:70], "task", status="ready", source=src,
                 meta="finding", detail={"summary": f})
            for i, f in enumerate(item["findings"])]
    return node(nid, item["prospect"], "record", status="in_progress", source=src,
                badge=item["label"] or "prospect",
                meta=f"{len(kids)} findings",
                detail={"summary": f"{len(kids)} findings from the approach research."},
                children=kids)


# ---------------------------------------------------------------------------------
# Research — the industry opportunity maps, which are what we sell
# ---------------------------------------------------------------------------------

def _build_map(item, nid):
    src, out = item["source"], []
    for ind in item["industries"]:
        iid = f"{nid}.{ind['id']}"
        stages = []
        for s in ind["stages"]:
            sid = f"{iid}.{s['id']}"
            pieces = [node(f"{sid}.p{j}", pc["name"], PIECE_KIND.get(pc["kind"], "task"),
                           status="locked" if pc["gate"] else "ready", source=src,
                           meta=pc["kind"], badge="gated" if pc["gate"] else "",
                           detail={"summary": pc["sub"]})
                      for j, pc in enumerate(s["pieces"])]
            stages.append(node(sid, s["name"], "section", status="ready", source=src,
                               meta=f"{len(pieces)} pieces",
                               detail=_clean({"summary": " · ".join(s["sub"]),
                                             "bullets": s["today"]}),
                               children=pieces))
        out.append(node(iid, ind["id"].replace("-", " ").title(), "workflow",
                        status="ready", source=src, meta=f"{len(stages)} stages",
                        badge=item["label"] or "", children=stages))
    return out


# ---------------------------------------------------------------------------------

BUILDERS = {"client_delivery": _delivery, "prospect_notes": _prospect}


# ---------------------------------------------------------------------------------
# Agents — what runs in this department, and what should
# ---------------------------------------------------------------------------------

# A card's node kind. A guardrail is not an agent and is not drawn as one: it refuses
# things, which is the opposite job.
AGENT_KIND = {"agent": "agent", "automation": "workflow", "guardrail": "system"}


def _agent_card(a, nid, scale):
    built = a["status"] == "built"
    lvl = a.get("autonomy")
    bits = [a["kind"]]
    if lvl:
        bits.append(lvl)
    ghosted = a["status"] == "recommended"
    fields = _clean({
        "the one job": a["lane"],
        "kind": a["kind"] + (" — rules can be written in advance" if a["kind"] == "automation"
                             else " — the path changes every run" if a["kind"] == "agent" else ""),
        "autonomy": f"{lvl} — {scale.get(lvl, '')}" if lvl else "",
        "trigger": a["trigger"],
        # ⚠️ The three buckets, from the knowledge base's reversible/irreversible rule.
        # They are a NAMED ARTIFACT on purpose — §10.5 says put this table in every
        # proposal, because it is the fastest way to get a nervous owner to sign.
        "can do on its own": "; ".join(a["can_do"]),
        "must ask first": "; ".join(a["must_ask"]) or "nothing",
        "never touches": "; ".join(a["never_touch"]),
        # §10.11: the one metric worth reporting on a retainer.
        "makes the same mistake twice?": ("not measured yet" if built and a["mistake_twice"] is None
                                          else ("yes" if a["mistake_twice"] else "no") if built else ""),
    })
    if ghosted:
        return ghost(f"{nid}.{a['id']}", a["label"], AGENT_KIND.get(a["kind"], "workflow"),
                     a.get("confidence") or 0.5, a["rationale"] or a["lane"],
                     [a["origin"]], a["origin"],
                     meta="recommended, not built", badge=a["kind"],
                     detail=_clean({"summary": a["rationale"] or a["lane"], "fields": fields}))
    return node(
        f"{nid}.{a['id']}", a["label"], AGENT_KIND.get(a["kind"], "workflow"),
        status="active" if built else "locked", source=a["origin"],
        meta=a["trigger"], badge=" · ".join(bits),
        # The note only. The lane already has its own labelled field, and printing it
        # twice on one panel wastes the space where the note would have gone.
        detail=_clean({"summary": a["note"], "fields": fields}))


def _agents(item, nid):
    """One group per department, holding what runs there and what is recommended.

    ⭐ This is also the retainer conversation drawn: here is your department, here are its
    two automations, here are the three we would build next and why.
    """
    src = item["source"]
    scale = item.get("scale") or {}
    live = [a for a in item["agents"] if a["status"] == "built"]
    recs = [a for a in item["agents"] if a["status"] == "recommended"]
    kids = [_agent_card(a, nid, scale) for a in live + recs]
    # ⭐ A department with nothing running is not an empty row, it is a finding. It says
    # so in words rather than drawing a blank and letting the reader assume it is fine.
    empty = not live and not recs
    return node(
        nid, "Agents and automations", "section",
        status="active" if live else "blocked" if not live and recs else "ready",
        source=src,
        # The badge counts; the meta explains. Both saying "nothing runs here" was one
        # fact printed twice on one row, which wastes the only line read before expanding.
        badge=f"{len(live)} running" if live else "0 built",
        meta=("nothing runs here yet, and nothing is recommended" if empty
              else "nothing runs here yet" if not live
              else f"{len(live)} built · {len(recs)} recommended" if recs
              else f"{len(live)} built"),
        detail=_clean({
            "summary": ("What actually runs in this department, and what the knowledge base "
                        "says to build next. A card is only 'built' when a file behind it "
                        "makes it run."
                        + ("  Nothing runs in this department at all — which is the finding."
                           if not live else "")),
            "fields": _clean({"built": str(len(live)), "recommended": str(len(recs))}),
        }),
        children=kids)


# ---------------------------------------------------------------------------------
# Brain — what the business knows
# ---------------------------------------------------------------------------------

# What each index section's rows ARE, so a row is drawn as itself and not as a filename.
BRAIN_KIND = {"skills": "skill", "memory": "memory", "knowledge": "knowledge",
              "tools": "tool", "workflows": "workflow"}


def _brain_row(section, r, rid):
    """One indexed thing, with the fact about it that is worth seeing before you click."""
    src = r.get("source") or ""
    if section == "skills":
        return node(rid, r.get("name", "?"), "skill",
                    status="locked" if r.get("locked") else "ready", source=src,
                    meta=r.get("kind") or "no kind declared",
                    badge="vendored" if r.get("locked") else "",
                    detail=_clean({"summary": r.get("description") or "",
                                   "fields": _clean({"kind": r.get("kind"),
                                                     "why that kind": r.get("kind_why")})}))
    if section == "memory":
        links = r.get("links") or []
        opens = r.get("open_items") or 0
        # ⚠️ A note carrying 🔴 marks is not the BUSINESS being stuck. 27 of them are
        # historical open calls inside written-up decisions, and marking them blocked put
        # 27 into a count whose whole job is to say "go and unblock this today". The
        # badge still surfaces them; the status does not overstate them.
        return node(rid, r.get("name", "?"), "memory", status="ready", source=src,
                    meta=r.get("type") or "no type",
                    badge=(f"{opens} open" if opens else (f"{len(links)} links" if links else "")),
                    detail=_clean({"summary": r.get("description") or "",
                                   "fields": _clean({"type": r.get("type"),
                                                     "links out": str(len(links)) if links else "",
                                                     "open calls": str(opens) if opens else "",
                                                     "in the index": "yes" if r.get("in_index") else "NO"})}))
    if section == "tools":
        return node(rid, r.get("name", "?"), "tool", status="ready", source=src,
                    meta=r.get("folder") or "",
                    badge="" if r.get("in_routing_table") else "unrouted",
                    detail=_clean({"summary": r.get("summary") or "",
                                   "fields": _clean({
                                       "lines": str(r.get("lines") or ""),
                                       "in the routing table": "yes" if r.get("in_routing_table") else "NO",
                                       "in SCRIPTS.md": "yes" if r.get("in_scripts_index") else "NO"})}))
    heads = r.get("headings") or r.get("sections") or []
    return node(rid, r.get("name", "?"), BRAIN_KIND.get(section, "doc"),
                status="ready", source=src,
                meta=f"{len(heads)} sections" if heads else "",
                detail=_clean({"bullets": [str(h) for h in heads[:12]]}))


def _brain(idx, prof, did):
    """The index, drawn as what it holds.

    ⚠️ These sections used to be built with NO children and a `ready` status, so clicking
    Skills or Memory opened a panel that said "not started" over an empty tree — for an
    index holding 47 notes. A section that is full does not describe itself as unstarted,
    and a row that exists is worth drawing.
    """
    out = []
    for name, rows in (idx.get("sections") or {}).items():
        if not rows:
            continue
        sid = f"{did}.{name}"
        kids = [_brain_row(name, r, f"{sid}.{i}") for i, r in enumerate(rows)]
        stuck = sum(1 for k in kids if k["status"] == "blocked")
        out.append(node(
            sid, name.title(), "section", status="active",
            source=(prof.get("brain") or {}).get(f"{name}_path") or prof["_path"],
            meta=f"{len(rows)} indexed" + (f" · {stuck} with open calls" if stuck else ""),
            badge=str(len(rows)), children=kids))
    return out


def build(ctx):
    prof, w = ctx["profile"], ctx.get("work") or {}
    recommend = (prof.get("work") or {}).get("recommend") or {}
    fit = (prof.get("work") or {}).get("fit") or {}
    band_specs = {b["id"]: b for b in (fit.get("bands") or []) if isinstance(b, dict) and b.get("id")}
    idx = ctx.get("brain_index") or {}
    nodes, live, stuck, recs = [], 0, 0, 0

    for d in prof["departments"]:
        did = f"board.{d['id']}"
        kids = []
        for item in w.get(d["id"], []):
            shape = item["shape"]
            # The item's own id is derived from its source file, so two clients in one
            # department cannot collide — the validator rejects a duplicate id outright,
            # and it caught exactly that the first time this ran.
            iid = f"{did}.{_slug(item['source'])}"
            if shape == "build_map":
                kids += _build_map(item, iid)
            elif shape == "agent_manifest":
                kids.append(_agents(item, f"{did}.agents"))
            elif shape == "prospects":
                kids.append(_prospects(item, iid, band_specs))
            elif shape == "site_scan":
                kids.append(_our_site(item, iid, recommend))
            elif shape in BUILDERS:
                kids.append(BUILDERS[shape](item, iid))

        # The brain is a lens, not a bucket: it draws the index the other views already
        # build rather than a second copy of it.
        if d.get("cross_cutting") and idx:
            kids += _brain(idx, prof, did)

        nodes.append(node(
            did, d["label"], "department",
            status="active" if kids else "ready", source=prof["_path"],
            meta=d.get("plain_english", ""), badge=str(len(kids)) if kids else "",
            detail=_clean({"summary": d.get("plain_english", ""),
                          "fields": _clean({"owns": d.get("owns", "")})}),
            children=kids))

    for n in nodes:
        for k in _walk(n):
            if k["status"] in ("active", "in_progress"):
                live += 1
            elif k["status"] == "blocked":
                stuck += 1
            elif k["status"] == "ghost":
                recs += 1

    notes = []
    # Keyed on the WORK, not on whether anything drew. The brain lens always has children
    # from the index, so "does any node have children" was answering a different question
    # and an unconfigured board looked configured.
    if not w:
        notes.append("No work sources are configured, so this board is drawing only what "
                     "the brain index already knew. The profile's `work` block is what "
                     "points a board at a business's own trees.")
    return {
        "nodes": nodes,
        "canvas": True,          # page.py renders a canvas shell instead of a row list
        "stats": [("departments", len(nodes), prof["_path"]),
                  ("running", live, "computed from the work sources"),
                  ("stuck", stuck, "computed from the work sources"),
                  ("recommended", recs, "computed — ghost branches")],
        "notes": notes,
    }


def _walk(n):
    yield n
    for k in n.get("children") or []:
        yield from _walk(k)
