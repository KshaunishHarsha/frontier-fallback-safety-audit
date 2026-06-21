# CONTEXT.md — Why This Project Exists

Read this alongside BUILD.md and pipeline_architecture.md. Those tell you *what* to build. This explains *why*, who it's for, and what would make the output good or bad as research — which should shape judgment calls during the build, not just task completion.

---

## What this is actually for

This codebase exists to produce evidence for a **hackathon research submission**, not a production tool. The end deliverable is a short paper/report plus a presentation, submitted to the Global South AI Safety Hackathon (Apart Research, Asia track). The code is the *means* of generating one credible empirical result — it does not need to be polished software, but the **data it produces needs to be trustworthy enough to survive a skeptical judge asking "how do I know this isn't just vibes?"**

That tradeoff matters: prioritize correctness, traceability, and reproducibility of the *results* over code elegance, test coverage, or extensibility. A judge will look at a plot and a methodology section, not at the codebase.

---

## The research question this code answers

On June 12, 2026, the US government ordered Anthropic to suspend Claude Fable 5 / Mythos 5 access for foreign nationals, after a safeguard-bypass was found in Fable 5's cybersecurity-capability guardrails. This is a real, live export-control event.

The hackathon project argues that "safety" isn't one thing — it's at least two separable axes:
1. **Catastrophic-misuse risk** — can a model do something dangerous well if misused
2. **Everyday reliability risk** — does a model behave well on ordinary high-stakes tasks (calibration, resistance to sycophancy, appropriate caution)

The export control optimized only for axis 1. The research question is whether forcing countries off a frontier model and onto smaller open-weight fallbacks **improves axis 1 while quietly worsening axis 2** — a "scissor" that the original policy never measured, and whose cost lands disproportionately on regions (e.g. India, Vietnam) that had no say in the original decision.

The codebase exists to produce one clean piece of evidence for or against that scissor: a plot of capability-proxy score vs. reliability score across a ladder of models from frontier-closed down to small-open.

**Why this matters for how you build:** the finding is only as credible as the methodology behind it. A judge who suspects the ground truth was written after seeing model outputs, or that an AI graded another AI's safety behavior, will discount the result entirely. Every design choice in BUILD.md and pipeline_architecture.md that looks like "extra rigor" (pre-registration, human-in-the-loop scoring, double-score agreement checks, mechanical scoring where possible) exists specifically to survive that scrutiny — don't suggest shortcuts that remove these, even if they'd make the code simpler or faster to ship.

---

## Who's reading the output

Judges are AI safety researchers and people from governance/policy backgrounds (the hackathon is run by Apart Research with hubs including Electric Sheep Bengaluru and Secure AI Futures Lab New Delhi). They will be evaluating:
- Is the methodology sound and the result reproducible
- Is the regional/policy framing genuine or forced
- Is the finding novel (not a rehash of "small models are worse," which is obvious and uninteresting)

This means: when generating any writeup text, plot titles, or commentary, don't oversell the result. If the scissor doesn't clearly appear in the data, say so honestly — a well-reported null/ambiguous result with sound methodology beats a forced narrative. The team would rather present "we tested this rigorously and here's what we actually found" than a result that collapses under one good question from a judge.

---

## Hard constraints — do not cross these regardless of what's convenient

- **The capability-proxy battery must use only existing, published, sanitized benchmarks.** Never generate, suggest, or autocomplete novel exploit-style, vulnerability-discovery, or attack content, even as "test data," even if it would make the capability axis more rigorous. If no suitable existing benchmark is on hand, flag this to the user rather than writing one.
- **Ground truth and rubrics for the reliability battery are written by the user (and their collaborator) before any model is called.** Claude Code should never fill in ground_truth, answer_key, or ideal_response_type values on its own initiative, even placeholder ones that might get forgotten and left in. If a file is incomplete, stop and ask, don't auto-populate.
- **No model is used to score or judge another model's output for the reliability battery.** This must stay human-in-the-loop (see Phase 3). If asked to "speed this up" with an LLM-as-judge, push back and explain why, then defer to the user's explicit decision rather than silently implementing it.
- **Raw outputs are always logged before scoring**, with nothing scored "live" during the run — this is what makes re-scoring possible without re-spending API budget, and what makes the pipeline auditable after the fact.

---

## Practical / time constraints

This is being built during a live hackathon (roughly a 48-hour build window). Practical implications:
- Favor simple, working, debuggable code over abstraction. A flat script that works beats a clean framework that's half-built when time runs out.
- Build incrementally and get a smoke test working at small scale (1 model, 2 items per battery) before scaling to the full matrix — API costs and time are both limited.
- If something is ambiguous or under-specified in BUILD.md/pipeline_architecture.md, surface the ambiguity and ask rather than guessing silently and burning time on a wrong assumption.
- The user's collaborator (writing the prompt content) is a **non-technical person**, not a developer — anything that needs to go to them must be plain text/doc format, not code or JSON they'd need to edit directly.

---

## What "done" looks like

A `results/scissor_plot.png` the team can drop straight into their report, a `model_results.csv` that's the clean backing dataset, and a clear answer (yes/no/ambiguous, with the sub-score breakdown) to: **does reliability degrade faster than capability as you move down the model ladder, or do they move together?** Everything else in the build serves getting to that one defensible answer.