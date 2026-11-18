# CRM install log (Fran)

No secrets in this file. Guide: `START-HERE.md`.

## Decisions

- 2026-11-01: The CRM gets its own new domain, **basicamericancrm.com** (bought at GoDaddy 2026-11-01).
  basicamericanenterprises.com keeps its DNS at GoDaddy and is never moved (Fran).
- 2026-11-01: Use Fran's existing Cloudflare account (franmcgrath11@gmail.com sign-in; it also holds oroliquidators.com).
- Address: https://crm.basicamericancrm.com. Kit files updated to it; `release.tar.gz` rebuilt with Sam's
  exact 193-file list (only `CLAUDE.md` text changed).
- Not using Vercel for the CRM: the engine's sign-in only accepts Cloudflare Access.
- 2026-11-04 (Fran, after speaking with Sam): this CRM is Fran's own instance on Fran's server. It is not on Sam's master code and does not need Sam to manage it. Note: `memory/crm-independent-of-sam-2026-11.md`.
- 2026-11-04 (Fran): Access model for all tabs. Fran is admin everywhere. Each agent is listed only under the company they work for, with role `rep`. Tested on a temporary server copy (sign-in check stubbed): an agent gets one tab, is refused the others, and sees only contacts assigned to them.

## Checklist

- [x] 1a DigitalOcean account (Fran, 2026-11-01). Nothing created in it yet.
- [x] 1b Cloudflare: basicamericancrm.com added on the Free plan. Nameservers duke.ns.cloudflare.com and
      sharon.ns.cloudflare.com. Cloudflare imported GoDaddy's parked-page records (2 A, 2 CNAME, 1 TXT); remove later.
- [x] 1c GoDaddy: DNSSEC was off; nameservers changed to Cloudflare's (2026-11-01). Waiting for Cloudflare "Active" email.
- [x] Sign-in email: franmcgrath11@gmail.com (Fran, 2026-11-01).
- [x] 2a SSH key on this computer (~/.ssh/basic-american_crm, outside Drive)
- [x] 2b Droplet basic-american-crm (SFO3, Ubuntu 24.04, Basic Regular 1 GB, $6.00/month per DigitalOcean form, backups off), IP: 147.182.238.159
- [x] 2c Reached the server over SSH (2026-11-01)
- [x] 3a Zero Trust Free active ($0/month per Cloudflare checkout). Team domain: https://yellow-star-31c7.cloudflareaccess.com (Cloudflare chose it)
- [x] 3b Tunnel basic-american-crm (ID 27dfce98-91db-4fec-b5d8-7d84a58ded05); route crm.basicamericancrm.com -> http://localhost:8780. Token not saved anywhere.
- [x] 3c Access app "Fran CRM", policy "Fran" (Allow, email franmcgrath11@gmail.com), 24 h session, one-time PIN default. AUD: 889d072fe6db7917eafd9f1ba267fb089f898f6383965eee37b07419a4749269
- [x] 3d access.json written (no secrets)
- [x] 4 Installed 2026-11-01 (6/6 checks, code at /srv/basic-american/crm-20261102). Tunnel connected via a one-time server login (cert removed after; token never left the server). Port 8780 closed from outside; site redirects to Access. Fran signed in: All businesses with 4 tabs.
- [ ] 5 Hand-over (crm/ into the brain, CLAUDE.md IP)

## Open questions

- The guide says to email Sam at several points. No longer needed (decision 2026-11-04).

## Still to do

- Done 2026-11-01: One-time PIN (email code) added as a login method; the sign-in page shows an email box plus "Sign in with Cloudflare". A new rep still needs their email added to the "Fran" policy (or a new policy) and to the CRM profiles.
- Remove the GoDaddy parked-page records Cloudflare imported (A/CNAME/TXT on basicamericancrm.com).
- Optional: rename the team domain yellow-star-31c7; DigitalOcean backups (extra cost).
- 5: walk-through of the CRM; save to GitHub with Fran's yes.

## Local changes to the engine (Fran's own code; re-apply only if Fran ever takes a new release from Sam)

- 2026-11-01 (Fran): dashboard shows a permanent contact total. `command_center/portfolio_rollup.py`
  (`contacts` per business and in totals; "contacts in all" box; card shows Contacts and "New, 7 days")
  and `command_center/www/portfolio-rollup.css` (`.pr-stats` 2 columns). Deployed with refresh-portfolio.sh
  (code only), release crm-20261101-2247; previous releases kept on the server. Tested first on a copy.
  Deploy without Python: tar the crm/ folder (no private/, caches, databases), upload as release.tar.gz with
  deploy/refresh-portfolio.sh, run it with PORTFOLIO=basic-american SLUG=basic-american DATE=<stamp>.
- 2026-11-17 (Fran): bulk assign of contacts (a patch from Sam, applied by Fran's Claude Code on the desktop). New files:
  `command_center/contact_bulk.py`, `command_center/www/contact-bulk.js`, `command_center/www/contact-bulk.css`.
  Small additions to `strategy_work.py`, `crm_events.py`, `crm_page.py`, `server.py` and `www/crm.js`. No schema change.
  Command `contact.assign_bulk`, limit 5,000 contacts for each save, administrators only.
  First rep: Dustin Padgett (id dustin, dustin@ipostsports.com), role rep, listed in `ipost-sports.json` only;
  deployed with --sync-setup, release crm-20261117-2142 (confirmed in the server's ipost-sports.json only).
  Cloudflare Access: policy "Reps" (Allow, Emails = dustin@ipostsports.com) added to app "Fran CRM" by Fran,
  2026-11-17; the app now shows Fran + 1 other policy.
  Deployed with push_release.py --sync-setup, release crm-20261117-2139 (old release crm-20261101-2247 kept;
  backup /var/lib/crm/backups/pre-refresh-20261117-2139).
  Before the change: `crm-before-bulk-assign/` (a copy of crm/). Check: `check_bulk_assign.py`, 77 of 77 passed.
- 2026-11-17: the desktop has its own server key (~/.ssh/basic-american_crm, comment "basic-american crm desktop"),
  added to the server by Fran in the DigitalOcean console. Python 3.12 installed on the desktop. Both computers can deploy.

## Imports

- 2026-11-17: iPost Sports +374 Placer County HS coaches (Claude Projects/Placer HS Coaches/placer_hs_coaches_crm_ready.csv,
  378 rows cleaned from the 379-row unique-emails file). Done through the CRM's own Import contacts screen (in the
  app's browser pane; SSH from Claude was blocked). Source channel "Web research", Source campaign
  "Placer County HS coaches (2026-11-17)", duplicates skipped: 4 (3 Del Oro coaches' second team mailbox, and
  Daniel Carbajal, already one of Dustin's). 0 failed; the CRM made its own backup before the first new contact.
  All 374 assigned to Dustin Padgett with the bulk Assign button. Dustin now has 947 (573 + 374).

- 2026-11-17: iPost Sports alumni (3,457) removed with contact.delete (soft, restorable from the removed list); backup
  private/backups/ipost-sports-20261118T063222Z-before-alumni-removal.sqlite3. iPost now: Dustin's 573 only.
  Dustin's access verified on a throwaway copy of live: 14/14 (one tab, own 573 only, 403 elsewhere, no roll-up/export).
- 2026-11-17: Basic American Intelligence +1,182 iPost Sports alumni that BAI did not have by name or email
  (Fran: "all of the alumni" in BAI). Source campaign "iPost Sports alumni (copied 2026-11-17)". 455 of them
  share a phone number with an existing BAI contact (about 170 with the same last name): created anyway, may
  hold duplicates. BAI now 8,134. Backup: private/backups/basic-american-intelligence-20261118T063028016913Z-before-import.sqlite3.
- 2026-11-17: iPost Sports 573 Sacramento County HS coaches (Claude Projects/Sacramento HS Coaches,
  unique-emails file; titles cut to 60 characters, school in Address; Source channel "Web research",
  Source campaign "Sacramento County HS coaches (2026-11-03)"; 17 likely duplicates created anyway, mostly a
  shared office phone). All 573 assigned to Dustin Padgett with contact.assign_bulk. Run on the server with the
  engine's own crm_io import (tested first on a copy). Backup before import:
  private/backups/ipost-sports-20261118T061728595079Z-before-import.sqlite3. Placer County list: not found.
- 2026-11-01: iPost Sports 3,457 (alumni (1).csv). Basic American Intelligence 6,952 from the Loyola
  directory (Claude Projects/Loyola Alum CSV/For import, parts 1-3; "63 to 91" and name-only rows left out).
