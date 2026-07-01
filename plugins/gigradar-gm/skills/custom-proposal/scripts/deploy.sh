#!/usr/bin/env bash
# deploy.sh <proposal.html> <slug>
# Publishes a proposal to https://<slug>.via.gigradar.io (Cloudflare Worker + R2).
# The page auto-tracks engagement in PostHog (project GigRadar), keyed by the slug.
#
# Requires GIGRADAR_PROPOSAL_TOKEN in the environment — your personal
# "proposals-push" token, provisioned via the ai-researcher-users Terraform
# scheme (set access.proposals_push: true in users.yaml) and emailed to
# ai-researcher-users@gigradar.io. It only grants publishing to via.gigradar.io.
set -euo pipefail

HTML="${1:?usage: deploy.sh <proposal.html> <slug>}"
SLUG="${2:?usage: deploy.sh <proposal.html> <slug>   (e.g. satiesh-sheriff)}"
: "${GIGRADAR_PROPOSAL_TOKEN:?set GIGRADAR_PROPOSAL_TOKEN (your proposals-push token)}"

# normalize slug: lowercase, spaces/underscores -> hyphens, strip other chars
SLUG=$(printf '%s' "$SLUG" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd 'a-z0-9-' | sed -E 's/-+/-/g;s/^-|-$//g')
URL="https://${SLUG}.via.gigradar.io/"

code=$(curl -sS -o /tmp/gr_deploy_resp.json -w '%{http_code}' -X PUT "$URL" \
  -H "Authorization: Bearer ${GIGRADAR_PROPOSAL_TOKEN}" \
  -H "Content-Type: text/html; charset=utf-8" \
  --data-binary "@${HTML}")

if [ "$code" = "200" ]; then
  echo "✓ published → ${URL}"
  echo "  analytics: PostHog (proposal_slug=${SLUG}) — views, scroll, book-demo clicks, modal opens, calculator use"
else
  echo "✗ deploy failed (HTTP ${code}):"; cat /tmp/gr_deploy_resp.json 2>/dev/null; echo; exit 1
fi
