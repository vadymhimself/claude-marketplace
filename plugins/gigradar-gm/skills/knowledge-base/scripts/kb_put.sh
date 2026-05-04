#!/usr/bin/env bash
# kb_put.sh — upload (or overwrite) a file in the Knowledge Base.
#
# Usage:
#   kb_put.sh <local-file> <kb-path>
#   echo "content" | kb_put.sh - <kb-path>
#
# Requires KB_WRITE_TOKEN in <skill>/.kb-config (separate from the read-only
# KB_TOKEN). After upload the indexer cron picks it up within ~5 minutes.
# Use kb_reindex.sh to apply immediately.
#
# Output: JSON {ok, path, size, note}.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_WRITE_TOKEN:?KB_WRITE_TOKEN not set — read-only KB_TOKEN cannot upload. Add it to <skill>/.kb-config or ask the KB owner.}"

[[ $# -lt 2 ]] && {
  echo "Usage: $0 <local-file|-> <kb-path>" >&2
  echo "Example: $0 ./new-finding.md research-2026-q2/insights/01_thing/INSIGHT.md" >&2
  exit 64
}

LOCAL="$1"
KB_PATH="$2"

ENCODE() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }

URL="$KB_WORKER_URL/put?path=$(ENCODE "$KB_PATH")"

if [[ "$LOCAL" == "-" ]]; then
  curl -sf -X PUT "$URL" \
    -H "Authorization: Bearer $KB_WRITE_TOKEN" \
    --data-binary @- || {
    echo "upload failed for path: $KB_PATH" >&2
    exit 1
  }
else
  [[ ! -f "$LOCAL" ]] && { echo "no such file: $LOCAL" >&2; exit 1; }
  curl -sf -X PUT "$URL" \
    -H "Authorization: Bearer $KB_WRITE_TOKEN" \
    --data-binary @"$LOCAL" || {
    echo "upload failed (file: $LOCAL → kb path: $KB_PATH)" >&2
    exit 1
  }
fi
echo
