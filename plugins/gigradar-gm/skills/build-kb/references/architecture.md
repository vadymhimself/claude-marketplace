# Architecture — design choices

Why each piece of the stack exists and what alternatives were rejected.

## The constraints (recap)

1. **Agents need grep-equivalent access** to a folder of research / notes
2. **Content must not live on the agent's device**
3. **Content must not live in any GitHub repo**
4. **Multiple maintainers** can populate the KB
5. **Content must remain queryable from any Claude Code session** without complex setup

## The shape

```
Local folder (transient, source of truth on disk)
  ↓ sync script (one-shot or on-update)
R2 bucket (persistent, private, encrypted)
  ↓ indexer Worker (cron, embeds new/changed files)
Vectorize index (semantic search)
  ↓
Search Worker (proxy)
  ↓ (Bearer token over HTTPS)
Consumer skill (4 bash scripts, baked-in URL + token)
  ↓
AI agent in any Claude Code session
```

Three persistent components (R2, Vectorize, two Workers) live in your Cloudflare account. The consumer skill is the only thing that gets distributed to agents — and it contains zero content, just plumbing.

## Why R2 (vs S3, GCS, Backblaze)

- **Egress is free.** S3 and GCS charge per GB egress; we hit the bucket on every search. Free egress is the difference between $5/month and $50/month at moderate usage.
- **S3-compatible API.** Standard tooling works (`aws s3 cp ...`) if you'd rather use it.
- **Same vendor as Workers / Vectorize.** Single auth surface, single billing.
- **Native R2 → Worker bindings** mean the indexer accesses R2 with zero network hop.

Rejected: Cloudflare KV (size limits — values cap at 25 MiB and we'll have larger CSVs), D1 (relational, not what we want for blob storage), private GitHub repo (violates the no-GH-repo constraint).

## Why Vectorize (vs Pinecone, Qdrant, Chroma, pgvector)

- **Free at our scale.** Up to 30M stored vectors and 5M queries/month free. We'll likely hit ~5K vectors and ~5K queries/month.
- **Sub-50ms latency** when called from a Worker in the same colo region.
- **Metadata filtering** supported — `tag in [...]`, `bundle_slug = ...` work natively. Required for the `--tag` filter in `kb_search.sh`.
- **No infra to maintain.** Pinecone is great but is a separate vendor billing relationship.
- **Built-in pairing with Workers AI** for embeddings — no separate API keys.

Rejected: Pinecone (vendor sprawl), Qdrant/Chroma/Weaviate (require hosting), pgvector (no Postgres in this stack).

## Why bge-large-en-v1.5 (vs OpenAI, vs other open models)

- **Free on Workers AI.** OpenAI is cheap but not free. At our scale the difference is academic, but free is free.
- **64.2 MTEB.** Within ~0.4 points of `text-embedding-3-large`'s 64.6 — undetectable difference at our corpus size.
- **1024-dim vectors.** Smaller storage in Vectorize than OpenAI's 3072-dim default.
- **Sub-50ms embed latency** when called from the same Worker.
- **Privacy.** Vectors don't leave your Cloudflare account.

Rejected: text-embedding-3-large (cost, latency, vendor sprawl), bge-base-en-v1.5 (smaller dim, marginal quality gain not worth the storage saving), nomic-embed-text-v1.5 (not on Workers AI), top-of-leaderboard models like NV-Embed-v2 (too large to host, not on Workers AI).

The model is configurable in `workers/indexer.js` — swap the `EMBED_MODEL` constant to upgrade later.

## Why two Workers (vs one)

The indexer has different needs than the search proxy:

| Concern | Indexer | Search |
|---|---|---|
| Trigger | Cron (every 5 min) | HTTP request from agent |
| CPU | High (embed every chunk) | Low (vector lookup + a few R2 reads) |
| Failure mode | Acceptable to retry | Must be fast and correct |
| Auth | Internal — not user-facing | Bearer token from consumer skill |

Splitting them means:
- The search Worker stays small and fast
- Indexing CPU usage doesn't impact search latency
- Either can be redeployed independently

Single-worker designs end up either bloated or doing everything synchronously on each request.

## Why a Worker for search (vs direct R2 access from the agent)

The agent could in principle hit R2 directly via S3-compatible API. We don't, because:

- **Auth.** We need bearer-token auth that's separate from the Cloudflare account token. The search Worker validates against a KV namespace of consumer tokens — rotation is one KV write, not regenerating Cloudflare credentials.
- **Search.** Direct R2 access doesn't include grep — every search would download every file and grep locally. The Worker does the grep server-side.
- **Walk-up-to-README.** The `/context-for` endpoint walks the path tree on the server. Doing this client-side would require multiple round trips and probably a second auth scope.
- **Privacy.** Workers KV is the only way to issue auth without going through Cloudflare account credentials. This is the standard pattern for app-level auth on Cloudflare.

## Why server-side walk-up (vs client-side)

The agent's mental model: "I found a file, I want full citation context." That's one logical operation. Doing it as multiple round trips (`get` + `walk up looking for README` + `manifest lookup`) increases latency and code complexity.

Server-side, the Worker has the R2 bucket prefix index in memory and can resolve the nearest README in microseconds. One round trip, full context. The shell script is just a curl call — no walk loop.

## Why YAML frontmatter (vs separate metadata file, vs nothing)

Frontmatter is colocated with content — when an agent edits an insight, they see the metadata in the same file. Separate metadata files drift fast (someone updates the markdown but forgets the JSON). Without metadata at all, you can't filter searches or carry citation rules forward.

YAML frontmatter is universally parseable, human-readable, and survives copy-paste between editors. It's the standard for static-site generators (Jekyll, Hugo, Astro) which means familiar tooling exists.

## Why tag-based search vs full-text-only

Tags answer different questions than full-text search:

- "Find anything about cover-letter length" — tag query
- "Find every mention of 'wordcount'" — full-text query

You want both. Frontmatter tags + Vectorize metadata filtering give you tag queries with zero extra infrastructure. The `tag_index` in MANIFEST.json gives you "what tags exist" so an agent can pick from a controlled vocabulary.

## What this skill is NOT

- **A general-purpose CMS.** Content goes in via local sync, not a web UI. If you need editorial workflows (drafts, reviews, scheduled publishes), use Notion / Airtable / Sanity / etc. and sync from there.
- **A real-time collaborative system.** Multiple maintainers can sync, but there's no merge conflict resolution. Last-write-wins on R2.
- **A search engine for billion-document corpora.** At our scale (thousands of documents) Vectorize is fine. At billions, you'd need sharding strategies the bootstrap doesn't address.
- **A replacement for source control.** R2 doesn't auto-version. If you need history, mount the local folder in git and sync from a versioned source.

## Future improvements

Things the bootstrap doesn't do but could:

- **Bundle versioning.** Re-running an analysis would create `<bundle>-v2/` and the search Worker could prefer newest by default. Currently you'd manually rename and update citations.
- **Multi-tenancy with per-bundle ACLs.** Currently the consumer token grants access to everything. Per-bundle scoping would require a token-namespace mapping in KV.
- **Real-time R2 → indexer triggers.** Currently uses cron (5-min lag). Cloudflare's R2 event notifications (when GA in your account) would cut lag to seconds.
- **Backup to a second region.** R2's durability is high but cross-region backup is good practice for irreplaceable research.
- **Web UI for browsing.** A static site that hits the search Worker would let non-CLI humans browse the KB. Not built; a 200-line React page would do it.

None of these are necessary for V1. Add them when you hit the limit.
