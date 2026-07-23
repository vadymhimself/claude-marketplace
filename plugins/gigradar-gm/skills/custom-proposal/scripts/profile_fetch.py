#!/usr/bin/env python3
"""
profile_fetch.py — pull a lead's REAL Upwork profile fields from GigRadar's ranked
product data in Elasticsearch, so the section-4 profile mockup uses their actual
agency title, overview and skills instead of guessed copy.

The `profile-agency` / `profile-contractor` docs carry the full crawled profile in
`_source.originalData` (stored, `enabled:false` — returned but not searchable): the
real `title`, `description` (overview), `skills[].preferredLabel`, `minRate`/`maxRate`,
Top-Rated status, member-since, earnings. This emits a partial `profile` block with
those real values filled; the orchestrator keeps the mockup's projection framing but
never invents the title/overview/skills again.

Env: ES_URL / ES_USER / ES_PASS (metajob-ro reads profile-*).

Usage:
  python3 profile_fetch.py --name "TechInfini Solutions" --emit-json > profile.json
  python3 profile_fetch.py --entity contractor --name "Jane Doe" --hot Shopify,React --emit-json
"""
import argparse, json, os, sys, urllib.request, urllib.error, base64

ES_URL, ES_USER, ES_PASS = (os.environ.get(k, "") for k in ("ES_URL", "ES_USER", "ES_PASS"))

def es(path, body):
    if not ES_URL: sys.exit("ERROR: set ES_URL/ES_USER/ES_PASS (metajob-ro).")
    req = urllib.request.Request(f"{ES_URL}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"ES {e.code} on {path}: {e.read().decode()[:300]}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", choices=["agency", "contractor"], default="agency")
    ap.add_argument("--name", required=True)
    ap.add_argument("--hot", default="", help="comma-separated skill substrings to flag as 'hot'")
    ap.add_argument("--max-overview", type=int, default=520, help="trim overview to N chars at a sentence")
    ap.add_argument("--emit-json", action="store_true")
    a = ap.parse_args()
    index = "profile-agency" if a.entity == "agency" else "profile-contractor"

    r = es(f"/{index}/_search", {"size": 1, "query": {"bool": {
        "must": [{"match": {"name": a.name}}], "filter": [{"exists": {"field": "recentEarnings"}}]}}})
    hits = r["hits"]["hits"]
    if not hits: sys.exit(f"No {index} record matched name={a.name!r}.")
    s = hits[0]["_source"]; od = s.get("originalData") or {}

    import re as _re
    title = _re.sub(r"\s+", " ", (s.get("title") or od.get("title") or "")).strip()
    overview = _re.sub(r"\s+", " ", (s.get("description") or od.get("description") or "")).strip()
    if a.max_overview and len(overview) > a.max_overview:      # trim at a sentence boundary
        cut = overview[:a.max_overview]
        dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        overview = (cut[:dot + 1] if dot > 120 else cut).strip()

    skills = [x.get("preferredLabel") for x in (od.get("skills") or []) if x.get("preferredLabel")]
    if not skills: skills = od.get("skillsNames") or []
    hot = [h.strip().lower() for h in a.hot.split(",") if h.strip()]
    skills_block = [{"name": sk, "hot": any(h in sk.lower() for h in hot)} for sk in skills]

    j = s.get("jobSuccessScore")
    badges = []
    if s.get("topRatedPlusStatus"): badges.append({"label": "★ Top Rated Plus", "topRated": True})
    elif s.get("topRatedStatus"):   badges.append({"label": "★ Top Rated", "topRated": True})
    mn, mx = s.get("minRate"), s.get("maxRate")
    rate = (f"${mn:.0f}-{mx:.0f}" if mn and mx else (f"${mn:.0f}" if mn else None))

    block = {
        "_source_note": "Real Upwork profile fields from ES profile-* originalData. Do not overwrite title/overview/skills with guesses.",
        "profileTitle": title,
        "overview": overview,
        "skills": skills_block,
        "rate": rate, "rateSub": "+ fixed-bid projects",
        "badges": badges,
        "kpis": [
            {"v": "$" + (f"{s.get('totalEarnings',0)/1000:.0f}K+" if s.get('totalEarnings') else "0"), "navy": False, "k": "Total earned on Upwork"},
            {"v": (f"{j*100:.0f}%" if j else "—"), "navy": True, "k": "Job success score"},
            {"v": str(s.get("totalJobs") or 0), "navy": False, "k": "Jobs completed"},
            {"v": f"{(s.get('totalHours') or 0):,.0f}", "navy": False, "k": "Hours delivered"},
        ],
    }
    print(f"\n{s.get('name')}  ({index})", file=sys.stderr)
    print(f"  title: {title[:90]}...", file=sys.stderr)
    print(f"  skills ({len(skills)}): {', '.join(skills[:8])}...", file=sys.stderr)
    print(f"  rate: {rate} · JSS {(j*100 if j else 0):.0f}%", file=sys.stderr)
    if a.emit_json: print(json.dumps(block, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
