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

## `profile-skill` + `profile-skill-rank` (added 2026-07)

The `metajob-ro` role now also grants read on `profile-skill*` (matches both `profile-skill` and `profile-skill-rank`). These indices are tiny compared to `metajob*` and carry no PII — full field access is fine.

### `profile-skill-rank` shape (rank history)

Only 4 fields: `upworkContractorUid` (keyword), `skillUid` (keyword), `createdAt` (date), `rank` (integer). One doc per `(contractor, skill, day)`.

`rank = 1` is top of the Upwork skill leaderboard; higher rank = worse position. Records are sparse — Upwork doesn't publish rank for every freelancer, and GigRadar only snapshots tracked skills.

### `profile-skill` shape (skill master)

Use it as a **name lookup** for the `skillUid` values that appear in `profile-skill-rank.skillUid` and `metaJob.skills.uid`. Fields: `skillUid`, `name`, `slug`, `type`, plus a sync-job state block that market research can ignore.

Fast pattern for building a `{skillUid: name}` map — one bulk fetch:

```json
POST /profile-skill/_search?size=10000
{
  "_source": ["skillUid", "name"],
  "sort": [{"skillUid": "asc"}]
}
```

### Skill competitiveness — how many distinct freelancers rank for X

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

`distinct_contractors` = supply proxy for the skill. Growing over time = more supply, harder to differentiate on that skill; shrinking = specialization opportunity. Cross-reference with `metajob` `doc_count` for the same skill to get supply vs demand.

### Rank-drift for a specific contractor

```json
POST /profile-skill-rank/_search?size=0
{
  "query": {
    "bool": {
      "filter": [
        {"term": {"upworkContractorUid": "~<contractor-uid>"}},
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

Then feed the `min_rank` time series into whatever alerting logic you want (weekly delta > 20 = "significant drop").

### Combined: growing-supply, growing-demand skills

Join the outputs of:
- `metajob` — `doc_count` per `metaJob.skills.name.keyword` for two consecutive windows → demand delta
- `profile-skill-rank` — distinct `upworkContractorUid` per `skillUid` for the same windows (map back to skill name via `profile-skill`) → supply delta

Sweet spot for GigRadar is **demand growing faster than supply** — those are skills where the market pays a premium and our customers can win more jobs. Include this cross-index diff in monthly market reports.
