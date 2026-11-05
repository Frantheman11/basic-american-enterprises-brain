---
name: crm-independent-of-sam
date: 2026-11-04
source: Fran, in chat, after speaking with Sam Briones that day
company: Basic American Enterprises (CRM covers all four companies)
---

Fran's CRM (crm.basicamericancrm.com) is a separate instance that Fran owns and runs. It runs on Fran's
own server, and Fran modifies the code. It is not meant to be on Sam's master code and does not need
Sam to manage it. Fran and Sam are separate businesses, partners on several levels.

How to apply: `projects/CRM/` is Fran's code, including `command_center/`. Engine changes are made there
and deployed. No step needs Sam. Sam's releases are not installed automatically; taking one is Fran's
choice and it would overwrite local engine changes unless they are re-applied.

Checked 2026-11-04 (read-only): the server's only SSH key is Fran's, root is the only login user, and
no scheduled job or engine code calls back to Sam. Who has access to the Cloudflare account was not checked.

This is about the CRM only. The email hold on Sam in the send-email skill is a separate rule and stays.
