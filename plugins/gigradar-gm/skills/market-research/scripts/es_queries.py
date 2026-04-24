"""Elasticsearch metajob aggregation helpers for GigRadar market research.

Credentials come from env vars:
  ES_URL   (default: https://<es-host>:9243)
  ES_USER  (default: researcher-prod)
  ES_PASS  (required — no default)

Windows are passed as dicts like {"name": "may2025", "gte": "2025-05-01", "lt": "2025-06-01"}.
"""
from __future__ import annotations
import json, os, sys, urllib3, datetime
import requests

urllib3.disable_warnings()

ES_URL  = os.environ.get("ES_URL",  "https://<es-host>:9243")
ES_USER = os.environ.get("ES_USER", "researcher-prod")
ES_PASS = os.environ.get("ES_PASS")

if not ES_PASS:
    # Allow running with no password only for --help-like use
    print("[es_queries] WARN: ES_PASS env var not set — search() calls will fail", file=sys.stderr)

AUTH = (ES_USER, ES_PASS or "")
INDEX = os.environ.get("ES_INDEX", "metajob")


def search(body: dict, params: dict | None = None, index: str | None = None) -> dict:
    """POST {ES_URL}/{index}/_search."""
    idx = index or INDEX
    r = requests.post(
        f"{ES_URL}/{idx}/_search",
        json=body,
        auth=AUTH,
        timeout=180,
        params=params or {},
        verify=False,
    )
    r.raise_for_status()
    return r.json()


def make_window(name: str, gte: str, lt: str) -> dict:
    """Build a window descriptor. gte/lt are ISO dates like '2025-05-01'."""
    return {"name": name, "gte": gte, "lt": lt}


def last_n_months(n: int = 3, ref: datetime.date | None = None) -> list[dict]:
    """Return n month-sized windows ending at `ref` (defaults to last completed month).

    Example: last_n_months(3) on 2026-04-22 returns windows for Jan, Feb, Mar 2026
    (three completed months prior to the current month).
    """
    if ref is None:
        today = datetime.date.today()
        # Default to the first of the previous month
        ref = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    out = []
    cur = ref
    # Step back n months
    for _ in range(n):
        gte = cur.replace(day=1)
        # compute lt = first day of next month
        if cur.month == 12:
            lt = datetime.date(cur.year + 1, 1, 1)
        else:
            lt = datetime.date(cur.year, cur.month + 1, 1)
        name = gte.strftime("%b%Y").lower()
        out.append(make_window(name, gte.isoformat(), lt.isoformat()))
        # step back one month
        prev_month = gte - datetime.timedelta(days=1)
        cur = prev_month.replace(day=1)
    return list(reversed(out))


def window_filter(window: dict) -> dict:
    return {"range": {"metaJob.createdOn": {"gte": window["gte"], "lt": window["lt"]}}}


def quality_subaggs() -> dict:
    """Quality sub-aggregations for a bucket.

    Budget medians are scoped by `metaJob.budget.type` (1=fixed, 2=hourly) — mixing
    them is meaningless since hourly is $/hr and fixed is total $.
    """
    return {
        "hourly": {
            "filter": {"term": {"metaJob.budget.type": 2}},
            "aggs": {
                "median_hourly_min": {"percentiles": {"field": "metaJob.budget.hourlyMin", "percents": [50]}},
                "median_hourly_max": {"percentiles": {"field": "metaJob.budget.hourlyMax", "percents": [50]}},
            },
        },
        "fixed": {
            "filter": {"term": {"metaJob.budget.type": 1}},
            "aggs": {
                "median_fixed": {"percentiles": {"field": "metaJob.budget.fixed", "percents": [50]}},
                "p25_fixed":    {"percentiles": {"field": "metaJob.budget.fixed", "percents": [25]}},
                "p75_fixed":    {"percentiles": {"field": "metaJob.budget.fixed", "percents": [75]}},
            },
        },
        "pct_payment_verified": {"avg": {"field": "metaJob.client.paymentVerified"}},
        "avg_total_spent":      {"avg": {"field": "metaJob.client.stats.totalSpent"}},
        "avg_feedback_score":   {"avg": {"field": "metaJob.client.stats.feedbackScore"}},
        "avg_hire_rate":        {"avg": {"field": "metaJob.client.stats.hireRate"}},
    }


def top_terms_query(window: dict, field: str, size: int = 100, extra_filter: dict | None = None) -> dict:
    """Build a top-N terms aggregation with quality sub-aggs, windowed by `createdOn`.

    `extra_filter` is merged into the query's bool.filter — use it to narrow to an ICP,
    e.g. {"term": {"metaJob.categoryName": "Sales & Marketing"}}.
    """
    filters = [window_filter(window)]
    if extra_filter:
        filters.append(extra_filter)
    query = {"bool": {"filter": filters}} if len(filters) > 1 else filters[0]
    return {
        "size": 0,
        "track_total_hits": True,
        "query": query,
        "aggs": {"top": {"terms": {"field": field, "size": size}, "aggs": quality_subaggs()}},
    }


def total_count(window: dict, extra_filter: dict | None = None) -> int:
    """Market total for a window (with optional ICP narrowing)."""
    filters = [window_filter(window)]
    if extra_filter:
        filters.append(extra_filter)
    query = {"bool": {"filter": filters}} if len(filters) > 1 else filters[0]
    body = {"size": 0, "track_total_hits": True, "query": query}
    return search(body)["hits"]["total"]["value"]


# Default dimensions to aggregate across. Keep these field names accurate — see
# references/es-patterns.md for the gotchas (.keyword suffixing, etc).
DEFAULT_DIMS = {
    "skills":      "metaJob.skills.name.keyword",
    "category":    "metaJob.categoryName",
    "subcategory": "metaJob.subCategoryName",
}


if __name__ == "__main__":
    # Smoke test: python es_queries.py may2025 skills 5
    name = sys.argv[1] if len(sys.argv) > 1 else "may2025"
    slug = sys.argv[2] if len(sys.argv) > 2 else "skills"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    # Parse window name like "may2025" into a window dict
    if len(name) == 7 and name[3:].isdigit():
        month = datetime.datetime.strptime(name[:3], "%b").month
        year  = int(name[3:])
    else:
        raise SystemExit(f"expected window name like 'may2025', got {name!r}")
    gte = datetime.date(year, month, 1)
    lt  = (datetime.date(year + (1 if month == 12 else 0), (month % 12) + 1, 1))
    window = make_window(name, gte.isoformat(), lt.isoformat())

    field = DEFAULT_DIMS.get(slug, slug)
    out = search(top_terms_query(window, field, size))
    print(json.dumps(out, indent=2, default=str)[:5000])
