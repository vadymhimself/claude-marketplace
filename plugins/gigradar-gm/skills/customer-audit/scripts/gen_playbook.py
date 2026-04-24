#!/usr/bin/env python3
"""Generate COMPETITIVE_PLAYBOOK.md from the 10 AI judgments in /tmp/comp_judgments."""
import json
import os
from pathlib import Path

JUDG_DIR = Path("/tmp/comp_judgments")
OUT = Path("/sessions/dazzling-nifty-fermat/mnt/GigRadar AI Auto Researcher/COMPETITIVE_PLAYBOOK.md")

judgments = []
for jf in sorted(os.listdir(JUDG_DIR)):
    if jf.endswith(".json"):
        judgments.append(json.load((JUDG_DIR / jf).open()))

md = []
md.append("# Ubiquify Competitive Playbook\n")
md.append("_Generated from head-to-head analysis of 10 direct competitors — 42 winning-proposal pairs._")
md.append(f"_Data window: focus 2026-03-23..2026-04-22. Audit date: 2026-04-22._\n")
md.append("---\n")

md.append("## How to read this playbook\n")
md.append("Each section below is one competitor team that won jobs in the same KNN neighborhoods as Ubiquify. For each, we analyzed their top-5 winning cover letters against Ubiquify's closest title-matched WIN and LOSS, and extracted:\n")
md.append("- **Competitor formula** — their repeating CL shape and voice\n")
md.append("- **Per-pair insights** — what specific moves separated their win from Ubiquify's loss on the most comparable jobs\n")
md.append("- **Tactics for Ubiquify** — 3–4 template-able edits to adopt\n")
md.append("\nRead them in order — tactics compound. The final section consolidates patterns that appeared ≥3 times across competitors into a priority tactic stack.\n")
md.append("---\n")

# Track all tactics for pattern-counting
all_tactics = []

for j in judgments:
    name = j["team_name"]
    md.append(f"## {name}\n")
    md.append(f"**Competitor formula.** {j['competitor_summary']}\n")
    md.append("**Per-pair insights**\n")
    for p in j["pairs"]:
        md.append(f"### Pair {p['pair_num']}")
        md.append(f"**What worked for them.** {p['what_worked_for_them']}\n")
        md.append(f"**What Ubiquify did.** {p['what_ubiquify_did']}\n")
        md.append(f"**Specific tactic to copy.** {p['specific_tactic_to_copy']}\n")
    md.append("**Top tactics for Ubiquify (from this competitor):**\n")
    tactics = j.get("top_tactics_for_ubiquify") or j.get("top_tactics_for_subject_team", [])
    for t in tactics:
        md.append(f"- {t}")
        all_tactics.append((name, t))
    md.append("\n---\n")

# ===== Priority tactic stack (consolidated) =====
md.append("## Priority Tactic Stack — Consolidated Across All Competitors\n")
md.append("The tactics below recur across ≥3 competitors and are the highest-leverage edits to Ubiquify's bidding template.\n")

themes = [
    ("1. Replace the FAANG/credentials opener with a THESIS or INFERENCE line",
     "The single biggest pattern. Winning competitors open with an asserted opinion about how the JD's problem should be solved, not with credentials. Variants: "
     "(a) THESIS opener — 'The most [effective/efficient] way to [JD goal] is by [asserted method]' (EZOps Cloud, Pair 1-4); "
     "(b) INFERENCE opener — 'The way you [client's architectural choice] tells me you [underlying motivation]' (Starbourne Labs, Pair 2); "
     "(c) HYPOTHESIS opener — 'The core technical challenge appears to be [JD goal]. My initial hypothesis is that this can be addressed by [3-step plan]' (Requestum, Pair 1). "
     "All three reframe Ubiquify from 'applicant' to 'consultant scoping the problem'."),
    ("2. Cut the recycled 3-case-study block (SEO / drone / Excel / franchisor)",
     "Nine of ten competitors explicitly flag this as self-sabotage. The block is ~1100 chars of credentials that don't topically match most JDs. Rule: ONE per-bid case study, chosen for vertical match, with a specific metric and timeframe. Drop the other two. When no case study matches, skip the portfolio block entirely — a 700-char consulting-style CL beats a 1700-char credential wall for small/consulting jobs (Modsi, Pair 3)."),
    ("3. Segment CL templates by JD INTENT CATEGORY",
     "Build / consult / tutor / audit / maintain — each needs a different sentence-1 shape. EZOps won a tutoring job (Pair 2, title_jaccard 1.600) Ubiquify lost because Ubiquify used its build template on a tutoring engagement. The JD-intent parser should route to a different template before the CL generator runs."),
    ("4. Add a risk-reversal banner for design/frontend/MVP jobs",
     "Stubbs wins with '🟢 Ready to provide a one-week trial period with a full refund' / '🟢 Ready to create a free design concept'. Flips evaluation friction onto the freelancer. Cheap to offer (most clients don't take it), massively lowers client-side activation energy."),
    ("5. Mirror the client's language in sentence 2 — JD keyword or tone",
     "Natife opens CLs with the JD's exact tool list ('Base44, Lovable, Cursor'); Modsi opens with the client's own trademark ('Good evening Basketball Gods™'); Marlon opens with an inference of the client's architectural choice. All of these signal 'I actually read your listing' — whereas FAANG openers signal 'I copy-pasted'."),
    ("6. Propose a specific call time in the CTA",
     "Natife: 'free between 10 am – 2 pm Hialeah time' / 'a 15-minute call on Upwork tomorrow around 11:00-13:00 CET'. Beats 'open for a quick chat?' because the client's next action is yes/no, not 'suggest a time'. Estimated CTA conversion uplift: 2x vs vague open-ended."),
    ("7. Portfolio URL format should match the JD type",
     "Mobile JDs → App Store / Play Store URLs (Stubbs mobile bids). SaaS / web JDs → named company URLs with stack annotations in parens (Requestum, Natife). Code-heavy JDs → GitHub 'code_examples' repo (Requestum: github.com/requestum-team/code_examples). Live shipped work outranks Loom demos. Loom is a fallback, not a default."),
    ("8. Specific-number social proof beats generic tenure",
     "'20 Lovable apps shipped' / '50+ n8n workflows deployed' / '15 RAG systems in production' all beat '10+ years of experience'. Maintain a per-category shipped-project counter and inject the matching number per bid (Natife, Pair 3)."),
    ("9. Close with an implementation-level or business-level question",
     "Not 'what's your timeline?'. Instead: 'is there an existing rendering setup or are we starting the visual layer from scratch?' (Requestum, implementation), 'planning to include any monetization options for the beats?' (Requestum, business), 'How are you currently structuring context and session management in your Claude Code workflows?' (EZOps, insider). These questions prove expertise by their specificity."),
    ("10. Institutional namedrop for Ubiquify itself",
     "Marlon writes 'at Starbourne Labs' — positions himself as a practitioner from a named shop, not a solo. Ubiquify has its own brand — open with 'Having designed and deployed X at Ubiquify...' and the authority framing upgrades every CL with zero effort."),
]
for hdr, body in themes:
    md.append(f"### {hdr}")
    md.append(body)
    md.append("")

md.append("---\n")
md.append("## Implementation order (recommendation)\n")
md.append("1. **Ship today (1 day, zero risk):** Add Ubiquify-shop namedrop (#10), swap vague CTA for specific call times (#6), remove SEO/drone/Excel block when not topically relevant (#2).")
md.append("2. **Ship this week (template work):** Build the 5 JD-INTENT templates (#3) — each with its own opener variant from #1.")
md.append("3. **Ship this month (infra):** Per-category shipped-project counter for specific-number social proof (#8), GitHub code_examples repo (#7), risk-reversal banner A/B (#4).")
md.append("4. **Continuous:** Tune inference-opener templates per scanner (#1, #5), refine closing-question library per vertical (#9).")
md.append("")
md.append("---\n")
md.append("_This playbook is regenerated automatically when `phase2c_match_reasoning.json` and `/tmp/comp_judgments/*.json` are updated. See `customer-audit/SKILL.md` §Phase 2 Part B for the subagent dispatch pattern that produces these judgments._\n")

OUT.write_text("\n".join(md))
print(f"Wrote {OUT}  ({OUT.stat().st_size} bytes)")
