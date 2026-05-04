# Troubleshooting

Indexed by symptom — what you see → what's wrong → what to do. Every entry here is a real failure we hit during deploys; the fix is shipped in the current scripts.

## Pre-flight & bootstrap

### Symptom: `00_preflight.sh` says "token missing permission"
**Cause:** API token doesn't have the full set of five permissions.
**Fix:** Either edit the existing token at [https://dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) and add the missing rows, or mint a fresh one. Required scopes: Workers Scripts: Edit, Workers AI: Read, R2 Storage: Edit, Vectorize: Edit, Workers KV Storage: Edit.

### Symptom: "Please enable R2 through the Cloudflare Dashboard" (error 10042)
**Cause:** R2 isn't enabled at the account level (token permissions alone aren't enough).
**Fix:** Go to [https://dash.cloudflare.com/?to=/:account/r2/overview](https://dash.cloudflare.com/?to=/:account/r2/overview) and click "Purchase R2". The free tier is genuinely free; Cloudflare may require a payment method on file regardless. Re-run `00_preflight.sh` after.

### Symptom: "no Workers subdomain set up on this account"
**Cause:** Account has never used Workers, so no `<subdomain>.workers.dev` is reserved.
**Fix:** Visit [https://dash.cloudflare.com/?to=/:account/workers/overview](https://dash.cloudflare.com/?to=/:account/workers/overview) — you'll be prompted to pick one. Free, takes 30 seconds. Update `WORKERS_SUBDOMAIN` in `~/.kb-bootstrap.env` to match what you chose.

### Symptom: bootstrap stops at step 02 with "404 page not found" on metadata-index
**Cause:** The Vectorize metadata-index endpoint uses an underscore (`metadata_index/create`), not a dash. Older versions of this skill had the wrong path.
**Fix:** Already fixed in `02_create_vectorize_index.sh`. If you're seeing this on a fork or old copy, change `metadata-index/create` → `metadata_index/create`.

### Symptom: `bootstrap_all.sh` exits silently with non-zero code
**Cause:** A child script failed but didn't print enough context.
**Fix:** Run each numbered script directly (`01_create_r2_bucket.sh`, then `02_…`, etc.) — each one prints the API response on failure.

## Indexing

### Symptom: `/reindex` returns `errors: N` for every file with "id too long; max is 64 bytes"
**Cause:** Vector IDs exceed Vectorize's 64-byte limit. Your file paths are over 64 chars.
**Fix:** Already fixed — the indexer hashes the path with SHA-256 and uses the first 16 hex chars. If you forked the indexer and changed the ID scheme, you need to keep the result under 64 bytes.

### Symptom: `/reindex` returns `quota_blocked: true` and a "neuron" error message
**Cause:** Workers AI free tier caps at 10,000 neurons/day. Embedding ~100 markdown files burns the daily budget.
**Fix:** Two options:
1. Wait for 00:00 UTC daily reset. The cron will keep firing every 5 minutes and resume embedding once quota is fresh.
2. Upgrade Workers to the Paid plan ($5/month) at [https://dash.cloudflare.com/?to=/:account/workers/plans](https://dash.cloudflare.com/?to=/:account/workers/plans) — paid tier has dramatically higher AI quotas.

### Symptom: `/reindex` returns 503 Error 1102 ("exceeded resources")
**Cause:** Workers Free has tight CPU and subrequest limits. The original indexer.js had a pathological pattern (likely regex backtracking on certain content).
**Fix:** Already fixed — the current indexer is a clean rewrite that processes max 5 files and 4 chunks per file per call. If you still hit this, lower `DEFAULT_BATCH` in `workers/indexer.js` to 3 and redeploy.

### Symptom: `/reindex` returns 200 but `indexed: 0, errors: N` and burns through every file
**Cause:** A loop bug counted only successes against the limit, so all files got attempted in one call.
**Fix:** Already fixed — the loop now bounds by *processed* (success or fail), not just successful indexes.

### Symptom: All files show `unchanged: true` but you know the content changed
**Cause:** The indexer caches etags in KV `STATE`. If R2 returns the same etag (file actually unchanged) the indexer skips embedding.
**Fix:** If you really want to force a reindex, delete the etag entries: open Cloudflare dashboard → Workers & Pages → KV → `<KB_NAME>-indexer-state` → delete keys ending in `:etag`. Or rotate the entire bucket content (delete + re-sync).

### Symptom: Indexer cron isn't running (no progress without manual /reindex)
**Cause:** Cron *is* running — it's the silent failures. Every 5-minute tick exhausted on neuron quota errors.
**Fix:** Verify via observability: turn observability on in the Worker settings, query the events endpoint. Look for entries with the cron trigger type. If you see frequent error events, the cron is fine; the embedding side is the bottleneck (see neuron quota above).

### Symptom: Single AI embed call hangs for 30+ seconds the first time
**Cause:** Vectorize / Workers AI binding cold start. First request after deploy is slow.
**Fix:** Hit the worker 2–3 times before declaring failure. Subsequent calls drop to <1s.

## Sync

### Symptom: Sync script refuses to upload — "These bundles are missing README.md"
**Cause:** A subfolder doesn't have `README.md`. AI agents need it as the citation manual; the sync blocks to prevent silently-uncitable bundles.
**Fix:** Either edit each missing folder and add a real `README.md`, OR re-run sync with `--auto-readme`:
```bash
bash scripts/05_sync_folder_to_r2.sh /path/to/folder --auto-readme
```
That generates stub READMEs you can edit in place.

### Symptom: Sync uploads files but the indexer never processes them
**Cause:** The indexer doesn't see files at the bucket root — only files inside subfolders ("bundles"). Files dropped at root get uploaded but stay un-indexed.
**Fix:** Move any root-level content into a bundle subfolder. The convention is `<KB-root>/<bundle-slug>/<files>`.

### Symptom: Sync 403s on `PUT /accounts/.../r2/buckets/.../objects/...`
**Cause:** R2 isn't enabled OR your token lacks R2 Storage: Edit.
**Fix:** Run `00_preflight.sh` to confirm. Re-mint or edit the token if needed.

## Search

### Symptom: `/search?mode=semantic` returns empty results despite having content indexed
**Cause:** The query embedding call also costs neurons. If your daily neuron quota is exhausted from indexing, query embeds also fail.
**Fix:** Same as the indexer quota issue — wait for daily reset OR upgrade Workers Paid. Until then, semantic search is offline.

### Symptom: `/search?mode=keyword` returns zero hits but you know the term exists in the KB
**Cause:** Keyword search is implemented as semantic search filtered by literal-substring match in chunk snippets. If the literal phrase doesn't appear in the first ~200 chars of any indexed chunk, keyword returns nothing — even if it's in the body.
**Fix:** Default to semantic. Use keyword only when you're confident the term is short and prominent.

### Symptom: `/search` returns `unauthorized`
**Cause:** Bearer token is missing or invalid.
**Fix:** Check the consumer skill's `KB_TOKEN` value matches what's in the search Worker's KV `TOKENS` namespace. If you've rotated tokens, re-generate the consumer skill with the new value.

### Symptom: `/manifest` returns "no manifest yet"
**Cause:** Indexer hasn't run `/rebuild-manifest` yet. The manifest is regenerated on demand or when the indexer rewrites it.
**Fix:**
```bash
curl -X POST https://<KB_NAME>-indexer.<WORKERS_SUBDOMAIN>.workers.dev/rebuild-manifest
```

### Symptom: `/manifest` shows "fake bundles" like `README.md` or `_CONVENTIONS.md`
**Cause:** Older indexer treated root-level files as their own bundles.
**Fix:** Already fixed. If you see this, redeploy the indexer with the current `workers/indexer.js`, then `POST /rebuild-manifest`.

### Symptom: Bundle has 0 insights even though `insights/` folder is full
**Cause:** None of the `INSIGHT.md` files have YAML frontmatter, so the manifest's per-insight metadata is empty (insights are still searchable; they just don't show up in the bundle's `insights[]` array in the manifest).
**Fix:** Add frontmatter to your INSIGHT.md files (schema in `references/architecture.md`). Backfill is lazy — do it as you touch each file.

## Authentication & tokens

### Symptom: Direct Cloudflare API calls return `401 — Authentication error` (code 10000)
**Cause:** API token is wrong or has wrong scope.
**Fix:** Run `00_preflight.sh` — it tests each individual scope and tells you exactly which is missing.

### Symptom: Consumer token works initially, then suddenly stops working
**Cause:** Either the token was rotated, or its KV entry was deleted, or you copied the wrong token from `.token` file.
**Fix:** Check `<KB-skill>/.token` matches what's in the KV namespace `<KB_NAME>-consumer-tokens`. If they diverge, regenerate the consumer skill with `06_generate_consumer_skill.sh`.

## Costs

### Symptom: Cloudflare bill is non-zero
**Cause:** You exceeded the free tier on something. Most likely Workers requests (>100K/day) or Workers AI neurons (>10K/day on free, much higher on paid).
**Fix:** Check the Cloudflare dashboard → Billing → Usage. If a single resource is way over limit, you may have a runaway loop. Inspect Worker logs for evidence and either pause (don't run cron) or fix the loop.

### Symptom: Cloudflare asks for a payment method to enable R2
**Cause:** This is normal even for the free tier. Cloudflare requires a payment method on file before R2 access is granted.
**Fix:** Add the card. You won't be charged unless you exceed the free 10 GB / 1M ops limits.

## Operational

### Symptom: I want to start over from scratch
**Fix:**
```bash
bash scripts/teardown.sh
```
Deletes the bucket, the Vectorize index, the Workers, and the KV namespaces. Irreversible. (Note: this script is documented but may not be in the repo yet — check `scripts/`. If missing, delete via Cloudflare dashboard manually: R2 buckets → Vectorize indexes → Workers & Pages → KV namespaces.)

### Symptom: I changed the embedding model and existing vectors are wrong
**Cause:** Different embedding models produce different vector dimensions. A `bge-large` index can't store `text-embedding-3-large` vectors (different dim) and even same-dim swaps mean similarity is meaningless across models.
**Fix:** Delete the Vectorize index, recreate it with the new model's dimensions, delete all etag entries in KV STATE so the indexer re-embeds everything.

### Symptom: I want to share KB access with a teammate without giving them my Cloudflare account
**Fix:** Hand them the generated `knowledge-base/` folder. It contains the consumer URL + read-only token baked in. They can install it in any plugin's skills directory and query the same KB.

### Symptom: I want to rotate a token I gave a teammate
**Fix:** Re-run `04_deploy_search_worker.sh` to mint a fresh token (writes a new entry into KV TOKENS), then delete the old token's entry from KV TOKENS via the Cloudflare dashboard. The old skill stops working immediately; distribute the new one.

## When in doubt

- **Worker logs:** Cloudflare dashboard → Workers & Pages → `<worker-name>` → Logs. With observability enabled, you can query historic events via the API.
- **Indexer status:** `GET https://<KB_NAME>-indexer.<WORKERS_SUBDOMAIN>.workers.dev/status` returns the current MANIFEST.json
- **Vector count:** `GET /accounts/<account>/vectorize/v2/indexes/<KB_NAME>/info` returns `vectorCount` and `processedUpToDatetime`
- **R2 contents:** Cloudflare dashboard → R2 → `<KB_NAME>` shows every uploaded object with size and timestamps

If a symptom isn't covered here, check `references/known-issues.md` for the chronological session log of what we hit during the GigRadar deploy — it's a different cut of the same data.
