# CRM install log (Fran)

No secrets in this file. Guide: `START-HERE.md`.

## Decisions

- 2026-11-01: The CRM gets its own new domain, **basicamericancrm.com** (bought at GoDaddy 2026-11-01).
  basicamericanenterprises.com keeps its DNS at GoDaddy and is never moved (Fran).
- 2026-11-01: Use Fran's existing Cloudflare account (franmcgrath11@gmail.com sign-in; it also holds oroliquidators.com).
- Address: https://crm.basicamericancrm.com. Kit files updated to it; `release.tar.gz` rebuilt with Sam's
  exact 193-file list (only `CLAUDE.md` text changed).
- Not using Vercel for the CRM: the engine's sign-in only accepts Cloudflare Access.

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

- The guide says to email Sam at several points. Do not, unless Fran says yes in chat.

## Still to do

- Done 2026-11-01: One-time PIN (email code) added as a login method; the sign-in page shows an email box plus "Sign in with Cloudflare". A new rep still needs their email added to the "Fran" policy (or a new policy) and to the CRM profiles.
- Remove the GoDaddy parked-page records Cloudflare imported (A/CNAME/TXT on basicamericancrm.com).
- Optional: rename the team domain yellow-star-31c7; DigitalOcean backups (extra cost).
- 5: walk-through of the CRM; save to GitHub with Fran's yes.

## Local changes to Sam's engine (re-apply after each new release from Sam)

- 2026-11-01 (Fran): dashboard shows a permanent contact total. `command_center/portfolio_rollup.py`
  (`contacts` per business and in totals; "contacts in all" box; card shows Contacts and "New, 7 days")
  and `command_center/www/portfolio-rollup.css` (`.pr-stats` 2 columns). Deployed with refresh-portfolio.sh
  (code only), release crm-20261101-2247; previous releases kept on the server. Tested first on a copy.
  Deploy without Python: tar the crm/ folder (no private/, caches, databases), upload as release.tar.gz with
  deploy/refresh-portfolio.sh, run it with PORTFOLIO=basic-american SLUG=basic-american DATE=<stamp>.

## Imports

- 2026-11-01: iPost Sports 3,457 (alumni (1).csv). Basic American Intelligence 6,952 from the Loyola
  directory (Claude Projects/Loyola Alum CSV/For import, parts 1-3; "63 to 91" and name-only rows left out).
