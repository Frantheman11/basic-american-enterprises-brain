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
8. CRM server key (Fran asked 2026-11-01). The CRM server is 147.182.238.159 (projects/CRM/INSTALL-LOG.md).
   The laptop's key never travels: the desktop makes its own, and the server is told to trust it.
   a. On the desktop, in Git Bash:
      `ssh-keygen -t ed25519 -f ~/.ssh/basic-american_crm -N "" -C "basic-american crm desktop"`
      (the user folder is C:\Users\Intel; quote paths that contain spaces). Show Fran only the `.pub` line.
   b. Fran adds that one public line to the server, either way:
      - DigitalOcean: Droplets, basic-american-crm, Access, Launch Droplet Console (signs in as root
        in the browser). Paste: `echo '<the .pub line>' >> /root/.ssh/authorized_keys`
      - or save the `.pub` line (public, safe) as `handoffs/crm-desktop-key.pub` and ask a laptop
        session to append it to /root/.ssh/authorized_keys over SSH; then delete that file.
   c. Check from the desktop: `ssh -i ~/.ssh/basic-american_crm -o StrictHostKeyChecking=accept-new root@147.182.238.159 "uname -a"`
   d. Never put the private key file in Google Drive, the brain, a note or a chat. Note "done" here.
   e. For CRM setup changes the desktop also needs Python (`winget install Python.Python.3.12`).

## Done on the desktop (session 2026-10-28 to 2026-10-31)
- Step 1 done: Google Drive for desktop runs; the brain opens at G:\My Drive\basic-american-enterprises-brain.
- Claude here opens new sessions in the brain (C:\Users\Intel\.claude\CLAUDE.md); Desktop shortcut
  "Basic American Brain (Google Drive)" added.
- Remote Control on for new sessions on the desktop. The laptop still needs the same switch.
- The updated site (projects/BAE-website/site) was viewed here and differs from Sam's Oct 19 zip.
  The phone on the page is 562-573-3200 in both copies; what the "phone fix" changed is not confirmed.
- Setup paused at setup-files (see setup/progress.json).
- Step 4 done 2026-10-31: git 2.56 installed with winget (C:\Program Files\Git\cmd\git.exe); Fran signed in
  to GitHub once; first push from the desktop worked. Both folders added to git's safe.directory.

## Not done
- Steps 2, 5, 6, 7, 8 (CRM server key). Scheduled task file-sam-website-reply is off since 2026-10-20 (Sam correspondence shut off).
- Sessions do not sync between computers; this handoff and log.md carry the work across.
