#!/usr/bin/env bash
# kb_reindex.sh — trigger an immediate indexer pass (don't wait for the cron).
#
# Usage:
#   kb_reindex.sh                      # default batch
#   kb_reindex.sh --limit 50           # process up to 50 changed files
#
# Requires KB_WRITE_TOKEN in <skill>/.kb-config. The indexer cron runs every
# 5 minutes anyway — call this only when you need fresh content searchable now.
#
# Output: JSON {total_files, indexed, unchanged, skipped, errors, ...}.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/../.kb-config" ]] && source "$HERE/../.kb-config"
: "${KB_WORKER_URL:?KB_WORKER_URL not set — populate <skill>/.kb-config first}"
: "${KB_WRITE_TOKEN:?KB_WRITE_TOKEN not set — read-only KB_TOKEN cannot trigger reindex.}"

LIMIT=20
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    *)       echo "Usage: $0 [--limit N]" >&2; exit 64 ;;
  esac
done

curl -sf -X POST "$KB_WORKER_URL/reindex?limit=$LIMIT" \
  -H "Authorization: Bearer $KB_WRITE_TOKEN" || {
  echo "reindex failed" >&2
  exit 1
}
echo
