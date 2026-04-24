import os
"""
Phase 5 — Auto-bidding aggregates.

Slice on opportunities.scannerId × opportunities.originalGigTempId × application.algorithmSignature.
Report: sent, replies, reply_rate, connects_spent, cost_per_reply, hires, hire_rate (diagnostic).
Include focus vs prior window.
"""
import json
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from bson import ObjectId

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase5_aggregates.json"

FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)
PRIOR_START = datetime(2026, 2, 21, tzinfo=timezone.utc)
PRIOR_END = datetime(2026, 3, 23, tzinfo=timezone.utc)

CONN_PRICE = 0.15

c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]

def agg_window(win_start, win_end, label):
    pipeline = [
        {"$match": {
            "gigradarTeamId": TEAM_OID,
            "notified": {"$gte": win_start, "$lt": win_end},
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
                    "_id": 0,
                    "dashroomUID": 1, "meta.status": 1, "meta.chat.chatId": 1,
                    "meta.connectsExpended": 1, "connectsExpended": 1,
                    "terms.connectsBid": 1,
                }},
            ],
            "as": "proposal",
        }},
        {"$unwind": {"path": "$proposal", "preserveNullAndEmptyArrays": False}},
        {"$group": {
            "_id": {
                "scannerId": "$scannerId",
                "scannerName": "$scannerName",
                "algo": "$application.algorithmSignature",
                "promptVer": "$application.promptVersion",
            },
            "sent": {"$sum": 1},
            "replies": {"$sum": {"$cond": [
                {"$and": [
                    {"$ne": ["$proposal.dashroomUID", None]},
                    {"$ne": ["$proposal.dashroomUID", ""]},
                ]}, 1, 0]}},
            "hires": {"$sum": {"$cond": [
                {"$in": ["$proposal.meta.status", [10, "Hired"]]},
                1, 0]}},
            "connects": {"$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$proposal.terms.connectsBid", 0]}, 0]},
                    "$proposal.terms.connectsBid",
                    {"$ifNull": ["$proposal.meta.connectsExpended",
                                 {"$ifNull": ["$proposal.connectsExpended", 0]}]}
                ]
            }},
        }},
    ]
    rows = list(db.opportunities.aggregate(pipeline, allowDiskUse=True))
    print(f"  [{label}] raw slices: {len(rows)}")

    out = []
    for r in rows:
        k = r["_id"]
        sent = r["sent"]
        replies = r["replies"]
        hires = r["hires"]
        connects = r["connects"]
        cost = connects * CONN_PRICE
        out.append({
            "scanner_id": str(k.get("scannerId") or ""),
            "scanner_name": k.get("scannerName"),
            "algorithm_signature": k.get("algo"),
            "prompt_version": k.get("promptVer"),
            "sent": sent,
            "replies": replies,
            "hires": hires,
            "connects": connects,
            "cost_usd": cost,
            "reply_rate": replies / sent if sent else None,
            "hire_rate": hires / sent if sent else None,
            "cost_per_reply_usd": cost / replies if replies else None,
            "cost_per_hire_usd": cost / hires if hires else None,
        })
    return sorted(out, key=lambda x: -x["sent"])


print(f"FOCUS window {FOCUS_START.date()}..{FOCUS_END.date()}")
focus = agg_window(FOCUS_START, FOCUS_END, "FOCUS")

print(f"PRIOR window {PRIOR_START.date()}..{PRIOR_END.date()}")
prior = agg_window(PRIOR_START, PRIOR_END, "PRIOR")


# Build per-scanner rollup (sum template × algo under scanner)
def rollup_by(rows, key):
    agg = {}
    for r in rows:
        k = r[key]
        if k not in agg:
            agg[k] = {"key": k, "name": r.get("scanner_name"), "sent": 0, "replies": 0, "hires": 0, "connects": 0}
        agg[k]["sent"] += r["sent"]
        agg[k]["replies"] += r["replies"]
        agg[k]["hires"] += r["hires"]
        agg[k]["connects"] += r["connects"]
    for v in agg.values():
        v["reply_rate"] = v["replies"] / v["sent"] if v["sent"] else None
        v["hire_rate"] = v["hires"] / v["sent"] if v["sent"] else None
        v["cost_usd"] = v["connects"] * CONN_PRICE
        v["cost_per_reply_usd"] = v["cost_usd"] / v["replies"] if v["replies"] else None
    return sorted(agg.values(), key=lambda x: -x["sent"])


# Attach prior deltas to focus rows
prior_index = {(r["scanner_id"], r["algorithm_signature"], r["prompt_version"]): r for r in prior}
for f in focus:
    key = (f["scanner_id"], f["algorithm_signature"], f["prompt_version"])
    p = prior_index.get(key)
    if p:
        f["prior_sent"] = p["sent"]
        f["prior_reply_rate"] = p["reply_rate"]
        f["prior_cost_per_reply_usd"] = p["cost_per_reply_usd"]
        f["delta_reply_rate"] = (f["reply_rate"] or 0) - (p["reply_rate"] or 0)
    else:
        f["prior_sent"] = 0
        f["prior_reply_rate"] = None
        f["prior_cost_per_reply_usd"] = None
        f["delta_reply_rate"] = None


summary = {
    "focus_window": {"start": FOCUS_START.isoformat(), "end": FOCUS_END.isoformat()},
    "prior_window": {"start": PRIOR_START.isoformat(), "end": PRIOR_END.isoformat()},
    "slice_count_focus": len(focus),
    "slice_count_prior": len(prior),
    "focus_rows": focus,
    "prior_rows": prior,
    "rollup_by_scanner_focus": rollup_by(focus, "scanner_id"),
    "rollup_by_scanner_prior": rollup_by(prior, "scanner_id"),
}

with open(OUT, "w") as f:
    json.dump(summary, f, indent=2, default=str)

# Quick display — top focus slices
print()
print("TOP SLICES IN FOCUS WINDOW (by sent):")
print(f"  {'scanner':20s}{'sent':>5s}  {'rr':>7s}  {'$/reply':>8s}  {'prior_rr':>9s}  {'Δrr':>7s}")
for r in focus[:15]:
    sc = (r.get("scanner_name") or r["scanner_id"][:8])[:20]
    rr = r.get("reply_rate")
    cpr = r.get("cost_per_reply_usd")
    pr = r.get("prior_reply_rate")
    d = r.get("delta_reply_rate")
    print(f"  {sc:20s}{r['sent']:5d}  {(rr*100 if rr else 0):6.2f}%  {('$'+str(round(cpr,2))) if cpr else 'N/A':>8s}  {(pr*100 if pr else 0):8.2f}%  {(d*100 if d else 0):+6.2f}pp")
print()
print(f"Wrote {OUT}")
