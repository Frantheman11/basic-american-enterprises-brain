---
name: build-skill
description: Create or improve a reusable skill from a repeatable process. Use for turn this into a skill, save this workflow, or repair a skill that repeats a procedural mistake.
---

# Save a reusable procedure

Read `knowledge/skill-design.md`, `library/SKILLS.md`, the relevant process and `library/skill-template.md`.

1. Identify the workflow that actually worked, its input, output and verification. If no example exists, work through a harmless one before claiming the procedure is tested.
2. Extend an existing skill if it owns the job. Otherwise create `.claude/skills/<lowercase-hyphen-name>/SKILL.md` with matching `name` and a precise description.
3. Write the steps and meaningful exceptions. Reference canonical facts. Add supporting resources only where they improve correctness or reduce repeated work; link each from the procedure.
4. Do not include a broad tool allowlist, account credentials or silent external side effects. Preserve existing human authorization and explain any external action at the point it is relevant.
5. Add the catalog row. Keep the root routing map short. Test a matching request, a nearby request that should not trigger, and an incomplete-input case. If natural skill discovery cannot be exercised, distinguish file validation from behavioral testing.
6. Run the foundation check, verify any new script on realistic inputs, and report the saved path and actual rehearsal results.

A supporting script belongs to the skill when it serves only that skill. A genuinely shared helper belongs in `tools/` and its inventory. Keep one maintained body for all assistants that use this brain.
