---
name: stripe-billing
description: >-
  Look up a GigRadar team's Stripe billing status — subscriptions (with statuses) and
  invoices (with statuses, amounts, and line-item detail) — via the read-only
  stripe-billing-proxy Cloudflare Worker, resolving the team's Stripe customer id(s) from
  the Mongo `teams` collection first. Use whenever a GM, Growth, or Success Manager asks
  about a team's payment status, subscription state, invoice history, whether a team is
  past due / canceled / active, or "what has this team paid us". Trigger phrases include
  "check billing for team X", "is <team> past due", "what's their subscription status",
  "pull invoices for <team/email>", "did they pay last month", "billing history for
  <customer>". Not for market-wide billing analytics or Stripe meter/price configuration —
  this is a single-team, read-only lookup.
---

# /stripe-billing

Two-step lookup: **resolve** the team's Stripe customer id(s) from Mongo, then **fetch**
subscriptions/invoices for each via the `stripe-billing-proxy` Worker. The proxy is the
only sanctioned path to live Stripe data from this skill — never call `api.stripe.com`
directly, and never ask for or embed the raw Stripe secret key. See
`references/proxy-reference.md` for the full proxy contract, example payloads, and how to
read the fields.

## Why two steps

A GigRadar team is not one Stripe customer — it can be **up to three**, one per product:

| Product | Mongo field | 
|---|---|
| `leads` (core proposals) | `teams.subscription.stripe.customer` |
| `api` | `teams.apiSubscription.stripe.customer` |
| `profiles` | `teams.profilesSubscription.stripe.customer` |

See `../../references/data-reference.md` §7 (Billing) for the canonical field map — this
skill's `resolve_customer_ids.py` implements exactly that lookup. Always resolve all three
before answering "what has this team paid" — a team can be active on `leads` and canceled
on `profiles` at the same time, and reporting only one product's number is misleading.

## Step 0 — check credentials before doing anything else

Run `[ -n "$MONGO_URI" ] && echo mongo:ok; [ -n "$STRIPE_BILLING_PROXY_KEY" ] && echo proxy:ok`.

If either is missing: **stop and ask the user directly** for the missing value(s) — do not
guess, do not fall back to a different Mongo URI or proxy key you've seen elsewhere, do not
silently skip the check and let the script fail three steps later. `MONGO_URI` is the same
credential used across every skill in this plugin, so if the user has already provided it in
this session for another skill, reuse it. `STRIPE_BILLING_PROXY_KEY` is specific to this skill
and worker — if the user hasn't given it and doesn't say where to find it, ask "what's the
`stripe-billing-proxy` key?" rather than inventing one or trying `sk_live_...`/`sk_test_...`
values (this skill never uses a raw Stripe key at all — see "What NOT to do" below).

## Steps

1. **Parse the team identifier** from the user's request: Mongo `_id` (24-char ObjectId),
   an email (`teams.name` is usually the owner's email), or a free-text team-name substring.

2. **Resolve customer id(s):**
   ```bash
   export MONGO_URI='mongodb://researcher-prod:<pw>@<mongo-host>:<port>/gigradar-dev?authSource=admin'
   python3 scripts/resolve_customer_ids.py --email someone@example.com
   # or --team-oid <24-char-oid>  /  --team-name "<substring>"
   ```
   Returns `{teamId, name, customers: {leads, api, profiles}}` — any product with no active
   Stripe link comes back `null`. Pull the `.customer` id out of whichever product(s) the
   user is asking about (default: all non-null ones).

3. **Fetch billing per customer id:**
   ```bash
   export STRIPE_BILLING_PROXY_KEY='<ask a GigRadar admin>'
   ./scripts/get_billing.sh cus_XXXXXXXXXXXXXX both
   ```
   Run once per resolved customer id (a team asking about "billing" broadly may need this run
   2-3 times, once per product).

4. **Answer using the field guide in `references/proxy-reference.md`** — amounts are cents,
   subscription/invoice statuses are standard Stripe enums, a team can have multiple
   subscriptions per customer (upgrades create new ones), invoice totals can be negative
   (credit notes).

## What NOT to do

- **Do NOT call Stripe directly.** No `api.stripe.com` requests, no asking the user to paste
  a Stripe secret key. The proxy is the only path — it's scoped to these two GET routes and
  never exposes the underlying `sk_live_...` key.
- **Do NOT assume one customer id per team.** Always check all three product fields before
  reporting "no subscription" — a null `leads` customer with an active `api` customer is a
  live, paying team.
- **Do NOT hardcode or persist `STRIPE_BILLING_PROXY_KEY`.** It's a rotatable shared secret;
  read it from the environment every time, same as `MONGO_URI` / `ES_PASS` elsewhere in this
  plugin.
- **Do NOT treat this as a market-wide billing tool.** For aggregate revenue/MRR analysis
  across many teams, that's a different job (raw Mongo `usage`/`teams` aggregation, not this
  per-customer proxy) — this skill is for one team at a time.
- **Do NOT guess, reuse-from-memory, or fabricate `STRIPE_BILLING_PROXY_KEY`.** If Step 0
  shows it's unset, ask the user — don't try old keys you recall from a prior session (it's
  rotatable, so a stale one will just 401) and don't ever substitute a raw Stripe key instead.

## Required env vars

| Var | Required | Default | Purpose |
|---|---|---|---|
| `MONGO_URI` | **yes** | — | Same Mongo credentials used by every other skill in this plugin |
| `MONGO_DB` | no | `gigradar-dev` | Mongo database |
| `STRIPE_BILLING_PROXY_URL` | no | `https://gigradar-stripe-proxy.scalifier.workers.dev` | Worker base URL |
| `STRIPE_BILLING_PROXY_KEY` | **yes** | — | Shared proxy key — ask a GigRadar admin, rotatable anytime |

Python deps: `pymongo` (already required by the rest of the plugin).
