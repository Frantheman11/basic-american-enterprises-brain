"""The views. One module per surface, split from the first commit on purpose.

Beam's command center is a single 94KB index.html, and that is the measured reason a
revision there has to be captured in a separate vision file instead of simply made. The
trap is real and cheap to avoid: the renderer, the views and the data loaders are
separate files here from commit one.

Each view module exposes:

    ID          the view's id, matching profiles.VIEW_IDS
    TITLE       what the tab says
    BLURB       one plain-English line under the title
    build(ctx)  -> {"nodes": [...], "stats": [...], "notes": [...]}

`ctx` is the loaded context (profile, brain index, repo root). A view never opens a file
itself — it reads what the loaders already put in `ctx`, so a view cannot become a second
place that knows where data lives.
"""

from . import agents, artifacts, board, brain, business, overview, pipeline, records
from ..profiles import RECORD_VIEWS, VIEW_IDS

# Tab order, and it is the order a business reads in: what needs me, where the work
# lives, who we are talking to, what ran, what we know.
MODULES = (board, overview, pipeline, agents, brain, business, artifacts)

BY_ID = {m.ID: m for m in MODULES}

# The profile validator lists view ids; this asserts the two cannot drift apart silently.
assert tuple(BY_ID) == VIEW_IDS, (
    f"views package exposes {tuple(BY_ID)} but profiles.VIEW_IDS is {VIEW_IDS}")

# A view declares `SCOPE = "records"` when it is built from records alone, and only those
# may be given to a role that sees its own records. No declaration means the view reads
# the whole repo, which is the safe default. The validator's list and the modules agree.
_record_built = tuple(m.ID for m in MODULES if getattr(m, "SCOPE", "repo") == "records")
assert set(_record_built) == set(RECORD_VIEWS), (
    f"views declaring SCOPE='records' are {_record_built} but profiles.RECORD_VIEWS is "
    f"{RECORD_VIEWS}")


def build(view_id, ctx):
    return BY_ID[view_id].build(ctx)


__all__ = ["MODULES", "BY_ID", "build"]
