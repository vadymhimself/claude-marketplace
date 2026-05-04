#!/usr/bin/env bash
# 02_create_vectorize_index.sh — create the Vectorize index, sized for BGE-large (1024 dim).

set -euo pipefail
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${KB_NAME:?}"

API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/vectorize/v2/indexes"

echo "→ Creating Vectorize index: $KB_NAME (1024-dim cosine for bge-large-en-v1.5)"

RESP=$(curl -sX POST "$API" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$KB_NAME\",
    \"description\": \"KB semantic index — bge-large-en-v1.5\",
    \"config\": {
      \"dimensions\": 1024,
      \"metric\": \"cosine\"
    }
  }")

python3 - <<PY
import json
d = json.loads('''$RESP''')
if d.get("success"):
    print(f"  ✓ vectorize index '{d['result']['name']}' created")
elif any("already exists" in (e.get("message","") or "").lower() for e in d.get("errors", [])):
    print(f"  ✓ index '$KB_NAME' already exists (idempotent)")
else:
    print(f"  ✗ FAILED:")
    for e in d.get("errors", []):
        print(f"    [{e.get('code')}] {e.get('message')}")
    import sys; sys.exit(1)
PY

# Create metadata indexes for filterable fields
for FIELD in bundle_slug tags public_safe; do
    curl -sX POST \
        "$API/$KB_NAME/metadata_index/create" \
        -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"propertyName\":\"$FIELD\",\"indexType\":\"string\"}" > /dev/null 2>&1 || true
done
echo "  ✓ metadata filters: bundle_slug, tags, public_safe"
