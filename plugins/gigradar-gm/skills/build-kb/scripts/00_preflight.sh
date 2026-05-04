#!/usr/bin/env bash
# 00_preflight.sh — pre-flight checks that catch every gotcha we hit during the GigRadar bootstrap.
#
# Run this BEFORE bootstrap_all.sh. It will tell you exactly what's wrong
# and exactly what to click in the Cloudflare dashboard to fix it.

set -uo pipefail

# Try config locations
CFG="${HOME}/.kb-bootstrap.env"
[[ -f "$CFG" ]] && source "$CFG"

FAIL=0
red()    { printf "\033[31m%s\033[0m\n" "$1"; }
green()  { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

echo "════════════════════════════════════════════════════════════════"
echo "  Pre-flight check for KB bootstrap"
echo "════════════════════════════════════════════════════════════════"
echo

# Local tools
echo "→ Local tools"
for cmd in curl python3 file find xargs; do
  if command -v "$cmd" >/dev/null; then
    green "  ✓ $cmd"
  else
    red "  ✗ $cmd not found — install with your package manager"
    FAIL=1
  fi
done
echo

# Config
echo "→ Config file ($CFG)"
if [[ ! -f "$CFG" ]]; then
  red "  ✗ $CFG does not exist"
  yellow "    Create it like this:"
  cat <<EOF

      cat > "\$HOME/.kb-bootstrap.env" <<'CFG'
      CLOUDFLARE_API_TOKEN=<paste-your-token-here>
      CLOUDFLARE_ACCOUNT_ID=<your-account-id>
      KB_NAME=my-kb
      KB_REGION=auto
      WORKERS_SUBDOMAIN=<your-subdomain>
      CFG
      chmod 600 "\$HOME/.kb-bootstrap.env"
EOF
  echo
  FAIL=1
else
  for v in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID KB_NAME WORKERS_SUBDOMAIN; do
    if [[ -z "${!v:-}" ]]; then
      red "  ✗ $v is not set in $CFG"
      FAIL=1
    else
      green "  ✓ $v is set"
    fi
  done
fi
echo

# Token
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "→ Cloudflare token"
  RESP=$(curl -s -m 10 "https://api.cloudflare.com/client/v4/user/tokens/verify" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN")
  STATUS=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result',{}).get('status','?') if d.get('success') else 'INVALID')" 2>/dev/null || echo "?")
  if [[ "$STATUS" == "active" ]]; then
    green "  ✓ token is active"
  else
    red "  ✗ token rejected by Cloudflare ($STATUS)"
    yellow "    Re-mint at https://dash.cloudflare.com/profile/api-tokens"
    FAIL=1
  fi
fi
echo

# Account access
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  echo "→ Permissions on account $CLOUDFLARE_ACCOUNT_ID"

  probe() {
    local name="$1" url="$2" missing_msg="$3"
    local code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
      -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$url")
    if [[ "$code" == "200" ]]; then
      green "  ✓ $name"
    elif [[ "$code" == "403" || "$code" == "401" ]]; then
      red "  ✗ $name — token missing permission"
      yellow "    $missing_msg"
      FAIL=1
    else
      yellow "  ⚠ $name — got HTTP $code (might be a feature-not-enabled issue)"
      yellow "    $missing_msg"
      FAIL=1
    fi
  }

  probe "Workers Scripts: Edit" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts" \
    "Add 'Workers Scripts: Edit' permission to your token."

  probe "Workers AI: Read" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/models/search" \
    "Add 'Workers AI: Read' permission to your token."

  probe "Vectorize: Edit" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/vectorize/v2/indexes" \
    "Add 'Vectorize: Edit' permission to your token."

  probe "KV Storage: Edit" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces" \
    "Add 'Workers KV Storage: Edit' permission to your token."

  # R2 needs both: token permission AND account-level enable
  R2_RESP=$(curl -s -m 10 -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets")
  if echo "$R2_RESP" | grep -q '"success":true'; then
    green "  ✓ R2 enabled & token has access"
  elif echo "$R2_RESP" | grep -q "Please enable R2"; then
    red "  ✗ R2 is NOT enabled on this account"
    yellow "    1. Go to: https://dash.cloudflare.com/?to=/:account/r2/overview"
    yellow "    2. Click 'Purchase R2' (free tier — no actual purchase, just enables)"
    yellow "    3. You may need to add a payment method even for the free tier."
    yellow "    4. Once enabled, re-run this preflight."
    FAIL=1
  elif echo "$R2_RESP" | grep -qE 'Authentication error|10042|10000'; then
    red "  ✗ R2 access denied"
    yellow "    Add 'R2 Storage: Edit' permission to your token."
    FAIL=1
  else
    yellow "  ⚠ R2: unexpected response — $R2_RESP"
    FAIL=1
  fi
fi
echo

# Workers subdomain
if [[ -n "${WORKERS_SUBDOMAIN:-}" && -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "→ Workers subdomain"
  SUB=$(curl -s -m 10 -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result',{}).get('subdomain') or '')" 2>/dev/null)
  if [[ -z "$SUB" ]]; then
    red "  ✗ no Workers subdomain set up on this account"
    yellow "    1. Go to: https://dash.cloudflare.com/?to=/:account/workers/overview"
    yellow "    2. You'll be prompted to pick a subdomain. Pick anything (free)."
    yellow "    3. Update WORKERS_SUBDOMAIN in $CFG to match."
    FAIL=1
  elif [[ "$SUB" != "$WORKERS_SUBDOMAIN" ]]; then
    yellow "  ⚠ Subdomain mismatch: dashboard says '$SUB', config says '$WORKERS_SUBDOMAIN'"
    yellow "    Update $CFG: WORKERS_SUBDOMAIN=$SUB"
    FAIL=1
  else
    green "  ✓ subdomain '$SUB.workers.dev' matches config"
  fi
fi

echo
echo "════════════════════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
  green "  ALL CHECKS PASSED — you can run bootstrap_all.sh now."
else
  red "  PRE-FLIGHT FAILED — fix the items above, then re-run this script."
  exit 1
fi
echo "════════════════════════════════════════════════════════════════"
