# Basic American Enterprises website: project context

Fran McGrath and Sam Briones (Custom Tailored Agents) worked out this website over calls on
2026-10-01, 10-13 and 10-14. This file is the summary of those calls, so a new Claude session
can continue the work without having heard them.

## The business in one picture

```
          Basic American Enterprises (BAE)  = the incubator, holds every contract
          represents: Custom Tailored Agents, Clear Insights AI, iPost Sports, IoT Data Group, ...
           red = DISTRIBUTION                         blue = CAPITAL
                 |                                          |
   3 regional leads                                strategic investors ("smart money")
   West = Long Beach, Central = Austin TX,         matched to each portfolio company
   East = Philadelphia
                 |
   1 master agent per state
                 |
   sub-agents under him
                 |
   customers, tagged to their rep; a rep sees only his own territory's leads
```

- **Basic American Intelligence (BAI)** = an AI media center: podcast, webinars, an open forum
  for AI builders. It sells nothing. Builders who want BAE to represent them interview on the
  podcast first. Its own two or three page site, later.
- **Basic American Networks (BAN)** = the existing telecom site, basicamericannetworks.com.
  Keep it and move it with the others.
- Fran's line: "smart money invests in smart American businesses". Always say "smart money"
  and "smart companies".

## Decisions made on the calls

| Topic | Decision |
|---|---|
| Order on the page | Follow the current live site: hero, map, team, portfolio, contact |
| Two halves | Distribution first (red), capital second (blue) |
| The map | The main feature, high on the page. States pop on hover. Clicking a state shows that state's reps by name and a rep sign-up |
| Reps | None signed yet, so every state shows "Open territory. Be the first." |
| Rep pitch | Reps offer every product BAE represents and earn subscription and referral commissions, paid monthly under a rep agreement. The CRM is the rep portal |
| Who we look for | American business salespeople and independent agents, tech sales or business backgrounds, experience in area distribution and capital formation |
| Two kinds of rep | Network reps (handshakes, trade shows, their own state) and social media reps (can sell anywhere). Both welcome |
| Smart money | Investment from businesses whose interests line up with the company raising it. iPost: sports apparel, sporting goods, energy drinks. IoT Data Group: telecom providers, systems integrators, low voltage contractors. CTA: a private equity group that runs the system across its portfolio. All portfolio companies are open to investment |
| Portfolio | Every company on the current site, in one clean logo grid |
| Team | Bios and photos from the current site for now |
| BAE logo | Fran asked for new options. Three are in `review/bae-logo-options.html`: A star shield, B stripe wordmark, C seal |
| BAI look | Red, white and blue plus black, purple and gold. New BAI logo still to make |

## Design rules (keep these)

- **Light site.** Whites and grays with red and blue accents. Fran does not like dark websites.
  Dark only inside photos and logo tiles.
- **Buttons are blue and white, never red.** Red reads as negative on a button. Red is an
  accent only: the distribution band, small labels, numbers, stripes.
- Fonts: Archivo (wide, bold) for headings, Inter for body text, from Google Fonts.
- **No investment numbers, returns, rates or "guaranteed".** The footer has a placeholder
  disclaimer for Fran's attorney. Keep it until the attorney approves the wording.
- **Never invent reps.** The `REPS` list in `site/assets/bae.js` stays empty until real people sign.
- The draft has no backend: buttons open a note that says what the live site will do.

## How the site was built (three rounds)

| Round | Date | What changed |
|---|---|---|
| 1 | 2026-10-01 | First concept, investor-first, rebuilt on a light base the same night |
| 2 | 2026-10-14 | Distribution first (red), capital second (blue) |
| 3 | 2026-10-14 | Fran's answers to the 14 questions. Map moved right under the hero. Live-site order. Three logo options |

## Still open

- The rep tagline (Become a rep section). Fran writes it.
- Fran's written "distribution" piece and his capital section. Each one replaces our draft copy.
- The logo pick (A, B, C or a mix). Then make it a clean SVG for the nav, footer and browser tab.
- One-line descriptions for BAN, Block Battery and BlackMP in the portfolio.
- The amber dashed "Waiting on Fran" boxes on the page mark each gap. Remove a box when its text lands.

## Going live (later, plan only)

- Recommended: **Cloudflare Pages on Fran's own Cloudflare account**, plus a small Cloudflare
  Worker and database for the rep list and the sign-up form. The free tier covers it.
- Domains stay at GoDaddy; only the DNS records change.
- Rebuild clean. Never copy the old WordPress files: the old sites were hacked on GoDaddy.
- Cancel the GoDaddy hosting only after the new site is live, and before its renewal date.
- Coordinate the launch with Sam so the sign-up form connects to the CRM.

## Files

- `site/index.html`: the page (all the words).
- `site/assets/bae.css`: colors and layout. The round-two and round-three styles are at the end.
- `site/assets/bae.js`: the map, the state panel and the `REPS` list.
- `site/assets/us-states.js`: the 50 state outlines (public domain), tagged with the three regions.
- `site/assets/*.png, *.jpg, *.webp`: images, logos and headshots.
- `review/bae-logo-options.html`: the logo choices. Not part of the website.
- `original-images/`: untouched files from the current site.
