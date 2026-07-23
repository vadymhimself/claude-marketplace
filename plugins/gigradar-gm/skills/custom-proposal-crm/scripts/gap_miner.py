#!/usr/bin/env python3
"""
gap_miner.py — the custom-proposal-crm rank + competitor-gap miner.

Queries GigRadar's live ES `profile-agency` (or `profile-contractor`) index with the
`metajob-ro` role and produces the `competitorGap` block for lead-data.json: the lead's
own MRR + rank + percentile, the competitors ranked above them in their niche, and a
first-pass gap read. Every figure is a REAL ES record — never invent a competitor.

MRR is computed as recentEarnings / 6 (there is no stored `mgr`/`mrr` field on ES; the
public rankings API computes it, and so do we). Rank-in-scope = count(recentEarnings > lead)
+ 1 within the scope filter; percentile = rank / pool.

Env (same creds as the market-research / opportunity-mining ES recipe):
  ES_URL, ES_USER, ES_PASS   (role metajob-ro also grants read on profile-*)

Usage:
  # by name (best-match), auto-scope to the agency's country + first service:
  python3 gap_miner.py --name "TechInfini Solutions" --emit-json > gap.json
  # pin the scope + how many competitors to show:
  python3 gap_miner.py --name "TechInfini" --country "India" \
      --service shopify-development-companies --top 10 --emit-json > gap.json
  # freelancer instead of agency:
  python3 gap_miner.py --entity contractor --name "Jane Doe" --emit-json

Notes:
  - `services` are Upwork slugs (email-marketing-agencies, lead-generation-companies,
    shopify-development-companies, ...). `country` is a display name ("United States").
  - Prints a human-readable table to stderr; --emit-json writes the competitorGap object
    to stdout. Drop that object into lead-data.json under "competitorGap" (fill copy/headline).
  - This is a data primer, not final copy: it emits factual rows + a numeric gap read; the
    orchestrator writes the persuasive headline/lede/closeWhy grounded in these numbers.
"""
import argparse, json, os, sys, urllib.request, urllib.error, base64

ES_URL = os.environ.get("ES_URL", "")
ES_USER = os.environ.get("ES_USER", "")
ES_PASS = os.environ.get("ES_PASS", "")

def es(path, body):
    if not ES_URL:
        sys.exit("ERROR: ES_URL/ES_USER/ES_PASS must be set (metajob-ro creds).")
    req = urllib.request.Request(
        f"{ES_URL}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"ES {e.code} on {path}: {e.read().decode()[:400]}")

# ── entity-specific field maps ────────────────────────────────────────────────
# `rank_path` = the www.upworkrank.com route for this entity ("agency"/"freelancer");
# a profile URL is https://www.upworkrank.com/<rank_path>/<uid> and IS the public
# rank page (view full rank + claim). Verified against gigradar-upwork-rankings routes.
RANK_SITE = "https://www.upworkrank.com"
AGENCY = dict(index="profile-agency", uid="upworkAgencyUid", rank_path="agency",
              scope_country="country", scope_service="services")
CONTRACTOR = dict(index="profile-contractor", uid="upworkContractorUid", rank_path="freelancer",
                  scope_country="location.country", scope_service="skills.name.keyword")

def rank_url(cfg, src):
    uid = src.get(cfg["uid"])
    return f"{RANK_SITE}/{cfg['rank_path']}/{uid}" if uid else None

def fnum(n):
    try: return f"{float(n):,.0f}"
    except Exception: return "0"

def find_lead(cfg, name):
    r = es(f"/{cfg['index']}/_search", {
        "size": 5,
        "query": {"bool": {"must": [{"match": {"name": name}}],
                           "filter": [{"exists": {"field": "recentEarnings"}}]}},
        "sort": ["_score"]})
    hits = r["hits"]["hits"]
    if not hits:
        sys.exit(f"No {cfg['index']} record with earnings matched name={name!r}.")
    return hits[0]["_source"]

def scope_filter(cfg, country, service):
    f = [{"exists": {"field": "recentEarnings"}}]
    if country: f.append({"term": {cfg["scope_country"]: country}})
    if service: f.append({"term": {cfg["scope_service"]: service}})
    return f

def count(cfg, filt):
    return es(f"/{cfg['index']}/_count", {"query": {"bool": {"filter": filt}}})["count"]

def mrr(src): return round((src.get("recentEarnings") or 0) / 6)

def badge(src):
    if src.get("topRatedPlusStatus"): return "Top Rated Plus"
    if src.get("topRatedStatus"): return "Top Rated"
    return False

def row(cfg, src, rank, you=False):
    j = src.get("jobSuccessScore")
    return {
        "rank": str(rank),
        "name": src.get("name", ""),
        "mrr": "$" + fnum(mrr(src)),
        "jss": (f"{j*100:.0f}%" if j else "—"),
        "topRated": badge(src),
        "badgeNote": "not yet",
        "jobs": f"{fnum(src.get('totalJobs'))} jobs",
        "url": rank_url(cfg, src),   # deep-link to this agency's rank page
        "you": you,
        "_re": src.get("recentEarnings") or 0,   # internal, strip before emit
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", choices=["agency", "contractor"], default="agency")
    ap.add_argument("--name", required=True)
    ap.add_argument("--country", default=None, help="display name; default = lead's own")
    ap.add_argument("--service", default=None, help="Upwork service slug; default = lead's first")
    ap.add_argument("--top", type=int, default=10, help="how many competitors above to list")
    ap.add_argument("--emit-json", action="store_true")
    a = ap.parse_args()
    cfg = AGENCY if a.entity == "agency" else CONTRACTOR

    lead = find_lead(cfg, a.name)
    lead_re = lead.get("recentEarnings") or 0
    # --country "" (explicit empty) → WORLDWIDE (no country filter); omitted → lead's own.
    if a.country is not None:
        country = a.country or None
    else:
        country = lead.get("country") or (lead.get("location") or {}).get("country")
    services = lead.get("services") or [s.get("name") for s in (lead.get("skills") or [])]
    service = a.service or (services[0] if services else None)

    filt = scope_filter(cfg, country, service)
    pool = count(cfg, filt)
    above = count(cfg, filt + [{"range": {"recentEarnings": {"gt": lead_re}}}])
    rank = above + 1
    pct = round(rank / pool * 100, 1) if pool else None

    # competitors above the lead (highest first)
    comp = es(f"/{cfg['index']}/_search", {
        "size": a.top,
        "query": {"bool": {"filter": filt + [{"range": {"recentEarnings": {"gt": lead_re}}}]}},
        "sort": [{"recentEarnings": "desc"}]})["hits"]["hits"]

    board = [row(cfg, h["_source"], i + 1) for i, h in enumerate(comp)]
    board.append(row(cfg, lead, rank, you=True))

    # ── numeric gap read (facts the orchestrator turns into copy) ──
    lead_badge = badge(lead)
    above_with_badge = sum(1 for b in board[:-1] if b["topRated"])
    nearest = board[-2]["_re"] if len(board) > 1 else lead_re     # rung right above
    gap_to_next = max(0, round((nearest - lead_re) / 6))
    lead_jss = lead.get("jobSuccessScore")

    # human summary → stderr
    print(f"\n{lead.get('name')}  |  scope: {country} × {service}", file=sys.stderr)
    print(f"  MRR ${mrr(lead):,} · JSS {lead_jss*100:.0f}% · badge {lead_badge or 'NONE'} "
          f"· {fnum(lead.get('totalJobs'))} jobs · {fnum(lead.get('totalHours'))} hrs", file=sys.stderr)
    print(f"  RANK #{rank} of {pool}  (top {pct}%)", file=sys.stderr)
    print(f"  {above_with_badge}/{len(board)-1} agencies above you carry a Top-Rated badge; "
          f"you: {lead_badge or 'none'}", file=sys.stderr)
    print(f"  nearest rung up = +${gap_to_next:,}/mo MRR\n", file=sys.stderr)
    print(f"  {'#':>2} {'agency':<32}{'MRR':>10}{'JSS':>6}  badge", file=sys.stderr)
    for b in board:
        tag = " <= YOU" if b["you"] else ""
        print(f"  {b['rank']:>2} {b['name'][:32]:<32}{b['mrr']:>10}{b['jss']:>6}  "
              f"{(b['topRated'] or '—'):<15}{tag}", file=sys.stderr)

    for b in board:
        b.pop("_re", None)
    block = {
        "_gap_facts": {
            "rank": rank, "pool": pool, "pct": pct, "scopeCountry": country, "scopeService": service,
            "leadMrr": mrr(lead), "leadJss": (round(lead_jss*100) if lead_jss else None),
            "leadBadge": lead_badge or None, "aboveWithBadge": above_with_badge,
            "gapToNextMrr": gap_to_next, "leadJobs": lead.get("totalJobs"), "leadHours": lead.get("totalHours"),
        },
        "headline": "", "lede": "",
        "rankUrl": rank_url(cfg, lead),     # lead's own rank page (view full rank + claim)
        "rankSite": RANK_SITE,
        "rankLabel": f"#{rank}", "poolLabel": f"of {pool:,}",
        "scopeLine": f"{country or 'Worldwide'} · {service} · ranked by monthly revenue",
        "stats": [
            {"v": "$" + fnum(mrr(lead)), "k": "Your MRR"},
            {"v": f"#{rank}", "k": f"of {pool:,}"},
            {"v": f"Top {pct}%", "k": "in your niche", "good": True},
        ],
        "board": board,
        "boardNote": "Monthly revenue estimated from public Upwork earnings (trailing 6 months / 6). Live as of preparation date.",
        "closeTitle": "", "closeWhy": "",
        "fixes": [],
    }
    if a.emit_json:
        print(json.dumps(block, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
