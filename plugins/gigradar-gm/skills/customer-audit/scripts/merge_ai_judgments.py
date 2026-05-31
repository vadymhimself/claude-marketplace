#!/usr/bin/env python3
"""Merge AI-generated judgments from /tmp/comp_judgments into phase2c_match_reasoning.json.

Schema changes on each competitor:
  - replaces summary_reasoning with ai_summary
  - adds ai_tactics (list of top_tactics_for_subject_team)
  - on each pair: adds ai_what_worked_for_them, ai_what_ubiquify_did, ai_specific_tactic_to_copy
"""

# ===== v0.5 ENV-VAR CONTRACT =====
# Set TEAM_OID + AUDIT_WORK_DIR per audit. Other vars have sensible defaults.
import os as _os_env
from datetime import datetime as _dt_env, timezone as _tz_env, timedelta as _td_env

def _env_date(name, default_date):
    raw = _os_env.environ.get(name)
    if not raw: return default_date
    try:
        return _dt_env.fromisoformat(raw).replace(tzinfo=_tz_env.utc)
    except Exception:
        return default_date

_today = _dt_env.utcnow().replace(tzinfo=_tz_env.utc, hour=0, minute=0, second=0, microsecond=0)
_FOCUS_END_DEFAULT   = _today
_FOCUS_START_DEFAULT = _today - _td_env(days=30)
# ===== end env contract =====

import json
import os
import re
from pathlib import Path

PHASE2C = Path("/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2c_match_reasoning.json")
JUDGMENTS_DIR = Path("/tmp/comp_judgments")

phase2c = json.load(PHASE2C.open())
by_id = {c["team_id"]: c for c in phase2c["competitors"]}

# Phase2c stores competitors in ranked order. Judgments named
# judgment_NN.json from subagent dispatch use the 1-based bundle index.
# Also support legacy NN_<prefix>.json filenames.
prefix_map = {tid[:12]: tid for tid in by_id}
ordered_competitors = phase2c["competitors"]

matched = 0
unmatched = []
for jf in sorted(os.listdir(JUDGMENTS_DIR)):
    m_positional = re.match(r"judgment_(\d+)\.json", jf)
    m_legacy = re.match(r"(\d+)_([a-f0-9]+)\.json", jf)
    competitor = None
    if m_positional:
        idx = int(m_positional.group(1))
        if 1 <= idx <= len(ordered_competitors):
            competitor = ordered_competitors[idx - 1]
            full_tid = competitor.get("team_id")
    elif m_legacy:
        prefix = m_legacy.group(2)
        full_tid = prefix_map.get(prefix)
        if full_tid:
            competitor = by_id[full_tid]
    if not competitor:
        unmatched.append(jf)
        continue
    judgment = json.load((JUDGMENTS_DIR / jf).open())

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
