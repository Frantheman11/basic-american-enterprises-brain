---
name: brain-setup
description: Install this second brain with the owner, start to finish, with no installer on the call. Use when the owner says "install my brain", "set me up", "continue setup", or when setup/progress.json is not "installed".
---

# Install the second brain

The owner downloaded this folder, opened it in Claude and typed one line. From here **you lead
everything**: what to click, what to answer, what to drop in. Nobody from the installer is on the
call. The owner is busy and may not be technical. Be warm, short and plain.

**The owner's only real job is giving information about the business. Everything else is yours.**

## How to run it

1. Read `setup/steps.json`, `setup/progress.json` and `config/business.json`. Read
   `setup/review.md` too if it exists (the installer already built a first draft).
2. You are running inside the folder, so the three "owner" steps are done: mark any missing ones in
   `progress.done` with today's date. Factory steps not done are normal for a do-it-yourself brain:
   the setup steps do that work instead (`setup-website` reads the site, `setup-build` builds).
3. Find the first "setup" step not in `progress.done`. If its `do` says "skip if", check and skip it
   (mark it done with a note).
4. Say where you are, in one line: "Step 4 of 10: questions about your business. About 20 minutes."
   On the very first step, also say: "Now and then a box asks to allow something (reading your
   website, opening a folder, saving). For this folder it is safe to click Yes, and 'don't ask
   again' if offered."
5. Write `progress.timing.<step id>.started` (ISO time).
6. Do the step's `do` text.
7. When it is done: add it to `progress.done` with today's date, set `status` to "setup", write
   `progress.timing.<step id>.finished`, add one line to `log.md`, and save (below).
8. Ask: "Ready for the next step, or stop here for today?" Stopping is fine; progress is saved.
   To come back, they type "install my brain" again.

## How to ask

- **Questionnaire, not interview.** Ask a short numbered list (three to six questions) and let the
  owner answer all of it in one reply. Accept short answers. "Skip" is a fine answer: leave it blank
  and note it.
- **Show, then ask.** Before each round, show what is already known and where it came from:
  "From your website: phone (714) 555-0100, hours 7 to 5. Right?" Never ask for what you already have.
- **Blank beats wrong.** Unsure answers stay blank, with a note.
- **Plain words.** If a word might be new to them, add a one-line example.
- **Several companies** (more than one in `companies`): ask which company each answer, file, rule
  and fact belongs to when it is not obvious, and tag it (see `shapes` in `config/business.json`).
- **Rules go in the owner's rules table** in `CLAUDE.md`, in their words, with the date.

## Doing things for them

- **Open folders for them.** When they need to drop files, open the folder window yourself
  (Windows `explorer raw\inbox`, Mac `open raw/inbox`), then ask "did a folder window open?" and
  say "drag your files into it". On Windows, explorer reports an error code even when it worked:
  trust the owner's answer, not the code. If that fails, tell them where it is: "Documents, then <folder>, then raw, then inbox".
- **One click at a time** for anything outside Claude (GitHub Desktop, a website): one instruction,
  wait for "done", then the next. Say what they should see after each click.
- **Pasted text counts.** If dragging files is hard, they can paste text into the chat; save it to
  `raw/inbox/<name>.md`.

## When the owner gets stuck

A stall is anything that makes them hesitate, ask what something means, or do something twice. Each
one goes into `progress.notes` as one line: `"setup-files: did not know where Documents is"`. Never
skip these: they are how the next install gets easier. **Never quote a secret** in a note, a log
line or a commit message: write `<password>` instead.

## Secrets

Before any save, read new files in `raw/inbox/` for passwords, keys, card or bank numbers. If one is
there, say which file and what kind of secret (never the secret itself), ask "May I remove it?", and
with a yes replace only the secret with `[removed <date>]`.

## Saving

- **Before `setup-backup`**: if git is present, save with `git add -A` and `git commit -m "Setup: <step>"`
  (first time with no repository yet: `git init -b main`). If the commit says "Author identity
  unknown", or git is missing, skip saving quietly: the files are safe on their computer, and
  `setup-backup` makes the copy.
- **After `setup-backup`**: a cloud-drive copy saves itself. A GitHub copy: save with git if it
  works; otherwise say "In GitHub Desktop: write 'Setup' in the Summary box, click Commit to main,
  then Push origin." GitHub Desktop does not give Claude git, so on most owner computers that click
  is theirs, every session.

## The check by hand (no Python on this computer)

Read the files and confirm each line; fix what fails:
- `index.md` lists every page in `wiki/`, `wiki/companies/`, `wiki/processes/` and `handoffs/`
- `CLAUDE.md` is under 200 lines; `memory/MEMORY.md` under 150; every memory note has name, date, source
- `config/business.json` has: what_we_do, phone or email, at least one service, ideal_customer, at least one team member, three voice words
- `wiki/business.md`, `offers.md`, `customers.md`, `voice.md`, `team.md` and every company page have no `<!-- stub -->` line
- at least one page in `wiki/processes/`
- the "Who this business is" paragraph in `CLAUDE.md` is written
- every setup step except `setup-finish` is in `progress.done`
- no password, key or card number anywhere (a `[removed <date>]` marker is fine)

## Finishing

On `setup-finish`, run the check as the step says. Fix every failure it names; anything you cannot
fix goes into `progress.notes`. Set `status` to "installed" and add `installed_on`. Then tell the
owner, in three lines, how to use the brain from now on:
"**file it**" after dropping files in, "**catch me up**" to start a day, "**remember this**" when
you correct me.
