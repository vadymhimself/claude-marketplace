// lib/calc-core.mjs — pure calculator math, NODE + browser
//
// ⚠️ THIS IS A DUPLICATE of inline math in public/sql-calculator/index.html.
//    If you edit either side, update the other AND run:
//        node scripts/regression-sql.mjs
//    See docs/calc-invariants.md → "Sync rule".
//
// Functions mirrored here:
//   money(n)                      ← public/sql-calculator/index.html:1658
//   pct(n)                        ← public/sql-calculator/index.html:1668
//   periodToMo(period)            ← derived from index.html:1828 periodMult()
//   normalizeDfyCostToMonth(...)  ← formula from index.html:2429 (and :2961, :3110)
//   calcFunnel(nd, dfyCostMo)     ← public/sql-calculator/index.html:1681
//   getNicheLrrBounds(...)        ← public/sql-calculator/index.html:1766
//   buildTieredNd(effNd, key, bench) ← public/sql-calculator/index.html:1789

export function money(n) {
  if (n < 0) return '-' + money(-n);
  if (n >= 1000000000) return '$' + (n / 1000000000).toFixed(2) + 'B';
  if (n >= 10000000) return '$' + Math.round(n / 1000000) + 'M';
  if (n >= 1000000) return '$' + (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return '$' + Math.round(n).toLocaleString('en-US');
  if (n >= 1) return '$' + n.toFixed(0);
  return '$0';
}

export function pct(n) { return (n * 100).toFixed(1) + '%'; }

// Period helpers. periodMo is the number of months per "period unit":
//   monthly → 1, quarterly → 3, annual → 12.
export function periodToMo(period) {
  return period === 'monthly' ? 1 : period === 'quarterly' ? 3 : 12;
}

export function annualMultFor(period) {
  return 12 / periodToMo(period);
}

// Normalize a period-scaled input back to a per-month anchor.
//
// Why this exists (audit: oscillation 3× — commits f9c3255, e150b7d, 2429376):
//   The dfyCostMonth INPUT is period-scaled by data-revenue-scalable in the
//   HTML, so a user on Annual sees $15,600/yr in the field but downstream
//   math expects /month. Forgetting to divide produces a 12× inflation on
//   Annual and a 4× inflation on Quarterly — exactly the bugs that kept
//   coming back.
//
// Formula (from public/sql-calculator/index.html:2429):
//   dfyCostMonth = rawInput / (12 / annualMult)  =  rawInput / periodMo
export function normalizeDfyCostToMonth(rawInput, period) {
  const mo = periodToMo(period);
  return Math.max(rawInput / mo, 0);
}

// Inverse: given a per-month anchor, project to period-scaled display value.
// Useful for tests that want to verify round-trip stability.
export function projectMonthlyToPeriod(perMonth, period) {
  return perMonth * periodToMo(period);
}

// calcFunnel — single source of truth for SQL/client funnel math.
//
//   proposalsMonth = bidsPerQuarter / 3
//   repliesMonth   = proposalsMonth × lrr
//   sqlsMonth      = repliesMonth × rtc
//   clientsMonth   = sqlsMonth × ctp
//
// dfyCostMonthly is the per-month DFY service fee (already normalized via
// normalizeDfyCostToMonth — DO NOT pass period-scaled inputs).
//
// Returns raw + display-rounded values. Includes _audit metadata. Does NOT
// mutate any global state — the browser version sets window._mathBroken;
// here we return mathBroken on the result instead so Node tests can assert.
export function calcFunnel(nd, dfyCostMonthly) {
  const proposalsMonth = (nd && nd.bidsPerQuarter > 0) ? nd.bidsPerQuarter / 3 : 0;
  const lrr = (nd && nd.lrr > 0) ? nd.lrr : 0;
  const rtc = (nd && nd.rtc > 0) ? nd.rtc : 0;
  const ctp = (nd && nd.ctp > 0) ? nd.ctp : 0;
  const bidPrice = (nd && nd.bidPrice > 0) ? nd.bidPrice : 0;

  const repliesMonth = proposalsMonth * lrr;
  const sqlsMonth = repliesMonth * rtc;
  const clientsMonth = sqlsMonth * ctp;
  const connectCostMonth = proposalsMonth * bidPrice;
  const totalCostMonth = connectCostMonth + (dfyCostMonthly > 0 ? dfyCostMonthly : 0);

  const allInputsPositive = proposalsMonth > 0 && lrr > 0 && rtc > 0 && ctp > 0;
  const allOutputsPositive = repliesMonth > 0 && sqlsMonth > 0 && clientsMonth > 0;
  const mathBroken = allInputsPositive && !allOutputsPositive;
  const mathInputsMissing = !allInputsPositive;

  // Cost-per-acquisition uses the SAME rounded yearly counts the user sees
  // displayed so the math reconciles on paper. User-reported audit bug #6:
  // calc showed cac = $1,110 but $22,800/yr ÷ 21 displayed clients = $1,085.
  // Previously cac/costPerSQL used the raw monthly float (clientsMonth =
  // 1.711 → cac = $1,110); now uses Math.round(clientsMonth × 12) so the
  // user's mental math reconciles. Mirrors public/sql-calculator/index.html
  // line ~1731 (sync rule — see docs/calc-invariants.md §5).
  const _annualCost = totalCostMonth * 12;
  const _sqlsYearRounded = Math.round(sqlsMonth * 12);
  const _clientsYearRounded = Math.round(clientsMonth * 12);
  const costPerSQL = _sqlsYearRounded >= 1 ? _annualCost / _sqlsYearRounded : null;
  const cac = _clientsYearRounded >= 1 ? _annualCost / _clientsYearRounded : null;

  return {
    proposalsMonth, bidsMonth: Math.round(proposalsMonth),
    repliesMonth, sqlsMonth, clientsMonth,
    repliesDisplay: Math.round(repliesMonth),
    sqlsDisplay: Math.round(sqlsMonth),
    clientsDisplay: Math.max(Math.floor(clientsMonth), clientsMonth >= 0.5 ? 1 : 0),
    connectCostMonth: Math.round(connectCostMonth),
    totalCostMonth: Math.round(totalCostMonth),
    costPerSQL, cac,
    connectsPerBid: bidPrice > 0 ? Math.round(bidPrice / 0.15) : 0,
    connectsMonth: bidPrice > 0 ? Math.round(proposalsMonth) * Math.round(bidPrice / 0.15) : 0,
    mathBroken, mathInputsMissing,
    _audit: { proposalsMonth, lrr, rtc, ctp, allInputsPositive, allOutputsPositive }
  };
}

// Niche-key → bench-name map (mirrors NICHE_KEY_TO_BENCH at index.html:1737).
export const NICHE_KEY_TO_BENCH = {
  webdev:            'Web & Software Development',
  mobile:            'Mobile App Development',
  ecom:              'E-commerce Development',
  ai:                'AI & Machine Learning',
  data:              'Data, Analytics & ETL',
  devops:            'DevOps & Cloud Infrastructure',
  qa:                'QA & Testing',
  design:            'UX/UI & Graphic Design',
  digital_marketing: 'Digital Marketing & SEO',
  lead_gen:          'Lead Generation & Sales',
  content:           'Writing & Content Marketing',
  video:             'Video & Creative Production',
  admin:             'VA, Admin & Customer Support',
  finance:           'Finance, Accounting & Legal',
  ppc:        'Digital Marketing & SEO',
  seo:        'Digital Marketing & SEO',
  email_warm: 'Digital Marketing & SEO',
  email_cold: 'Lead Generation & Sales',
  legal:      'Finance, Accounting & Legal',
  appdev:     'Mobile App Development',
  crm:        'DevOps & Cloud Infrastructure',
  csm:        'VA, Admin & Customer Support'
};

// Returns { lower, mean, upper } LRR bounds for a niche key.
// nicheBenchmarks: array of { Niche, LRR_lower99, LRR_mean, LRR_upper99 }
//   — pass [] or undefined to fall back to ±15% of baseLrr.
export function getNicheLrrBounds(nicheKey, baseLrr, nicheBenchmarks) {
  const benchName = NICHE_KEY_TO_BENCH[nicheKey];
  if (Array.isArray(nicheBenchmarks) && benchName) {
    const b = nicheBenchmarks.find(n => n.Niche === benchName);
    if (b) return { lower: b.LRR_lower99, mean: b.LRR_mean, upper: b.LRR_upper99 };
  }
  return { lower: baseLrr * 0.85, mean: baseLrr, upper: baseLrr * 1.15 };
}

// Build 3-tier projection: cons / likely / best.
// Same multipliers as index.html:1789 — must stay monotonic:
//   cons.lrr ≤ likely.lrr ≤ best.lrr  (revenue-positive)
//   cons.rtc ≤ likely.rtc ≤ best.rtc
//   cons.ctp ≤ likely.ctp ≤ best.ctp
//
// `lrrUserOverride=true` anchors the Likely tier to the user-typed `effNd.lrr`
// instead of letting the niche bench mean override it. Mirror of HTML behavior
// (public/sql-calculator/index.html:1789). Default false (back-compat).
export function buildTieredNd(effNd, nicheKey, nicheBenchmarks, lrrUserOverride) {
  const baseLrr = effNd.lrr;
  let consLrr, likelyLrr, bestLrr;
  if (lrrUserOverride) {
    likelyLrr = baseLrr;
    consLrr   = baseLrr * 0.85;
    bestLrr   = baseLrr * 1.18;
  } else {
    const bounds = getNicheLrrBounds(nicheKey, baseLrr, nicheBenchmarks);
    const consLrrRaw = Math.max(bounds.lower, baseLrr * 0.80);
    likelyLrr        = Math.max(bounds.mean,  consLrrRaw * 1.10);
    const bestLrrRaw = Math.max(bounds.upper, likelyLrr * 1.10);
    consLrr = Math.min(consLrrRaw, likelyLrr * 0.85);
    bestLrr = Math.max(bestLrrRaw, likelyLrr * 1.18);
  }
  return {
    cons:   { ...effNd, lrr: consLrr,   rtc: effNd.rtc * 0.85, ctp: effNd.ctp * 0.90 },
    likely: { ...effNd, lrr: likelyLrr, rtc: effNd.rtc,        ctp: effNd.ctp        },
    best:   { ...effNd, lrr: bestLrr,   rtc: effNd.rtc * 1.15, ctp: effNd.ctp * 1.10 }
  };
}

// CURE-P0-4: post-funnel monotonicity guard. Apply on the trio of calcFunnel
// outputs (cons/likely/best) before downstream rounding. Mirror of the inline
// guard in public/sql-calculator/index.html (recalc after the three calcFunnel
// calls). Mutates the funnel objects in place — caller passes the same objects
// that will be consumed for display.
//
// Why this exists: at low bid volume × low reply rate, the calcFunnel raw
// floats can land close enough that Math.round() collapses Cons/Likely/Best
// into identical integers. The PDF renderer has a downstream revenue-level
// guard but the calculator UI itself doesn't, producing a confusing "AE
// walked you here at the middle but middle == floor" moment on screen.
export function enforceTierMonotonicity(fCons, fLikely, fBest) {
  ['repliesMonth', 'sqlsMonth', 'clientsMonth'].forEach((k) => {
    if (fLikely[k] <= fCons[k])   fLikely[k] = fCons[k]   * 1.05;
    if (fBest[k]   <= fLikely[k]) fBest[k]   = fLikely[k] * 1.05;
  });
}
