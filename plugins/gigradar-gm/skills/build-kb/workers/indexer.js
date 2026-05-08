/**
 * KB Indexer Worker.
 *
 * Indexes the ENTIRE content of every .md/.txt/.json file in R2. No YAML
 * frontmatter is required, expected, or special-cased — humans write plain
 * markdown, the indexer chunks and embeds the whole thing.
 *
 * Bindings: KB_BUCKET (R2), VECTOR (Vectorize), AI (Workers AI), STATE (KV)
 * Routes:
 *   POST /reindex?limit=N        — index up to N changed files
 *   POST /rebuild-manifest       — regenerate MANIFEST.json from R2 listing
 *   GET  /status                 — return current MANIFEST.json
 */

const EMBED_MODEL = "@cf/baai/bge-large-en-v1.5";
const INDEXABLE_EXT = /\.(md|txt|json)$/i;
// bge-large-en-v1.5 context window is 512 tokens (~2000 chars).
const MAX_CHARS_PER_CHUNK = 1900;
const CHUNK_OVERLAP = 250;
const MAX_CHUNKS_PER_FILE = 6;
const DEFAULT_BATCH = 5;

// Vectorize IDs cap at 64 bytes. Hash the path so any depth fits.
async function vectorIdFor(path, chunkIndex) {
  const data = new TextEncoder().encode(path);
  const hash = await crypto.subtle.digest("SHA-256", data);
  const hex = [...new Uint8Array(hash)].slice(0, 8)
    .map(b => b.toString(16).padStart(2, "0")).join("");
  return `${hex}::${chunkIndex}`;
}

function chunkText(s) {
  if (s.length <= MAX_CHARS_PER_CHUNK) return [s];
  const out = [];
  let i = 0;
  while (i < s.length) {
    out.push(s.slice(i, i + MAX_CHARS_PER_CHUNK));
    i += MAX_CHARS_PER_CHUNK - CHUNK_OVERLAP;
  }
  return out;
}

async function indexOne(env, obj) {
  const cached = await env.STATE.get(`${obj.key}:etag`);
  if (cached === obj.etag) return { unchanged: true };

  const text = await env.KB_BUCKET.get(obj.key).then(o => o?.text());
  if (!text || text.trim().length < 20) {
    await env.STATE.put(`${obj.key}:etag`, obj.etag);
    return { skipped: true, reason: "empty or too short" };
  }

  // Index the ENTIRE file content. No frontmatter stripping, no special-casing.
  const chunks = chunkText(text).slice(0, MAX_CHUNKS_PER_FILE)
    .map(c => c.trim())
    .filter(c => c.length >= 20);
  if (chunks.length === 0) {
    await env.STATE.put(`${obj.key}:etag`, obj.etag);
    return { skipped: true, reason: "no usable chunks" };
  }

  const bundle = obj.key.split("/")[0];

  const emb = await env.AI.run(EMBED_MODEL, { text: chunks });
  if (!emb?.data || emb.data.length !== chunks.length) {
    throw new Error(`embed returned ${emb?.data?.length ?? 0} vectors for ${chunks.length} chunks`);
  }

  const vectors = await Promise.all(chunks.map(async (c, i) => ({
    id: await vectorIdFor(obj.key, i),
    values: emb.data[i],
    metadata: {
      path: obj.key,
      bundle_slug: bundle,
      chunk_index: i,
      // Short snippet for display only — the source of truth is the file in R2.
      snippet: c.slice(0, 200).replace(/\s+/g, " ").trim(),
    },
  })));

  // Delete-then-upsert so a shrunken file doesn't leave orphan chunks.
  const allPossibleIds = await Promise.all(
    Array.from({ length: MAX_CHUNKS_PER_FILE }, (_, i) => vectorIdFor(obj.key, i))
  );
  await env.VECTOR.deleteByIds(allPossibleIds);

  await env.VECTOR.upsert(vectors);
  await env.STATE.put(`${obj.key}:etag`, obj.etag);
  return { chunks: vectors.length };
}

// Walk the entire R2 bucket via cursor pagination. The Workers R2 binding's
// `list()` regularly returns fewer than the requested limit on larger buckets
// (the practical per-call ceiling is around 500 objects regardless of `limit`),
// and `truncated` cannot be trusted in isolation — we keep paginating as long
// as a cursor is returned, capped at 50 pages for safety.
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

async function reindex(env, limit) {
  const all = await listAllObjects(env);
  const todo = all.filter(o =>
    INDEXABLE_EXT.test(o.key) && o.key !== "MANIFEST.json"
  );

  let indexed = 0, unchanged = 0, skipped = 0, errors = 0, processed = 0;
  const sampleErrors = [];
  let quotaBlocked = false;
  for (const obj of todo) {
    if (processed >= limit) break;
    if (quotaBlocked) break;
    try {
      const r = await indexOne(env, obj);
      if (r.unchanged) unchanged++;
      else if (r.skipped) skipped++;
      else if (r.chunks) {
        indexed++;
        processed++;
      }
    } catch (e) {
      errors++;
      processed++;
      if (/4006|daily free allocation/i.test(e.message || "")) {
        quotaBlocked = true;
      }
      if (sampleErrors.length < 3) {
        sampleErrors.push({ key: obj.key, error: e.message });
      }
      console.error(`indexOne ${obj.key}:`, e.message);
    }
  }
  return {
    total_files: todo.length,
    indexed,
    unchanged,
    skipped,
    errors,
    quota_blocked: quotaBlocked,
    sample_errors: sampleErrors,
    remaining_estimated: Math.max(0, todo.length - indexed - unchanged - skipped),
    note: quotaBlocked
      ? "Workers AI daily free-tier neuron quota exhausted. Resets at 00:00 UTC. Or upgrade Workers Paid ($5/mo) for higher caps."
      : undefined,
  };
}

async function rebuildManifest(env) {
  const all = await listAllObjects(env);

  const bundles = new Map();
  for (const obj of all) {
    if (obj.key === "MANIFEST.json") continue;
    if (!obj.key.includes("/")) continue; // root files aren't bundles
    const slug = obj.key.split("/")[0];
    if (!slug) continue;
    if (!bundles.has(slug)) {
      bundles.set(slug, {
        slug,
        n_files: 0,
        n_md: 0, n_csv: 0, n_json: 0, n_txt: 0, n_other: 0,
        files: [],          // every file path inside the bundle
        last_modified: obj.uploaded,
      });
    }
    const b = bundles.get(slug);
    b.n_files++;
    if (obj.key.endsWith(".md")) b.n_md++;
    else if (obj.key.endsWith(".csv")) b.n_csv++;
    else if (obj.key.endsWith(".json")) b.n_json++;
    else if (obj.key.endsWith(".txt")) b.n_txt++;
    else b.n_other++;
    if (obj.uploaded > b.last_modified) b.last_modified = obj.uploaded;
    b.files.push(obj.key);
  }

  // Bundle README first line — used as a cheap human-readable label.
  // We pull the H1 if present, else the first non-empty line. No frontmatter
  // parsing, no narrative-paragraph heuristics.
  for (const b of bundles.values()) {
    const readme = await env.KB_BUCKET.get(`${b.slug}/README.md`).then(x => x?.text());
    if (readme) {
      const h1 = (readme.match(/^#\s+(.+)$/m) || [])[1];
      const firstLine = h1 || (readme.split("\n").find(l => l.trim()) || "").trim();
      b.tagline = firstLine.slice(0, 240).replace(/\s+/g, " ").trim();
    }
    b.files.sort();
  }

  const manifest = {
    $schema: "kb-manifest-v2",
    generated_at: new Date().toISOString(),
    n_bundles: bundles.size,
    bundles: [...bundles.values()].sort((a, b) => a.slug.localeCompare(b.slug)),
  };

  await env.KB_BUCKET.put("MANIFEST.json", JSON.stringify(manifest, null, 2), {
    httpMetadata: { contentType: "application/json" },
  });
  return { ok: true, n_bundles: manifest.n_bundles };
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (url.pathname === "/reindex") {
      const limit = parseInt(url.searchParams.get("limit") || `${DEFAULT_BATCH}`, 10);
      const r = await reindex(env, limit);
      return Response.json(r);
    }
    if (url.pathname === "/rebuild-manifest") {
      const r = await rebuildManifest(env);
      return Response.json(r);
    }
    if (url.pathname === "/status") {
      const m = await env.KB_BUCKET.get("MANIFEST.json").then(o => o?.text());
      return new Response(m || JSON.stringify({ error: "no manifest yet" }), {
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("indexer — POST /reindex /rebuild-manifest, GET /status\n");
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(reindex(env, DEFAULT_BATCH));
  },
};
