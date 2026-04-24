# Audit metrics — canonical formulas

All metrics in the customer audit MUST match the product dashboard. The source of
truth is `gigradar-aws-functions/services/utils/repositories/stats/stats-repository.ts`
and the benchmark recompute workflow
`schedulerWorkflowsV1/workflows/benchmark-stats.workflow.ts`. If your number disagrees
with the dashboard, 99% of the time the issue is in this file's rules.

> **North-star shortcut:** reply rate and $/reply are the two headline metrics for every audit. Hire rate and $/hire exist but are *diagnostic only* — because off-Upwork closes undercount hires. See "Metric glossary" below for priority labels.

---

## Base filter (always)

Every audit aggregation on `proposals` starts with the same match:

```js
{
  _gigradarTeamOid: ObjectId("<team id>"),
  "meta.createdAt": { $gte: from, $lt: to },
  "meta.inviteToInterviewUid": null,   // exclude invite-initiated replies
}
```

The `inviteToInterviewUid` filter is **mandatory** — otherwise GigRadar's reply rate
is conflated with "clients who invited the freelancer first", which is meaningless for
outbound attribution.

---

## Proposal-level indicators

Per the canonical StatsRepository + benchmark-stats workflow:

| Indicator | Definition |
|---|---|
| **Sent** | count of matching docs |
| **Reply** | `dashroomUID != null` (client opened a chat thread) |
| **View** | `dashroomUID != null` **OR** `status == 7` (ACTIVE) **OR** `12 ∈ otherAnnotations` (PROPOSAL_VIEWED) |
| **Interview-accepted** | `meta.status == 7` (ACTIVE) or `meta.status == 10` (HIRED). Per `notifications-ingestion.platrum.md` this is the INTERVIEW_ACCEPTED signal. |
| **Hired / Contract started** | `meta.status == 10` (HIRED) |
| **Offer won** | `meta.status == 9` (OFFER_WON) |
| **Offer lost** | `meta.status == 3` (OFFER_LOST / DECLINED_BY_CLIENT) |
| **Connects spent** | `terms.connectsBid if > 0 else meta.connectsExpended ?? connectsExpended ?? 0` |

ProposalStatus enum reference (from `upwork/index.ts`):
`SUBMITTED=2, OFFER_LOST=3, ACTIVE=7, JOB_CLOSED=8, OFFER_WON=9, HIRED=10`.

---

## Metric glossary — headline vs diagnostic

**North-star metrics** for customer audits (always reported first):

| Short | Long | Numerator | Denominator | Priority |
|---|---|---|---|---|
| **Reply rate** (canonical "LRR") | Lead Reply Rate | replies (`dashroomUID` non-empty) | sent | **HEADLINE** |
| **$/reply** | Cost per reply | `sum(connectsLadder) × $0.15` | replies | **HEADLINE** |

`connectsLadder = terms.connectsBid > 0 ? terms.connectsBid : (meta.connectsExpended ?? connectsExpended ?? 0)` — per-bid, not `$ifNull` (Upwork writes `0` as "no data").

**Diagnostic metrics** (reported with explicit population labels, never as the top-line):

| Short | Long | Numerator | Denominator | Priority |
|---|---|---|---|---|
| **PVR** | Proposal View Rate | views | sent | supporting |
| **Hire rate** (canonical "CR") | Hire rate | `meta.status ∈ {10, "Hired"}` via `$in` | sent | diagnostic only |
| **$/hire** | Cost per hire | `sum(connectsLadder) × $0.15` | hires | diagnostic only |
| **Interview rate** | Interview-accepted rate | `status ∈ {7, 10}` | replies | supporting |
| **CPR** | Connects Per Reply (count, not $) | connects | replies | legacy |

**Mongo query gotchas (must use these patterns):**
- Reply signal: `{"dashroomUID": {"$exists": True, "$nin": [None, ""]}}`. Never `{"$ne": None}` — missing-field semantics return zero.
- Hire status: `{"meta.status": {"$in": [10, "Hired"]}}`. Both int and string appear within a single team. Never `{"$eq": 10}`.
- `meta.chat.chatId` (dual-written mirror of dashroomUID) follows the same pattern — use `$exists` / `$nin: [null, ""]`.

**Why reply rate is the north star, not hire rate:** GigRadar users routinely close contracts off-Upwork — paid outside the platform, moved to Slack/email. `meta.status == 10` (HIRED) systematically undercounts real wins. `dashroomUID` non-empty is the least-lossy observable success signal. Hire rate stays on diagnostic sheets and detail rows; it is never the headline number.

**Why $/reply, not $/hire:** same reason — hire counts are censored by off-platform closes. $/reply is the robust economic metric tied directly to `connectsExpended * $0.15 / replies`.

**Interview rate denominator = replies, not sent.** Once the client has opened the chat, the question is "does the reply convert to an interview?". Keeping the denominator as replies isolates the CL→interview conversion step from the upstream pitch→reply step.

**Hire rate uses sent as denominator**, because that's the aggregate outbound conversion. Only reported alongside its population label (`"P-rank among N=xx teams with ≥100 sent"`), never `"P-rank overall"` with a filtered denominator.

> The canonical dashboard still exposes LRR and CR for continuity with existing product surfaces. When cross-referencing the dashboard use those labels. When writing audit output, use "reply rate" and "hire rate" — and hire rate only as diagnostic.

---

## Methodology: mean-of-per-team-rates (benchmarks)

When comparing a team to the benchmark, the benchmark stored in
`dashboard.benchmarks` uses `avgPvr` and `avgLrr` computed as **mean of per-team rates**,
not total-views / total-sent. This is unbiased — it prevents a few huge teams from
dominating the benchmark.

For **within-team** audits (this skill's core), compute rates using totals: the team
is a single denominator, so ratio-of-totals is correct.

Use mean-of-per-team-rates only when:
- Comparing across teams (benchmark lookup)
- Averaging across multiple teams in a sub-category study

---

## Per-scanner attribution

Proposals do not carry `scannerId` directly. The join is:

```
opportunities.application.proposalId (string = Upwork applicationUID)
  ↔ proposals.meta.uid (string)
opportunities.scannerId           ↔ teams.scanners[]._id
opportunities.application.algorithmSignature  → bidder signature
opportunities.application.model   → AutoBidderModel (Sardor Bidder, Template Bidder, etc.)
opportunities.application.promptVersion       → CL prompt version
opportunities.application.originalStrategy    → full strategy snapshot at send time
```

Opportunity match filter for the audit window:

```js
{
  gigradarTeamId: ObjectId("<team id>"),
  notified: { $gte: from, $lt: to },
  isPreview: { $ne: true },
  $or: [
    { "application.sent":  { $exists: true } },
    { "application.error": { $exists: true } },
  ]
}
```

That's the same filter the benchmark workflow uses — matches the dashboard exactly.

---

## CL prompt identity

A "CL prompt" for the audit is defined as the tuple:

```
(bidder_model, prompt_version, template_hash, answer_template_hash)
```

- `bidder_model` comes from `originalStrategy.algorithmSignature` (Template / SardorAiV2 / Laziza / etc.) or `application.model`.
- `prompt_version` comes from `application.promptVersion` or `originalStrategy.options.llmConfigOverride.prompt_version`.
- `template_hash` / `answer_template_hash` = short md5 of `originalStrategy.options.template` / `answerTemplate` — distinct template strings hash to distinct IDs.

Distinct tuples = distinct CL prompt variants. Group audit metrics by this tuple to
answer "which prompt is working for which scanner".

---

## Caveats (always flag in the audit)

1. **Reply-rate snapshot effect.** `dashroomUID` is written when a client first opens the
   chat thread — this can happen days or weeks after submission. Windows younger than
   60 days are biased low. Flag this for any `--windows` entry that ends within the last
   60 days.
2. **Status lag.** `meta.status` is updated on sync, which runs periodically. Interview
   and contract numbers for the most recent ~7 days are always under-counted.
3. **Sample-size cutoff.** Do not report a per-scanner rate with fewer than **30 proposals**
   in the window. Do not report a per-(scanner × CL prompt) rate with fewer than **15 proposals**.
4. **Connects top-up events.** If the team ran out of connects mid-window, their sent
   volume collapsed and rate comparisons are unstable. Check `application.error` code
   12 (InsufficientConnects) before drawing conclusions.
5. **Invite replies excluded.** Invite-initiated chats are separate — the filter
   `meta.inviteToInterviewUid: null` excludes them. Interview rate in the audit reflects
   outbound-only interview conversion.

---

## Source files

- `gigradar-aws-functions/services/utils/repositories/stats/stats-repository.ts` — canonical per-team rate formulas
- `gigradar-aws-functions/services/utils/repositories/benchmark/benchmark-repository.ts` — benchmark collection schema
- `gigradar-aws-functions/services/api/functions/schedulerWorkflowsV1/workflows/benchmark-stats.workflow.ts` — exact aggregation pipeline
- `gigradar-definitions/upwork/index.ts` — `ProposalStatus` enum and `ReverseEngineeredAnnotation` enum
- `gigradar-aws-functions/docs/ingestor/notifications-ingestion.platrum.md` — status-to-signal mapping (INTERVIEW_ACCEPTED, CONTRACT_STARTED)
