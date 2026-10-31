# Set up the Windows desktop to run the brain

Started 2026-10-19 · Fran, in chat. Goal: the desktop stays on and runs the brain (scheduled checks,
email); the laptop can sleep and is used on the go. No migration: the same folder, synced.

## Decided so far
- The desktop is a Windows PC. Google Drive for desktop and Outlook are not installed yet; Fran installs them first.
- Suggested (not yet decided): the desktop becomes the always-on machine.
- Decided 2026-10-31: Google Drive keeps laptop and desktop in step; private GitHub repos under
  Frantheman11 are the second backup (not ipost-sports, a separate account).

## Steps (walk Fran through these one at a time, at the desktop)
1. Google Drive for desktop, signed in as franmcgrath11@gmail.com; confirm the
   basic-american-enterprises-brain folder appears and opens.
2. Outlook with fran@basicamericanenterprises.com (Microsoft 365 via GoDaddy) and any other accounts
   (frantheman11@hotmail.com, fran@ipostsports.com, franmcgrath11@gmail.com, others).
3. Claude desktop app, signed in; open the brain folder; run "catch me up".
4. Git on the desktop (Fran installs). The GitHub copies already exist (done 2026-10-31 from the
   laptop): Frantheman11/basic-american-enterprises-brain and Frantheman11/BAE-Website, both private.
   Do not clone: Drive already brings both folders and their `.git`. Fran signs in to GitHub once in
   the pop-up on the first push from the desktop. Then: one computer at a time; at the end of a
   session, wait for Drive to finish syncing, then commit and push.
5. Recreate the scheduled task "file-sam-website-reply" on the desktop (its prompt is in
   C:\Users\Fran McGrath\.claude\scheduled-tasks\file-sam-website-reply\SKILL.md on the laptop) and
   turn it off on the laptop, so only one machine checks.
6. Test the send-email skill on the desktop: `-Action accounts`, and a discarded test draft.
7. Desktop power settings: never sleep when plugged in (Fran changes this himself).

## Not done
Nothing installed or changed on the desktop yet. Scheduled task still runs on the laptop.
