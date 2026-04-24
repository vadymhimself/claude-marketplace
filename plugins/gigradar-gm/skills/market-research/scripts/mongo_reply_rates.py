"""Mongo proposals reply-rate aggregation, windowed.

Canonical formula (transcribed from `StatsRepository.getOpportunityStats`):
  reply  = dashroomUID non-null
  view   = dashroomUID OR status==7 OR otherAnnotations contains 12
  base   = meta.createdAt in [from, to), meta.inviteToInterviewUid is null

No team filter by default — produces market-wide reply-rate benchmarks across all
GigRadar customers. Pass --team-oid to narrow to one team.

Credentials come from env:
  MONGO_URI  (required — full URI incl. creds + ?authSource=admin)
  MONGO_DB   (default: gigradar-dev)

Usage:
  export MONGO_URI='mongodb://user:pass@host:port/gigradar-dev?authSource=admin'
  python mongo_reply_rates.py --out raw/ --windows may2025,jun2025 \
                              [--groupings category,subcategory,skill] \
                              [--limit 500] [--team-oid <oid>]
"""
from __future__ import annotations
import argparse, datetime, json, os, sys, time

try:
    from pymongo import MongoClient
    from bson import ObjectId
except ImportError:
    print("pymongo not installed. `pip install pymongo --break-system-packages`", file=sys.stderr)
    raise

URI = os.environ.get("MONGO_URI")
DB  = os.environ.get("MONGO_DB", "gigradar-dev")

HINT = {"meta.createdAt": 1}  # force standalone createdAt index for cross-team scans

GROUPINGS = {
    # slug         group-expr                  unwind (or None)
    "category":    ("$metaJob.categoryName",    None),
    "subcategory": ("$metaJob.subCategoryName", None),
    "skill":       ("$metaJob.skills.name",     "$metaJob.skills"),
}


def parse_window_name(name: str) -> dict:
    """Parse 'may2025' / 'YYYY-MM' / 'YYYY-MM-DD:YYYY-MM-DD' into UTC datetime bounds."""
    if ":" in name:
        gte_s, lt_s = name.split(":")
        gte = datetime.datetime.fromisoformat(gte_s).replace(tzinfo=datetime.timezone.utc)
        lt  = datetime.datetime.fromisoformat(lt_s).replace(tzinfo=datetime.timezone.utc)
        slug = name.replace(":", "_")
        return {"name": slug, "gte": gte, "lt": lt}
    if len(name) == 7 and name[4] == "-" and name[:4].isdigit() and name[5:].isdigit():
        year, month = int(name[:4]), int(name[5:])
    elif len(name) == 7 and name[:3].isalpha() and name[3:].isdigit():
        month = datetime.datetime.strptime(name[:3].title(), "%b").month
        year = int(name[3:])
    else:
        raise SystemExit(f"cannot parse window name {name!r}")
    gte = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
    if month == 12:
        lt = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
    else:
        lt = datetime.datetime(year, month + 1, 1, tzinfo=datetime.timezone.utc)
    slug = gte.strftime("%b%Y").lower()
    return {"name": slug, "gte": gte, "lt": lt}


def build_match(window: dict, team_oid: str | None = None) -> dict:
    match = {
        "meta.createdAt": {"$gte": window["gte"], "$lt": window["lt"]},
        "meta.inviteToInterviewUid": None,
    }
    if team_oid:
        match["_gigradarTeamOid"] = ObjectId(team_oid)
    return {"$match": match}


def build_pipeline(window: dict, group_expr: str, unwind: str | None, limit: int, team_oid: str | None):
    stages = [build_match(window, team_oid)]
    if unwind:
        stages.append({"$unwind": {"path": unwind, "preserveNullAndEmptyArrays": False}})
    stages += [
        {"$group": {
            "_id": group_expr,
            "proposals": {"$sum": 1},
            "replies":   {"$sum": {"$cond": [{"$not": "$dashroomUID"}, 0, 1]}},
            "views":     {"$sum": {"$cond": [
                {"$or": [
                    {"$ne": ["$dashroomUID", None]},
                    {"$eq": ["$status", 7]},
                    {"$in": [12, {"$ifNull": ["$otherAnnotations", []]}]},
                ]}, 1, 0
            ]}},
        }},
        {"$match": {"_id": {"$ne": None}}},
        {"$sort": {"proposals": -1}},
        {"$limit": limit},
    ]
    return stages


def run_one(db, window: dict, slug: str, limit: int, out_dir: str, team_oid: str | None) -> int:
    group_expr, unwind = GROUPINGS[slug]
    pipe = build_pipeline(window, group_expr, unwind, limit, team_oid)
    t0 = time.time()
    docs = list(db["proposals"].aggregate(pipe, allowDiskUse=True, maxTimeMS=600_000, hint=HINT))
    dt = time.time() - t0
    path = os.path.join(out_dir, f"reply__{window['name']}__{slug}.json")
    with open(path, "w") as f:
        json.dump(docs, f, default=str)
    print(f"  {window['name']}/{slug:12s}  rows={len(docs):4d}  took={dt:6.1f}s  -> {path}")
    return len(docs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory for raw reply JSON")
    ap.add_argument("--windows", required=True, help="comma-separated: may2025,jun2025 / 2025-05 / 2025-05-01:2025-06-01")
    ap.add_argument("--groupings", default="category,subcategory,skill",
                    help=f"comma-separated; any of: {','.join(GROUPINGS.keys())}")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--team-oid", default=None, help="narrow to one GigRadar team (ObjectId string)")
    ap.add_argument("--force", action="store_true", help="overwrite existing JSON files")
    args = ap.parse_args()

    if not URI:
        raise SystemExit("MONGO_URI env var is required")

    os.makedirs(args.out, exist_ok=True)
    windows = [parse_window_name(w.strip()) for w in args.windows.split(",") if w.strip()]
    slugs = [s.strip() for s in args.groupings.split(",") if s.strip()]
    bad = [s for s in slugs if s not in GROUPINGS]
    if bad:
        raise SystemExit(f"unknown groupings: {bad} (known: {list(GROUPINGS)})")

    client = MongoClient(URI, serverSelectionTimeoutMS=15_000)
    db = client[DB]

    summary = {}
    for w in windows:
        summary[w["name"]] = {}
        for slug in slugs:
            path = os.path.join(args.out, f"reply__{w['name']}__{slug}.json")
            if (not args.force) and os.path.exists(path) and os.path.getsize(path) > 10:
                print(f"  {w['name']}/{slug} already exists, skipping (use --force to overwrite)")
                summary[w["name"]][slug] = {"path": path, "skipped": True}
                continue
            try:
                n = run_one(db, w, slug, args.limit, args.out, args.team_oid)
                summary[w["name"]][slug] = {"path": path, "rows": n}
            except Exception as e:
                print(f"  FAIL {w['name']}/{slug}: {e}", file=sys.stderr)
                summary[w["name"]][slug] = {"path": path, "error": str(e)}

    with open(os.path.join(args.out, "_mongo_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print("done")


if __name__ == "__main__":
    main()
