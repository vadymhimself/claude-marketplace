#!/usr/bin/env python3
"""Regenerate subagent bundle text files from phase2c_match_reasoning.json.

Each bundle is a plain-text summary sized < 25 KB so a subagent can Read the
whole file in one call. One bundle per competitor → dispatched to one parallel
subagent. See skills/customer-audit/SKILL.md §Phase 2 Part B for the prompt
template and the Usage-Policy-safe framing.
"""
import json, os, shutil
from pathlib import Path

IN = Path("/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2c_match_reasoning.json")
IN2B = Path("/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2b_peer_knn_v2.json")
OUT_DIR = Path("/tmp/comp_bundles")

data = json.load(IN.open())
# Phase2b carries the rich competitor profile (agency + primary freelancer) +
# Ubiquify's own subject_profile. We inline both sides into each bundle so the
# subagent has profile context, not just CL text.
p2b = json.load(IN2B.open())
subj_profile = p2b.get("subject_profile") or {}
# Competitor profile lookup by team_id
comp_profiles = {c["team_id"]: c for c in p2b.get("competitors_top10", [])}
OUT_DIR.mkdir(parents=True, exist_ok=True)
# clear old bundles so stale files don't linger
for f in OUT_DIR.glob("SUMMARY_*.txt"):
    f.unlink()

for idx, comp in enumerate(data["competitors"], 1):
    tid = comp["team_id"]
    name = comp.get("team_name") or "?"
    prefix12 = tid[:12]
    fname = OUT_DIR / f"SUMMARY_{idx:02d}_{prefix12}.txt"

    lines = []
    lines.append(f"=== {idx:02d}_{prefix12}.json | {name} | pairs:{len(comp['pairs'])}")

    # ===== PROFILE BLOCK: competitor agency + primary freelancer =====
    # Inline both sides so the subagent can reason about profile positioning,
    # not just CL copy (see SKILL.md §Phase 2 Part B prompt template).
    cp = comp_profiles.get(comp.get("team_id")) or {}
    cag = cp.get("agency") or {}
    cfl_top = cp.get("freelancer") or {}
    cfl = (cfl_top.get("data") or {}) if isinstance(cfl_top, dict) else {}
    fm = cp.get("focus_metrics") or {}
    lines.append("")
    lines.append("-- COMPETITOR PROFILE --")
    if cag:
        lines.append(f"  agency_name: {cag.get('name')}")
        _stats = cag.get("stats") or {}
        _jss = _stats.get("jobSuccessScore")
        lines.append(f"  agency_stats: earned=${(_stats.get('totalEarning') or 0):,.0f} · jss={(_jss*100 if isinstance(_jss,(int,float)) else '—')}% · badge={cag.get('badge')}")
        lines.append(f"  agency_locations: {[(l or {}).get('country') for l in (cag.get('locations') or [])[:2]]}")
        lines.append(f"  agency_summary: {(cag.get('summary_or_description') or '')[:400]}")
        if cag.get("skills"):
            lines.append(f"  agency_skills (top 10): {cag['skills'][:10]}")
        if cag.get("services"):
            svc_titles = [f"{(s or {}).get('name') or (s or {}).get('title')}" for s in cag["services"][:5]]
            lines.append(f"  agency_services: {svc_titles}")
        if cag.get("portfolios"):
            port_titles = [(p or {}).get("title", "")[:60] for p in cag["portfolios"][:4]]
            lines.append(f"  agency_portfolio_titles (top 4 of {len(cag['portfolios'])}): {port_titles}")
    if cfl:
        lines.append(f"  primary_freelancer: {cfl.get('general_name')} · ${cfl.get('general_hourly_rate')}/hr · {cfl.get('general_location')} · tier={cfl.get('contractor_tier')}")
        lines.append(f"  fl_title: {cfl.get('general_title')}")
        lines.append(f"  fl_description (first 500): {(cfl.get('general_description') or '')[:500]}")
        if cfl.get("skills"):
            lines.append(f"  fl_skills (top 10): {cfl['skills'][:10]}")
        _fs = cfl.get("stats") or {}
        if _fs:
            lines.append(f"  fl_stats: earned=${(_fs.get('total_earnings') or 0):,.0f} · jobs={_fs.get('total_jobs')} · jss={_fs.get('job_success_score')}")
        if cfl.get("employment_history"):
            eh = cfl["employment_history"][:3]
            for e in eh:
                lines.append(f"  fl_history: {e.get('title','')} @ {e.get('company','')} ({e.get('start')} → {e.get('end')}): {(e.get('summary') or '')[:200]}")
    if fm:
        rr = fm.get('reply_rate')
        rr_txt = f"{rr*100:.2f}%" if isinstance(rr, (int, float)) else "—"
        cpr = fm.get('cost_per_reply')
        cpr_txt = f"${cpr:.2f}" if isinstance(cpr, (int, float)) else "—"
        lines.append(f"  FOCUS METRICS (30d): sent={fm.get('sent')} · replies={fm.get('replied')} · reply_rate={rr_txt} · hires={fm.get('hired')} · $/reply={cpr_txt}")

    # Ubiquify's subject profile (appears once per bundle so each subagent has it)
    lines.append("")
    lines.append("-- SUBJECT TEAM PROFILE (Ubiquify Digital) --")
    sag = subj_profile.get("agency") or {}
    sfl_top = subj_profile.get("main_freelancer") or {}
    sfl = (sfl_top.get("data") or {}) if isinstance(sfl_top, dict) else {}
    if sag:
        _stats = sag.get("stats") or {}
        _jss = _stats.get("jobSuccessScore")
        lines.append(f"  agency_name: {sag.get('name')}")
        lines.append(f"  agency_stats: earned=${(_stats.get('totalEarning') or 0):,.0f} · jss={(_jss*100 if isinstance(_jss,(int,float)) else '—')}%")
        lines.append(f"  agency_locations: {[(l or {}).get('country') for l in (sag.get('locations') or [])[:2]]}")
        lines.append(f"  agency_summary: {(sag.get('summary_or_description') or '')[:400]}")
        if sag.get("skills"):
            lines.append(f"  agency_skills (top 10): {sag['skills'][:10]}")
        if sag.get("services"):
            svc_titles = [f"{(s or {}).get('name') or (s or {}).get('title')}" for s in sag["services"][:5]]
            lines.append(f"  agency_services: {svc_titles}")
    if sfl:
        lines.append(f"  main_freelancer: {sfl.get('general_name')} · ${sfl.get('general_hourly_rate')}/hr · {sfl.get('general_location')} · tier={sfl.get('contractor_tier')}")
        lines.append(f"  fl_title: {sfl.get('general_title')}")
        lines.append(f"  fl_description (first 500): {(sfl.get('general_description') or '')[:500]}")
        if sfl.get("skills"):
            lines.append(f"  fl_skills (top 10): {sfl['skills'][:10]}")

    lines.append("")
    lines.append("-- BID PAIRS --")
    for pn, pair in enumerate(comp["pairs"], 1):
        cw = pair["competitor_win"]
        uw = pair.get("ub_win_match") or {}
        ul = pair.get("ub_loss_match") or {}
        fr = (cw.get("freelancer") or {}) if isinstance(cw.get("freelancer"), dict) else {}
        lines.append(f"")
        lines.append(f"PAIR {pn}")
        lines.append(f"  comp_title: {cw.get('job_title','')}")
        lines.append(f"  comp_meta: bid={cw.get('bid_amount')} connects={cw.get('connects_bid')} gr={(cw.get('gigradar_bid') or {}).get('is_gigradar_bid')} scanner={(cw.get('gigradar_bid') or {}).get('scanner_name')} algo={(cw.get('gigradar_bid') or {}).get('algorithm_name')} cl_len={cw.get('cl_length')} is_invite={cw.get('is_invite')}")
        lines.append(f"  comp_freelancer: name={fr.get('name')!r} title={fr.get('title')!r} rate={fr.get('hourly_rate')} location={fr.get('location')!r}")
        lines.append(f"  COMP_CL:")
        lines.append((cw.get("full_cl") or "")[:4000])

        if uw:
            lines.append(f"  UB_WIN: title={uw.get('job_title','')!r} match_mode={uw.get('match_mode')} cos={uw.get('match_cosine')} len={uw.get('cl_length')} freelancer={uw.get('freelancer_name')!r}")
            lines.append(f"  UB_WIN_CL:")
            lines.append((uw.get("cl") or "")[:3000])
        else:
            lines.append(f"  UB_WIN: (no comparable above similarity threshold)")

        if ul:
            lines.append(f"  UB_LOSS: title={ul.get('job_title','')!r} match_mode={ul.get('match_mode')} cos={ul.get('match_cosine')} len={ul.get('cl_length')} freelancer={ul.get('freelancer_name')!r}")
            lines.append(f"  UB_LOSS_CL:")
            lines.append((ul.get("cl") or "")[:3000])
        else:
            lines.append(f"  UB_LOSS: (no comparable above similarity threshold)")

    body = "\n".join(lines)
    fname.write_text(body)
    print(f"  wrote {fname}  ({fname.stat().st_size} bytes)")

print(f"\n{len(data['competitors'])} bundles written to {OUT_DIR}")
