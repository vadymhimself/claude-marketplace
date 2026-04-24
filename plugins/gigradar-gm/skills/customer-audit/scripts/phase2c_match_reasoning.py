import os
"""
Phase 2C — Match each top-5 competitor win to Ubiquify's best-matching
WIN and best-matching LOSS (from phase4_winloss rows). Generates a JSON
with per-competitor pair-up + algorithmic AI reasoning paragraphs.

Matching (v2 — vector-first): for each (competitor-win, UB-pool-entry) pair
we compute cosine similarity between the two jobs' ES `matcher.embedding`
vectors (1536-dim OpenAI text-embedding-3-small). Tokenized title Jaccard is
kept as a tiebreaker + as a graceful fallback when one side has no embedding
(e.g. old jobs predating the embedding rollout, or jobs not indexed in ES).

Below the similarity threshold MIN_COSINE (default 0.75) we emit
`ub_win_match = None` / `ub_loss_match = None` with an explicit reason, rather
than forcing a bad pairing. The iOS Camera MVP → LLM Analytics Platform class
of mismatch is exactly what this guards against.

Reasoning: observable, data-backed — opener style, CL length delta,
specificity signals (named tech, year-count), template use, and scanner
overlap. Intentionally NOT an LLM call; we want reproducibility.
"""
import json, re, math, os, urllib.parse as urlp
import urllib3
import requests
urllib3.disable_warnings()

IN2B = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2b_peer_knn_v2.json"
IN4  = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase4_winloss.json"
IN1  = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase1_retro_v2.json"
OUT  = "/sessions/dazzling-nifty-fermat/audit_work/v2_ubiquify/phase2c_match_reasoning.json"

# ES (for embedding fetch)
ES_URL = os.environ.get("ES_URL", "https://<es-host>:9243")
ES_AUTH = (os.environ.get("ES_USER", "researcher-prod"), os.environ.get("ES_PASS", ""))

# Vector-match thresholds
MIN_COSINE_TIGHT = 0.78   # first-pass threshold for "confident match"
MIN_COSINE_LOOSE = 0.70   # fallback threshold when no tight match found
MIN_JACCARD_FALLBACK = 0.12  # title-token fallback when one side has no embedding

# Embedding cache across all competitor pairings — fetch each job's embedding once
_emb_cache = {}

def fetch_embedding(ciphertext):
    """Pull `matcher.embedding` from ES metajob by ciphertext. Returns a list of
    1536 floats, or None if the doc is missing or the field is absent. Cached."""
    if not ciphertext:
        return None
    if ciphertext in _emb_cache:
        return _emb_cache[ciphertext]
    try:
        resp = requests.get(
            f"{ES_URL}/metajob/_doc/{urlp.quote(ciphertext, safe='')}",
            auth=ES_AUTH, verify=False, timeout=15,
            params={"_source_includes": "matcher.embedding"},
        )
        if resp.status_code != 200:
            _emb_cache[ciphertext] = None
            return None
        src = (resp.json().get("_source") or {})
        emb = (src.get("matcher") or {}).get("embedding")
        _emb_cache[ciphertext] = emb if (emb and isinstance(emb, list) and len(emb) >= 256) else None
        return _emb_cache[ciphertext]
    except Exception:
        _emb_cache[ciphertext] = None
        return None

def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return None
    return dot / (math.sqrt(na) * math.sqrt(nb))

STOP = set("""a an the and or for of in on at to with from by into over under is are was were been be being this that these those
developer engineer senior junior lead principal expert specialist needed wanted looking hire
ai ml api app apps web mobile cross full stack backend frontend fullstack front end back
project projects system platform solution solutions tool tools job work contract remote freelance
""".split())

def tok(s):
    if not s: return set()
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s.lower())
    return {w for w in s.split() if w and w not in STOP and len(w) >= 2}

def jaccard(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

def opener_style(cl):
    cl = (cl or "").strip()
    first_line = cl.split("\n", 1)[0][:80]
    hook = first_line.rstrip("!,.?").strip()
    if re.match(r"^(hi|hello|hey|greetings)\b", hook.lower()):
        # greeting-only opener
        second = cl.split("\n", 2)[1][:80] if "\n" in cl else ""
        starter = second.strip().split()[:6]
    else:
        starter = hook.split()[:6]
    return " ".join(starter)

def has_question(cl):
    # first 400 chars — does competitor ask a question early?
    return "?" in (cl or "")[:400]

def specificity_signals(cl):
    cl = cl or ""
    sigs = []
    if re.search(r"\b\d+\+?\s*years?\b", cl.lower()): sigs.append("years-of-exp")
    if re.search(r"\b(react|next|vue|angular|node|typescript|python|django|fastapi|postgres|mongo|three\.?js|tailwind|graphql|aws|gcp|azure|kubernetes|docker|llm|openai|claude|gemini|n8n|zapier|make\.com|langchain)\b", cl.lower()):
        sigs.append("named-tech")
    if re.search(r"\b(portfolio|https?://|\.com|\.io|github\.com)\b", cl.lower()): sigs.append("links/portfolio")
    if re.search(r"\bcalendly|book.{0,20}call|schedule.{0,20}call\b", cl.lower()): sigs.append("CTA-call")
    if re.search(r"\bestimat|timeline|weeks?\b|days?\b", cl.lower()): sigs.append("timeline")
    if re.search(r"\$\d", cl) or re.search(r"\bbudget\b", cl.lower()): sigs.append("pricing")
    return sigs

def first_sentence(cl, max_c=200):
    cl = (cl or "").strip()
    m = re.match(r"([^.!?\n]+[.!?\n])", cl)
    s = m.group(1).strip() if m else cl[:max_c]
    return s[:max_c]

def build_ub_pool():
    p4 = json.load(open(IN4))
    pool = []
    for sc in p4["scanners"]:
        for r in sc["rows"]:
            pool.append({
                "source":"phase4",
                "scanner_name": r.get("scanner_name"),
                "outcome": r["outcome"],
                "job_title": r.get("job_title") or r.get("jd_title") or "",
                "jd_excerpt": r.get("jd_excerpt") or "",
                "jd_category": r.get("jd_category") or "",
                "cl": r.get("rendered_cover_letter_excerpt") or "",
                "cl_length": r.get("cl_length"),
                "algorithm_signature": r.get("algorithm_signature"),
                "prompt_version": r.get("prompt_version"),
                "match_pct": r.get("match_percentage"),
                # Richer client info (symmetric with competitor side)
                "client_company": r.get("client_company"),
                "client_industry": r.get("client_industry"),
                "client_size": r.get("client_size"),
                "client_total_spent": r.get("client_total_spent"),
                "client_feedback_score": r.get("client_feedback_score"),
                "client_hire_rate": r.get("client_hire_rate"),
                "bid_amount": (r.get("app_bid") or {}).get("amount") or r.get("amount"),
                "connects_bid": r.get("connects_bid") or r.get("connects_expended"),
                "hire_date": r.get("hire_date"),
                "created": r.get("meta_createdAt"),
                "proposal_id": r.get("proposal_id"),
                "job_ciphertext": r.get("job_ciphertext"),
                # Frozen-at-bid template (from opp.application.originalStrategy — never
                # the current scanner state; see phase4 notes).
                "cl_template_frozen": r.get("cl_template_frozen"),
                # Per-proposal freelancer identity + full resolved profile (name,
                # rate, location, title, description) so the workbook can render a
                # card with the same shape as the competitor side.
                "freelancer_name": r.get("freelancer_name"),
                "freelancer_rid": r.get("freelancer_rid"),
                "upwork_freelancer_uid": r.get("upwork_freelancer_uid"),
                "freelancer_profile": r.get("freelancer_profile"),
                "tokens_title": tok(r.get("job_title") or r.get("jd_title") or ""),
                "tokens_excerpt": tok((r.get("jd_excerpt") or "")[:600]),
            })
    return pool

def pick_match(comp_title, comp_jd_excerpt, comp_ciphertext, pool, outcome_filter, exclude_ids=None):
    """Vector-first pairing.

    Primary signal: cosine similarity between the two jobs' ES
    `matcher.embedding` vectors (1536-dim). When either side lacks an
    embedding (old job, not indexed, ES timeout) we fall back to a weighted
    title+excerpt Jaccard so we still emit *some* pairing — but we explicitly
    tag the match mode in the return so the rendering can show low-confidence
    pairs distinctly (or not render them at all).

    Returns: (best_pool_entry, score, match_mode, best_cosine_or_None)
      match_mode ∈ {"cosine_tight", "cosine_loose", "token_fallback",
                    "no_match_above_threshold"}
      Caller can treat "no_match_above_threshold" as None → "no comparable UB bid".
    """
    exclude_ids = exclude_ids or set()
    ct = tok(comp_title)
    cex = tok((comp_jd_excerpt or "")[:600])
    comp_emb = fetch_embedding(comp_ciphertext)

    scored = []  # (cosine_or_None, jaccard_score, entry)
    for p in pool:
        if p["outcome"] != outcome_filter: continue
        if p["proposal_id"] in exclude_ids: continue
        pool_emb = fetch_embedding(p.get("job_ciphertext")) if comp_emb else None
        c = cosine(comp_emb, pool_emb) if (comp_emb and pool_emb) else None
        s_title = jaccard(ct, p["tokens_title"])
        s_excerpt = jaccard(cex, p["tokens_excerpt"]) * 0.6
        j = s_title + s_excerpt
        scored.append((c, j, p))

    if not scored:
        return None, 0.0, "empty_pool", None

    # Prefer cosine matches above the tight threshold; then cosine above loose;
    # then fall back to token jaccard above the floor.
    tight = [(c, j, p) for (c, j, p) in scored if c is not None and c >= MIN_COSINE_TIGHT]
    loose = [(c, j, p) for (c, j, p) in scored if c is not None and MIN_COSINE_LOOSE <= c < MIN_COSINE_TIGHT]
    jaccard_ok = [(c, j, p) for (c, j, p) in scored if (c is None) and j >= MIN_JACCARD_FALLBACK]

    if tight:
        c, j, p = max(tight, key=lambda x: (x[0], x[1]))
        return p, c, "cosine_tight", c
    if loose:
        c, j, p = max(loose, key=lambda x: (x[0], x[1]))
        return p, c, "cosine_loose", c
    if jaccard_ok:
        c, j, p = max(jaccard_ok, key=lambda x: (x[1], x[0] or 0))
        return p, j, "token_fallback", None
    return None, 0.0, "no_match_above_threshold", None

def reason_paragraph(comp_name, win, ub_win, ub_loss):
    """Build a compact, observable-facts paragraph about the pairing."""
    parts = []

    c_cl = win.get("full_rendered_cover_letter") or ""
    u_w_cl = (ub_win or {}).get("cl") or ""
    u_l_cl = (ub_loss or {}).get("cl") or ""

    c_len = win.get("cl_length") or len(c_cl)
    u_w_len = (ub_win or {}).get("cl_length") or len(u_w_cl)
    u_l_len = (ub_loss or {}).get("cl_length") or len(u_l_cl)

    c_open = opener_style(c_cl)
    u_w_open = opener_style(u_w_cl) if ub_win else ""
    u_l_open = opener_style(u_l_cl) if ub_loss else ""

    c_sigs = specificity_signals(c_cl)
    u_w_sigs = specificity_signals(u_w_cl) if ub_win else []
    u_l_sigs = specificity_signals(u_l_cl) if ub_loss else []

    gb = win.get("gigradar_bid") or {}
    is_gr = gb.get("is_gigradar_bid")
    algo = gb.get("algorithm_name") or "—"
    scanner_c = gb.get("scanner_name") or ""

    # LENGTH
    if ub_win and u_w_len:
        diff = c_len - u_w_len
        if abs(diff) < 200:
            parts.append(f"Length parity ({c_len} vs {u_w_len} ch) — CL length isn't the differentiator.")
        else:
            longer = "longer" if diff > 0 else "shorter"
            parts.append(f"{comp_name}'s winning CL is {abs(diff)} ch {longer} than Ubiquify's best match ({c_len} vs {u_w_len}).")

    # OPENER
    if c_open and u_w_open:
        parts.append(f"Opener — them: '{c_open}…' vs Ubiquify win: '{u_w_open}…'.")

    # SIGNAL DELTA
    only_c = [s for s in c_sigs if s not in (u_w_sigs + u_l_sigs)]
    only_u = [s for s in (u_w_sigs + u_l_sigs) if s not in c_sigs]
    if only_c:
        parts.append(f"They include {', '.join(only_c)} — Ubiquify's nearest analogues did not.")
    if only_u and ub_win:
        parts.append(f"Ubiquify's win leaned on {', '.join(set(only_u))} — they did not.")

    # Q-OPENER (CTA-hook style)
    if has_question(c_cl) and not has_question(u_w_cl):
        parts.append("Their opening asks a qualifying question (engagement hook); Ubiquify leads with credentials.")

    # TEMPLATE / SCANNER
    if is_gr:
        parts.append(f"They generated this via GigRadar scanner '{scanner_c}' ({algo}) — same platform, so the delta is prompt/template, not manual craft.")

    # LOSS CONTRAST
    if ub_loss and u_l_len:
        parts.append(f"Ubiquify LOSS analogue ({u_l_len} ch): opener '{u_l_open}…' — see right pane for full text.")

    return " ".join(parts).strip()


def main():
    p2b = json.load(open(IN2B))
    ub_pool = build_ub_pool()

    out = {
        "ub_pool_size": len(ub_pool),
        "ub_wins": sum(1 for p in ub_pool if p["outcome"] == "WIN"),
        "ub_losses": sum(1 for p in ub_pool if p["outcome"] == "LOSS"),
        "competitors": [],
    }

    for comp in p2b.get("competitors_top10", []):
        comp_name = comp.get("team_name") or comp.get("team_id", "")[:12]
        used_win_ids, used_loss_ids = set(), set()
        pair_records = []

        UB_POOL_FIELDS = [
            "scanner_name","outcome","job_title","jd_excerpt","jd_category","cl","cl_length",
            "algorithm_signature","prompt_version","match_pct","client_company",
            "client_industry","client_size","client_total_spent","client_feedback_score","client_hire_rate",
            "bid_amount","connects_bid","hire_date",
            "created","proposal_id","job_ciphertext",
            "cl_template_frozen","freelancer_name","freelancer_rid","upwork_freelancer_uid",
            "freelancer_profile",
        ]

        for w in (comp.get("winning_proposals_top5") or []):
            job = w.get("job") or {}
            title = job.get("title") or ""
            excerpt = job.get("description_excerpt") or ""
            comp_ct = job.get("ciphertext")

            ub_win, ws, w_mode, w_cos = pick_match(title, excerpt, comp_ct, ub_pool, "WIN", used_win_ids)
            ub_loss, ls, l_mode, l_cos = pick_match(title, excerpt, comp_ct, ub_pool, "LOSS", used_loss_ids)
            # Strict: drop token_fallback matches below the cosine thresholds for this
            # sheet — better to show "no comparable UB bid" than a random one.
            if w_mode == "no_match_above_threshold":
                ub_win = None
            if l_mode == "no_match_above_threshold":
                ub_loss = None
            if ub_win: used_win_ids.add(ub_win["proposal_id"])
            if ub_loss: used_loss_ids.add(ub_loss["proposal_id"])

            reasoning = reason_paragraph(comp_name, w, ub_win, ub_loss)
            pair_records.append({
                "competitor_win": {
                    "proposal_id": w.get("proposal_id"),
                    "selection_tier": w.get("selection_tier"),
                    "is_invite": w.get("is_invite"),
                    "cl_length": w.get("cl_length"),
                    "job_title": title,
                    "jd_excerpt": excerpt,
                    "jd_category": job.get("category"),
                    "jd_budget": job.get("budget"),
                    "job_ciphertext": comp_ct,
                    "job_url": f"https://www.upwork.com/jobs/{comp_ct}" if comp_ct else None,
                    # Full client card — mirrors UB side.
                    "client_company": job.get("client_company"),
                    "client_country": job.get("client_country"),
                    "client_total_spent": job.get("client_total_spent"),
                    "client_feedback_score": job.get("client_feedback_score"),
                    "client_hire_rate": job.get("client_hire_rate"),
                    "client_payment_verified": job.get("client_payment_verified"),
                    "bid_amount": w.get("terms_charge_amount"),
                    "connects_bid": w.get("terms_connectsBid"),
                    "full_cl": w.get("full_rendered_cover_letter") or "",
                    "gigradar_bid": w.get("gigradar_bid") or {},
                    # Per-proposal freelancer (full profile card) flowed through
                    # from phase2b — same shape on both sides for symmetric render.
                    "freelancer": w.get("freelancer"),
                },
                "ub_win_match": (ub_win and {
                    **{k: ub_win[k] for k in UB_POOL_FIELDS},
                    "match_mode": w_mode,
                    "match_cosine": round(w_cos, 3) if w_cos is not None else None,
                    "match_jaccard": round(ws, 3) if w_cos is None else None,
                }) or None,
                "ub_loss_match": (ub_loss and {
                    **{k: ub_loss[k] for k in UB_POOL_FIELDS},
                    "match_mode": l_mode,
                    "match_cosine": round(l_cos, 3) if l_cos is not None else None,
                    "match_jaccard": round(ls, 3) if l_cos is None else None,
                }) or None,
                "match_diagnostics": {
                    "competitor_job_ciphertext": comp_ct,
                    "win_mode": w_mode, "win_cosine": round(w_cos, 3) if w_cos is not None else None,
                    "loss_mode": l_mode, "loss_cosine": round(l_cos, 3) if l_cos is not None else None,
                },
                "reasoning": reasoning,
            })

        # Per-competitor summary reasoning — aggregate across pairs
        total_c_len = sum((p["competitor_win"]["cl_length"] or 0) for p in pair_records)
        total_uw_len = sum(((p["ub_win_match"] or {}).get("cl_length") or 0) for p in pair_records if p["ub_win_match"])
        avg_c = total_c_len // max(1, len(pair_records))
        uw_count = sum(1 for p in pair_records if p["ub_win_match"])
        avg_uw = (total_uw_len // max(1, uw_count)) if uw_count else 0
        all_gr = all((p["competitor_win"]["gigradar_bid"] or {}).get("is_gigradar_bid") for p in pair_records)
        gr_scanners = sorted({(p["competitor_win"]["gigradar_bid"] or {}).get("scanner_name") for p in pair_records if (p["competitor_win"]["gigradar_bid"] or {}).get("is_gigradar_bid")})
        gr_scanners = [s for s in gr_scanners if s]
        algos = sorted({(p["competitor_win"]["gigradar_bid"] or {}).get("algorithm_name") for p in pair_records if (p["competitor_win"]["gigradar_bid"] or {}).get("is_gigradar_bid")})
        algos = [a for a in algos if a]

        summary_bits = []
        summary_bits.append(f"{comp_name} won {len(pair_records)} jobs in Ubiquify's neighborhoods.")
        if all_gr and algos:
            summary_bits.append(f"All {len(pair_records)} wins are GigRadar bids — {', '.join(algos)} · scanners: {', '.join(gr_scanners) or '—'}.")
        summary_bits.append(f"Avg CL length — them: {avg_c} ch vs Ubiquify matched-wins: {avg_uw} ch.")

        # dominant signals across their wins
        all_sigs = []
        for p in pair_records:
            all_sigs += specificity_signals(p["competitor_win"]["full_cl"])
        sig_counts = {}
        for s in all_sigs: sig_counts[s] = sig_counts.get(s, 0) + 1
        dom = [s for s, c in sig_counts.items() if c >= max(2, len(pair_records) // 2)]
        if dom:
            summary_bits.append(f"Consistent signals in their wins: {', '.join(dom)}.")
        summary_bits.append("See per-win reasoning for opener-level deltas.")

        out["competitors"].append({
            "team_id": comp.get("team_id"),
            "team_name": comp_name,
            "wins_in_neighborhoods": comp.get("wins_in_neighborhoods"),
            # Focus-window performance + full profile data flow through so
            # build_workbook can render the profile block + rank tactics by
            # reply rate without re-reading phase2b.
            "focus_metrics": comp.get("focus_metrics"),
            "agency": comp.get("agency"),
            "freelancer": comp.get("freelancer"),
            "pairs": pair_records,
            "summary_reasoning": " ".join(summary_bits),
        })

    # Subject-team profile snapshot flows through too (for UB side of profile block)
    out["subject_profile"] = p2b.get("subject_profile")

    with open(OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Wrote {OUT}")
    print(f"competitors: {len(out['competitors'])}")
    total_pairs = sum(len(c['pairs']) for c in out['competitors'])
    print(f"pairs: {total_pairs}")


if __name__ == "__main__":
    main()
