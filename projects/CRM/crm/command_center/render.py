"""One recursive renderer. Every expandable thing in every view comes through here.

Two rules govern the output, and both are checkable by looking at a page:

  ⭐ The collapsed state of every node must be legible on its own. A row that only makes
    sense once expanded has failed. So a parent row carries a ROLLUP computed from its
    children — "24 skills · 3 recommended" — and depth is opt-in. This is also what makes
    the phone form free: the same page, shallower by default, no second build.

  ⭐ Density is what makes an instrument read as an instrument. Resist spacing it out
    until it looks like a marketing page.

Disclosure is <details>/<summary>: native, keyboard-accessible, works with JavaScript
off, and the browser keeps the open/closed state for free. No widget was written for a
job the platform already does.

No colour, no font and no business name appears in this file. Appearance lives in
www/cc.css and is written entirely in the brand stylesheet's tokens.
"""

from html import escape

from .schema import walk

# The plain-English word for each kind. DESIGN.md: write the plain-English label first —
# "agent" is our word. Used as the row's type tag.
KIND_LABEL = {
    "department": "department", "system": "system", "workflow": "workflow",
    "agent": "agent", "skill": "skill", "memory": "note", "knowledge": "knowledge",
    "tool": "tool", "record": "record", "artifact": "artifact", "task": "task",
    "metric": "figure", "section": "group", "doc": "document",
    "asset": "asset", "data": "data file",
}

# `section` is a grouping node, not a kind of thing the business owns. Letting it compete
# for the two kind slots in a rollup spent one of them saying "18 groups" — which tells a
# reader nothing they could not see by looking at the rows underneath.
NOT_A_KIND = ("section",)

# Which statuses are worth a chip on a collapsed row. `ready` is the default state of
# most things and a chip on everything is noise, not information.
CHIP_STATUSES = ("active", "in_progress", "blocked", "ghost", "locked", "done")


def esc(s):
    return escape(str(s if s is not None else ""), quote=True)


def _kind_counts(n):
    """How many of each kind are UNDER `n` — by what is on disk, not by what got drawn.

    A node may declare `rollup_counts` to mean "these are the true counts beneath me; my
    children are a sample, or absent". Two views need it and both read falsely without
    it: a folder too big to list returned no children and so contributed nothing, and a
    run ledger drew its latest 40 of 103 rows so the collapsed line read "40 records". In
    both the summary described the view instead of the business, and it was least accurate
    exactly where there was most to summarise — the opposite of the job.

    A declared count wins and stops the descent, so nothing is ever counted twice.
    """
    declared = n.get("rollup_counts")
    if isinstance(declared, dict) and declared:
        return {k: int(v) for k, v in declared.items()}
    counts = {}
    for k in n.get("children") or []:
        if k["kind"] not in NOT_A_KIND and not (
                isinstance(k.get("rollup_counts"), dict) and k["rollup_counts"]):
            counts[k["kind"]] = counts.get(k["kind"], 0) + 1
        for kind, count in _kind_counts(k).items():
            counts[kind] = counts.get(kind, 0) + count
    return counts


def rollup(n):
    """The one-line summary that makes a collapsed row legible.

    Counts what is UNDER a node, not the node itself: how many leaves, and how many of
    them are in a state the reader would want to know about before expanding.
    """
    kids = [k for k in walk(n)][1:]
    if not kids:
        return ""
    parts = []
    kinds = _kind_counts(n)
    if not kinds:
        return ""
    top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    for kind, count in top:
        word = KIND_LABEL.get(kind, kind)
        parts.append(f"{count} {word}{'s' if count != 1 else ''}")
    for state, word in (("blocked", "blocked"), ("ghost", "recommended"), ("active", "running")):
        c = sum(1 for k in kids if k["status"] == state)
        if c:
            parts.append(f"{c} {word}")
    waiting = sum(1 for k in kids if k.get("queue_status") == "waiting_human")
    if waiting:
        parts.append(f"{waiting} waiting on you")
    return " · ".join(parts)


def _chips(n):
    out = []
    if n["status"] in CHIP_STATUSES:
        out.append(f'<span class="cc-chip mono st-{esc(n["status"])}">{esc(n["status"].replace("_", " "))}</span>')
    if n.get("queue_status") and n["queue_status"] not in ("unqueued", None):
        out.append(f'<span class="cc-chip mono q-{esc(n["queue_status"])}">'
                   f'{esc(n["queue_status"].replace("_", " "))}</span>')
    for b in n.get("badges") or []:
        if b == "recommended":
            continue  # the ghost status chip already says it — two chips for one fact is noise
        out.append(f'<span class="cc-chip mono bdg-{esc(b)}">{esc(b.replace("_", " "))}</span>')
    return "".join(out)


def _source(n):
    """Every node names the file its fact came from. This is the rule made visible: if a
    row has no path under it, somebody typed a number."""
    src = n.get("source") or ""
    return f'<span class="cc-src mono" title="{esc(src)}">{esc(src)}</span>'


def _detail(n):
    """The body a row opens into: the summary line, the sourced fields, the ghost case."""
    bits = []
    d = n.get("detail") or {}
    if d.get("summary"):
        bits.append(f'<p class="cc-sum">{esc(d["summary"])}</p>')
    if n["status"] == "ghost":
        pct = int(round(float(n.get("confidence", 0)) * 100))
        bits.append(f'<p class="cc-sum"><strong>Why this is recommended.</strong> {esc(n["rationale"])}</p>')
        bits.append(f'<p class="cc-fine">Confidence {pct}% · computed from '
                    + ", ".join(f'<span class="mono">{esc(e)}</span>' for e in n.get("evidence", []))
                    + "</p>")
    fields = d.get("fields") or {}
    if fields:
        rows = "".join(
            f'<div class="cc-f"><span class="cc-fk mono">{esc(k)}</span>'
            f'<span class="cc-fv">{esc(v)}</span></div>' for k, v in fields.items())
        bits.append(f'<div class="cc-fields">{rows}</div>')
    if d.get("links"):
        bits.append('<p class="cc-fine">'
                    + " · ".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in d["links"])
                    + "</p>")
    if d.get("decide"):
        bits.append(_decide(d["decide"]))
    return "".join(bits)


def _decide(spec):
    """The approvals control: a button per decision, and nothing else.

    ⭐ Pressing one records a STATUS. It does not send, publish, pay or delete — the next
    run reads the status and acts. That separation is the whole reason this is safe to put
    in front of somebody on a phone, and it is why there is no "send now" button here.

    Blue is the only accent that means act, so the buttons carry it singly; the two that
    are not the happy path are outlined rather than filled.
    """
    btns = []
    for opt in spec.get("options") or []:
        primary = " cc-primary" if opt == "approved" else ""
        btns.append(
            f'<button class="cc-btn{primary}" data-decide="{esc(opt)}" '
            f'data-record="{esc(spec["record"])}" data-id="{esc(spec["id"])}">'
            f'{esc(opt.replace("-", " "))}</button>')
    return (f'<div class="cc-decide">{"".join(btns)}'
            f'<span class="cc-fine cc-decide-msg" role="status"></span></div>')


def _row_inner(n, depth):
    r = rollup(n)
    return (
        f'<span class="cc-kind mono">{esc(KIND_LABEL.get(n["kind"], n["kind"]))}</span>'
        f'<span class="cc-label">{esc(n["label"])}</span>'
        f'<span class="cc-chips">{_chips(n)}</span>'
        + (f'<span class="cc-roll">{esc(r)}</span>' if r else "")
        + _source(n)
    )


def render_node(n, depth=0, open_to=1):
    """One node and everything under it. `open_to` is how many levels start expanded —
    depth is opt-in, so the default is shallow and the top level is a readable summary."""
    kids = n.get("children") or []
    cls = f'cc-node d{min(depth, 4)} k-{esc(n["kind"])} s-{esc(n["status"])}'
    body = _detail(n)
    if not kids:
        if not body:
            return f'<div class="{cls} leaf" id="{esc(n["id"])}"><div class="cc-row">{_row_inner(n, depth)}</div></div>'
        return (f'<details class="{cls} leaf" id="{esc(n["id"])}">'
                f'<summary class="cc-row">{_row_inner(n, depth)}</summary>'
                f'<div class="cc-body">{body}</div></details>')
    inner = "".join(render_node(c, depth + 1, open_to) for c in kids)
    op = " open" if depth < open_to else ""
    return (f'<details class="{cls}" id="{esc(n["id"])}"{op}>'
            f'<summary class="cc-row">{_row_inner(n, depth)}</summary>'
            f'<div class="cc-body">{body}<div class="cc-kids">{inner}</div></div></details>')


def render_tree(nodes, open_to=1):
    """A list of root nodes — a whole view."""
    return "".join(render_node(n, 0, open_to) for n in nodes)


def stat_strip(stats):
    """A row of figures. Each one is (label, value, source) — the source is required by
    the same rule as a node's: a figure with no file behind it is a typed number."""
    cells = "".join(
        f'<div class="cc-stat"><div class="cc-stat-v">{esc(v)}</div>'
        f'<div class="cc-stat-l">{esc(label)}</div>'
        f'<div class="cc-src mono" title="{esc(src)}">{esc(src)}</div></div>'
        for label, v, src in stats)
    return f'<div class="cc-stats">{cells}</div>'
