# ES `metajob-all` — patterns & gotchas

This is the condensed playbook for running aggregations against the GigRadar Elasticsearch crawled-jobs index. Everything here was earned by trial and error during the May 2025 research run; details and examples live in `data-reference.md` §8 and §15.

> **Alias renamed May 2026: `metajob` → `metajob-all`.** All hardcoded `/metajob/*` URL paths in older scripts are wrong. Use `/{ES_INDEX}/*` and let `ES_INDEX` (default `metajob-all`) carry the alias. Older clusters that haven't migrated still respond to `metajob` — set `ES_INDEX=metajob` to fall back. The conceptual data source is unchanged: still the public Upwork crawl, same `metaJob.*` document shape.

---

## Access

- Endpoint: `https://<es-host>:9243`
- User: `researcher-prod`
- Role: `metajob-ro` — **index-scoped**. Cluster-level endpoints (`/_cluster/health`, `/_cat/indices`) return 403. Stick to `/metajob-all/_search`, `/metajob-all/_count`, `/metajob-all/_mapping`.
- Default index alias: `metajob-all` (renamed from `metajob` in May 2026; underlying `metajob-v9-000001`).
- Env vars the scripts read: `ES_URL`, `ES_USER`, `ES_PASS`, optional `ES_INDEX`.

---

## Field names — where `.keyword` matters

- `metaJob.categoryName` — already `keyword` type. **Do not** append `.keyword` (returns 0 buckets).
- `metaJob.subCategoryName` — same: already `keyword`. No suffix.
- `metaJob.ontologySkillNames` — already `keyword` but **effectively empty** in 2025 data (see below).
- `metaJob.skills.name` — `text` field with a `.keyword` sub-field. Aggregations **must** use `metaJob.skills.name.keyword`.
- Date for windowing: `metaJob.createdOn` (not `date_scrapped`).
- Budget: `metaJob.budget.type` (`1=fixed, 2=hourly`), `metaJob.budget.fixed`, `metaJob.budget.hourlyMin`, `metaJob.budget.hourlyMax`.
- Client quality: `metaJob.client.paymentVerified`, `metaJob.client.stats.totalSpent`, `...feedbackScore`, `...hireRate`.

Rule of thumb: if a terms aggregation returns 0 buckets and you expected a non-trivial count, the first thing to check is the `.keyword` suffix — the index mapping has both `keyword` sibling fields and already-keyword-typed fields, and they behave opposite ways.

---

## `ontologySkillNames` is a trap

It is indexed and appears in the mapping, but it is functionally empty for all of 2025 (only ~10 docs for April, 0 for May/June). Don't build anything around it. Use `metaJob.skills.name.keyword` instead.

---

## `track_total_hits` is required for counts

ES caps `hits.total.value` at 10,000 by default. Every analytical aggregation must include `"track_total_hits": true` in the body — otherwise every window looks like "exactly 10,000 jobs." The bundled `es_queries.py` sets this everywhere.

---

## Budget medians must be scoped by type

`metaJob.budget.fixed` is 0/null for hourly jobs, and `hourlyMin`/`hourlyMax` are 0/null for fixed jobs. A naive percentile over the raw field produces nonsense medians like $0–3.

Correct pattern (hourly + fixed filtered sub-aggs):

```json
{
  "hourly": {
    "filter": {"term": {"metaJob.budget.type": 2}},
    "aggs": {
      "median_hourly_min": {"percentiles": {"field": "metaJob.budget.hourlyMin", "percents": [50]}},
      "median_hourly_max": {"percentiles": {"field": "metaJob.budget.hourlyMax", "percents": [50]}}
    }
  },
  "fixed": {
    "filter": {"term": {"metaJob.budget.type": 1}},
    "aggs": {
      "median_fixed": {"percentiles": {"field": "metaJob.budget.fixed", "percents": [50]}},
      "p25_fixed":    {"percentiles": {"field": "metaJob.budget.fixed", "percents": [25]}},
      "p75_fixed":    {"percentiles": {"field": "metaJob.budget.fixed", "percents": [75]}}
    }
  }
}
```

Caveat: `median_hourly_max` populates only on ~50% of hourly jobs — Upwork stores only a min rate for many postings. Report `hourly_min` as the reliable signal.

---

## Client-quality avgs

These are populated reliably, so `avg` aggs give meaningful per-bucket quality signals:

| Field | Interpretation |
|---|---|
| `metaJob.client.paymentVerified` | Boolean-as-avg → % of bucket's jobs posted by payment-verified clients (typically 80–89%) |
| `metaJob.client.stats.totalSpent` | Mean all-time USD spend on Upwork for the bucket's clients |
| `metaJob.client.stats.feedbackScore` | 0–5 |
| `metaJob.client.stats.hireRate` | 0–1 share of the client's prior job postings that resulted in a hire |

---

## ICP narrowing via `bool.filter`

To narrow a whole run to one category / subcategory / skill, merge an extra filter into the query's `bool.filter`:

```python
extra = {"term": {"metaJob.categoryName": "Sales & Marketing"}}  # exact casing
# or
extra = {"term": {"metaJob.skills.name.keyword": "Video Editing"}}
```

The bundled `run_aggs.py` exposes this via `--focus-category` / `--focus-skill`. Always verify the exact casing of the term — ES `term` queries are case-sensitive.

---

## Common exact casings (Upwork categories)

These are the 12 top-level categories as they appear in `metaJob.categoryName`. Use these strings exactly:

- `Web, Mobile & Software Dev`
- `Design & Creative`
- `Sales & Marketing`
- `Admin Support`
- `Engineering & Architecture`
- `Customer Service`
- `IT & Networking`
- `Data Science & Analytics`
- `Accounting & Consulting`
- `Writing`
- `Legal`
- `Translation`

If a user says "dev" or "design work", map to the full string above.

---

## Query skeleton (the one the bundled helpers produce)

```json
{
  "size": 0,
  "track_total_hits": true,
  "query": {
    "bool": {
      "filter": [
        {"range": {"metaJob.createdOn": {"gte": "2025-05-01", "lt": "2025-06-01"}}},
        {"term":  {"metaJob.categoryName": "Sales & Marketing"}}   // optional
      ]
    }
  },
  "aggs": {
    "top": {
      "terms": {"field": "metaJob.skills.name.keyword", "size": 100},
      "aggs": { /* quality_subaggs() */ }
    }
  }
}
```

---

## Mapping-probe pattern

When exploring a new field, first `GET /metajob-all/_mapping` (works under `metajob-ro`). Then do a `size=1` query projecting the field to see a real value. Only then write the aggregation. Getting the field name wrong is the single biggest time-sink in this index.

---

## Performance

- Terms aggs up to `size=200` on a month-sized window return in 1–3s. Don't go wider without a reason.
- `cardinality` estimates on high-cardinality text fields are cheap; exact counts via large terms aggs are not.
- The index has multiple shards — `top_hits` sub-aggs that require global sort are slow; avoid them for routine runs.

---

## `profile-*` indices (added 2026-07)

The `metajob-ro` role now also grants read on:
- `profile-skill*` (glob covers both `profile-skill` and `profile-skill-rank`)
- `profile-contractor*` — freelancer profiles
- `profile-agency*` — agency profiles + earnings
- `profile-metric-snapshot*` — weekly snapshots per entity (**two disjoint row types** — MRR + SERP; see below)

All open reads — the data mirrors public Upwork profile pages. Full field-type reference is in [data-reference.md §8](../../../references/data-reference.md#8-elasticsearch); the essentials for query authoring are below.

### Field-type gotchas (analogous to the metajob `.keyword` trap)

Same rule as `metajob*`: check the field type before writing terms aggs. Compact reference:

| Index | Field | Type | Terms-agg field |
|---|---|---|---|
| `profile-skill` | `name` | **`keyword`** | `name` (NO `.keyword`) |
| `profile-skill` | `slug`, `type`, `skillUid` | keyword | direct |
| `profile-contractor` | `name`, `skills.name`, `customKeywords.name`, `title` | text `[keyword]` | append `.keyword` |
| `profile-contractor` | `location.country`, `location.city`, `slug`, `role`, `defaultAgencyUid`, `skills.uid` | keyword | direct |
| `profile-contractor` | `description` | pure text | search only, no aggs |
| `profile-agency` | `name`, `owner.name` | text `[keyword]` | append `.keyword` |
| `profile-agency` | `country`, `city`, `region`, `services`, `topRatedStatus`, `numberOfEmployees`, `clientFocus` | keyword | direct |
| `profile-agency` | `title`, `description` | pure text | search only, no aggs |
| `profile-metric-snapshot` | all — `entityType`, `entityUid`, `weekKey`, `scopeType`, `scopeValue` | keyword | direct |

`country` in `profile-agency` is a display name (`"United States"`, `"Ukraine"`). The gigradar-monorepo writer filters with `case_insensitive: true` — exact casing is tolerated, but a `terms` agg returns whatever casing was ingested.

`services[]` holds Upwork skill **display names** (e.g. `"Video Editing"`), NOT skillUids. To filter agencies by a Upwork skill, use the display name here. Note the contrast with `profile-metric-snapshot.scopeValue` (SERP rows), which is the numeric skillUid.

### `profile-metric-snapshot` — two row types in one index

Doc-id prefix + field-presence discriminates:

| Row type | Doc-id prefix | Discriminator filter | What it holds |
|---|---|---|---|
| **MRR** | `mrr:<entityType>:<uid>:<weekKey>` | `{exists: {field: recentEarnings}}` | Weekly earnings snapshot per entity |
| **SERP** | `serp:<entityType>:<uid>:<scopeType>:<scopeValue>:<weekKey>` | `{exists: {field: scopeType}}` | Weekly leaderboard rank per (entity, scope) |

**Do not use `lifetimeEarnings` to discriminate** — it's nullable, ES `exists` returns false for stored null. Always use `recentEarnings` (MRR) or `scopeType` (SERP).

**`weekKey` format**: `YYYY-Wxx` (ISO-8601, e.g. `2026-W29`, Thursday owns the year). Order by `snapshotAt desc` when you want "latest wins" — different weeks and mid-week reruns both show up correctly.

**When to use each**: prefer `prevRecentEarnings` on `profile-contractor` / `profile-agency` for **week-over-week** growth (pre-computed by BF-3489). Reach for MRR rows here when you need **multi-week windows** or the per-week series. Reach for SERP rows for per-skill leaderboard positions over time.

### Skill uid ↔ name lookup

Cheap one-shot to build a `{skillUid: name}` map:

```json
POST /profile-skill/_search?size=10000
{
  "_source": ["skillUid", "name"],
  "sort": [{"skillUid": "asc"}]
}
```

### Skill competitiveness (distinct-contractor supply)

```json
POST /profile-skill-rank/_search?size=0
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"skillUid": "<uid-from-profile-skill>"}},
        {"range": {"createdAt": {"gte": "2026-05-01", "lt": "2026-06-01"}}}
      ]
    }
  },
  "aggs": {
    "distinct_contractors": {"cardinality": {"field": "upworkContractorUid"}},
    "top50_share": {
      "filter": {"range": {"rank": {"lte": 50}}},
      "aggs": {"contractors_in_top50": {"cardinality": {"field": "upworkContractorUid"}}}
    }
  }
}
```

### Rank drift for a specific contractor

```json
POST /profile-skill-rank/_search?size=0
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"upworkContractorUid": "~01abc..."}},  // includes leading tilde — see "Contractor uid format" below
        {"range": {"createdAt": {"gte": "now-90d"}}}
      ]
    }
  },
  "aggs": {
    "by_skill": {
      "terms": {"field": "skillUid", "size": 20},
      "aggs": {
        "rank_by_week": {
          "date_histogram": {"field": "createdAt", "calendar_interval": "week"},
          "aggs": {"min_rank": {"min": {"field": "rank"}}}
        }
      }
    }
  }
}
```

### Combined: growing-supply, growing-demand skills

Join two aggregations client-side:
- `metajob` — `doc_count` per `metaJob.skills.name.keyword` per window → demand delta
- `profile-skill-rank` — distinct `upworkContractorUid` per `skillUid` per window (map uid→name via `profile-skill`) → supply delta

Sweet spot for GigRadar: **demand growing faster than supply** — skills where our customers can win more jobs. Include this cross-index diff in monthly market reports.

### Top freelancers ranking for a skill + full profile hydration

Three-step join: `profile-skill` (name → uid) → `profile-skill-rank` (uid → top contractors) → `profile-contractor` (contractor uid → full profile).

```json
POST /profile-skill/_search?size=1
{"query": {"term": {"name": "Video Editing"}}, "_source": ["skillUid", "name"]}
```

```json
POST /profile-skill-rank/_search
{
  "size": 5,
  "_source": ["upworkContractorUid", "rank", "createdAt"],
  "query": {
    "bool": {
      "filter": [
        {"term": {"skillUid": "<uid-from-step-1>"}},
        {"range": {"createdAt": {"gte": "now-14d"}}}
      ]
    }
  },
  "sort": [{"rank": "asc"}, {"createdAt": "desc"}],
  "collapse": {"field": "upworkContractorUid"}
}
```

`collapse` deduplicates by contractor when the same contractor appears in multiple recent snapshots. Then:

```json
POST /profile-contractor/_search
{
  "query": {"terms": {"upworkContractorUid": ["~01abc...", "~01def...", ...]}},  // tilde included
  "_source": [
    "upworkContractorUid", "name", "title", "photoUrl",
    "skills", "defaultAgencyUid",
    "stats.jobSuccessScore", "stats.totalEarning", "stats.totalHours",
    "location.country", "location.city",
    "recentEarnings", "prevRecentEarnings"
  ]
}
```

Optional 4th call: hydrate `defaultAgencyUid` via `profile-agency` `{terms: {upworkAgencyUid: [...]}}`.

### Top-N agencies by week-over-week earnings growth (cheap version)

Uses `prevRecentEarnings` pre-computed on `profile-agency` — no metric-snapshot join needed.

```json
POST /profile-agency/_search
{
  "size": 20,
  "_source": ["upworkAgencyUid", "name", "country", "recentEarnings", "prevRecentEarnings", "logo"],
  "query": {
    "bool": {
      "filter": [
        {"range": {"recentEarnings": {"gt": 0}}},
        {"range": {"prevRecentEarnings": {"gt": 0}}}
      ]
    }
  },
  "runtime_mappings": {
    "growth_pct": {
      "type": "double",
      "script": {"source": "double p = doc['prevRecentEarnings'].value; if (p > 0) emit((doc['recentEarnings'].value - p) / p);"}
    }
  },
  "fields": ["growth_pct"],
  "sort": [{"growth_pct": {"order": "desc"}}]
}
```

### Top-N agencies by 6-month earnings growth (proper time-series)

Use `profile-metric-snapshot` MRR rows. Compare `recentEarnings` at two `weekKey`s ~26 weeks apart per agency.

```json
POST /profile-metric-snapshot/_search
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {"exists": {"field": "recentEarnings"}},
        {"term": {"entityType": "agency"}},
        {"terms": {"weekKey": ["2026-W03", "2026-W29"]}}
      ]
    }
  },
  "aggs": {
    "by_agency": {
      "terms": {"field": "entityUid", "size": 5000},
      "aggs": {
        "start": {
          "filter": {"term": {"weekKey": "2026-W03"}},
          "aggs": {"earn": {"max": {"field": "recentEarnings"}}}
        },
        "end": {
          "filter": {"term": {"weekKey": "2026-W29"}},
          "aggs": {"earn": {"max": {"field": "recentEarnings"}}}
        },
        "delta_pct": {
          "bucket_script": {
            "buckets_path": {"s": "start>earn", "e": "end>earn"},
            "script": "params.s > 0 ? (params.e - params.s) / params.s : 0"
          }
        },
        "sort_by_delta": {"bucket_sort": {"sort": [{"delta_pct": "desc"}], "size": 20}}
      }
    }
  }
}
```

Then bulk-hydrate the 20 winning `entityUid`s via `POST /profile-agency/_search {"query":{"terms":{"upworkAgencyUid":[…]}}}`.

### Agency SERP rank for a skill (weekly leaderboard)

```json
POST /profile-metric-snapshot/_search
{
  "size": 100,
  "query": {
    "bool": {
      "filter": [
        {"exists": {"field": "scopeType"}},
        {"term": {"entityType": "agency"}},
        {"term": {"scopeType": "service"}},
        {"term": {"scopeValue": "<skillUid-from-profile-skill>"}},
        {"term": {"weekKey": "2026-W29"}}
      ]
    }
  },
  "_source": ["entityUid", "rank", "total"],
  "sort": [{"rank": "asc"}]
}
```

### Cross-country agency comparison (Q C from the doc-test)

```json
POST /profile-agency/_search
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {"terms": {"country": ["United States", "Ukraine"]}},
        {"term":  {"services": "Video Editing"}}
      ]
    }
  },
  "aggs": {
    "by_country": {
      "terms": {"field": "country", "size": 5},
      "aggs": {
        "avg_recent": {"avg": {"field": "recentEarnings"}},
        "avg_jss":    {"avg": {"field": "jobSuccessScore"}},
        "median_min_rate": {"percentiles": {"field": "minRate", "percents": [50]}},
        "n": {"value_count": {"field": "upworkAgencyUid"}}
      }
    }
  }
}
```

`country` is a display-name string (writer uses `case_insensitive: true` when filtering, but agg output preserves ingested casing). `services` is a display-name string too. If a country name returns 0 buckets, probe with `{terms: {country: [], size: 200}}` to see the ingested values.

### Contractor uid format

`upworkContractorUid` values include Upwork's leading `~` — e.g. `~01abc0123456789def`. Store and query the tilde as part of the string:
```json
{"term": {"upworkContractorUid": "~01abc0123456789def"}}
```
The tilde is NOT a wildcard or optional prefix. If you built a query without it, you'll get zero hits.

### Computing `weekKey` values

`weekKey` is ISO-8601 `YYYY-Wxx` (Thursday-of-week owns the year). Compute it locally rather than hard-coding sample dates:

```bash
# Today's weekKey
date -u +'%G-W%V'
# 26 weeks ago (approx 6 months)
date -u -d '182 days ago' +'%G-W%V'  # GNU date
date -u -v-182d +'%G-W%V'            # BSD/macOS date
```

Python:
```python
import datetime as dt
d = dt.date.today() - dt.timedelta(days=182)
year, week, _ = d.isocalendar()
print(f"{year}-W{week:02d}")
```

**MRR retention**: `profile-metric-snapshot` is append-only — the BF-3489 handler writes weekly rows and does NOT prune. Available history goes back to when BF-3489 first ran in production (`git log gigradar-monorepo/.../mrr-snapshot-handler.ts` in the monorepo for the first-deploy date). If the earliest MRR row you need isn't there, fall back to a shorter window.
