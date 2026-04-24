"""Merge ES volume/quality JSON + Mongo reply-rate JSON into per-dim tidy CSVs.

Reads raw files produced by run_aggs.py + mongo_reply_rates.py:
  <raw>/<window>__<es_dim>.json          (ES aggregation output)
  <raw>/reply__<window>__<mongo_dim>.json (Mongo reply-rate output)

Produces:
  <tidy>/<dim>__full.csv                  (one row per bucket key, wide by window)

The ES dim name 'skills' maps to the Mongo dim name 'skill' for the same entity.
"""
from __future__ import annotations
import argparse, csv, json, os, sys

# Map between mongo grouping slugs and ES dim slugs.
# ES run_aggs.py outputs `<window>__skills.json`, mongo outputs `reply__<window>__skill.json`
ES_SLUG_FOR = {"skill": "skills", "category": "category", "subcategory": "subcategory"}


def load_reply(raw_dir: str, window: str, slug: str) -> dict:
    path = os.path.join(raw_dir, f"reply__{window}__{slug}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {r["_id"]: r for r in data}


def load_es(raw_dir: str, window: str, slug: str):
    es_slug = ES_SLUG_FOR.get(slug, slug)
    path = os.path.join(raw_dir, f"{window}__{es_slug}.json")
    if not os.path.exists(path):
        return 0, {}
    with open(path) as f:
        data = json.load(f)
    return data.get("total", 0), {b["key"]: b for b in data.get("buckets", [])}


def bucket_metrics(b):
    if not b:
        return {}
    hourly = b.get("hourly") or {}
    fixed  = b.get("fixed")  or {}
    return {
        "doc_count": b.get("doc_count"),
        "hourly_jobs": hourly.get("doc_count"),
        "fixed_jobs":  fixed.get("doc_count"),
        "median_hourly_min": (hourly.get("median_hourly_min") or {}).get("values", {}).get("50.0"),
        "median_hourly_max": (hourly.get("median_hourly_max") or {}).get("values", {}).get("50.0"),
        "median_fixed":      (fixed.get("median_fixed") or {}).get("values", {}).get("50.0"),
        "p25_fixed":         (fixed.get("p25_fixed") or {}).get("values", {}).get("25.0"),
        "p75_fixed":         (fixed.get("p75_fixed") or {}).get("values", {}).get("75.0"),
        "pct_payment_verified": (b.get("pct_payment_verified") or {}).get("value"),
        "avg_total_spent":   (b.get("avg_total_spent") or {}).get("value"),
        "avg_feedback":      (b.get("avg_feedback_score") or {}).get("value"),
        "avg_hire_rate":     (b.get("avg_hire_rate") or {}).get("value"),
    }


def build(dim: str, windows: list[str], raw_dir: str):
    """dim = 'skill' | 'category' | 'subcategory'"""
    es = {w: load_es(raw_dir, w, dim) for w in windows}
    rp = {w: load_reply(raw_dir, w, dim) for w in windows}
    es_totals = {w: es[w][0] for w in windows}

    keys = set()
    for w in windows:
        keys.update(es[w][1].keys())
        keys.update(rp[w].keys())

    rows = []
    for k in keys:
        row = {"key": k}
        for w in windows:
            bm = bucket_metrics(es[w][1].get(k))
            rv = rp[w].get(k) or {}
            p = rv.get("proposals") or 0
            r = rv.get("replies") or 0
            v = rv.get("views") or 0
            jobs = bm.get("doc_count") or 0
            row[f"{w}_jobs"] = jobs
            row[f"{w}_jobs_share"] = (jobs / es_totals[w]) if es_totals[w] else 0
            row[f"{w}_median_hourly_min"] = bm.get("median_hourly_min")
            row[f"{w}_median_hourly_max"] = bm.get("median_hourly_max")
            row[f"{w}_median_fixed"] = bm.get("median_fixed")
            row[f"{w}_p25_fixed"] = bm.get("p25_fixed")
            row[f"{w}_p75_fixed"] = bm.get("p75_fixed")
            row[f"{w}_pct_pv"] = bm.get("pct_payment_verified")
            row[f"{w}_avg_total_spent"] = bm.get("avg_total_spent")
            row[f"{w}_avg_feedback"] = bm.get("avg_feedback")
            row[f"{w}_avg_hire_rate"] = bm.get("avg_hire_rate")
            row[f"{w}_hourly_jobs"] = bm.get("hourly_jobs")
            row[f"{w}_fixed_jobs"] = bm.get("fixed_jobs")
            row[f"{w}_pct_hourly"] = (bm.get("hourly_jobs") / jobs) if (bm.get("hourly_jobs") is not None and jobs) else None
            row[f"{w}_proposals"] = p
            row[f"{w}_replies"] = r
            row[f"{w}_views"] = v
            row[f"{w}_reply_rate"] = (r / p) if p else None
            row[f"{w}_view_rate"]  = (v / p) if p else None
        # Pairwise deltas between consecutive windows
        for i in range(1, len(windows)):
            prev, cur = windows[i - 1], windows[i]
            pj, cj = row.get(f"{prev}_jobs") or 0, row.get(f"{cur}_jobs") or 0
            row[f"{cur}_vs_{prev}_jobs_pct"] = ((cj - pj) / pj) if pj else None
            pr, cr = row.get(f"{prev}_reply_rate"), row.get(f"{cur}_reply_rate")
            row[f"{cur}_vs_{prev}_reply_rate_abs"] = (cr - pr) if (cr is not None and pr is not None) else None
        rows.append(row)

    # Sort by the last window's jobs desc
    last = windows[-1]
    rows.sort(key=lambda r: r.get(f"{last}_jobs") or 0, reverse=True)
    return rows, es_totals


def save_csv(rows, path):
    if not rows:
        print(f"  (no rows for {path})", file=sys.stderr)
        return
    fns = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fns)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="dir containing <window>__<dim>.json + reply__<window>__<dim>.json")
    ap.add_argument("--out", required=True, help="dir for tidy CSVs")
    ap.add_argument("--windows", required=True, help="comma-separated window slugs (order matters for deltas)")
    ap.add_argument("--dims", default="category,subcategory,skill",
                    help="comma-separated; any of: category, subcategory, skill")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    dims = [d.strip() for d in args.dims.split(",") if d.strip()]

    summary = {}
    for dim in dims:
        rows, totals = build(dim, windows, args.raw)
        path = os.path.join(args.out, f"{dim}__full.csv")
        save_csv(rows, path)
        summary[dim] = {"rows": len(rows), "es_totals": totals, "path": path}
        print(f"{dim}: {len(rows)} rows (ES totals {totals}) -> {path}")

    with open(os.path.join(args.out, "_merge_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
