#!/usr/bin/env python3
"""Merge AI-generated judgments from /tmp/comp_judgments into phase2c_match_reasoning.json.

Schema changes on each competitor:
  - replaces summary_reasoning with ai_summary
  - adds ai_tactics (list of top_tactics_for_subject_team)
  - on each pair: adds ai_what_worked_for_them, ai_what_ubiquify_did, ai_specific_tactic_to_copy
"""
import json
import os
import re
from pathlib import Path

PHASE2C = Path("/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2c_match_reasoning.json")
JUDGMENTS_DIR = Path("/tmp/comp_judgments")

phase2c = json.load(PHASE2C.open())
by_id = {c["team_id"]: c for c in phase2c["competitors"]}

# Build prefix map: first 12 chars -> full team_id
prefix_map = {tid[:12]: tid for tid in by_id}

matched = 0
unmatched = []
for jf in sorted(os.listdir(JUDGMENTS_DIR)):
    m = re.match(r"(\d+)_([a-f0-9]+)\.json", jf)
    if not m:
        continue
    prefix = m.group(2)
    full_tid = prefix_map.get(prefix)
    if not full_tid:
        unmatched.append(jf)
        continue
    judgment = json.load((JUDGMENTS_DIR / jf).open())
    competitor = by_id[full_tid]

    # Attach competitor-level AI fields
    competitor["ai_summary"] = judgment.get("competitor_summary", "")
    competitor["ai_tactics"] = judgment.get("top_tactics_for_ubiquify") or judgment.get("top_tactics_for_subject_team", [])
    # v3: profile-positioning analysis (added when subagent prompt was updated
    # to include profile context alongside CL text).
    competitor["ai_profile_positioning"] = judgment.get("profile_positioning", "")

    # Attach per-pair AI fields — index by pair_num - 1
    judg_pairs = {p["pair_num"]: p for p in judgment["pairs"]}
    for idx, pair in enumerate(competitor["pairs"]):
        jp = judg_pairs.get(idx + 1)
        if jp:
            pair["ai_what_worked_for_them"] = jp.get("what_worked_for_them", "")
            pair["ai_what_ubiquify_did"] = jp.get("what_ubiquify_did", "")
            pair["ai_specific_tactic_to_copy"] = jp.get("specific_tactic_to_copy", "")
    matched += 1

print(f"matched {matched}/10 competitors")
if unmatched:
    print("unmatched:", unmatched)

# Back up original
backup = PHASE2C.with_suffix(".pre_ai.json")
if not backup.exists():
    backup.write_text(PHASE2C.read_text())
    print(f"backup → {backup}")

json.dump(phase2c, PHASE2C.open("w"), indent=2, ensure_ascii=False)
print(f"wrote {PHASE2C}")
