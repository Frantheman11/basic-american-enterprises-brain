# Grow the system one useful capability at a time

Before building, state the job and the observable result. Search the skill catalog, processes and tool inventory. Improve an existing capability when it already owns that job.

## Choose the smallest useful form

| Need | Start with |
|---|---|
| Store a fact or explanation | Profile field or sourced wiki page |
| Repeat a human procedure | Process page, then a skill after rehearsal |
| Repeat an exact transformation | Small script with a meaningful test |
| Reuse an output layout | Blank template or asset in the responsible skill/library |
| Integrate an outside service | A reviewed connector with the minimum needed access |
| Run unattended | A separate schedule decision after the workflow works manually |

The structural categories used here are local conventions: **house** skills maintain the brain; **engine** skills do a reusable job; **profiles** hold business-specific facts; **delivery** skills serve the business's delivery work; **vendored** skills come from another maintained source. Categories help ownership and routing; they do not require extra folders or installing every category.

Keep one implementation per job with configuration for legitimate variations. Do not fork a workflow for each customer just to change its name or tone. Customer facts stay in that customer's brain; the reusable master stays blank.

## Minimum design record for a new capability

- Job, trigger and result the owner can recognize.
- Canonical inputs, business/access boundary and output path.
- Existing capability considered and why extension is insufficient.
- Tools and dependencies actually required; source and version for an external component.
- Verification example, expected failure behavior and recovery method.
- Owner authorization needed for external effects.

Build a small useful slice, run it with harmless data, check the actual output, and update its catalog entry. A passing syntax check alone does not establish that an integration works.

Avoid introducing a database, agent team, background service or large indexing system merely because it is available. Add infrastructure when a measured problem calls for it. Record the reason and maintenance cost so a later session understands the decision.

Version meaningful structural changes. Review a proposed diff before merging an upstream starter update; never overwrite the operating brain with a new template.
