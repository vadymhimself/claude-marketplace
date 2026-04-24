# Output examples

Two canonical deliverables for every market-research run:

1. `upwork_<focus>_trends.xlsx` — the workbook (produced by `build_xlsx.py`)
2. `upwork_<focus>_summary.md` — the executive summary (written by Claude after reading the tidy CSVs + workbook)

Both land in the user's Cowork workspace folder so they get `computer://` links.

---

## Workbook sheet layout (for `build_xlsx.py`)

`build_xlsx.py` produces the following sheets. Sheet count depends on how many windows were passed.

### Overview
- Market totals per window (jobs posted) + Δ vs prior
- Market-wide reply/view rates per window (proposals / replies / views / reply rate / views / view rate)
- Top 5 categories for the focus window (share of jobs, reply rate, avg client spend)

### Categories / Subcategories / Top skills
One row per bucket key with columns:

| Column | Meaning |
|---|---|
| Key | Category / subcategory / skill name |
| Jobs (focus / prior) | ES doc counts for the two comparison windows |
| Jobs Δ focus/prior | % change |
| % Hourly | hourly_jobs / doc_count |
| Median $/hr (min / max) | budget.hourlyMin and hourlyMax medians (hourly jobs only) |
| Median $ fixed / P25 / P75 | budget.fixed percentiles (fixed jobs only) |
| Avg client total spent ($) | avg of metaJob.client.stats.totalSpent |
| % Payment verified | avg of metaJob.client.paymentVerified |
| Avg hire rate / Avg feedback | avg client stats |
| Proposals (focus) | Mongo proposal volume |
| Reply rate | replies / proposals |
| View rate | views / proposals |
| Δ reply rate focus/prior (abs pp) | absolute percentage-point delta |

Conditional formatting: green color-scale on reply rate and on jobs Δ.

### Trending up / down
Filtered tables showing the biggest movers above a min-volume floor (default 500 jobs in the focus window). Columns: prior jobs / focus jobs / delta % / reply rate / avg client spend.

### Reply rate leaders
Cross-dimension ranking of the highest reply rates in the focus window, with a `min 100 proposals` filter to cut noise. Columns: dim, key, jobs, proposals, replies, reply rate, view rate, delta.

### Methodology
Prose section: data sources, window conventions, quality-metric caveats, canonical reply-rate formula, sample-size biases, the category=null note, and any index/perf notes. Mirrors the caveats block from the summary.

---

## Exec summary structure (markdown)

The following is the structure Claude should follow when drafting `upwork_<focus>_summary.md`. The May 2025 version (`upwork_may2025_summary.md`) is the canonical example — keep the same tone: short, evidence-based, actionable.

```markdown
# Upwork trends — <Focus window>

<One-line lens sentence: what windows were compared, what data sources, what the
caveat context is.>

## TL;DR
- 5–7 bullets. Lead with the market-volume number (flat or moving), then the
  biggest growth cluster, the biggest decline cluster, the market-wide reply
  rate, and 1–2 ICP implications.

## Quantity — market volume
- Market total table (jobs posted per window, Δ vs prior)
- Top 5 categories by focus-window volume
- Fastest-growing skills (min 2,000 focus-window jobs)
- Fastest-shrinking skills (same floor)

## Quality — client & budget
- Category-level read: client bankroll, payment verification, hourly vs fixed mix
- Median hourly rates by category (hourly jobs only)
- Budget caveat: median hourly-max populates on only ~50% of hourly jobs

## Reply rate — what clients actually respond to
- Reply / view rate by category
- Highest reply-rate skills (min 500 focus-window proposals)
- Lowest reply-rate skills (same floor)

## Actionable read
- 3–5 numbered takeaways. Each should either propose a pitch pivot, surface an
  ICP over/under-index, or flag a specific skill cluster to reposition around.

## Caveats
- Sample-size biases (small-n categories)
- `metaJob.categoryName = null` share (~25–30%)
- Recent-window reply-rate snapshot timing (dashroomUID asynchrony)
- Population bias of proposals = GigRadar customers only, not market

---
*Workbook: `upwork_<focus>_trends.xlsx` — N sheets.*
```

---

## Common floors and thresholds

Use these unless the user says otherwise:

| Table | Floor |
|---|---|
| Fastest-growing / shrinking skills | ≥ 2,000 jobs in focus window |
| Trending sheets (workbook) | ≥ 500 jobs in focus window |
| Reply-rate leaders | ≥ 100 proposals in focus window |
| Reply-rate by category (summary) | Report with proposal denominator; flag <500 as thin |

---

## Style for numbers

- Volumes: thousands separator, no decimals (`197,724`).
- % changes: `+5.6%` / `-7.3%` with explicit sign; `-` for missing.
- Reply/view rates: `0.0%` with one decimal.
- Dollar amounts: `$1,234` or `$1,234.56` — depends on column.

The bundled `build_xlsx.py` encodes all of this; match the workbook in the summary.

---

## Where to link results from

After saving the workbook + summary to the Cowork workspace folder, provide the user:
- A `computer://` link to the xlsx
- A `computer://` link to the summary .md

Avoid writing extensive prose recapping the summary in chat — let the markdown file speak.
