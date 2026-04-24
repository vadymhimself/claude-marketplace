# gigradar-gm

GigRadar market research & growth-insights plugin for Claude. Built for the GigRadar GM, Growth, and Success Manager workflows — turns Upwork public job-market data plus GigRadar's own proposal pool into actionable market-intelligence reports.

## What this plugin does

- Aggregates the Upwork `metajob` Elasticsearch index for volume + quality (budget, client) signals across any windowed time range.
- Aggregates GigRadar's Mongo `proposals` collection for reply/view rates using the canonical `StatsRepository` formula.
- Joins the two into per-category / per-subcategory / per-skill tidy CSVs.
- Produces a styled Excel workbook + markdown executive summary per run.

All configuration is inferred from the user's natural-language prompt — there is no YAML to fill in. The skill's `SKILL.md` documents how to parse intent into script invocations.

## Skills

- **`/market-research`** — run a full market-research pass for an arbitrary time window (or subset of it — category / subcategory / skill / team narrowing all supported).
- **`/customer-audit`** — single-team deep-dive audit: retro-first (pre-GigRadar + historical wins), competitive deep-dive (cohort compare + ES metajob KNN peer look-alikes with subagent-dispatched per-competitor CL analysis), chat transcripts, Win/Loss CL comparison, auto-bidding aggregates, three-tier WINS/OKAY/CRITICAL exec summary. Reply rate + $/reply are north-star. See `skills/customer-audit/SKILL.md`.

## Installation

From a Cowork session or Claude Code checkout:

```bash
cp -r gigradar-gm ~/.claude/plugins/
# or (inside a repo): reference via local marketplace
```

## Environment variables

The plugin reads credentials from the environment — no secrets are bundled.

| Var | Required | Default | Purpose |
|---|---|---|---|
| `ES_URL` | no | `https://<es-host>:9243` | ES endpoint |
| `ES_USER` | no | `researcher-prod` | ES username |
| `ES_PASS` | **yes** | — | ES password (role: `metajob-ro`) |
| `ES_INDEX` | no | `metajob` | ES index alias |
| `MONGO_URI` | **yes** | — | Full Mongo URI incl. creds + `?authSource=admin` |
| `MONGO_DB` | no | `gigradar-dev` | Mongo database |

Set these in the shell the skill runs scripts from. Example:
```bash
export ES_PASS='<from 1Password: researcher-prod>'
export MONGO_URI='mongodb://researcher-prod:<pw>@<mongo-host>:<port>/gigradar-dev?authSource=admin'
```

## Python dependencies

The skill's scripts need:
- `requests` + `urllib3` — ES HTTP
- `pymongo` — Mongo driver
- `openpyxl` — xlsx writer

```bash
pip install --break-system-packages requests pymongo openpyxl
```

(On Claude's sandbox `pip install` requires `--break-system-packages`.)

## Trigger phrases for `/market-research`

Plain-language asks that should route to this skill:

- "Run market research for May 2025"
- "How's the Upwork market trending over the last 3 months?"
- "What's growing/shrinking in Sales & Marketing?"
- "Reply rate for Legal last month?"
- "Benchmark the Video Editing skill"
- "Build the monthly market report"
- "Compare April vs May 2025"
- "Run the monthly report for team <oid>"

See `skills/market-research/SKILL.md` for the full prompt-to-args mapping.

## Plugin layout

```
gigradar-gm/
├── .claude-plugin/
│   └── plugin.json                        # manifest
├── README.md                              # this file
├── references/                            # PLUGIN-ROOT shared references
│   └── data-reference.md                  # single canonical GigRadar data reference
└── skills/
    ├── market-research/
    │   ├── SKILL.md                       # skill entry point
    │   ├── scripts/
    │   │   ├── es_queries.py              # ES helpers (windows, sub-aggs, top-terms)
    │   │   ├── run_aggs.py                # ES CLI runner
    │   │   ├── mongo_reply_rates.py       # Mongo CLI runner (canonical formula)
    │   │   ├── merge_all.py               # join ES + Mongo into tidy CSVs
    │   │   └── build_xlsx.py              # build the workbook
    │   └── references/
    │       ├── data-reference.md          # pointer → ../../references/data-reference.md
    │       ├── es-patterns.md             # ES field gotchas + perf notes
    │       ├── mongo-patterns.md          # proposals patterns, index hints, snapshot caveats
    │       └── output-examples.md         # workbook layout + summary template
    └── customer-audit/
        ├── SKILL.md                       # skill entry point
        ├── scripts/                       # reference implementation (Ubiquify run) — copy + edit per team
        │   ├── README.md                  # pipeline order + per-team edit points
        │   ├── phase1_retro_v2.py         # full proposal history pull
        │   ├── phase2a_cohort_v2.py       # sibling-team cohort compare
        │   ├── phase2b_v2_peer_knn.py     # ES KNN peer look-alikes + CL harvest
        │   ├── phase2c_match_reasoning.py # pair competitor wins → subject team WIN/LOSS analogues
        │   ├── merge_ai_judgments.py      # merge subagent outputs → phase2c (in-place)
        │   ├── phase3_chats.py            # chat transcripts for HIRED + replied
        │   ├── phase4_winloss.py          # Win/Loss CL table
        │   ├── phase5_aggregates.py       # auto-bidding aggregates (opps-first join)
        │   ├── build_workbook.py          # dark-mode 14-col xlsx builder
        │   └── gen_playbook.py            # consolidate subagent tactics → COMPETITIVE_PLAYBOOK.md
        └── references/
            ├── data-reference.md          # pointer → ../../references/data-reference.md
            ├── audit-playbook.md          # phase-by-phase decision tree
            ├── metrics.md                 # reply/view/hire formulas
            ├── scanners.md                # scanner schema + biddingStrategy subtypes
            ├── cover-letters.md           # CL generation flow + reading heuristics
            └── output-examples.md         # three-tier exec-summary layout
```

## Maintenance — keep the data reference alive

The plugin treats **`references/data-reference.md` at plugin root** as the single living doc. It is the one canonical copy shared across every skill; both `skills/*/references/data-reference.md` files are pointer stubs, not duplicates. Whenever a run surfaces a finding that isn't already documented — a new index hint, a new field gotcha, a new sample-size bias — append a short log entry to the relevant section (§14 perf, §15 ES fields, §16 cross-team analytics, §17 customer-audit corrections, §25 competitive deep-dive artifacts).

The workspace-level `DATA_REFERENCE.md` (visible to the rest of the GigRadar team in Cowork) should be kept in sync manually by whoever ran the update.

## Versioning

- `0.1.0` — Initial release. May 2025 trends research run was the driving use case; bundled scripts are the generalized versions of the one-off research scripts.
- `0.2.0` — Added `/customer-audit` skill and reference docs (retro-first methodology, opportunities-first join, reply-rate as north-star).
- `0.3.0` — Deduped `DATA_REFERENCE` into a single plugin-root canonical (`references/data-reference.md`). Bundled the full Phase-1→6 reference-implementation scripts under `skills/customer-audit/scripts/` (Ubiquify 2026-04-22 run), including the subagent-dispatched AI judgment pass (`merge_ai_judgments.py` + `gen_playbook.py`) for per-competitor CL pattern analysis. Added §25 to the data reference documenting the phase2c schema, subagent bundle format, and dark-mode workbook rendering conventions.

## Questions / ownership

- **Plugin owner:** GigRadar (internal)
- **Canonical data reference:** `references/data-reference.md` (plugin root; shared across all skills)
- **Source-of-truth for reply formula:** `gigradar-aws-functions/services/utils/repositories/stats/stats-repository.ts` in the monorepo. If the plugin's formula drifts from that file, the plugin is wrong — update the plugin.
