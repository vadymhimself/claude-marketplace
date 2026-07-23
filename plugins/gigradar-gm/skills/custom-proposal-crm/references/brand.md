# GigRadar brand tokens (gigradar.io)

The template already encodes these — reference only if editing styles.

- **Fonts:** Inter / "SF Pro Display" (`"SF Pro Display",-apple-system,BlinkMacSystemFont,"Inter",sans-serif`).
- **Primary blue:** `#216bef` → gradient to `#1852d3` / `#1d4ed8`. Lighter: `#3b82f6`, `#5dadfd`.
- **Light backgrounds:** `#eff6ff` (blue-50), `#dbeafe`, `#f4f8ff`, `#eff7ff`.
- **Dark:** `#0a0f1f` (near-black, used for calculator + final CTA + footer), slate `#2d323e`.
- **Text:** ink `#0a0f1f`, slate `#4a5568`, muted `#7a89a3`.
- **Accents:** green `#27AE60` ($ figures / success), amber `#f59e0b` (rating stars only), red `#EC0A0A` (sparingly).
- **Shape:** generously rounded (cards ~16px, pills 999px), soft blue-tinted shadows
  (e.g. `0 24px 64px -20px rgba(33,107,239,.26)`), gradient-text on hero headline words.
- **Feel:** bright, white/light-blue, lots of whitespace; dark panels only for the
  calculator, the final CTA, and the footer (as on gigradar.io).

Personalization rule: the **page** is GigRadar brand; only the **hero lockup**
carries the lead's photo + logo. The message is "GigRadar built this *for you*".

Booking: the demo modal embeds `https://meetings.hubspot.com/gigradar/upwork-growth-hacking`
via a lazy direct `<iframe src=...?embed=true>` (HubSpot allows framing; the official
embed *script* does NOT work when injected after page load, so the template uses a
direct iframe). Keep this `demoUrl` default for all leads.
