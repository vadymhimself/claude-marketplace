#!/usr/bin/env bash
# kb_context_for.sh — fetch a file PLUS its bundle README + manifest entry.
#
# This is the canonical "I found something, give me everything I need to cite it"
# call. The Worker pulls the bundle README and the manifest entry alongside the
# file, in one round trip.
#
# Usage: kb_context_for.sh "path/inside/r2/file.md"
# Output: JSON {file, bundle_readme, bundle_slug, manifest_entry}.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_TOKEN:?KB_TOKEN not set — populate <skill>/.kb-config first}"

[[ $# -lt 1 ]] && { echo "Usage: $0 <path>" >&2; exit 64; }

ENCODE() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }
PATH_ENC=$(ENCODE "$1")

curl -sf -H "Authorization: Bearer $KB_TOKEN" \
  "$KB_WORKER_URL/context-for?path=$PATH_ENC" || {
  echo "context-for failed for path: $1" >&2
  exit 1
}
