# Known issues & lessons from the GigRadar bootstrap

Real-world failure modes we hit setting up `gigradar-kb`, with their causes and fixes. Read before bootstrapping a new KB.

## The big surprise: Workers AI free-tier quota is *daily*, not lifetime

The Cloudflare Workers AI free tier gives you **10,000 neurons per day**, resetting at 00:00 UTC. A single `bge-large-en-v1.5` embedding on a typical 2-page markdown file uses ~2–4 chunks × ~25 neurons each ≈ **80–100 neurons per file**.

Math: **~100–125 files per day on the free tier**, then everything fails for the rest of the UTC day with `error 4006: you have used up your daily free allocation`.

For our 127-file backfill we hit the cap around file ~100. The remaining ~26 files indexed automatically the next day when the cron fired and quota reset.

**The fix shipped:** the indexer detects error 4006 and stops wasting subrequests on the rest of the batch. It returns a `quota_blocked: true` flag and a `note` field explaining what to do. Cron will continue retrying every 5 minutes; eventually one tick lands after 00:00 UTC and clears the backlog.

**For users who can't wait:** upgrade Workers to the **Paid plan ($5/month)** at [https://dash.cloudflare.com/?to=/:account/workers/plans](https://dash.cloudflare.com/?to=/:account/workers/plans). The paid tier has dramatically higher AI quotas — easily fits our use case.

**Vector ID gotcha:** Vectorize caps vector IDs at 64 bytes. Our R2 paths can be 80+ chars (`<bundle>/insights/NN_topic-slug/INSIGHT.md`). The fix shipped is to SHA-256 the path, take the first 16 hex chars, and append `::<chunk_index>`. Full path stays in metadata. **If you change this hashing scheme later, you must reindex everything** because the IDs won't match what's already in Vectorize.

## What worked first try

- **Vectorize index creation** via the v2 API: `POST /accounts/{id}/vectorize/v2/indexes`. Clean, fast, deterministic.
- **R2 bucket creation** once R2 was enabled at the account level.
- **Workers AI binding** for embeddings via `@cf/baai/bge-large-en-v1.5` — sub-200ms direct, ~1.4s cold via Worker binding.
- **KV namespaces** for both indexer state and consumer tokens.
- **Workers cron triggers** at the API level — schedule was set, though runs went silent (see below).

## What surprised us

### R2 needs an account-level enable, not just a token permission
First bootstrap attempt failed because `GET /r2/buckets` returned `10042 — Please enable R2 through the Cloudflare Dashboard`. Cloudflare requires an explicit one-click R2 sign-up at `dash.cloudflare.com/?to=/:account/r2/overview` even though it's free. Token permissions alone aren't enough.

**Fix in this skill:** `00_preflight.sh` checks for this specifically and prints the exact dashboard URL to enable R2.

### Vectorize metadata-index endpoint uses underscore, not dash
The Cloudflare docs are inconsistent. The right path is:

```
POST /vectorize/v2/indexes/{name}/metadata_index/create
                                         ^---- underscore, not dash
```

Variants like `/metadata-index/create` or `/metadata-indexes` return 404. Took us 15 minutes to find this.

### Workers Free CPU limit ≠ wallclock limit
A Worker can wait on I/O for 30+ seconds without issue but burns CPU budget on string parsing. A reindex loop processing 5 files (5 × small markdown parse) hit `Error 1102 — exceeded resource limits` at exactly ~3.2 seconds. The same code as a single-file fetch+embed+upsert worked fine.

**Lesson:** keep CPU work per request minimal. The fix was to batch fewer files per request and make `MAX_CHUNKS_PER_FILE` a hard cap. Not all CPU-time blowups manifest as obvious tight-loops — just regex over markdown adds up.

### Vectorize binding cold starts
The first request after a Worker deploy is slower against the Vectorize binding than subsequent requests. Sometimes 30+ seconds; sometimes hangs entirely. After a few warm calls, latency drops to ~700ms for a single upsert.

**Lesson:** never trust the first request after deploy as a smoke test. Hit it 2–3 times before declaring failure.

### Cron-triggered runs are silent failures
Setting `*/5 * * * *` succeeded via the API. Runs may or may not actually fire — they don't surface in the dashboard's Workers logs unless observability is on. In our run, after 2+ hours of expected cron ticks, the index stayed at the same vector count it had after manual `/reindex` runs.

**Workaround:** use `/reindex` from a long-running script for the initial backfill instead of waiting on cron. Cron is fine for incremental updates after the bucket is mostly indexed.

## What we got wrong the first time

### Walk-up-to-nearest-README pattern was brittle
Original design: the `/context-for` endpoint walked the path up the R2 prefix tree looking for the nearest `README.md`. The moment any sub-folder accidentally has a `README.md` (e.g. someone documents the `insights/` folder), the walk-up returns the wrong README and every citation downstream is broken.

**Fix shipped:** the bundle slug is the first path segment by construction. Just `bundle_slug + "/README.md"` is deterministic, one R2 read, can't be confused by intermediate files. Sub-folder READMEs are now banned by convention; the bundle README is the only README that exists. See `references/architecture.md`.

### Manifest treated root-level files as their own bundles
Our manifest builder did `bundle_slug = path.split("/")[0]`. For `README.md` or `_CONVENTIONS.md` at the KB root, `split("/")[0]` returns the file name — so they appeared as fake bundles in the output.

**Fix shipped:** the indexer's `rebuildManifest` now skips paths without a `/`. Bundles are folders only.

### Keyword search did R2-scan-and-grep
Original `keyword` mode read up to 200 R2 objects sequentially looking for substring matches. Tripped the same Workers Free CPU limit that broke the indexer.

**Fix shipped:** keyword now runs as semantic search and post-filters results to those whose snippet contains the literal query string. Slightly less precise on rare-word matches but never trips resource limits.

### Indexer.js had something pathological that resisted debugging
The original indexer.js consistently failed at exactly 3.2s with Error 1102 even with `limit=1`. We never fully isolated the cause — possibly a regex backtrack on certain content shapes, possibly something else. A clean rewrite (mirroring a proven `/one-with-parse` pattern from a test worker) worked first try.

**Lesson:** when an obscure failure resists debugging, rewrite from a proven minimal pattern rather than continuing to bisect. The skill ships the clean version.

## Recommendations for future deploys

1. Always run `00_preflight.sh` before `bootstrap_all.sh`. It catches every gotcha above.
2. Run `generate_missing_readmes.sh` or use `--auto-readme` on sync. Non-technical users won't have written READMEs and the sync rejects bundles without one.
3. After the initial sync, run `/reindex?limit=10` 10–15 times sequentially over a coffee break instead of waiting for cron.
4. Test semantic search first; it's the more reliable mode and shows off the value better than keyword.
5. If you change the embedding model in `workers/indexer.js`, you also have to delete and recreate the Vectorize index because the dimensions changed.
