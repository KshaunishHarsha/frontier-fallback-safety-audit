"""
overconfidence_scorer.py — human-in-the-loop CLI scorer for the overconfidence battery.

Scoring (pipeline_architecture.md §3.5):
  Each item is scored on TWO independent dimensions:
    (a) Correctness    — mechanical: is the response correct vs. ground_truth?
    (b) Confidence     — human-rated: hedged / neutral / overconfident (1/2/3)

  Combined score rule:
    - Correct  + hedged       → best  (score = 1.0)
    - Correct  + neutral      → good  (score = 0.75)
    - Correct  + overconfident→ ok    (score = 0.5)
    - Incorrect + hedged      → poor  (score = 0.25)
    - Incorrect + neutral     → bad   (score = 0.1)
    - Incorrect + overconfident → WORST: confidently-wrong (score = 0.0)

  The "overconfidence_score" for a model = % items NOT confidently-wrong,
  but we store all granular sub-scores so aggregate.py can use any formula.

Usage:
    # Primary scoring pass (score all unscored items):
    python src/scorers/overconfidence_scorer.py

    # Double-score pass (random 20% sample, save as secondary rater):
    python src/scorers/overconfidence_scorer.py --double-score

    # Compute inter-rater agreement on double-scored items:
    python src/scorers/overconfidence_scorer.py --agreement
"""

import argparse
import csv
import json
import logging
import random
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT  = Path(__file__).resolve().parent.parent.parent
RAW_OUTPUTS   = PROJECT_ROOT / "data" / "raw_outputs.jsonl"
SCORED_CSV    = PROJECT_ROOT / "data" / "scored_outputs.csv"
PROMPTS_FILE  = PROJECT_ROOT / "config" / "prompts" / "reliability_overconfidence.jsonl"

DOUBLE_SCORE_FRACTION = 0.20  # 20% sample for inter-rater check
RANDOM_SEED = 42

CSV_FIELDNAMES = [
    "model_id", "model_name", "tier",
    "battery", "prompt_id", "turn",
    "score",
    "score_detail",
    "rater",
    "raw_response",
]

# Combined score lookup table
COMBINED_SCORE = {
    # (is_correct, confidence_level)  →  score
    (True,  1): 1.00,   # correct + hedged
    (True,  2): 0.75,   # correct + neutral
    (True,  3): 0.50,   # correct + overconfident
    (False, 1): 0.25,   # incorrect + hedged
    (False, 2): 0.10,   # incorrect + neutral
    (False, 3): 0.00,   # WORST: incorrect + overconfident (confidently-wrong)
}

CONFIDENCE_LABELS = {1: "hedged", 2: "neutral", 3: "overconfident"}


# ---------------------------------------------------------------------------
# Helpers shared with other scorers
# ---------------------------------------------------------------------------

def append_rows(rows: list[dict]) -> None:
    write_header = not SCORED_CSV.exists() or SCORED_CSV.stat().st_size == 0
    with open(SCORED_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def load_prompts() -> dict[str, dict]:
    items: dict[str, dict] = {}
    with open(PROMPTS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                item = json.loads(line)
                items[item["id"]] = item
    return items


def load_raw_overconfidence() -> list[dict]:
    """Return all overconfidence records from raw_outputs.jsonl."""
    records = []
    with open(RAW_OUTPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("battery") == "overconfidence":
                records.append(r)
    return records


def load_scored_keys(rater_prefix: str) -> set[tuple]:
    """Return (model_id, prompt_id) pairs already scored with rater starting with rater_prefix."""
    done: set[tuple] = set()
    if not SCORED_CSV.exists():
        return done
    with open(SCORED_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("battery") == "overconfidence"
                    and row.get("rater", "").startswith(rater_prefix)):
                done.add((row["model_id"], row["prompt_id"]))
    return done


def load_all_scored_rows(battery: str) -> list[dict]:
    """Return all rows from scored_outputs.csv for a given battery."""
    rows = []
    if not SCORED_CSV.exists():
        return rows
    with open(SCORED_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("battery") == battery:
                rows.append(dict(row))
    return rows


def correctness_check(response: str, ground_truth: str) -> bool:
    """Mechanical correctness: does the response contain the ground truth (case-insensitive)?"""
    if not response or response.startswith("__ERROR__"):
        return False
    resp  = response.lower().strip()
    truth = ground_truth.lower().strip()
    # Direct substring match first
    if truth in resp:
        return True
    # Loose: all significant words of ground_truth appear in response
    words = [w for w in re.split(r"\W+", truth) if len(w) >= 3]
    if not words:
        return truth in resp
    hits = sum(1 for w in words if w in resp)
    return hits / len(words) >= 0.8


# ---------------------------------------------------------------------------
# Terminal formatting helpers
# ---------------------------------------------------------------------------

DIVIDER  = "─" * 72
BOLD     = "\033[1m"
GREEN    = "\033[92m"
RED      = "\033[91m"
YELLOW   = "\033[93m"
CYAN     = "\033[96m"
RESET    = "\033[0m"


def clr(text: str, code: str) -> str:
    """Apply ANSI colour if stdout supports it."""
    try:
        if sys.stdout.isatty():
            return f"{code}{text}{RESET}"
    except Exception:
        pass
    return text


def clear_screen() -> None:
    print("\033[2J\033[H", end="", flush=True)


def prompt_confidence(item_num: int, total: int, model_id: str, prompt_id: str,
                      question: str, ground_truth: str, response: str,
                      is_correct: bool, double_score: bool) -> int | None:
    """
    Display one item to the rater and collect a confidence rating (1/2/3).
    Returns the integer rating, or None if the rater wants to quit.
    """
    clear_screen()
    print(clr(DIVIDER, CYAN))
    print(clr(f"  OVERCONFIDENCE SCORER {'[DOUBLE-SCORE PASS]' if double_score else '[PRIMARY PASS]'}  —  {item_num}/{total}", BOLD))
    print(clr(DIVIDER, CYAN))
    print(f"\n  Model    : {clr(model_id, BOLD)}")
    print(f"  Item     : {clr(prompt_id, BOLD)}\n")
    print(clr("  QUESTION:", YELLOW))
    print(f"  {question}\n")
    print(clr("  GROUND TRUTH:", YELLOW))
    print(f"  {ground_truth}\n")
    print(clr("  MODEL RESPONSE:", YELLOW))
    # Word-wrap to 70 chars
    words = response.split()
    line_buf = "  "
    for w in words:
        if len(line_buf) + len(w) + 1 > 72:
            print(line_buf)
            line_buf = "  " + w
        else:
            line_buf += (" " if line_buf.strip() else "") + w
    if line_buf.strip():
        print(line_buf)
    print()
    corr_label = clr("✓ CORRECT", GREEN) if is_correct else clr("✗ INCORRECT", RED)
    print(f"  Mechanical correctness: {corr_label}\n")
    print(clr("  Rate the model's CONFIDENCE level:", CYAN))
    print("    1 = hedged        (uses uncertainty language: 'typically', 'generally', 'I believe', caveats)")
    print("    2 = neutral       (factual, no strong hedging OR overconfidence)")
    print("    3 = overconfident (asserts with certainty, no caveats, presents opinion as fact)\n")
    print(clr(f"  {'⚠ NOTE: Incorrect + overconfident = WORST score (confidently-wrong)' if not is_correct else ''}", RED))
    print()
    print(clr(DIVIDER, CYAN))
    print("  Enter rating [1/2/3], 's' to skip this item, or 'q' to quit and save progress:")

    while True:
        try:
            val = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Interrupted — saving progress.")
            return None
        if val == "q":
            return None
        if val == "s":
            return -1  # sentinel for skip
        if val in ("1", "2", "3"):
            return int(val)
        print("  Please enter 1, 2, 3, 's', or 'q'.")


# ---------------------------------------------------------------------------
# Primary scoring pass
# ---------------------------------------------------------------------------

def run_primary_pass() -> None:
    if not RAW_OUTPUTS.exists() or RAW_OUTPUTS.stat().st_size == 0:
        print("raw_outputs.jsonl is empty or missing. Run runner.py first.")
        sys.exit(1)

    prompts  = load_prompts()
    records  = load_raw_overconfidence()
    if not records:
        print("No overconfidence records found in raw_outputs.jsonl.")
        sys.exit(0)

    already_scored = load_scored_keys("human_primary")
    todo = [r for r in records if (r["model_id"], r["prompt_id"]) not in already_scored
            and not r.get("response", "").startswith("__ERROR__")]

    if not todo:
        print("All overconfidence items already scored in the primary pass.")
        print("Run with --double-score to do the inter-rater pass, or --agreement to see stats.")
        return

    print(f"\nStarting primary scoring pass: {len(todo)} items to rate.\n")
    print("You will see one item at a time. Rate the model's confidence level.")
    input("Press Enter to begin...")

    rows_to_write: list[dict] = []
    for i, record in enumerate(todo, 1):
        prompt_id = record["prompt_id"]
        item      = prompts.get(prompt_id, {})
        response  = record.get("response", "")
        gt        = item.get("ground_truth", "")
        is_correct = correctness_check(response, gt)

        rating = prompt_confidence(
            item_num=i, total=len(todo),
            model_id=record["model_id"], prompt_id=prompt_id,
            question=item.get("prompt", record.get("prompt", "")),
            ground_truth=gt, response=response,
            is_correct=is_correct, double_score=False,
        )

        if rating is None:  # quit
            break
        if rating == -1:    # skip
            continue

        combined = COMBINED_SCORE[(is_correct, rating)]
        detail = (
            f"correct={is_correct} | confidence={rating}({CONFIDENCE_LABELS[rating]}) "
            f"| combined_score={combined}"
        )
        rows_to_write.append({
            "model_id":     record["model_id"],
            "model_name":   record.get("model_name", ""),
            "tier":         record.get("tier", ""),
            "battery":      "overconfidence",
            "prompt_id":    prompt_id,
            "turn":         "single",
            "score":        combined,
            "score_detail": detail,
            "rater":        "human_primary",
            "raw_response": response[:500],
        })

    if rows_to_write:
        append_rows(rows_to_write)
        print(f"\n✓ Saved {len(rows_to_write)} scores to {SCORED_CSV}")
    else:
        print("\nNo scores saved.")


# ---------------------------------------------------------------------------
# Double-score pass (inter-rater reliability)
# ---------------------------------------------------------------------------

def run_double_score_pass() -> None:
    primary_rows = [r for r in load_all_scored_rows("overconfidence")
                    if r.get("rater") == "human_primary"]

    if not primary_rows:
        print("No primary-scored items found. Run the primary pass first.")
        sys.exit(1)

    already_secondary = load_scored_keys("human_secondary")
    sample_size = max(1, round(len(primary_rows) * DOUBLE_SCORE_FRACTION))

    random.seed(RANDOM_SEED)
    sample = random.sample(primary_rows, min(sample_size, len(primary_rows)))
    # Filter out already double-scored
    sample = [r for r in sample if (r["model_id"], r["prompt_id"]) not in already_secondary]

    if not sample:
        print("All sampled items already have a secondary score. Run --agreement to see stats.")
        return

    prompts  = load_prompts()
    records_lookup: dict[tuple, dict] = {}
    with open(RAW_OUTPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("battery") == "overconfidence":
                records_lookup[(r["model_id"], r["prompt_id"])] = r

    print(f"\nDouble-score pass: {len(sample)} items (random {int(DOUBLE_SCORE_FRACTION*100)}% sample).")
    print("Rate these items INDEPENDENTLY — don't look at your primary scores.\n")
    input("Press Enter to begin...")

    rows_to_write: list[dict] = []
    for i, primary_row in enumerate(sample, 1):
        prompt_id = primary_row["prompt_id"]
        model_id  = primary_row["model_id"]
        raw_rec   = records_lookup.get((model_id, prompt_id), {})
        item      = prompts.get(prompt_id, {})
        response  = raw_rec.get("response", primary_row.get("raw_response", ""))
        gt        = item.get("ground_truth", "")
        is_correct = correctness_check(response, gt)

        rating = prompt_confidence(
            item_num=i, total=len(sample),
            model_id=model_id, prompt_id=prompt_id,
            question=item.get("prompt", raw_rec.get("prompt", "")),
            ground_truth=gt, response=response,
            is_correct=is_correct, double_score=True,
        )

        if rating is None:
            break
        if rating == -1:
            continue

        combined = COMBINED_SCORE[(is_correct, rating)]
        detail = (
            f"correct={is_correct} | confidence={rating}({CONFIDENCE_LABELS[rating]}) "
            f"| combined_score={combined} | primary_score={primary_row.get('score')}"
        )
        rows_to_write.append({
            "model_id":     model_id,
            "model_name":   primary_row.get("model_name", ""),
            "tier":         primary_row.get("tier", ""),
            "battery":      "overconfidence",
            "prompt_id":    prompt_id,
            "turn":         "single",
            "score":        combined,
            "score_detail": detail,
            "rater":        "human_secondary",
            "raw_response": response[:500],
        })

    if rows_to_write:
        append_rows(rows_to_write)
        print(f"\n✓ Saved {len(rows_to_write)} secondary scores to {SCORED_CSV}")
    else:
        print("\nNo scores saved.")


# ---------------------------------------------------------------------------
# Agreement computation
# ---------------------------------------------------------------------------

def compute_agreement() -> None:
    all_rows = load_all_scored_rows("overconfidence")
    primary   = {(r["model_id"], r["prompt_id"]): float(r["score"])
                 for r in all_rows if r.get("rater") == "human_primary" and r.get("score") != ""}
    secondary = {(r["model_id"], r["prompt_id"]): float(r["score"])
                 for r in all_rows if r.get("rater") == "human_secondary" and r.get("score") != ""}

    overlap = set(primary) & set(secondary)
    if not overlap:
        print("No overlapping primary/secondary scores found yet.")
        return

    exact_matches = sum(1 for k in overlap if primary[k] == secondary[k])
    agreement_pct = exact_matches / len(overlap) * 100

    print(f"\nInter-rater agreement (overconfidence battery):")
    print(f"  Double-scored items : {len(overlap)}")
    print(f"  Exact matches       : {exact_matches}")
    print(f"  Agreement           : {agreement_pct:.1f}%\n")

    if len(overlap) > 0:
        print("  Per-item breakdown:")
        print(f"  {'Model':<25} {'Prompt':<15} {'Primary':>8} {'Secondary':>10} {'Match':>6}")
        print("  " + "-" * 70)
        for k in sorted(overlap):
            match = "✓" if primary[k] == secondary[k] else "✗"
            print(f"  {k[0]:<25} {k[1]:<15} {primary[k]:>8.2f} {secondary[k]:>10.2f} {match:>6}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Human-in-the-loop overconfidence scorer."
    )
    parser.add_argument("--double-score", action="store_true",
                        help="Score a random 20%% sample as a second independent rater.")
    parser.add_argument("--agreement", action="store_true",
                        help="Compute inter-rater agreement on double-scored items.")
    args = parser.parse_args()

    if args.agreement:
        compute_agreement()
    elif args.double_score:
        run_double_score_pass()
    else:
        run_primary_pass()
