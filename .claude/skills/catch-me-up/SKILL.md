---
name: catch-me-up
description: Start a work session - what changed, what is waiting, what is next. Use when the owner says "catch me up", "where did we leave off", "what's new", or at the start of a session.
---

# Catch me up

1. Pull the latest from GitHub first (`git pull`), so this computer has every change.
2. Read `setup/progress.json`. If setup is not finished, say so first and offer to continue.
3. Read the last 20 lines of `log.md` and any brief in `handoffs/` not marked done.
4. Count files waiting in `raw/inbox/`, drafts waiting in `drafts/`, and open lines in the
   to-do list named in `CLAUDE.md`.
5. Answer in at most six lines:
   - what changed since last time
   - what is waiting (files to file, questions the owner has not answered)
   - the one thing to do next, as a suggestion
