---
name: knowledge-base
description: >-
  Query a private Knowledge Base — a folder tree of plain markdown / text files hosted on Cloudflare R2 and indexed in Vectorize. Acts like grep over a private repo but every call routes through a Cloudflare Worker so content never lives on the agent's device or in any GitHub repo. Use this skill any time an agent needs first-party content with citation lineage — defensible numbers, phrase patterns, video transcripts, case-study findings — for an article, slide, audit, report, or any synthesis task. Trigger phrases include "search the KB", "find content about", "look up in the knowledge base", "what does our research say about", "cite the bundle on…". Reach for this skill any time a domain-agnostic lookup over private content is useful; it replaces local grep over the source folder and adds semantic match for conceptual queries.
---

# /knowledge-base

Query a private Knowledge Base via small bash scripts. The KB is a folder tree of bundles — each bundle is a self-contained body of work with a `README.md` describing how to cite from it. Bundles can hold any markdown / text / JSON content; any number of bundles can coexist in the same KB.

The indexer treats every file as plain prose. **No YAML frontmatter is required, expected, or special-cased.** Authors write whatever markdown they want and it gets indexed as-is.

This skill is **domain-agnostic** — it makes no assumptions about what kind of content the KB holds or who is calling it.

---

## ⚠️ Step 0 — Prerequisite check (ALWAYS run before any operation)

Before invoking any of `kb_manifest`, `kb_search`, `kb_get`, `kb_context_for`, `kb_put`, `kb_delete`, or `kb_reindex`, the agent **MUST** verify the user has KB credentials configured. The check is one line:

```bash
test -s "$(dirname scripts/kb_manifest.sh)/.kb-config" && grep -q '^KB_TOKEN=' "$(dirname scripts/kb_manifest.sh)/.kb-config"
```

Or in plain terms: `<skill-dir>/.kb-config` must exist and contain at least `KB_WORKER_URL` and `KB_TOKEN`.

**If the file is missing, empty, or incomplete — STOP. Do NOT proceed with any KB operation. Instead:**

1. Tell the user, in plain language: "I can't reach the Knowledge Base yet — I need your KB credentials. If you don't have them, contact your KB / GigRadar admin to get provisioned (you should receive an email with a paste-ready setup snippet)."
2. Ask the user to paste the snippet from their provisioning email. It looks like:
   ```bash
   KB_WORKER_URL="https://…workers.dev" KB_TOKEN="kb_…" KB_WRITE_TOKEN="kb_write_…" bash scripts/kb_setup.sh
   ```
   (`KB_WRITE_TOKEN` is optional — read-only is fine for search/get.)
3. Run that snippet on the user's behalf, OR have them run it themselves. `kb_setup.sh` writes `.kb-config` (chmod 600) and runs a smoke-test against `/manifest` to confirm the token works before declaring success.
4. Only after `kb_setup.sh` exits 0 should you continue with the original task.

**Never silently fail on missing config.** Every script in `scripts/` errors loudly if `.kb-config` is missing or incomplete (e.g., `KB_TOKEN: parameter null or not set`). If you see that error, you skipped Step 0 — back up and run the prereq check first.

---

## Why this skill exists

Three problems a local-grep version has:
- The KB stays on a single user's device — other team members and remote agents can't see it.
- Sensitive content (customer numbers, internal names, raw evidence) can't safely live in a GitHub repo.
- Cross-bundle topic search needs semantic match (e.g. "wordcount" → "cover letter length"), not just literal grep.

This skill solves all three: content lives in private R2, queries route through a private Worker, vectors live in Cloudflare Vectorize. Agents call bash scripts and get grep-equivalent results without any data ever touching their disk.

---

## Configuration

Every script in `scripts/` sources `<skill-dir>/.kb-config` on startup. That file holds three lines:

```bash
KB_WORKER_URL="https://<your-kb>-search.<your-subdomain>.workers.dev"
KB_TOKEN="kb_..."           # read-only token (search, get, context-for, manifest)
KB_WRITE_TOKEN="kb_write_..." # write-scoped token (put, delete, reindex) — optional
```

This file is created either by the `build-kb` bootstrap (when you self-host) or by `scripts/kb_setup.sh` (when an admin provisions your account). **It's the one place to update tokens or rotate URLs** — no string substitution across script files. If `KB_WRITE_TOKEN` is empty or missing, write operations refuse to run; read operations still work.

To create or refresh `.kb-config`, run:

```bash
# From the snippet your admin sent in your provisioning email
KB_WORKER_URL="https://…workers.dev" KB_TOKEN="kb_…" KB_WRITE_TOKEN="kb_write_…" \
  bash scripts/kb_setup.sh

# Or pass positionally
bash scripts/kb_setup.sh "https://…workers.dev" "kb_…" "kb_write_…"
```

`kb_setup.sh` validates the URL/token shape, writes `.kb-config` with `chmod 600`, and smoke-tests `/manifest` to confirm the token works before declaring success.

If a call returns `401`, the read token rotated — re-run `kb_setup.sh` with the new token. If `/put` returns `403`, you don't have a write token (or the one in `.kb-config` is stale).

---

## The four read operations

### 1. Survey what's available — `kb_manifest.sh`

```bash
bash scripts/kb_manifest.sh
```

Returns the current `MANIFEST.json`. For each bundle: `slug`, `tagline` (first H1 / first non-empty line of the bundle README), `n_files`, file-type counts (`n_md`, `n_csv`, `n_json`, `n_txt`, `n_other`), `last_modified`, and a flat `files[]` array of every file path in the bundle.

This is the cheapest call. Run it on every research pass — it's the canonical "what's in scope" answer in one round trip.

### 2. Find content by query — `kb_search.sh`

```bash
# Keyword (default) — literal substring match across FULL file content (R2 scan)
bash scripts/kb_search.sh "exact phrase"

# Semantic — vector match against indexed chunks (handles paraphrasing)
bash scripts/kb_search.sh --semantic "what I'm looking for"

# Hybrid — both, fused with Reciprocal Rank Fusion
bash scripts/kb_search.sh --hybrid "query"
```

Returns up to 10 hits ordered by relevance. Each hit is `{path, snippet, score, bundle_slug, ...}`. The `path` is what you pass to `kb_get.sh` or `kb_context_for.sh`.

**When to use which:**
- **`--semantic`** — for conceptual questions where you don't know the exact phrasing. Vectors handle synonyms and paraphrasing.
- **Keyword (default)** — for known phrases, proper nouns, code identifiers, or any literal string you expect to appear in the text. Scans full file content via R2, so it's correct (not snippet-limited like the old hybrid).
- **`--hybrid`** — when you don't know whether the term is literal or conceptual. Runs both and fuses with RRF.

### 3. Fetch one file with citation context — `kb_context_for.sh`

```bash
bash scripts/kb_context_for.sh "<bundle-slug>/path/to/file.md"
```

Returns:

```json
{
  "file": { "path": "...", "content": "..." },
  "bundle_readme": { "path": "<bundle-slug>/README.md", "content": "..." },
  "bundle_slug": "<bundle-slug>",
  "manifest_entry": { ... }
}
```

The canonical "I found something, give me everything I need to cite it" call — file body + bundle README (citation manual) + bundle's manifest entry, in one round trip.

### 4. Fetch one file raw — `kb_get.sh`

```bash
bash scripts/kb_get.sh "<bundle-slug>/path/to/file.md" > /tmp/file.md
```

Streams the file body (text/plain) to stdout. Use when you already know the path and don't need the citation envelope.

---

## Adding or updating content (write operations)

Use these when your task is to put new findings into the KB rather than just read from it. **All three require `KB_WRITE_TOKEN`.**

### Upload a new file or overwrite an existing one — `kb_put.sh`

```bash
# Upload a local file to a path inside the KB
bash scripts/kb_put.sh ./local-finding.md research-2026-q2/insights/01_thing/INSIGHT.md

# Or pipe content from stdin
cat report.md | bash scripts/kb_put.sh - my-bundle/REPORT.md
```

**Path conventions** (the indexer expects this layout — break it and content silently won't surface):
- Every file lives inside a **bundle folder** at the KB root. Files at the root itself are NOT indexed.
- Every bundle MUST have a `<bundle-slug>/README.md` (the citation manual). Upload it first when you create a new bundle.
- Insight folders go under `<bundle-slug>/insights/<folder>/INSIGHT.md` if you're following the standard insight pattern; supporting files (CSV / JSON / TXT) sit alongside.
- Indexable file types: `.md`, `.txt`, `.json`. Anything else uploads but stays un-indexed.
- Max upload size: 5 MB per file.

After upload, the indexer's cron picks up the change within ~5 minutes. Run `kb_reindex.sh` if you need it searchable immediately.

### Delete a file — `kb_delete.sh`

```bash
bash scripts/kb_delete.sh "<bundle-slug>/path/to/old-file.md"
```

The R2 object is gone immediately; vector entries are cleaned up on the next indexer pass (so re-run `kb_reindex.sh` if a stale snippet keeps appearing in search).

### Apply changes immediately — `kb_reindex.sh`

```bash
bash scripts/kb_reindex.sh                # default batch
bash scripts/kb_reindex.sh --limit 50     # process up to 50 changed files
```

The cron runs every 5 minutes regardless — call this only when you can't wait. The response shows how many files were indexed / skipped / errored.

### Typical "ship a new finding" flow

```bash
# 1. (First time only) — establish the bundle by uploading its README
bash scripts/kb_put.sh ./README.md research-2026-q2/README.md

# 2. Upload the actual finding(s)
bash scripts/kb_put.sh ./finding.md research-2026-q2/insights/01_thing/INSIGHT.md
bash scripts/kb_put.sh ./evidence.csv research-2026-q2/insights/01_thing/evidence.csv

# 3. Trigger immediate indexing (skip if you can wait 5 min)
bash scripts/kb_reindex.sh

# 4. Verify it's searchable
bash scripts/kb_search.sh --semantic "summary of the finding"
```

### What NOT to upload

- **No YAML frontmatter is required, expected, or special-cased** — write plain markdown.
- Don't upload binary content (images, PDFs, parquet) expecting it to be indexed — the indexer ignores anything not in `.md/.txt/.json`.
- Never overwrite `MANIFEST.json` directly — the indexer regenerates it.

---

## What to do AFTER `kb_search` returns matches

A search hit is a pointer, not evidence. Before citing anything, do these in order:

1. **Don't cite from the snippet.** It's a 220-char sliver — fine for ranking, useless as evidence. Treat the snippet as "is this hit relevant?" not "what's the claim?"
2. **Pick paths, not snippets.** Deep-dive files (e.g. `<bundle>/insights/<folder>/INSIGHT.md`) are authoritative; index files (`INSIGHTS.md`, `TOP_20.md`, `PLAYBOOK.md`) are for orientation — drill into the linked file for the real number.
3. **Fetch the full file body before citing.** Call `kb_context_for.sh <path>` for the top 1–3 paths you intend to cite — the response's `file.content` field is the **entire markdown body** of the match (every table, caveat, sample size, and footnote). The same call also returns the bundle's `README.md` (citation manual) and manifest entry, in one round trip. If you only need the body and already know the citation rules, `kb_get.sh <path>` returns the raw file content alone.
4. **Re-read the bundle README every time you cite from a bundle.** Different bundles have different rules for what's publishable, who to attribute to, and what leaks. Don't cache assumptions across bundles or sessions.
5. **Quote evidence, not summary.** When citing a stat, pull the actual number / table / quote from the file — not the summary line you skimmed in the snippet.
6. **Log the citation with the full bundle-relative path:** `[kb:<bundle-slug>/path/to/file.md] <one-line claim>` so reviewers can trace the source.
7. **If hits are weak**, broaden — try `--hybrid`, paraphrase the query, or rephrase semantically. Do not manufacture a claim from a thin match.

---

## Generic recipes

### A — "Find first-party evidence on topic X"

```bash
bash scripts/kb_manifest.sh                       # see what bundles exist
bash scripts/kb_search.sh --semantic "X"          # find candidate hits
bash scripts/kb_context_for.sh "<top-hit-path>"   # pull file + README in one call
```

### B — "Find every file that mentions a literal phrase"

```bash
bash scripts/kb_search.sh "exact phrase"
# Scans actual file contents in R2 — not just the indexed chunk snippet.
```

### C — "Survey one specific bundle"

```bash
bash scripts/kb_manifest.sh | jq '.bundles[] | select(.slug == "<slug>")'
# Inspect tagline, n_files, files[]
```

---

## Citation lineage

Every cited fact should be logged with the full bundle-relative path:

```
[kb:<bundle-slug>/path/to/file.md] <one-line summary of the cited claim>
```

This lets a reviewer (or a future agent) trace the claim back to the exact source. `kb_context_for.sh` returns the path in its response so it's easy to grab. Always re-read the bundle's README from the response before publishing — different bundles have different citation rules.

---

## Bundle taxonomy

The KB is a flat folder of bundles. Each bundle is a top-level folder with a `README.md` (the citation manual) plus arbitrary markdown / text inside. Run `kb_manifest.sh` to see the live list — never hard-code bundle slugs.

For the bundle-creation contract (folder layout, README requirements, indexer behaviour), see `references/kb-conventions.md`.

---

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` on every call | Token rotated | Update `KB_TOKEN` in the config block above |
| `404` on `kb_get` / `kb_context_for` | Path doesn't exist in R2 | Re-run `kb_search` to find the actual path |
| Search returns nothing for a known phrase | Indexer hasn't picked up new content yet | Wait 5 min for the cron tick |
| Semantic search returns nothing | Workers AI daily neuron quota exhausted (Free tier ~10K/day) | Wait for 00:00 UTC reset, or upgrade to Workers Paid |
| Keyword search slow | Bundle is huge (thousands of files) | Use semantic for broad queries; keyword is for specific phrases |

---

## When to load which reference

| Situation | Reference |
|---|---|
| Choosing keyword vs semantic vs hybrid | `references/query-patterns.md` |
| Adding new content to the KB / understanding the bundle contract | `references/kb-conventions.md` |
| Bootstrapping a fresh KB stack from scratch | The standalone `build-kb` skill |
