#!/usr/bin/env bash
# 03_deploy_indexer_worker.sh — deploy the indexer Worker with R2 + Vectorize + AI bindings + cron.

set -euo pipefail
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${KB_NAME:?}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKER_SRC="$SCRIPT_DIR/../workers/indexer.js"
WORKER_NAME="${KB_NAME}-indexer"
API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/$WORKER_NAME"

echo "→ Ensuring KV namespace for indexer state…"
KV_NAME="${KB_NAME}-indexer-state"
KV_ID=$(curl -sX POST \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"$KV_NAME\"}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d.get('success'): print(d['result']['id'])
else:
  msg = (d.get('errors') or [{}])[0].get('message','')
  if 'already exists' in msg.lower():
    # find the existing one
    pass
" 2>/dev/null) || true

if [[ -z "$KV_ID" ]]; then
  KV_ID=$(curl -s "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces?per_page=100" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for ns in d.get('result', []):
  if ns['title'] == '$KV_NAME':
    print(ns['id']); break
")
fi
echo "  KV namespace: $KV_NAME ($KV_ID)"

echo "→ Deploying indexer Worker: $WORKER_NAME"

METADATA=$(cat <<EOF
{
  "main_module": "indexer.js",
  "compatibility_date": "2024-09-23",
  "compatibility_flags": ["nodejs_compat"],
  "bindings": [
    {"type": "r2_bucket",          "name": "KB_BUCKET", "bucket_name": "$KB_NAME"},
    {"type": "vectorize",          "name": "VECTOR",    "index_name":  "$KB_NAME"},
    {"type": "ai",                 "name": "AI"},
    {"type": "kv_namespace",       "name": "STATE",     "namespace_id": "$KV_ID"}
  ]
}
EOF
)

RESP=$(curl -sX PUT "$API" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -F "metadata=$METADATA;type=application/json" \
  -F "indexer.js=@$WORKER_SRC;type=application/javascript+module;filename=indexer.js")

python3 - <<PY
import json, sys
d = json.loads('''$RESP''')
if d.get("success"):
    print(f"  ✓ indexer Worker deployed: ${WORKER_NAME}")
else:
    print("  ✗ FAILED:")
    for e in d.get("errors", []): print(f"    [{e.get('code')}] {e.get('message')}")
    sys.exit(1)
PY

echo "→ Setting cron trigger (every 5 min)…"
curl -sX PUT \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/$WORKER_NAME/schedules" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{"cron":"*/5 * * * *"}]' > /dev/null

echo "  ✓ cron set: */5 * * * * (every 5 minutes)"
