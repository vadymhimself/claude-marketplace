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
const KEYWORD_MAX_FILES = 250;       // upper bound on files we'll fetch in one query
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
    hits = rrfFuse(kw, sem);
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

// Real keyword search: scans actual file contents in R2 for the literal
// (case-insensitive) substring. Skips MANIFEST.json and non-text files.
async function keywordSearch(env, q, limit) {
  if (!q) return [];
  const list = await env.KB_BUCKET.list({ limit: 1000 });
  const targets = list.objects
    .filter(o => INDEXABLE_EXT.test(o.key) && o.key !== "MANIFEST.json" && o.key.includes("/"))
    .slice(0, KEYWORD_MAX_FILES);

  const qLower = q.toLowerCase();
  const hits = [];

  for (const o of targets) {
    if (hits.length >= Math.max(limit, KEYWORD_MAX_HITS)) break;
    const text = await readTextObject(env, o.key);
    if (!text) continue;
    const lower = text.toLowerCase();
    const idx = lower.indexOf(qLower);
    if (idx === -1) continue;

    // Score: more occurrences ranks higher; cap at 10 to keep score bounded.
    let occurrences = 0;
    let from = 0;
    while (true) {
      const pos = lower.indexOf(qLower, from);
      if (pos === -1) break;
      occurrences++;
      from = pos + qLower.length;
      if (occurrences >= 10) break;
    }

    hits.push({
      path: o.key,
      snippet: snippetAround(text, q),
      score: 0.5 + Math.min(occurrences, 10) * 0.05, // 0.55 .. 1.00
      bundle_slug: o.key.split("/")[0],
      match_count: occurrences,
    });
  }

  return hits.sort((a, b) => b.score - a.score);
}

async function semanticSearch(env, q, limit) {
  const emb = await env.AI.run(EMBED_MODEL, { text: [q] });
  const vector = emb.data[0];
  const result = await env.VECTOR.query(vector, {
    topK: limit,
    returnMetadata: "all",
  });
  return (result.matches || []).map(m => ({
    path: m.metadata?.path,
    snippet: m.metadata?.snippet || "",
    score: m.score,
    bundle_slug: m.metadata?.bundle_slug,
  }));
}

// Reciprocal Rank Fusion across two ranked lists.
function rrfFuse(a, b, k = 60) {
  const map = new Map();
  for (const list of [a, b]) {
    list.forEach((hit, i) => {
      const prev = map.get(hit.path) || { ...hit, score: 0 };
      prev.score += 1 / (k + i + 1);
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
