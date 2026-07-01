#!/usr/bin/env bash
# kb_setup.sh — write <skill>/.kb-config from credentials provided either as
# arguments or as env vars (whichever the user pastes from their provisioning
# email). Run this once when first activating the skill. Re-run any time the
# token rotates.
#
# Usage (any of these — use the script's real path, e.g. from the knowledge-base
# skill directory: bash "<skill-dir>/scripts/kb_setup.sh" ...):
#   # Option A: flags (the form the provisioning email ships)
#   bash kb_setup.sh --url "https://…" --token "kb_…" [--write-token "kb_write_…"] [--role active]
#
#   # Option B: positional args
#   bash kb_setup.sh <KB_WORKER_URL> <KB_TOKEN> [KB_WRITE_TOKEN]
#
#   # Option C: env vars
#   KB_WORKER_URL=https://… KB_TOKEN=kb_… [KB_WRITE_TOKEN=kb_write_…] bash kb_setup.sh
#
# After this runs, every other script in scripts/ will pick up the values
# automatically. No env vars need to stay set in your shell.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HERE/../.kb-config"

# Accept flags (--url/--token/--write-token, the form the provisioning email
# ships), positional args, OR env vars — whichever the user/agent pastes. Flags
# and positional win over env. --role is accepted for compatibility and ignored
# (the role is encoded server-side in the token, not in the config).
URL="" ; RTOK="" ; WTOK="" ; POS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)                            URL="${2:-}";  shift 2;;
    --url=*)                          URL="${1#*=}"; shift;;
    --token)                          RTOK="${2:-}"; shift 2;;
    --token=*)                        RTOK="${1#*=}"; shift;;
    --write-token|--write_token)      WTOK="${2:-}"; shift 2;;
    --write-token=*|--write_token=*)  WTOK="${1#*=}"; shift;;
    --role)                           shift 2;;
    --role=*)                         shift;;
    --)                               shift; while [[ $# -gt 0 ]]; do POS+=("$1"); shift; done;;
    -*)                               echo "kb_setup.sh: unknown flag '$1'" >&2; exit 64;;
    *)                                POS+=("$1"); shift;;
  esac
done
URL="${URL:-${POS[0]:-${KB_WORKER_URL:-}}}"
RTOK="${RTOK:-${POS[1]:-${KB_TOKEN:-}}}"
WTOK="${WTOK:-${POS[2]:-${KB_WRITE_TOKEN:-}}}"

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
