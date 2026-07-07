"""Resolve a GigRadar team to its Stripe customer id(s).

A team can hold up to THREE independent Stripe customers, one per product —
see `../../../references/data-reference.md` §7 for the full field map:

  teams.subscription.stripe.customer        -> "leads" product (core proposals)
  teams.apiSubscription.stripe.customer     -> "api" product
  teams.profilesSubscription.stripe.customer -> "profiles" product

They are frequently different `cus_...` ids even for the same team, because
each product subscribes the team under its own Stripe Customer object. Always
resolve all three and let the caller decide which one(s) it needs.

Credentials come from env (same contract as every other script in this plugin):
  MONGO_URI  (required — full URI incl. creds + ?authSource=admin)
  MONGO_DB   (default: gigradar-dev)

Usage:
  export MONGO_URI='mongodb://user:pass@host:port/gigradar-dev?authSource=admin'
  python3 resolve_customer_ids.py --team-oid 65f44d6c61651d267485d777
  python3 resolve_customer_ids.py --email someone@example.com
  python3 resolve_customer_ids.py --team-name "some team name substring"

Output (JSON on stdout):
  {
    "teamId": "65f44d6c61651d267485d777",
    "name": "someone@example.com",
    "customers": {
      "leads":    {"customer": "cus_...", "status": "active"},
      "api":      null,
      "profiles": null
    }
  }
"""
from __future__ import annotations
import argparse, json, os, sys

try:
    from pymongo import MongoClient
    from bson import ObjectId
except ImportError:
    print("pymongo not installed. `pip install pymongo --break-system-packages`", file=sys.stderr)
    raise

URI = os.environ.get("MONGO_URI")
DB = os.environ.get("MONGO_DB", "gigradar-dev")

PROJECTION = {
    "name": 1,
    "subscription.stripe.customer": 1,
    "subscription.stripe.status": 1,
    "apiSubscription.stripe.customer": 1,
    "apiSubscription.stripe.status": 1,
    "profilesSubscription.stripe.customer": 1,
    "profilesSubscription.stripe.status": 1,
}


def _extract(doc, key):
    sub = (doc or {}).get(key) or {}
    stripe = sub.get("stripe") or {}
    if not stripe.get("customer"):
        return None
    return {"customer": stripe["customer"], "status": stripe.get("status")}


def resolve(db, *, team_oid=None, email=None, team_name=None):
    if team_oid:
        query = {"_id": ObjectId(team_oid)}
    elif email:
        query = {"name": email}
    elif team_name:
        query = {"name": {"$regex": team_name, "$options": "i"}}
    else:
        raise SystemExit("pass one of --team-oid / --email / --team-name")

    doc = db.teams.find_one(query, PROJECTION)
    if not doc:
        return None

    return {
        "teamId": str(doc["_id"]),
        "name": doc.get("name"),
        "customers": {
            "leads": _extract(doc, "subscription"),
            "api": _extract(doc, "apiSubscription"),
            "profiles": _extract(doc, "profilesSubscription"),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--team-oid")
    g.add_argument("--email")
    g.add_argument("--team-name")
    args = ap.parse_args()

    if not URI:
        raise SystemExit("MONGO_URI is required")

    client = MongoClient(URI, serverSelectionTimeoutMS=15000)
    db = client[DB]

    result = resolve(db, team_oid=args.team_oid, email=args.email, team_name=args.team_name)
    if not result:
        print(json.dumps({"error": "team_not_found"}))
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
