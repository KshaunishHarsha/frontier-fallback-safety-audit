# BUILD.md — Two-Axis Safety Evaluation Harness

## Project Summary

Build an evaluation harness that runs a ladder of LLMs (1 frontier-closed model down to several open-weight models) through two independent batteries:
1. **Capability-proxy battery** — published, sanitized benchmark(s) measuring technical/security reasoning ability
2. **Reliability battery** — three sub-batteries (overconfidence, sycophancy, over-compliance) measuring everyday high-stakes behavioral reliability

The pipeline scores both axes per model and outputs a scatter plot (capability score vs. reliability score) to test whether the two axes move together or decouple ("the scissor") as models get smaller/weaker — simulating what happens to a country forced off a frontier model onto open-weight fallbacks.

Full architecture reference: see `pipeline_architecture.md` (provided separately) for the detailed data flow, file schemas, and scoring logic. This file translates that into buildable phases.

---

## Tech Stack

- Python 3.11+
- `pyyaml`, `jsonlines` (or plain `json`), `pandas`, `matplotlib`
- API clients for whichever providers you have keys for (e.g. `anthropic`, `openai`, `together`)
- No web framework needed — this is a CLI/script pipeline, not a deployed app

---

## Repository Structure (target)

```
project/
├── config/
│   ├── models.yaml
│   └── prompts/
│       ├── capability_proxy.jsonl
│       ├── reliability_overconfidence.jsonl
│       ├── reliability_sycophancy.jsonl
│       └── reliability_overcompliance.jsonl
├── src/
│   ├── model_clients.py
│   ├── runner.py
│   ├── scorers/
│   │   ├── capability_scorer.py
│   │   ├── overconfidence_scorer.py
│   │   ├── sycophancy_scorer.py
│   │   └── overcompliance_scorer.py
│   ├── aggregate.py
│   └── plot.py
├── data/
│   ├── raw_outputs.jsonl
│   ├── scored_outputs.csv
│   └── model_results.csv
├── results/
│   └── scissor_plot.png
├── requirements.txt
└── README.md
```

---

## Phases

### Phase 0 — Setup (both, ~30 min, do together first)
- Agree on the final model ladder (which actual providers/models you have API access to) and fill in `config/models.yaml`
- Confirm API keys are available as environment variables for both of you
- Create repo structure and empty files above
- Agree on a shared spreadsheet or doc for prompt-writing (Phase 1) before either of you touches code

**Definition of done:** repo skeleton exists, both have API keys working (test with a single "hello" call to each provider), `models.yaml` filled in.

---

### Phase 1 — Prompt Batteries (PRE-REGISTERED — must be done before Phase 3 scoring)
This is the most important phase for credibility. All ground-truth answers and expected response types must be written down *before* anyone sees a model output.

- **Capability-proxy battery:** source a published, sanitized security/technical-reasoning benchmark subset (10–20 items). Format into `capability_proxy.jsonl` with `id`, `prompt`, `answer_key`.
- **Reliability — overconfidence:** write 15–20 factual high-stakes prompts (medical/financial/admin) with clear correct answers. Format with `id`, `prompt`, `ground_truth`, `domain`.
- **Reliability — sycophancy:** write 15 factual Q&A pairs plus a pushback follow-up turn. Format with `id`, `turn_1`, `turn_2_pushback`, `ground_truth`.
- **Reliability — over-compliance:** write 15 borderline risky-but-common requests with a pre-defined `ideal_response_type` (refuse / caveat-and-comply / uncritical-comply scale).

**Definition of done:** all 4 `.jsonl` files exist, fully populated, committed, and not touched again except for fixing format bugs.

---

### Phase 2 — Model Client + Runner (engineering core)
- `model_clients.py`: a single `call_model(model_config, prompt, conversation_history=None) -> str` function that routes to the right provider SDK
- `runner.py`: loops model × battery × item, calls `call_model`, logs every raw output to `data/raw_outputs.jsonl` immediately (never score live)
- Handle multi-turn for sycophancy battery (turn_1 → response → turn_2_pushback → response)
- Basic rate-limit handling / retry logic

**Definition of done:** running `python src/runner.py` executes the full matrix end-to-end and produces a complete `raw_outputs.jsonl` with no missing rows.

---

### Phase 3 — Scorers
- `capability_scorer.py`: mechanical match against `answer_key`
- `sycophancy_scorer.py`: mechanical — did stance flip from correct → incorrect after pushback
- `overconfidence_scorer.py`: correctness check (mechanical) + confidence-calibration rating (human-in-the-loop scoring script/CLI prompt that shows output, asks rater to pick hedged/neutral/overconfident)
- `overcompliance_scorer.py`: human-in-the-loop scoring script against `ideal_response_type`
- Build a simple scoring CLI/notebook so the human-rated items can be scored by both teammates independently for the 20% double-score check

**Definition of done:** `data/scored_outputs.csv` populated for all models/batteries; 20% of human-rated items have two independent scores logged with agreement % computed.

---

### Phase 4 — Aggregation + Plotting
- `aggregate.py`: collapses `scored_outputs.csv` into `data/model_results.csv` (one row per model: capability_score, overconfidence_score, sycophancy_score, overcompliance_score, reliability_score)
- `plot.py`: scatter plot (capability vs. reliability, colored/labeled by tier) + grouped bar chart of reliability sub-scores per model, saved to `results/`

**Definition of done:** `results/scissor_plot.png` and sub-score chart exist and are readable; `model_results.csv` is the clean final dataset for the report.

---

### Phase 5 — Optional: Adaption Mitigation Extension
- Use Adaptive Data to build a small targeted training set from the worst-performing model's failure cases (sycophancy/overconfidence/overcompliance examples)
- Run AutoScientist to fine-tune that model
- Re-run Phase 3/4 reliability battery on the adapted model, add it to `model_results.csv` as a new row, regenerate the plot to show before/after

**Definition of done:** an additional point on the scatter plot showing the adapted model, plus a short before/after comparison.

---

## Work Split (sequential, by phase — not parallel tracks)

Two teammates split the project by **phase**, not by axis. A natural cut:

- **Teammate 1 (first half):** Phase 0 (setup), Phase 1 (prompt batteries — see `claude_code_prompts.md` and `instructions_for_friend.md`), Phase 2 (model client + runner)
- **Teammate 2 (second half):** Phase 3 (scorers), Phase 4 (aggregation + plotting), Phase 5 (optional Adaption extension)

This works because each phase produces a clean, complete artifact the next phase consumes (prompt files → raw outputs → scored outputs → final results/plot), so the handoff points are unambiguous. Adjust the exact cut based on time available, but keep handoffs at phase boundaries, not mid-phase.

### Handoff checklist
- End of Phase 1 → Phase 2: all 4 `.jsonl` files exist, are reviewed, and are locked (no further edits — this is the pre-registration point)
- End of Phase 2 → Phase 3: `raw_outputs.jsonl` is complete with no missing model/battery/item rows
- End of Phase 3 → Phase 4: `scored_outputs.csv` is complete, 20% double-score agreement check has been run and logged
- End of Phase 4: review the plot together before writing it up; re-run any model that looks like an outlier/error before trusting the result

---

## Notes for Claude Code

- Build Phase 1 content (the `.jsonl` prompt files) as plain data files — do not generate placeholder/dummy prompts, ask the user for the actual prompt content or help them draft it explicitly, since these are the pre-registered ground truth for the whole study and must be deliberate, not auto-generated filler.
- Keep `model_clients.py` provider-agnostic — all provider-specific logic isolated there, nowhere else.
- Never have the scoring scripts call a model to "judge" its own or another model's output for the human-rated batteries unless explicitly instructed — these are designed as human-in-the-loop for credibility (see pipeline_architecture.md, section 4).
- Log raw outputs before any scoring step, always — scoring must be re-runnable without re-calling APIs.
- Keep the capability-proxy battery sourced from existing published benchmarks only — do not generate novel security/exploit-style content.