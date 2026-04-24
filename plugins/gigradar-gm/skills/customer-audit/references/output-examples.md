# Output examples — customer audit deliverable

One deliverable per audit run:

**`audit_<team-handle>_<focus>.xlsx`** — a single dark-mode workbook. Sheet 1 is the executive summary (three-tier WINS / OKAY / CRITICAL). No separate markdown file. No detached exec summary.

The workbook lands in the Cowork workspace folder so the GM gets a `computer://` link.

> **North-star reminder:** reply rate and $/reply are the headline metrics everywhere they appear. Hire rate and $/hire are diagnostic-only, with explicit population labels. Never rank a team on hire rate in the exec summary.

---

## Sheet order (required)

| # | Sheet | Source section | Purpose |
|---|---|---|---|
| 1 | **Executive Summary** | Section 6 | Three-tier WINS / OKAY / CRITICAL block with hyperlinks into detail sheets. The first thing the GM sees. |
| 2 | **Retro Evidence** | Section 1 | Timeline of HIRED proposals (`auditDetails.modifiedTs`), winning CL excerpts, profile description, pre-vs-auto template delta. |
| 3 | **Competitive Deep-Dive** | Section 2 | Cohort table on top (reply rate / $/reply / view rate / hire rate-diagnostic + population labels). Peer look-alike winners below (seed + KNN hits with CL, rate, profile excerpts). |
| 4 | **Chat Excerpts** | Section 3 | 3–5 client-side exchanges verbatim with one-line interpretation. |
| 5 | **Win/Loss CL Table** | Section 4 | THE core diagnostic. Paired winners & losers per scanner, grouped. Scanner + template + algorithm + CL excerpt + JD excerpt + client stats + chat length for wins. |
| 6 | **Auto-Bidding Aggregates** | Section 5 | Per `scanner × templateId × algorithmSignature` with reply rate + $/reply as headline, hires/hire-rate as diagnostic, focus vs prior window columns. |
| 7 | **Recommendations Detail** | Section 6 | One row per OKAY + CRITICAL item, with lever, specific change, evidence refs (links to sheets 2–6), expected effect, success metric (reply rate or $/reply delta), rollout, stop-loss. |

---

## Sheet 1 — Executive Summary (three-tier)

### Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ <Team Name> — Audit window: <from> → <to>                              │
│ Lens: <category inferred>, <scanner count active>, N=<sent> proposals  │
│                                                                        │
│ Headline: Reply rate X% (Δ vs prior Ypp) | $/reply $Z (Δ vs prior $W) │
│           cohort median reply rate A%, p75 B%, customer at P-rank C%   │
├────────────────────────────────────────────────────────────────────────┤
│ ── WINS ── (green band)                                                │
│ Row 1: What won, dated evidence, scanner/template/algorithm, CL motif. │
│ Row 2: …                                                               │
├────────────────────────────────────────────────────────────────────────┤
│ ── OKAY ── (amber band)                                                │
│ Row 1: Metric at benchmark, named delta, specific opportunity with ref.│
│ Row 2: …                                                               │
├────────────────────────────────────────────────────────────────────────┤
│ ── CRITICAL ── (red band)                                              │
│ Row 1: Failure + its cost, root-cause evidence ref, specific change.   │
│ Row 2: …                                                               │
├────────────────────────────────────────────────────────────────────────┤
│ Caveats: embedding coverage window, status lag, cohort inference, …    │
└────────────────────────────────────────────────────────────────────────┘
```

### Formatting spec (required)

| Element | Style |
|---|---|
| Workbook background | `#0f1419` |
| Primary text | `#e6edf3` |
| Header font | 14pt bold, tier-colored |
| Body font | 11pt, system sans |
| CL / JD excerpts | 10pt monospaced (`JetBrains Mono` or `Courier New`) — visual signal that it's a quote |
| WINS band | bg `#1f6f3f`, text `#9ef0b2` |
| OKAY band | bg `#7a5a1a`, text `#f3d582` |
| CRITICAL band | bg `#7a2021`, text `#f1949a` |
| Evidence links | cell `=HYPERLINK("#'<SheetName>'!A<row>", "→ <SheetName> row N")` |
| Percentile labels | inline on every percentile: `"P62 (among N=147 peers ≥100 sent)"` — never bare `"P62"` |

### Tier row schemas

**WIN row fields (one bullet = one dated win):**
- Date (close: `auditDetails.modifiedTs`)
- Scanner + template ID + algorithm signature
- Job title
- CL excerpt (first 200–300 chars, monospaced)
- Terms: `$X/hr` or `$X fixed`
- Reply rate and $/reply for that `scanner × template × algorithm` slice (with population)
- "Keep doing this:" one-line motif

**OKAY row fields:**
- Metric + value ("reply rate 5.4%")
- Benchmark reference + delta ("cohort median 6.1% → −0.7pp")
- Opportunity: the specific change
- Evidence: link to Section 4 Win/Loss row OR Section 2B peer look-alike
- Expected effect (optional, only when quantifiable)

**CRITICAL row fields:**
- Failure + its cost in dollars ("Scanner `X-07` — 312 sent, 0 replies, **$46.80 wasted** in 90d")
- Root cause: Section 4 Win/Loss pair OR Section 3 chat drop-off
- The change: specific, not vague
- Evidence: link to detail-sheet row
- Stop-loss: kill condition if the change doesn't move the metric

### Rules

- Every **CRITICAL** item must be backed by a Win/Loss table row OR a chat drop-off excerpt. Aggregate rates alone are insufficient.
- Every **OKAY** opportunity must cite at least one Section 2B look-alike or Section 4 Win/Loss pair.
- Every **WIN** must quote the actual CL excerpt (first 200+ chars) or chat excerpt.
- No vague recommendations ("improve cover letters"). Every line is a specific one-change action the team can execute.
- No hype language.

---

## Sheet 2 — Retro Evidence

**Purpose:** the retrospective picture — what won for this team historically, before GigRadar and after.

### Contents

- Timeline: HIRED proposals sorted by `auditDetails.modifiedTs`. Columns: date, job title, scanner (if any — pre-GigRadar rows have blank), `terms.hourlyRate` / `terms.amount`, client name / industry, `meta.createdAt` → `auditDetails.modifiedTs` gap (time-to-close).
- For each of the top 3 pre-GigRadar wins: CL excerpt (first 200–400 chars, monospaced).
- Profile section: `upwork.agency.profiles` description excerpt, hourly rate band, services, title. `upwork.freelancer.profiles` excerpt when relevant.
- Scanner history: `scanners.find({_gigradarTeamOid: teamOid})` — names, created/updated timestamps, active/deleted.
- Explicit delta cell: **"Pre-GigRadar template did X → current auto-generated template does Y"** — surfaced as a highlighted note.

---

## Sheet 3 — Competitive Deep-Dive

**Purpose:** situate the customer against peers on real metrics AND harvest competitive CL / rate / profile patterns.

### Part A — Cohort compare (top table)

Columns: **Metric | Subject value | Cohort median | Cohort p75 | Subject P-rank | Population label**

Rows:
- Reply rate (headline)
- $/reply (headline)
- View rate (supporting)
- Hire rate (diagnostic — population label must say `"among N=xx teams with ≥1 hire in window"`)

Cohort-size row: `"N=<count> peer teams in <category> with ≥100 sent in the focus window."`

If `dashboard.benchmarks` lookup is missing and a category was inferred, add an amber caveat cell explaining the inference.

### Part B — Peer look-alikes (below cohort table)

Per winner harvested from KNN neighborhoods (Section 2B):

| Column | Content |
|---|---|
| Seed job title | the customer proposal or rich-seed we KNN'd from |
| Seed createdOn | `metaJob.createdOn` (footnote if pre-2025-10) |
| Similarity score | KNN score |
| Look-alike job title | neighbor `metaJob.title` |
| Winner team (anon) | `teamId` hashed to a short token |
| Hourly rate | `terms.hourlyRate` |
| Rate context | agency profile hourly band |
| Profile excerpt (first 300 chars) | `upwork.agency.profiles.description` |
| CL excerpt (first 400 chars) | `renderedCoverLetter` |
| Close date | `auditDetails.modifiedTs` |

Pattern-spot summary rows at bottom: common opening hooks, recurring rate bands, recurring positioning statements, recurring skills.

---

## Sheet 4 — Chat Excerpts

**Purpose:** what the client actually said after opening the chat — "CL worked" vs "CL got opened but couldn't close."

Columns: **Date | Chat URI (`upworkRoomUid`) | Message author (freelancer/client) | Verbatim excerpt | One-line interpretation**

3–5 high-signal exchanges. Signal library for the interpretation column: rate pushback, scope pivot, ghost-after-rate-quote, reference-ask, call-ask.

---

## Sheet 5 — Win/Loss CL Table (the core diagnostic)

**Purpose:** understand WHY individual scanners win or lose by comparing real paired examples.

**Grouping:** by scanner. Wins and losses adjacent per scanner.

Columns:

| Column | Content |
|---|---|
| Outcome | WIN / LOSS (with green/red cell fill) |
| Scanner | `name` (e.g. `AM-05-C`) |
| Template | `templateId` |
| Algorithm | `algorithmSignature` + `algorithmVersion` |
| Send date | `meta.createdAt` |
| Close date | `auditDetails.modifiedTs` (WINs only) |
| Job title | `meta.jobTitle` |
| Job excerpt (first 400 chars) | `opportunities.description[:400]`, monospaced |
| CL excerpt (first 400 chars) | `renderedCoverLetter[:400]`, monospaced |
| Terms | `"$X/hr"` or `"$X fixed"` + connects bid |
| Client | `company.name`, `profile.industry`, `totalSpent`, `feedbackScore`, `hireRate` |
| Chat length | message count (WINs only) |
| Notes | one-line interpretation |

1–3 wins and 1–3 losses per scanner with meaningful volume. Cherry-pick criteria defined in `audit-playbook.md` Section 4.

---

## Sheet 6 — Auto-Bidding Aggregates (supporting)

**Purpose:** aggregate tables that frame Section 4 narratives economically. Not the headline — supporting context.

Slice: `scannerId × templateId × algorithmSignature`.

Columns (focus window + prior window):

| Column | Formula |
|---|---|
| Slice | scanner + template + algorithm |
| Sent | `count(sent proposals)` |
| Replies | `count(meta.chat.chatId != null)` |
| **Reply rate** | replies / sent (**headline cell style**) |
| Connects $ | `sum(meta.connectsExpended) * $0.15` |
| **$/reply** | connects_$ / replies (**headline cell style**) |
| Hires | `count(meta.status == 10)` *(diagnostic)* |
| Hire rate | hires / sent *(diagnostic — greyed font)* |
| Δ reply rate | rr_focus − rr_prior |
| Δ $/reply | cpr_focus − cpr_prior |

**Highlighting rules:**
- Green: reply rate above cohort p75 AND sent ≥ 30.
- Red: reply rate == 0 AND sent ≥ 100 (wasted connects).
- Amber: reply rate below (team_avg × 0.6) AND sent ≥ 30.
- Grey: `sent < 15` (insufficient signal).
- Hire rate cells always in greyed font — they are diagnostic, not actionable on their own.

---

## Sheet 7 — Recommendations Detail

**Purpose:** full expansion of each OKAY + CRITICAL item from Sheet 1.

Columns: **Tier | Lever | Specific change | Evidence (hyperlink) | Expected effect | Success metric | Rollout (scope + duration + min sample) | Stop-loss**

Lever values: `CL rewrite | scanner query | scanner bidding | rate positioning | profile rewrite | algorithm version | A/B test | portfolio`.

Every row must have evidence references that link back to Sheets 2–6. No standalone recommendation rows.

---

## Style conventions

- Volumes: thousands separator, no decimals (`412`, `1,028`).
- Rates: one decimal (`9.2%`).
- Deltas: percentage points with explicit sign (`+2.3pp`, `-0.8pp`).
- Dollar amounts: `$65` or `$1,234.56` depending on column.
- Scanner names: backticks in prose (`` `AM-05-C` ``) and bold column text in the workbook.
- CL / JD excerpts: monospaced font in the cell; always first 200–400 chars, no truncation without `…`.
- Job URLs: full Upwork URL when available (`https://www.upwork.com/jobs/~<uid>` from `metaJob.jobUid`).
- Population labels on every percentile.

---

## Where to link results from

After writing the xlsx, reply with **one line** containing:

- `[View the audit]( computer://.../audit_<team>_<focus>.xlsx )` — the workbook is the deliverable.

No separate markdown summary. No chat recap of what's in the workbook — the workbook itself is the canvas.
