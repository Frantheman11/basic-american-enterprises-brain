# CRM: instructions for Claude


This folder is **Fran's CRM**: several businesses behind one sign-in, at **https://crm.basicamericancrm.com**.
It runs on Fran's own server (147.182.238.159). The records live on that server, never in this repo.

## What is yours and what is not

| Path | Whose | Change it? |
|---|---|---|
| `data/command-center/<business>.json` | yours: one profile per business (people, roles, pipelines, stages, regions, colors) | yes |
| `data/command-center/portfolios/basic-american.json` | yours: the tabs, their order, labels and colors, the owners | yes |
| `brand.css` and other brand files here | yours | yes |
| `command_center/` | the engine, from Custom Tailored Agents. `ENGINE-RELEASE.json` lists every file | **no**: the next release replaces it. Ask Sam for engine changes |
| `deploy/` | the deploy tools | no |
| `private/` | local test databases | never commit it |

## Run it on this computer

```bash
python -B -m command_center.portfolio serve --portfolio basic-american --root . --port 8880 --identity local
```

Open http://127.0.0.1:8880/. It signs you in as the profile's `access.local_person`. The databases it
makes go in `private/` and stay on this computer. Check the setup without starting it:

```bash
python -B -c "from command_center import portfolio; portfolio.load('basic-american', '.'); print('ok')"
```

## Common changes

- **A stage name or order:** on the live CRM, use Pipeline, Edit stages (it saves on the server at once).
  Once someone edits stages on the screen, the server's list wins over `pipeline.stages` in the profile.
  To change stages from here instead, edit `pipeline.stages` and ask Sam to clear the screen list.
- **A second pipeline in a business:** add `pipeline.pipelines` and a `pipeline` on each stage. Stage ids
  stay unique in the business. Every pipeline keeps at least one open stage.
- **A person:** add them to `people.members` in each business they work in, with `role` (`admin` sees all,
  `rep` sees their own, `territory_lead` sees their own plus their `territory`) and their sign-in `emails`.
  **Then ask Sam to add the email to the sign-in** (Cloudflare Access); without that they cannot get in.
- **Regions:** `crm.territories.regions` in each profile; a person's `territory` is `["WI"]` or `["region:west"]`.
- **A business:** the "+" button on the live CRM copies a business's setup into a new tab. It lives on the
  server; to keep it in this repo too, copy its profile and portfolio entry here.
- **Removing a business:** ask Sam. Deploying never removes a tab or its records; a business missing
  from this repo is kept on the server.

## Deploy a change

1. Check the setup (the command above) and run it on this computer.
2. Commit and push.
3. Dry run, then deploy:

```bash
python deploy/push_release.py --host 147.182.238.159 --key ~/.ssh/basic-american_crm --portfolio basic-american --slug basic-american --sync-setup
python deploy/push_release.py --host 147.182.238.159 --key ~/.ssh/basic-american_crm --portfolio basic-american --slug basic-american --sync-setup --apply
```

The server backs up every database first. If its checks fail, it puts the old version, the setup and
every database back by itself. It keeps its own sign-in values and any business added there with "+".

## Backups

Every night at 10:30 UTC the server copies every database to `/var/lib/crm/backups` (30 days kept).

## Never

- Commit `private/`, a database, a password, a token or `access.json`.
- Edit files on the server by hand. Change them here and deploy.
- Change `command_center/`. Ask Sam.

Help: Sam Briones.
