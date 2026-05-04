#!/usr/bin/env bash
# kb_setup.sh — write <skill>/.kb-config from credentials provided either as
# arguments or as env vars (whichever the user pastes from their provisioning
# email). Run this once when first activating the skill. Re-run any time the
# token rotates.
#
# Usage:
#   # Option A: positional args (read token only)
#   bash kb_setup.sh <KB_WORKER_URL> <KB_TOKEN> [KB_WRITE_TOKEN]
#
#   # Option B: env vars (the format the provisioning email ships)
#   KB_WORKER_URL=https://… KB_TOKEN=kb_… [KB_WRITE_TOKEN=kb_write_…] bash kb_setup.sh
#
# After this runs, every other script in scripts/ will pick up the values
# automatically. No env vars need to stay set in your shell.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HERE/../.kb-config"

# Accept positional or env. Positional overrides env.
URL="${1:-${KB_WORKER_URL:-}}"
RTOK="${2:-${KB_TOKEN:-}}"
WTOK="${3:-${KB_WRITE_TOKEN:-}}"

if [[ -z "$URL" || -z "$RTOK" ]]; then
  cat >&2 <<'USAGE'
kb_setup.sh — needs at minimum KB_WORKER_URL and KB_TOKEN.

Usage:
  bash kb_setup.sh <KB_WORKER_URL> <KB_TOKEN> [KB_WRITE_TOKEN]

Or paste the snippet from your provisioning email:
  KB_WORKER_URL=https://… KB_TOKEN=kb_… KB_WRITE_TOKEN=kb_write_… bash kb_setup.sh

If you don't have these credentials, contact your KB admin to get provisioned.
USAGE
  exit 64
fi

# Sanity-check the URL shape so we don't silently write garbage
if [[ ! "$URL" =~ ^https?://[^/]+ ]]; then
  echo "kb_setup.sh: KB_WORKER_URL must start with http:// or https://" >&2
  exit 65
fi
if [[ ! "$RTOK" =~ ^kb_ ]]; then
  echo "kb_setup.sh: KB_TOKEN should look like 'kb_…' — got '${RTOK:0:6}…'" >&2
  exit 65
fi

# Write the config (chmod 600 — tokens shouldn't be world-readable)
{
  echo "# KB instance config — sourced by every script in scripts/."
  echo "# Edit this file to rotate tokens or change the worker URL."
  echo "KB_WORKER_URL=\"$URL\""
  echo "KB_TOKEN=\"$RTOK\""
  echo "KB_WRITE_TOKEN=\"$WTOK\""
} > "$TARGET"
chmod 600 "$TARGET"

# Smoke-test the manifest call to confirm the token works before declaring victory
HTTP=$(curl -s -o /tmp/kb-setup-test.json -w "%{http_code}" \
  -H "Authorization: Bearer $RTOK" \
  "$URL/manifest" || echo "000")

if [[ "$HTTP" != "200" ]]; then
  echo "kb_setup.sh: wrote $TARGET but the smoke test failed (HTTP $HTTP)." >&2
  echo "Verify the URL and token, then re-run." >&2
  cat /tmp/kb-setup-test.json >&2 2>/dev/null || true
  exit 1
fi

N_BUNDLES=$(python3 -c "import json,sys; print(json.load(open('/tmp/kb-setup-test.json')).get('n_bundles', '?'))" 2>/dev/null || echo "?")
echo "✓ wrote $TARGET (chmod 600)"
echo "✓ smoke test passed — $N_BUNDLES bundles visible to your token"
[[ -n "$WTOK" ]] && echo "✓ write token recorded — kb_put / kb_delete / kb_reindex enabled" \
                 || echo "ℹ  no write token — read-only mode (kb_put / kb_delete / kb_reindex will refuse)"
