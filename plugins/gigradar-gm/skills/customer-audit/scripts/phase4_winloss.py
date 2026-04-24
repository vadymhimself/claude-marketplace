import os
"""
Phase 4 — Win/Loss CL comparison.

For each top scanner (by volume in focus window), cherry-pick:
  - 1-2 BEST winning proposals (hired > strong reply)
  - 1-2 WORST losing proposals (sent, no reply, good client stats)

Then pull full CL + JD + client + scanner config for each — ready for xlsx.
"""
import json, urllib.request, ssl, base64
from collections import defaultdict
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
ES_URL  = os.environ.get("ES_URL", "https://your-es-cluster.example/")  # override via env
ES_USER = os.environ.get("ES_USER", "")
ES_PASS = os.environ["ES_PASS"]  # request from admin
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase4_winloss.json"

FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)

auth = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
ctx = ssl.create_default_context()

def es_get(path):
    import urllib.parse
    req = urllib.request.Request(
        ES_URL + path,
        headers={"Authorization": f"Basic {auth}"},
        method="GET",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30, context=ctx).read())
    except urllib.error.HTTPError as e:
        return None

c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]
contractor_col = db.get_collection("upwork.contractor.profiles")

# UB side contractor profile cache — the subject team typically rotates 3-4
# freelancers across all bids; a cache collapses the lookup to one per identity.
_ub_contractor_cache = {}

def resolve_ub_freelancer(f_uid, f_prof_uid):
    """Resolve Ubiquify freelancer profile by upworkFreelancerUid. Mirrors
    phase2b's `_resolve_freelancer` but kept inline to avoid a cross-script import."""
    if not f_uid:
        return None
    cache_key = (f_uid, f_prof_uid)
    if cache_key in _ub_contractor_cache:
        return _ub_contractor_cache[cache_key]
    fp = contractor_col.find_one({"uid": f_uid}) or \
         contractor_col.find_one({"contractorUid": f_uid}) or \
         contractor_col.find_one({"ciphertext": f_uid})
    if not fp:
        _ub_contractor_cache[cache_key] = None
        return None
    ciph = fp.get("ciphertext")
    nested = (fp.get("profile") or {})
    lvl1 = nested.get("profile") or {}
    lvl2 = lvl1.get("profile") or {}
    general_title = fp.get("title") or lvl2.get("title")
    general_desc = lvl2.get("description") or nested.get("description") or fp.get("description")
    general_location = (lvl2.get("location") or {}).get("country")
    hr_top = fp.get("hourlyRate") or {}
    top_hourly = hr_top.get("amount") if isinstance(hr_top, dict) else hr_top
    # Specialized profile (preferred if f_prof_uid specified — that is what was
    # actually selected at bid time by the scanner's biddingStrategy)
    spec_title = None; spec_desc = None; spec_hourly = None
    if f_prof_uid:
        for sp in (fp.get("specializedProfiles") or []):
            if sp.get("profileUid") == f_prof_uid:
                spec_title = sp.get("title")
                spec_desc = sp.get("description")
                hr = sp.get("hourlyRate") or {}
                spec_hourly = hr.get("amount") if isinstance(hr, dict) else hr
                break
    data = {
        "name": fp.get("fullName"),
        "url": f"https://www.upwork.com/freelancers/{ciph}" if ciph else None,
        "hourly_rate": spec_hourly or top_hourly,
        "location": general_location,
        "title": spec_title or general_title,
        "description": (spec_desc or general_desc or "")[:600] if (spec_desc or general_desc) else None,
    }
    _ub_contractor_cache[cache_key] = data
    return data

# ---- Scanner-volume count in focus window (opportunities-driven) ----
print("Aggregating scanner volumes in focus window...")
volume_pipeline = [
    {"$match": {
        "gigradarTeamId": TEAM_OID,
        "isPreview": {"$ne": True},
        "notified": {"$gte": FOCUS_START, "$lt": FOCUS_END},
        "application.sent": {"$exists": True},
    }},
    {"$group": {
        "_id": "$scannerId",
        "name": {"$last": "$scannerName"},
        "sent": {"$sum": 1},
    }},
    {"$sort": {"sent": -1}},
]
scanners = list(db.opportunities.aggregate(volume_pipeline, allowDiskUse=True))
print(f"Total scanners with sent in focus: {len(scanners)}")
top_scanners = [s for s in scanners if s["sent"] >= 15][:12]
print(f"Top scanners (≥15 sent):")
for s in top_scanners:
    print(f"  {str(s['_id'])[:8] if s['_id'] else 'null':8s}  name={s['name']!r:40s}  sent={s['sent']}")


# Fetch team scanners once — embedded in team.scanners[]
team_doc = db.teams.find_one({"_id": TEAM_OID}, {"scanners": 1})
scanner_configs = {str(s.get("_id")): s for s in (team_doc.get("scanners") or [])}


def scanner_cfg(scanner_id):
    cfg = scanner_configs.get(str(scanner_id), {}) or {}
    q = cfg.get("query") or {}
    bs = cfg.get("biddingStrategy") or {}
    return {
        "name": cfg.get("name"),
        "query_q": (q.get("q") or "")[:800],
        "query_category": q.get("category"),
        "bidding_strategy_type": bs.get("type"),
        "bidding_strategy_amount": bs.get("amount"),
        "bidding_strategy_currency": bs.get("currency"),
        "strategy_template": (bs.get("template") or "")[:400] if isinstance(bs.get("template"), str) else None,
        "disabled": cfg.get("disabled"),
        "archived": cfg.get("archived"),
        "deleted": cfg.get("deleted"),
    }


def pick_winners_losers(scanner_id, limit_each=2):
    """
    For a scanner, pull all opps w/ joined proposals in focus,
    cherry-pick best wins and worst losses.
    """
    pipeline = [
        {"$match": {
            "gigradarTeamId": TEAM_OID,
            "scannerId": scanner_id,
            "notified": {"$gte": FOCUS_START, "$lt": FOCUS_END},
            "isPreview": {"$ne": True},
            "application.proposalId": {"$exists": True, "$nin": [None, ""]},
        }},
        {"$lookup": {
            "from": "proposals",
            "localField": "application.proposalId",
            "foreignField": "meta.uid",
            "pipeline": [
                {"$match": {"_gigradarTeamOid": TEAM_OID, "meta.inviteToInterviewUid": None}},
                {"$project": {
                    "_id": 1,
                    "meta.uid": 1, "meta.jobId": 1, "meta.jobTitle": 1,
                    "meta.createdAt": 1, "meta.status": 1, "meta.chat.chatId": 1,
                    "meta.connectsExpended": 1,
                    "meta.freelancer.name": 1, "meta.freelancer.rid": 1,
                    "meta.author.name": 1, "meta.author.uid": 1,
                    "renderedCoverLetter": 1, "coverLetter": 1,
                    "terms.connectsBid": 1, "terms.hourlyRate": 1, "terms.amount": 1,
                    "client.buyer.info.company.name": 1,
                    "client.buyer.info.company.profile.industry": 1,
                    "client.buyer.info.company.profile.size": 1,
                    "client.stats": 1,
                    "archiveReason.reason": 1, "declineReadon": 1,
                    "auditDetails.modifiedTs": 1, "auditDetails.createdTs": 1,
                    "dashroomUID": 1, "otherAnnotations": 1, "connectsExpended": 1,
                    "metaJob.ciphertext": 1,
                }},
            ],
            "as": "proposal",
        }},
        {"$unwind": {"path": "$proposal", "preserveNullAndEmptyArrays": False}},
        {"$project": {
            "_id": 1,
            "scannerId": 1,
            "scannerName": 1,
            "originalGigTempId": 1,
            "score": 1,
            "jobId": 1,
            "application": 1,
            "proposal": 1,
        }},
    ]
    rows = list(db.opportunities.aggregate(pipeline, allowDiskUse=True))

    # split
    winners, losers = [], []
    for r in rows:
        p = r["proposal"]
        ms = p.get("meta", {}).get("status")
        ap = r.get("application") or {}
        is_hired = ms in (10, "Hired")
        is_replied = bool(p.get("dashroomUID")) or bool((p.get("meta", {}).get("chat") or {}).get("chatId"))
        is_interviewed = ms in (7, "Active", "ACTIVE")
        stats = (p.get("client") or {}).get("stats") or {}
        spent = stats.get("totalSpent") or 0
        fb = stats.get("feedbackScore") or 0
        match = ap.get("matchPercentage") or 0
        if is_hired or is_interviewed or is_replied:
            priority_key = 3 if is_hired else 2 if is_interviewed else 1
            winners.append(((priority_key, spent, fb), r))
        else:
            # Losers: high-match-score or good-client-stats jobs that didn't convert
            # We want the most instructive losses — jobs that looked like strong fits
            loss_score = match + (spent / 10000.0) + fb * 2.0
            losers.append(((loss_score, match, spent), r))
    winners = sorted(winners, key=lambda x: (-x[0][0], -(x[0][1] or 0), -(x[0][2] or 0)))
    losers = sorted(losers, key=lambda x: (-(x[0][0] or 0), -(x[0][1] or 0), -(x[0][2] or 0)))
    return [w[1] for w in winners[:limit_each]], [l[1] for l in losers[:limit_each]]


def fetch_jd_excerpt(ciphertext, limit=1200):
    if not ciphertext:
        return None
    import urllib.parse
    doc = es_get(f"/metajob/_doc/{urllib.parse.quote(ciphertext, safe='')}")
    if not doc or "_source" not in doc:
        return None
    mj = (doc.get("_source") or {}).get("metaJob") or {}
    desc = mj.get("description") or ""
    title = mj.get("title") or ""
    cat = mj.get("categoryName")
    return {
        "title": title,
        "category": cat,
        "description_excerpt": desc[:limit],
        "budget": mj.get("budget"),
    }


def row_to_table(r, outcome):
    p = r["proposal"] or {}
    ap = (r.get("application") or {})
    meta = p.get("meta") or {}
    terms = p.get("terms") or {}
    buyer_co = (((p.get("client") or {}).get("buyer") or {}).get("info") or {}).get("company") or {}
    stats = (p.get("client") or {}).get("stats") or {}
    cl = p.get("renderedCoverLetter") or p.get("coverLetter") or ""
    cl_gen = ap.get("coverLetter") or ""
    ciphertext = (p.get("metaJob") or {}).get("ciphertext")
    jd = fetch_jd_excerpt(ciphertext, limit=1000) if ciphertext else None
    meta_freelancer = (meta.get("freelancer") or {})
    meta_author = (meta.get("author") or {})
    return {
        "outcome": outcome,
        "proposal_id": str(p["_id"]),
        "opp_id": str(r["_id"]),
        "scanner_id": str(r.get("scannerId") or ""),
        "scanner_name": r.get("scannerName"),
        "template_id": str(r.get("originalGigTempId") or ""),
        "algorithm_signature": ap.get("algorithmSignature"),
        "algorithm_ver": ap.get("algorithmVer"),
        "prompt_version": ap.get("promptVersion"),
        "model": ap.get("model"),
        "match_percentage": ap.get("matchPercentage"),
        "match_argumentation": (ap.get("matchPercentageArgumentation") or "")[:500],
        "score": r.get("score"),
        # Frozen-at-bid CL template — sourced STRICTLY from
        # opp.application.originalStrategy.options.template. Never falls back to the
        # team's current scanner.biddingStrategy.options.template (which drifts after
        # scanner edits and will NOT match the actual rendered CL below).
        "cl_template_frozen": ((ap.get("originalStrategy") or {}).get("options") or {}).get("template"),
        "cl_template_source": "opp.application.originalStrategy.options.template" if ((ap.get("originalStrategy") or {}).get("options") or {}).get("template") else None,
        # Per-proposal freelancer identity + resolved contractor profile.
        # Subject team rotates freelancers across bids; card rendering needs all
        # the same fields the competitor side has (name, rate, title, description,
        # location) so the workbook can lay them out symmetrically.
        "freelancer_name": meta_freelancer.get("name") or meta_author.get("name"),
        "freelancer_rid": meta_freelancer.get("rid"),
        "author_uid": meta_author.get("uid"),
        "upwork_freelancer_uid": ap.get("upworkFreelancerUid"),
        "upwork_freelancer_profile_uid": ap.get("upworkFreelancerProfileUid"),
        "freelancer_profile": resolve_ub_freelancer(
            ap.get("upworkFreelancerUid"),
            ap.get("upworkFreelancerProfileUid"),
        ),
        "job_title": meta.get("jobTitle"),
        "job_ciphertext": ciphertext,
        "jd_title": (jd or {}).get("title"),
        "jd_category": (jd or {}).get("category"),
        "jd_excerpt": (jd or {}).get("description_excerpt"),
        "jd_budget": (jd or {}).get("budget"),
        "meta_createdAt": str(meta.get("createdAt") or ""),
        "meta_status": meta.get("status"),
        "hire_date": str((p.get("auditDetails") or {}).get("modifiedTs") or "") if meta.get("status") in (10, "Hired") else None,
        "replied": bool(p.get("dashroomUID") or (meta.get("chat") or {}).get("chatId")),
        "rendered_cover_letter_excerpt": (cl or "")[:1500],
        "generated_cover_letter_excerpt": (cl_gen or "")[:1500],
        "cl_length": len(cl),
        "connects_bid": terms.get("connectsBid"),
        "connects_expended": meta.get("connectsExpended"),
        "hourly_rate": terms.get("hourlyRate"),
        "amount": terms.get("amount"),
        "app_bid": ap.get("bid"),
        "app_connect_price": ap.get("connectPrice"),
        "app_cost": ap.get("cost"),
        "client_company": buyer_co.get("name"),
        "client_industry": ((buyer_co.get("profile") or {}).get("industry")),
        "client_size": ((buyer_co.get("profile") or {}).get("size")),
        "client_total_spent": stats.get("totalSpent"),
        "client_feedback_score": stats.get("feedbackScore"),
        "client_hire_rate": stats.get("hireRate"),
        "client_country": stats.get("countryCode") or stats.get("country"),
        "archive_reason": (p.get("archiveReason") or {}).get("reason"),
        "decline_reason": p.get("declineReadon"),
    }


result = {"scanners": []}

for s in top_scanners:
    sid = s["_id"]
    if not sid:
        continue
    winners, losers = pick_winners_losers(sid)
    rows_out = []
    for w in winners:
        rows_out.append(row_to_table(w, "WIN"))
    for l in losers:
        rows_out.append(row_to_table(l, "LOSS"))
    sc = {
        "scanner_id": str(sid),
        "scanner_name": s["name"],
        "sent_in_focus": s["sent"],
        "config": scanner_cfg(sid),
        "winners_count": len(winners),
        "losers_count": len(losers),
        "rows": rows_out,
    }
    result["scanners"].append(sc)
    print(f"  [{s['name']!r:40s}] wins={len(winners)} losses={len(losers)}")

with open(OUT, "w") as f:
    json.dump(result, f, indent=2, default=str)
print(f"\nWrote {OUT}")
