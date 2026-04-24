"""
Phase 2A v2 — Cohort compare with inferred peer cohort.

Ubiquify has empty serviceNames, so we fall back to scanner-keyword inference:
- pull subject team's active (non-deleted) scanner queries
- extract salient keywords
- find peer teams whose scanners match ≥1 of those keywords
- qualify teams with ≥100 sent in focus window (outbound bids only, inviteToInterviewUid null)
- rank Ubiquify on reply rate / $/reply / hire rate

Plus: a second "broad" cohort = all platform teams ≥100 sent in focus window
(gives a floor comparison against any bidding team regardless of vertical).
"""
import json
import os
import re
from bson import ObjectId
from pymongo import MongoClient
from datetime import datetime, timezone
import statistics as st

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2a_cohort_v2.json"

FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)

MIN_SENT = 100
CONN_PRICE = 0.15

c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]


# ============================================================================
# 1) Extract subject team's scanner keywords
# ============================================================================
subj = db.teams.find_one({"_id": TEAM_OID}, {"scanners": 1, "name": 1})
print(f"Subject: {subj.get('name')}")
scanners = subj.get("scanners") or []
# Keep only non-deleted/non-disabled scanners
active = [s for s in scanners if not s.get("deleted") and not s.get("disabled") and not s.get("archived")]
print(f"  total scanners: {len(scanners)}, active: {len(active)}")

# Build a curated keyword list from the scanner queries
# Rather than parse every boolean expression, pick out domain nouns that are
# meaningful category anchors
PRIORITY_KEYWORDS = [
    # Stack / language
    "full stack", "full-stack", "fullstack",
    "react", "node.js", "nodejs", "next.js", "nextjs",
    "python", "django", "flask", "fastapi",
    "mern", "mean", "mevn",
    "typescript", "javascript",
    # AI / automation
    "ai automation", "llm", "openai", "chatgpt", "gpt-4",
    "n8n", "zapier", "make.com",
    "langchain", "rag", "vector",
    # SaaS / product
    "saas", "mvp", "startup",
    # Ecom / headless
    "shopify", "headless",
]
# For the inference match, we'll do case-insensitive substring matching on scanner.query.q
kw_regex = "|".join([re.escape(k) for k in PRIORITY_KEYWORDS])
print(f"  priority keywords for inference: {len(PRIORITY_KEYWORDS)}")


# ============================================================================
# 2) Find peer teams with scanner queries overlapping any priority keyword
# ============================================================================
peer_q = {
    "_id": {"$ne": TEAM_OID},
    "scanners": {"$elemMatch": {"query.q": {"$regex": kw_regex, "$options": "i"}}},
}
peer_ids = set()
for t in db.teams.find(peer_q, {"_id": 1}):
    peer_ids.add(t["_id"])
print(f"  keyword-matched peer teams: {len(peer_ids)}")


# ============================================================================
# 3) Aggregate reply/hire stats per team in focus window for qualified teams
# ============================================================================
def aggregate_for_team_set(team_ids, label):
    pipeline = [
        {"$match": {
            "_gigradarTeamOid": {"$in": list(team_ids)},
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
    rows = list(db.proposals.aggregate(pipeline, allowDiskUse=True))
    print(f"  [{label}] teams with ≥1 bid in focus: {len(rows)}")
    qualified = []
    for r in rows:
        if r["sent"] < MIN_SENT:
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
    print(f"  [{label}] qualified (≥{MIN_SENT} sent): {len(qualified)}")
    return qualified


# Inferred peer cohort
print("\n[INFERRED PEER COHORT (scanner-keyword match)]")
inferred_ids = peer_ids | {TEAM_OID}
inferred = aggregate_for_team_set(inferred_ids, "inferred")

# Geo cohort — peer teams whose primary agency location matches the subject team's
# primary agency location. Anchors competitor set on the same country so price
# expectations, work-hour overlap, and buyer-side language expectations are
# comparable. See §2A cohort rationale in SKILL.md.
print("\n[GEO COHORT (teams in same primary country as subject agency)]")
subj_ap = db["upwork.agency.profiles"].find_one(
    {"gigradarTeamId": str(TEAM_OID)},
    {"metaProfile.locations": 1},
) or {}
subj_locs = ((subj_ap.get("metaProfile") or {}).get("locations") or [])
subj_country = (subj_locs[0] or {}).get("country") if subj_locs else None
print(f"  subject primary agency country: {subj_country}")

geo_ids = set()
if subj_country:
    # Find agency profiles whose PRIMARY location (locations[0]) matches subject's
    # country, then map back to their gigradarTeamId (stored as string).
    geo_cursor = db["upwork.agency.profiles"].find(
        {"metaProfile.locations.0.country": subj_country},
        {"gigradarTeamId": 1},
    )
    for ap_row in geo_cursor:
        tid_str = ap_row.get("gigradarTeamId")
        if not tid_str:
            continue
        try:
            geo_ids.add(ObjectId(tid_str))
        except Exception:
            pass
    # Always include subject so its percentile rank lands on the cohort table.
    geo_ids.add(TEAM_OID)
    print(f"  geo-matched agency teams (agency profile primary country = {subj_country}): {len(geo_ids)}")
geo = aggregate_for_team_set(geo_ids, "geo") if geo_ids else []

# Broad platform cohort — ALL teams ≥100 sent in focus
print("\n[BROAD PLATFORM COHORT (any team with ≥100 sent)]")
# Step 1 — find all team_oids with ≥1 bid in focus (sample, then filter by MIN_SENT)
all_team_ids_with_bids = set()
for r in db.proposals.aggregate([
    {"$match": {
        "meta.createdAt": {"$gte": FOCUS_START, "$lte": FOCUS_END},
        "meta.inviteToInterviewUid": None,
    }},
    {"$group": {"_id": "$_gigradarTeamOid"}},
]):
    all_team_ids_with_bids.add(r["_id"])
print(f"  total teams with ≥1 bid in focus (platform-wide): {len(all_team_ids_with_bids)}")
broad = aggregate_for_team_set(all_team_ids_with_bids, "broad")


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
            "min": min(rrs) if rrs else None,
            "p25": pct(rrs, 0.25),
            "median": st.median(rrs) if rrs else None,
            "p75": pct(rrs, 0.75),
            "p90": pct(rrs, 0.90),
            "max": max(rrs) if rrs else None,
        },
        "cost_per_reply_stats": {
            "n": len(cprs),
            "min": min(cprs) if cprs else None,
            "p25": pct(cprs, 0.25),
            "median": st.median(cprs) if cprs else None,
            "p75": pct(cprs, 0.75),
            "p90": pct(cprs, 0.90),
            "max": max(cprs) if cprs else None,
        },
        "hire_rate_among_hiring_stats": {
            "n_teams_with_hire": len(hrs),
            "min": min(hrs) if hrs else None,
            "p25": pct(hrs, 0.25),
            "median": st.median(hrs) if hrs else None,
            "p75": pct(hrs, 0.75),
            "p90": pct(hrs, 0.90),
            "max": max(hrs) if hrs else None,
        },
    }
    if subj_row:
        out["subject_position"] = {
            "team_id": subj_row["team_id"],
            "sent": subj_row["sent"],
            "replied": subj_row["replied"],
            "hired": subj_row["hired"],
            "cost_usd": subj_row["cost_usd"],
            "reply_rate": subj_row["reply_rate"],
            "reply_rate_percentile": rank_among(rrs, subj_row["reply_rate"], higher_is_better=True),
            "cost_per_reply": subj_row["cost_per_reply"],
            "cost_per_reply_percentile_lower_better": rank_among(cprs, subj_row["cost_per_reply"], higher_is_better=False) if subj_row["cost_per_reply"] is not None else None,
            "hire_rate": subj_row["hire_rate"],
            "hire_rate_percentile_among_hiring": rank_among(hrs, subj_row["hire_rate"], higher_is_better=True) if subj_row["hired"] >= 1 else None,
        }
    else:
        out["subject_position"] = {"note": "subject not qualifying"}
    out["top_by_reply_rate"] = sorted(qualified, key=lambda q: -(q["reply_rate"] or 0))[:15]
    out["top_by_cost_per_reply"] = sorted([q for q in qualified if q["cost_per_reply"] is not None], key=lambda q: q["cost_per_reply"])[:15]
    return out


inferred_sum = cohort_summary(inferred, "inferred-scanner-keyword-peers")
broad_sum = cohort_summary(broad, "broad-platform-≥100-sent")


summary = {
    "window": {"start": FOCUS_START.isoformat(), "end": FOCUS_END.isoformat()},
    "subject": {"team_id": str(TEAM_OID), "name": subj.get("name")},
    "subject_serviceNames": subj.get("serviceNames"),
    "subject_scanner_count": {"total": len(scanners), "active": len(active)},
    "inference": {
        "method": "scanner.query.q substring match on priority keyword list (case-insensitive)",
        "keywords": PRIORITY_KEYWORDS,
        "matched_peer_teams": len(peer_ids),
    },
    "inferred_cohort": inferred_sum,
    "broad_cohort": broad_sum,
    "geo_cohort": cohort_summary(geo, "geo") if geo else None,
    "geo_cohort_country": subj_country,
}

with open(OUT, "w") as f:
    json.dump(summary, f, indent=2, default=str)


def pct_fmt(v):
    return f"{v*100:.2f}%" if v is not None else "N/A"


def usd_fmt(v):
    return f"${v:.2f}" if v is not None else "N/A"


print()
print("=" * 70)
print("INFERRED COHORT — scanner-keyword-matched peers")
print("=" * 70)
rrs = inferred_sum["reply_rate_stats"]
cprs = inferred_sum["cost_per_reply_stats"]
print(f"Qualified teams: {inferred_sum['qualified_count']}")
print(f"Reply rate:  min={pct_fmt(rrs['min'])}  p25={pct_fmt(rrs['p25'])}  med={pct_fmt(rrs['median'])}  p75={pct_fmt(rrs['p75'])}  p90={pct_fmt(rrs['p90'])}  max={pct_fmt(rrs['max'])}")
print(f"$/reply:     min={usd_fmt(cprs['min'])}  p25={usd_fmt(cprs['p25'])}  med={usd_fmt(cprs['median'])}  p75={usd_fmt(cprs['p75'])}  p90={usd_fmt(cprs['p90'])}  max={usd_fmt(cprs['max'])}")
sp = inferred_sum["subject_position"]
if "reply_rate" in sp:
    print(f"Subject: sent={sp['sent']}  rr={pct_fmt(sp['reply_rate'])}  $/reply={usd_fmt(sp['cost_per_reply'])}  hr={pct_fmt(sp['hire_rate'])}")
    print(f"  P-rank RR:          {sp['reply_rate_percentile']:.1f} (higher better)")
    p_cpr = sp['cost_per_reply_percentile_lower_better']
    cpr_str = f"{p_cpr:.1f}" if p_cpr is not None else "N/A"
    print(f"  P-rank $/reply:     {cpr_str} (higher = cheaper than more peers)")
    if sp.get("hire_rate_percentile_among_hiring") is not None:
        print(f"  P-rank hire rate:   {sp['hire_rate_percentile_among_hiring']:.1f} among N={inferred_sum['hire_rate_among_hiring_stats']['n_teams_with_hire']} hiring peers")

print()
print("=" * 70)
print("BROAD PLATFORM COHORT — all teams ≥100 sent in focus")
print("=" * 70)
rrs = broad_sum["reply_rate_stats"]
cprs = broad_sum["cost_per_reply_stats"]
print(f"Qualified teams: {broad_sum['qualified_count']}")
print(f"Reply rate:  min={pct_fmt(rrs['min'])}  p25={pct_fmt(rrs['p25'])}  med={pct_fmt(rrs['median'])}  p75={pct_fmt(rrs['p75'])}  p90={pct_fmt(rrs['p90'])}  max={pct_fmt(rrs['max'])}")
print(f"$/reply:     min={usd_fmt(cprs['min'])}  p25={usd_fmt(cprs['p25'])}  med={usd_fmt(cprs['median'])}  p75={usd_fmt(cprs['p75'])}  p90={usd_fmt(cprs['p90'])}  max={usd_fmt(cprs['max'])}")
sp = broad_sum["subject_position"]
if "reply_rate" in sp:
    print(f"Subject: sent={sp['sent']}  rr={pct_fmt(sp['reply_rate'])}  $/reply={usd_fmt(sp['cost_per_reply'])}  hr={pct_fmt(sp['hire_rate'])}")
    print(f"  P-rank RR:          {sp['reply_rate_percentile']:.1f}")
    p_cpr = sp['cost_per_reply_percentile_lower_better']
    if p_cpr is not None:
        print(f"  P-rank $/reply:     {p_cpr:.1f}")
    if sp.get("hire_rate_percentile_among_hiring") is not None:
        print(f"  P-rank hire rate:   {sp['hire_rate_percentile_among_hiring']:.1f} among N={broad_sum['hire_rate_among_hiring_stats']['n_teams_with_hire']} hiring peers")

print()
print(f"Wrote {OUT}")
