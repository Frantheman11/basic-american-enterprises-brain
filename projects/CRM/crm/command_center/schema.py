"""The node grammar — one recursive shape, one closed vocabulary, one validator.

Sam's "levels within levels" is not many shapes. It is ONE shape rendered by one
renderer, to any depth:

    {"id": "sales.outbound.approach-atc", "label": "ATC approach", "kind": "task",
     "status": "in_progress", "source": "data/approaches/atc.md", "children": [...]}

Four rules make it hold together, and each one is enforced below rather than remembered:

  1. `kind` is CLOSED. Adding a kind is a schema change with a test, not an ad-hoc string.
  2. `status` (what a node IS) and `queue_status` (where it is in the pipeline) are
     SEPARATE fields. Beam merged them once and had to unpick it. Do not merge them again.
  3. Every node carries `source` — the file the fact came from. A node with no source is
     a bug, because it means a number was typed instead of derived. This is the mechanical
     form of the repo's "verified at source" rule.
  4. Ids are unique across a tree and a child's id is prefixed by its parent's. That is
     what makes a deep link (`#sales.outbound`) possible without a lookup table.

The build brief's sample put `source` inside `detail`. It is top level here on purpose:
a required field buried in an optional sub-object cannot be validated cheaply, and the
prose rule ("every node carries source") is the one that matters.
"""

# ---------------------------------------------------------------------------------
# Closed vocabularies. Adding a member is a deliberate schema change — the test that
# pins these counts is in tests/test_command_center.py.
# ---------------------------------------------------------------------------------

KINDS = (
    "department",   # a room in the business — comes from the profile, never hardcoded
    "system",       # a built thing that runs
    "workflow",     # the unit of sale: a manifest
    "agent",        # something that acts
    "skill",        # .claude/skills/<name>/SKILL.md
    "memory",       # memory/<name>.md
    "knowledge",    # knowledge/<name>.md — what we learned from outside
    "tool",         # a script an operator runs
    "record",       # lead | customer | conversation | run
    "artifact",     # an interactive artifact, filed to a department
    "task",         # a piece of work with an owner
    "metric",       # a number. Never authored — always derived, always sourced
    "section",      # a grouping node. Its source is the directory it walked
    "doc",          # a written document under docs/ or brand/
    # Added 2026-09-21. `tool` above says "a script an operator runs", and the business
    # view was calling every non-.md file one — so 176 PNGs, 5 WAVs and an MP4 counted as
    # tools, and Marketing's headline read "95 tools" when it owns almost none. Of 515
    # files typed `tool`, 90 were scripts. A number that is 83% wrong is worse than absent.
    "asset",        # an image, video, audio or stylesheet — rendered or shown, never run
    "data",         # .json/.jsonl/.csv — what a tool reads and writes, not a tool
)

STATUSES = (
    "active",       # running now
    "in_progress",  # started, not finished
    "ready",        # could start
    "blocked",      # waiting on something named
    "ghost",        # RECOMMENDED but not built — dimmed, carries a rationale
    "locked",       # gated behind an earlier phase
    "done",
)

# Where a node sits in the pipeline. Separate from `status` on purpose (rule 2).
QUEUE_STATUSES = (
    "unqueued",      # not in any queue — the default, and most nodes
    "queued",
    "running",
    "waiting_human",  # the approvals queue. This is the durable "what is waiting on you"
    "complete",
)

BADGES = (
    "blocked", "new", "stale", "unsourced", "recommended", "needs_review",
    "client", "engine", "profile", "house", "vendored", "unknown_kind",
)

REQUIRED = ("id", "label", "kind", "status", "source")

# A ghost node claims something should exist. It has to say why, how sure, and from what.
GHOST_REQUIRED = ("confidence", "rationale", "evidence")


class SchemaError(ValueError):
    """A node, tree or profile that the renderer must not be asked to draw."""


def node(id, label, kind, status="ready", source=None, **extra):
    """Build a node with the required fields present. Raises rather than emitting a
    half-node, because a half-node fails later, in the renderer, where it is harder to
    read."""
    n = {"id": id, "label": label, "kind": kind, "status": status, "source": source}
    n.update(extra)
    n.setdefault("children", [])
    validate_node(n)
    return n


def ghost(id, label, kind, confidence, rationale, evidence, source, **extra):
    """A recommendation, rendered dimmed. Beam's proven pattern — do not invent a second
    affordance for 'the system thinks you should build this'.

    `confidence` 0..1 · `rationale` one sentence a human can argue with ·
    `evidence` the files the recommendation was computed from.
    """
    return node(id, label, kind, status="ghost", source=source,
                confidence=round(float(confidence), 2), rationale=rationale,
                evidence=list(evidence), badges=["recommended"], **extra)


def validate_node(n, path="<root>"):
    if not isinstance(n, dict):
        raise SchemaError(f"{path}: node is {type(n).__name__}, not an object")
    for field in REQUIRED:
        if field not in n:
            raise SchemaError(f"{path}: missing required field '{field}'")
    if n["kind"] not in KINDS:
        raise SchemaError(f"{path}: unknown kind {n['kind']!r} — kinds are closed, see schema.KINDS")
    if n["status"] not in STATUSES:
        raise SchemaError(f"{path}: unknown status {n['status']!r} — see schema.STATUSES")
    if n.get("queue_status") is not None and n["queue_status"] not in QUEUE_STATUSES:
        raise SchemaError(f"{path}: unknown queue_status {n['queue_status']!r}")
    if not n.get("source"):
        raise SchemaError(f"{path}: no source. A node with no source means a fact was typed, "
                          "not derived — see schema module docstring rule 3")
    for b in n.get("badges") or []:
        if b not in BADGES:
            raise SchemaError(f"{path}: unknown badge {b!r}")
    if n["status"] == "ghost":
        for field in GHOST_REQUIRED:
            if field not in n:
                raise SchemaError(f"{path}: a ghost node must carry '{field}' — a recommendation "
                                  "with no rationale is a guess wearing a badge")
        if not 0.0 <= n["confidence"] <= 1.0:
            raise SchemaError(f"{path}: confidence {n['confidence']} outside 0..1")
    if not isinstance(n.get("children", []), list):
        raise SchemaError(f"{path}: children is not a list")
    return n


def validate_tree(root, seen=None, parent_id=None):
    """Validate a node and everything under it. Returns the node count.

    Two whole-tree rules the per-node check cannot see: ids are unique, and a child's id
    starts with its parent's id + '.' so a deep link resolves without a lookup table.
    """
    seen = {} if seen is None else seen
    validate_node(root, root.get("id", "<no id>"))
    nid = root["id"]
    if nid in seen:
        raise SchemaError(f"duplicate id {nid!r} — ids are unique across a tree")
    seen[nid] = True
    if parent_id is not None and not nid.startswith(parent_id + "."):
        raise SchemaError(f"{nid!r} is a child of {parent_id!r} but is not prefixed by it")
    count = 1
    for child in root.get("children") or []:
        count += validate_tree(child, seen, nid)
    return count


def walk(root):
    """Every node under and including `root`, depth first."""
    yield root
    for child in root.get("children") or []:
        yield from walk(child)


def count_by(root, field):
    """{value: n} over a tree — how the summary lines on a collapsed view are computed."""
    out = {}
    for n in walk(root):
        out[n.get(field)] = out.get(n.get(field), 0) + 1
    return out
