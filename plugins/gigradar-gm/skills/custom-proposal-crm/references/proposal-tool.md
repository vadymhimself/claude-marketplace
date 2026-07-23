# Proposal-tool recipe — the "watch it write itself" block (section 7)

This is the spec for the **dedicated sub-agent** that generates section 7: the
interactive tool where a prospect flips between their live Upwork jobs (right) and
watches GigRadar draft a tailored cover letter (left) in three tones. It is the
single most persuasive block on the page because it lets the prospect *feel* the
product, so the letters must be genuinely good, not filler.

## HARD RULES (a letter that breaks any of these is rejected)
1. **No long dashes. Ever.** Em-dash (—) and en-dash (–) are the number-one "written
   by AI" tell and are banned from every visible field. Use periods, commas, colons,
   or parentheses instead. Write number ranges with a hyphen ("$15-45", "12-18%").
   `build.py` will strip any that slip through, but write them clean, and **sign off
   with no leading dash** (e.g. `Best,\nMaya` or `Maya, Pixel & Beam`, never
   `— Maya`).
2. **Never say "KB".** A lead has no idea what that means. In any on-page text use
   plain words: "our knowledge base", "our proposal data", "insights from 133,872
   real proposals", "our database". (The JSON key `kbSourced` is internal and stays;
   its *values* and the on-page label must be human.)
3. **Never fabricate or reinvent.** Only use facts about the lead, their clients,
   past projects, and metrics that are actually in the recon dossier (their website)
   or the outreach database. Do **not** invent a client name, a result, a metric, or
   a testimonial. The Upwork jobs on the right are **real** jobs the opportunity-miner
   pulled from the live index, quote them, never embellish or invent job details. If
   you have no real proof point for a claim, write about method and fit, not a made-up
   outcome. When in doubt, leave it out and tell the orchestrator.
4. **Bonus (do this whenever you can): personalize with the lead's REAL client work.**
   The strongest letters name the lead's own actual clients or case studies (from the
   twins/recon dossier) as the proof, mapped to the job at hand. Their own track
   record is the most convincing thing on the page, use it when it's real.

## Who runs this
A **separate sub-agent**, spawned to run **in parallel** with the rest of proposal
assembly (see SKILL.md). Use the **Sonnet** model: this is high-volume copywriting
(3n letters) grounded in retrieved KB text — lots of tokens, not much hard
reasoning, so Sonnet is the right cost/quality point. The agent **must have the
`gigradar-gm:knowledge-base` skill** (KB access) — the letters are only credible if
their moves are drawn from GigRadar's real cover-letter/bidding research.

## Inputs the orchestrator hands you
1. **The jobs** — the same `n` live Upwork jobs used in `twinsSection.twins`
   (usually 4). For each: title, category, budget, client quality (spend / feedback
   / verified), skills, and the real JD text. These are the RIGHT side.
2. **The lead's voice + proof** — from the recon dossier: founder name, company,
   services, positioning, brand voice, and any real client outcomes (e.g. "5.3×
   reply lift for Clay BPO"). The letters are written **as the lead**, first person.
3. Nothing is invented about the lead. If you don't have a real proof point for a
   claim, keep the letter about method and fit, not fabricated metrics.

## Step 1 — pull the cover-letter playbook from the KB
Before writing, query the Knowledge Base for what actually earns replies. Run a few
searches with the `knowledge-base` skill and keep the concrete patterns + any
numbers with their citations. Good queries:
- "cover letter opening line that gets replies"
- "Upwork proposal structure high reply rate"
- "personalization first line proposal"
- "proposal mistakes low reply rate" / "generic proposal why ignored"
- "call to action Upwork proposal booked call"
- the lead's niche + "proposal" (e.g. "cold email agency proposal", "Clay proposal")

Distil ~5–8 reusable moves (opening on the client's outcome, mirroring the JD in
sentence one, one concrete proof-with-a-number, short + skimmable, one low-friction
CTA, timezone/availability trust triggers, etc.). Every `kbSourced` bullet you
write later must map to one of these — that's the provenance the block promises.

## Step 2 — the three tones (same substance, different voice)
For **each job**, write three letters. The **offer, the proof, and the CTA stay the
same**; only the voice changes. This is the point of the switch — it shows the
prospect the same winning letter in whatever register fits their brand.

- **Formal** — polished, confident, professional. Direct sentences, no slang. The
  safe default a corporate buyer expects. Still opens on the client, not "I".
- **Quirky** — warm, human, a little playful. A pattern-interrupt opener, casual
  phrasing ("cards on the table", "quick one"), light humor — but every claim and
  the CTA survive intact. Personality, not fluff.
- **Creative** — a strong framing device: future-pacing ("Picture your CRM two
  weeks from now…"), a contrarian thesis, or a vivid contrast. Makes the reader
  *see* the outcome. Still grounded in the real proof and one clear ask.

### Letter craft (all tones)
- **90–140 words.** Long letters lose. Three or four short paragraphs.
- **First line earns the second.** Open on the client's problem/outcome and mirror
  something specific from the JD. Never "I am writing to apply / I'm interested in".
- **One concrete proof.** A real number or named result from the dossier. One is
  enough; don't stuff.
- **One CTA.** A single, low-friction next step (a 15-minute call, a free teardown,
  "define done in 20 min"). No menu of options.
- Write as the lead, first person, signing off with their name + company (no
  leading dash on the sign-off).
- Format as HTML: first paragraph `<p class="greet">`, body `<p>`s, sign-off
  `<p class="sig">`. Bold a proof point with `<b>` sparingly. No long dashes (see
  Hard Rule 1); use commas, periods, colons, parentheses.

### Under each letter — two lists
- **`whyConverts`** (2–3 bullets): the persuasion mechanics — *why* this specific
  letter works (what the opener does, the proof, the CTA). Speaks to a savvy agency.
- **`kbSourced`** (2–3 bullets): the real knowledge-base pattern each move is
  grounded in, **and how you made it specific** to this lead/job. This is the
  "sourced from our knowledge base + templated to be specific" promise, made visible.
  Say "knowledge base" / "our proposal data" / "insights from 133,872 proposals" —
  **never the abbreviation "KB"** (Hard Rule 2). Cite the real insight you pulled
  (e.g. "top-decile teams quote the client's own words"), not an invented one.

## Step 3 — the framing fields (write once)
- `headline`, `intro` — sell the block.
- `explainer` — **assume the reader has never used Upwork.** One short paragraph:
  a company posts a job (right), a freelancer sends a short written proposal (left)
  to win it, that letter is the whole first impression, most are generic — GigRadar
  writes and sends a tailored one per job. This is a hard requirement (a non-Upwork
  reader must understand the tool).
- `foot` — a dark strip tying it to conversion: a `<span class="pf-badge">` pill +
  the reply-rate proof (12–18% vs 4% median) + an `<a href="#book">` link.

## Step 4 — emit ONE JSON object (nothing else)
Return exactly the `proposalTool` object from `assets/template-schema.json`:

```json
{
  "headline": "…",
  "intro": "…",
  "explainer": "…",
  "foot": "<span class=\"pf-badge\">⚡ Auto-written &amp; sent</span> <span>… <a href=\"#book\">See it write one live →</a></span>",
  "jobs": [
    {
      "tabLabel": "Clay Lead Ops",
      "jobCategory": "Lead Gen · Clay",
      "jobTitle": "Clay / ZoomInfo / CRM Lead Operations Expert",
      "jobStats": [{"v":"$15–45","k":"/hr"},{"v":"$7.9M","k":"client spend"},{"v":"4.94","k":"★"},{"ver":true,"k":"Payment verified"}],
      "jobSkills": ["Clay","ZoomInfo","HubSpot","Data Enrichment","CRM"],
      "jobDescription": "<p>Looking for a Clay expert to build, clean and enrich CRM-ready B2B prospect data…</p>",
      "jobUrl": "https://www.upwork.com/jobs/~0220604552632972018540",
      "letters": {
        "formal":   {"coverLetter":"<p class=\"greet\">Hi there —</p><p>You need CRM-ready B2B data your team actually trusts…</p><p class=\"sig\">— Satiesh, Remote Guyana</p>","whyConverts":["…","…","…"],"kbSourced":["…","…","…"]},
        "quirky":   {"coverLetter":"…","whyConverts":["…"],"kbSourced":["…"]},
        "creative": {"coverLetter":"…","whyConverts":["…"],"kbSourced":["…"]}
      }
    }
  ]
}
```

The orchestrator drops this straight into `lead-data.json` under `proposalTool`.
`assets/example-remote-guyana.json` has a full 4-job × 3-tone worked example.

## Quality bar
- 3n letters, all distinct. Never ship the same letter under two tones.
- Every letter opens on the client, carries one real proof, ends on one CTA.
- `kbSourced` bullets are real KB-grounded patterns, not hand-wave. If the KB is
  unavailable, say so to the orchestrator rather than inventing citations.
- A reader who has never touched Upwork can read `explainer` + one letter + one job
  and understand what GigRadar does and why it would help them.
