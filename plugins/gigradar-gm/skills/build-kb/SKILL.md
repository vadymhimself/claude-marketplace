---
name: build-kb
description: >-
  Bootstrap a private, grep-able, vector-indexed knowledge base on Cloudflare in one shot — R2 bucket for content, Vectorize index for semantic search, indexer Worker that auto-embeds on R2 changes, search Worker that exposes search/get/manifest/context-for endpoints, plus a generated consumer skill (with baked-in token) you drop into any Claude Code plugin to query the KB. Use this skill whenever a team or individual wants to set up a private folder-based knowledge base that AI agents can query via grep-style API but without storing content on disk or in GitHub. Trigger phrases include "build a knowledge base", "set up KB", "deploy a private vector DB", "make my research folder queryable by agents", "bootstrap KB on Cloudflare", "private RAG over folder structure". Reach for this skill any time the goal is "private folder → queryable by AI agents in one shot".
---

# /build-kb — bootstrap a private knowledge-base-as-a-service

This skill sets up everything needed to turn a local folder of research / notes / transcripts into a private, queryable knowledge base that AI agents can reach from anywhere — without the content ever living on a single device or in a GitHub repo.

The output is two things:
1. A **deployed Cloudflare stack** (R2 bucket + Vectorize index + 2 Workers) that hosts and serves your content
2. A **generated consumer skill** with a baked-in URL + auth token that you (or anyone you share it with) drop into a Claude Code plugin to query the KB

This skill is shareable: hand it to another team and they can stand up their own isolated stack in their own Cloudflare account by following the same steps. No data crosses tenants.

---

## Architecture

```
┌──────────────────────────────────────┐
│ Local folder (your research)         │
│ <root>/                              │
│   ├── <bundle-slug>/                 │
│   │   ├── README.md                  │
│   │   ├── INSIGHTS.md                │
│   │   └── insights/NN_*/INSIGHT.md   │
│   └── <other-bundles>/...            │
└─────────────┬────────────────────────┘
              │ scripts/05_sync_folder_to_r2.sh (one-shot or on-update)
              ↓
┌──────────────────────────────────────┐
│ Cloudflare R2: <kb-name>             │   private storage, no public read
│   /<bundle-slug>/...                 │
│   /MANIFEST.json (auto-generated)    │
└─────┬─────────────────────────┬──────┘
      │                          │
      │ event: object change     │ S3 read
      ↓                          │
┌────────────────────┐    ┌─────┴────────────────────┐
│ Indexer Worker     │    │ Search Worker            │
│  - chunks files    │    │  GET /search             │
│  - embeds via      │    │  GET /get                │
│    Workers AI BGE  │    │  GET /context-for        │
│  - upserts to      │    │  GET /manifest           │
│    Vectorize       │    │                          │
└────────┬───────────┘    │  Auth: Bearer <token>    │
         │                └──────────┬───────────────┘
         ↓                            │
┌──────────────────────────────────────┐
│ Cloudflare Vectorize: <kb-name>      │
│   1024-dim BGE embeddings            │
│   Metadata: path, tags, frontmatter  │
└──────────────────────────────────────┘
                                       │
                                       ↓
                     ┌──────────────────────────────┐
                     │ Generated consumer skill     │
                     │  - kb_search.sh              │
                     │  - kb_get.sh                 │
                     │  - kb_context_for.sh         │
                     │  - kb_manifest.sh            │
                     │ Drop into any plugin's       │
                     │ skills/ folder. Done.        │
                     └──────────────────────────────┘
```

The whole stack runs in your Cloudflare account, gated by one API token. R2 storage at our scale (hundreds of MB) is free. Workers AI (BGE embeddings) is free up to 10K neurons/day, which covers indexing and querying for typical KBs. Vectorize storage is ~$0.04/M vectors/month.

---

## Step 0 — Run the pre-flight check FIRST

Before doing anything else, run:

```bash
bash scripts/00_preflight.sh
```

This validates: token works, account ID is correct, R2 is enabled, Workers subdomain is set up, all five permissions are present, local tools (curl/python3/file/find/xargs) are installed. **Each failure mode comes with the exact dashboard URL to fix it.** Re-run after every fix until everything's green.

If you don't have a token yet, the preflight will tell you and you'll skip ahead to:

## Step 1 — Get a Cloudflare API token

The full step-by-step (including screenshots-style guidance for non-technical users) is in `README.md` — it covers token minting, R2 enable, Workers subdomain creation, account ID lookup, and Windows WSL setup.

The 60-second version: mint a custom token at [https://dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) with these five permissions:

| Scope | Level |
|---|---|
| Account → Workers Scripts | **Edit** |
| Account → Workers AI | **Read** |
| Account → R2 Storage | **Edit** |
| Account → Vectorize | **Edit** |
| Account → Workers KV Storage | **Edit** |

Then save your config:

```bash
cat > ~/.kb-bootstrap.env <<EOF
CLOUDFLARE_API_TOKEN=<paste-token-here>
CLOUDFLARE_ACCOUNT_ID=<your-account-id>
KB_NAME=<your-kb-name>
KB_REGION=auto
WORKERS_SUBDOMAIN=<your-workers-subdomain>
EOF
chmod 600 ~/.kb-bootstrap.env
```

**Then re-run `00_preflight.sh`** to validate everything before bootstrap. The preflight will fail loudly with the exact dashboard URLs to fix anything missing.

---

## Step 1 — Bootstrap

```bash
source ~/.kb-bootstrap.env
bash scripts/bootstrap_all.sh
```

This runs every step in order:

| Step | Script | What it does |
|---|---|---|
| 1 | `01_create_r2_bucket.sh` | Creates R2 bucket named `$KB_NAME`, region `$KB_REGION` |
| 2 | `02_create_vectorize_index.sh` | Creates a Vectorize index named `$KB_NAME` with BGE dimensions (1024) |
| 3 | `03_deploy_indexer_worker.sh` | Deploys the indexer Worker (cron-triggered every 5 min) |
| 4 | `04_deploy_search_worker.sh` | Deploys the search Worker, mints a read-only consumer token, prints the public URL |
| 5 | `06_generate_consumer_skill.sh` | Generates the `knowledge-base/` consumer skill with the URL + token baked in |

After bootstrap, the script prints:
- The **search Worker URL** (`https://<KB_NAME>.<WORKERS_SUBDOMAIN>.workers.dev`)
- The **read-only consumer token** (saved into the generated skill)
- The path to the **generated consumer skill** ready to drop into any plugin

If anything fails midway, fix the issue and re-run `bootstrap_all.sh`. Each individual step is idempotent — re-creating a bucket that already exists returns the existing one.

---

## Step 2 — Make sure every bundle has a README

The KB is organised as **bundles** (subfolders of your KB root). Each bundle MUST have a `README.md` at its root — that's the citation manual AI agents read every time they cite content from that bundle.

If any bundle is missing one, generate stubs:

```bash
bash scripts/generate_missing_readmes.sh /path/to/your/folder
```

This walks every direct subfolder, finds the ones without a README.md, and writes a stub with bracketed sections to fill in (what is this, how to cite it, what's off-limits, etc.). Open each stub, fill it in (~5 minutes per bundle), then proceed.

The sync script in Step 3 will block if any bundle is still missing a README — pass `--auto-readme` to auto-generate stubs at sync time, but you'll still want to edit them before they go live.

## Step 3 — Sync your folder

```bash
bash scripts/05_sync_folder_to_r2.sh /path/to/your/folder
```

Uploads every `.md` / `.txt` / `.json` file. Skips dotfiles and binaries. Re-running is safe — etag comparison means only changed files re-upload.

The indexer worker picks up new objects within 5 minutes and embeds them. Watch progress via:

```bash
bash scripts/poll_indexer_status.sh
```

For the first sync of a large folder (100+ files), the initial indexing chews through ~5 files per minute on the cron. You can also trigger manually:

```bash
curl -X POST https://<KB_NAME>-indexer.<WORKERS_SUBDOMAIN>.workers.dev/reindex?limit=10
```

After everything's indexed, also rebuild the manifest so bundle metadata reflects the latest content:

```bash
curl -X POST https://<KB_NAME>-indexer.<WORKERS_SUBDOMAIN>.workers.dev/rebuild-manifest
```

---

## Step 3 — Install the consumer skill

```bash
ls generated/knowledge-base/
# SKILL.md, scripts/, references/

# Drop it into your Claude Code plugin
cp -r generated/knowledge-base/ /path/to/your/plugin/skills/

# OR package it as a standalone .skill
zip -r knowledge-base.skill generated/knowledge-base/
```

The generated skill is pre-configured to talk to YOUR Worker URL with YOUR token. Other people can use it without knowing how to mint Cloudflare tokens — they just install the skill.

If you want to share read access with another team member, give them the generated skill file directly. If you want to revoke access, regenerate the token via `scripts/rotate_consumer_token.sh` and redistribute.

---

## Operations

### Adding new content to the KB

Just drop files into your local folder and re-run `05_sync_folder_to_r2.sh`. The indexer auto-picks up changes on the next cron tick. No need to redeploy Workers, no need to update the consumer skill.

### Rotating the consumer token

```bash
bash scripts/rotate_consumer_token.sh
```

Mints a fresh token, updates the search Worker's token list, regenerates the consumer skill with the new token. Distribute the new skill to consumers; the old token stops working immediately.

### Watching costs

R2 / Vectorize / Workers AI all expose usage in the Cloudflare dashboard. For typical KBs (under 10K insights, under 1M tokens/month of queries) you should be well under the free-tier ceilings of all three. The dashboard shows daily usage so you can spot runaway costs early.

### Multi-tenancy

This skill is single-tenant — one bootstrap = one KB. If multiple teams in the same company want isolated KBs, run the bootstrap multiple times with different `KB_NAME` values. Each gets its own bucket, index, Workers, and consumer skill.

---

## What the indexer Worker does

The source is at `workers/indexer.js`. Brief summary:

1. Triggered on R2 object create/update events (or every 5 min via cron, depending on Cloudflare features at deploy time)
2. For each new/changed object:
   - Fetches the file content from R2
   - Parses YAML frontmatter (if present)
   - Chunks the content into 800-token windows with 100-token overlap (configurable in `workers/indexer.js`)
   - Calls Workers AI with `@cf/baai/bge-large-en-v1.5` to embed each chunk
   - Upserts vectors to Vectorize with metadata: `{path, bundle_slug, tags, sample_size, effect_size, confidence, public_safe}`
3. Maintains a tiny D1 (SQLite) row per file tracking `last_indexed` for the manifest

The MANIFEST.json is regenerated by the indexer whenever any bundle changes, written back to R2 at the bucket root.

## What the search Worker does

The source is at `workers/search.js`. Routes:

- `GET /search?q=...&mode=keyword|semantic|hybrid&tag=...` — returns hit list
- `GET /get?path=...` — streams a single file from R2
- `GET /context-for?path=...` — file + nearest README (server walks up R2 prefix tree) + manifest entry
- `GET /manifest` — returns MANIFEST.json from R2

Auth: `Authorization: Bearer <consumer-token>` on every route. Tokens are stored in a Workers KV namespace; the rotation script just adds/removes entries there.

---

## Customization

The defaults are tuned for the GigRadar use case (research bundles + course transcripts, English, ~110 insights per bundle). To adapt:

- **Change embedding model:** edit `workers/indexer.js`, swap the model string. Re-run `02_create_vectorize_index.sh` if dimensions change.
- **Change chunk size:** edit `CHUNK_SIZE_TOKENS` in `workers/indexer.js`.
- **Add custom metadata fields:** add them to the `extract_metadata()` function in the indexer; they become filterable in `/search?...&meta_X=Y`.
- **Restrict which file types get indexed:** edit `INDEXABLE_EXTENSIONS` at the top of `workers/indexer.js` (default: `.md`, `.txt`, `.json`).

---

## When this skill is NOT the right tool

- **You want public-readable docs** — use a static site generator (Astro, Docusaurus). This skill is for *private* content.
- **You're indexing >1M chunks** — Vectorize's free tier won't cover you; consider Pinecone or self-hosted Qdrant.
- **You need ACLs per file** — the consumer token is all-or-nothing. For per-file ACLs you'd need an auth layer between the search Worker and R2.
- **You need real-time consistency** — the indexer is eventually consistent (5-min cron). For strict-consistency RAG, look at services like Turbopuffer.

---

## Files in this skill

```
build-kb/
├── SKILL.md                      # this file
├── README.md                     # alt entry point with token instructions for non-AI users
├── scripts/
│   ├── 01_create_r2_bucket.sh
│   ├── 02_create_vectorize_index.sh
│   ├── 03_deploy_indexer_worker.sh
│   ├── 04_deploy_search_worker.sh
│   ├── 05_sync_folder_to_r2.sh
│   ├── 06_generate_consumer_skill.sh
│   ├── bootstrap_all.sh          # runs 01-04 + 06 in sequence
│   ├── poll_indexer_status.sh
│   └── rotate_consumer_token.sh
├── workers/
│   ├── indexer.js                # the Worker that embeds + upserts
│   └── search.js                 # the Worker that serves queries
├── templates/
│   └── consumer-skill-template/  # skeleton populated by 06_generate_consumer_skill.sh
└── references/
    └── architecture.md           # deeper dive on design choices
```

Read `references/architecture.md` if you want to understand WHY each piece exists and what it does in detail. The SKILL.md you're reading is the operational guide.

## When something breaks

Symptom-first lookup table → cause → exact fix:

→ **`references/troubleshooting.md`** ← go here first when anything fails

It covers every error mode we hit during the GigRadar deploy: token permissions, R2 enable, Vector ID 64-byte cap, Workers Free CPU/subrequest limits, Workers AI 10K-neuron daily quota, indexer cron silent failures, semantic vs keyword search edge cases, manifest "fake bundle" entries, token rotation, "I want to start over." Each entry is `Symptom: …` → `Cause: …` → `Fix: …` with the exact commands or dashboard URLs.

For the chronological session log of what we discovered building this (different cut of the same data, organized by *when* we hit each issue rather than *what symptom*), see `references/known-issues.md`.

## Decide if you need Workers Paid before you start

`references/cost-and-sizing.md` covers it: KB size → expected time-to-indexed, what each Cloudflare resource costs, and a decision tree for "Free vs $5/mo Paid". The honest summary: corpus under 80 files, Free works; over 100 files, pay the $5 from day one and save yourself a day of cron-watching.
