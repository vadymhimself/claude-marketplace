# Opportunity mining — cherry-pick high-value Upwork jobs

Find the live US jobs the lead is *missing* — the ones that make them think
"I want that client." This section makes or breaks the proposal: **lead only with
the top tail; never low-ballers.** The median post in any category is junk
(~$100 fixed / ~$5/hr); a sharp agency judges GigRadar by the worst job you show.

## Data source
GigRadar's Elasticsearch `metajob` index (the live Upwork crawl) — the same source
the `gigradar-gm:market-research` skill uses. Reuse that skill's ES access /
credentials rather than duplicating secrets. Fields live under the `metaJob.`
prefix (title, ciphertext, budget, skills, createdOn, category/subCategory,
client country / totalSpent / feedbackScore / hireRate / totalHires /
paymentVerified, company industry/size, description).

## Query
- Filter to the lead's service lines (their `serviceTags` → categories/skills,
  e.g. Lead Generation & Telemarketing, Digital Marketing, the relevant skill
  terms like "Clay", "Cold Email", "HubSpot", "Appointment Setting").
- US clients, recent (last ~60–90 days), `paymentVerified=true`.
- Pull a few hundred, then rank and hand-pick ~9.

## Quality filter (hard floor — discard everything below)
Keep only jobs where the CLIENT is clearly serious:
- `client.totalSpent` ≥ ~$20k (prefer six/seven-figure spenders),
- a real budget: fixed ≥ ~$3k OR hourlyMax ≥ ~$40,
- `feedbackScore` ≥ ~4.5 and a healthy `hireRate`,
- payment verified.
Prefer a spread of budget types (a couple of eye-popping fixed budgets, some
hourly retainers) and a spread of the lead's service lines.

## For each chosen job, return
`{cat, title, what_they_need, budget (display, e.g. "$15–45"), budgetType
("/hr"|"fixed"|"$500/demo"), desc (1–2 sentences), client_totalSpent ("$7.9M"),
feedbackScore, totalHires, company_industry, upwork_url
(https://www.upwork.com/jobs/<ciphertext>), ltv}` where **`ltv`** is the money
line: why this job fits the lead AND how a single entry gig expands into a
high-LTV retainer. This "why it fits → how it expands" note is what sells.

## Output
Hand the orchestrator the ~9 objects (→ `lead-data.json` `opportunities[]`) plus
a few market-band stats for section 2 (total US jobs/mo in their lines, % payment
verified, avg client lifetime spend, % fixed-price). Be honest that these are live
as-of today and churn.

## ES field reference (read this — every prior run lost an iteration here)
The live index has quirks that fail **silently** (zero hits, no error). Confirmed
across runs:
- Query the **`metajob`** alias (or dated `metajob-v10-YYYY-MM`) with `_search`.
  `_cat/indices` and cluster-monitor endpoints return 403 — that's expected, ignore.
- Fields are under `metaJob.`. **`metaJob.categoryName` / `metaJob.subCategoryName`
  are bare `keyword`-style text** — term-filter and aggregate on them **directly**;
  appending `.keyword` returns **zero hits silently**. (`metaJob.category` does not
  exist.) For skills, aggregate on `metaJob.skills.name.keyword`. If a term/agg
  returns 0, suspect a wrong `.keyword` suffix first.
- **`metaJob.hireRate` and rates are 0–1 fractions, not 0–100** (filter `>= 0.5`,
  not `>= 50`). `metaJob.feedbackScore` is 0–5.
- **Country has two values: `"United States"` AND `"USA"`** — filter both.
- `hits.total` caps at 10,000 unless you set `track_total_hits: true`. For honest
  market-band counts, use `track_total_hits` or trust the aggregation bucket counts.
- Example taxonomy seen: categories like "Sales & Marketing", "Web, Mobile &
  Software Dev"; subcats "Web Development", "Digital Marketing", "Lead Generation &
  Telemarketing", "Video & Animation". Discover the real values with a `terms` agg
  on `metaJob.subCategoryName` before filtering.
