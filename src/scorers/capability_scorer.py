"""
capability_scorer.py — mechanical scorer for the capability_proxy battery.

Scoring logic (pipeline_architecture.md §3.5):
  - Strip the model's response, extract the first uppercase letter A-D found.
  - Compare against answer_key (case-insensitive exact match).
  - Score: 1 = correct, 0 = incorrect.

Usage (standalone):
    python src/scorers/capability_scorer.py

Writes results into data/scored_outputs.csv (appends; skips already-scored rows).
"""

import csv
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_OUTPUTS  = PROJECT_ROOT / "data" / "raw_outputs.jsonl"
SCORED_CSV   = PROJECT_ROOT / "data" / "scored_outputs.csv"
PROMPTS_FILE = PROJECT_ROOT / "config" / "prompts" / "capability_proxy.jsonl"

CSV_FIELDNAMES = [
    "model_id", "model_name", "tier",
    "battery", "prompt_id", "turn",
    "score",                      # primary numeric score (0 or 1)
    "score_detail",               # human-readable note
    "rater",                      # "mechanical" | "human_primary" | "human_secondary"
    "raw_response",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_answer_keys() -> dict[str, str]:
    """Return {prompt_id: answer_key} from capability_proxy.jsonl."""
    keys: dict[str, str] = {}
    with open(PROMPTS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                item = json.loads(line)
                keys[item["id"]] = item["answer_key"].strip().upper()
    return keys


def extract_letter(response: str) -> str:
    """
    Pull the first A/B/C/D letter out of the model's response.
    Prompts already ask for 'only the correct uppercase letter', but models
    sometimes add punctuation or a full sentence — this is robust to that.
    """
    response = response.strip()
    # Quick path: single letter response
    if response.upper() in ("A", "B", "C", "D"):
        return response.upper()
    # Look for a lone letter at the start, or after common patterns like "Answer: B"
    match = re.search(r"\b([A-D])\b", response, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""  # couldn't extract


def load_already_scored(battery: str) -> set[tuple]:
    """Return set of (model_id, prompt_id) already in scored_outputs.csv."""
    done: set[tuple] = set()
    if not SCORED_CSV.exists():
        return done
    with open(SCORED_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("battery") == battery:
                done.add((row["model_id"], row["prompt_id"]))
    return done


def append_rows(rows: list[dict]) -> None:
    write_header = not SCORED_CSV.exists() or SCORED_CSV.stat().st_size == 0
    with open(SCORED_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_capability() -> None:
    if not RAW_OUTPUTS.exists() or RAW_OUTPUTS.stat().st_size == 0:
        logger.error("raw_outputs.jsonl is empty or missing. Run runner.py first.")
        sys.exit(1)

    answer_keys = load_answer_keys()
    already_scored = load_already_scored("capability")

    rows_to_write: list[dict] = []
    total = 0
    skipped = 0
    correct = 0

    with open(RAW_OUTPUTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)

            if record.get("battery") != "capability":
                continue

            model_id  = record["model_id"]
            prompt_id = record["prompt_id"]
            response  = record.get("response", "")

            if (model_id, prompt_id) in already_scored:
                skipped += 1
                continue

            total += 1
            answer_key = answer_keys.get(prompt_id, "")
            extracted  = extract_letter(response)

            if response.startswith("__ERROR__"):
                score = 0
                detail = f"runner_error — skipped"
            elif not extracted:
                score = 0
                detail = f"no_letter_found | raw='{response[:60]}'"
            elif extracted == answer_key:
                score = 1
                detail = f"correct | extracted={extracted} | key={answer_key}"
                correct += 1
            else:
                score = 0
                detail = f"incorrect | extracted={extracted} | key={answer_key}"

            rows_to_write.append({
                "model_id":     model_id,
                "model_name":   record.get("model_name", ""),
                "tier":         record.get("tier", ""),
                "battery":      "capability",
                "prompt_id":    prompt_id,
                "turn":         record.get("turn", "single"),
                "score":        score,
                "score_detail": detail,
                "rater":        "mechanical",
                "raw_response": response[:500],
            })

    if rows_to_write:
        append_rows(rows_to_write)
        accuracy = correct / total if total else 0
        logger.info(
            "Capability scored: %d items, %d correct (%.1f%%). %d skipped (already scored).",
            total, correct, accuracy * 100, skipped,
        )
    else:
        logger.info("Nothing new to score (all %d items already in scored_outputs.csv).", skipped)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    score_capability()
    print(f"\nResults written to: {SCORED_CSV}")
