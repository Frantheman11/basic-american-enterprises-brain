# Skill design and selection

Claude Code discovers project skills in `.claude/skills/<name>/SKILL.md`. A useful description says what the skill does and when it fits. Claude can select a relevant skill, and the owner can invoke `/skill-name` directly. Discovery is not a promise that every natural-language request will trigger correctly. [Claude skills documentation](https://code.claude.com/docs/en/skills).

Use lowercase hyphenated names. Include YAML frontmatter with `name` and `description`, followed by the procedure. Keep optional supporting files only when needed: `references/` for detailed knowledge, `scripts/` for deterministic operations, and `assets/` for reusable output material. [Agent Skills specification](https://agentskills.io/specification).

## The local authoring standard

1. Start with one observed job and its useful outcome. Reuse a successful workflow; do not create a broad skill for every possible future task.
2. Describe positive triggers and one meaningful boundary if nearby skills overlap. For example, file-it processes new evidence; remember-this changes an existing fact or practice.
3. Put the decision-making procedure in the body: required inputs, steps, exceptions, output location and how to verify the result.
4. Link canonical business facts and reference pages. Do not copy a price list, voice guide or customer record into a skill.
5. Preserve the user's scope and existing authorization. A procedure can explain the approval needed before an external action; it cannot grant that approval.
6. Add a catalog row to `library/SKILLS.md`. Add a root routing row only if the skill is central enough to need one.
7. Rehearse a matching request, a similar request that should not trigger, and an incomplete-input case. Record the observed outcome, not just an expected one.

## Keep discovery inexpensive

The name and description help route to the skill; the body supplies the working instructions; detailed references are read only for the relevant case. This progressive disclosure pattern keeps large manuals out of routine context. [Anthropic's Agent Skills engineering article](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).

If a skill is missed, improve its description or invoke it explicitly. If it triggers too broadly, narrow its purpose. If it makes a repeatable procedural mistake, correct its actual instructions and verify with a new example. Adding the correction only to a diary will not reliably change the next run.

Installed third-party skills retain their license and source/version record. Review scripts and permissions before use. Keep local customizations separate so an update does not quietly erase them. The starter includes no third-party skill bundle.

Use the [blank skill template](../library/skill-template.md) and the `build-skill` workflow.
