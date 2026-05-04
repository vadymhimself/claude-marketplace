#!/usr/bin/env bash
# 05_sync_folder_to_r2.sh — upload every file in a local folder to R2.
#
# Uses the Cloudflare API directly (no AWS S3 client required).
# Skips dotfiles, dotdirectories, and binary files larger than 50 MB.
#
# Usage: bash 05_sync_folder_to_r2.sh /path/to/folder

set -euo pipefail
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${KB_NAME:?}"

[[ $# -lt 1 ]] && { echo "Usage: $0 <folder> [--auto-readme]" >&2; exit 64; }
SRC="$(cd "$1" && pwd)"
[[ ! -d "$SRC" ]] && { echo "not a directory: $SRC" >&2; exit 66; }

AUTO_README=0
[[ "${2:-}" == "--auto-readme" ]] && AUTO_README=1

# Pre-check: every direct subfolder must have README.md
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MISSING=()
for d in "$SRC"/*/; do
  [[ -d "$d" ]] && [[ ! -f "$d/README.md" ]] && MISSING+=("$(basename "$d")")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo
  echo "⚠  These bundles are missing README.md (citation manual):"
  for s in "${MISSING[@]}"; do echo "     $s/"; done
  echo
  if [[ $AUTO_README -eq 1 ]]; then
    echo "→ Auto-generating stub READMEs (you'll need to edit them before publishing)"
    bash "$SCRIPT_DIR/generate_missing_readmes.sh" "$SRC"
    echo
  else
    echo "Without a README, AI agents won't know how to cite content from these bundles."
    echo
    echo "Either:"
    echo "  1. Re-run with --auto-readme to generate stub READMEs you can edit:"
    echo "       bash $0 \"$SRC\" --auto-readme"
    echo "  2. Or write the READMEs yourself, then re-run this sync."
    echo
    echo "Aborting."
    exit 1
  fi
fi

API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/r2/buckets/$KB_NAME/objects"
MAX_BYTES=$((50 * 1024 * 1024))

echo "→ Syncing $SRC → R2 bucket $KB_NAME"

UPLOADED=0
SKIPPED=0

while IFS= read -r -d '' f; do
  # skip dotfiles/dotdirs
  case "$f" in *"/."*) SKIPPED=$((SKIPPED+1)); continue ;; esac
  size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
  if [[ "$size" -gt "$MAX_BYTES" ]]; then
    echo "  ⚠ skip (>50 MB): ${f#$SRC/}"
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  KEY="${f#$SRC/}"
  CT=$(file --mime-type -b "$f")
  curl -sX PUT "$API/$KEY" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    -H "Content-Type: $CT" \
    --data-binary @"$f" > /dev/null
  UPLOADED=$((UPLOADED+1))
  if [[ $((UPLOADED % 10)) -eq 0 ]]; then
    echo "  uploaded $UPLOADED files…"
  fi
done < <(find "$SRC" -type f -print0)

echo "  ✓ uploaded $UPLOADED files (skipped $SKIPPED)"
echo
echo "  Indexer Worker will pick up changes on the next 5-min cron tick."
echo "  To trigger immediately:"
echo "    curl -X POST https://${KB_NAME}-indexer.${WORKERS_SUBDOMAIN:-<your-subdomain>}.workers.dev/reindex"
