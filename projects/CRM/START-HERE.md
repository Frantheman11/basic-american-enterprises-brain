# Instructions for Claude: help Fran set up and install his CRM

> **Fran, 2026-11-01:** the CRM uses its own new domain, **basicamericancrm.com** (bought at GoDaddy
> 2026-11-01), not basicamericanenterprises.com. That domain's DNS stays at GoDaddy and is never moved.
> The new domain has no email and no website, so in 1b skip checks 1-3 and in 1c DNSSEC is already off.
> Do not contact Sam without Fran's yes in chat.

You are Claude Code, helping **Fran McGrath** put his new CRM online, on his own server and accounts,
at **https://crm.basicamericancrm.com**. Custom Tailored Agents built it; this folder holds everything you need. Nobody
else has to be involved. At the end, you also become the one who helps him change it.

## How to work with Fran

- He is a busy executive, not an engineer. Short, plain sentences. Explain why only when it helps.
- **One step at a time.** Give a step, wait until he says it is done or sends a screenshot, then go on.
  Ask for a screenshot whenever you are not sure what he sees. Website menus change: find the nearest match.
- **Never ask for a password, a card number or a login code, and never write one down.** He types those
  into the websites himself. The one secret you handle is the tunnel command in Phase 3: run it on the
  server once, and never save it in a file, a commit or a note.
- Do only what this guide says. Never delete anything on his accounts. Never change his email settings.
- If a step fails twice, stop and help him email **sam@customtailoredagents.com** with what you tried and the last lines
  of output. Do not improvise around a failure on the server.
- Keep a running checklist in `INSTALL-LOG.md` in this folder (no secrets in it).

## What is in this folder

| Path | What it is |
|---|---|
| `crm/` | his CRM: the engine, his setup (tabs: BAE, Basic American Intelligence, Basic American Networks, iPost Sports), the deploy tools, and `CLAUDE.md` for later |
| `crm/portfolio-spec.json` | the short description his setup was built from |
| `release.tar.gz` | `crm/` packed for the server (made for you; rebuild only if you change `crm/`) |

## Before you start, check

- You are **Claude Code** with a terminal (this guide runs commands). If you are a chat without a
  terminal, tell Fran: "Please open this folder in Claude Code; I can't run the install from a chat."
- `ssh -V` works in the terminal (it ships with Windows 10+, macOS and Linux).
- Tell him: "About an hour, in 5 phases. Your domain stays at GoDaddy; only its DNS moves to Cloudflare.
  It costs about $6 a month for the server. Ready?"

---

## Phase 1 · Accounts (20 min)

**1a. DigitalOcean.** digitalocean.com, Sign up, add his card. Don't create anything yet.

**1b. Cloudflare.** cloudflare.com, Sign up (free). **Add a domain**: `basicamericancrm.com`, **Free** plan.
Cloudflare scans and shows the DNS records it found. **You check them, carefully:**
1. Ask: "Do you get email at an @basicamericancrm.com address? Where is it hosted (GoDaddy, Microsoft 365, Google)?"
2. If yes, the list **must** have its **MX** records (and usually a **TXT** with `v=spf1`). If they are
   missing, **stop**: help him email sam@customtailoredagents.com before going on. His email depends on it.
3. The website should have an **A** or **CNAME** record for `basicamericancrm.com` and for `www`.
4. Set every record that is there to **DNS only** (grey cloud), so his site and email behave as today.
5. Write down the **two nameservers** Cloudflare shows (`something.ns.cloudflare.com`).

**1c. GoDaddy.** godaddy.com, My Products, `basicamericancrm.com`, DNS.
1. **DNSSEC first:** if it is on, turn it off, then **stop for a day** (Cloudflare advises waiting about
   24 hours before changing nameservers). Continue tomorrow from 1c.2.
2. **Nameservers**, Change, **I'll use my own nameservers**, enter Cloudflare's two exactly. Save.
3. Cloudflare emails him when the domain is **Active** (minutes to a day). Phase 2 can go on meanwhile;
   Phase 3 needs it Active.

## Phase 2 · His server (15 min)

**2a. A key for this computer** (lets you, and later his Claude, reach the server; no password involved):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/basic-american_crm -N "" -C "basic-american crm"
```

Show him the contents of `~/.ssh/basic-american_crm.pub` (the public half; safe to share). Never show the other file.

**2b. Create the server** in DigitalOcean: **Create**, **Droplets**.
- Region: the one nearest him (for California, **San Francisco**).
- Image: **Ubuntu 24.04 (LTS)**.
- Size: **Basic**, **Regular**, the **$6/mo** one (1 GB).
- Authentication: **SSH Key**, **New SSH Key**, paste the `.pub` contents, name it `basic-american crm`.
- Hostname: `basic-american-crm`. Backups: his choice (it shows the extra cost). Create.
- Write the server's **IP address** in `INSTALL-LOG.md`.

**2c. Check you can reach it:**

```bash
ssh -i ~/.ssh/basic-american_crm -o StrictHostKeyChecking=accept-new root@<IP> "uname -a"
```

## Phase 3 · The sign-in and the tunnel, in Cloudflare (20 min; the domain must be Active)

**3a. Zero Trust.** In Cloudflare, open **Zero Trust**. Pick a **team name** (for example `basic-american`).
Choose the **Free** plan; it asks for a card and does not charge it on Free. Write down the team domain:
`https://<team name>.cloudflareaccess.com`.

**3b. The tunnel** (how the website reaches the server without opening it to the internet):
Networks, **Tunnels**, **Create a tunnel**, **Cloudflared**, name `basic-american-crm`. On the install screen,
choose **Debian** / 64-bit. It shows a command with a long token (`cloudflared service install eyJ...`).
**Have him copy only that command into this chat. Do not run it yet; keep it for Phase 4 and never save it.**
Next: add a **public hostname** (or "route"): subdomain `crm`, domain `basicamericancrm.com`, service type **HTTP**,
URL `localhost:8780`. Save.

**3c. The sign-in rule:** Access, **Applications**, **Add an application**, **Self-hosted**.
- Name: `Fran CRM`. Domain: `crm.basicamericancrm.com`. Session: 24 hours.
- Policy: name `Fran`, action **Allow**, include **Emails**: `franmcgrath11@gmail.com`.
- Login method: **One-time PIN** (a code to his email; nothing to set up).
- Save. Open the application again and copy its **Application Audience (AUD) Tag** (a long hex string;
  not a secret) into `INSTALL-LOG.md`.

**3d. `access.json`** in this folder (no secrets in it):

```json
{"identity": "cloudflare_access", "team_domain": "https://<team name>.cloudflareaccess.com",
 "aud": "<the AUD tag>", "origins": ["https://crm.basicamericancrm.com"]}
```

**If he will sign in with a different email than `franmcgrath11@gmail.com`:** change it in every
`crm/data/command-center/*.json` (`people.members[].emails`) and in the 3c policy, then rebuild
`release.tar.gz`: `tar -czf release.tar.gz --exclude=private -C crm .`

## Phase 4 · Install (10 min)

```bash
ssh -i ~/.ssh/basic-american_crm root@<IP> "mkdir -p /root/crm-install"
scp -i ~/.ssh/basic-american_crm release.tar.gz access.json crm/deploy/install-portfolio.sh root@<IP>:/root/crm-install/
ssh -i ~/.ssh/basic-american_crm root@<IP> 'cd /root/crm-install && chmod +x install-portfolio.sh && PORTFOLIO=basic-american SLUG=basic-american PORT=8780 DATE=$(date +%Y%m%d) ./install-portfolio.sh'
```

(Single quotes on purpose: the date is the server's. On Windows, Claude Code's shell is Git Bash, where this works as written.)

It prints 6 steps and ends with `6/6 done`. It refuses to finish unless the CRM answers and turns away
anyone not signed in. Read every line; if it stops, show Fran the last lines and see "If it fails".

Then the tunnel (paste the command from 3b, with `sudo` dropped because you are root):

```bash
ssh -i ~/.ssh/basic-american_crm root@<IP> "cloudflared service install <TOKEN FROM 3b>"
```

Forget the token now. Wait a minute, then check from this computer:
- `https://crm.basicamericancrm.com` in his browser: Cloudflare asks for his email, sends a code, then **his CRM opens**
  on the All businesses page with his tabs.
- `curl -m 5 http://<IP>:8780/` must **fail** (the server is closed to the internet; only the tunnel reaches it).

## Phase 5 · Hand it to his own Claude (10 min)

1. Put `crm/` into his Second Brain repo (ask him which folder; it is a git repo), as `crm/`. Commit
   and push. `crm/.gitignore` already keeps databases, `access.json` and `release.tar.gz` out.
2. In `crm/CLAUDE.md`, replace every `<server IP>` with the real IP.
   Commit. From now on, any Claude opened in that repo reads it and knows how to change and deploy his CRM.
3. Walk him through it for 5 minutes: the tabs, All businesses, BAE's two pipelines (the switch above the
   board), adding one contact with a state, and Export this business (account menu).
4. Tell him what he can now ask you: rename a stage, add a pipeline, add a rep (then add their email in
   the Cloudflare policy too), change colors, set his regions. You change `crm/`, check it, and deploy
   from inside `crm/` with `python deploy/push_release.py --host <IP> --key ~/.ssh/basic-american_crm --portfolio basic-american --slug basic-american --sync-setup --apply` (dry run first, without `--apply`; it needs Python on his computer).
5. Help him send a short note to sam@customtailoredagents.com: "My CRM is live at https://crm.basicamericancrm.com."

## If it fails

- **Phase 4 stops:** the message says which check failed. Run
  `ssh -i ~/.ssh/basic-american_crm root@<IP> "journalctl -u crm -n 40 --no-pager"` and read it with him.
- **`... exists; pick another DATE`:** rerun with `DATE=$(date +%Y%m%d)-2` (inside the single quotes).
- **`... already has a live portfolio`:** the install already worked once; do not rerun it. Check the tunnel.
- **The website says the host is not found:** the domain is not Active in Cloudflare yet, or the public
  hostname in 3b is missing. Check both.
- **Signed in, but "not let in":** the email he used is not in `crm/data/command-center/*.json`. Fix as in 3d.
- Anything else, or the same failure twice: stop and email sam@customtailoredagents.com with what you saw.
