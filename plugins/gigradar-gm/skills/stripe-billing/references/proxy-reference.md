# stripe-billing-proxy — reference

A minimal Cloudflare Worker that proxies exactly two **read-only**, single-customer-scoped
Stripe calls. It exists so agents (and anyone else) can pull a team's billing status
without ever touching the live Stripe secret key.

- Repo/worker: `gigradar-stripe-proxy` (Cloudflare Worker, source in `gigradar-monorepo/gigradar-stripe-proxy/`)
- Default URL: `https://gigradar-stripe-proxy.scalifier.workers.dev` (override via `STRIPE_BILLING_PROXY_URL`)
- Auth: `Authorization: Bearer <STRIPE_BILLING_PROXY_KEY>` or `X-Proxy-Key: <key>`. The key is a
  Cloudflare-secret shared value, unrelated to the Stripe key itself, and rotatable at any time
  by whoever administers the worker. **It is never committed to this repo.** Ask a GigRadar admin
  for the current value and export it as `STRIPE_BILLING_PROXY_KEY` in your shell.
- Everything else 404s. Non-GET → 405. Missing/bad key → 401. It is not a general Stripe
  passthrough — only these two routes, only the customer supplied in `?customer=`.

## Endpoints

### `GET /subscriptions?customer=cus_xxx`

All subscriptions for that customer (`status=all`, auto-paginated), with items/pricing detail.

### `GET /invoices?customer=cus_xxx`

All invoices for that customer, with full line-item detail (auto-paginated, including
per-invoice line pagination for invoices with many items).

**All Stripe amounts are in minor units (cents).** Divide by 100 for a display dollar figure.

## Example return values

Sanitized/fabricated example — same field shape returned by the live proxy, IDs replaced.

`GET /subscriptions?customer=cus_EXAMPLE00000001`:

```json
{
  "customer": "cus_EXAMPLE00000001",
  "count": 2,
  "subscriptions": [
    {
      "id": "sub_EXAMPLE1",
      "status": "active",
      "currency": "usd",
      "created": 1710509406,
      "current_period_start": 1748457806,
      "current_period_end": 1751049806,
      "cancel_at_period_end": false,
      "items": [
        {
          "id": "si_EXAMPLE1",
          "quantity": 1,
          "price_id": "price_EXAMPLE1",
          "unit_amount": 24900,
          "currency": "usd",
          "interval": "month",
          "usage_type": "licensed"
        }
      ]
    },
    {
      "id": "sub_EXAMPLE0",
      "status": "canceled",
      "currency": "usd",
      "created": 1690000000,
      "canceled_at": 1700000000,
      "current_period_end": 1702592000,
      "items": [
        { "id": "si_EXAMPLE0", "quantity": 1, "price_id": "price_OLD", "unit_amount": 8000, "currency": "usd", "interval": "month" }
      ]
    }
  ]
}
```

**How to read it:**
- `status` — `active`, `trialing`, `past_due`, `canceled`, `incomplete`, `incomplete_expired`,
  `unpaid`, `paused` (standard Stripe subscription statuses).
- A team can have **more than one subscription per customer** (upgrades/downgrades create a
  new subscription rather than always mutating the old one in place) — don't assume `count: 1`.
- `unit_amount` is cents. `interval` + `interval_count` (when present) give the billing cadence.
- `usage_type: "metered"` items are billed via Stripe usage records (GigRadar's per-algorithm
  meters — see `../../../references/data-reference.md` §7) — `unit_amount` on a metered price is
  the per-unit rate, not a flat recurring charge.

`GET /invoices?customer=cus_EXAMPLE00000001`:

```json
{
  "customer": "cus_EXAMPLE00000001",
  "count": 2,
  "invoices": [
    {
      "id": "in_EXAMPLE2",
      "number": "EXAMPLE-0002",
      "status": "paid",
      "currency": "usd",
      "total": 24900,
      "amount_paid": 24900,
      "amount_remaining": 0,
      "created": 1748457806,
      "period_start": 1748457806,
      "period_end": 1751049806,
      "subscription": "sub_EXAMPLE1",
      "hosted_invoice_url": "https://invoice.stripe.com/i/EXAMPLE",
      "lines": [
        { "id": "il_EXAMPLE2a", "description": "1 x Team Plan (at $249.00 / month)", "quantity": 1, "amount": 24900, "currency": "usd" }
      ]
    },
    {
      "id": "in_EXAMPLE1",
      "number": "EXAMPLE-0001",
      "status": "void",
      "currency": "usd",
      "total": 10000,
      "amount_paid": 0,
      "amount_remaining": 0,
      "created": 1745779806,
      "lines": [
        { "id": "il_EXAMPLE1a", "description": "Usage this billing period (algorithm meter)", "quantity": 1, "amount": 10000, "currency": "usd" }
      ]
    }
  ]
}
```

**How to read it:**
- `status` — `draft`, `open` (awaiting payment), `paid`, `uncollectible`, `void`.
- `total` / `amount_paid` / `amount_remaining` are cents. `total` can be **negative** for a
  credit note / adjustment invoice — don't assume non-negative when summing.
- `hosted_invoice_url` / `invoice_pdf` (when present) are safe to hand a human directly — they're
  Stripe's own customer-facing links, no proxy key needed to open them.
- `lines[].description` is the human-readable Stripe line description — usually enough to tell
  a flat subscription charge from a metered usage charge without cross-referencing prices.

## Errors

| Condition | Response |
|---|---|
| Missing/wrong `STRIPE_BILLING_PROXY_KEY` | `401 {"error":"unauthorized"}` |
| `customer` param missing or not `cus_...` shaped | `400 {"error":"invalid_or_missing_customer"}` |
| Unknown route or non-GET | `404`/`405` |
| Customer doesn't exist in Stripe | `502 {"error":"upstream_error","message":"No such customer: '...'"}` |

## Rotating the key

The proxy key is independent of the Stripe key and can be rotated at any time without
redeploying code — ask whoever administers `gigradar-stripe-proxy` (Cloudflare account) to run:

```bash
wrangler secret put PROXY_KEY   # or the equivalent Cloudflare API secret PUT
```

Then update `STRIPE_BILLING_PROXY_KEY` in your own shell/secrets store. Nothing in this plugin
needs to change.
