# Architecture: one place for each kind of knowledge

The brain keeps evidence, current understanding and procedures separate so a correction has a clear home.

| Kind | Canonical home | What belongs there |
|---|---|---|
| Source evidence | `raw/` | Original notes and documents, with origin and capture date |
| Current business facts | `config/business.json` | Confirmed name, contact, offers, team, hours, with field-level sources |
| Current explanations | `wiki/` | Summaries and decisions about a subject, linked to evidence |
| How to carry out a job | `.claude/skills/` | A reusable procedure, its triggers, references and checks |
| Human operating process | `wiki/processes/` | A job's flow, handoffs, exceptions and definition of done |
| Why a decision happened | `memory/` | Dated context and reasoning; superseded decisions labeled |
| Current commitments | `todo.md` or the chosen task system | The next action and status, once |
| How to resume a task | `handoffs/` | Scope, completed work, key files, validation and task IDs |
| Reusable blank material | `library/` | Templates and examples that contain no business facts |

A skill is a recipe; a profile is information. A skill that drafts a reply should read the current voice and offer files rather than embed them. When a phone number changes, edit its canonical field once. Derived documents can show snapshots, but label their date and regenerate them when needed.

## Access determines the boundary

Every customer owns a separate working repository. Reusable starter machinery contains no customer data. Several companies can share one brain only if their access requirements match; use stable company identifiers and make the company explicit in every fact. Different access requirements call for different repositories, not merely different folders.

## Small entry points

`CLAUDE.md` routes work and carries the few rules needed in every session. Indexes point to deeper pages. Detailed instructions are loaded when relevant. Our limits of 200 lines for this guide and 150 for the repo's memory index are maintenance budgets, not guarantees about context or memory.

This repo's `memory/MEMORY.md` is an ordinary shared file that CLAUDE.md tells the assistant to read. Claude's machine-local auto memory is separate; it is not your cross-machine backup. [Claude memory documentation](https://code.claude.com/docs/en/memory).
