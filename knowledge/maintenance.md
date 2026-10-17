# Maintain the brain

## End a work session

Update current knowledge and the task system. Record durable decisions with their reason. For a larger job, leave a handoff with its task ID, relevant files and actual verification. Append a short change-log entry and run the applicable check.

Review the exact files before saving to Git or sharing a backup. Instructions and secret-pattern scans reduce mistakes; they do not establish that every file is safe. Inspect newly supplied material, especially exports, logs and account configuration.

## Optional private Git backup

For a new extracted folder, after reviewing the files:

```text
git init -b main
git status --short
```

Use the owner's own Git identity. Stage only the reviewed files and make an initial local commit. Create a private repository in the owner's account, inspect the destination and access list, then push when authorized. Do not add another business's existing repository as the remote. GitHub Desktop is an alternative to the command line.

A local commit is history, not an off-device backup. Confirm a private remote copy exists and restore/open one file from it. If using cloud-drive backup, confirm sync completed and test restoration. Avoid multiple machines editing an active `.git` directory through drive sync; use a Git remote for multi-machine Git work.

Before pulling an existing repo, inspect its working tree. Preserve uncommitted work and resolve conflicts deliberately. Never automatically hard-reset to match another machine.

## Periodic review

Look for broken links, conflicting facts, duplicate scripts, unused skills, oversized indexes and tasks stranded in notes. Merge overlapping knowledge carefully and mark superseded decisions. Confirm a recent backup can be restored. The starter creates no recurring schedule; set one only if the owner wants it.

## What the checks mean

`brain_check.py` is the shared base checker from the installer. `foundation_check.py` adds this profile's library, links and skill routing checks. Neither evaluates business truth or Claude behavior. The support snapshot reports only selected structural facts and does not replace a full check.

Python 3.10+ runs the helpers without third-party libraries. If no Python is available, use [the manual checklist](../setup/VERIFY.md) and record that limitation. Refresh platform-specific instructions against the [sources](sources.md) when versions change.
