import os
"""
Phase 3 — Chat transcripts.

For each HIRED or strong-reply proposal, pull leads.chats + leads.chats.messages
and extract first 5-10 messages per thread. Print high-signal exchanges.

Probe first: if leads.chats is empty for Ubiquify, skip gracefully.
"""
import json
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
TEAM_OID = ObjectId("679a215568faa05722aabb93")
OUT = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase3_chats.json"

FOCUS_START = datetime(2026, 3, 23, tzinfo=timezone.utc)
FOCUS_END = datetime(2026, 4, 22, tzinfo=timezone.utc)

c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]

# Playbook warns: probe leads.chats coverage first
leads_db = c["gigradar-dev"]  # same DB in practice; adjust if leads is separate
# The actual collection may be in a different DB; try both
probe_coverage = 0
try:
    probe_coverage = db.get_collection("leads.chats").count_documents({"gigradarTeamId": TEAM_OID}, limit=10)
except Exception:
    pass
print(f"leads.chats coverage for Ubiquify (probe): {probe_coverage}")

# Also try the dedicated leads DB
leads_db_alt = c["leads"] if "leads" in c.list_database_names() else None
if leads_db_alt is not None:
    alt = leads_db_alt.chats.count_documents({"gigradarTeamId": TEAM_OID}, limit=10)
    print(f"leads.chats (leads DB) coverage: {alt}")
else:
    alt = 0

# Sanity-check: how many proposals have a chat.chatId?
cc = db.proposals.count_documents({"_gigradarTeamOid": TEAM_OID, "meta.chat.chatId": {"$exists": True, "$nin": [None, ""]}})
print(f"Proposals with chat.chatId: {cc}")

if probe_coverage == 0 and alt == 0:
    print("leads.chats is empty for this team — chat-transcript reading SKIPPED.")
    with open(OUT, "w") as f:
        json.dump({
            "status": "skipped",
            "reason": "leads.chats dry for Ubiquify — chat-sync not populated",
            "proposals_with_chatId": cc,
            "caveat": "CL-to-interview conversion can't be diagnosed from transcripts; rely on reply-rate only",
        }, f, indent=2, default=str)
    raise SystemExit(0)

# If populated — walk and harvest
# (fallback code path; not exercised on Ubiquify)

# Pick HIRED + top replies in focus window
hits = list(db.proposals.find(
    {
        "_gigradarTeamOid": TEAM_OID,
        "meta.inviteToInterviewUid": None,
        "$or": [
            {"meta.status": {"$in": [10, "Hired", 9, 7, "ACTIVE"]}},
            {"meta.chat.chatId": {"$exists": True, "$nin": [None, ""]}},
        ],
        "meta.createdAt": {"$gte": FOCUS_START, "$lte": FOCUS_END},
    },
    {"meta.chat.chatId": 1, "meta.jobTitle": 1, "meta.status": 1, "meta.createdAt": 1}
).sort("meta.createdAt", -1).limit(10))

chats_db = leads_db_alt or db
chats_coll = chats_db.get_collection("chats") if leads_db_alt else chats_db.get_collection("leads.chats")
msgs_coll = chats_db.get_collection("chats.messages") if leads_db_alt else chats_db.get_collection("leads.chats.messages")

out_threads = []
for p in hits:
    chatId = (p.get("meta") or {}).get("chat", {}).get("chatId")
    if not chatId:
        continue
    room = chats_coll.find_one({"upworkRoomUid": chatId}, {"upworkRoomUid": 1, "jobDetails": 1, "startedAt": 1})
    if not room:
        continue
    msgs = list(msgs_coll.find({"upworkRoomUid": chatId}, {
        "text": 1, "author.type": 1, "author.name": 1, "createdAt": 1, "type": 1
    }).sort("createdAt", 1).limit(12))
    thread = {
        "proposal_id": str(p["_id"]),
        "job_title": (p.get("meta") or {}).get("jobTitle"),
        "status": (p.get("meta") or {}).get("status"),
        "messages": [{
            "author": (m.get("author") or {}).get("type"),
            "name": (m.get("author") or {}).get("name"),
            "text": (m.get("text") or "")[:600],
            "ts": str(m.get("createdAt")),
        } for m in msgs],
    }
    out_threads.append(thread)

with open(OUT, "w") as f:
    json.dump({"status": "populated", "threads": out_threads}, f, indent=2, default=str)
print(f"Wrote {OUT}")
