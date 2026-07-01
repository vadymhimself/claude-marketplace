# Recon recipe — lead dossier + real brand assets

Goal: a dossier the proposal fills from, plus the lead's **real headshot + company
logo** (the single biggest "this was made for me" signal).

## Dossier (what to return)
- Company: one-paragraph summary, location, founded year, positioning/tagline,
  brand voice (so the copy matches their tone).
- **Core services** (~6) — the ones with Upwork demand. Map each to how it sells on
  Upwork (entry gig → retainer expansion).
- Founder: name, real title (people undersell themselves in email sigs — check the
  site/LinkedIn for the true title), background, credentials.
- `serviceTags` for case-study matching, from the taxonomy in
  `assets/case-studies/case-studies.json` (lead-gen, cold-email, email-marketing,
  revops, crm, data-analytics, web-development, software-development, design-ux,
  ecommerce, ai-automation, ppc-ads, bookkeeping-finance, customer-support,
  bpo-staffing, market-research, consulting, …).

## Extracting the photo + logo (the tricky part)
Many sites (Webflow/Next/Framer) inline images as base64 with **no external URL**,
and the favicon is often the platform's default (e.g. Webflow's "W", or a
WordPress mark) — not the brand. So:

1. `curl -sL -A "Mozilla/5.0" <site>/ <site>/about` → save HTML.
2. Extract every `data:image/...;base64,...`, decode, and check dimensions with
   `sips -g pixelWidth -g pixelHeight`. Founder photos are the largest portraits
   (often 400–800px square); match people to `alt="..."` order in the HTML
   (founder is usually first / highest-res). The logo is a small mark near the nav.
3. If no usable logo on-site, try the company domain's favicon
   (`https://www.google.com/s2/favicons?domain=<domain>&sz=128`) — but **reject
   generic/platform icons** (WordPress, Webflow, a plain colored dot). A wrong logo
   is worse than none; the template handles a missing logo gracefully.
4. If no headshot anywhere, use Clay enrichment
   (`find-and-enrich-contacts-at-company`) to get a LinkedIn photo URL, else fall
   back to the initials monogram (template default).
5. **Resize before saving** so the inlined base64 stays small (the whole page is
   one self-contained file — a 1700px headshot bloats it to ~2 MB). Cap the photo
   at ~480px and the logo at ~256px: `sips -Z 480 in.jpg --out photo.jpg`. Then
   reference them in `lead-data.json` as `"@file:<path>"` — `build.py` inlines them.
6. **Logo visibility:** the page background is white/light. If the logo is
   white-on-transparent (it'll vanish), recolor the wordmark to a dark ink
   (`#0a0f1f`) while preserving alpha, or use the lead's dark/full-color mark
   instead. Verify it's visible on white before using it.

## Cautions
- LinkedIn blocks scrapers; Perplexity's index or the company's own About page are
  more reliable for bio facts.
- Verify the true title — the proposal should address a founder as a founder.
- Note honest gaps (headcount, etc.); don't invent.
