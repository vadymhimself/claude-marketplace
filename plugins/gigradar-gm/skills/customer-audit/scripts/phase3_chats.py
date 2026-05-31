import os
"""
Phase 3 — Chat transcripts.

For each HIRED or strong-reply proposal, pull leads.chats + leads.chats.messages
and extract first 5-10 messages per thread. Print high-signal exchanges.

Probe first: if leads.chats is empty for Ubiquify, skip gracefully.
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
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone

MONGO_URI = os.environ["MONGO_URI"]  # request read-only creds from admin; see plugin README
TEAM_OID = ObjectId(os.environ.get("TEAM_OID") or "679a215568faa05722aabb93")
OUT_DIR = os.environ.get("AUDIT_WORK_DIR") or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(OUT_DIR, "phase3_chats.json")

FOCUS_START = _env_date("FOCUS_START", _FOCUS_START_DEFAULT)
FOCUS_END = _env_date("FOCUS_END", _FOCUS_END_DEFAULT)

c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=60000)
db = c["gigradar-dev"]

# Playbook warns: probe leads.chats coverage first.
# Some teams store gigradarTeamId as string on leads.chats; others as ObjectId.
# Probe both shapes with find_one (cheap), then fall back to count_documents.
leads_db = c["gigradar-dev"]  # same DB; adjust if leads is separate
probe_coverage = 0
team_filter_for_chats = None
try:
    if db.get_collection("leads.chats").find_one({"gigradarTeamId": TEAM_OID}, {"_id": 1}):
        team_filter_for_chats = {"gigradarTeamId": TEAM_OID}
        probe_coverage = db.get_collection("leads.chats").count_documents(team_filter_for_chats, limit=200)
    elif db.get_collection("leads.chats").find_one({"gigradarTeamId": str(TEAM_OID)}, {"_id": 1}):
        team_filter_for_chats = {"gigradarTeamId": str(TEAM_OID)}
        probe_coverage = db.get_collection("leads.chats").count_documents(team_filter_for_chats, limit=200)
except Exception as _e:
    print(f"  probe error: {_e}")
print(f"leads.chats coverage for this team (probe): {probe_coverage} (shape: {'ObjectId' if team_filter_for_chats and isinstance(team_filter_for_chats.get('gigradarTeamId'), ObjectId) else 'string' if team_filter_for_chats else 'none'})")

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
    print(f"leads.chats is empty for this team — chat-transcript reading SKIPPED.")
    with open(OUT, "w") as f:
        json.dump({
            "status": "skipped",
            "reason": "leads.chats not populated for this team — chat-sync may be off or this team predates sync",
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
    # NOTE: leads.chats.messages.gigradarTeamId is unset on many teams; do NOT filter by team here.
    # The upworkRoomUid alone is sufficient since the room has the team scoping.
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
