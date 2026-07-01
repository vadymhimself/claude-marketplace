---
name: custom-proposal
description: >-
  Generate a stunning, personalized Upwork "opportunity-map" proposal (a single
  self-contained HTML landing page) for a specific outreach lead — to convert a
  cold-email/LinkedIn reply into a booked demo. Use this whenever someone on the
  GigRadar team wants to build a custom proposal, lead magnet, "opportunity map",
  or bespoke pitch page for a prospect who replied to outreach — e.g. "build a
  proposal for this lead", "make the opportunity map for their company", "create the
  custom lead magnet for this reply", "turn this Smartlead/LGM reply into a
  proposal". It spawns sub-agents to research the lead, mine high-value Upwork
  jobs, match GigRadar case studies, and assembles a GigRadar-branded page with a
  live earnings calculator and an in-page HubSpot booking modal. Trigger even if
  the user doesn't say "skill" — if the goal is a per-lead GigRadar proposal page,
  this is the workflow.
---

# Custom Upwork Opportunity-Map Proposal

Produces one self-contained `proposal.html` for a single lead: their company, the
exact high-value Upwork jobs they're missing, a mocked-up Upwork profile, a live
earnings calculator, relevant GigRadar case studies, and a "Book a demo" modal.
Built to be **irresistible to a competent agency** and identical-quality every
time, so any teammate can run it.

The output is GigRadar-branded (bright, white/blue, Inter) but personalized with
the lead's real photo + company logo. It deploys to a shareable URL or sends as a
file/PDF.

## The pipeline

Run these as a team. Steps 1–2 are independent — **spawn them in parallel**.
Steps 3–5 you (the orchestrator) do once they return.

### 1. Recon sub-agent  → `references/recon.md`
Scrape the lead's website + LinkedIn into a dossier: company summary, core
services, positioning, brand voice, and — critically — **extract their real
headshot + company logo** (sites often inline these as base64; recipe in the
reference). Save assets to a working dir and report file paths.

### 2. Opportunity-miner sub-agent  → `references/opportunity-mining.md`
Query GigRadar's live ES `metajob` index for US clients hiring in the lead's
service lines. Filter HARD for quality (verified, deep-pocketed clients, real
budgets) and return ~9 cherry-picked jobs with a "why it fits → how it expands"
note each. **Lead only with the top tail — never low-ballers.** (Reuses the same
ES recipe as the `gigradar-gm:market-research` skill.)

### 3. Earnings config  → `references/earnings-model.md` + `references/calc-core.mjs`
Pick the lead's niche row from the benchmark table (reply-rate scenarios, close
rates, avg contract value, GigRadar cost). These feed the calculator. The math is
the verbatim GigRadar DFY engine — do not invent numbers.

### 4. Assemble `lead-data.json`
Fill the schema in `assets/template-schema.json` with everything from steps 1–3
(hero, standing/services, opportunities, profile mockup, calculator config).
- Reference images as `"@file:<path>"` (the build script inlines them as base64 —
  never paste base64 by hand).
- Set `serviceTags` to the lead's niches using the taxonomy in
  `assets/case-studies/case-studies.json` (e.g. `["lead-gen","email-marketing",
  "revops"]`). The build script auto-selects the most relevant case studies by
  tag overlap — no manual case-study picking needed.
- Leave `demoUrl` as the GigRadar HubSpot link (already the default).

### 5. Build, verify, deliver
```bash
cd <skill>/scripts
python3 build.py --lead <path>/lead-data.json --out <path>/proposal.html
```
Then **verify** by rendering to PDF and eyeballing every section (the screenshot
tool renders fresh-at-top, so use headless Chrome → PDF → read it):
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --virtual-time-budget=6000 --no-pdf-header-footer --print-to-pdf=<path>/proposal.pdf \
  --no-sandbox "file://<abs path>/proposal.html"
```
**Publish** to a shareable, tracked URL:
```bash
scripts/deploy.sh <proposal.html> <lead-slug>   # → https://<lead-slug>.via.gigradar.io
```
This uploads to the `gigradar-proposals` Cloudflare Worker (R2-backed) and needs
your `GIGRADAR_PROPOSAL_TOKEN` (per-user push token — provision via
`ai-researcher-users` with `proposals_push: true`). Every published page
auto-reports engagement to **PostHog** (project GigRadar), keyed by the slug:
`proposal_viewed`, `scroll_depth`, `section_view`, `book_demo_click`,
`demo_modal_open`, `calculator_used`, `opportunity_click`, `case_study_click` —
so you see exactly who opened it and how far they got. (Analytics only fire on the
live host, never on local preview or PDF.)

## Quality bar (do not skip — this is why it converts)
- **No low-baller opportunities.** Every job shown must be a verified, high-spend
  US client with a real budget and an expansion path. A sharp agency will judge
  GigRadar by the worst job on the page.
- **Use the lead's real face + logo.** A monogram placeholder is a fallback, not
  the goal. If the site has nothing usable, try Clay enrichment for a LinkedIn
  photo before settling.
- **Be honest in the numbers.** The calculator is a projection (label it). Don't
  fake per-client star ratings on case studies — GigRadar only has a site-wide
  Trustpilot score. Don't show a wrong logo (e.g. a site's WordPress favicon).
- **Match their brand in the hero only** (photo/logo); the page itself is GigRadar
  brand. The point is "GigRadar built this *for you*".

## Files
- `assets/template.html` — the data-driven template (`{{LEAD_JSON}}` slot).
- `assets/template-schema.json` — the contract for `lead-data.json` (every field
  + an example). Read this before filling.
- `assets/example-remote-guyana.json` — a complete worked example.
- `assets/case-studies/` — the ~22-study library (`case-studies.json` + images),
  tagged for relevance matching.
- `scripts/build.py` — fills the template (image embed + case-study selection).
- `scripts/deploy.sh` — deploy to Netlify.
- `references/` — recon recipe, opportunity-mining recipe, earnings model + calc
  engine, GigRadar brand tokens.

## Tips
- Keep `proposal.html` self-contained (the build script inlines all images) so it
  works emailed, hosted, or printed to PDF.
- The case-study grid shows ~6 of the library; raise `--cases` to show more.
- To preview locally, just open the HTML — the booking modal and calculator are
  live; the HubSpot scheduler needs internet.
