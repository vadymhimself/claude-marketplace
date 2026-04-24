# Cover letters — prompt system, attribution, and audit patterns

The cover letter (CL) is the second-biggest lever for reply rate after the scanner query.
This file documents how CLs are generated, what "CL prompt" means in the audit schema,
and which patterns recur in underperforming teams.

> **Audit note:** the canonical scanner diagnostic is the **Win/Loss CL table** (paired cherry-picked wins and losses per scanner — see `audit-playbook.md` Section 4), not aggregate rejection-reason or CL-quality distributions. Read this file for *what to look for* in each winner/loser CL — then put the evidence in the paired table.

---

## Generation flow (high level)

1. **Opportunity matched** — scanner emits an `OpportunityV2` doc with `application` stub.
2. **Bidder dispatched** — based on `scanner.biddingStrategy.algorithmSignature`:
   - `template` → Template Bidder substitutes `scanner.biddingStrategy.options.template`
     with opportunity values, no LLM call.
   - `sardor-ai-v2` / `laziza-ai` → Sardor invokes the `generate-cover-letter` Lambda
     with a `GenerateCoverLetterLambdaPayload { context, data, config }`.
3. **Sardor LLM call.** The Lambda reads the team's `CoverLetterLlmConfig` (from
   `team.subscription.coverLetterLlmConfig`), merged with any per-scanner override in
   `scanner.biddingStrategy.options.llmConfigOverride`. It loads the scanner's
   `memory.statements[]` and the scanner's `template`/`answerTemplate` as the starter,
   then asks the LLM to produce the full cover letter + question answers.
4. **Proofreader pass** (optional, per `proof_reader_version`). Clean-up on style, links, banned phrases.
5. **Validator pass.** Ensures the output isn't empty, doesn't leak the prompt, doesn't
   include unsafe links. On failure: `ProposalErrorCode.EmptyCoverLetterOrAnswerMissing`
   / `PromptLeaked` / `ProposalContainsUnsafeLinks`.
6. **Submission.** `application.coverLetter` is written to the opportunity. When the
   scheduler picks it up, the `application.sent` timestamp is written and the Upwork
   apply worker fires.
7. **Snapshot.** At send time `application.originalStrategy` is written — a **frozen
   copy** of the bidding strategy that produced this specific CL. This is the audit's
   attribution anchor: even if the user edits the scanner later, the snapshot tells
   us which template version actually generated the replied-to CL.

---

## Fields the audit reads for CL attribution

**⚠️ All CL-attribution fields live on `opportunities.application.*`, not on `proposals`.** The `proposals.coverLetter` / `proposals.renderedCoverLetter` fields are a post-send mirror (what Upwork sees) and do not carry the generator metadata. The join is `opportunities.application.proposalId ↔ proposals.meta.uid` (strings; see `data-reference.md` §24.10a). Project the proposal side for join-key + reply/hire outcomes, project the opportunity side for every column in the table below.

On `opportunities.application` (for each sent proposal):

| Field | Meaning |
|---|---|
| `proposalId` | **join key** → `proposals.meta.uid` (string) |
| `coverLetter` | the actual CL text that was sent (generator-side; authoritative for CL analysis) |
| `originalStrategy` | full `BiddingStrategy<any>` snapshot: `.algorithmSignature`, `.options.template`, `.options.answerTemplate`, `.options.llmConfigOverride` |
| `algorithmSignature` | bidder family (`sardor-ai-v2`, `template`, `laziza-ai`, `ㅤ⁤`-style opaque markers — keep as-is) |
| `model` | `AutoBidderModel` — "Sardor Bidder", "Template Bidder", or a specific `SardorLLMTypes` code |
| `promptVersion` | `PromptVersion` enum value ('1.2.7.mem', '1.3', 'latest', etc.) |
| `config` | full resolved `CoverLetterLlmConfig` at send time (llm, prompt_version, add_ons, llm_kwargs, proof_reader_version) |
| `llmRawOutput` | raw LLM response before proofreading — useful for debugging why a CL looks weird |
| `prompt` | the actual chat or prompt messages sent to the LLM (array of messages) |
| `algorithmVer` | SardorVersions enum ('sardor-ai-v2', 'laziza-ai'…) |
| `cost` | generation cost in cents |
| `failedCoverLetters` | any prior attempts that validator-failed before this succeeded |
| `priceStripeId` | which Stripe usage item was charged (links to billing audit) |
| `matchPercentage` + `matchPercentageArgumentation` | pre-matcher score + LLM reasoning |

Template/scanner attribution for a CL also pulls two top-level opportunity fields (not under `application`):

| Field | Meaning |
|---|---|
| `scannerId` / `scannerName` | which scanner surfaced the job (attribution anchor for Win/Loss grouping) |
| `originalGigTempId` | template ObjectId — pair with scanner for the (scanner × template) slice |

---

## CL prompt identity tuple (audit grouping key)

For the audit, a "CL prompt" is defined as (all five components read from the **opportunity** side — NOT the proposal side):

```
(
  bidder_family,               // opportunities.application.originalStrategy.algorithmSignature
                               //   OR opportunities.application.algorithmSignature
  llm_model,                   // opportunities.application.config.llm
                               //   OR opportunities.application.model
  prompt_version,              // opportunities.application.promptVersion
                               //   OR opportunities.application.config.prompt_version
  template_hash,               // sha256(opportunities.application.originalStrategy.options.template)[:8]
  answer_template_hash,        // sha256(opportunities.application.originalStrategy.options.answerTemplate)[:8]
)
```

Two proposals share a CL-prompt identity iff all five components match. The audit
slices by **scanner × template × CL-prompt** (scanner + template come from the opportunity top-level: `opportunities.scannerId` and `opportunities.originalGigTempId`), and reports headline metrics per slice.

Edge cases:
- If `template` is `null` or `""`, hash to the literal string `"empty"` (don't drop).
- If `originalStrategy` is missing (very old proposals), mark as `legacy-no-snapshot`
  and treat as a single bucket.
- If the proposal has **no joined opportunity** (manual bid cohort — see `data-reference.md` §24.10a), there is no CL-prompt identity. Report these proposals in a separate "manual" bucket; never attribute them to a scanner or CL prompt.
- `prompt_version = 'latest'` is the float target and **resolves** to a concrete version
  at send time. If the audit needs stable identity, prefer `application.config.prompt_version`
  over the scanner's current override — the latter drifts.
- `algorithmSignature` is sometimes an opaque zero-width-unicode marker (e.g. `'ㅤ⁤'`) — treat as an opaque ID, don't try to pretty-print, but keep it as the grouping key.

---

## CoverLetterLlmConfig (what lives behind "the prompt")

From `gigradar-definitions/ai/index.ts`:

```ts
interface CoverLetterLlmConfig {
  prompt_version: PromptVersion;
  validator: LLMValidatorType;             // currently only 'default'
  add_ons: LLMAddOns;                       // memory / antiprompt-leak / client-name-extractor / ...
  llm: SardorLLMTypes;                      // gpt-4o, gpt-5.1, claude-sonnet-4-6, ...
  llm_kwargs: LLMKeywordsArgs;              // temperature, max_tokens, etc.
  examples?: ExampleIds[] | null;
  proof_reader_version?: ProofReaderVersion;
  fallback_llm?: SardorLLMTypes | null;
}
```

PromptVersion enum (2026): `'1.1'`, `'1.2'`, `'1.2.1'`, `'1.2.3'`, `'1.2.4'`, `'1.2.6'`,
`'1.2.6.mem'`, `'1.2.7.mem'`, `'1.2.7.1.mem'`, `'1.2.7.2.mem'`, `'1.2.6.1'` (no
freelancer context), `'1.2.6.2'`, `'1.2.6.instructionsfix'`, `'1.2.6.1.instructionsfix'`,
`'1.2.6.2.instructionsfix'`, `'1.2.6.3.instructionsfix'`, `'1.2.6.1.mem'`, `'1.2.6.2.mem'`,
`'1.3'`, `'latest'`.

Rule of thumb: `.mem` versions use scanner memory; non-mem versions do not. `.instructionsfix`
branches are experimental refinements. `1.3` is the newest stable; `latest` floats.

---

## Anti-patterns (common audit findings)

1. **Generic hook across all CLs.** Template starts with "I came across your job post"
   or "I'd like to help with..." — Sardor occasionally inherits this starter verbatim
   when given a weak `template` field. Symptom: top 10 sent CLs all look the same.
   Fix: rewrite template with a specific proof-point hook; use a name-extractor add-on
   to open with "Hi <client first name>".
2. **Proof point doesn't match scanner segment.** Scanner targets Shopify jobs but
   template name-drops WordPress portfolios. Symptom: high view rate, low reply rate —
   clients opened, bounced on the mismatch. Fix: scanner-specific template with the right
   case study.
3. **Price in CL doesn't match `biddingTerms`.** CL says "rate $50/hr" but the bid
   posts at $85/hr because `biddingTerms.hourlyStrategy = PercentOfClientMax`.
   Symptom: chat opens, client asks about rate discrepancy, no interview. Fix: stop
   hardcoding rate in template text.
4. **Too long.** Sardor configured with high `max_tokens` produces 400+ word CLs.
   Upwork benchmarks show <150-word CLs reply 2x more. Fix: `llm_kwargs.max_tokens`
   cap + template rewrite instructing brevity.
5. **Question-answer template is stale.** Client screening questions often share
   patterns (portfolio links, availability, rate). If `answerTemplate` is one giant
   prose block, the LLM can't map it cleanly. Fix: structure as Q: A: pairs per
   question.
6. **`add_ons.memory = null` on a team with strong scanner memory.** Team accumulated
   great feedback but the LLM isn't reading it. Fix: switch `prompt_version` to a
   `.mem` variant and enable the memory add-on.
7. **Fallback LLM misconfigured.** Primary is `claude-sonnet-4-6`; fallback is also
   Anthropic. When Anthropic has an outage, both fail. Fix: fallback on the opposite
   provider (e.g. GPT-4o).

---

## What the audit can say about a CL without reading it

Things computable from `originalStrategy` / `config` alone (no NLP needed):

- Template length (chars)
- Number of {placeholder} tokens in template
- Whether template contains a client-name placeholder
- Word count of top-N sent CLs
- Whether `memory` add-on is enabled on `prompt_version = 'latest'` vs a pinned version
- LLM model distribution across a scanner's proposals
- How many distinct template hashes have been used in the window (template churn)
- Fallback LLM same-provider-as-primary (risk flag)

Things that require reading the CL text (flag as "needs human review"):

- Tone (consultative vs desperate vs salesy)
- Whether hook is specific or generic
- Whether proof points match segment
- Rate consistency between CL body and terms

---

## Source files

- `gigradar-definitions/ai/index.ts` — `CoverLetterLlmConfig`, `PromptVersion`, `SardorLLMTypes`, `AutoBidderModel`, LLM add-ons
- `gigradar-definitions/index.ts` — `Application.originalStrategy`, `Application.prompt`, `Application.llmRawOutput`, `Application.config`, `Application.failedCoverLetters`
- `gigradar-definitions/auto-bidder/index.ts` — CL-generation error codes (EmptyCoverLetterOrAnswerMissing, CoverLetterFailedValidation, PromptLeaked, etc.)
- `gigradar-aws-functions/services/generateCoverLetterV1/` — actual Lambda (if deeper investigation is needed; the audit usually doesn't need this)
