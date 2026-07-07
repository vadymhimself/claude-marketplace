#!/usr/bin/env bash
# Fetch subscriptions + invoices for one Stripe customer via the GigRadar
# stripe-billing-proxy (Cloudflare Worker). The proxy is the ONLY sanctioned
# way to read live Stripe data for this skill — it exposes exactly these two
# read-only, single-customer-scoped endpoints. Never call api.stripe.com
# directly from this skill; the Worker holds the only copy of the Stripe
# secret key so it never has to touch an agent session.
#
# Credentials come from env (never hardcode, never commit):
#   STRIPE_BILLING_PROXY_URL   default: https://gigradar-stripe-proxy.scalifier.workers.dev
#   STRIPE_BILLING_PROXY_KEY   required — ask a GigRadar admin (rotatable anytime)
#
# Usage:
#   export STRIPE_BILLING_PROXY_KEY='...'
#   ./get_billing.sh cus_XXXXXXXXXXXXXX subscriptions
#   ./get_billing.sh cus_XXXXXXXXXXXXXX invoices
#   ./get_billing.sh cus_XXXXXXXXXXXXXX both   # default
set -euo pipefail

CUSTOMER="${1:?usage: get_billing.sh <cus_id> [subscriptions|invoices|both]}"
MODE="${2:-both}"
BASE="${STRIPE_BILLING_PROXY_URL:-https://gigradar-stripe-proxy.scalifier.workers.dev}"

if [[ -z "${STRIPE_BILLING_PROXY_KEY:-}" ]]; then
  echo "STRIPE_BILLING_PROXY_KEY is required (ask a GigRadar admin for the current key)" >&2
  exit 1
fi

fetch() {
  local path="$1"
  curl -sS -H "Authorization: Bearer ${STRIPE_BILLING_PROXY_KEY}" \
    "${BASE}${path}?customer=${CUSTOMER}"
}

case "$MODE" in
  subscriptions) fetch "/subscriptions" | jq .;;
  invoices)      fetch "/invoices" | jq .;;
  both)
    echo "=== subscriptions ==="
    fetch "/subscriptions" | jq .
    echo "=== invoices ==="
    fetch "/invoices" | jq .
    ;;
  *) echo "unknown mode: $MODE (expected subscriptions|invoices|both)" >&2; exit 1;;
esac
