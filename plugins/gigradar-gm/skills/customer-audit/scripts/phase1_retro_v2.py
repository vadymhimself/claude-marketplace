"""
Phase 1 (v2) — Retro with OPPORTUNITIES-FIRST JOIN.

Per audit-playbook.md §join-rule-0 / data-reference.md §24.10a:
- scanner / template / algorithm / generated-CL metadata lives on `opportunities`, NOT `proposals`
- Join: opportunities.application.proposalId ↔ proposals.meta.uid  (both strings)
- Team filter: opportunities.gigradarTeamId (ObjectId) ↔ proposals._gigradarTeamOid (ObjectId)

Per metrics.md / §24.8 / §24.7 / §24.11:
- Reply signal: dashroomUID non-empty (canonical), meta.chat.chatId (dual-written mirror) — use $exists/$nin, NEVER $ne:null
- Hire: meta.status ∈ {10, "Hired"} via $in
- Connects ladder: terms.connectsBid > 0 ? : meta.connectsExpended ?? connectsExpended ?? 0

Split auto-bidder (has opp) vs manual (no opp) cohorts — they diverge ~16× on Ubiquify.
Split invite (meta.inviteToInterviewUid != null) vs outbound-bid cohorts.
Team joined date: use team.createdAt, fallback to ObjectId(team._id).generation_time.
Hire date: auditDetails.modifiedTs. NOT client.buyer.info.company.contractDate (that's client signup).

Output: phase1_retro_v2.json  (structured retro record)
        all_proposals_v2.ndjson  (per-proposal enriched with opp join + cohort flags)
"""

import json
import os
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import MongoClient


MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT_DIR = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify"

FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)
FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
PRIOR_END = FOCUS_START
PRIOR_START = datetime(2026, 2, 21, tzinfo=timezone.utc)


def d2s(d):
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.isoformat()
    return str(d)


def _default(v):
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    try:
        return str(v)
    except Exception:
        return None


def aware(d):
    if d is None:
        return None
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(d, datetime) and d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def connects_ladder(prop):
    """Codebase-canonical connects-spent: terms.connectsBid>0 ? : meta.connectsExpended ?? connectsExpended ?? 0"""
    terms = prop.get("terms") or {}
    cb = terms.get("connectsBid") or 0
    if cb and cb > 0:
        return cb
    meta = prop.get("meta") or {}
    if meta.get("connectsExpended"):
        return meta["connectsExpended"]
    return prop.get("connectsExpended") or 0


def is_hired(prop):
    """meta.status ∈ {10, 'Hired'}."""
    ms = (prop.get("meta") or {}).get("status")
    return ms == 10 or ms == "Hired"


def is_replied(prop):
    """dashroomUID non-empty (canonical) OR meta.chat.chatId non-empty (dual-write)."""
    d = prop.get("dashroomUID")
    if d is not None and d != "":
        return True
    chat = (prop.get("meta") or {}).get("chat") or {}
    return bool(chat.get("chatId"))


def is_outbound_bid(prop):
    """Non-invite bid: meta.inviteToInterviewUid is null."""
    return not (prop.get("meta") or {}).get("inviteToInterviewUid")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
    db = client["gigradar-dev"]

    # ----- A. Team + agency profile + scanners -----
    team_doc = db.teams.find_one(
        {"_id": TEAM_OID},
        {
            "_id": 1, "name": 1, "createdAt": 1,
            "subscription.coverLetterLlmConfig": 1,
            "subscription.status": 1, "subscription.plan": 1,
            "industry": 1, "serviceNames": 1,
        },
    )
    if not team_doc:
        print("TEAM NOT FOUND")
        return

    team_joined = aware(team_doc.get("createdAt")) or TEAM_OID.generation_time

    t_full = db.teams.find_one(
        {"_id": TEAM_OID},
        {
            "scanners._id": 1, "scanners.name": 1, "scanners.deleted": 1,
            "scanners.lastScan": 1,
            "scanners.biddingStrategy.algorithmSignature": 1,
            "scanners.biddingStrategy.options.disabled": 1,
            "scanners.biddingStrategy.options.template": 1,
            "scanners.biddingStrategy.options.answerTemplate": 1,
            "scanners.biddingStrategy.options.biddingTerms": 1,
            "scanners.biddingStrategy.options.smartBoost": 1,
            "scanners.biddingStrategy.options.autoBiddingDailyLimit": 1,
            "scanners.biddingStrategy.options.autoBiddingMonthlyLimit": 1,
            "scanners.biddingStrategy.options.connectsDailyLimit": 1,
            "scanners.biddingStrategy.options.connectsMonthlyLimit": 1,
            "scanners.biddingStrategy.options.llmConfigOverride": 1,
            "scanners.query.q": 1, "scanners.query.excluded": 1,
            "scanners.query.categories": 1, "scanners.query.budgets": 1,
            "scanners.query.countries": 1, "scanners.query.experienceLevel": 1,
            "scanners.query.clientIndustry": 1,
            "scanners.memory": 1,
        },
    )
    scanners_full = (t_full or {}).get("scanners") or []

    profile = db["upwork.agency.profiles"].find_one(
        {"gigradarTeamId": TEAM_OID},
        {
            "_id": 1, "title": 1, "description": 1, "hourlyRate": 1, "name": 1,
            "portfolioItems.title": 1, "portfolioItems.description": 1,
            "categoryName": 1, "skills": 1, "createdAt": 1,
        },
    )

    # ----- B. Build opportunity → proposal join map (proposalId → opp) -----
    # Opportunities are the auto-bidder record. Team filter uses gigradarTeamId (ObjectId).
    # Filter: proposalId exists (= there is an actual attempted proposal).
    print("Pulling opportunities ...")
    opps_cursor = db.opportunities.find(
        {
            "gigradarTeamId": TEAM_OID,
            "isPreview": {"$ne": True},
            "application.proposalId": {"$exists": True, "$nin": [None, ""]},
        },
        {
            "_id": 1, "gigradarTeamId": 1,
            "scannerId": 1, "scannerName": 1,
            "originalGigTempId": 1, "score": 1, "jobId": 1, "jobUid": 1,
            "notified": 1, "detected": 1, "generationStartedAt": 1, "published": 1,
            "application.proposalId": 1,
            "application.algorithmSignature": 1,
            "application.algorithmVer": 1,
            "application.promptVersion": 1,
            "application.model": 1,
            "application.config.llm": 1,
            "application.config.prompt_version": 1,
            "application.bid": 1,
            "application.connectPrice": 1,
            "application.cost": 1,
            "application.boost": 1,
            "application.matchPercentage": 1,
            "application.generated": 1,
            "application.sent": 1,
            # Keep CL / strategy narrow — only fetched separately for hired/replied later
        },
    )

    opp_by_pid = {}
    opp_count = 0
    for o in opps_cursor:
        opp_count += 1
        pid = ((o.get("application") or {}).get("proposalId"))
        if pid:
            opp_by_pid[str(pid)] = o
    print(f"  opps fetched: {opp_count}, with non-empty proposalId: {len(opp_by_pid)}")

    # ----- C. Full proposal history -----
    print("Pulling proposals ...")
    prop_cursor = db.proposals.find(
        {"_gigradarTeamOid": TEAM_OID},
        {
            "_id": 1, "_gigradarTeamOid": 1,
            "meta.uid": 1,
            "meta.createdAt": 1, "meta.status": 1,
            "meta.jobId": 1, "meta.jobTitle": 1,
            "meta.author.name": 1, "meta.author.uid": 1,
            "meta.freelancer.name": 1, "meta.freelancer.rid": 1,
            "meta.chat.chatId": 1, "meta.chat.createdTs": 1,
            "meta.inviteToInterviewUid": 1,
            "meta.connectsExpended": 1,
            "terms.connectsBid": 1, "terms.rate": 1, "terms.chargeRate": 1,
            "terms.amount": 1, "terms.hourlyRate": 1, "terms.duration": 1,
            "connectsExpended": 1,
            "dashroomUID": 1,
            "auditDetails.modifiedTs": 1, "auditDetails.createdTs": 1,
            "applicationUID": 1,
            "otherAnnotations": 1,
            "archiveReason.reason": 1, "archiveReason.reasonRef": 1,
            # CL text only kept for hired/replied — collected in D
        },
    ).sort("meta.createdAt", 1)

    proposals = []
    hired_docs = []
    replied_docs = []
    for p in prop_cursor:
        proposals.append(p)
        if is_hired(p):
            hired_docs.append(p)
        if is_replied(p):
            replied_docs.append(p)

    print(f"  proposals fetched: {len(proposals)}  hired: {len(hired_docs)}  replied: {len(replied_docs)}")

    # Fetch CL text narrowly for hired + replied only
    ids_for_cl = [p["_id"] for p in hired_docs + replied_docs]
    cl_map = {}
    if ids_for_cl:
        for d in db.proposals.find(
            {"_id": {"$in": ids_for_cl}},
            {"_id": 1, "renderedCoverLetter": 1, "coverLetter": 1},
        ):
            cl_map[d["_id"]] = {"renderedCoverLetter": d.get("renderedCoverLetter"), "coverLetter": d.get("coverLetter")}

    # Fetch opp-side CL text for the hired + replied set (from joined opps)
    opp_ids_for_cl = []
    for p in hired_docs + replied_docs:
        pid = (p.get("meta") or {}).get("uid")
        if pid and str(pid) in opp_by_pid:
            opp_ids_for_cl.append(opp_by_pid[str(pid)]["_id"])
    opp_cl_map = {}
    if opp_ids_for_cl:
        for d in db.opportunities.find(
            {"_id": {"$in": opp_ids_for_cl}},
            {
                "_id": 1,
                "application.coverLetter": 1,
                "application.originalStrategy.algorithmSignature": 1,
                "application.originalStrategy.options.template": 1,
                "application.originalStrategy.options.answerTemplate": 1,
                "application.originalStrategy.options.biddingTerms": 1,
                "application.originalStrategy.options.llmConfigOverride": 1,
            },
        ):
            opp_cl_map[d["_id"]] = d

    # ----- D. Per-proposal enrichment: cohort flags + opp join fields -----
    enriched = []
    auto_count = 0
    manual_count = 0
    for p in proposals:
        pid = (p.get("meta") or {}).get("uid")
        opp = opp_by_pid.get(str(pid)) if pid else None
        has_opp = opp is not None
        if has_opp:
            auto_count += 1
        else:
            manual_count += 1

        flat = {
            "_id": str(p["_id"]),
            "meta_uid": pid,
            "meta_jobId": (p.get("meta") or {}).get("jobId"),
            "meta_jobTitle": (p.get("meta") or {}).get("jobTitle"),
            "meta_createdAt": d2s((p.get("meta") or {}).get("createdAt")),
            "meta_status": (p.get("meta") or {}).get("status"),
            "auditDetails_modifiedTs": d2s((p.get("auditDetails") or {}).get("modifiedTs")),
            "dashroomUID": p.get("dashroomUID"),
            "chat_chatId": ((p.get("meta") or {}).get("chat") or {}).get("chatId"),
            "invite_to_interview_uid": (p.get("meta") or {}).get("inviteToInterviewUid"),
            "freelancer_name": ((p.get("meta") or {}).get("freelancer") or {}).get("name"),
            "freelancer_rid": ((p.get("meta") or {}).get("freelancer") or {}).get("rid"),
            "author_name": ((p.get("meta") or {}).get("author") or {}).get("name"),
            "terms_connectsBid": (p.get("terms") or {}).get("connectsBid"),
            "terms_hourlyRate": (p.get("terms") or {}).get("hourlyRate"),
            "terms_amount": (p.get("terms") or {}).get("amount"),
            "meta_connectsExpended": (p.get("meta") or {}).get("connectsExpended"),
            "connectsExpended_top": p.get("connectsExpended"),
            "connects_ladder": connects_ladder(p),
            "archive_reason": (p.get("archiveReason") or {}).get("reason"),
            "is_sent_bid": is_outbound_bid(p),
            "is_replied": is_replied(p),
            "is_hired": is_hired(p),
            "is_auto_bidder": has_opp,
            "cohort": "auto" if has_opp else "manual",
        }
        if has_opp:
            app = opp.get("application") or {}
            flat.update({
                "opp_id": str(opp["_id"]),
                "opp_scannerId": str(opp.get("scannerId")) if opp.get("scannerId") else None,
                "opp_scannerName": opp.get("scannerName"),
                "opp_originalGigTempId": str(opp.get("originalGigTempId")) if opp.get("originalGigTempId") else None,
                "opp_score": opp.get("score"),
                "opp_notified": d2s(opp.get("notified")),
                "app_algorithmSignature": app.get("algorithmSignature"),
                "app_algorithmVer": app.get("algorithmVer"),
                "app_promptVersion": app.get("promptVersion"),
                "app_model": app.get("model"),
                "app_config_llm": ((app.get("config") or {}).get("llm")),
                "app_config_prompt_version": ((app.get("config") or {}).get("prompt_version")),
                "app_bid_type": ((app.get("bid") or {}).get("type")),
                "app_bid_amount": ((app.get("bid") or {}).get("amount")),
                "app_connectPrice": app.get("connectPrice"),
                "app_cost": app.get("cost"),
                "app_boost": app.get("boost"),
                "app_matchPercentage": app.get("matchPercentage"),
                "app_generated": d2s(app.get("generated")),
                "app_sent": d2s(app.get("sent")),
            })
        # CL text (only for hired/replied)
        if p["_id"] in cl_map:
            flat["rendered_cover_letter"] = cl_map[p["_id"]].get("renderedCoverLetter")
            flat["cover_letter_proposal"] = cl_map[p["_id"]].get("coverLetter")
        if has_opp and opp["_id"] in opp_cl_map:
            o_d = opp_cl_map[opp["_id"]]
            flat["cover_letter_opportunity"] = ((o_d.get("application") or {}).get("coverLetter"))
            flat["original_strategy"] = ((o_d.get("application") or {}).get("originalStrategy"))
        enriched.append(flat)

    print(f"  cohorts: auto={auto_count}  manual={manual_count}")

    # ----- E. Aggregations -----
    def agg_cohort(records, predicate=None, label=""):
        sent = replied = hired = connects = 0
        for r in records:
            if not r["is_sent_bid"]:
                continue
            if predicate and not predicate(r):
                continue
            sent += 1
            connects += int(r["connects_ladder"] or 0)
            if r["is_replied"]:
                replied += 1
            if r["is_hired"]:
                hired += 1
        return {
            "label": label,
            "sent": sent, "replied": replied, "hired": hired,
            "connects_spent": connects, "connects_spend_usd": connects * 0.15,
            "reply_rate": (replied / sent) if sent else None,
            "hire_rate": (hired / sent) if sent else None,
            "cost_per_reply_usd": (connects * 0.15 / replied) if replied else None,
            "cost_per_hire_usd": (connects * 0.15 / hired) if hired else None,
        }

    def in_window(rec, start, end):
        ts = rec.get("meta_createdAt")
        if not ts:
            return False
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return start <= dt <= end

    # Overall (all-time) — split by cohort
    all_auto = agg_cohort(enriched, lambda r: r["is_auto_bidder"], "auto-bidder (all-time)")
    all_manual = agg_cohort(enriched, lambda r: not r["is_auto_bidder"], "manual (all-time)")
    all_combined = agg_cohort(enriched, None, "combined (all-time)")
    all_invites = sum(1 for r in enriched if not r["is_sent_bid"])

    focus_auto = agg_cohort(enriched, lambda r: r["is_auto_bidder"] and in_window(r, FOCUS_START, FOCUS_END), "auto-bidder focus")
    focus_manual = agg_cohort(enriched, lambda r: (not r["is_auto_bidder"]) and in_window(r, FOCUS_START, FOCUS_END), "manual focus")
    focus_combined = agg_cohort(enriched, lambda r: in_window(r, FOCUS_START, FOCUS_END), "combined focus")

    prior_auto = agg_cohort(enriched, lambda r: r["is_auto_bidder"] and in_window(r, PRIOR_START, PRIOR_END), "auto-bidder prior")
    prior_manual = agg_cohort(enriched, lambda r: (not r["is_auto_bidder"]) and in_window(r, PRIOR_START, PRIOR_END), "manual prior")
    prior_combined = agg_cohort(enriched, lambda r: in_window(r, PRIOR_START, PRIOR_END), "combined prior")

    # ----- F. Hired retro records -----
    hired_records = []
    for p in hired_docs:
        meta = p.get("meta") or {}
        audit = p.get("auditDetails") or {}
        pid = meta.get("uid")
        opp = opp_by_pid.get(str(pid)) if pid else None
        opp_cl = opp_cl_map.get(opp["_id"]) if opp else None
        app = (opp or {}).get("application") or {}
        sent_ts = aware(meta.get("createdAt"))
        hire_ts = aware(audit.get("modifiedTs")) or sent_ts
        ttc_days = None
        if sent_ts and hire_ts:
            try:
                ttc_days = (hire_ts - sent_ts).days
            except Exception:
                ttc_days = None
        cl_p = cl_map.get(p["_id"]) or {}
        hired_records.append({
            "proposal_id": str(p["_id"]),
            "meta_uid": pid,
            "job_title": meta.get("jobTitle"),
            "meta_status": meta.get("status"),
            "created_at": d2s(meta.get("createdAt")),
            "hire_ts_modified": d2s(audit.get("modifiedTs")),
            "time_to_close_days": ttc_days,
            "pre_gigradar": (aware(hire_ts) < aware(team_joined)) if (hire_ts and team_joined) else None,
            "freelancer_name": (meta.get("freelancer") or {}).get("name"),
            "author_name": (meta.get("author") or {}).get("name"),
            "is_invite": not is_outbound_bid(p),
            "terms": p.get("terms"),
            "connects_ladder": connects_ladder(p),
            # Auto-bidder attribution (only if opp exists)
            "cohort": "auto" if opp else "manual",
            "scanner_id": str(opp.get("scannerId")) if opp and opp.get("scannerId") else None,
            "scanner_name": (opp or {}).get("scannerName"),
            "template_id": str(opp.get("originalGigTempId")) if opp and opp.get("originalGigTempId") else None,
            "algorithm_signature": app.get("algorithmSignature"),
            "algorithm_ver": app.get("algorithmVer"),
            "prompt_version": app.get("promptVersion"),
            "model": app.get("model"),
            "match_percentage": app.get("matchPercentage"),
            "app_cost": app.get("cost"),
            "cover_letter_opp": ((opp_cl or {}).get("application") or {}).get("coverLetter") if opp_cl else None,
            "cover_letter_proposal": cl_p.get("renderedCoverLetter") or cl_p.get("coverLetter"),
            "original_strategy": ((opp_cl or {}).get("application") or {}).get("originalStrategy") if opp_cl else None,
        })

    # ----- G. Scanner inventory -----
    scanner_summary = []
    for s in scanners_full:
        if s.get("deleted"):
            continue
        strat = (s.get("biddingStrategy") or {}).get("options") or {}
        mem = s.get("memory") or {}
        mem_stmts = (mem.get("statements") or [])
        scanner_summary.append({
            "scanner_id": str(s.get("_id")),
            "name": s.get("name"),
            "disabled": strat.get("disabled"),
            "algorithm": (s.get("biddingStrategy") or {}).get("algorithmSignature"),
            "daily_cap": strat.get("autoBiddingDailyLimit"),
            "monthly_cap": strat.get("autoBiddingMonthlyLimit"),
            "connects_daily_cap": strat.get("connectsDailyLimit"),
            "connects_monthly_cap": strat.get("connectsMonthlyLimit"),
            "template_len": len(strat.get("template") or ""),
            "template_head": (strat.get("template") or "")[:400],
            "bidding_terms": strat.get("biddingTerms"),
            "q": (s.get("query") or {}).get("q"),
            "excluded": (s.get("query") or {}).get("excluded"),
            "categories_count": len((s.get("query") or {}).get("categories") or []),
            "countries": (s.get("query") or {}).get("countries"),
            "experience_level": (s.get("query") or {}).get("experienceLevel"),
            "memory_statements_count": len(mem_stmts),
            "last_scan": d2s(s.get("lastScan")),
            "llm_override": strat.get("llmConfigOverride"),
        })

    out = {
        "team": {
            "_id": str(team_doc["_id"]),
            "name": team_doc.get("name"),
            "created_at": d2s(team_doc.get("createdAt")),
            "joined_effective": d2s(team_joined),
            "subscription_status": (team_doc.get("subscription") or {}).get("status"),
            "subscription_plan": (team_doc.get("subscription") or {}).get("plan"),
            "industry": team_doc.get("industry"),
            "serviceNames": team_doc.get("serviceNames"),
            "default_cover_letter_config": (team_doc.get("subscription") or {}).get("coverLetterLlmConfig"),
        },
        "agency_profile": {
            "title": (profile or {}).get("title"),
            "description": (profile or {}).get("description"),
            "category": (profile or {}).get("categoryName"),
            "hourly_rate": (profile or {}).get("hourlyRate"),
            "portfolio_item_titles": [
                (pi or {}).get("title") for pi in ((profile or {}).get("portfolioItems") or [])
            ][:10],
            "skills": (profile or {}).get("skills"),
        },
        "cohort_split": {
            "total_proposals": len(enriched),
            "auto_bidder": auto_count,
            "manual": manual_count,
            "auto_pct": auto_count / len(enriched) if enriched else None,
            "manual_pct": manual_count / len(enriched) if enriched else None,
            "invites": all_invites,
        },
        "all_time": {
            "auto_bidder": all_auto,
            "manual": all_manual,
            "combined": all_combined,
        },
        "focus_window": {"start": d2s(FOCUS_START), "end": d2s(FOCUS_END),
                         "auto_bidder": focus_auto, "manual": focus_manual, "combined": focus_combined},
        "prior_window": {"start": d2s(PRIOR_START), "end": d2s(PRIOR_END),
                         "auto_bidder": prior_auto, "manual": prior_manual, "combined": prior_combined},
        "hired_proposals": hired_records,
        "scanners_active": scanner_summary,
    }

    with open(os.path.join(OUT_DIR, "phase1_retro_v2.json"), "w") as f:
        json.dump(out, f, indent=2, default=_default)

    with open(os.path.join(OUT_DIR, "all_proposals_v2.ndjson"), "w") as f:
        for r in enriched:
            f.write(json.dumps(r, default=_default) + "\n")

    # --- brief stdout summary ---
    print()
    print(f"Team: {team_doc.get('name')}  joined={d2s(team_joined)}")
    print(f"Proposals: total={len(enriched)}  auto={auto_count} ({auto_count/len(enriched):.1%})  manual={manual_count} ({manual_count/len(enriched):.1%})  invites={all_invites}")
    print()
    def fmt(a):
        rr = f"{a['reply_rate']*100:.2f}%" if a['reply_rate'] else "n/a"
        hr = f"{a['hire_rate']*100:.2f}%" if a['hire_rate'] else "n/a"
        cpr = f"${a['cost_per_reply_usd']:.2f}" if a['cost_per_reply_usd'] else "n/a"
        cph = f"${a['cost_per_hire_usd']:.2f}" if a['cost_per_hire_usd'] else "n/a"
        return f"sent={a['sent']:5d} rep={a['replied']:4d} hir={a['hired']:3d} conn={a['connects_spent']:6d} rr={rr:>7} hr={hr:>7} $/rep={cpr:>8} $/hir={cph:>9}"
    print("ALL-TIME")
    print(f"  auto    : {fmt(all_auto)}")
    print(f"  manual  : {fmt(all_manual)}")
    print(f"  combined: {fmt(all_combined)}")
    print()
    print(f"FOCUS  ({FOCUS_START.date()}..{FOCUS_END.date()})")
    print(f"  auto    : {fmt(focus_auto)}")
    print(f"  manual  : {fmt(focus_manual)}")
    print(f"  combined: {fmt(focus_combined)}")
    print()
    print(f"PRIOR  ({PRIOR_START.date()}..{PRIOR_END.date()})")
    print(f"  auto    : {fmt(prior_auto)}")
    print(f"  manual  : {fmt(prior_manual)}")
    print(f"  combined: {fmt(prior_combined)}")
    print()
    print(f"Hired: {len(hired_records)} (of which pre-GigRadar: {sum(1 for r in hired_records if r.get('pre_gigradar'))})")
    print(f"Wrote: {os.path.join(OUT_DIR, 'phase1_retro_v2.json')}")
    print(f"Wrote: {os.path.join(OUT_DIR, 'all_proposals_v2.ndjson')}")


if __name__ == "__main__":
    main()
