# KB conventions — how new content gets added (multi-maintainer)

The KB is populated by the team and a growing roster of agents (`research-agent`, `customer-audit`, `market-research`, ad-hoc analyses). For all of them to coexist without breaking each other or leaking sensitive data, every contributor follows the same contract.

This skill is read-only — it queries the KB. To add new content, you upload via the **`build-kb` skill's** `sync_folder_to_r2.sh` (run by the maintainer) or you can `r2 cp` directly with a write-scoped token. The contract below applies regardless of how content lands in R2.

## Bundle structure

```
<bundle-slug>/
├── README.md                                  # required — citation manual for the bundle
├── INSIGHTS.md (or *_INDEX.md)                # required if insights/ exists — master index
├── EXEC_BRIEFING.md                           # recommended — 1-page summary
├── (other top-level docs as the bundle needs)
├── insights/
│   ├── NN_topic-slug/
│   │   ├── INSIGHT.md                         # the narrative
│   │   └── *.csv (or *.parquet)               # supporting evidence
│   └── ...
└── (raw/, scripts/ if relevant)
```

## Bundle slug format

- **Date-prefixed for snapshot research:** `<YYYY-MM-DD>_<topic-slug>` — e.g. `2026-04-26_cover-letters-bidding`
- **Undated for evergreen sources:** `<topic-slug>` — e.g. `agency-success-course`

The slug is the citation handle. **Don't change it after publication** — every shipped article that cites the bundle has the slug baked in.

## Mandatory bundle README

Each bundle's `README.md` is the citation manual. It answers:

1. **What is this?** One paragraph.
2. **What's in it?** Layout map.
3. **How do I query it?** `jq` / `grep` / `cat` recipes.
4. **How do I cite it?** Authorship, dates, sample size, neutral-vs-named attribution rules.
5. **What's off-limits?** Numbers we don't publish, names we don't use, claims that need disclaimers.
6. **Authority level.** When does this source override public web sources?
7. **Maintenance.** How and when does this get refreshed?

The `kb_context_for.sh` call always returns the bundle's README alongside any insight, so an agent automatically gets the citation rules with every fetch.

## Insight frontmatter (required for new INSIGHT.md files)

YAML frontmatter at the top of every INSIGHT.md powers structured queries and the Vectorize metadata filter:

```yaml
---
id: cl-length-u-shape
title: Cover-letter length is U-shaped, not "shorter is better"
bundle: 2026-04-26_cover-letters-bidding
tags: [cover-letter-length, cover-letter-shape, reply-rate]
sample_size: 133872
effect_size: "+11.8pp at 700+ words vs 100-149 words"
confidence: cross-cohort-validated   # or single-cohort, exploratory, anecdote
public_safe: true                    # OK to cite in published content?
related_insights:
  - 2026-04-26_cover-letters-bidding/insights/02_cover_letter_structure
last_validated: 2026-04-26
---
```

| Field | Why it exists |
|---|---|
| `id` | Stable kebab-case handle. Citations use this so they survive folder reorgs. |
| `title` | Human-readable headline. |
| `bundle` | Parent bundle slug (denormalized for filter queries). |
| `tags` | Vector search uses these for metadata-filtered queries. **Stick to the controlled vocabulary** in the manifest's `tag_index` when possible — don't invent synonyms. |
| `sample_size` | Filterable: "find insights with n > 10000". |
| `effect_size` | Plain-English magnitude — surfaced in search results. |
| `confidence` | One of `cross-cohort-validated`, `single-cohort`, `exploratory`, `anecdote`. Filterable. |
| `public_safe` | If `true`, the *finding* is safe to cite externally (the prose may still need redaction). If `false`, internal-only. |
| `related_insights` | Cross-references, populated as bundles relate. |
| `last_validated` | Triggers re-validation cadence (annual). |

Legacy bundles (e.g. the cover-letter bundle's 110 insights) don't have frontmatter yet — the linter warns until 2026-05-01 then hard-fails. Backfill is lazy: add frontmatter when you touch an insight for any reason.

## Banned-term hygiene

The KB *internal* files MAY contain DTO names, schema field references, customer counts, internal tooling names — that's fine, this is the source data layer. **What's forbidden is letting those leak into published content.**

The split:
- `public_safe: true` insights → the *finding* is safe to cite, but the narrative may still need redaction. Publishing skills (`linkedin-publisher`, blog publisher) must run their own banned-term audit on output.
- `public_safe: false` insights → the finding itself isn't safe to cite externally. Used for internal CSM playbooks, product roadmap, internal benchmarks.

The KB does not pre-redact. It exposes everything to authorized agents. Redaction is the publishing layer's job.

## Adding a new bundle (high level)

```bash
# 1. Build the bundle locally (or in your agent's outputs)
mkdir my-bundle/
# ... drop content in, write README.md, write INSIGHTS.md, add frontmatter ...

# 2. Validate locally before upload
python3 build-kb-skill/scripts/validate_bundle.py ./my-bundle

# 3. Upload to R2 via the build-kb skill
bash build-kb-skill/scripts/05_sync_folder_to_r2.sh ./my-bundle

# 4. Indexer picks up the change on the next 5-min cron tick.
#    Manifest auto-rebuilds. Vectorize index refreshes.
```

The agent who built the bundle is responsible for:
- Writing the README citation manual
- Adding frontmatter to every INSIGHT.md
- Marking any insight that contains sensitive data as `public_safe: false`
- Logging the bundle's existence somewhere humans see it (Slack, calendar)

Other agents don't need to know about a new bundle until they query — the manifest auto-discovers it on next sync.

## Cadence

- **Per bundle creation:** validate + sync + log to team channel.
- **Quarterly:** team re-runs the manifest builder, audits stale `last_validated` dates, archives bundles that are no longer trustworthy.
- **Annually:** revalidate sample data — if the underlying numbers have drifted significantly, the bundle gets a `v2` date suffix and the original is marked `archived`.

## When the contract gets violated

Common failure modes and how to handle them:

| Symptom | What it means | Fix |
|---|---|---|
| Search returns hit with no `tags` | Legacy insight, no frontmatter | Backfill frontmatter when you next touch it; works in keyword search regardless |
| `kb_context_for` returns no bundle_readme | Bundle is missing README.md | Critical — bundle isn't citable. Open ticket, don't publish from this bundle until fixed |
| `public_safe: true` insight contains DTO names in narrative | Frontmatter says safe but prose contains banned terms | Fine internally — publishing skills will catch it on output. But ideally the prose gets redacted on next touch |
| Two bundles use different tags for the same concept (`CL_length` vs `cover-letter-length`) | Tag drift | Manifest's `tag_index` will show both as separate keys; consolidate by retagging the older bundle |
