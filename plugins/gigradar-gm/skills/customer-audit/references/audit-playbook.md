# Audit playbook — retro-first decision tree

This file is the decision tree for running a customer audit. It documents **what to look for in each phase**, **what patterns probably mean**, and **what to recommend** — so audits are consistent across teams and not just a dump of numbers.

**Ordering rule (non-negotiable):** retro → competitive deep-dive (cohort compare + peer KNN) → chat transcripts → win/loss CL comparison → auto-bidding aggregates → three-tier synthesis. Do NOT start with auto-bidding tables.

**North-star metrics:** **reply rate** (not hire rate) and **$/reply** (not $/hire). Users often close contracts off-Upwork — hire metrics undercount wins. Hire rate is reported as a secondary diagnostic only.

All queries must be pinned to an index and use a minimalistic projection. See `references/data-reference.md` §17.3 for projection templates and §23 for the query-design checklist.

---

## ⚠️ Join rule #0 — the auto-bidder record lives on `opportunities`, not `proposals` (READ FIRST)

Every audit query that slices by scanner / template / algorithm / generated-CL MUST go through `opportunities`. The proposal document is the Upwork-side sync of what got submitted; the opportunity document is GigRadar's record of what the scanner surfaced and what the bidder generated.

These fields are ONLY on `opportunities.application.*`:
- `algorithmSignature`, `algorithmVer`, `promptVersion`, `model`, `config.*`
- `coverLetter` (generated CL), `originalStrategy` (frozen strategy snapshot at send time), `prompt`, `llmRawOutput`, `failedCoverLetters`
- `bid.{type,amount}`, `connectPrice`, `cost`, `matchPercentage`, `boost`, `rank`

And these are on the opportunity top-level (not `proposals`):
- `scannerId`, `scannerName`, `originalQuery`, `originalGigTempId` (the real "template" id), `score`

**Canonical join (matches `StatsRepository.getScannerStats` exactly):**

```
opportunities.application.proposalId  ↔  proposals.meta.uid
```

Both sides are **strings** (numeric-looking Upwork applicationUIDs). Team filters: `gigradarTeamId` on `opportunities`, `_gigradarTeamOid` on `proposals` (both ObjectIds). Do NOT join on `jobId ↔ meta.jobId` — that collapses when a job has multiple proposals and is not what the codebase uses.

**Manual-vs-auto split falls out of join coverage.** Proposals with no joinable opportunity are MANUAL bids (synced from Upwork, not originated by GigRadar). **BUT: also split the manual tail into invite-initiated vs outbound manual bids** (`meta.inviteToInterviewUid` non-null = invite). On Ubiquify (verified 2026-04-22): auto-outbound 8441 sent / 8.58% rr / 0.25% hr; manual-outbound 70 sent / 8.57% rr / 1.43% hr (near-identical reply rate to auto); manual-invite 29 sent / 100% rr / 10.34% hr (invite by definition). The prior "manual ~16× better" framing was collapsing the invite cohort into "manual" — apples-to-apples outbound manual vs outbound auto, reply rates are effectively identical; only hires differ (and only 6 total manual-outbound replies). Always break out **{auto|manual} × {invite|bid}** as four cohorts; blending hides where the audit lever actually is.

See `references/data-reference.md` §24.10a for the empirical probe and §10.C for the `$lookup` snippet. Scanner-level and CL-level aggregation must never start from `proposals` alone.

---

## Section 1 — Retro (the team's full Upwork history)

**Goal:** understand what won for this client historically, before touching auto-bidding data.

**Mindset:** this phase is creative research, not a script. Write queries as needed, follow threads that look interesting. Don't force every team through the same template. A team's "real" playbook often lives in 3-5 winning CLs from before they ever automated — find them.

**Primary pull (opportunities-first `$lookup`, NOT proposals-only):**

This pull is the foundation of every subsequent phase — run it once, cache the joined rows in an NDJSON and reuse. Filter by team on BOTH sides of the join (team asymmetry — `gigradarTeamId` on opportunities, `_gigradarTeamOid` on proposals).

```python
# all-time retro with opportunity join — cache this to NDJSON; every later phase reuses it
pipeline = [
  {"$match": {"_gigradarTeamOid": TEAM_OID,
              "meta.inviteToInterviewUid": None}},  # bid cohort only; invites handled separately
  {"$lookup": {
      "from": "opportunities",
      "localField": "meta.uid",
      "foreignField": "application.proposalId",
      "pipeline": [
        {"$match": {"gigradarTeamId": TEAM_OID, "isPreview": {"$ne": True}}},
        {"$project": {
          "_id": 1, "scannerId": 1, "scannerName": 1, "originalGigTempId": 1,
          "score": 1, "jobId": 1,
          "application.algorithmSignature": 1, "application.algorithmVer": 1,
          "application.promptVersion": 1, "application.model": 1,
          "application.config": 1, "application.bid": 1,
          "application.connectPrice": 1, "application.cost": 1,
          "application.boost": 1, "application.matchPercentage": 1,
          "application.generated": 1, "application.sent": 1,
          "application.coverLetter": 1,                  # generated CL (narrow-fetch for Win/Loss only)
          "application.originalStrategy": 1,             # frozen bidding-strategy snapshot
        }}
      ],
      "as": "opp"}},
  {"$project": {
      "_id": 1, "_gigradarTeamOid": 1,
      "meta.uid": 1, "meta.createdAt": 1, "meta.status": 1, "meta.jobId": 1,
      "meta.jobTitle": 1, "meta.chat.chatId": 1, "meta.connectsExpended": 1,
      "meta.freelancer.name": 1, "meta.freelancer.rid": 1,
      "meta.author.name": 1, "meta.inviteToInterviewUid": 1,
      "auditDetails.modifiedTs": 1, "auditDetails.createdTs": 1,
      "dashroomUID": 1, "status": 1, "otherAnnotations": 1,
      "terms.connectsBid": 1, "terms.hourlyRate": 1, "terms.amount": 1,
      "archiveReason.reason": 1, "archiveReason.reasonRef": 1,
      "declineReadon": 1,  # yes the typo field — see data-reference §24.2
      "connectsExpended": 1,
      "opp": 1,
  }},
]
```

**Interpretation rule: presence of a joined `opp` row = AUTO-BIDDER proposal. Empty `opp` = MANUAL proposal (bid submitted through Upwork directly, synced to GigRadar but not originated by the auto-bidder).** Always split the cohort this way and report both separately. See data-reference §24.10a; on Ubiquify the 1.2% manual tail carried 16× the auto-bidder reply rate, so blending silently hides the real story.

**Separately fetch all HIRED proposals** (wider projection for retro evidence): `{_gigradarTeamOid: teamOid, "meta.status": {"$in": [10, "Hired"]}}` — the status field is MIXED TYPE (int and string), always use `$in`. Include `auditDetails.modifiedTs`, `meta.jobTitle`, `"terms.hourlyRate"`, `"terms.amount"`, `"terms.connectsBid"`. Then narrow-fetch `renderedCoverLetter` (proposal-side text) and the joined opportunity's `application.coverLetter` (generated text) only for these.

**Reply-signal query: use `$exists` or `$nin: [None, '']`, NEVER `$ne: None`.** `{'meta.chat.chatId': {'$ne': None}}` returns 0 in pymongo because the field is absent (not null) on non-replied docs — see data-reference §24.8. Codebase-canonical definition uses top-level `dashroomUID` (see §13) which is dual-written with `meta.chat.chatId` and agrees on real data; either works, but pick one and stay consistent.

**Complementary signals (follow as useful):**
- **Upwork profile history:** `upwork.agency.profiles.find({gigradarTeamId: teamOid})` — profile description, positioning, hourly rate range, services. Also check `upwork.freelancer.profiles` via proposals' `meta.freelancer.rid`. This tells you how the team presents itself — which matters as much as the CL.
- **Chat rooms on old hires:** if a pre-GigRadar hire has a `meta.chat.chatId`, pull the first 10 messages via `leads.chats.messages`. Clients' first reactions on a win are gold for reverse-engineering what worked.
- **Scanner history** (if rich): `scanners.find({_gigradarTeamOid: teamOid})` with created/updated timestamps — what queries has the team tried? Which lived, which got deprecated?

**Investigation prompts — answer these with the data, don't tick boxes:**
- Do they have pre-GigRadar wins? Quote the 3 best winning CLs verbatim. What is the repeated motif — opening hook, specificity level, question answered, rate positioning?
- What changed between the pre-GigRadar winning template and the current auto-generated CL? Is there an obvious regression?
- What kinds of jobs were they targeting before vs now? Has the target space drifted? If so, was that deliberate (positioning shift) or accidental (scanner drift)?
- What's the time-to-close distribution for hires (`auditDetails.modifiedTs - meta.createdAt`)? Long tails mean the CL gets read later — CL design can tolerate slower openers. Short tails mean hot-reply jobs — first-line hook matters most.
- Is there a rate or engagement (hourly vs fixed, part-time vs full-time) that correlates with wins?
- Is there a recurring client or client type across wins (same industry, same geography, same company-stage)? That's a target-profile hint.
- What does their profile description emphasize — and does the current CL match that positioning or contradict it?

**Output into workbook — "Retro Evidence" sheet:**
- Timeline of HIRED proposals by `auditDetails.modifiedTs`.
- Quote the first 200-400 chars of each winning CL.
- Profile description excerpt.
- 1-2 standout chat excerpts from old hires.
- Explicit delta: "pre-GigRadar template did X → current auto-generated template does Y."

**If the team has <5 historical wins,** the retro alone won't form a hypothesis. Proceed to Section 2B peer look-alike as the PRIMARY signal, with the thin retro as context.

---

## Section 2 — Competitive deep-dive (cohort compare + peer look-alike vector search)

**Goal:** situate the customer relative to peers on real metrics AND harvest concrete competitive CL / rate / profile patterns. Understanding peers informs every downstream recommendation — this belongs at the top, not the end.

This is one consolidated section with two parts. Always run both.

### Section 2A — Cohort compare (reply rate, $/reply, view rate as headline)

**Steps:**
1. Pull `teams.serviceNames[]` for the subject team — the category signal.
2. **Sibling teams:** `teams.find({serviceNames: {$in: subject.serviceNames}, _id: {$ne: teamOid}})`, limit to teams with ≥100 sent in the focus window.
3. For each sibling, aggregate:
   - **Reply rate** = `count(chatId != null) / sent` — headline.
   - **$/reply** = `sum(connectsExpended) * 0.15 / replies` — headline.
   - **View rate** (if available) — diagnostic.
   - **Hire rate** — diagnostic (secondary).
   Compute cohort median + p75 per metric.
4. **`dashboard.benchmarks`:** for each subject category, pull `avgLrr`, `avgCpr`, `pvrStd`, `lrrStd` (avoid leaning on `avgHireRate`). Per-category cohort averages.
5. Rank the subject on each headline metric: below-median / median / above-median / above-p75.

**Fallback when `dashboard.benchmarks` is missing for the exact category:**
- Infer the agency category from Upwork profile description + scanner names/queries ("automation agency", "AI/ML consulting", "web-dev shop", …).
- Either (a) pull the closest-matching `dashboard.benchmarks` category, or (b) query sibling teams directly by `serviceNames` similarity + profile-text similarity, compute medians ad-hoc.
- Never silently skip. Produce a number even if approximate and note the inference in the caveats.

**Report cohort size explicitly:** "across N=23 peer teams in Web/Mobile/SW Dev with ≥100 sent in window, median reply rate = 5.8%, p75 = 8.1%."

**Percentile-reporting rule (critical):** whenever you quote a percentile rank, state the population inline:
- **All qualifying teams** (≥100 sent) — use for reply rate, view rate, $/reply. The customer's P-rank is directly comparable here.
- **Teams with ≥1 hire in window** — use only if reporting hire rate as a diagnostic. Otherwise the rank is dominated by the zero-hire long tail (88% of auto-bidding scanners never hire in any 90-day window) and inflates apparent standing. Always name the filter inline (e.g. "P47 hire rate among 158 teams with ≥1 hire in 90d").
- Never mix denominators in the same rank sentence. Never quote "P77 overall" on a metric whose population is filtered.

### Section 2B — Peer look-alike vector search (ES metajob KNN)

### ⚠️ Embedding coverage constraint (READ FIRST)

`matcher.embedding` in the `metajob` index (dense_vector, 1536 dims — OpenAI `text-embedding-3-small` shape) was rolled out mid-2025. Monthly coverage:

| Month | Embedded docs |
|---|---:|
| 2025-01..03 | ~0-5 |
| 2025-04 | 510 |
| 2025-05 | 392 |
| 2025-06 | 18,583 |
| 2025-07..09 | ~19-21k each |
| 2025-10 | 106,596 (near-full) |
| 2025-11+ | 150k+ (full) |

**Therefore:**
- KNN is **valid for seed jobs created ≥ 2025-10** (full coverage). Partial ≥2025-06.
- **Do NOT try KNN on pre-2025-06 jobs** — embeddings don't exist.
- The client's OWN pre-GigRadar retrospective (Section 1) is pure Mongo on `proposals`, no ES.
- Before any KNN call, verify `_source.matcher.embedding` is a 1536-length array on the seed. If absent, skip that seed.
- **topBids field is dead/anonymized — do not use.** See below.

### Flow
1. Pick seed jobs — TWO complementary approaches, use both:
   - **Customer-seeded (primary):** 2-3 of the team's RECENT proposals with strong outcomes (hire or strong reply) in the last ~90 days. Neighborhoods around customer-hires often contain 0-5 other applied teams — thin but high-relevance.
   - **Rich-seed discovery (complement):** search ES for seeds where ≥1 GigRadar team was hired, since ≥2025-10. Rank by `len(appliedByTeams)`. Richest seeds (7-10 applied teams) produce neighborhoods with multiple distinct winners. Prefer seeds aligned to the customer's vertical (match by `categoryName` / `ontologySkillNames`).
     ```json
     {
       "query": {"bool": {
         "must": [{"range": {"metaJob.createdOn": {"gte": "2025-10-01"}}}],
         "filter": [{"nested": {"path": "matcher.appliedByTeams",
                                 "query": {"term": {"matcher.appliedByTeams.proposalStatus": 10}}}}]
       }},
       "size": 30,
       "_source": ["metaJob.ciphertext","metaJob.title","metaJob.createdOn","matcher.appliedByTeams"]
     }
     ```
2. Fetch each seed's `matcher.embedding`. Skip any that return None.
3. For each seed, KNN query on `matcher.embedding`, **without** the team filter:
   ```json
   {
     "knn": {"field": "matcher.embedding", "query_vector": [...],
             "k": 30, "num_candidates": 300},
     "_source": ["metaJob.ciphertext","metaJob.title","metaJob.createdOn",
                 "metaJob.budget","metaJob.client.stats.totalSpent",
                 "metaJob.meta.jobTrend.clientActivity.totalApplicants",
                 "matcher.appliedByTeams"],
     "size": 30
   }
   ```
4. Walk `matcher.appliedByTeams[]` in the hits. Keep `proposalStatus == 10` OR `isInterviewed == true`. Dedup by `(teamId, ciphertext)`. Tally winning teams across neighborhood — top `teamId`s are the customer's direct competitors on that job type.
5. For each look-alike winner, harvest:
   - Proposal: `proposals.findOne({_gigradarTeamOid: ObjectId(teamId), "metaJob.ciphertext": ciphertext})`, projection `{renderedCoverLetter: 1, "terms.connectsBid": 1, "terms.hourlyRate": 1, "terms.amount": 1, templateId: 1, algorithmSignature: 1, "auditDetails.modifiedTs": 1}`.
   - Agency profile: `upwork.agency.profiles.findOne({gigradarTeamId: ObjectId(teamId)})`, projection `{description: 1, hourlyRate: 1, services: 1, title: 1}`.

**`metaJob.meta.topBids` is DEAD — do not harvest.** Observed 2026-04: all `bids.name` are placeholders (`"1st place"`, `"2nd place"`), `bids.connects: 0` everywhere. Anonymized, useless. Use `appliedByTeams` + Mongo lookups.

### Harvest into workbook — "Competitive Deep-Dive" sheet
- Cohort table on top: reply rate median / p75 / customer rank; $/reply median / p75 / customer rank; view rate; hire rate (diagnostic only, with population label).
- Peer look-alikes below: seed-job title, similarity score, look-alike job title + `metaJob.createdOn`, winner team anonymized, hourly rate, profile description excerpt, CL excerpt (first 400 chars), `auditDetails.modifiedTs`.
- Pattern-spot columns: common opening hooks, common rate bands, common positioning statements, recurring skills.
- **Always record the seed job's `metaJob.createdOn`** — if pre-2025-10, footnote "partial embedding coverage window."

Use these patterns to propose concrete edits to the subject team's CL / rate / profile in Section 6 recommendations.

---

## Section 3 — Chat-room transcript reading

**Goal:** see what the client actually said after opening a chat. Separates "CL worked" from "CL got opened but couldn't close."

**Flow per HIRED or replied proposal:**
1. `leads.chats.findOne({upworkRoomUid: proposal.meta.chat.chatId})` — projection `{upworkRoomUid: 1, gigradarTeamId: 1, jobDetails: 1, startedAt: 1}`.
2. `leads.chats.messages.find({upworkRoomUid: room.upworkRoomUid}).sort({createdAt: 1})` — projection `{upworkStoryUid: 1, text: 1, "author.type": 1, "author.name": 1, createdAt: 1, type: 1}`.
3. Read the first 5-10 messages. Look for:
   - **Client's first question** — rate? timeline? specific capability?
   - **Rate pushback** — did the client counter? To what?
   - **Scope change** — did the engagement proposal shift (hourly ↔ fixed, part-time ↔ full-time)?
   - **Drop-off point** — at which freelancer message did the client stop replying?

**Output — "Chat Excerpts" sheet:** 3-5 high-signal exchanges verbatim, each with a one-line interpretation.

**Signal library:**
- Client asks for hourly when CL quoted fixed → rate/positioning mismatch.
- Client asks for references → portfolio weak or generic.
- Client asks for a quick call → CL was strong; hand-off process needs tightening.
- Client ghosted after freelancer quoted rate → rate mismatch relative to JD budget.

---

## Section 4 — Win/Loss CL comparison (THE core scanner diagnostic)

**Goal:** understand WHY individual scanners/templates win or lose by comparing paired real examples. This is the concrete, actionable artifact — more useful than any distribution statistic.

**This section replaces the former "scanner quality via rejection reasons" grid.** We dropped that grid: in practice, `archiveReason` / `OpportunityFeedbackReason` / `ApplicationFeedbackReason` distributions do not surface actionable insights for real audits. Paired example analysis does.

### Cherry-picking strategy

For each scanner with meaningful volume in the focus window (say, ≥50 sent, or top 10 scanners by volume):

- **1-3 BEST winning proposals:**
  - Prefer HIRED (`meta.status == 10`).
  - If no hires, pick strong replies: multi-message client back-and-forth (pull `leads.chats.messages` count per `upworkRoomUid`), client asked substantive follow-up questions, client offered a call, long conversation duration.
  - Weight by client quality: `client.stats.totalSpent` high, `client.stats.feedbackScore` ≥ 4.8, `client.stats.hireRate` reasonable.

- **1-3 WORST losing proposals:**
  - Sent, zero reply, ideally declined or archived with a clearly relevant reason.
  - Prefer losses on jobs that *looked like good fits on paper* — high match score, fits the scanner's intended niche, client had good stats. Those are the most instructive losses.

### For every cherry-picked proposal, pull the full context (proposal side + opportunity side — they carry different fields)

**Proposal side** (Upwork-sync fields):
```python
proposals.find_one(
  {"_id": proposal_id},
  projection={
    "meta.uid": 1, "meta.jobId": 1, "meta.jobTitle": 1, "meta.createdAt": 1,
    "meta.status": 1, "meta.chat.chatId": 1, "meta.connectsExpended": 1,
    "meta.freelancer.name": 1, "meta.author.name": 1,
    "renderedCoverLetter": 1,       # text actually shown on Upwork (proposal-side)
    "coverLetter": 1,                # legacy / fallback field
    "terms.connectsBid": 1, "terms.hourlyRate": 1, "terms.amount": 1,
    "client.buyer.info.company.name": 1,
    "client.buyer.info.company.id": 1,
    "client.buyer.info.company.description": 1,
    "client.buyer.info.company.profile.industry": 1,
    "client.buyer.info.company.profile.size": 1,
    "client.stats": 1,
    "archiveReason.reason": 1, "declineReadon": 1,
    "auditDetails.modifiedTs": 1, "auditDetails.createdTs": 1,
    "dashroomUID": 1, "status": 1, "otherAnnotations": 1,
  }
)
```

**Opportunity side** (auto-bidder / scanner / CL-generation fields — only exists for auto-bidder proposals):
```python
opportunities.find_one(
  {"application.proposalId": proposal["meta"]["uid"], "gigradarTeamId": TEAM_OID},
  projection={
    "_id": 1, "scannerId": 1, "scannerName": 1, "score": 1,
    "originalGigTempId": 1,   # the real "template" id (NOT `templateId` on proposal)
    "originalQuery": 1,        # the scanner filter snapshot
    "jobId": 1, "jobUid": 1,
    "application.algorithmSignature": 1, "application.algorithmVer": 1,
    "application.promptVersion": 1, "application.model": 1,
    "application.config": 1,
    "application.coverLetter": 1,      # GENERATED CL (what the bidder produced pre-edit)
    "application.originalStrategy": 1, # frozen bidding-strategy snapshot
    "application.prompt": 1, "application.llmRawOutput": 1,
    "application.failedCoverLetters": 1,
    "application.bid": 1, "application.connectPrice": 1, "application.cost": 1,
    "application.boost": 1, "application.matchPercentage": 1,
    "application.matchPercentageArgumentation": 1,
    "application.generated": 1, "application.sent": 1,
  },
)
```

If the `opportunities.find_one` returns None, the proposal is MANUAL — flag it as such in the Win/Loss table (manual proposals are still eligible for cherry-picking if the team's manual tail carries disproportionate outcomes; see the join-rule callout).

Then pull the job description — ES is authoritative for current jobs, Mongo `temp.gigs` may exist for recent jobs but has 30-day TTL. Prefer ES:
```python
# ES (authoritative)
GET /metajob/_doc/{ciphertext}
```

Fall back to `temp.gigs.findOne({"metaJob.ciphertext": opp["jobId"]})` only if ES is unavailable. Don't rely on "job description on opportunity" — the opportunity has `jobId` (ciphertext) but not the full JD body.

And pull scanner config from the team doc (scanners are EMBEDDED in `teams.scanners[]`, NOT a separate collection):
```python
team = db.teams.find_one({"_id": TEAM_OID}, {"scanners": 1})
scanner = next((s for s in team["scanners"] if s["_id"] == opp["scannerId"]), None)
# Projection: scanner.name, scanner.query, scanner.biddingStrategy, scanner.memory
```

### Build the Win/Loss CL Table

A single workbook sheet, grouped by scanner. Columns:

| Column | Content |
|---|---|
| Outcome | WIN / LOSS + color band |
| Scanner | name (e.g. `AM-05-C`) |
| Template | templateId |
| Algorithm | algorithmSignature + version |
| Date | `meta.createdAt` for send, `auditDetails.modifiedTs` for WIN |
| Job title | `meta.jobTitle` |
| Job excerpt (first 400 chars) | `opportunities.description[:400]` |
| CL excerpt (first 400 chars) | `renderedCoverLetter[:400]` |
| Terms | "$X/hr" or "$X fixed", connects |
| Client | `company.name`, industry, totalSpent, feedbackScore, hireRate |
| Chat length (WINs only) | message count |
| Notes | one-line interpretation |

Group wins and losses adjacently per scanner. The visual juxtaposition is the analysis — the GM should be able to see in one row-flip what winners do differently.

### Derive edit suggestions

From the paired table, draft concrete edit proposals. Examples of the form:
- "Scanner `AM-05-C` template opens with `Thanks for sharing your project` on both losses; winners open with `For a [industry] at [size] stage, the [tech] pattern I'd recommend is…`. Rewrite opener to reference JD's first-paragraph context and tech stack."
- "Losses on scanner `PY-07` all quoted $75/hr fixed; wins quoted hourly $50-$70. Clients in this segment prefer hourly — switch `biddingStrategy.format` to hourly for PY-07."

These become Tier 3 CRITICAL recommendations when the evidence is clean; Tier 2 OKAY opportunities when the pattern is suggestive but thin.

### DO NOT

- Aggregate across scanner sibling variants (`-A`, `-B`, `-C`) — they're controlled experiments. Table each variant separately.
- Build a rejection-reason distribution grid. The reason distributions don't surface real insight; this paired table replaces them.
- Recommend 3+ scanner-query changes to the same scanner in one run — attribution collapses.

---

## Section 5 — Auto-bidding aggregates (supporting context)

**Goal:** aggregate tables that frame Section 4 win/loss narratives economically. Secondary, not headline.

**Slice on:** `opportunities.scannerId × opportunities.originalGigTempId × opportunities.application.algorithmSignature` (or `application.algorithmVer`). **These fields live on `opportunities`, not `proposals`.** Use the canonical `$lookup` pattern from `data-reference.md` §13.6 / §24.13 — opportunities-driven aggregation joining proposals for the reply/hire signal. This is what `StatsRepository.getScannerStats` does:

```python
db.opportunities.aggregate([
  {"$match": {
      "gigradarTeamId": TEAM_OID,
      "notified": {"$gte": focus_from, "$lt": focus_to},
      "isPreview": {"$ne": True},
  }},
  {"$lookup": {
      "from": "proposals",
      "localField": "application.proposalId",
      "foreignField": "meta.uid",
      "pipeline": [
        {"$match": {"_gigradarTeamOid": TEAM_OID,
                    "meta.inviteToInterviewUid": None}},  # bid cohort only
        {"$project": {"_id": 0, "dashroomUID": 1, "status": 1,
                      "meta.status": 1, "otherAnnotations": 1,
                      "meta.chat.chatId": 1,
                      "meta.connectsExpended": 1, "terms.connectsBid": 1,
                      "connectsExpended": 1}},
      ],
      "as": "proposal",
  }},
  {"$unwind": {"path": "$proposal", "preserveNullAndEmptyArrays": False}},
  {"$group": {
      "_id": {"scannerId": "$scannerId",
              "templateId": "$originalGigTempId",
              "algo": "$application.algorithmSignature"},
      "scannerName": {"$last": "$scannerName"},
      "sent": {"$sum": 1},
      "replies": {"$sum": {"$cond": [{"$and": [
          {"$ne": ["$proposal.dashroomUID", None]},
          {"$ne": ["$proposal.dashroomUID", ""]},
      ]}, 1, 0]}},
      "hires": {"$sum": {"$cond": [
          {"$in": ["$proposal.meta.status", [10, "Hired"]]},
          1, 0]}},
      "connects": {"$sum": {"$cond": [
          {"$gt": ["$proposal.terms.connectsBid", 0]},
          "$proposal.terms.connectsBid",
          {"$ifNull": ["$proposal.meta.connectsExpended",
                       {"$ifNull": ["$proposal.connectsExpended", 0]}]}]}},
  }},
])
```

The `$unwind` with `preserveNullAndEmptyArrays: False` drops opportunities without a joined proposal (matcher surfaced the job but no bid was sent — not what Phase 5 is measuring). Conversely, proposals without a joined opportunity (the manual cohort) are invisible to this aggregation; that's fine for Phase 5 because it's specifically the auto-bidder diagnostic — but Section 1 must have already split and reported the manual cohort separately.

**Compute per slice (focus window + prior window):**

| Column | Formula |
|---|---|
| Sent | `count(opportunities with joined proposal)` (bid cohort; invites excluded via proposals pipeline filter) |
| Replies | `count(proposal.dashroomUID truthy)` — equivalent to `meta.chat.chatId` populated on real data (see data-reference §13.1 and §24.11) |
| **Reply rate** (headline) | `replies / sent` |
| Connects $ spent | `sum(connects_ladder) * $0.15` where ladder = `terms.connectsBid > 0 ? terms.connectsBid : meta.connectsExpended ?? connectsExpended ?? 0` (NOT plain `$ifNull` — Upwork writes `0` as "no data"; see data-reference §13.5) |
| **$ per reply** (headline) | `connects_spent / replies` |
| Hires (diagnostic) | `count(proposal.meta.status ∈ [10, "Hired"])` — MIXED TYPES, always use `$in` (data-reference §24.7) |
| Hire rate (diagnostic) | `hires / sent` — always label population |
| Δ reply rate | `rr_focus - rr_prior` |
| Δ $/reply | `cpr_focus - cpr_prior` |

**Signal priority:** reply rate + $/reply are primary. Hires may be under-reported (off-Upwork closes); don't anchor recommendations to hire counts alone.

**For hires bucketed by month, use `auditDetails.modifiedTs`** (NOT `meta.createdAt`).

**Highlighting rules in the workbook:**
- Green: reply rate above cohort p75 with sent ≥ 30.
- Red: reply rate == 0 AND sent ≥ 100 (wasted connects).
- Amber: reply rate below team_avg × 0.6 with sent ≥ 30.
- Grey out rows with `sent < 15`.

**DO NOT:**
- Headline hire rate or $/hire — they're misleading (off-Upwork closes undercount).
- Compute a separate interview rate. Interview = reply = chat-id.
- Slice on `promptVersion` / `llm` / `proof_reader_version`.
- Draw independent recommendations from this sheet alone — every recommendation must be backed by a Section 4 Win/Loss CL table row or a Section 2 look-alike.

---

## Section 6 — Three-tier synthesis (WINS / OKAY / CRITICAL)

**Goal:** convert all prior evidence into a structured, visually obvious executive summary. This is sheet 1 of the workbook — the first thing the GM sees.

**Structure (required, in this order):**

### Tier 1 — WINS (green band)

What's working, with exact evidence. For each win, include:
- Dated hire(s) or strong reply(ies) — `auditDetails.modifiedTs` for close, `meta.createdAt` for the proposal.
- Scanner name, template ID, algorithm signature.
- CL excerpt (first 200-400 chars) that won.
- Contract / engagement details: `terms.hourlyRate` or `terms.amount`, client name/industry if available.
- Reply rate and $/reply for the scanner × template slice that produced the win — with cohort percentile.
- Explicit "keep doing this" note — what motif to preserve.

### Tier 2 — OKAY (amber band)

At-or-near-benchmark areas with open opportunities. For each row:
- The metric and its value (reply rate, $/reply, view rate — with population label).
- Benchmark it's being compared to (cohort median / p75 / category average) — name the benchmark.
- Delta (pp or $) — name the gap.
- Opportunity — the concrete change that could move it into WINS. Must cite a Section 4 Win/Loss CL table row or a Section 2B peer look-alike as evidence.

### Tier 3 — CRITICAL (red band)

Must-fix-immediately items. For each row:
- The failure and its cost — "Scanner `X-07` sent 312 proposals in 90d, 0 replies, $46.80 wasted."
- Root cause from evidence — cite the Section 4 Win/Loss pair or Section 3 chat drop-off that explains it.
- The specific change — not "improve" or "review," but "pause scanner X-07 until query is rewritten" or "rewrite template T-12 opener from A to B."
- Evidence cell linking to the detail-sheet row (workbook hyperlink via xlsx skill `=HYPERLINK(...)`).

### Formatting rules for sheet 1

- Dark-mode palette: background `#0f1419`, text `#e6edf3`. WINS band `#1f6f3f` / text `#9ef0b2`. OKAY band `#7a5a1a` / text `#f3d582`. CRITICAL band `#7a2021` / text `#f1949a`.
- Section headers: 14pt bold, tier color.
- Evidence cells: cell hyperlink to the detail sheet and row (Retro Evidence, Competitive Deep-Dive, Chat Excerpts, Win/Loss CL Table, Auto-Bidding Aggregates).
- CL excerpts: monospaced cell font (`Courier New` or `JetBrains Mono`) for readability.
- Each tier row shows its source section so the GM can drill down.

**Rules:**
- Every CRITICAL item must be backed by a Section 4 Win/Loss table row OR a Section 3 chat transcript excerpt.
- Every OKAY opportunity must cite at least one Section 2B peer look-alike or Section 4 Win/Loss pair.
- Every WIN must quote the actual CL or chat excerpt.
- No vague recommendations ("improve cover letters," "review scanner quality"). Every line is a specific, one-change action.
- No hype. Plain-stated evidence.

---

## Statistical hygiene

- **Significance floor:** do not report a rate with `sent < 30`.
- **Delta floor:** absolute delta < 1pp is noise for typical team sizes.
- **Wilson CI** for headline reply-rate claims — keep the half-width in the caveats footnote. Target half-width ≤ 2pp for any "statistically meaningful" claim.
- **Reply rate is the ground-truth headline.** Hire rate is a secondary diagnostic and is systematically under-counted by off-Upwork closes; never anchor recommendations solely on hire counts.
- **Multiple comparisons:** if reporting 20+ per-slice rankings, flag that top movers are partially luck. Prefer cross-validated statements ("held up in both the last 30 days and the prior 30 days").
- **Seasonality:** Q1 post-holidays and December are both soft; avoid cross-year comparisons without an explicit seasonal caveat.
- **Snapshot lag:** hire-close (`auditDetails.modifiedTs`) trails `meta.createdAt` by days to weeks. Windows ending <30 days ago under-report hires — another reason to headline reply rate rather than hire rate.
- **Population labels** on every percentile. "P62 reply rate among N=147 peers with ≥100 sent" is the correct form. Never mix denominators.

---

## Output — single dark-mode xlsx

One workbook, dark palette (background `#0f1419`, light text; WIN green, OKAY amber, CRITICAL red). NO separate `.md` summary file.

**Sheet order:**
1. **Executive Summary** — three-tier WINS / OKAY / CRITICAL block with hyperlinks into detail sheets. This is Section 6 output.
2. **Retro Evidence** — timeline of HIRED proposals, winning CL excerpts, profile description, pre-vs-auto template delta. (Section 1)
3. **Competitive Deep-Dive** — cohort compare table on top (reply rate / $/reply / view rate / hire rate-diagnostic, with population labels); peer look-alike winners below (seed + KNN hits with CL, rate, profile excerpts). (Section 2)
4. **Chat Excerpts** — 3-5 client-side exchanges verbatim with one-line interpretation. (Section 3)
5. **Win/Loss CL Table** — paired winners & losers per scanner, grouped by scanner, with scanner config, template ID, algorithm signature, CL excerpt, JD excerpt, client stats, chat length for wins. This is the core diagnostic. (Section 4)
6. **Auto-Bidding Aggregates** — per `scanner × template × algorithmSignature` with reply rate + $/reply as headline, hires/hire-rate as diagnostic, focus vs prior window columns, highlight bands. (Section 5)
7. **Recommendations Detail** — one row per item from tiers OKAY + CRITICAL, with fields: tier, lever (CL rewrite / scanner query / rate / profile / bidding / A-B test), specific change, evidence refs (links back to sheets 2-6), expected effect, success metric (reply rate or $/reply delta), rollout (scope + duration + min sample), stop-loss.

Use the `xlsx` skill for styling — see `data-reference.md` §1 for the projection templates and the xlsx SKILL for dark-mode builders. Emit one `computer://` link to the workbook.

---

## Data realities — empirical gotchas every audit will hit

These are real-data quirks (discovered on the Ubiquify audit 2026-04-22) that WILL break the audit if you forget them. They are not in-code contracts; they are observed behavior. Ignore at your peril.

1. **Auto-bidder metadata is on `opportunities.application.*`, not `proposals`.** `scannerId`, `algorithmSignature`, `originalGigTempId`, generated `coverLetter`, `originalStrategy`, `model`, `promptVersion`, `bid`, `cost`, `connectPrice` — all opportunity-side. A proposal-only projection for these returns 100% nulls and will make you wrongly conclude the team isn't using the auto-bidder. See §0 at top.

2. **`meta.status` is MIXED TYPE — int AND string — on the same team.** Ubiquify has status codes `2/3/7/8/10` AND string labels `"Accepted"/"Activated"/"Declined"/"Archived"/"Hired"`. Always query with `$in`: `{"meta.status": {"$in": [10, "Hired"]}}` for hires. (Data-reference §24.7.)

3. **`meta.chat.chatId` with `$ne: None` returns 0.** The field is ABSENT on non-replied docs (not null, not empty string). Use `{"$exists": True}` or `{"$nin": [None, ""]}`. In aggregations use `$and` of multiple `$ne` checks, or pre-filter with `$match`. Codebase-canonical reply signal is `dashroomUID` which is dual-written and works cleanly with `$ne: None`. (Data-reference §24.8, §24.11.)

4. **`team.createdAt` is often null. Use `ObjectId(team._id).generation_time`** to derive signup date. Corroborate with `team.payment.selection.providerData.subscription.trial_start` if present. (Data-reference §24.9.)

5. **`client.buyer.info.company.contractDate` is NOT the hire date.** It's the client company's Upwork-signup year. Values span 2011-2024 on a single team because different clients signed up in different years. Use `auditDetails.modifiedTs` (last mutation, typically the status flip to HIRED) for the close timestamp. For pre-GigRadar-era ingested hires, `modifiedTs` reflects the ingest time, not the original hire — flag this caveat in the retro. (Data-reference §21.2, §21.4.)

6. **`meta.inviteToInterviewUid` separates invite-originated from outbound-bid proposals.** The benchmark workflow EXCLUDES invites (`meta.inviteToInterviewUid: null`). Always split invite vs. bid cohorts — invites have ~100% reply and ~10% hire rates that would inflate any blended number. On Ubiquify: 29 invite proposals (0.34% of volume) produced 3 of 25 hires (12%). (Data-reference §24.12.)

7. **Manual bids vs. auto-bidder: join coverage, not an explicit flag.** A proposal with no joinable opportunity (via `application.proposalId ↔ meta.uid`) is a manual submission. **AND you must further split manual into invite-initiated vs outbound** using `meta.inviteToInterviewUid`. On Ubiquify the 1.2% manual tail (99 proposals) decomposes as 70 manual-outbound (8.57% rr — matches auto-outbound 8.58%) + 29 manual-invites (100% rr by construction). The earlier "manual converted 16× better" framing was invite-driven and apples-to-oranges. Always report the four cohorts: {auto|manual} × {invite|bid}.

8. **Scanners are EMBEDDED in `teams.scanners[]`, not a separate collection.** `db.scanners` does not exist. Fetch the team doc, then walk the array. Scanner `_id` is an ObjectId within the array; `opportunities.scannerId` is sometimes a string — match both types on lookups.

9. **`archiveReason` is an empty skeleton on most teams.** Present as an object but all sub-fields null. Probe before relying on it. The typo field `declineReadon` (extra letter) carries the real client-decline signal — but only on 0.6% of proposals (post-reply declines). Don't build distribution grids off these — use only for one-line Notes in Win/Loss rows.

10. **`leads.chats` is populated for some teams, empty for others.** Ubiquify has 759 proposals with `chat.chatId` but ZERO `leads.chats` docs for the team (chat sync not run). Probe `db.leads.chats.findOne({gigradarTeamId: TEAM_OID})` before writing Section 3 and fall back to skipping chat-transcript reading if the collection is dry.

11. **`opportunities.feedback` + `OpportunityFeedbackReason` / `ApplicationFeedbackReason` are empty on many teams.** Schema-wise they exist; data-wise they don't populate consistently. Don't build an audit around these fields without a coverage probe. (Data-reference §24.5.)

12. **Retro-corpus size is team-dependent.** Ubiquify has 10 pre-signup retro proposals (negligible tail). Other teams have 500+ (majority of their data is retro). Compute retro size per-team before deciding whether Phase 1 retro carries the narrative or Phase 2B peer KNN does.

13. **Connect cost per bid is NOT always 15.** Per-opportunity `application.connectPrice` values vary (Ubiquify's samples showed 20). Use the ladder `terms.connectsBid > 0 ? terms.connectsBid : meta.connectsExpended ?? connectsExpended ?? 0` for actual spend per proposal — not a constant.

14. **Hourly bids store `amount: null` on the opportunity.** The hourly rate is NOT on the opportunity doc; it's on the proposal (`terms.hourlyRate`) or inside the CL text. For hourly-heavy customers, GMV / $ totals cannot be computed from opportunity data alone.

15. **`matcher.embedding` coverage is incomplete before 2025-10.** KNN on pre-2025-06 seeds returns nothing. Always check `_source.matcher.embedding` is a 1536-length array on the seed before running KNN; skip if absent. (See Section 2B coverage table.)

16. **`metaJob.meta.topBids` is anonymized — useless.** All `bids.name` are placeholders (`"1st place"`), `connects: 0`. Do not harvest.

---

## Explicit reminders — what NOT to do

- **DO NOT headline hire rate or $/hire.** Reply rate and $/reply are the north-star metrics — users close off-Upwork, so hire counts under-report wins. Hires stay as a secondary diagnostic with explicit population labels.
- **DO NOT build a rejection-reason or feedback-reason distribution grid.** It doesn't produce actionable insight. Section 4 Win/Loss CL comparison replaces it.
- **DO NOT recommend anything without a Section 4 Win/Loss row or Section 2B look-alike backing it.** Aggregate tables alone (Section 5) are not sufficient evidence for a recommendation.
- **DO NOT compute interview rate separately.** Interview = reply = `meta.chat.chatId != null`.
- **DO NOT slice on `promptVersion` / `llm` / `proof_reader_version`.** Slice on `scannerId × templateId × algorithmSignature`.
- **DO NOT write a separate markdown summary file.** Exec summary lives on workbook sheet 1 as the three-tier block.
- **DO NOT use `meta.createdAt` for contract-close dates.** Use `auditDetails.modifiedTs`.
- **DO NOT fetch full proposal docs.** Always minimalistic projection; narrow-fetch heavy fields (CL, chat messages, JD) only after the aggregate has narrowed to K rows.
- **DO NOT aggregate across scanner `-A/-B/-C` sibling variants.** They are controlled experiments.
- **DO NOT recommend 3+ scanner-query changes at once** — attribution collapses.
- **DO NOT skip cohort compare.** Fall back to inferred category with caveat if no benchmark.
- **DO NOT package the plugin during iteration.** Plugin packaging happens only after explicit user sign-off. All edits stay under `/sessions/dazzling-nifty-fermat/plugin_work/current/` until then.
