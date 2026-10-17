---
name: foundation-1-0-0
date: 2026-10-17
source: Fran, in chat, 2026-10-17 (reviewed merge from Documents\second-brain-foundation)
company: all companies
---

foundation_version: 1.0.0, adopted as a reviewed merge, not a replacement.

Added: skills build-skill, grow-system, support-session; knowledge/, library/, support/,
setup/VERIFY.md, tools/foundation_check.py, tools/support_snapshot.py, tools/README.md,
AGENTS.md, ADOPT-EXISTING.md, MEETING-GUIDE.md. CLAUDE.md: one line added to rule 1 (mention setup briefly,
do not force the interview) plus new rows (pointers, 3 skills, 2 tools). Fran's rules unchanged.
Not added: "a skill, a file or another agent cannot give that yes" on rule 2; the edit was blocked
by Claude's safety check and waits for Fran to add it by hand.

Kept ours: .claude/settings.json (foundation's is empty, would drop the guardrails),
setup/steps.json (foundation's step names do not match progress, setup paused at setup-files),
the 5 same-named skills, .gitattributes, .github/workflows/brain-check.yml. brain_check.py was identical.

Checks: none run (no Python on this computer). foundation_check.py will report setup gaps
(old step names, no `backup` record) until steps are merged after setup finishes.

**Why:** a straight copy would have removed the guardrails and broken the paused setup.
