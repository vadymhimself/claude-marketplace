# Cost and sizing reality

**Should you upgrade Workers to Paid ($5/month) before bootstrapping a KB?** Almost certainly yes if your KB has more than ~100 markdown files. Here's the math behind that.

## What we measured during the GigRadar deploy

- Corpus: 127 markdown files, ~1.5 MB total of text
- Indexer: bge-large-en-v1.5 embeddings, chunked at 1,900 chars each, capped at 6 chunks per file
- Average chunks produced per file: ~1.6 (most files small, a handful large)
- Total embeddings burned: ~200 (one per chunk)
- Total neurons consumed for full backfill: roughly 7,000–9,000

We hit Workers Free's **10,000 neurons/day** cap right around file ~100 of 127 — meaning the backfill cron stalled overnight on quota and only resumed at the next UTC midnight. Upgrading to Workers Paid let us finish the rest in 30 seconds.

Net: the **practical free-tier ceiling is ~100 typical-sized markdown files for a one-time bootstrap.** After bootstrap, daily incremental updates of a few changed files cost a tiny fraction and stay well within the free tier.

## Where each Cloudflare resource costs you

| Resource | Free tier | What it covers (typical KB use) | When you'll exceed it |
|---|---|---|---|
| **Workers AI** | **10,000 neurons/day** | ~80 neurons / file × 100 files = ~8,000 neurons | **First bootstrap of a >100 file KB. This is the big one.** |
| Workers requests | 100K/day | Cron + manual reindex + every search | Almost never |
| Workers CPU time | Limited per-request on Free | Indexing CPU per-file | Fine; we cap chunks per call |
| Vectorize storage | 30M vectors | A 100K-file KB at 4 chunks each = 400K vectors. Plenty of headroom. | Maybe at ~1M files |
| Vectorize queries | 5M/month | Even active research teams query a few thousand/day | Almost never |
| R2 storage | 10 GB | Markdown is small — 100K files of typical size = ~1 GB | At ~1M files |
| R2 class A ops (write) | 1M/month | One PUT per upload | At sustained heavy turnover |
| R2 class B ops (read) | 10M/month | Reads on every search and indexer pass | Fine for typical use |
| KV reads/writes | 100K reads + 1K writes/day on free | Token auth (every search), etag cache (every index) | At ~100K queries/day |

## The decision tree

```
How many files in your initial corpus?
├── 1–80 markdown files            → Free tier handles bootstrap fine.
├── 80–150 files                   → Free tier might just stretch; cron will
│                                    finish over 1–2 UTC days. Upgrade if
│                                    you don't want to wait overnight.
└── 150+ files                     → Upgrade to Paid before bootstrapping.
                                     Saves you a weekend of cron-watching.

Once the initial bootstrap is done:
├── Daily incremental updates       → Free tier easily covers it.
└── Querying the KB                 → Free tier covers it.
```

## What Paid actually unlocks

- **Workers AI: 10,000 neurons/day → ~10× more on Paid** (Cloudflare pricing changes; check [https://developers.cloudflare.com/workers-ai/platform/pricing/](https://developers.cloudflare.com/workers-ai/platform/pricing/) for current numbers). Beyond that allowance, $0.011 per 1,000 neurons (rough — varies by model).
- **Workers CPU time: 30 seconds per request** (vs ~10ms on Free), so the indexer can chew through more files per cron tick.
- **Workers subrequest limit: 1,000 per request** (vs 50 on Free), so any future fan-out indexing wouldn't hit the cap.

## Honest cost projection — running the GigRadar KB indefinitely

| Phase | Cost / month |
|---|---|
| First-time bootstrap of 127 files (one-shot) | ~$0 (one day's free quota covers it) |
| Steady-state: ~5 files updated per week, ~50 queries/day | $0 if Free, $5 if Paid (just the base subscription, you don't actually use any paid quota) |
| Heavy-use: 100 file updates/week, 500 queries/day | Still well under Paid plan caps; effective cost ≈ $5 |
| 10× scale: 1000+ file KB, frequent updates | Estimate ~$5–15/month depending on AI usage |

**The honest answer:** Paid plan is $5/month effectively forever for typical use. The Workers/AI/Vectorize/R2 free tiers are generous enough that you almost never accumulate metered usage on top of that base. Most KB operators will pay $5/month and that's it.

## Why we hit the free-tier wall

Daily neurons replenishes at 00:00 UTC. The indexer's cron runs every 5 minutes, processing up to 5 files per tick. If the cron has work to do continuously, it eats:

```
12 ticks/hour × 5 files × ~80 neurons = ~4,800 neurons/hour
```

So the daily quota is gone in about 2 hours of continuous backfill. After that the cron keeps trying every 5 minutes but every embed call returns `error 4006 — daily allocation exhausted`. The indexer detects this and exits cleanly via the `quota_blocked` flag, but it can't make forward progress until 00:00 UTC the next day.

Steady-state (a few changed files per week) is well below the daily quota.

## Sizing your KB for the future

The hard constraint is **Vectorize storage: 30M vectors**. Each file produces 1–6 chunks, so:

- 30M / 6 = 5M files maximum at the 6-chunk-per-file cap
- 30M / 1 = 30M files if everything's small (fits in 1 chunk)
- Realistic for typical mixed content: **2M files**

For a KB to hit even 100K files, you'd be running an enterprise-scale operation. For practical purposes the system has *no upper bound* you'll hit — you'll outgrow your patience for organizing the content before you outgrow Cloudflare's limits.

## Things that scale BADLY (mitigations baked into this skill)

- **Chunk size vs token window** — `bge-large-en-v1.5` caps at 512 tokens. Chunks above ~2,000 chars hit a `3043 internal server error` on some content. Chunks are sized at 1,900 chars (well under the limit) for safety.
- **Vector ID 64-byte cap** — paths exceed it. We hash with SHA-256 first 16 chars to stay safely under.
- **Loop budget per Worker call** — capped at 5 files per `/reindex` call to keep CPU/subrequest usage predictable.
- **Cron tick interval** — every 5 minutes; tune in `03_deploy_indexer_worker.sh` if you want faster catch-up after burst uploads.

## Bottom line for the decision

Pay the $5/month if any of these are true:
- Your initial corpus is >100 files
- You can't tolerate "wait until tomorrow for indexing to finish"
- You'd rather have headroom than save $60/year

Stay on Free if all of these are true:
- Tiny corpus (under 80 files)
- Comfortable letting the cron fill the index gradually over multiple UTC days
- You enjoy figuring out free-tier optimization

**For GigRadar's actual deploy:** we did Free first, hit the wall at ~100 files, spent 4 hours debugging assuming the wall was a code bug (it was a quota), upgraded to Paid for $5, and finished the remaining 26 files in 30 seconds. In hindsight, paying $5 from the start would have saved 4 hours of confusion. Learn from us.
