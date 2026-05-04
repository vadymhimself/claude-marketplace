#!/usr/bin/env bash
# 01_create_r2_bucket.sh — create the R2 bucket. Idempotent.

set -euo pipefail
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${KB_NAME:?}" "${KB_REGION:=auto}"

API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets"

echo "→ Creating R2 bucket: $KB_NAME (region: $KB_REGION)"

RESP=$(curl -sX POST "$API" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$KB_NAME\",\"locationHint\":\"$KB_REGION\"}")

python3 - <<PY
import json
d = json.loads('''$RESP''')
if d.get("success"):
    print(f"  ✓ bucket '{d['result']['name']}' created")
elif any("already exists" in (e.get("message", "") or "").lower() for e in d.get("errors", [])):
    print(f"  ✓ bucket '$KB_NAME' already exists (idempotent)")
else:
    print(f"  ✗ FAILED:")
    for e in d.get("errors", []):
        print(f"    [{e.get('code')}] {e.get('message')}")
    import sys; sys.exit(1)
PY
