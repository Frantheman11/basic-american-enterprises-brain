"""Commission plans, for the "My earnings" estimate (sales rep review 2026-09-27).

The rates live in the profile (`crm.commission`), copied from the business's own policy
(CTA: docs/sales/partner-commission-policy-2026-09-24.md). The engine names no rate.
Each person sees only their own plan; an administrator sees everyone's, to know what is owed.

The screen shows an ESTIMATE: commission is paid only on money collected, never on
pass-through costs (ad spend, third-party software, hosting, tax), and a discount or a refund
lowers it. The CRM knows none of that, so it never calls the figure "owed".
"""
from . import access


def plans(prof, person):
    spec = (prof.get("crm") or {}).get("commission") or {}
    table = {k: v for k, v in (spec.get("plans") or {}).items() if isinstance(v, dict)}
    members = {m: p for m, p in (spec.get("members") or {}).items() if p in table}
    if not table or person is None:
        return None
    everyone = access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")
    shown = members if everyone else {k: v for k, v in members.items() if k == person.id}
    return {"plans": {k: table[k] for k in set(shown.values())}, "members": shown,
            "note": spec.get("note") or "An estimate. Paid only on money collected."}
