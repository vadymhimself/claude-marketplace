# Query patterns — when to use which search mode

The KB Worker exposes three search modes plus optional metadata filters. Picking the right one shaves time and produces better citations.

## Modes at a glance

| Mode | Flag | Best for | Cost | Recall | Precision |
|---|---|---|---|---|---|
| Keyword | (default) | Exact terms, known phrases, schema-named tags | Lowest, ~50ms | Low — misses paraphrases | High — exact match |
| Semantic | `--semantic` | Concepts, paraphrases, fuzzy intent | ~150ms (embedding + vector search) | High — finds related text | Medium — may rank related-but-wrong above exact |
| Hybrid | `--hybrid` | Default for "I'm not sure how the bundle phrased this" | ~200ms (both runs + RRF fusion) | Highest | Highest |

## Choosing rules

**Default to keyword when** you know the exact term that should appear in the bundle:
- Insight IDs (`cl-length-u-shape`)
- Specific numbers (`134,000`, `8.13%`)
- Bundle slugs (`agency-success-course`)
- Code-like tags (`StatsRepository`, `dashroomUID` — though these are banned in published content, they're searchable internally)

**Switch to semantic when** the agent's query language might not match the bundle's:
- "wordcount" → finds "cover-letter-length"
- "pricing strategy" → finds "bid-vs-budget" insights
- "what to do when banned" → finds "Account blocked: what to do?" course lesson
- "low engagement clients" → finds "low-feedback-clients" insight

**Use hybrid when** you'd otherwise run both back-to-back. Hybrid runs both and merges results via Reciprocal Rank Fusion — top hits show up regardless of which mode caught them.

**Always combine with `--tag` when** you've already narrowed the topic:
```bash
kb_search.sh --semantic --tag boost-economics "marginal cost"
```
This says "vector-search the question, but only return hits whose frontmatter tags include boost-economics." Drastically tighter relevance.

## Common pitfalls

- **Long natural-language queries on keyword mode** — keyword treats them as substring AND match. Use semantic for sentences, keyword for terms.
- **Empty query with `--tag`** — returns ALL insights with that tag. Useful for "give me everything you have on X". Combine with the manifest's `tag_index` to discover what tags exist before searching.
- **Searching with banned terms** — the KB itself can contain DTO names like `dashroomUID`; you're allowed to search for them. Just don't paste search results verbatim into published content. Use `kb_context_for` and rephrase.

## When semantic search returns nothing

The Vectorize index is built lazily by the indexer Worker. If you search semantically and get zero hits but you know the content exists:

1. Check `last_indexed` in the manifest entry for that bundle. If it's older than the bundle's `last_modified`, the indexer hasn't caught up yet.
2. Fall back to keyword.
3. If still nothing, the file may not be in R2 yet (sync didn't run). Ping whoever owns the bundle.

## Result shape

Every search response is an array of objects with the same shape:

```json
[
  {
    "path": "2026-04-26_cover-letters-bidding/insights/01_cover_letter_length/INSIGHT.md",
    "snippet": "...In our data the reply curve is U-shaped on word count...",
    "score": 0.847,
    "bundle_slug": "2026-04-26_cover-letters-bidding",
    "tags": ["cover-letter-length", "reply-rate", "u-shape"]
  }
]
```

`score` is the relevance score (0–1, higher = better). For hybrid results, `score` is the post-fusion rank. Don't compare scores across modes — they're calibrated differently.

## Result limits

Default 10 hits per query. The Worker caps at 50. If you need more, paginate by appending `&offset=10`, `&offset=20`, etc.
