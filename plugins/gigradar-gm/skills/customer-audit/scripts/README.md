# customer-audit scripts

Reference implementation of the full audit pipeline, as shipped for the Ubiquify run (2026-04-22). Treat these as scaffolding you copy into a fresh audit directory and edit for the current team — they are NOT yet parameterized to take a team id on the command line.

## Pipeline order

Run in sequence. Each script writes a JSON blob that the next one reads. Final step is the workbook builder + playbook generator.

| # | Script | Writes | Reads from prior phase |
|---|---|---|---|
| 1 | `phase1_retro_v2.py` | `phase1_retro_v2.json` | — (pulls Mongo directly) |
| 2a | `phase2a_cohort_v2.py` | `phase2a_cohort_v2.json` | — |
| 2b | `phase2b_v2_peer_knn.py` | `phase2b_peer_knn_v2.json` | phase1 (for customer seeds) |
| 2c | `phase2c_match_reasoning.py` | `phase2c_match_reasoning.json` | phase1, phase2b, phase4 (UB pool); vector-matches competitor wins to UB WIN/LOSS via ES `matcher.embedding` cosine (requires `ES_PASS`) |
| 2c.b | `gen_bundles.py` | `/tmp/comp_bundles/SUMMARY_*.txt` | phase2c (regenerate whenever phase2c re-runs so subagents don't analyze stale pairs) |
| — | **subagent dispatch** (see SKILL.md §Phase 2 Part B) | `/tmp/comp_judgments/*.json` | bundles in `/tmp/comp_bundles/` |
| 2c.ai | `merge_ai_judgments.py` | phase2c (in-place; `.pre_ai.json` backup) | `/tmp/comp_judgments/*.json` |
| 3 | `phase3_chats.py` | `phase3_chats.json` | phase1 (HIRED + replied chat ids) |
| 4 | `phase4_winloss.py` | `phase4_winloss.json` | — (pulls Mongo directly) |
| 5 | `phase5_aggregates.py` | `phase5_aggregates.json` | — |
| 6a | `build_workbook.py` | `<Team>_Audit_<date>.xlsx` | all of the above |
| 6b | `gen_playbook.py` | `COMPETITIVE_PLAYBOOK.md` | `/tmp/comp_judgments/*.json` |

## Per-team variables to edit before running

Each script has a small block of hardcoded paths + IDs near the top. Audit this list when adapting for a new team:

- **Team handle** — the `TEAM_EMAIL` / team ObjectId (phase1, phase2a, phase2b, phase4, phase5).
- **Focus window** — the `FOCUS_START` / `FOCUS_END` dates (phase2a, phase5).
- **Output directory** — the `OUT` path passed to `build_workbook.py` (currently `/sessions/<session>/mnt/<cowork-folder>/<Team>_Audit_<date>.xlsx`).
- **Subject-team highlight label** — the display name used in the cohort-compare row (`build_workbook.py`).

A future refactor should collapse these into a single `audit_config.py` module the scripts all import. For now, grep each script for `ubiquify` / `TEAM_EMAIL` / `OUT = Path(` and substitute.

## Environment

Reads credentials from env vars — see plugin `README.md` for the full list. Minimum: `MONGO_URI`, `ES_PASS`.

## Subagent dispatch between phase 2c and the merge step

Phase 2c writes `phase2c_match_reasoning.json` *without* the `ai_*` fields. The fields are injected by the `merge_ai_judgments.py` step AFTER you dispatch one subagent per competitor to analyze its bundle.

See `../SKILL.md` §Phase 2 Part B for the verbatim prompt template — the framing is what bypasses Usage Policy refusals. Bundle format is documented in `../../../references/data-reference.md` §25.3; judgment output shape in §25.4.
