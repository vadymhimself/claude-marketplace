#!/usr/bin/env bash
# kb_search.sh — search the Knowledge Base.
#
# Usage:
#   kb_search.sh "QUERY"              # keyword (default; literal substring match across full file content)
#   kb_search.sh --semantic "QUERY"   # vector match against indexed chunks
#   kb_search.sh --hybrid "QUERY"     # both, fused with Reciprocal Rank Fusion
#
# Output: JSON {query, mode, hits: [{path, snippet, score, bundle_slug, ...}]}.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_TOKEN:?KB_TOKEN not set — populate <skill>/.kb-config first}"

MODE="keyword"
QUERY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --semantic) MODE="semantic"; shift ;;
    --hybrid)   MODE="hybrid";   shift ;;
    --keyword)  MODE="keyword";  shift ;;
    *)          QUERY+="${QUERY:+ }$1"; shift ;;
  esac
done

if [[ -z "$QUERY" ]]; then
  echo "Usage: $0 [--semantic|--hybrid|--keyword] \"QUERY\"" >&2
  exit 64
fi

ENCODE() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }

URL="$KB_WORKER_URL/search?mode=$MODE&q=$(ENCODE "$QUERY")"

curl -sf -H "Authorization: Bearer $KB_TOKEN" "$URL" || {
  echo "search failed (URL: $URL)" >&2
  exit 1
}
