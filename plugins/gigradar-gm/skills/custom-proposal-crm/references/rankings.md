# Gap-miner recipe — rank + competitor gap (custom-proposal-crm)

The section-1b spine. Turns GigRadar's live Upwork rankings data into the `competitorGap`
block: the lead's own MRR + rank + percentile, and the agencies earning more than them in
their niche. Everything here is a **real ES record** — this is a trust document, never
invent a competitor, an MRR, a JSS, or a rank.

## Data source
The rankings live in the **Inbound backend** Elasticsearch, indices `profile-*`. The
`metajob-ro` role (the same `ES_URL`/`ES_USER`/`ES_PASS` used by `market-research` /
`opportunity-mining`) was extended (2026-07) to read them:
- `profile-agency` — agency profiles + earnings (86k+ docs)
- `profile-contractor` — freelancer profiles (379k+ docs)
- `profile-skill`, `profile-metric-snapshot` — skill map + weekly snapshots

Field notes (mirror `market-research/references/es-patterns.md`):
- **MRR = `recentEarnings` / 6.** There is no stored `mgr`/`mrr` field — the public API
  computes it; so do we. Sort and rank by `recentEarnings`.
- `services` are Upwork **slugs** (`shopify-development-companies`, `lead-generation-companies`,
  `email-marketing-agencies`, …). `country` is a **display name** (`"United States"`, `"India"`).
- Quality/throughput fields: `jobSuccessScore` (0–1), `topRatedStatus` / `topRatedPlusStatus`,
  `totalHours`, `totalJobs`, `numberOfEmployees`, `totalEarnings`, `totalRevenue`.
- Freelancers: `profile-contractor`, country at `location.country`, skills at `skills.name`.
- Respect `hideEarnings` / `isHideEacEarnings` if present — mask, don't display.

## Run it
```bash
export ES_URL='https://prod-search-deployment.es.us-west-2.aws.found.io'
export ES_USER='ai_vadym_at_gigradar_io' ES_PASS='…'   # metajob-ro
python3 scripts/gap_miner.py --name "TechInfini Solutions" \
    --country "India" --service shopify-development-companies --top 10 --emit-json > gap.json
```
- Omit `--country` / `--service` to auto-scope to the lead's own country + first service.
- `--entity contractor` for freelancers.
- Prints a human table + a numeric gap read to stderr; `--emit-json` writes the
  `competitorGap` object to stdout.

## Rank math (what the script does)
```
rank  = count(recentEarnings > lead.recentEarnings  within scope) + 1
pool  = count(scope with earnings)
pct   = round(rank / pool * 100, 1)            # "Top 0.5%"
scope = {country term} AND {services/skills term} AND exists(recentEarnings)
```
Pick the scope that flatters truthfully and matches the outbound hook: usually the lead's
**country × their strongest service** (that's the tightest, most credible peer set). The
worldwide-service scope is a fine second stat.

## Turning `_gap_facts` into copy (your job)
The script fills `board`, `stats`, `rankLabel`, `poolLabel`, `scopeLine` and an `_gap_facts`
summary. You write the persuasion, grounded ONLY in those numbers:
- `headline` — the rank as a hook, and the reframe ("the N above you aren't more talented").
- `lede` — one line: same quality, less throughput + trust signal.
- `closeWhy` — name the SPECIFIC gap this lead has from `_gap_facts`: compare their
  `leadJss` / `leadBadge` / `leadJobs` to the board. Common true pattern: their JSS already
  rivals the top of the board, but they lack **Top Rated Plus** and run a fraction of the
  **bid volume** (jobs/hours). Use `gapToNextMrr` for "you're only $X/mo from the next rung".
- `fixes` — exactly two cards, the two products that close the gap:
  1. **Auto Bidding** → throughput ("scans Upwork 24/7 and applies with custom cover letters,
     so you bid at the volume the top N run"). pill: "more bids out".
  2. **GigRadar CRM** → speed + tracking ("answers your first reply in your voice in under 5
     minutes, 10-stage pipeline so no warm lead cools"). pill: "faster replies".
- **Delete `_gap_facts`** before building (internal only).

## Honesty rules
- Every board row is a quoted ES record. If the lead already has a Top Rated badge, say so —
  the gap is Top Rated **Plus** + volume, not "no badge". Don't overstate.
- MRR is an estimate (the `boardNote` already says so). Keep it.
- If the lead can't be resolved in `profile-*` (not yet crawled), either connect/add them
  first or omit the section — don't fabricate a rank.
