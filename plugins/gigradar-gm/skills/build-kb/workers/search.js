/**
 * KB Search Worker.
 * Routes:
 *   GET /manifest                         → MANIFEST.json
 *   GET /get?path=...                     → file body (text/plain)
 *   GET /context-for?path=...             → {file, bundle_readme, bundle_slug, manifest_entry}
 *   GET /search?q=...&mode=keyword|semantic|hybrid&offset=N&limit=N
 *
 * Two search modes (no frontmatter, no tags, no tag_index):
 *   - keyword:  literal substring match across full file content (R2 scan)
 *   - semantic: vector match against indexed chunks
 *   - hybrid:   semantic + keyword, fused with Reciprocal Rank Fusion
 *
 * Auth: Authorization: Bearer <token>. Tokens live in env.TOKENS (KV; key = token, val = "active").
 *
 * Bindings:
 *   env.KB_BUCKET  — R2 bucket
 *   env.VECTOR     — Vectorize index
 *   env.AI         — Workers AI
 *   env.TOKENS     — KV namespace
 */

const EMBED_MODEL = "@cf/baai/bge-large-en-v1.5";
const INDEXABLE_EXT = /\.(md|txt|json)$/i;
const SNIPPET_LEN = 220;

// Keyword scan budgets (Workers Paid CPU limit is ~30s, but we want fast).
const KEYWORD_MAX_FILES = 1000;      // upper bound on files we'll fetch in one query
const KEYWORD_MAX_HITS = 50;         // stop after this many literal matches

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Token roles in KV TOKENS: "active" = read-only, "write" = read+write.
// `requireWrite=true` rejects read-only tokens at write endpoints.
async function authorize(req, env, requireWrite = false) {
  const auth = req.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  if (!token) return false;
  const role = await env.TOKENS.get(token);
  if (!role) return false;
  if (requireWrite) return role === "write";
  return role === "active" || role === "write";
}

function contentTypeFor(path) {
  if (path.endsWith(".md"))   return "text/markdown; charset=utf-8";
  if (path.endsWith(".json")) return "application/json; charset=utf-8";
  if (path.endsWith(".csv"))  return "text/csv; charset=utf-8";
  if (path.endsWith(".txt"))  return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

function safePath(p) {
  if (!p) return null;
  if (p.startsWith("/")) return null;
  if (p.includes("..")) return null;
  if (p === "MANIFEST.json") return null;  // managed by indexer
  return p;
}

async function readJsonObject(env, key) {
  const obj = await env.KB_BUCKET.get(key);
  if (!obj) return null;
  return JSON.parse(await obj.text());
}

async function readTextObject(env, key) {
  const obj = await env.KB_BUCKET.get(key);
  if (!obj) return null;
  return await obj.text();
}

function snippetAround(text, q, len = SNIPPET_LEN) {
  const lower = text.toLowerCase();
  const idx = lower.indexOf(q.toLowerCase());
  if (idx === -1) return text.slice(0, len).replace(/\s+/g, " ").trim();
  const start = Math.max(0, idx - 60);
  const end = Math.min(text.length, start + len);
  const left = start > 0 ? "…" : "";
  const right = end < text.length ? "…" : "";
  return (left + text.slice(start, end) + right).replace(/\s+/g, " ").trim();
}

// ─── /manifest ─────────────────────────────────────────────────────────
async function handleManifest(env) {
  const m = await readJsonObject(env, "MANIFEST.json");
  if (!m) return json({ error: "no manifest yet — indexer hasn't run" }, 503);
  return json(m);
}

// ─── /get ──────────────────────────────────────────────────────────────
async function handleGet(env, url) {
  const path = url.searchParams.get("path");
  if (!path) return json({ error: "path required" }, 400);
  const body = await readTextObject(env, path);
  if (body === null) return json({ error: "not found" }, 404);
  return new Response(body, { headers: { "content-type": "text/plain; charset=utf-8" } });
}

// ─── /context-for — file + bundle README + manifest entry ─────────────
async function handleContextFor(env, url) {
  const path = url.searchParams.get("path");
  if (!path) return json({ error: "path required" }, 400);

  const fileText = await readTextObject(env, path);
  if (fileText === null) return json({ error: "not found" }, 404);

  const bundleSlug = path.split("/").filter(Boolean)[0] || "";
  const readmePath = `${bundleSlug}/README.md`;
  const readmeText = bundleSlug ? await readTextObject(env, readmePath) : null;

  const manifest = await readJsonObject(env, "MANIFEST.json");
  const manifestEntry = manifest?.bundles?.find(b => b.slug === bundleSlug) || null;

  return json({
    file: { path, content: fileText },
    bundle_readme: readmeText ? { path: readmePath, content: readmeText } : null,
    bundle_slug: bundleSlug,
    manifest_entry: manifestEntry,
  });
}

// ─── /put — upload (or update) a file (write token required) ──────────
async function handlePut(env, req, url) {
  const path = safePath(url.searchParams.get("path"));
  if (!path) return json({ error: "path required (no leading '/', no '..', not MANIFEST.json)" }, 400);
  const body = await req.text();
  if (!body || body.length === 0) return json({ error: "empty body" }, 400);
  if (body.length > 5_000_000) return json({ error: "file too large (5 MB limit)" }, 413);
  await env.KB_BUCKET.put(path, body, {
    httpMetadata: { contentType: contentTypeFor(path) },
  });
  return json({
    ok: true,
    path,
    size: body.length,
    note: "Indexer cron picks up new content within 5 min. POST /reindex to apply immediately.",
  });
}

// ─── /delete — remove a file (write token required) ───────────────────
async function handleDelete(env, url) {
  const path = safePath(url.searchParams.get("path"));
  if (!path) return json({ error: "path required" }, 400);
  await env.KB_BUCKET.delete(path);
  return json({
    ok: true,
    path,
    note: "Vector entries are cleaned up when /reindex runs next.",
  });
}

// ─── /reindex — proxy to the indexer worker (write token required) ────
// Uses a service binding (env.INDEXER) so it works without going through the
// public workers.dev edge (which would trigger a Worker self-fetch loop).
async function handleReindex(env, url) {
  if (!env.INDEXER) {
    return json({ error: "INDEXER service binding not configured on this Worker" }, 500);
  }
  const limit = url.searchParams.get("limit") || "20";
  const r = await env.INDEXER.fetch(new Request(
    `https://internal/reindex?limit=${encodeURIComponent(limit)}`,
    { method: "POST" }
  ));
  return new Response(await r.text(), {
    status: r.status,
    headers: { "content-type": r.headers.get("content-type") || "application/json" },
  });
}

// ─── /search ───────────────────────────────────────────────────────────
async function handleSearch(env, url) {
  const q = (url.searchParams.get("q") || "").trim();
  const mode = url.searchParams.get("mode") || "keyword";
  const offset = parseInt(url.searchParams.get("offset") || "0", 10);
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "10", 10), 50);

  if (!q) return json({ error: "q required" }, 400);

  let hits = [];
  if (mode === "keyword") {
    hits = await keywordSearch(env, q, limit * 2);
  } else if (mode === "semantic") {
    hits = await semanticSearch(env, q, limit * 2);
  } else if (mode === "hybrid") {
    const [kw, sem] = await Promise.all([
      keywordSearch(env, q, limit * 2),
      semanticSearch(env, q, limit * 2),
    ]);
    // Semantic gets weight 1.5x in RRF — a doc that's #1 in semantic but
    // only weakly present in keyword should still surface above a doc
    // that's #1 in keyword but only weakly relevant semantically.
    hits = rrfFuse(sem, kw, 60, [1.5, 1.0]);
  } else {
    return json({ error: `unknown mode: ${mode}` }, 400);
  }

  // Dedupe by path, keep highest score
  const seen = new Map();
  for (const h of hits) {
    const prev = seen.get(h.path);
    if (!prev || h.score > prev.score) seen.set(h.path, h);
  }
  hits = [...seen.values()].sort((a, b) => b.score - a.score).slice(offset, offset + limit);

  return json({ query: q, mode, hits });
}

// Walk every R2 object via cursor pagination. The Workers R2 binding's
// `list()` returns fewer than the requested limit on larger buckets and
// `truncated` cannot be trusted in isolation — keep paginating while a
// cursor is returned, capped at 50 pages for safety.
async function listAllObjects(env, prefix) {
  const all = [];
  let cursor;
  for (let page = 0; page < 50; page++) {
    const opts = { limit: 1000 };
    if (cursor) opts.cursor = cursor;
    if (prefix) opts.prefix = prefix;
    const resp = await env.KB_BUCKET.list(opts);
    if (resp.objects?.length) all.push(...resp.objects);
    if (!resp.cursor) break;
    cursor = resp.cursor;
  }
  return all;
}

// Common English stopwords + question words — stripped from keyword tokens
// because they appear in nearly every doc and don't add discrimination.
const KEYWORD_STOPWORDS = new Set([
  "a","an","the","of","in","on","at","to","for","by","with","and","or","but",
  "is","are","was","were","be","been","being","have","has","had","do","does",
  "did","will","would","could","should","may","might","must","can","cannot",
  "no","not","yes","i","you","he","she","it","we","they","my","your","his",
  "her","its","our","their","this","that","these","those","what","when","where",
  "why","how","who","whom","which","there","here","then","than","so","too","as",
  "if","while","because","since","just","also","such","very","more","most","some","any","all",
]);

// Tokenize a query: lowercase, split on non-alphanumeric, drop stopwords and
// 1-char fragments. Numbers and identifiers are kept ("kb_123" → ["kb", "123"]).
function tokenizeQuery(q) {
  return q.toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(t => t.length >= 2 && !KEYWORD_STOPWORDS.has(t));
}

// Keyword search: surface candidate paths via semantic search (cheap), then
// verify each candidate by TOKEN-level substring match on the FULL file body.
// Every meaningful query token must appear at least once (AND-of-tokens);
// score is total token occurrences. This handles natural-language queries
// like "connects refund Upwork rules" where the literal 4-word phrase never
// appears anywhere but all four words appear together in the right doc.
async function keywordSearch(env, q, limit) {
  if (!q) return [];

  const tokens = tokenizeQuery(q);
  if (tokens.length === 0) return [];   // query had nothing but stopwords

  // Pull a candidate pool — semantic ranking surfaces paraphrases too,
  // so over-fetch and let the token filter do the precision work.
  const candidates = await semanticSearch(env, q, 50);

  // Dedupe candidates by path (semantic returns multiple chunks per file).
  const seenPaths = new Set();
  const uniqueCandidates = [];
  for (const c of candidates) {
    if (!c.path || seenPaths.has(c.path)) continue;
    seenPaths.add(c.path);
    uniqueCandidates.push(c);
  }

  // Per-read wrapper that NEVER throws — one R2 failure must not poison
  // the whole batch (was causing CF 1101 worker errors before this guard).
  async function checkOne(c) {
    try {
      const text = await readTextObject(env, c.path);
      if (!text) return null;
      const lower = text.toLowerCase();

      // Count occurrences of EACH token (capped per-token to keep score bounded).
      const tokenHits = tokens.map(tok => {
        let from = 0, n = 0;
        while (true) {
          const pos = lower.indexOf(tok, from);
          if (pos === -1) break;
          n++;
          from = pos + tok.length;
          if (n >= 10) break;   // per-token cap
        }
        return n;
      });

      // Coverage threshold: require at least 60% of meaningful query tokens
      // to appear. Strict AND would miss strong semantic matches that paraphrase
      // one of the query words (e.g. a doc about "connects refund Upwork rules"
      // that says "policy" instead of "rules"). For 1-2 token queries, require
      // every token. For 3+, allow one to be missing.
      const matchedTokens = tokenHits.filter(n => n > 0).length;
      const minRequired = tokens.length <= 2 ? tokens.length : Math.ceil(tokens.length * 0.6);
      if (matchedTokens < minRequired) return null;

      const totalHits = tokenHits.reduce((sum, n) => sum + n, 0);
      const coverage = matchedTokens / tokens.length;
      const density  = Math.min(totalHits, 30) / 30;   // 0..1

      // Snippet anchored on the rarest-but-present token (most distinctive).
      const presentHits = tokenHits.map((n, i) => ({ tok: tokens[i], n })).filter(x => x.n > 0);
      const anchorTok = presentHits.sort((a, b) => a.n - b.n)[0]?.tok || tokens[0];
      const snippet = snippetAround(text, anchorTok);

      return {
        path: c.path,
        snippet,
        // Score: coverage dominates (a doc that has all tokens beats a doc
        // that has 3/4 with higher density), but density still differentiates
        // within a coverage tier.
        score: 0.4 + (coverage * 0.4) + (density * 0.2),
        bundle_slug: c.bundle_slug || c.path.split("/")[0],
        match_count: totalHits,
        coverage,                     // fraction of query tokens present
        token_matches: tokenHits,     // per-token diagnostic
      };
    } catch (e) {
      console.error(`keyword check failed for ${c?.path}:`, e?.message);
      return null;
    }
  }

  // Read in small parallel chunks (friendly to R2 connection limits).
  const CHUNK = 10;
  const hits = [];
  for (let i = 0; i < uniqueCandidates.length; i += CHUNK) {
    const slice = uniqueCandidates.slice(i, i + CHUNK);
    const results = await Promise.all(slice.map(checkOne));
    for (const r of results) if (r) hits.push(r);
  }

  hits.sort((a, b) => b.score - a.score);
  return hits.slice(0, Math.max(limit, KEYWORD_MAX_HITS));
}

async function semanticSearch(env, q, limit) {
  const emb = await env.AI.run(EMBED_MODEL, { text: [q] });
  const vector = emb.data[0];
  const result = await env.VECTOR.query(vector, {
    topK: Math.min(limit, 50),
    returnMetadata: "all",
  });

  // Dedupe by path, keep the highest-scoring chunk per file. Without dedupe,
  // hybrid RRF over-rewards files that have many matched chunks (was causing
  // the wrong article to surface at #1 even when a single-chunk file was the
  // clear best semantic match).
  const matches = result.matches || [];
  const best = new Map();
  for (const m of matches) {
    const path = m.metadata && m.metadata.path;
    if (!path) continue;
    const prev = best.get(path);
    if (!prev || m.score > prev.score) {
      best.set(path, m);
    }
  }

  return [...best.values()]
    .sort((a, b) => b.score - a.score)
    .map(m => ({
      path: m.metadata.path,
      snippet: (m.metadata && m.metadata.snippet) || "",
      score: m.score,
      bundle_slug: m.metadata && m.metadata.bundle_slug,
    }));
}

// Reciprocal Rank Fusion across two ranked lists.
// Reciprocal Rank Fusion. `weights` lets callers weight one list higher
// than the other (e.g. semantic 1.5x over keyword for natural-language queries).
function rrfFuse(a, b, k = 60, weights = [1.0, 1.0]) {
  const map = new Map();
  const lists = [a, b];
  for (let li = 0; li < lists.length; li++) {
    const w = weights[li] ?? 1.0;
    lists[li].forEach((hit, i) => {
      const prev = map.get(hit.path) || { ...hit, score: 0 };
      prev.score += w / (k + i + 1);
      map.set(hit.path, prev);
    });
  }
  return [...map.values()].sort((x, y) => y.score - x.score);
}

// ─── Router ────────────────────────────────────────────────────────────
//
// Read endpoints (any token):    GET /manifest /get /context-for /search
// Write endpoints (write token): PUT /put, DELETE /delete, POST /reindex
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const m = req.method;

    // Write endpoints — require role "write"
    if (m === "PUT" && url.pathname === "/put") {
      if (!(await authorize(req, env, true))) return json({ error: "write token required" }, 403);
      return handlePut(env, req, url);
    }
    if (m === "DELETE" && url.pathname === "/delete") {
      if (!(await authorize(req, env, true))) return json({ error: "write token required" }, 403);
      return handleDelete(env, url);
    }
    if (m === "POST" && url.pathname === "/reindex") {
      if (!(await authorize(req, env, true))) return json({ error: "write token required" }, 403);
      return handleReindex(env, url);
    }

    // Read endpoints — accept any active token
    if (!(await authorize(req, env))) return json({ error: "unauthorized" }, 401);
    if (url.pathname === "/manifest") return handleManifest(env);
    if (url.pathname === "/get") return handleGet(env, url);
    if (url.pathname === "/context-for") return handleContextFor(env, url);
    if (url.pathname === "/search") return handleSearch(env, url);
    return json({ error: "not found" }, 404);
  },
};
