#!/usr/bin/env bash
# bootstrap_all.sh — runs the full KB stack setup end-to-end.
#
# Reads config from $HOME/.kb-bootstrap.env (or env vars).
# Each step is idempotent — safe to re-run after fixing failures.

set -euo pipefail

# Load config
[[ -f "${HOME}/.kb-bootstrap.env" ]] && source "${HOME}/.kb-bootstrap.env"
: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN must be set}"
: "${CLOUDFLARE_ACCOUNT_ID:?CLOUDFLARE_ACCOUNT_ID must be set}"
: "${KB_NAME:?KB_NAME must be set (slug for this knowledge base, e.g. gigradar-kb)}"
: "${WORKERS_SUBDOMAIN:?WORKERS_SUBDOMAIN must be set}"
: "${KB_REGION:=auto}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(dirname "$SCRIPT_DIR")"

echo "═══════════════════════════════════════════════════════════"
echo "  KB Bootstrap"
echo "  KB name:     $KB_NAME"
echo "  Account:     $CLOUDFLARE_ACCOUNT_ID"
echo "  R2 region:   $KB_REGION"
echo "  Workers:     *.${WORKERS_SUBDOMAIN}.workers.dev"
echo "═══════════════════════════════════════════════════════════"
echo

bash "$SCRIPT_DIR/01_create_r2_bucket.sh"
bash "$SCRIPT_DIR/02_create_vectorize_index.sh"
bash "$SCRIPT_DIR/03_deploy_indexer_worker.sh"
bash "$SCRIPT_DIR/04_deploy_search_worker.sh"
bash "$SCRIPT_DIR/06_configure_consumer_skill.sh"

echo
echo "═══════════════════════════════════════════════════════════"
echo "  Bootstrap complete."
echo "═══════════════════════════════════════════════════════════"
echo
echo "  Search Worker URL:"
echo "    https://${KB_NAME}-search.${WORKERS_SUBDOMAIN}.workers.dev"
echo
echo "  Consumer skill configured at:"
TARGET_DEFAULT="$(dirname "$SKILL_ROOT")/knowledge-base"
echo "    $TARGET_DEFAULT/.kb-config"
echo
echo "  Next:"
echo "    1. bash scripts/05_sync_folder_to_r2.sh /path/to/your/research"
echo "    2. wait 5 min for the indexer cron to embed the content (or run kb_reindex.sh from the consumer skill)"
echo
