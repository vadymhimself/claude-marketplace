#!/usr/bin/env bash
# kb_manifest.sh — fetch the KB MANIFEST.json. Always start research with this.
#
# Output: full manifest JSON with bundle index for cross-bundle lookups.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_TOKEN:?KB_TOKEN not set — populate <skill>/.kb-config first}"

curl -sf -H "Authorization: Bearer $KB_TOKEN" \
  "$KB_WORKER_URL/manifest" || {
  echo "manifest fetch failed" >&2
  exit 1
}
