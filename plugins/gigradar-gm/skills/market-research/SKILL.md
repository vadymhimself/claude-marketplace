---
name: market-research
description: GigRadar Upwork market research — job-volume trends, client quality, and reply/view rates. Use this skill whenever the user asks to "run market research", "check Upwork trends", "reply rate for X", "what's growing/shrinking on Upwork", "benchmark a skill/category/subcategory", "produce the monthly market report", or any similar question that needs ES metajob aggregations joined with Mongo proposal reply rates. Also trigger for ICP sizing questions from the GigRadar GM/Growth/Success Manager workflows.
---

# GigRadar market research

This skill produces a defensible view of the Upwork market from GigRadar's two data sources:

- **Elasticsearch `metajob` index** — public crawl of Upwork job postings. Used for volume, category/subcategory/skill mix, budget, client quality.
- **Mongo `proposals` collection** — GigRadar customers' agency-side proposals. Used for reply/view rates (via the canonical `StatsRepository` formula).

The output is a tidy Excel workbook + markdown exec summary, covering the windows the user cares about (typical: a focus month vs one or more prior months).

---

## How to invoke: infer everything from the user's prompt

The user will not supply structured flags. They will say things like:

| User prompt | Inferred args |
|---|---|
| "Run market research for May 2025" | `--windows apr2025,may2025` `--focus may2025` |
| "How's the market trending over the last 3 months?" | compute the 3 most recent completed months; focus = last one |
| "Reply rate in Legal last month?" | 1-month window, `--focus-category "Legal"` on ES, mongo reply for that category |
| "What's growing in Sales & Marketing?" | 2–3 months, `--focus-category "Sales & Marketing"`, emphasize Trending up |
| "Benchmark the Video Editing skill" | `--focus-skill "Video Editing"`, 2–3 months, report volume + reply rate |
| "Compare April vs May 2025" | `--windows apr2025,may2025` `--focus may2025` |
| "Run the monthly report for the Acme team" | last completed month + prior, `--team-oid <lookup>` on mongo |

### Resolving windows

- "May 2025" / "last month" / "Apr 2024" → a single month. Add at least one comparison month (the prior) so trending tables work.
- "Last 3 months" / "Q2 2025" → multiple months; set focus to the last window.
- "Last week" / arbitrary range → use the `YYYY-MM-DD:YYYY-MM-DD` form (inclusive/exclusive — `gte`/`lt`).
- Windows are named with slugs like `may2025`, `2025-05`, or `2025-05-01_2025-06-01` (the scripts accept any of these forms).

### Resolving dimensions and ICP focus

Default dims: `category, subcategory, skill` (three tidy files + three workbook tabs). Narrow when the user is clearly ICP-scoped:

- "in Sales & Marketing" → `--focus-category "Sales & Marketing"` on ES side (must be exact casing — check `references/es-patterns.md`).
- "for Video Editing" → `--focus-skill "Video Editing"` on ES side.
- If they name a team or customer (e.g. "for Acme") → `--team-oid <oid>` on mongo side (ask the user for the ObjectId if not obvious).

---

## Workflow — 4 steps

Run these in order. Each step appends to a per-run directory so re-runs are incremental.

Pick a working directory. Recommended:
```
RUN_DIR=/sessions/<session>/market_research_<focus_slug>
RAW=$RUN_DIR/raw       # step 1 + 2 output
TIDY=$RUN_DIR/tidy     # step 3 output
```

### Step 1 — ES aggregations (volume + quality)

Requires env: `ES_URL`, `ES_USER`, `ES_PASS`. Defaults in `scripts/es_queries.py` point at `<es-host>:9243` with user `researcher-prod`.

```bash
python scripts/run_aggs.py \
  --out "$RAW" \
  --windows apr2025,may2025,jun2025 \
  --dims skills,category,subcategory \
  --size 100 \
  [--focus-category "Sales & Marketing"]   # optional ICP narrow
```

Produces one JSON per `(window, dim)` plus `_es_summary.json`. Each bucket carries `doc_count`, hourly/fixed budget percentiles, client avgs (`paymentVerified`, `totalSpent`, `feedbackScore`, `hireRate`).

### Step 2 — Mongo reply rates (canonical `StatsRepository` formula)

Requires env: `MONGO_URI` (full URI with creds + `?authSource=admin`), optional `MONGO_DB` (default `gigradar-dev`).

```bash
python scripts/mongo_reply_rates.py \
  --out "$RAW" \
  --windows apr2025,may2025,jun2025 \
  --groupings category,subcategory,skill \
  [--team-oid <ObjectId>]                  # optional: narrow to one GigRadar team
```

Reply = `proposal.dashroomUID` non-null. Base filter = `meta.createdAt` in window AND `meta.inviteToInterviewUid: null`. The script forces `hint={"meta.createdAt": 1}` (mandatory — without it the planner picks the tenant compound index and scans are slow). See §14 in `../../references/data-reference.md`.

### Step 3 — Merge into tidy CSVs

```bash
python scripts/merge_all.py \
  --raw  "$RAW" \
  --out  "$TIDY" \
  --windows apr2025,may2025,jun2025 \
  --dims category,subcategory,skill
```

Produces `<dim>__full.csv` — one row per key, columns wide by window. Pairwise deltas (jobs % and reply-rate pp) between consecutive windows are added automatically.

### Step 4 — Build the workbook + write the summary

```bash
python scripts/build_xlsx.py \
  --tidy "$TIDY" \
  --out  "/sessions/<session>/mnt/<cowork-folder>/upwork_<focus>_trends.xlsx" \
  --windows apr2025,may2025,jun2025 \
  --focus  may2025 \
  --market-totals  '{"apr2025":198783,"may2025":197724,"jun2025":199117}' \
  --market-reply   '{"apr2025":[79103,9117,25079],"may2025":[93487,9934,28081]}'
```

`--market-totals` and `--market-reply` are optional but fill in the Overview sheet nicely. `market-totals` values come from each window's `_es_summary.json["<window>"]["total"]`. `market-reply` values come from a single-grouping-less mongo aggregation over the window (or derive them by summing a cat-grouped reply file).

Then draft `upwork_<focus>_summary.md` with:
- **TL;DR** — 5-7 bullets: market volume flat/growing, biggest growth + decline clusters, market-wide reply rate, any category the user is over/under-indexed in.
- **Quantity** tables — top 5 categories, fastest-growing & fastest-shrinking skills (floor: ≥2,000 jobs).
- **Quality** read — client bankroll, payment verification, hourly vs fixed mix, median rates.
- **Reply rate** tables — by category, highest & lowest reply-rate skills (floor: ≥500 proposals).
- **Actionable read** — 3-5 ICP / positioning implications.
- **Caveats** — category=null share, any known sample-size biases, snapshot timing (late dashroomUIDs → recent-month reply rate artificially low).

Save the workbook + summary + a copy of the run's tidy CSVs to the Cowork workspace folder, then provide `computer://` links.

---

## Output locations

Final deliverables always go under the user's Cowork workspace folder so they can open them directly:
- `upwork_<focus_slug>_trends.xlsx` — the workbook
- `upwork_<focus_slug>_summary.md` — the exec summary

Intermediate raw JSON + tidy CSVs can stay in the session temp directory unless the user asks for them.

---

## When the user asks a narrower question

- **Single-number ask** ("what's the reply rate for Legal in May?") — no need for the full workbook. Run steps 1+2 for the one window + the one dim, pull the number from the Mongo result, and answer in chat. Don't spin the whole pipeline.
- **"Why" questions** ("why did PHP drop?") — pull prior/current buckets for that skill and inspect `doc_count`, the subcategory mix, and any visible budget shift. This is forensic work, not a report.
- **Ad-hoc filtering** (e.g. only US clients) — not covered by the bundled scripts. Extend ES/Mongo queries inline; document the new pattern in `references/es-patterns.md` or `references/mongo-patterns.md`.

---

## Keep DATA_REFERENCE.md alive

The canonical reference is the single plugin-root file `../../references/data-reference.md` — it is shared by every skill in this plugin. Every time this skill runs and produces a finding that wasn't documented (a new field gotcha, a new performance trap, a new collection, a new index recommendation) — **append a short log entry to the relevant section** of that one file:

- §14 Index / perf log — when you hit a Mongo query that needs a `hint` or a slow ES aggregation
- §15 ES metajob field gotchas — new `.keyword` vs keyword-typed discoveries, mapping changes, empty fields
- §16 Cross-team reply-rate analytics — sample-size biases, snapshot timing issues, new dimension ideas

Then keep the workspace-level `DATA_REFERENCE.md` in sync (the workspace copy is the source of truth the rest of the team sees; the plugin copy is the one this plugin carries).

---

## Dependencies

- `requests`, `urllib3` — ES HTTP client
- `pymongo` — Mongo driver (requires `--break-system-packages` on the sandbox: `pip install pymongo --break-system-packages`)
- `openpyxl` — Excel writer

Install if missing:
```bash
pip install --break-system-packages requests pymongo openpyxl
```

---

## Files bundled with this skill

- `scripts/es_queries.py` — ES helpers (windows, budget sub-aggs, top-terms query)
- `scripts/run_aggs.py` — ES CLI runner (volume + quality)
- `scripts/mongo_reply_rates.py` — Mongo CLI runner (reply/view rates, canonical formula)
- `scripts/merge_all.py` — join ES + Mongo into tidy per-dim CSVs
- `scripts/build_xlsx.py` — build the final report workbook
- `../../references/data-reference.md` — **plugin-root** canonical GigRadar data reference (single shared copy across all skills)
- `references/es-patterns.md` — ES-specific gotchas distilled from live research
- `references/mongo-patterns.md` — proposals collection patterns, index choices, canonical formulas
- `references/output-examples.md` — a filled-out example summary + workbook table layouts

Read the relevant reference(s) before invoking a script for the first time in a new dimension — they encode painful trial-and-error.
