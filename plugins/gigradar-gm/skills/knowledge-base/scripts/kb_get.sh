#!/usr/bin/env bash
# kb_get.sh — fetch a single file from the KB.
#
# Usage: kb_get.sh "path/inside/r2/file.md"
# Output: raw file content to stdout.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_TOKEN:?KB_TOKEN not set — populate <skill>/.kb-config first}"

[[ $# -lt 1 ]] && { echo "Usage: $0 <path>" >&2; exit 64; }

ENCODE() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }
PATH_ENC=$(ENCODE "$1")

curl -sf -H "Authorization: Bearer $KB_TOKEN" \
  "$KB_WORKER_URL/get?path=$PATH_ENC" || {
  echo "fetch failed for path: $1" >&2
  exit 1
}
