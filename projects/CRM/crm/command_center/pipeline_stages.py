"""The business's own pipeline stages, edited on the screen (EMAC loan officer review, R1:
"every business names its own pipeline", Sam 2026-09-26).

The profile's `pipeline.stages` is the starting list. The first edit copies it into the
database (table `pipeline_stages`), and from then on the database is the list: a deployed CRM
runs from a read-only release, so the screen can never write the profile file.

Rules:
- Rename, reorder, add and remove. At least one open stage.
- The won and the lost stage keep their meaning (the leaderboard credits wins by it). They can
  be renamed and moved, never removed.
- A stage the profile's Research rules name (`crm.research.claim_stage`, `outcome_stages`) is
  never removed, or taken leads would land nowhere. Rename it instead.
- Removing a stage that holds cards needs a stage to move them to. The move and the new list
  commit together, with one audit line.
- More than one pipeline (Portfolio CRM P3, `pipeline.pipelines` in the profile): each stage
  belongs to one pipeline, and stage ids are unique across the business, so a deal's stage says
  which pipeline it is in and the deals table does not change. Every pipeline keeps an open stage.
  The pipelines themselves (ids, names) come from the profile; the screen edits their stages.
"""
import re

from . import crm_store

DDL = """
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, position INTEGER NOT NULL,
    outcome TEXT NOT NULL DEFAULT '' CHECK(outcome IN ('','won','lost')),
    pipeline TEXT NOT NULL DEFAULT '');
"""
MAIN = {"id": "main", "label": "Pipeline"}


def migrate(db):
    db.executescript(DDL)
    if "pipeline" not in {r[1] for r in db.execute("PRAGMA table_info(pipeline_stages)")}:
        db.execute("ALTER TABLE pipeline_stages ADD COLUMN pipeline TEXT NOT NULL DEFAULT ''")


def pipelines(prof):
    """[{id, label}] in order. One unnamed pipeline when the profile lists none."""
    lines = (prof.get("pipeline") or {}).get("pipelines")
    return [{"id": p["id"], "label": p["label"]} for p in lines] if lines else [dict(MAIN)]


def _line(prof, value):
    ids = [p["id"] for p in pipelines(prof)]
    return value if value in ids else ids[0]


def _profile_rows(prof):
    return [{"id": s["id"], "label": s.get("label", s["id"]), "outcome": s.get("outcome", ""),
             "terminal": bool(s.get("terminal")) or s.get("outcome") in ("won", "lost"),
             "pipeline": _line(prof, s.get("pipeline"))}
            for s in (prof.get("pipeline") or {}).get("stages", [])]


def current(db, prof):
    """The stages in order. The database list once someone has edited it, else the profile's."""
    rows = [dict(r) for r in db.execute("SELECT * FROM pipeline_stages ORDER BY position")]
    if not rows:
        return _profile_rows(prof)
    return [{"id": r["id"], "label": r["label"], "outcome": r["outcome"],
             "terminal": r["outcome"] in ("won", "lost"), "pipeline": _line(prof, r["pipeline"])} for r in rows]


def revision(db):
    row = db.execute("SELECT value FROM metadata WHERE key='stages_revision'").fetchone()
    return int(row[0]) if row else 0


def _protected(prof):
    spec = (prof.get("crm") or {}).get("research") or {}
    named = {spec.get("claim_stage")} | set((spec.get("outcome_stages") or {}).values())
    return {s for s in named if isinstance(s, str)}


def _slug(label, taken):
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30] or "stage"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def change(store, db, prof, person, data, stamp):
    """pipeline.stages: the whole new list, plus where the cards of a removed stage go."""
    CRMError = crm_store.CRMError
    if set(data) - {"stages", "moves", "revision"}:
        raise CRMError("Unexpected fields in command")
    if type(data.get("revision")) is not int or data["revision"] != revision(db):
        raise CRMError("Someone changed the stages. Refresh before saving again.", 409)
    rows, moves = data.get("stages"), data.get("moves") or {}
    lines = pipelines(prof)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20 * len(lines) or not isinstance(moves, dict):
        raise CRMError("Send between 1 and 20 stages per pipeline")
    old = {s["id"]: s for s in current(db, prof)}
    new, labels = [], set()
    for r in rows:
        if not isinstance(r, dict) or set(r) - {"id", "label", "pipeline"}:
            raise CRMError("Each stage needs a name")
        label = crm_store.text(r.get("label"), "Stage name", 40, True)
        if label.casefold() in labels:
            raise CRMError(f"Two stages are called {label}")
        labels.add(label.casefold())
        sid = r.get("id") or ""
        if sid and sid not in old:
            raise CRMError("Unknown stage")
        sid = sid or _slug(label, set(old) | {s["id"] for s in new})
        line = r.get("pipeline", old.get(sid, {}).get("pipeline") or lines[0]["id"])
        if line not in {p["id"] for p in lines}:
            raise CRMError("Unknown pipeline")
        new.append({"id": sid, "label": label, "outcome": old.get(sid, {}).get("outcome", ""), "pipeline": line})
    kept = {s["id"] for s in new}
    if len(kept) != len(new):
        raise CRMError("A stage is listed twice")
    gone = [sid for sid in old if sid not in kept]
    for sid in gone:
        if old[sid]["outcome"]:
            raise CRMError(f"{old[sid]['label']} marks deals as {old[sid]['outcome']}. Rename it, never remove it.")
        if sid in _protected(prof):
            raise CRMError(f"Research puts leads in {old[sid]['label']}. Rename it instead of removing it.")
    for p in lines:
        if not any(not s["outcome"] and s["pipeline"] == p["id"] for s in new):
            raise CRMError("Keep at least one open stage" + (f" in {p['label']}" if len(lines) > 1 else ""))
    for sid in gone:
        held = db.execute("SELECT count(*) FROM deals WHERE stage=? AND deleted_at IS NULL", (sid,)).fetchone()[0]
        target = moves.get(sid)
        if not held:  # only deleted cards (or none): they follow to the first open stage
            target = target if target in kept else next(s["id"] for s in new if not s["outcome"])
        elif target not in kept or next(s for s in new if s["id"] == target)["outcome"]:
            raise CRMError(f"Choose an open stage for the {held} card(s) in {old[sid]['label']}")
        db.execute("UPDATE deals SET stage=?,revision=revision+1,updated_at=? WHERE stage=?",
                   (target, stamp, sid))
    db.execute("DELETE FROM pipeline_stages")
    db.executemany("INSERT INTO pipeline_stages (id,label,position,outcome,pipeline) VALUES (?,?,?,?,?)",
                   [(s["id"], s["label"], n, s["outcome"], s["pipeline"]) for n, s in enumerate(new)])
    db.execute("INSERT INTO metadata VALUES ('stages_revision', ?) ON CONFLICT(key) DO UPDATE "
               "SET value=excluded.value", (str(revision(db) + 1),))
    return "pipeline"
