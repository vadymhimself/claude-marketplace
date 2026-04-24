# Scanners — schema, bidding strategy, memory

Scanners are the atomic unit of the audit: each scanner has its own search query, its
own bidding strategy, and its own AI memory. The audit groups proposal performance by
scanner so that each scanner's config can be tuned independently.

---

## ⚠️ Join rule — scanner attribution lives on `opportunities`, not `proposals`

Every scanner-level metric (volume, reply rate, hire rate, $/reply, CL signature,
algorithm version) must be computed from `opportunities` and joined into `proposals`.
The `proposals` collection has **no** `scannerId`, `scannerName`, `templateId`,
`originalGigTempId`, `algorithmSignature`, `algorithmVer`, `promptVersion`, or
`model`. Those fields live on the matching `opportunities.application.*` sub-doc, and
join via:

```
opportunities.application.proposalId  ↔  proposals.meta.uid   (both strings)
```

Team filter asymmetry: `gigradarTeamId` (ObjectId) on `opportunities`,
`_gigradarTeamOid` (ObjectId) on `proposals`. Always include `isPreview: {$ne: true}`
on the opportunities side — preview opps are dry-runs that never went to Upwork.

**A proposal with no joinable opportunity is a manual bid**, not an auto-bidder
output. Manual bids cannot be attributed to a scanner and must be reported as a
separate "manual" cohort. On the Ubiquify probe (2026-04-22) manual bids converted
~16× better than auto-bidder output (35.35% reply vs 8.58%; 4.04% hire vs 0.249%) —
bucket them separately or the headline rates will be noise.

Drive scanner aggregations **from** `opportunities` (not from proposals) — filter
`gigradarTeamId` + `notified` window + `isPreview: {$ne: true}`, then `$lookup`
proposals by `application.proposalId → meta.uid`, then `$group` by `scannerId`.
See `data-reference.md` §10.C and §24.13 for the canonical pipeline, and
`audit-playbook.md` Section 5 for the aggregation shape.

---

## Storage

Scanners are **embedded inside `teams` documents** under `teams.scanners[]`. Not a
separate collection. Querying patterns:

```python
team = db.teams.find_one({"_id": ObjectId(team_id)})
for scanner in team.get("scanners", []):
    if scanner.get("deleted"): continue
    # scanner._id, scanner.name, scanner.query, scanner.biddingStrategy, scanner.memory
```

To enumerate all non-deleted scanners across a team:
```js
{ $match: { _id: ObjectId(teamId) } },
{ $unwind: "$scanners" },
{ $match: { "scanners.deleted": { $ne: true } } },
```

Scanner audit history lives in the separate `scanner.history` collection
(updates[] on scanner doc itself is deprecated).

---

## Scanner schema (condensed)

From `gigradar-definitions/index.ts` (api.Scanner interface):

```ts
interface Scanner {
  _id: ObjectId;
  name: string;
  createdBy?: string;          // User ID
  lastScan?: Date;
  updatedAt?: Date;
  query: GigsQueryV2;          // the search filter
  biddingStrategy?: BiddingStrategy<any>;
  scoring?: ScoringStats;      // match-scoring thresholds
  deleted?: boolean;
  version?: number;
  memory?: Memory;             // GPT scanner memory (feedback-trained)
  alerts?: {...};              // slack/telegram/email notification settings
  forceShowUSJobs?: boolean;
  forceShowUKJobs?: boolean;
}
```

### `query` (GigsQueryV2) — what jobs this scanner matches

Key fields the audit reads:
- `q` — free-text keywords
- `excluded` — negative keywords
- `categories` — Upwork category/subcategory IDs
- `budgets` — min/max budget per job type
- `countries` — client-country filter
- `clientIndustry` — e.g. "Tech & IT", "Retail & Consumer"
- `companySize` — client employee band
- `experienceLevel` — entry/intermediate/expert
- `workload` — part/full
- `duration` — less-than-month / 1-3-months / 3-6-months / more-than-6
- `talentPreference` — location / verified / hire-rate restrictions

In the audit, scanners with very broad `query` (large `q`, no exclusions, no category
narrowing) typically underperform on reply rate because the proposal pool is diluted
with poor-fit jobs. This is the #1 lever for "scanner too broad" recommendations —
and the kind of thing you'll see surface in the Win/Loss CL table as a pattern of
losses on off-target jobs.

### `biddingStrategy` — how proposals are generated + sent

```ts
interface BiddingStrategy<T extends BiddingAlgorithmOptions> {
  algorithmSignature: string;  // e.g. "sardor-ai-v2", "template", "laziza-ai"
  options: T;                   // algorithm-specific, see below
}
```

Shared options (`BiddingAlgorithmOptions`):

| Field | Purpose |
|---|---|
| `disabled` | **true = auto-bid off**. Audit must exclude disabled scanners from "active" counts. |
| `days`, `from`, `to`, `timezone` | time-of-day throttling |
| `biddingTerms.hourlyStrategy / hourlyValue` | how the scanner sets its bid rate (absolute $ vs % of client budget) |
| `biddingTerms.fixedStrategy / fixedValue` | same for fixed-price jobs |
| `advancedSettings.preferredLocation` | honor Upwork's preferred-location filter |
| `smartBoost.{enabled,type,boost}` | auto-boost config |
| `selectedPortfolioItems` | which portfolio items to attach |
| `preGenerate` | generate-then-schedule vs generate-at-send |
| `turboMode.enabled` | skip wait on generation completion |
| `autoBiddingDailyLimit`, `autoBiddingMonthlyLimit` | proposal volume caps |
| `connectsDailyLimit`, `connectsMonthlyLimit` | connects-spend caps |
| `upworkFreelancerUid`, `upworkFreelancerProfileUid` | which freelancer identity submits |
| `disabledAt`, `disabledReason`, `disabledCode` | last-auto-disable info |

Two bidding-algorithm subtypes extend this:

**TemplateBiddingAlgorithmOptions** (pure template substitution, no LLM):
```ts
{ template?: string; answerTemplate?: string; answers?: Record<string, string> }
```

**SardorAiBiddingAlgorithmOptions** (LLM with CL config override):
```ts
{
  template?: string;                    // starter template for LLM
  answerTemplate?: string;              // question-answer starter
  answers?: Record<string, string>;
  llmConfigOverride?: Partial<CoverLetterLlmConfig>;  // override team's default LLM + prompt version
  preMatcherConfigOverride?: PreMatcherConfig;
  selectedPortfolioItems?: string[];
}
```

**isAutoBiddingEnabled** helper: returns true iff `scanner.biddingStrategy` exists and
`options.disabled !== true`. The audit should use the same predicate when filtering
"active" scanners.

### `memory` (ai.Memory) — GPT scanner memory

```ts
interface Memory {
  metadata: { updated_at: Date; version: number };
  statements: { statement: string; date_created: string; type: 'opportunity' | 'application' }[];
}
```

Statements are accumulated from user feedback (thumbs up/down on opps). On each
proposal generation they get injected into the LLM prompt, so **memory is a first-class
input variable** for the audit. Teams with 0 memory statements and teams with 50+ noisy
contradictory memory statements both underperform.

When surveying a scanner: count statements, scan for duplicates, scan for contradictions.

---

## AutoBidder models

From `gigradar-definitions/ai/index.ts`:

```ts
enum AutoBidderType { Sardor = 'Sardor', Laziza = 'Laziza', Template = 'Template' }

type AutoBidderModel = 'Sardor Bidder' | 'Template Bidder' | 'None' | SardorLLMTypes;
type SardorVersions = 'sardor-ai-v1' | 'sardor-ai-v2' | 'laziza-ai';

enum SardorLLMTypes {
  GPT4 = 'gpt-4',
  GPT4_TURBO_PREVIEW = 'gpt-4-1106-preview',
  GPT4_O = 'gpt-4o',
  GPT5 = 'gpt-5',
  GPT51 = 'gpt-5.1',
  CLAUDE_SONNET_46 = 'claude-sonnet-4-6',
  CLAUDE35_SONNET = 'claude-3-5-sonnet-20240620',
  CLAUDE3_OPUS = 'claude-3-opus-20240229',
  CLAUDE3_SONNET = 'claude-3-sonnet-20240229',
}
```

In practice for 2026 audits, most production proposals use GPT-4o, GPT-5/5.1, or
Claude Sonnet 4.6. Template Bidder is legacy; audit findings showing "Template Bidder
reply rate < Sardor AI reply rate" on the same scanner is a common "move to Sardor"
recommendation.

---

## Scanner-level anti-patterns (common audit findings)

These surface repeatedly — recognize them fast:

1. **Scanner too broad.** Query `q` contains 8+ keywords with OR semantics, no excluded
   terms, no category filter. Symptom: low reply rate + high proposal volume + low match
   scores on opportunities. Fix: narrow query OR add excluded terms OR raise scoring
   threshold. Corroborate with Section 4 Win/Loss losses on off-target JDs.
2. **Scanner too narrow.** Query produces <10 proposals/week. Symptom: healthy reply
   rate but total replies below the statistical-significance floor (sent < 30). Fix:
   broaden terms, relax category filter, or merge with a sibling scanner.
3. **Rate mismatch.** `biddingTerms.hourlyValue` is well above median rate for the
   target segment (from market-research skill). Symptom: healthy view rate but low
   reply rate — clients opened the proposal then bounced on price. Fix: lower absolute
   rate or switch to percent-of-budget strategy.
4. **Memory pollution.** `memory.statements.length > 50` with many contradictions.
   Symptom: generic, confused cover letters. Fix: memory recreate (retool workflow).
5. **Disabled scanners still consuming attention.** `biddingStrategy.options.disabled = true`
   but user keeps asking about it in support chats. Fix: either re-enable or delete.
6. **Notification-only scanners.** `biddingStrategy` is empty or absent, so scanner
   only notifies but never sends. Not a problem — but flag these in the audit as
   "opportunity funnel only, not bid funnel" so the GM knows they shouldn't appear in
   reply-rate comparisons.

---

## Source files

- `gigradar-definitions/index.ts` — `api.Scanner`, `bidding.BiddingStrategy`, `BiddingAlgorithmOptions`, `TemplateBiddingAlgorithmOptions`, `SardorAiBiddingAlgorithmOptions`
- `gigradar-definitions/auto-bidder/index.ts` — `isAutoBiddingEnabled`, error-message catalog (ProposalErrorCode interpretations)
- `gigradar-definitions/ai/index.ts` — `AutoBidderType`, `AutoBidderModel`, `SardorLLMTypes`, `PromptVersion`, `Memory`, `MemoryStatement`
- `gigradar-definitions/scanners/scanner-utils.ts` — `ComparisonProps` (which fields define scanner identity for "same scanner after edit?")
