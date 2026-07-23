# GigRadar DFY — Projected Upwork Earnings Model (extracted from `dimap-stack/dfy-final`)

Repo cloned to: `.../earnings/dfy-final`. All citations are paths inside that clone.
This is the production proposal/calculator engine GigRadar's AEs use to build per-prospect
DFY earnings projections + ROI. It is NOT reconstructed — every number below is lifted verbatim.

---

## 1. THE FUNNEL MODEL (inputs → formulas → outputs)

Single source of truth: `lib/calc-core.mjs` → `calcFunnel()` (mirrored inline in
`public/sql-calculator/index.html:1681`). Pure monthly-anchored math, no time ramp in the
formula itself (the ramp is the qualitative 8-week timeline in §3).

### Inputs (per niche / per prospect)
| Input | Meaning | Source |
|---|---|---|
| `bidsPerQuarter` | proposals sent per quarter (GigRadar auto-bidding volume) | niche table, `index.html:1646+` |
| `lrr` | lead/reply rate = replies ÷ proposals | niche table + BENCH_BOUNDS |
| `rtc` | reply-to-call rate = SQLs ÷ replies | niche table |
| `ctp` | call-to-paid (close) rate = clients ÷ SQLs | niche table |
| `bidPrice` | $ of Upwork Connects spent per proposal | niche table |
| `avgContractValue` | avg $ billed per won client | UI input, default **$8,000** (`index.html:647`) |
| `margin` | profit margin on delivered revenue | UI input, default **40%** (`index.html:660`) |
| `dfyCostMonth` | GigRadar DFY service fee /month | UI input, default **$3,900** (`index.html:681`) |

### Core funnel formulas (`lib/calc-core.mjs:74-119`)
```
proposalsMonth   = bidsPerQuarter / 3
repliesMonth     = proposalsMonth × lrr
sqlsMonth        = repliesMonth   × rtc            // SQL = qualified sales call
clientsMonth     = sqlsMonth      × ctp
connectCostMonth = proposalsMonth × bidPrice       // Upwork Connects spend
totalCostMonth   = connectCostMonth + dfyCostMonth
connectsPerBid   = round(bidPrice / 0.15)          // $0.15 = price per Connect
```

### Revenue + profit chain (`index.html:3279-3297`)
```
annualClients         = round(clientsMonth × 12)
additionalRevenueYear = annualClients × avgContractValue
deliveryCost          = additionalRevenueYear × (1 − margin)
dfyAnnualCost         = dfyCostMonth × 12
connectsAnnual        = connectCostMonth × 12        // (0 in SaaS mode — bundled)
netProfit             = additionalRevenueYear − deliveryCost − dfyAnnualCost − connectsAnnual
totalAnnualExpenses   = deliveryCost + dfyAnnualCost + connectsAnnual
ROI                   = netProfit / totalAnnualExpenses × 100
costPerSQL            = totalAnnualCost / round(sqlsMonth × 12)
CAC                   = totalAnnualCost / round(clientsMonth × 12)
revenueMultiple       = after / today (annual run-rate)
monthsToTarget        = ceil(clientsNeeded × 12 / planClientsPerYear)   // index.html:3117
```
Period scaling: monthly=÷12-mult, quarterly=×3, semi-annual=×6, annual=×1. Any period-scaled
input must be normalized back to /month before `calcFunnel` (`normalizeDfyCostToMonth`,
`calc-core.mjs:50`) — forgetting this caused 4×/12× inflation bugs.

### 3-tier scenario projection (`calc-core.mjs:168-206`, `buildTieredNd`)
Conservative / Likely / Best Case, kept monotonic. Multipliers applied to the Likely base:
| Tier | lrr | rtc | ctp |
|---|---|---|---|
| Conservative | ×0.80–0.85 (or niche 99% lower bound) | ×0.85 | ×0.90 |
| Likely | niche mean (or user-typed) | ×1.0 | ×1.0 |
| Best Case | ×1.15–1.18 (or niche 99% upper bound) | ×1.15 | ×1.10 |

---

## 2. BENCHMARK NUMBERS (the defensible defaults)

### Per-niche funnel defaults — `public/sql-calculator/index.html:1646-1764`
| Niche key | bidsPerQuarter | lrr (reply) | rtc (reply→call) | ctp (close) | bidPrice | defaultContract | dealCycleDays |
|---|---|---|---|---|---|---|---|
| ppc (PPC/Ads) | 1050 | 10.0% | 32% | 35% | $2.00 | $28,000 | 14 |
| seo | 1050 | 8.3% | 28% | 35% | $2.00 | $24,000 | 12 |
| email_warm | 900 | 10.5% | 30% | 35% | $2.00 | $21,000 | 10 |
| email_cold | 1200 | 9.6% | 30% | 35% | $2.00 | $24,000 | 14 |
| legal | 600 | 10.5% | 32% | 38% | $1.50 | $35,000 | 10 |
| finance | 600 | 10.3% | 30% | 35% | $2.00 | $30,000 | 12 |
| ai/ML | 1200 | 8.1% | 28% | 30% | $2.00 | $35,000 | 18 |
| webdev | 1200 | 7.7% | 26% | 25% | $2.00 | $28,000 | 14 |
| appdev | 1200 | 7.3% | 25% | 25% | $2.00 | $35,000 | 18 |
| design | 1200 | 7.5% | 28% | 25% | $2.00 | $21,000 | 12 |
| crm/ERP/SaaS | 600 | 7.0% | 28% | 28% | $1.50 | $30,000 | 16 |
| ecom | 1200 | 7.5% | 27% | 25% | $2.00 | $24,000 | 14 |
| csm (VA/Support) | 900 | 8.8% | 30% | 28% | $2.00 | $18,000 | 8 |

**Headline default funnel** (no niche selected): ≈1200 bids/qtr = **400 proposals/mo**,
lrr ≈ 7.5–8%, rtc ≈ 28%, ctp ≈ 25%, contract $8,000.

### Reply-rate 99% CI per niche — `lib/proposal-niche-data.ts:49-64` (BENCH_BOUNDS, 1,433 teams × 239 days)
| Niche | lower | mean | upper |
|---|---|---|---|
| Web & Software Dev | 7.12% | 7.62% | 8.13% |
| Mobile App Dev | 6.85% | 7.30% | 7.75% |
| E-commerce Dev | 7.05% | 7.49% | 7.93% |
| AI & ML | 7.16% | 7.67% | 8.17% |
| Data/Analytics/ETL | 7.50% | 8.07% | 8.64% |
| DevOps/Cloud | 7.20% | 7.74% | 8.28% |
| QA & Testing | 6.28% | 6.80% | 7.31% |
| UX/UI & Design | 6.88% | 7.38% | 7.88% |
| Digital Marketing & SEO | 8.67% | 9.42% | 10.18% |
| Lead Gen & Sales | 8.59% | 9.39% | 10.19% |
| Writing & Content | 8.83% | 9.62% | 10.41% |
| Video & Creative | 9.50% | 10.28% | 11.06% |
| VA/Admin/Support | 7.75% | 8.40% | 9.05% |
| Finance/Accounting/Legal | 8.40% | 9.14% | 9.87% |

**Upwork-wide median reply rate (all bidders): 4.0%** — `proposal-niche-data.ts:68`
(`UPWORK_GLOBAL_MEDIAN_LRR = 0.040`). This is the "floor" GigRadar beats ~2×.

### Other benchmark constants
- **Connect price: $0.15** each (`calc-core.mjs:114`, `index.html:3010`).
- **View rate** (proposal seen by client): ~25–40% (`index.html:3459`: `0.25 + random×0.15`).
- **Upwork sales cycle: averages 3 weeks reply→close** (`proposal-content-data.ts:93`, SaaS timeline).
- Avg job size per niche $3,670–$17,083 (`proposal-niche-data.ts:31-44`, NICHE_MARKET).

### Customer proof stats — `lib/proposal-content-data.ts:25-29` (DFY_PROOF_STATS)
- Momin · Works of Web: **4% → 18.88% reply rate, 8 weeks after switching to DFY**.
- Dom · Oakhurst: **$6,000 first DFY-sourced contract, closed in 2 weeks**.
- Yasir · Smart Solutions CPA: **30 qualified replies / 90 days, first quarter on DFY**.
- Company-wide: **800+ agencies, $45M+ attributed earnings** (`proposal-content-data.ts:64`).

---

## 3. THE RAMP / ROADMAP (the "8-week Upwork account build-up")

DFY week-by-week — `lib/proposal-content-data.ts:71-77` (TIMELINE_PHASES_DFY). Rendered as
"What the first 90 days actually look like" (`lib/proposal-pages/Timeline.tsx`).

| Phase | Title | What happens | Milestone metric |
|---|---|---|---|
| **Week 1** | Setup | Team rebuilds profile, configures niche scanner, locks ICP targeting, onboarding call, Connects topped up | Profile + scanner live by day 7 |
| **Week 2-3** | Launch | Daily proposals start; niche-tuned cover letters in client's voice; bid timing optimized; first reply data | ~50-100 proposals sent · first replies |
| **Week 4** | First booked calls | Qualified replies → discovery calls; first weekly report (full funnel + top objections) | 2-4 sales calls on calendar |
| **Week 5-8** | Steady flow | Pipeline rhythm; weekly cover-letter A/B testing; calendar fills | Weekly cadence · A/B tests compounding |
| **Day 56-90** | Scale | Volume + win-rate tuned to data; profile equity (JSS, reviews) compounds; 90-day renewal review | JSS + reviews compound · renewal decision |

SaaS variant (self-serve) — `proposal-content-data.ts:89-95`: Week 1 Integration → Week 2 AI
training → Week 3-5 Funnel live → Week 5-6 First client lands → Months 2-3 Algorithm tilts.
Note SaaS shows **first client by week 5-6** (vs DFY first calls week 4).

Operating cadence: DFY = team runs daily ops, client takes qualified calls only, ~"sales calls
only"; SaaS = client runs platform ~15-30 min/day (`Timeline.tsx:63-65`).

---

## 4. PRICING / COST (for the ROI side)

### DFY service fee
- **Public-facing floor: $1,300/mo** ("cheapest tier · final price set on demo call") — `index.html:676-677`.
- **Internal/default model fee: $3,900/mo** — `index.html:681` (the calc's editable default).
- AE-editable with $ or % discount field.
- Implied range for a DFY calculator slider: **$1,300 – $5,000/mo** (Offer page also references
  "$3-5K/mo + risk" framing, `lib/proposal-pages/Offer.tsx:153`).

### SaaS plans (canonical) — `lib/saas-plans.ts:47-89`
| Plan | Period fee | Setup fee | Proposals incl. | /mo equiv | Months |
|---|---|---|---|---|---|
| Quarterly Standard | $1,510 | $250 | 700 | $503.33/mo | 3 |
| Easy Plan Quarterly | $790 | $250 | 300 | $263.33/mo | 3 (income <$3k/mo only) |
| Semi Annual | $2,250 | $250 | 1,400 | $375/mo | 6 |
| Annual Standard | $3,000 | $250 | 2,800 | $250/mo | 12 |

**Setup fee: $250 one-time** (all SaaS plans, AE may waive).

### Connects / credits
- Connect price **$0.15** each; ~13 Connects per bid at $2.00 bidPrice.
- DFY connect cost is a real line item (≈ proposalsMonth × $2 × 12/yr); in SaaS it's bundled (=0).
- PAYG credit bundles — `saas-plans.ts:101-106`: $100→50cr, $500→250cr, $1,000→1,000cr,
  $2,000→4,000cr. Blended overage cost quoted at **$0.75/credit** (`saas-plans.ts:113`).

---

## 5. CALCULATOR IMPLEMENTATION (for re-building interactive HTML)

Verbatim math copied to `./calc-core.mjs` in this folder. Default UI input values
(`public/sql-calculator/index.html`):
- Current Revenue/yr: **$120,000** (`:619`)
- Target Revenue/yr: **$300,000** (`:633`)
- Avg Contract Value: **$8,000** (`:647`)
- Margin: **40%** (`:660`)
- DFY Fee/mo: **$3,900** (default) / **$1,300** (public floor) (`:676,:681`)
- Discount: $0 default, $/% toggle (`:688`)

Suggested slider ranges for a rebuilt calculator (derived from niche table spread):
- Proposals/mo: 200–400 (bidsPerQuarter 600–1200 ÷3)
- Reply rate: 4% (Upwork floor) – 19% (best DFY case); niche means 7–10%
- Reply→call (rtc): 25–32%
- Close (ctp): 25–38%
- Avg contract: $8,000–$35,000
- DFY fee/mo: $1,300–$5,000
- Margin: 30–60%

Three scenario toggle (Conservative/Likely/Best) using the §1 multipliers.
Output KPIs the calc displays: Additional Revenue, Net Profit, ROI %, Revenue Multiple,
Months to Target, New Clients/period, Cost per SQL, CAC.

### Worked example (default headline inputs, Likely tier)
400 proposals/mo × 7.5% lrr = 30 replies/mo × 28% = 8.4 SQLs/mo × 25% = 2.1 clients/mo
→ ~25 clients/yr × $8,000 = **$200k added revenue/yr**.
Cost: DFY $3,900×12 = $46.8k + connects (400×$2×12=$9.6k) = ~$56.4k.
Delivery cost @60% = $120k. Net profit ≈ $200k − $120k − $56.4k ≈ **$23.6k**, and the revenue
multiple/ROI framing is what the proposal leads with. (Higher-contract niches like AI/legal at
$35k contract produce dramatically better ROI — that's the lever to showcase.)
