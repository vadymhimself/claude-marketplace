"""Run ES metajob aggregations for a set of windows × dimensions.

Usage:
  python run_aggs.py --out <dir> --windows may2025,jun2025,jul2025 [--dims skills,category,subcategory]
                     [--size 100] [--focus-category "Sales & Marketing"]

Saves one JSON per (window, dim) to <dir>/<window>__<dim>.json and a _summary.json.
"""
from __future__ import annotations
import argparse, datetime, json, os, sys
from es_queries import search, total_count, top_terms_query, make_window, DEFAULT_DIMS


def parse_window_name(name: str) -> dict:
    """Parse 'may2025' or 'YYYY-MM' or 'YYYY-MM-DD:YYYY-MM-DD'."""
    # Explicit date-range form
    if ":" in name:
        gte, lt = name.split(":")
        return make_window(name.replace(":", "_"), gte, lt)
    # YYYY-MM form
    if len(name) == 7 and name[4] == "-" and name[:4].isdigit() and name[5:].isdigit():
        year, month = int(name[:4]), int(name[5:])
    # monYYYY like may2025
    elif len(name) == 7 and name[:3].isalpha() and name[3:].isdigit():
        month = datetime.datetime.strptime(name[:3].title(), "%b").month
        year = int(name[3:])
    else:
        raise SystemExit(f"cannot parse window name {name!r} (expected 'may2025', 'YYYY-MM', or 'YYYY-MM-DD:YYYY-MM-DD')")
    gte = datetime.date(year, month, 1)
    if month == 12:
        lt = datetime.date(year + 1, 1, 1)
    else:
        lt = datetime.date(year, month + 1, 1)
    slug = gte.strftime("%b%Y").lower()
    return make_window(slug, gte.isoformat(), lt.isoformat())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory for raw JSON")
    ap.add_argument("--windows", required=True, help="comma-separated: may2025,jun2025 or 2025-05,2025-06 or 2025-04-01:2025-07-01")
    ap.add_argument("--dims", default="skills,category,subcategory",
                    help="comma-separated: any of skills, category, subcategory, or any ES keyword field path")
    ap.add_argument("--size", type=int, default=100)
    ap.add_argument("--focus-category", default=None, help="narrow to one category (applies an ES filter)")
    ap.add_argument("--focus-skill", default=None, help="narrow to one skill name (applies an ES filter)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    windows = [parse_window_name(w.strip()) for w in args.windows.split(",") if w.strip()]
    dims = [d.strip() for d in args.dims.split(",") if d.strip()]

    extra_filter = None
    if args.focus_category:
        extra_filter = {"term": {"metaJob.categoryName": args.focus_category}}
    elif args.focus_skill:
        extra_filter = {"term": {"metaJob.skills.name.keyword": args.focus_skill}}

    summary = {}
    for w in windows:
        total = total_count(w, extra_filter)
        summary[w["name"]] = {"total": total, "window": w, "fields": {}}
        print(f"[{w['name']}] range={w['gte']}..{w['lt']}  total={total:,}")

        for slug in dims:
            field = DEFAULT_DIMS.get(slug, slug)
            print(f"  -> {slug} ({field})")
            try:
                body = top_terms_query(w, field, args.size, extra_filter)
                resp = search(body)
                buckets = resp["aggregations"]["top"]["buckets"]
            except Exception as e:
                print(f"     ERROR: {e}", file=sys.stderr)
                continue
            path = os.path.join(args.out, f"{w['name']}__{slug}.json")
            with open(path, "w") as f:
                json.dump({"window": w, "field": field, "total": total, "buckets": buckets}, f, default=str)
            summary[w["name"]]["fields"][slug] = {"path": path, "n_buckets": len(buckets)}
            print(f"     saved {len(buckets)} buckets -> {path}")

    with open(os.path.join(args.out, "_es_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print("done")


if __name__ == "__main__":
    main()
