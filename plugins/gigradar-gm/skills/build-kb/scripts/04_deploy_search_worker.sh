#!/usr/bin/env bash
# 04_deploy_search_worker.sh — deploy the search Worker, mint consumer token, save to .token file.

set -euo pipefail
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${KB_NAME:?}" "${WORKERS_SUBDOMAIN:?}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(dirname "$SCRIPT_DIR")"
WORKER_SRC="$SKILL_ROOT/workers/search.js"
WORKER_NAME="${KB_NAME}-search"
API_BASE="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID"

# 1. KV namespace for consumer tokens
echo "→ Ensuring KV namespace for consumer tokens…"
KV_NAME="${KB_NAME}-consumer-tokens"
KV_RESP=$(curl -sX POST "$API_BASE/storage/kv/namespaces" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"$KV_NAME\"}")
KV_ID=$(echo "$KV_RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d.get('success'): print(d['result']['id'])
")
if [[ -z "$KV_ID" ]]; then
  KV_ID=$(curl -s "$API_BASE/storage/kv/namespaces?per_page=100" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for ns in d.get('result', []):
  if ns['title'] == '$KV_NAME':
    print(ns['id']); break
")
fi
echo "  KV namespace: $KV_NAME ($KV_ID)"

# 2. Mint a read-only consumer token (random 40-char hex)
CONSUMER_TOKEN="kb_$(python3 -c 'import secrets; print(secrets.token_hex(20))')"
echo "→ Minting read-only consumer token: ${CONSUMER_TOKEN:0:12}…"
curl -sX PUT \
  "$API_BASE/storage/kv/namespaces/$KV_ID/values/$CONSUMER_TOKEN" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: text/plain" \
  --data "active" > /dev/null
echo "  ✓ read token registered (role=active)"

# 2b. Mint a write-scoped token (used by kb_put / kb_delete / kb_reindex)
WRITE_TOKEN="kb_write_$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
echo "→ Minting write-scoped token: ${WRITE_TOKEN:0:18}…"
curl -sX PUT \
  "$API_BASE/storage/kv/namespaces/$KV_ID/values/$WRITE_TOKEN" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: text/plain" \
  --data "write" > /dev/null
echo "  ✓ write token registered (role=write)"

# 3. Deploy search Worker
echo "→ Deploying search Worker: $WORKER_NAME"
METADATA=$(cat <<EOF
{
  "main_module": "search.js",
  "compatibility_date": "2024-09-23",
  "compatibility_flags": ["nodejs_compat"],
  "bindings": [
    {"type": "r2_bucket",      "name": "KB_BUCKET", "bucket_name": "$KB_NAME"},
    {"type": "vectorize",      "name": "VECTOR",    "index_name":  "$KB_NAME"},
    {"type": "ai",             "name": "AI"},
    {"type": "kv_namespace",   "name": "TOKENS",    "namespace_id": "$KV_ID"},
    {"type": "service",        "name": "INDEXER",   "service":      "${KB_NAME}-indexer"}
  ]
}
EOF
)

RESP=$(curl -sX PUT "$API_BASE/workers/scripts/$WORKER_NAME" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -F "metadata=$METADATA;type=application/json" \
  -F "search.js=@$WORKER_SRC;type=application/javascript+module;filename=search.js")

python3 - <<PY
import json, sys
d = json.loads('''$RESP''')
if d.get("success"):
    print(f"  ✓ search Worker deployed: ${WORKER_NAME}")
else:
    print("  ✗ FAILED:")
    for e in d.get("errors", []): print(f"    [{e.get('code')}] {e.get('message')}")
    sys.exit(1)
PY

# 4. Enable workers.dev subdomain for the worker
curl -sX POST \
  "$API_BASE/workers/scripts/$WORKER_NAME/subdomain" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' > /dev/null

URL="https://${WORKER_NAME}.${WORKERS_SUBDOMAIN}.workers.dev"
echo "  ✓ public URL: $URL"

# 5. Persist for downstream scripts
mkdir -p "$SKILL_ROOT/generated/knowledge-base"
echo "$CONSUMER_TOKEN" > "$SKILL_ROOT/generated/knowledge-base/.token"
echo "$WRITE_TOKEN"    > "$SKILL_ROOT/generated/knowledge-base/.write-token"
echo "$URL"            > "$SKILL_ROOT/generated/knowledge-base/.url"
chmod 600 "$SKILL_ROOT/generated/knowledge-base/.token" "$SKILL_ROOT/generated/knowledge-base/.write-token"
echo "  ✓ saved read token + write token + URL to generated/knowledge-base/"
