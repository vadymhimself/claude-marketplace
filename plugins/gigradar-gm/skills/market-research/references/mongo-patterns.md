# Mongo `proposals` — patterns & gotchas for reply-rate analytics

Canonical reference for the Mongo side of the market-research pipeline. Full details live in `data-reference.md` §4, §13, §14, §16; this file distills the patterns you need at the query desk.

---

## Access

- Host: `<mongo-host>:<port>` (prod Mongo, DocumentDB-compatible)
- DB: `gigradar-dev`
- Read-only user: `researcher-prod`
- Expected env vars: `MONGO_URI` (full URI with creds + `?authSource=admin`), optional `MONGO_DB`.

Example:
```
mongodb://researcher-prod:<password>@<mongo-host>:<port>/gigradar-dev?authSource=admin
```

---

## Canonical reply / view formula (MUST match)

Transcribed from `services/utils/repositories/stats/stats-repository.ts`. If your output disagrees with the product dashboard, this is usually why.

**Reply:** `proposal.dashroomUID` is non-null.
**View:** `dashroomUID` non-null OR `status === 7` OR `otherAnnotations` contains `12`.
**Base filter (proposals):**
```js
{
  "meta.createdAt": { $gte: from, $lt: to },
  "meta.inviteToInterviewUid": null,    // exclude invite-replies; we only count outbound proposals
  // _gigradarTeamOid: ObjectId(teamId)  // include this when narrowing to one team
}
```

In the bundled `mongo_reply_rates.py` this is encoded as:
```js
{
  $group: {
    _id: "$metaJob.categoryName",  // or subCategoryName, or $metaJob.skills.name after $unwind
    proposals: { $sum: 1 },
    replies:   { $sum: { $cond: [ { $not: "$dashroomUID" }, 0, 1 ] } },
    views:     { $sum: { $cond: [
      { $or: [
        { $ne: ["$dashroomUID", null] },
        { $eq: ["$status", 7] },
        { $in: [12, { $ifNull: ["$otherAnnotations", []] }] }
      ]}, 1, 0
    ]}}
  }
}
```

---

## Index hint is mandatory for cross-team aggregations

The collection has:
- `_gigradarTeamOid_1_meta.createdAt_1` (compound, tenant-first)
- `_gigradarTeamOid_1_meta.createdAt_-1` (compound, descending)
- `meta.createdAt_1` (standalone, date-only)

For **cross-team** market analytics (no team filter), the planner picks the compound tenant-first index by default and effectively scans the whole thing. Force the standalone date index explicitly:

```python
db.proposals.aggregate(pipe, hint={"meta.createdAt": 1}, allowDiskUse=True, maxTimeMS=600_000)
```

Measured impact (Apr 22 2026): a cross-team May 2025 category aggregation ran in **~95s with the hint** vs. timing out at **10 min without**. The bundled script always passes this hint.

For **single-team** queries (e.g. `--team-oid` narrow), do **not** pass the hint — the compound tenant-first index is correct there.

---

## `proposals.metaJob` — no ES join needed

`proposals.metaJob` embeds the full Upwork job shape at the moment the proposal was submitted. You can group directly by `metaJob.categoryName`, `metaJob.subCategoryName`, or unwind `metaJob.skills` and group by `metaJob.skills.name` — no need to `$lookup` into `opportunities` or join to ES.

This is the single biggest speedup compared to naive pipelines that walk opportunities → proposals.

---

## Null-category rows

~25–30% of proposals in any given month have `metaJob.categoryName = null`. These are older records from before the embedded-metaJob enrichment pipeline matured, or edge cases where the scanner emitted a proposal without enrichment.

They appear as a `null` bucket in `$group`. Always add a `{$match: {_id: {$ne: null}}}` stage after the group. They should be **excluded** from per-category tables but **included** in market-wide reply-rate totals.

---

## Proposal pool ≠ market

`proposals` represents only the bids submitted by GigRadar customer agencies, not the whole Upwork market. In May 2025, that was ~93k proposals against 198k Upwork job postings.

Consequence: the pool is heavily skewed toward **Web / Mobile / SW Dev** (~64% of proposals vs ~26% of market jobs). Categories underweighted by GigRadar customers (Translation, Customer Service, Legal) can have <1k proposals per month — any per-category reply-rate number has wide confidence intervals. **Always report the proposals denominator** alongside a reply rate.

---

## Reply-rate snapshot effect

`dashroomUID` is written when a client first opens a chat thread with the freelancer. This can happen days or weeks after the proposal was submitted. As a result:

- The **most recent month's** reply rate is always biased low — late replies haven't arrived yet.
- Observed: June 2025 reply rate queried in April 2026 was 7.7% vs 10.6% for May — the 10-month gap didn't fully close for June either, suggesting the late-reply tail matters materially.
- **Rule:** for stable numbers, use windows ≥ 60 days old. Flag this caveat in the executive summary whenever the focus window is recent.

---

## Team narrowing

`_gigradarTeamOid` is an `ObjectId`. From Python:
```python
from bson import ObjectId
match["_gigradarTeamOid"] = ObjectId("<24-hex>")
```
Bundled `mongo_reply_rates.py` exposes this via `--team-oid <hex>`.

---

## Connects spent (if you need it)

Not part of the core pipeline but sometimes requested. Priority ladder from `StatsRepository`:
```
terms.connectsBid (if > 0)  →  meta.connectsExpended  →  connectsExpended  →  0
```
Use `$cond + $gt` rather than `$ifNull`, because Upwork writes `0` for "no data" in some of these.

---

## Common analytical mistakes, in decreasing order of frequency

1. Forgot `hint={"meta.createdAt": 1}` on cross-team aggregations → timeout.
2. Grouped on `metaJob.skills.name` without `$unwind: "$metaJob.skills"` first → missed docs.
3. Included the `null` bucket in per-category table → made Design & Creative look 30% bigger than it is.
4. Didn't filter `meta.inviteToInterviewUid: null` → conflated invite-initiated chats with outbound replies, inflating reply rate.
5. Reported a single recent-month reply rate without the snapshot-timing caveat → readers draw the wrong trend.
