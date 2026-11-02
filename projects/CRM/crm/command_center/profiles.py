"""Profile loading — the only place the engine learns what business it is drawing.

⭐ The rule this module exists to enforce: **the renderer never knows what business it
is drawing.** If any engine module branches on a business_id, the profile is missing a
field — add the field, do not add the branch.

A profile is `data/command-center/<business_id>.json` in the repo that runs the
instance. It is small and mostly structural: departments, where files live, which views
exist, and which stylesheet carries the brand.
"""

import re
import json
from pathlib import Path

from .schema import SchemaError

# Where profiles live, relative to the repo that runs the instance.
PROFILE_DIR = Path("data") / "command-center"

REQUIRED_FIELDS = ("business_id", "name", "root", "stylesheet", "departments", "views")

# The token contract. Any stylesheet a profile names must define these, because the
# engine's own CSS is written entirely in terms of them and carries no colour of its own.
# This is how "inherit the brand, invent no third visual system" is made mechanical.
REQUIRED_TOKENS = (
    "--carbon", "--carbon2", "--paper", "--white", "--ink", "--muted", "--dim", "--dim2",
    "--soft", "--soft2", "--blue", "--blue-dk", "--blue-lt", "--green",
    "--line", "--line2", "--wline", "--ok", "--warn", "--bad", "--low",
    "--r", "--r-sm", "--r-lg", "--r-xs", "--ease", "--sh-1", "--sh-2", "--sh-3",
    "--lift", "--surf",
)


def profile_path(business_id, root=None):
    return Path(root or ".") / PROFILE_DIR / f"{business_id}.json"


def load(business_id, root=None):
    """Load and validate one profile. Raises rather than returning a half-profile."""
    path = profile_path(business_id, root)
    if not path.is_file():
        raise SchemaError(f"no profile at {path} — an instance needs one, and only one")
    prof = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(prof, str(path))
    # Forward slashes always. A source path is read by a person and quoted in a brief;
    # a backslash there is Windows leaking onto the page.
    prof["_path"] = str(path).replace("\\", "/")
    return prof


def validate_profile(prof, where="<profile>"):
    for field in REQUIRED_FIELDS:
        if field not in prof:
            raise SchemaError(f"{where}: missing required field '{field}'")
    if not prof["departments"]:
        raise SchemaError(f"{where}: a business with no departments has no Business view")
    seen = set()
    for d in prof["departments"]:
        for field in ("id", "label", "plain_english", "owns"):
            if field not in d:
                raise SchemaError(f"{where}: department {d.get('id', '?')} missing '{field}'")
        if d["id"] in seen:
            raise SchemaError(f"{where}: duplicate department id {d['id']!r}")
        seen.add(d["id"])
        if not isinstance(d.get("sources", []), list):
            raise SchemaError(f"{where}: department {d['id']} sources is not a list")
    for v in prof["views"]:
        if v not in VIEW_IDS:
            raise SchemaError(f"{where}: unknown view {v!r} — views are {', '.join(VIEW_IDS)}")
    _validate_records(prof, where)
    _validate_access(prof, where)
    _validate_pipelines(prof, where)
    from . import territory  # regions, each person's territory, roles that see by it
    territory.validate(prof, where)
    return prof


# Kept here rather than imported from .views to avoid an import cycle; the views package
# asserts it matches at import time, so the two cannot drift silently.
VIEW_IDS = ("board", "today", "pipeline", "agents", "brain",
            # Still in the code and still available to a profile that wants them.
            # Off the default tab bar since 2026-09-21: `business` is repo hygiene
            # rather than a business view, and `artifacts` folds into the brain.
            "business", "artifacts")

# The views built from RECORDS alone. Every other view reads the whole repo (memory,
# client folders, pricing), so a role that sees only its own records may not have one.
# The views package asserts each module declares the same scope.
RECORD_VIEWS = ("today", "pipeline")


# ---------------------------------------------------------------------------------
# Access: who may sign in, what each role sees, and what each role can do
# ---------------------------------------------------------------------------------

IDENTITY_MODES = ("local", "cloudflare_access")
# Written down and refused until built, so a profile cannot switch on a mode that
# does not exist and fall through to something weaker.
NOT_BUILT_MODES = {"oidc": "not built yet: in-app Google or Microsoft sign-in is design "
                           "§4.1 (c), and the trigger is the first enterprise that asks"}

SEES = ("all", "own", "territory")  # territory: own + the person's states (territory.py)
NOT_BUILT_SEES = {"team": "not built yet: the trigger is a person who manages other reps"}

# Everything a role can be allowed to do. A closed list, so a typo is an error at load
# rather than a permission that silently matches nothing.
ACTIONS = ("assign", "reassign", "convert", "decide", "queue", "set_field",
           "log_touch", "release", "claim", "create_lead",
           "crm_read", "crm_create", "crm_edit", "crm_assign", "crm_publish_buildout", "buildout_read",
           "gym_read", "gym_checkin", "gym_promote", "gym_manage")  # the gym module (gym.py)
ACTIONS += ("pw_read", "pw_review", "pw_officer")  # the Prevailing Wage Desk (payroll_desk_store.py)
ACTIONS += ("quote_read", "quote_edit", "quote_approve")  # the Quote Desk (quote_desk.py)

# The field that says who a record belongs to. A record type without it cannot be judged
# by "own", so it is invisible to an "own" role (EspoCRM's fail-closed OnlyOwn).
OWNER_FIELD = "owner"


def _is_loopback_url(url):
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _validate_records(prof, where):
    recs = {k: v for k, v in (prof.get("records") or {}).items() if isinstance(v, dict)}
    for name, spec in recs.items():
        through = spec.get("visible_through")
        if through is None:
            continue
        if not isinstance(through, dict) or not through:
            raise SchemaError(f"{where}: records.{name}.visible_through must be a map of "
                              "field path -> record type")
        for path, target in through.items():
            tspec = recs.get(target)
            if tspec is None:
                raise SchemaError(f"{where}: records.{name}.visible_through names "
                                  f"{target!r}, which is not a record type here")
            if OWNER_FIELD not in tspec.get("human_owned", []):
                raise SchemaError(f"{where}: records.{name} follows {target!r} for visibility, "
                                  f"but a {target} has no {OWNER_FIELD!r} field to judge by")
            if tspec.get("visible_through"):
                raise SchemaError(f"{where}: records.{name} follows {target!r}, which itself "
                                  "follows another record. One hop only.")


def _validate_pipelines(prof, where):
    """`pipeline.pipelines` (Portfolio CRM P3): more than one pipeline in one business, for
    example investors and customers. Optional: without it the stages are one pipeline, as before.
    Each stage names its pipeline (`stage.pipeline`, else the first); stage ids stay unique across
    the business, so a deal's stage alone says which pipeline it is in."""
    spec = prof.get("pipeline") or {}
    lines = spec.get("pipelines")
    if lines is None:
        return
    if not isinstance(lines, list) or not 1 <= len(lines) <= 10:
        raise SchemaError(f"{where}: pipeline.pipelines must list 1 to 10 pipelines")
    ids = []
    for line in lines:
        pid = line.get("id") if isinstance(line, dict) else None
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", pid) or pid in ids:
            raise SchemaError(f"{where}: pipeline id {pid!r} must be short, lowercase and unique")
        label = line.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 40:
            raise SchemaError(f"{where}: pipeline {pid} needs a label of 1 to 40 characters")
        ids.append(pid)
    stages = spec.get("stages") or []
    seen = set()
    for st in stages:
        line = st.get("pipeline", ids[0])
        if line not in ids:
            raise SchemaError(f"{where}: stage {st.get('id')!r} names pipeline {line!r}, which is not listed")
        if st.get("id") in seen:
            raise SchemaError(f"{where}: stage id {st.get('id')!r} is used twice; stage ids are unique across pipelines")
        seen.add(st.get("id"))
    for pid in ids:
        if not any(st.get("pipeline", ids[0]) == pid and not st.get("terminal") and not st.get("outcome") for st in stages):
            raise SchemaError(f"{where}: pipeline {pid} needs at least one open stage")


def _validate_access(prof, where):
    """The rules that keep "a rep sees only their own" true, checked when the profile
    loads — so a bad role is an error at boot, never a permission at request time."""
    recs = {k: v for k, v in (prof.get("records") or {}).items() if isinstance(v, dict)}
    roles = prof.get("roles")
    if isinstance(roles, dict):
        # A `$comment` documents the block; it is not a role anybody can hold.
        roles = {k: v for k, v in roles.items() if not str(k).startswith("$")}
    access = prof.get("access")
    members = [m for m in (prof.get("people") or {}).get("members", []) if isinstance(m, dict)]

    if access is not None:
        if not isinstance(access, dict):
            raise SchemaError(f"{where}: access must be an object")
        mode = access.get("identity")
        if mode in NOT_BUILT_MODES:
            raise SchemaError(f"{where}: access.identity {mode!r} is {NOT_BUILT_MODES[mode]}")
        if mode not in IDENTITY_MODES:
            raise SchemaError(f"{where}: access.identity must be one of "
                              f"{', '.join(IDENTITY_MODES)}, not {mode!r}")
        team = access.get("team_domain") or ""
        if team:
            if team.endswith("/") or "/" in team.split("://", 1)[-1]:
                raise SchemaError(f"{where}: access.team_domain is a bare origin like "
                                  "https://<team>.cloudflareaccess.com, with no path or "
                                  "trailing slash (it must equal the token's issuer)")
            if not (team.startswith("https://")
                    or (team.startswith("http://") and _is_loopback_url(team))):
                raise SchemaError(f"{where}: access.team_domain must be https "
                                  "(plain http is allowed only to loopback, for tests)")
        for field in ("team_domain", "aud", "local_person"):
            if not isinstance(access.get(field, ""), str):
                raise SchemaError(f"{where}: access.{field} must be a string")
        if not isinstance(access.get("origins", []), list):
            raise SchemaError(f"{where}: access.origins must be a list")
        lp = access.get("local_person") or ""
        if lp and lp not in [m.get("id") for m in members]:
            raise SchemaError(f"{where}: access.local_person {lp!r} is not one of the people")

    if roles is not None:
        if not isinstance(roles, dict) or not roles:
            raise SchemaError(f"{where}: roles must be a non-empty map of role -> rules")
        for rid, r in roles.items():
            if not isinstance(r, dict):
                raise SchemaError(f"{where}: role {rid!r} must be an object")
            sees = r.get("sees")
            if sees in NOT_BUILT_SEES:
                raise SchemaError(f"{where}: role {rid!r} sees {sees!r}: {NOT_BUILT_SEES[sees]}")
            if sees not in SEES:
                raise SchemaError(f"{where}: role {rid!r} sees must be one of "
                                  f"{', '.join(SEES)}, not {sees!r}")
            views = r.get("views", [])
            for v in views:
                if v not in VIEW_IDS:
                    raise SchemaError(f"{where}: role {rid!r} names unknown view {v!r}")
                if sees in ("own", "territory") and v not in RECORD_VIEWS:
                    raise SchemaError(
                        f"{where}: role {rid!r} sees only its own records, but the {v!r} view "
                        f"reads the whole repo. An own-records role may have "
                        f"{', '.join(RECORD_VIEWS)} only.")
            for a in r.get("can", []):
                if a not in ACTIONS:
                    raise SchemaError(f"{where}: role {rid!r} can {a!r}, which is not an "
                                      f"action. Actions are {', '.join(ACTIONS)}")
            fields = r.get("fields")
            if fields is not None:
                if not isinstance(fields, dict):
                    raise SchemaError(f"{where}: role {rid!r} fields must be a map of "
                                      "record type -> field list")
                for rtype, flist in fields.items():
                    spec = recs.get(rtype)
                    if spec is None:
                        raise SchemaError(f"{where}: role {rid!r} names fields on {rtype!r}, "
                                          "which is not a record type here")
                    extra = sorted(set(flist) - set(spec.get("human_owned", [])))
                    if extra:
                        raise SchemaError(
                            f"{where}: role {rid!r} lists {', '.join(extra)} on a {rtype}, "
                            f"but a human may not write that at all. A role only narrows "
                            f"records.{rtype}.human_owned; it never adds to it.")

    if roles is not None or access is not None:
        owner_of = {}
        for m in members:
            if m.get("role") not in (roles or {}):
                raise SchemaError(f"{where}: {m.get('id')!r} has role {m.get('role')!r}, "
                                  f"which is not one of the roles "
                                  f"({', '.join(roles or {}) or 'none are defined'})")
            emails = m.get("emails", [])
            if not isinstance(emails, list):
                raise SchemaError(f"{where}: {m.get('id')!r} emails must be a list")
            for e in emails:
                if not isinstance(e, str) or "@" not in e or e != e.strip():
                    raise SchemaError(f"{where}: {m.get('id')!r} has a malformed email {e!r}")
                key = e.lower()
                if key in owner_of:
                    raise SchemaError(f"{where}: {e} belongs to both {owner_of[key]!r} and "
                                      f"{m.get('id')!r}. One email, one person, or a sign-in "
                                      "could land as the wrong one.")
                owner_of[key] = m.get("id")


def stylesheet_tokens(prof, root=None):
    """The token names the profile's stylesheet actually defines. Used by the design gate."""
    import re
    css = (Path(root or ".") / prof["stylesheet"])
    if not css.is_file():
        raise SchemaError(f"profile stylesheet {css} does not exist")
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", css.read_text(encoding="utf-8")))


def missing_tokens(prof, root=None):
    """Tokens the engine's CSS needs that the profile's stylesheet does not define."""
    return sorted(set(REQUIRED_TOKENS) - stylesheet_tokens(prof, root))


def departments(prof):
    return prof["departments"]


def instance_root(prof, root=None):
    """The directory the instance reads its live files from."""
    return (Path(root or ".") / prof["root"]).resolve()
