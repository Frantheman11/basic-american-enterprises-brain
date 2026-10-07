---
name: remember-this
description: Save a correction, a decision or a new fact in the right place so the same mistake never happens twice. Use when the owner says "remember this", "don't do that again", "from now on", or corrects your work.
---

# Remember this

The test the brain is scored on: **does it make the same mistake twice?** This skill is how it
doesn't.

## Decide where it goes

| It is about... | Save it in | Example |
|---|---|---|
| how a job is done | that job's skill file, or `wiki/processes/<job>.md` | "always attach the price sheet to a quote" |
| a fact | `config/business.json` or the wiki page, replacing the old value | "Saturday hours are now 9 to 1" |
| a rule Claude must never break | the owner's rules table near the top of `CLAUDE.md`, in their words, with the date | "never discount below 10%" |
| a decision and its reason | a new file in `memory/` | "we stopped serving Riverside, too far" |

## Saving a memory note

1. Read `memory/MEMORY.md` first. If a note already covers it, update that note. Never write two.
2. One fact per file: `memory/<short-name>.md`, starting with:
   ```
   ---
   name: short-name
   date: 2026-10-05
   source: owner, in chat
   ---
   ```
   Then the decision, and a **Why:** line.
3. Add one line to `memory/MEMORY.md`: `- [Title](short-name.md) - the decision in a few words`.
4. If the new note makes an old one wrong, delete the old one.

Then tell the owner in one line where it was saved.
