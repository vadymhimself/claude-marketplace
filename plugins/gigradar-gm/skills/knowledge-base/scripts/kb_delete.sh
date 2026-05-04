#!/usr/bin/env bash
# kb_delete.sh — delete a file from the Knowledge Base.
#
# Usage:
#   kb_delete.sh <kb-path>
#
# Requires KB_WRITE_TOKEN in <skill>/.kb-config. Vector entries are cleaned up
# on the next /reindex run (call kb_reindex.sh after a delete to apply now).
#
# Output: JSON {ok, path, note}.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_WRITE_TOKEN:?KB_WRITE_TOKEN not set — read-only KB_TOKEN cannot delete.}"

[[ $# -lt 1 ]] && { echo "Usage: $0 <kb-path>" >&2; exit 64; }

ENCODE() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }

curl -sf -X DELETE "$KB_WORKER_URL/delete?path=$(ENCODE "$1")" \
  -H "Authorization: Bearer $KB_WRITE_TOKEN" || {
  echo "delete failed for path: $1" >&2
  exit 1
}
echo
