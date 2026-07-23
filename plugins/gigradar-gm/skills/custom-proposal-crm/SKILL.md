---
name: custom-proposal-crm
description: >-
  Generate a personalized Upwork "rank + competitor-gap" proposal (a single
  self-contained HTML landing page) for a specific outreach lead, built to convert a
  cold-email/LinkedIn reply into a self-serve GigRadar CRM free trial (primary), with
  a booked demo as the secondary path. This is the CRM/rank variant of custom-proposal:
  it adds a live competitor-gap section fed by GigRadar's Upwork rankings data (their
  real MRR + rank + the agencies ahead of them), and flips the primary CTA from "book a
  demo" to "start a 10-day free trial". Use this whenever the GigRadar team wants a
  per-lead proposal whose hook is "here's exactly where you rank and who's ahead of you,
  close the gap with GigRadar" and whose primary ask is the CRM trial — e.g. "build the
  rank-gap proposal for this lead", "make the CRM-trial opportunity map", "turn this
  reply into a competitor-gap page", "build the crmranktrial proposal". For the classic
  demo-first opportunity map without the rank section, use custom-proposal instead.
  Trigger even if the user doesn't say "skill".
---

# Custom Upwork Rank + Competitor-Gap Proposal (CRM-trial variant)

Produces one self-contained `proposal.html` for a single lead: their company, **their
live Upwork rank and the competitors earning more than them in their niche**, the exact
high-value Upwork jobs they're missing, a mocked-up Upwork profile, a live earnings
calculator, relevant GigRadar case studies, and a **dual CTA: "Start a 10-day free
trial" (primary, GigRadar CRM) + "Book a demo" (secondary, HubSpot modal)**. Built to
be **irresistible to a competent agency** and identical-quality every time.

The narrative spine differs from the base skill: instead of "here are jobs you're
missing, book a demo", it is **"here's where you rank, here's who is ahead of you, and
here's exactly how to close the gap, start free today"** — the gap being **throughput
(Auto Bidding)** and **response speed / deal tracking (GigRadar CRM)**.

The output is GigRadar-branded (bright, white/blue, Inter) but personalized with
the lead's real photo + company logo. It deploys to a shareable URL or sends as a
file/PDF.

## What's different from `custom-proposal` (read this)
- **New section 1b — competitor gap.** Right after "where you stand", a dark rank block:
  the lead's live MRR + rank + percentile, a leaderboard of the agencies above them in
  their niche, and a "close it" block tying the gap to **Auto Bidding + GigRadar CRM**.
  Fed by **`scripts/gap_miner.py`** (direct ES on `profile-agency` / `profile-contractor`;
  recipe in `references/rankings.md`). All figures are REAL ES records.
- **Dual CTA, trial-primary.** The primary CTA everywhere is `Start a 10-day free trial`.
  It **scrolls to the in-page trial funnel** (`#trial`, section 9) rather than linking out
  (falls back to `crmCta.url` if the funnel is absent). `Book a demo` (HubSpot modal) is
  secondary. Driven by `crmCta` / `demoCta`. `build.py` refuses to build without `crmCta.url`.
- **Rank-product links.** The hero "See your full rank" button, every competitor row, and a
  final CTA button deep-link to the lead's page on `www.upworkrank.com` (from `gap_miner.py`
  `rankUrl` + per-row `url`). PostHog `rank_click`.
- **Section 8 — CRM "biggest weakness".** A dark hard-sell block (partial
  `assets/partials/crm-weakness.partial.html`, included via `<!--INCLUDE:...-->`): the lead's
  ~4h estimated response time, a "99% reply too late" chart (15-min ideal vs 3h+), the hosted
  CRM demo video (shared S3) side-by-side with a live CRM animation, feature popovers, and two
  YouTube follow-up embeds (manual vs agentic). Mostly static; `crmWeakness.responseTimeHours`
  is the only per-lead knob.
- **Section 9 — trial funnel** (partial `assets/partials/crm-trial-funnel.partial.html`, id
  `#trial`): the CRM pricing + capture + Stripe **trial** funnel ported from gigradar.io/crm,
  firing the real PostHog checkout events. The capture form **never disqualifies** (every lead
  reaches Stripe) and asks only what we don't know (# Upwork accounts, avg company revenue);
  all other fields are hidden + pre-filled from **`trialPrefill`** (the lead's known
  name/email/company/website/upworkUrl/country/phone from the outreach DB + rank data), and on
  submit it `posthog.identify()`s the lead so the session is stitched to a known person.
- **Conditional founder CTA.** `Book a call with the Founder` renders into `.founder-slot`s
  **only** when `founderCta` is present — the assembler includes it ONLY for leads with
  **MRR > $10k AND a Tier-1 country** (mirrors gigradar-website `getCountryTier`; excludes
  India/Pakistan/Bangladesh/Philippines etc). PostHog `founder_call_click`.
- **New PostHog events**: `trial_click`, `rank_click`, `founder_call_click`, plus the funnel's
  `crm_checkout_*`, and a `posthog.identify` on trial submit.

## The pipeline

Run these as a team. Steps 1–2 are independent — **spawn them in parallel**.
Step 3 (cover-letter sub-agent) spawns the moment the twin jobs are picked and runs
**in parallel** with your own steps 4–5. Steps 4–6 you (the orchestrator) do.

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

### 2b. Gap-miner (rank + competitors)  → `competitorGap` block
Run **`scripts/gap_miner.py`** against the live rankings indices to build the section-1b
data. It resolves the lead's own Upwork agency/freelancer profile, computes their **MRR
(= recentEarnings / 6), rank-in-scope and percentile**, and pulls the competitors ranked
above them in their niche. Recon (step 1) must capture the lead's real company name (and,
ideally, their Upwork profile URL) so this can resolve the right record.
```bash
export ES_URL=… ES_USER=… ES_PASS=…    # metajob-ro also reads profile-*
python3 scripts/gap_miner.py --name "<Agency Name>" \
  --country "<Display Name>" --service <service-slug> --top 10 --emit-json > gap.json
```
It emits a `competitorGap` object with the real `board`/`stats`/`rankLabel` filled and an
`_gap_facts` summary; **you** write the persuasive `headline` / `lede` / `closeWhy` and the
two `fixes` cards (Auto Bidding + GigRadar CRM) grounded in those numbers — then drop the
object into `lead-data.json`. Recipe, entity/scope options, and the honesty rules are in
`references/rankings.md`. Runs **in parallel** with steps 1–2. Never invent a competitor,
MRR, JSS, or rank — every row is a quoted ES record. Delete `_gap_facts` before building.

### 3. Proposal-tool cover letters (Sonnet + KB)  → `references/proposal-tool.md`
A **dedicated sub-agent** that generates section 7 — the interactive "watch the
proposal write itself" block. Give it the **same jobs** as the twins (usually 4),
the lead's voice/proof from recon, and **the `knowledge-base` skill**. It pulls
GigRadar's real cover-letter playbook from the KB, then writes **n jobs × 3 tones
(Formal / Quirky / Creative) = 3n** distinct cover letters, each with "why it
converts" + "sourced from the KB" notes, and returns one `proposalTool` JSON object.
Run it on **Sonnet** (high-volume copy, light reasoning — saves tokens) and **spawn
it in parallel** with steps 4–5, as soon as the twin jobs exist. Full recipe +
output shape + the three tone definitions live in `references/proposal-tool.md`.

### 4. Earnings config  → `references/earnings-model.md` + `references/calc-core.mjs`
Pick the lead's niche row from the benchmark table (reply-rate scenarios, close
rates, avg contract value, GigRadar cost). These feed the calculator. The math is
the verbatim GigRadar DFY engine — do not invent numbers.

### 5. Assemble `lead-data.json`
Fill the schema in `assets/template-schema.json` with everything from steps 1–4
(hero, standing/services, opportunities, profile mockup, calculator config).
- **The profile mockup's `profileTitle`, `overview` and `skills` MUST be the lead's
  REAL Upwork profile fields, not guessed.** Pull them from the ranked product data in
  ES with `scripts/profile_fetch.py --name "<Agency>" --hot <skills> --emit-json` (reads
  `profile-agency`/`profile-contractor` `originalData`: real title, overview, skills,
  rate, badges, earnings). Drop `profileTitle`/`overview`/`skills`/`rate`/`kpis`/`badges`
  in verbatim; only author the framing (`headline`, `intro`, `avatar`, `workHistory`,
  `mockFlag`). Never invent them when ES has the real ones.
- Drop the cover-letter agent's returned object in verbatim under `proposalTool`
  (section 7). Its jobs should be the same ones as `twinsSection.twins`.
- Reference images as `"@file:<path>"` (the build script inlines them as base64 —
  never paste base64 by hand).
- Set `serviceTags` to the lead's niches using the taxonomy in
  `assets/case-studies/case-studies.json` (e.g. `["lead-gen","email-marketing",
  "revops"]`). The build script auto-selects the most relevant case studies by
  tag overlap — no manual case-study picking needed.
- Leave `demoUrl` as the GigRadar HubSpot link (already the default) — it backs the
  SECONDARY demo button.
- **Set `crmCta`** (this variant's primary CTA): `url` = `https://gigradar.io/crm`,
  `label` = `Start a 10-day free trial`, `sub` = the honest trial terms (card required,
  cancel before day 10). Optionally set `demoCta.label`. `build.py` refuses to build
  without `crmCta.url`.
- **Drop in the `competitorGap`** object from step 2b (with your written copy). Omit it
  and the section auto-hides — but for this variant it's the spine, so include it.

### 6. Build, verify, deliver
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
scripts/deploy.sh <proposal.html> <lead-slug>   # → https://via.gigradar.io/<lead-slug>
```
This uploads to the `gigradar-proposals` Cloudflare Worker (R2-backed) and needs
your `GIGRADAR_PROPOSAL_TOKEN` (per-user push token — provision via
`ai-researcher-users` with `proposals_push: true`). Every published page
auto-reports engagement to **PostHog** (project GigRadar), keyed by the slug:
`proposal_viewed`, `scroll_depth`, `section_view`, `book_demo_click`,
`demo_modal_open`, `calculator_used`, `opportunity_click`, `case_study_click`,
`proposal_tool_job`, `proposal_tool_style` (which jobs/tones they explored) —
so you see exactly who opened it and how far they got. (Analytics only fire on the
live host, never on local preview or PDF.)

## Quality bar (do not skip — this is why it converts)
- **No low-baller opportunities.** Every job shown must be a verified, high-spend
  US client with a real budget and an expansion path. A sharp agency will judge
  GigRadar by the worst job on the page.
- **A real founder headshot is REQUIRED — not optional.** A monogram reads as
  templated and isn't premium; `build.py` **refuses to build** without `lead.photo`.
  Exhaust the escalation ladder in `references/recon.md` (site via a **real browser**
  for Cloudflare-blocked pages → LinkedIn → Upwork profile picture → Clay). If none
  exists after all of that, stop and get one manually — don't ship without it. Also
  use the real company logo (recolor a white-on-transparent mark so it shows).
- **Be honest in the numbers.** The calculator is a projection (label it). Don't
  fake per-client star ratings on case studies — GigRadar only has a site-wide
  Trustpilot score. Don't show a wrong logo (e.g. a site's WordPress favicon).
- **Never fabricate or reinvent — this is a trust document.** Every fact about the
  lead (their clients, past projects, metrics, positioning) must come from their
  real website or the outreach DB; every Upwork job/case study shown must be a real
  record from the miner/ES, quoted, never invented or embellished. No made-up client
  names, results, or testimonials. If you can't source it, leave it out.
- **No AI-slop tells in lead-facing copy.** No long dashes (em — / en –) anywhere —
  use commas, periods, colons, parentheses; `build.py` strips any that slip. No
  internal jargon a lead won't know: never write "KB" — say "knowledge base",
  "our proposal data", or "insights from 133,872 proposals".
- **Match their brand in the hero only** (photo/logo); the page itself is GigRadar
  brand. The point is "GigRadar built this *for you*".
- **The proposal tool (section 7) must be genuinely good copy.** It's the block
  where the prospect *feels* the product, so weak or duplicated letters undercut the
  whole page. All 3n letters distinct, each opening on the client with one real
  proof and one CTA, each `kbSourced` note a real KB pattern — never fabricated.

## Files
- `assets/template.html` — the data-driven template (`{{LEAD_JSON}}` slot).
- `assets/template-schema.json` — the contract for `lead-data.json` (every field
  + an example). Read this before filling.
- `assets/example-remote-guyana.json` — a complete worked example (incl. a full
  `proposalTool` block: 4 jobs × 3 tones).
- `references/proposal-tool.md` — the section-7 cover-letter recipe (Sonnet + KB
  sub-agent): the three tones, KB grounding, letter craft, and output shape.
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
