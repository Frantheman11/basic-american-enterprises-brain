# Leave GoDaddy except the domains (started 2026-11-01)

Fran's goal, in chat 2026-11-01: replicate the Basic American Networks site and host it on Vercel,
move email to another email server, then cancel everything at GoDaddy except the domains.
Domains and DNS stay at GoDaddy. Nothing is cancelled or sent without Fran's yes in chat.

## Where things stand (2026-11-01)

- basicamericanenterprises.com: moved to Vercel (team ipostsports, project bae-website). Done.
- GoDaddy "Web Hosting Deluxe" (listed under basicamericanenterprises.com), **auto-renews 11/11/2026**.
  It still serves **basicamericannetworks.com** (A record 72.167.58.141, title "Basic American Networks").
  Cancelling it now would take that site offline.
- Email for fran@basicamericanenterprises.com: Microsoft 365 bought through GoDaddy ("Microsoft 365 Secure Online Essentials", **auto-renews 11/11/2026**, same day as the hosting; do NOT turn off its auto-renew until mail runs on the new provider)
  (TXT NETORGFT12518801.onmicrosoft.com), filtered by Proofpoint (MX mx1/2/3-usg1.ppe-hosted.com).
  Other Microsoft 365 records: autodiscover, msoid, lyncdiscover, sip, two SRV.
  Full DNS copy: projects/BAE-website/dns-before-vercel-2026-11-01.md.
- Other GoDaddy domains point elsewhere (not on this hosting): iotdatagroup.com, schmaa.com,
  laolympiad.com, basicamerican.enterprises, basicamericanintelligence.com, gametapp.com, aiidiot.com, hinzu.net.
  schmaa.com auto-renews 11/3/2026 (a domain; keep unless Fran says otherwise).

## Steps, in order

1. **Basic American Networks to Vercel.** (2026-11-01: text and 19 images captured in projects/BAN-website/SOURCE-2026-11-01.md and site/assets; clean three-page copy built in projects/BAN-website/site, previewed locally; old Contact page was template filler, replaced with config facts. Vercel project ban-website (team ipostsports) created and deployed: https://ban-website-eight.vercel.app. DNS pointed to Vercel 2026-11-01 (backup in projects/BAN-website/dns-before-vercel-2026-11-01.md); waiting for the old records to expire.) Copy only the visible text and images from
   basicamericannetworks.com (never the old WordPress files: the old GoDaddy sites were hacked).
   Build a clean static site, new Vercel project in team ipostsports, Fran reviews it on a vercel.app
   address, then point basicamericannetworks.com DNS at Vercel (website records only).
   Done when basicamericannetworks.com loads from Vercel over https.
2. **Before 11/11/2026:** if step 1 is not done, turn off auto-renew on Web Hosting Deluxe or renew
   once, with Fran's yes, so the BAN site does not go dark.
3. **Email.** (2026-11-01: Fran keeps Outlook. Desktop Office is licensed by Fran's personal Microsoft 365 under frantheman11@hotmail.com, not GoDaddy, so Business Basic is enough. The GoDaddy tenant also holds OneDrive and SharePoint Sites for basicamericanenterprises.com, which must move too; recommended: transfer the existing tenant out of GoDaddy to a direct Microsoft subscription so mail and files stay in place.) Fran picks the new provider. Likely options: Microsoft 365 directly from Microsoft
   (same mailbox and Outlook, moved out of GoDaddy's account) or Google Workspace. Plan: check GoDaddy's
   Microsoft 365 renewal date, set up the new provider, copy mail, change MX/TXT/autodiscover records,
   test sending and receiving, keep the old service until mail flows. Fran's sign-ins and any
   payment are Fran's to enter.
4. **Cancel at GoDaddy, everything except domains:** Web Hosting Deluxe, Microsoft 365 / email,
   Proofpoint email security, any SSL or website security add-ons. Keep every domain and its
   Full Protection. Clean up DNS leftovers (admin, mail, cpanel, whm, webdisk records).

## Next brief

When a step finishes, update this file and log.md; when all four are done, mark it complete here.

## Email transfer plan (Fran chose "transfer the same account", 2026-11-01)

Keep the same Microsoft 365 tenant (NETORGFT12518801.onmicrosoft.com), mailbox, OneDrive and Sites;
move billing and control from GoDaddy to Microsoft ("defederation"). Order matters: wrong order can delete users.
1. Back up first: export the mailbox to a .pst from Outlook; copy anything important out of OneDrive and Sites.
2. GoDaddy releases the tenant: Fran asks GoDaddy support for a Microsoft 365 defederation and for
   Global Admin access (Fran's call or chat; Claude may draft the words, Fran sends).
3. Global Admin removes the federation (PowerShell, domain authentication to Managed), then resets the
   user password (Fran types it).
4. Fran buys Microsoft 365 Business Basic directly from Microsoft in the same tenant and assigns it to
   fran@basicamericanenterprises.com.
5. Remove GoDaddy's delegated admin access.
6. DNS at GoDaddy: MX to Microsoft (basicamericanenterprises-com.mail.protection.outlook.com), SPF to
   include spf.protection.outlook.com, drop Proofpoint (ppe-hosted) and secureserver.net once mail flows.
7. Test sending and receiving, then cancel GoDaddy's Microsoft 365 and add-ons. If this runs past
   11/11/2026, let GoDaddy's email renew once rather than lose mail.
Sources read 2026-11-01: Microsoft Q&A on GoDaddy defederation; T-Minus365 "Defederating GoDaddy 365";
O365CloudExperts defederation guide. Check each step against Microsoft's own docs before running it.

## Found in GoDaddy billing (2026-11-01)

- Microsoft 365 Secure Online Essentials (fran@basicamericanenterprises.com) and Web Hosting Deluxe both
  renew **Nov 12, 2026** (the products page says 11/11). GoDaddy shows **"Payment Issue: We may not be able
  to renew your subscription because there's an issue with your payment profile"** on the Microsoft 365.
  Fran fixes the payment profile (Manage Payment) or the email may lapse at renewal.
- Price, from search results (GoDaddy's own page not opened): about $14.99 per mailbox per month at
  renewal ($179.88 a year), $17.99 month to month. Microsoft 365 Business Basic direct: about $7 per user
  per month paid yearly ($8.40 monthly). Both unverified against the live pricing pages.
- Other GoDaddy renewals to review later: Standard SSL for basicamericannetworks.com (Nov 2, 2027; not
  needed on Vercel), Auctions Membership (Apr 26, 2027), schmaa.com (Nov 3, 2026).

## Plan choice (Fran, 2026-11-01)

Fran only needs email and keeps the existing desktop Outlook (licensed by Fran's personal Microsoft 365,
frantheman11@hotmail.com). Leading choice: **Exchange Online (Plan 1)** bought from Microsoft in the same
tenant after defederation (about $4 per user per month paid yearly, from reseller and comparison sites;
confirm at checkout). Same mailbox, Outlook keeps working after one re-sign-in. Plan 1 has no OneDrive or
Sites, so copy anything wanted out of the basicamericanenterprises.com OneDrive and Sites first.

## Backups in progress (2026-11-01)

- Mailbox: full .pst backup of fran@basicamericanenterprises.com being made in
  C:\Users\Fran McGrath\Documents\Outlook Files\ (first attempt used the old 2 GB format and missed
  Calendar, Contacts and Drafts; the "-full" file replaces it), then copied to G:\My Drive\Backups\Email\.
- Business OneDrive: 2,739 files, about 5.5 GB. Zip downloads in the iPost-managed Chrome never saved.
  OneDrive app installed with winget 2026-11-01 to copy them to G:\My Drive\Backups\OneDrive-basicamericanenterprises\.
  **Fran does not want the OneDrive app kept on the laptop: uninstall it (and remove the business account and
  its local copy) once the copy is verified.**
- Final home for both backups (Fran, 2026-11-01): one folder, **G:\My Drive\BAE Backup 2026-11-01\**, with the
  mailbox .pst and the OneDrive files keeping their own subfolders. The temporary
  G:\My Drive\Backups\OneDrive-basicamericanenterprises\ is moved into it and removed.

## Backups done (2026-11-01)

- **G:\My Drive\BAE Backup 2026-11-01\** holds everything:
  - fran-basicamericanenterprises-email-backup-2026-11-01.pst (2.6 GB, exact copy checked by SHA-256; every
    mailbox folder including Calendar 208, Contacts 57, Drafts 37; only Outlook's "Sync Issues" logs left out).
    A second copy stays at C:\Users\Fran McGrath\Documents\Outlook Files\...-full.pst until Drive has uploaded it.
  - OneDrive files\ (2,738 of 2,740 files, 5.0 GB, original subfolders). Not copied: one OneDrive system file and
    "Prolific West Promissory Note to Blazevich (FINAL 08 12 22) (1).docx" (failed to download; Fran: not needed).
- SharePoint Sites (root, basicamericanenterprises.com, allcompany, contentTypeHub): no documents, nothing to back up.
- OneDrive app uninstalled; the local business OneDrive folder and its account entry removed. Fran's personal
  OneDrive folder (frantheman11@hotmail.com) untouched. Files still in OneDrive online are left alone; they go when
  the tenant moves to Exchange Online Plan 1.
