import os
"""
Phase 2B v2 — Peer look-alike KNN with enriched competitor data + job-centric KNN cohort.

Fixes and additions vs v1:

1. FIX TERMS PATH — `terms.chargeRate.amount` (STRING) / `terms.chargeRate.currency`,
   not `terms.amount` / `terms.hourlyRate` (non-existent fields).
2. ALGO DECODER — map raw `opportunities.application.algorithmSignature` signatures
   to human names via the AutoBidderSettings dictionary in gigradar-ant.
3. AGENCY URL + excerpt — pull `upwork.agency.profiles.metaProfile.*` fields and
   build `https://www.upwork.com/agencies/{upworkAgencyUid}/` links.
4. FREELANCER PROFILE — look up `upwork.contractor.profiles` via the competitor's
   scanner biddingStrategy (`options.upworkFreelancerUid`) and resolve the
   specialised profile through `options.upworkFreelancerProfileUid`. Build
   `https://www.upwork.com/freelancers/{ciphertext}` link.
5. FULL WINNING CL — from `proposals.renderedCoverLetter` (canonical text).
6. CL TEMPLATE USED — from `opportunities.application.originalStrategy.options.template`
   via the opp join `opp.application.proposalId == proposals.meta.uid`.
7. JD EXCERPT + CLIENT SPEND — from ES metajob (`metaJob.description`,
   `metaJob.client.stats.totalSpent`).
8. JOB-CENTRIC KNN COHORT — new 3rd cohort row. Seed set = Ubiquify replied/won
   jobs. Neighbor set = KNN k=30 of each seed. Teams in the cohort = ALL teams
   who applied to any (seed ∪ neighbor) ciphertext in the focus window.
   Reports reply/$ distribution for this narrow job-territory cohort, no
   winner-only filter.

Output: phase2b_peer_knn_v2.json
"""
import json
import urllib.request
import urllib.parse as urlp
import ssl
import base64
import statistics as st
from datetime import datetime, timezone
from collections import defaultdict, Counter
from pymongo import MongoClient
from bson import ObjectId

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
ES_URL  = os.environ.get("ES_URL", "https://your-es-cluster.example/")  # override via env
ES_USER = os.environ.get("ES_USER", "")
ES_PASS = os.environ["ES_PASS"]  # request from admin
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2b_peer_knn_v2.json"

FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)
EMBED_COVERAGE_START = datetime(2025, 10, 1, tzinfo=timezone.utc)

CONN_PRICE = 0.15

# Algorithm signature → human name (from gigradar-ant AutoBidderSettings + gigradar-definitions)
ALGO_DECODER = {
    "ㅤ⁤ ": "Template Bidder",
    "ㅤ⁤": "Sardor AI",
    "ALG_LAZ": "Laziza AI",
    "PUBLIC_API": "Public API",
}


def decode_algo(sig):
    if not sig:
        return None
    return ALGO_DECODER.get(sig, f"Unknown({sig!r})")


auth = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
ctx = ssl.create_default_context()


def es(path, body, method="POST"):
    req = urllib.request.Request(
        ES_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())


def es_get(path):
    req = urllib.request.Request(
        ES_URL + path,
        headers={"Authorization": f"Basic {auth}"},
        method="GET",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30, context=ctx).read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code}


c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]


# ============================================================================
# 1) Pull Ubiquify's replied / hired proposals since embed-coverage start
#    → seed set for KNN expansion
# ============================================================================
print("=" * 70)
print("(1) Ubiquify seed jobs — replied OR hired since 2025-10")
print("=" * 70)

seed_cts = []
for p in db.proposals.find(
    {
        "_gigradarTeamOid": TEAM_OID,
        "meta.createdAt": {"$gte": EMBED_COVERAGE_START},
        "meta.inviteToInterviewUid": None,
        "metaJob.ciphertext": {"$exists": True, "$nin": [None, ""]},
        "$or": [
            {"meta.status": {"$in": [10, "Hired"]}},
            {"dashroomUID": {"$exists": True, "$nin": [None, ""]}},
            {"meta.chat.chatId": {"$exists": True, "$nin": [None, ""]}},
        ],
    },
    {"_id": 1, "meta.uid": 1, "meta.status": 1, "meta.jobTitle": 1,
     "meta.createdAt": 1, "metaJob.ciphertext": 1, "dashroomUID": 1},
).sort("meta.createdAt", -1).limit(40):
    ct = (p.get("metaJob") or {}).get("ciphertext")
    if ct:
        seed_cts.append({
            "ciphertext": ct,
            "title": (p.get("meta") or {}).get("jobTitle"),
            "status": (p.get("meta") or {}).get("status"),
            "replied": bool(p.get("dashroomUID")),
            "hired": (p.get("meta") or {}).get("status") in [10, "Hired"],
        })

print(f"  seed candidates: {len(seed_cts)}")
seed_set = sorted({s["ciphertext"] for s in seed_cts})
print(f"  unique seed ciphertexts: {len(seed_set)}")


# ============================================================================
# 2) KNN expansion → neighbor ciphertexts + track appliedByTeams per hit
# ============================================================================
print()
print("=" * 70)
print(f"(2) KNN expansion over {len(seed_set)} seeds (k=30 each)")
print("=" * 70)

neighbor_set = set()                   # all neighbor ciphertexts
# For winners-per-neighborhood (original v1 competitor-finder goal):
winner_registry = defaultdict(lambda: {"count": 0, "jobs": []})
# For cohort-wide team set (new v2 goal): every teamId that applied to any
# seed_or_neighbor ciphertext, irrespective of win/lose:
cohort_teams = set()
# Seed-level debug
seed_reports = []

seed_embeddings_missing = 0
for ct in seed_set:
    try:
        doc = es_get(f"/metajob/_doc/{urlp.quote(ct, safe='')}")
    except Exception as e:
        doc = {"_error": str(e)}
    if not doc or "_source" not in doc:
        seed_reports.append({"seed": ct, "skip": "doc-miss"})
        continue
    emb = ((doc.get("_source") or {}).get("matcher") or {}).get("embedding")
    if not emb or len(emb) != 1536:
        seed_embeddings_missing += 1
        seed_reports.append({"seed": ct, "skip": "no-embedding"})
        continue
    knn_body = {
        "knn": {
            "field": "matcher.embedding",
            "query_vector": emb,
            "k": 30,
            "num_candidates": 300,
        },
        "_source": [
            "metaJob.ciphertext", "metaJob.title", "metaJob.createdOn",
            "metaJob.budget", "metaJob.categoryName",
            "metaJob.client.stats.totalSpent",
            "matcher.appliedByTeams",
        ],
        "size": 30,
    }
    try:
        knn_resp = es("/metajob/_search", knn_body)
    except Exception as e:
        seed_reports.append({"seed": ct, "skip": f"knn-error {e}"})
        continue
    hits = (knn_resp.get("hits") or {}).get("hits") or []
    seed_reports.append({"seed": ct, "n_neighbors": len(hits)})

    for h in hits:
        src = h.get("_source", {})
        hit_ct = (src.get("metaJob") or {}).get("ciphertext")
        if not hit_ct:
            continue
        if hit_ct != ct:
            neighbor_set.add(hit_ct)
        ab = ((src.get("matcher") or {}).get("appliedByTeams") or [])
        for a in ab:
            tid = a.get("teamId")
            if tid and str(tid) != str(TEAM_OID):
                cohort_teams.add(str(tid))
            # Winner-in-neighborhood tracking
            if a.get("proposalStatus") == 10 or a.get("isInterviewed"):
                if tid and str(tid) != str(TEAM_OID):
                    rec = winner_registry[str(tid)]
                    rec["count"] += 1
                    rec["jobs"].append({
                        "ciphertext": hit_ct,
                        "title": (src.get("metaJob") or {}).get("title"),
                        "createdOn": (src.get("metaJob") or {}).get("createdOn"),
                        "status": a.get("proposalStatus"),
                        "isInterviewed": a.get("isInterviewed"),
                    })

print(f"  neighbor ciphertexts: {len(neighbor_set)}")
print(f"  cohort teams (any applier to seed∪neighbor): {len(cohort_teams)}")
print(f"  winner-count competitors: {len(winner_registry)}")
print(f"  seeds without embedding: {seed_embeddings_missing}")


# ============================================================================
# 3) Job-centric KNN cohort aggregation (new 3rd row)
#    For EVERY team that applied to (seed ∪ neighbor), pull their full-period
#    bidding stats in the FOCUS window — same metric machinery as phase2a.
# ============================================================================
print()
print("=" * 70)
print("(3) Cohort stats for KNN-neighborhood teams (focus window)")
print("=" * 70)

cohort_team_oids = [ObjectId(t) for t in cohort_teams if len(t) == 24]
print(f"  cohort ObjectId count: {len(cohort_team_oids)} (will include subject for position)")

subject_in_cohort = TEAM_OID not in cohort_team_oids
cohort_team_oids.append(TEAM_OID)

agg_pipe = [
    {"$match": {
        "_gigradarTeamOid": {"$in": cohort_team_oids},
        "meta.createdAt": {"$gte": FOCUS_START, "$lte": FOCUS_END},
        "meta.inviteToInterviewUid": None,
    }},
    {"$group": {
        "_id": "$_gigradarTeamOid",
        "sent": {"$sum": 1},
        "replied": {"$sum": {"$cond": [
            {"$and": [
                {"$ne": ["$dashroomUID", None]},
                {"$ne": ["$dashroomUID", ""]},
            ]}, 1, 0]}},
        "hired": {"$sum": {"$cond": [{"$in": ["$meta.status", [10, "Hired"]]}, 1, 0]}},
        "connects": {"$sum": {
            "$cond": [
                {"$gt": [{"$ifNull": ["$terms.connectsBid", 0]}, 0]},
                "$terms.connectsBid",
                {"$ifNull": ["$meta.connectsExpended", {"$ifNull": ["$connectsExpended", 0]}]}
            ]
        }}
    }},
]
cohort_rows = list(db.proposals.aggregate(agg_pipe, allowDiskUse=True))
print(f"  cohort teams with ≥1 bid in focus: {len(cohort_rows)}")

MIN_SENT_COHORT = 50   # looser than 100 because KNN cohort is narrower
qualified = []
for r in cohort_rows:
    if r["sent"] < MIN_SENT_COHORT:
        continue
    cost = r["connects"] * CONN_PRICE
    rr = r["replied"] / r["sent"] if r["sent"] else None
    hr = r["hired"] / r["sent"] if r["sent"] else None
    cpr = cost / r["replied"] if r["replied"] else None
    cph = cost / r["hired"] if r["hired"] else None
    qualified.append({
        "team_id": str(r["_id"]),
        "sent": r["sent"],
        "replied": r["replied"],
        "hired": r["hired"],
        "connects": r["connects"],
        "cost_usd": cost,
        "reply_rate": rr,
        "hire_rate": hr,
        "cost_per_reply": cpr,
        "cost_per_hire": cph,
    })
print(f"  qualified (≥{MIN_SENT_COHORT} sent in focus): {len(qualified)}")

def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c_ = min(f + 1, len(values) - 1)
    if f == c_:
        return values[f]
    return values[f] + (values[c_] - values[f]) * (k - f)

def rank_among(values, v, higher_is_better):
    if v is None or not values:
        return None
    below = sum(1 for x in values if (x < v if higher_is_better else x > v))
    equal = sum(1 for x in values if x == v)
    return (below + 0.5 * equal) / len(values) * 100

def cohort_summary(qualified, label):
    rrs = [q["reply_rate"] for q in qualified if q["reply_rate"] is not None]
    cprs = [q["cost_per_reply"] for q in qualified if q["cost_per_reply"] is not None]
    hrs = [q["hire_rate"] for q in qualified if q["hired"] >= 1]
    subj_row = next((q for q in qualified if q["team_id"] == str(TEAM_OID)), None)
    out = {
        "label": label,
        "qualified_count": len(qualified),
        "reply_rate_stats": {
            "n": len(rrs),
            "min": min(rrs) if rrs else None, "p25": pct(rrs, 0.25),
            "median": st.median(rrs) if rrs else None,
            "p75": pct(rrs, 0.75), "p90": pct(rrs, 0.90),
            "max": max(rrs) if rrs else None,
        },
        "cost_per_reply_stats": {
            "n": len(cprs),
            "min": min(cprs) if cprs else None, "p25": pct(cprs, 0.25),
            "median": st.median(cprs) if cprs else None,
            "p75": pct(cprs, 0.75), "p90": pct(cprs, 0.90),
            "max": max(cprs) if cprs else None,
        },
        "hire_rate_among_hiring_stats": {
            "n_teams_with_hire": len(hrs),
            "min": min(hrs) if hrs else None, "p25": pct(hrs, 0.25),
            "median": st.median(hrs) if hrs else None,
            "p75": pct(hrs, 0.75), "p90": pct(hrs, 0.90),
            "max": max(hrs) if hrs else None,
        },
    }
    if subj_row:
        out["subject_position"] = {
            "sent": subj_row["sent"],
            "replied": subj_row["replied"],
            "hired": subj_row["hired"],
            "cost_usd": subj_row["cost_usd"],
            "reply_rate": subj_row["reply_rate"],
            "reply_rate_percentile": rank_among(rrs, subj_row["reply_rate"], True),
            "cost_per_reply": subj_row["cost_per_reply"],
            "cost_per_reply_percentile_lower_better": (
                rank_among(cprs, subj_row["cost_per_reply"], False)
                if subj_row["cost_per_reply"] is not None else None
            ),
            "hire_rate": subj_row["hire_rate"],
            "hire_rate_percentile_among_hiring": (
                rank_among(hrs, subj_row["hire_rate"], True)
                if subj_row["hired"] >= 1 else None
            ),
        }
    else:
        out["subject_position"] = {"note": "subject below MIN_SENT threshold or no bids"}
    out["top_by_reply_rate"] = sorted(qualified, key=lambda q: -(q["reply_rate"] or 0))[:15]
    out["top_by_cost_per_reply"] = sorted(
        [q for q in qualified if q["cost_per_reply"] is not None],
        key=lambda q: q["cost_per_reply"])[:15]
    return out

knn_cohort_sum = cohort_summary(qualified, "KNN-neighbor-jobs-cohort")


# ============================================================================
# 4) Competitor enrichment — top 10 from winner_registry
# ============================================================================
print()
print("=" * 70)
print("(4) Enriching top 10 peer winners")
print("=" * 70)

# Build per-team metrics dict from the cohort aggregation (step 3) so we can
# attach focus-window performance numbers to each competitor record.
team_metrics_by_id = {q["team_id"]: q for q in qualified}

# SOURCING (competitor selection): rank by # of wins in Ubiquify's
# neighborhood. A team that shows up in 8 of Ubiquify's KNN neighborhoods is
# demonstrably a DIRECT competitor — same job types, same buyer language.
# A team with a 17% reply rate but only 1 neighborhood overlap may just be a
# high-performing team in a *different* space, which teaches us nothing.
#
# Minimum neighborhood overlap — drop teams with <2 to avoid one-off coincidental
# co-appliers. Previous versions had no floor, which let low-overlap teams with
# strong overall metrics float into the top-10.
MIN_NEIGHBORHOOD_OVERLAP = 2

# Ranking key: primary = -count (neighborhood overlap). Ties broken by reply
# rate — so between two equally-similar competitors, the higher-performing one
# wins. Unmetricced (not in qualified) teams fall to the end.
def _rank_key(kv):
    tid_str, rec = kv
    m = team_metrics_by_id.get(tid_str) or {}
    return (
        -rec["count"],                                      # neighborhood overlap (primary)
        -(m.get("reply_rate") if m.get("reply_rate") is not None else -1),  # tiebreak: RR
        -(m.get("hired") or 0),                             # tiebreak: absolute hires
    )

ranked = [(tid, rec) for tid, rec in winner_registry.items()
          if rec["count"] >= MIN_NEIGHBORHOOD_OVERLAP]
ranked = sorted(ranked, key=_rank_key)
print(f"  ranking method: neighborhood overlap DESC (then reply_rate, then hires)")
print(f"  floor: overlap ≥ {MIN_NEIGHBORHOOD_OVERLAP} neighborhood wins → {len(ranked)} eligible teams")
# Debug — top-10 with metrics
for tid_str, rec in ranked[:10]:
    m = team_metrics_by_id.get(tid_str, {})
    rr = m.get("reply_rate")
    rr_txt = f"{rr*100:.1f}%" if rr is not None else "—"
    cpr = m.get("cost_per_reply")
    cpr_txt = f"${cpr:.2f}" if cpr is not None else "—"
    print(f"    {tid_str[:12]}  sent={m.get('sent','—')}  rr={rr_txt}  "
          f"hires={m.get('hired','—')}  $/rep={cpr_txt}  nbhd_wins={rec['count']}")

agency_col = db.get_collection("upwork.agency.profiles")
contractor_col = db.get_collection("upwork.contractor.profiles")


def _resolve_freelancer(col, f_uid, f_prof_uid):
    """Resolve an Upwork freelancer profile by upworkFreelancerUid.
    Returns (fr_data_dict_or_None, upwork_url_or_None).

    Captures the RICH fields needed for the sheet-top profile comparison
    block: avatar portrait URL, nested skills list, employment history, JSS,
    contractor tier, profile URL. These aren't used for bid-row rendering
    (which only needs name/rate/location/title/description) but feed the
    freelancer-vs-freelancer side-by-side at the top of Sheet 3.
    """
    if not f_uid:
        return None, None
    fp = col.find_one({"uid": f_uid}) or \
         col.find_one({"contractorUid": f_uid}) or \
         col.find_one({"ciphertext": f_uid})
    if not fp:
        return None, None
    ciph = fp.get("ciphertext")
    fr_url = f"https://www.upwork.com/freelancers/{ciph}" if ciph else None
    general_title = fp.get("title")
    general_desc = None
    general_location = None
    general_name = fp.get("fullName")
    nested_profile = (fp.get("profile") or {})
    lvl1 = nested_profile.get("profile") or {}
    lvl2 = lvl1.get("profile") or {}
    # Portrait URL — prefer the nested 500-px version (smaller than original,
    # still high quality). The top-level `portrait` is a 120-px thumbnail.
    portrait_data = lvl2.get("portrait") or {}
    portrait_url = None
    if isinstance(portrait_data, dict):
        portrait_url = (portrait_data.get("portrait500") or portrait_data.get("bigPortrait")
                        or portrait_data.get("portrait") or portrait_data.get("smallPortrait"))
    portrait_url = portrait_url or fp.get("portrait")
    # Skills — the nested lvl2.skills list contains ontology-skill dicts;
    # extract the pretty label for readable display.
    skills_list = []
    raw_skills = lvl2.get("skills") or []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                label = s.get("prettyName") or s.get("name")
                if label:
                    skills_list.append(label)
    # Employment history — lives at lvl1.employmentHistory (list of dicts with
    # title, company, startDate, endDate, summary).
    emp_hist = []
    for e in (lvl1.get("employmentHistory") or [])[:6]:
        if isinstance(e, dict):
            emp_hist.append({
                "title": e.get("title") or e.get("jobTitle"),
                "company": e.get("company") or e.get("companyName"),
                "start": str(e.get("startDate") or "")[:10],
                "end": str(e.get("endDate") or "")[:10] or "present",
                "summary": (e.get("summary") or e.get("description") or "")[:300],
            })
    # Portfolios — lvl1.portfolios
    portfolios = []
    for p in (lvl1.get("portfolios") or [])[:8]:
        if isinstance(p, dict):
            portfolios.append({
                "title": p.get("title"),
                "description": (p.get("description") or "")[:300],
                "completionDate": str(p.get("completionDateTime") or "")[:10],
                "projectUrl": p.get("projectUrl"),
            })
    # Stats — lvl1.stats typically has earnings, hours, jobs count, jobSuccessScore
    stats = lvl1.get("stats") or {}
    if isinstance(stats, dict):
        stats = {
            "total_earnings": stats.get("totalEarnings") or stats.get("totalEarning"),
            "total_jobs": stats.get("totalJobs") or stats.get("totalJobsWorked"),
            "total_hours": stats.get("totalHoursWorked") or stats.get("totalHours"),
            "job_success_score": stats.get("jobSuccessScore"),
            "rating": stats.get("averageFeedback") or stats.get("rating"),
        }
    contractor_tier = lvl2.get("contractorTier") or lvl1.get("contractorTier")
    languages = []
    for l in (lvl1.get("languages") or [])[:6]:
        if isinstance(l, dict):
            lang = l.get("language") or l.get("name")
            prof = l.get("proficiency") or l.get("level")
            if lang:
                languages.append(f"{lang}" + (f" ({prof})" if prof else ""))
    if lvl2:
        general_desc = general_desc or lvl2.get("description")
        general_title = general_title or lvl2.get("title")
        general_location = (lvl2.get("location") or {}).get("country")
    general_desc = general_desc or nested_profile.get("description") or fp.get("description")
    hr_top = fp.get("hourlyRate") or {}
    top_hourly = hr_top.get("amount") if isinstance(hr_top, dict) else hr_top
    spec_title = None
    spec_desc = None
    spec_hourly = None
    spec_profile_uid = None
    if f_prof_uid:
        specs = fp.get("specializedProfiles") or []
        if isinstance(specs, list):
            for sp in specs:
                if sp.get("profileUid") == f_prof_uid:
                    spec_profile_uid = sp.get("profileUid")
                    spec_title = sp.get("title")
                    spec_desc = sp.get("description")
                    hr = sp.get("hourlyRate") or {}
                    spec_hourly = hr.get("amount") if isinstance(hr, dict) else hr
                    break
        if not spec_title:
            rates = fp.get("specializedProfilesRates") or []
            if isinstance(rates, list):
                for r_ in rates:
                    if r_.get("profileUID") == f_prof_uid:
                        spec_title = r_.get("profileTitle")
                        hr = r_.get("hourlyRate") or {}
                        spec_hourly = hr.get("amount") if isinstance(hr, dict) else hr
                        break
    fr_data = {
        "general_name": general_name,
        "general_title": general_title,
        "general_description": (general_desc or "")[:1500] if general_desc else None,
        "general_location": general_location,
        "general_ciphertext": ciph,
        "general_hourly_rate": top_hourly,
        # Rich profile-block fields:
        "portrait_url": portrait_url,
        "skills": skills_list[:20],
        "employment_history": emp_hist,
        "portfolios": portfolios,
        "stats": stats,
        "contractor_tier": contractor_tier,
        "languages": languages,
        "specialized": {
            "profile_uid": spec_profile_uid,
            "title": spec_title,
            "description": (spec_desc or "")[:1500] if spec_desc else None,
            "hourly_rate": spec_hourly,
        } if spec_profile_uid or spec_title else None,
    }
    return fr_data, fr_url


# Cross-competitor cache — if the same freelancer shows up on multiple bids
# (or across competitors), we fetch the contractor profile once.
contractor_uid_cache = {}

# --- Resolve Ubiquify's OWN main freelancer + agency for the profile block ---
# Pick the freelancerUid that appears most often across the subject team's
# active scanners (the "primary" identity). This anchors the right-pane of the
# sheet-top profile comparison and feeds the geo cohort country.
subj_team = db.teams.find_one(
    {"_id": TEAM_OID},
    {"name": 1, "scanners.biddingStrategy.options.upworkFreelancerUid": 1,
     "scanners.biddingStrategy.options.upworkFreelancerProfileUid": 1,
     "scanners.deleted": 1},
) or {}
_subj_uid_counts = {}
for _s in (subj_team.get("scanners") or []):
    if _s.get("deleted"): continue
    _opts = (_s.get("biddingStrategy") or {}).get("options") or {}
    _u = _opts.get("upworkFreelancerUid")
    if _u:
        _subj_uid_counts[_u] = _subj_uid_counts.get(_u, 0) + 1
subj_main_fl_uid = max(_subj_uid_counts, key=_subj_uid_counts.get) if _subj_uid_counts else None
# Find a specialized-profile uid that pairs with the main fl uid (pick the most
# common one from scanners that use main uid).
subj_main_fl_prof_uid = None
for _s in (subj_team.get("scanners") or []):
    _opts = (_s.get("biddingStrategy") or {}).get("options") or {}
    if _opts.get("upworkFreelancerUid") == subj_main_fl_uid and _opts.get("upworkFreelancerProfileUid"):
        subj_main_fl_prof_uid = _opts["upworkFreelancerProfileUid"]
        break

subj_fl_data, subj_fl_url = _resolve_freelancer(contractor_col, subj_main_fl_uid, subj_main_fl_prof_uid)
subj_agency = agency_col.find_one({"gigradarTeamId": str(TEAM_OID)}, {
    "metaProfile": 1, "portfolios": 1, "awards": 1, "skills": 1,
    "services": 1, "overview": 1, "description": 1,
    "companyInformation": 1, "upworkActivity": 1, "badges": 1,
}) or {}

competitors = []
for _comp_idx, (tid_str, rec) in enumerate(ranked[:10], 1):
    try:
        tid = ObjectId(tid_str)
    except Exception:
        continue

    print(f"  [{_comp_idx}/10] {tid_str[:12]} …", flush=True)

    # Team basics + scanners/biddingStrategy for freelancer profile resolution
    t = db.teams.find_one(
        {"_id": tid},
        {"name": 1, "serviceNames": 1, "industry": 1,
         "scanners._id": 1,
         "scanners.biddingStrategy.algorithmSignature": 1,
         "scanners.biddingStrategy.options.upworkFreelancerUid": 1,
         "scanners.biddingStrategy.options.upworkFreelancerProfileUid": 1,
         "scanners.biddingStrategy.options.template": 1,
         "scanners.deleted": 1,
        },
    ) or {}

    # Agency profile — gigradarTeamId is STRING in this collection.
    # Fetch RICH fields for the top-of-sheet profile comparison block:
    # portfolios, awards, skills, services (so we can show positioning, not
    # just the CL). See §2A Profile Comparison in the customer-audit skill.
    ap = agency_col.find_one({"gigradarTeamId": str(tid)}, {
        "metaProfile": 1,
        "agency.agencyProfile.photoUrl": 1,
        "portfolios": 1,            # list of portfolio items with thumbnails
        "awards": 1,                # industry recognitions
        "skills": 1,                # ontology-skill list (prefer preferredLabel)
        "services": 1,              # agency services with titles/descriptions
        "overview": 1,              # long-form positioning
        "description": 1,           # short-form positioning
        "companyInformation": 1,    # team size, founded year
        "upworkActivity": 1,        # hours worked, jobs completed
        "badges": 1,                # Top Rated / Expert Vetted badges
    }) or {}
    meta_profile = (ap.get("metaProfile") or {})
    agency_uid = meta_profile.get("upworkAgencyUid")
    agency_url = f"https://www.upwork.com/agencies/{agency_uid}/" if agency_uid else None

    # Pick up to TOP_N representative winning proposals by iterating the recorded jobs.
    # Tiered preference (higher = better evidence of actual win):
    #   T1 = hired outbound bid   (meta.status in [10, Hired], NOT an invite)
    #   T2 = replied outbound bid (dashroomUID non-empty, NOT an invite)
    # Invites are EXCLUDED from this analysis — this sheet compares *auto-bidding*
    # performance, so inbound-invite conversions are a different channel and would
    # pollute the apples-to-apples comparison. The is_invite flag is still preserved
    # downstream (for diagnostics / future ad-hoc queries) but invites never make it
    # into the competitor top-5 pool.
    TOP_N = 5
    proj = {
        "_id": 1,
        "meta.uid": 1, "meta.jobTitle": 1, "meta.createdAt": 1,
        "meta.status": 1, "meta.inviteToInterviewUid": 1,
        "meta.freelancer": 1, "meta.author": 1,
        "renderedCoverLetter": 1, "coverLetter": 1,
        "terms": 1,
        "auditDetails.modifiedTs": 1,
        "metaJob.ciphertext": 1,
    }
    ct_list = [job["ciphertext"] for job in rec["jobs"][:40]]
    tier_queries = [
        ("hired_bid",  {"meta.status": {"$in": [10, "Hired"]}, "meta.inviteToInterviewUid": None}),
        ("reply_bid",  {"dashroomUID": {"$exists": True, "$nin": [None, ""]}, "meta.inviteToInterviewUid": None}),
    ]
    collected = []      # list of (tier_name, prop)
    seen_ids = set()
    for tier_name, extra in tier_queries:
        if len(collected) >= TOP_N:
            break
        q = {"_gigradarTeamOid": tid, "metaJob.ciphertext": {"$in": ct_list}}
        q.update(extra)
        for p in db.proposals.find(q, proj).sort("meta.createdAt", -1).limit(TOP_N):
            if len(collected) >= TOP_N:
                break
            pid = str(p["_id"])
            if pid in seen_ids:
                continue
            if not (p.get("renderedCoverLetter") or p.get("coverLetter")):
                continue
            collected.append((tier_name, p))
            seen_ids.add(pid)

    rep_prop = collected[0][1] if collected else None
    rep_tier = collected[0][0] if collected else None
    rep_opp = None
    rep_job_src = None

    if rep_prop:
        rep_job_src = (rep_prop.get("metaJob") or {}).get("ciphertext")
        # Fetch the opp via proposalId join (for the primary proposal — used for
        # freelancer resolution + algo/template display)
        puid = (rep_prop.get("meta") or {}).get("uid")
        if puid:
            rep_opp = db.opportunities.find_one(
                {"gigradarTeamId": tid, "application.proposalId": str(puid)},
                {
                    "_id": 1,
                    "application.algorithmSignature": 1,
                    "application.algorithmVer": 1,
                    "application.originalStrategy.algorithmSignature": 1,
                    "application.originalStrategy.options.template": 1,
                    "application.upworkFreelancerUid": 1,
                    "application.upworkFreelancerProfileUid": 1,
                    "scannerId": 1, "scannerName": 1,
                    "application.matchPercentage": 1,
                },
            )

    # Freelancer profile: resolve via opp (preferred) or scanner fallback
    f_uid = None
    f_prof_uid = None
    scanner_algo_sig = None
    scanner_template = None
    if rep_opp:
        app = rep_opp.get("application") or {}
        f_uid = app.get("upworkFreelancerUid")
        f_prof_uid = app.get("upworkFreelancerProfileUid")

    # If not on opp, try to find from competitor team's scanners
    if not f_uid and t.get("scanners"):
        for s in t["scanners"]:
            if s.get("deleted"):
                continue
            strat_opts = (s.get("biddingStrategy") or {}).get("options") or {}
            if strat_opts.get("upworkFreelancerUid") and not f_uid:
                f_uid = strat_opts.get("upworkFreelancerUid")
                f_prof_uid = strat_opts.get("upworkFreelancerProfileUid")
                scanner_algo_sig = (s.get("biddingStrategy") or {}).get("algorithmSignature")
                scanner_template = strat_opts.get("template")
                break

    # Fetch freelancer profile — route through the shared helper so the REP
    # record has the same RICH shape (portrait_url, skills, employment_history,
    # portfolios, stats, contractor_tier, languages) as per-proposal freelancers.
    # Previously this was an inline partial copy of the helper that OMITTED
    # portrait_url — which is why avatar embedding broke for every competitor.
    fr_data, fr_url = _resolve_freelancer(contractor_col, f_uid, f_prof_uid)

    # Build competitor record
    cl = (rep_prop or {}).get("renderedCoverLetter") or (rep_prop or {}).get("coverLetter") or ""
    terms = (rep_prop or {}).get("terms") or {}
    charge = terms.get("chargeRate") or {}
    charge_amount = charge.get("amount")
    charge_currency = charge.get("currency")

    opp_app = (rep_opp or {}).get("application") or {}
    algo_sig = opp_app.get("algorithmSignature") or (opp_app.get("originalStrategy") or {}).get("algorithmSignature") or scanner_algo_sig
    # Strict: REP-level CL template pulled ONLY from originalStrategy.options.template.
    # The team's current scanner template is NOT a valid fallback — see phase2b comments.
    cl_template = ((opp_app.get("originalStrategy") or {}).get("options") or {}).get("template")

    # Pull JD from ES metajob
    jd_title = None
    jd_desc = None
    client_spent = None
    if rep_job_src:
        try:
            jd_doc = es_get(f"/metajob/_doc/{urlp.quote(rep_job_src, safe='')}")
            jd_src = (jd_doc or {}).get("_source") or {}
            mj = jd_src.get("metaJob") or {}
            jd_title = mj.get("title")
            jd_desc = (mj.get("description") or "")[:600]
            client_spent = ((mj.get("client") or {}).get("stats") or {}).get("totalSpent")
        except Exception:
            pass

    # ---- Enrich each of the TOP_N collected proposals (job + opp + template) ----
    winning_proposals_top5 = []
    for ptier, pp in collected:
        pp_cl = pp.get("renderedCoverLetter") or pp.get("coverLetter") or ""
        pp_terms = pp.get("terms") or {}
        pp_charge = pp_terms.get("chargeRate") or {}
        pp_ct = (pp.get("metaJob") or {}).get("ciphertext")

        # per-proposal JD + richer client info for symmetric card rendering.
        pp_jd_title = None
        pp_jd_desc = None
        pp_jd_category = None
        pp_jd_budget = None
        pp_client_spent = None
        pp_client_fb = None
        pp_client_hire_rate = None
        pp_client_country = None
        pp_client_company = None
        pp_client_payment_verified = None
        if pp_ct:
            try:
                jd2 = es_get(f"/metajob/_doc/{urlp.quote(pp_ct, safe='')}")
                jd2_src = (jd2 or {}).get("_source") or {}
                mj2 = jd2_src.get("metaJob") or {}
                pp_jd_title = mj2.get("title")
                pp_jd_desc = (mj2.get("description") or "")[:450]
                pp_jd_category = mj2.get("categoryName")
                pp_jd_budget = mj2.get("budget")
                mj2_client = mj2.get("client") or {}
                mj2_stats = mj2_client.get("stats") or {}
                pp_client_spent = mj2_stats.get("totalSpent")
                pp_client_fb = mj2_stats.get("feedbackScore")
                pp_client_hire_rate = mj2_stats.get("hireRate")
                pp_client_country = mj2_client.get("country") or (mj2_client.get("location") or {}).get("country")
                pp_client_company = ((mj2_client.get("company") or {}).get("name")) or (mj2_client.get("companyName"))
                pp_client_payment_verified = mj2_client.get("paymentVerificationStatus")
            except Exception:
                pass

        # per-proposal opp lookup — algo + template + freelancer can differ across bids
        # CRITICAL: CL template MUST come from opp.application.originalStrategy.options.template
        # (the strategy frozen AT THE TIME OF THE BID). NEVER fall back to the team's
        # CURRENT scanner.biddingStrategy.options.template — scanners get edited after
        # the fact, and the current scanner template will NOT match the rendered CL.
        pp_uid = (pp.get("meta") or {}).get("uid")
        pp_opp = None
        if pp_uid:
            pp_opp = db.opportunities.find_one(
                {"gigradarTeamId": tid, "application.proposalId": str(pp_uid)},
                {
                    "application.algorithmSignature": 1,
                    "application.originalStrategy.algorithmSignature": 1,
                    "application.originalStrategy.options.template": 1,
                    "application.upworkFreelancerUid": 1,
                    "application.upworkFreelancerProfileUid": 1,
                    "scannerId": 1, "scannerName": 1,
                    "application.matchPercentage": 1,
                },
            )
        pp_opp_app = (pp_opp or {}).get("application") or {}
        pp_algo_sig = pp_opp_app.get("algorithmSignature") or (pp_opp_app.get("originalStrategy") or {}).get("algorithmSignature")
        # Strict: ONLY from originalStrategy. No scanner-state fallback.
        pp_template = ((pp_opp_app.get("originalStrategy") or {}).get("options") or {}).get("template")

        # Per-proposal freelancer resolution — each bid can be under a different
        # freelancer identity (agencies rotate). Resolve via opp first, then fall
        # back to the proposal's meta.freelancer.name / meta.author.name for display.
        pp_f_uid = pp_opp_app.get("upworkFreelancerUid")
        pp_f_prof_uid = pp_opp_app.get("upworkFreelancerProfileUid")
        pp_fr_data = None
        pp_fr_url = None
        if pp_f_uid:
            # contractor_uid_cache: dict injected outside competitor loop for dedup
            if pp_f_uid in contractor_uid_cache:
                pp_fr_data, pp_fr_url = contractor_uid_cache[pp_f_uid]
            else:
                pp_fr_data, pp_fr_url = _resolve_freelancer(contractor_col, pp_f_uid, pp_f_prof_uid)
                contractor_uid_cache[pp_f_uid] = (pp_fr_data, pp_fr_url)
        # Proposal-side name fallback (no contractor profile lookup needed)
        pp_meta_fname = ((pp.get("meta") or {}).get("freelancer") or {}).get("name") or \
                        ((pp.get("meta") or {}).get("author") or {}).get("name")

        winning_proposals_top5.append({
            "proposal_id": str(pp["_id"]),
            "meta_uid": pp_uid,
            "selection_tier": ptier,
            "proposal_status": (pp.get("meta") or {}).get("status"),
            "is_invite": bool((pp.get("meta") or {}).get("inviteToInterviewUid")),
            "created_at": str((pp.get("meta") or {}).get("createdAt") or "")[:19],
            "hire_or_reply_date": str((pp.get("auditDetails") or {}).get("modifiedTs") or "")[:19],
            "terms_charge_amount": pp_charge.get("amount"),
            "terms_charge_currency": pp_charge.get("currency"),
            "terms_connectsBid": pp_terms.get("connectsBid"),
            "terms_duration": pp_terms.get("duration"),
            "full_rendered_cover_letter": pp_cl,
            "cl_length": len(pp_cl),
            "job": {
                "ciphertext": pp_ct,
                "url": f"https://www.upwork.com/jobs/{pp_ct}" if pp_ct else None,
                "title": pp_jd_title or (pp.get("meta") or {}).get("jobTitle"),
                "description_excerpt": pp_jd_desc,
                "category": pp_jd_category,
                "budget": pp_jd_budget,
                # Client card — same shape as UB side for symmetric render.
                "client_company": pp_client_company,
                "client_country": pp_client_country,
                "client_total_spent": pp_client_spent,
                "client_feedback_score": pp_client_fb,
                "client_hire_rate": pp_client_hire_rate,
                "client_payment_verified": pp_client_payment_verified,
            },
            # Per-proposal freelancer (each bid under an agency can be under a
            # different identity — rendering should show THIS freelancer, not the
            # agency-level one).
            "freelancer": {
                "name": (pp_fr_data or {}).get("general_name") if pp_fr_data else pp_meta_fname,
                "title": (pp_fr_data or {}).get("general_title") if pp_fr_data else None,
                "description": (pp_fr_data or {}).get("general_description") if pp_fr_data else None,
                "hourly_rate": (pp_fr_data or {}).get("general_hourly_rate") if pp_fr_data else None,
                "location": (pp_fr_data or {}).get("general_location") if pp_fr_data else None,
                "upwork_freelancer_uid": pp_f_uid,
                "url": pp_fr_url,
                "specialized": (pp_fr_data or {}).get("specialized") if pp_fr_data else None,
            } if (pp_fr_data or pp_meta_fname) else None,
            "gigradar_bid": {
                "is_gigradar_bid": bool(pp_opp),
                "algorithm_signature_raw": pp_algo_sig,
                "algorithm_name": decode_algo(pp_algo_sig) if pp_algo_sig else None,
                "match_percentage": pp_opp_app.get("matchPercentage"),
                # CL template comes STRICTLY from opp.application.originalStrategy.options.template.
                # If missing, that means no frozen strategy was captured (or this is a manual bid).
                # We explicitly surface that state instead of falling back to the current scanner
                # template, which drifts from the bid-time template after scanner edits.
                "cl_template_used": pp_template,
                "cl_template_source": "opp.application.originalStrategy.options.template" if pp_template else None,
                "cl_template_length": len(pp_template) if isinstance(pp_template, str) else None,
                "scanner_id": str((pp_opp or {}).get("scannerId")) if (pp_opp and (pp_opp or {}).get("scannerId")) else None,
                "scanner_name": (pp_opp or {}).get("scannerName"),
            } if pp_opp else {"is_gigradar_bid": False},
        })

    # Focus-window performance metrics — the REAL indicator of whether this
    # competitor's strategy is working. Used to rank them at render time and
    # to weight their AI tactics in the exec summary.
    _tm = team_metrics_by_id.get(tid_str) or {}

    competitors.append({
        "team_id": tid_str,
        "team_name": t.get("name"),
        "team_serviceNames": t.get("serviceNames"),
        "wins_in_neighborhoods": rec["count"],
        "focus_metrics": {
            "sent": _tm.get("sent"),
            "replied": _tm.get("replied"),
            "hired": _tm.get("hired"),
            "reply_rate": _tm.get("reply_rate"),
            "hire_rate": _tm.get("hire_rate"),
            "cost_usd": _tm.get("cost_usd"),
            "cost_per_reply": _tm.get("cost_per_reply"),
            "cost_per_hire": _tm.get("cost_per_hire"),
        } if _tm else None,
        "agency": {
            "url": agency_url,
            "upworkAgencyUid": agency_uid,
            "name": meta_profile.get("name"),
            "slug": meta_profile.get("slug"),
            "ciphertext": meta_profile.get("ciphertext"),
            "summary_or_description": (meta_profile.get("summary") or meta_profile.get("description") or "")[:1500],
            "stats": meta_profile.get("stats"),
            "badge": meta_profile.get("badge"),
            "locations": meta_profile.get("locations"),
            "photoUrl": meta_profile.get("photoUrl"),
            # Rich fields for sheet-top profile block:
            "portfolios": [
                {"title": (p or {}).get("title"),
                 "description": ((p or {}).get("description") or "")[:300],
                 "projectUrl": (p or {}).get("projectUrl")}
                for p in (ap.get("portfolios") or [])[:8]
            ],
            "awards": [
                {"title": (a or {}).get("title") or (a or {}).get("name"),
                 "description": ((a or {}).get("description") or "")[:200]}
                for a in (ap.get("awards") or [])[:5]
            ],
            "skills": [
                ((s or {}).get("preferredLabel") or (s or {}).get("name"))
                for s in (ap.get("skills") or [])[:20]
                if s
            ],
            "services": [
                {"name": (s or {}).get("serviceName"),
                 "title": (s or {}).get("title"),
                 "description": ((s or {}).get("description") or "")[:400]}
                for s in (ap.get("services") or [])[:6]
                if s
            ],
            "overview": (ap.get("overview") or "")[:1500] if ap.get("overview") else None,
        } if meta_profile else None,
        "freelancer": {
            "url": fr_url,
            "data": fr_data,
        } if f_uid else None,
        "winning_job": {
            "ciphertext": rep_job_src,
            "title": jd_title or ((rep_prop or {}).get("meta") or {}).get("jobTitle"),
            "description_excerpt": jd_desc,
            "client_total_spent": client_spent,
        } if rep_prop else None,
        "winning_proposal": {
            "proposal_id": str(rep_prop["_id"]) if rep_prop else None,
            "meta_uid": (rep_prop.get("meta") or {}).get("uid") if rep_prop else None,
            "proposal_status": (rep_prop.get("meta") or {}).get("status") if rep_prop else None,
            "is_invite": bool((rep_prop.get("meta") or {}).get("inviteToInterviewUid")) if rep_prop else None,
            "selection_tier": rep_tier,
            "created_at": str(((rep_prop or {}).get("meta") or {}).get("createdAt") or "")[:19],
            "hire_or_reply_date": str(((rep_prop or {}).get("auditDetails") or {}).get("modifiedTs") or "")[:19],
            "terms_charge_amount": charge_amount,
            "terms_charge_currency": charge_currency,
            "terms_duration": terms.get("duration"),
            "terms_connectsBid": terms.get("connectsBid"),
            "full_rendered_cover_letter": cl,
            "cl_length": len(cl),
        } if rep_prop else None,
        "winning_proposals_top5": winning_proposals_top5,
        "gigradar_bid": {
            "is_gigradar_bid": bool(rep_opp),
            "algorithm_signature_raw": algo_sig,
            "algorithm_name": decode_algo(algo_sig) if algo_sig else None,
            "match_percentage": opp_app.get("matchPercentage"),
            "cl_template_used": cl_template,
            "cl_template_length": len(cl_template) if isinstance(cl_template, str) else None,
            "opp_id": str(rep_opp["_id"]) if rep_opp else None,
            "scanner_id": str((rep_opp or {}).get("scannerId")) if (rep_opp and (rep_opp or {}).get("scannerId")) else None,
            "scanner_name": (rep_opp or {}).get("scannerName"),
        } if rep_opp or scanner_algo_sig else {"is_gigradar_bid": False},
    })
    print(f"  team={tid_str[:12]}  wins={rec['count']:2d}  name={(t.get('name') or '?')[:35]!r}  "
          f"top5={len(winning_proposals_top5)}  algo={decode_algo(algo_sig) if algo_sig else '—':18s}  "
          f"agency_url={'yes' if agency_url else 'no'}  freelancer_url={'yes' if fr_url else 'no'}")


# ============================================================================
# 5) Save output
# ============================================================================
summary = {
    "window": {
        "embed_coverage_start": EMBED_COVERAGE_START.isoformat(),
        "focus_start": FOCUS_START.isoformat(),
        "focus_end": FOCUS_END.isoformat(),
    },
    "subject_team_id": str(TEAM_OID),
    "seeds": {
        "n_seed_ciphertexts": len(seed_set),
        "seed_reports_sample": seed_reports[:10],
    },
    "expansion": {
        "neighbor_ciphertext_count": len(neighbor_set),
        "cohort_team_count": len(cohort_teams),
        "winner_registry_team_count": len(winner_registry),
    },
    "knn_cohort_summary": knn_cohort_sum,
    "competitors_top10": competitors,
    "algo_decoder": ALGO_DECODER,
    # Subject team profile snapshot — used by build_workbook.py to render the
    # Ubiquify side of the freelancer-profile comparison block at the top of
    # Sheet 3. Same rich-field shape as each competitor's agency + freelancer.
    "subject_profile": {
        "team_id": str(TEAM_OID),
        "team_name": subj_team.get("name"),
        "agency": {
            "url": f"https://www.upwork.com/agencies/{(subj_agency.get('metaProfile') or {}).get('upworkAgencyUid')}/" if (subj_agency.get('metaProfile') or {}).get('upworkAgencyUid') else None,
            "name": (subj_agency.get('metaProfile') or {}).get("name"),
            "summary_or_description": ((subj_agency.get('metaProfile') or {}).get("summary") or (subj_agency.get('metaProfile') or {}).get("description") or "")[:1500],
            "stats": (subj_agency.get('metaProfile') or {}).get("stats"),
            "locations": (subj_agency.get('metaProfile') or {}).get("locations"),
            "photoUrl": (subj_agency.get('metaProfile') or {}).get("photoUrl"),
            "portfolios": [
                {"title": (p or {}).get("title"),
                 "description": ((p or {}).get("description") or "")[:300],
                 "projectUrl": (p or {}).get("projectUrl")}
                for p in (subj_agency.get("portfolios") or [])[:8]
            ],
            "awards": [
                {"title": (a or {}).get("title") or (a or {}).get("name"),
                 "description": ((a or {}).get("description") or "")[:200]}
                for a in (subj_agency.get("awards") or [])[:5]
            ],
            "skills": [
                ((s or {}).get("preferredLabel") or (s or {}).get("name"))
                for s in (subj_agency.get("skills") or [])[:20]
                if s
            ],
            "services": [
                {"name": (s or {}).get("serviceName"),
                 "title": (s or {}).get("title"),
                 "description": ((s or {}).get("description") or "")[:400]}
                for s in (subj_agency.get("services") or [])[:6]
                if s
            ],
            "overview": (subj_agency.get("overview") or "")[:1500] if subj_agency.get("overview") else None,
        },
        "main_freelancer": {
            "url": subj_fl_url,
            "data": subj_fl_data,
        } if subj_fl_data else None,
    },
}

with open(OUT, "w") as f:
    json.dump(summary, f, indent=2, default=str)

print()
print(f"Wrote {OUT}")
