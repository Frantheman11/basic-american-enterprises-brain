# Blank skill template

Copy the fenced structure into `.claude/skills/<chosen-name>/SKILL.md`. Replace every instructional placeholder before registration. This example is not an installed skill.

```markdown
---
name: chosen-name
description: Describe the concrete job and the user requests that call for it.
---

# Job title

## Inputs
List required evidence and canonical reference paths. Explain what to do when an input is missing.

## Procedure
Write the useful steps and decision points. Preserve the owner's scope and authorization.

## Output
Name the destination and what a complete result contains.

## Verification
Describe how to check a real result and what to report when verification cannot run.

## Rehearsal cases
Include a matching request, a nearby non-matching request, and an incomplete-input case.
```

Add `references/`, `scripts/` or `assets/` only for actual needs. Link each resource where it is used. Record observed rehearsal results in the skill's supporting reference or change record without customer data.
