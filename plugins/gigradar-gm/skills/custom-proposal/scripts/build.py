#!/usr/bin/env python3
"""
build.py — fill the proposal template with one lead's data and produce a
self-contained HTML file.

What it does (deterministic, so any teammate gets the same result):
  1. Loads the lead data JSON (matches assets/template-schema.json).
  2. Resolves image references: any string of the form "@file:<path>" is read
     from disk and inlined as a base64 data URI (so the output is one portable
     file — emailable, hostable, PDF-able, no external assets).
  3. Selects the most relevant case studies from the library by tag overlap
     with the lead's serviceTags, embeds their avatar/logo, and attaches them
     as lead["caseStudies"].
  4. Injects the final lead object into the template's {{LEAD_JSON}} slot.

Usage:
  python3 build.py --lead lead-data.json --out proposal.html
  # optional overrides:
  #   --template <path>   (default: ../assets/template.html)
  #   --library  <path>   (default: ../assets/case-studies/case-studies.json)
  #   --cases    <int>    (default: 6)
  #   --base-dir <path>   (root for resolving @file: paths; default: lead json's dir)

The lead JSON should carry images as "@file:relative/or/abs/path.jpg" — the
orchestrator never pastes base64 by hand. Already-formed data: URIs pass through
untouched.
"""
import argparse, base64, html, json, os, sys

MIME = {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
        ".webp":"image/webp",".avif":"image/avif",".svg":"image/svg+xml",
        ".gif":"image/gif"}

def data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    mime = MIME.get(ext, "application/octet-stream")
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

def resolve_files(obj, base_dir):
    """Recursively replace '@file:<path>' strings with base64 data URIs."""
    if isinstance(obj, dict):
        return {k: resolve_files(v, base_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_files(v, base_dir) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("@file:"):
            p = obj[len("@file:"):]
            if not os.path.isabs(p):
                p = os.path.join(base_dir, p)
            if not os.path.exists(p):
                print(f"  WARN: image not found: {p}", file=sys.stderr)
                return ""
            return data_uri(p)
        if obj.startswith("data:"):
            return obj
        # The template injects most fields via textContent, where a literal
        # "&amp;" / "&mdash;" would show as raw text. Decode HTML entities here so
        # teammates can write either entities OR literal Unicode and it renders
        # correctly either way. (HTML-typed fields use literal <tags>, which have
        # no entities, so this is a no-op for them.)
        return html.unescape(obj)
    return obj

def pretty_tag(tag):
    special = {"revops":"RevOps","crm":"CRM","ppc-ads":"PPC / Ads","seo":"SEO",
               "ux-ui":"UX/UI","ai-automation":"AI Automation","bpo-staffing":"BPO / Staffing",
               "va-admin-support":"VA / Admin","data-analytics":"Data / Analytics"}
    if tag in special: return special[tag]
    return tag.replace("-", " ").title()

def select_cases(lead, library, lib_dir, n):
    """Rank library by tag overlap with the lead, embed assets, return n cards."""
    want = set(lead.get("serviceTags") or lead.get("caseStudyTags") or [])
    def score(cs):
        return len(set(cs.get("tags", [])) & want)
    # stable sort: relevance desc, then library order (curated impact order)
    ranked = sorted(enumerate(library), key=lambda t: (-score(t[1]), t[0]))
    picked, cards = [], []
    for _, cs in ranked:
        if len(cards) >= n: break
        ava = cs.get("avatar_file")
        if not ava: continue
        ava_path = os.path.join(lib_dir, ava)
        if not os.path.exists(ava_path): continue
        card = {
            "avatar": data_uri(ava_path),
            "name": cs.get("founder") or cs.get("company") or "",
            "company": cs.get("company") or "",
            "logo": None,
            "result": cs.get("result",""),
            "subtitle": cs.get("subtitle",""),
            "tag": pretty_tag((cs.get("tags") or ["case study"])[0]),
            "url": cs.get("url","#"),
            "relevance": score(cs),
        }
        logo = cs.get("logo_file")
        if logo:
            lp = os.path.join(lib_dir, logo)
            if os.path.exists(lp):
                card["logo"] = data_uri(lp)
        cards.append(card); picked.append(cs.get("slug"))
    print(f"  case studies selected (by tags {sorted(want)}): "
          + ", ".join(f"{c['company'] or c['name']}({c['relevance']})" for c in cards))
    return cards

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=os.path.join(here, "..", "assets", "template.html"))
    ap.add_argument("--library", default=os.path.join(here, "..", "assets", "case-studies", "case-studies.json"))
    ap.add_argument("--cases", type=int, default=6)
    ap.add_argument("--base-dir", default=None)
    a = ap.parse_args()

    with open(a.lead) as f:
        lead = json.load(f)
    base_dir = a.base_dir or os.path.dirname(os.path.abspath(a.lead))

    # 1) case studies (unless the lead already provides them explicitly)
    if "caseStudies" not in lead or not lead["caseStudies"]:
        if os.path.exists(a.library):
            with open(a.library) as f:
                library = json.load(f)
            lead["caseStudies"] = select_cases(lead, library, os.path.dirname(a.library), a.cases)
        else:
            print(f"  WARN: case-study library not found at {a.library}", file=sys.stderr)
            lead["caseStudies"] = []

    # 2) inline every @file: image (lead photo/logo, opportunity/profile assets)
    lead = resolve_files(lead, base_dir)

    # 3) inject into template
    with open(a.template) as f:
        tpl = f.read()
    if "{{LEAD_JSON}}" not in tpl:
        sys.exit("ERROR: template has no {{LEAD_JSON}} placeholder")
    out = tpl.replace("{{LEAD_JSON}}", json.dumps(lead, ensure_ascii=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  wrote {a.out} ({len(out):,} bytes, {len(lead.get('caseStudies',[]))} case studies)")

if __name__ == "__main__":
    main()
