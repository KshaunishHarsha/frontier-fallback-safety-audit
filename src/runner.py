"""
runner.py — executes the full model × battery × item matrix.

Usage
-----
  # Full run (all models, all batteries):
  python src/runner.py

  # Smoke test (first model, 2 items per battery — run this first!):
  python src/runner.py --smoke-test

  # Resume: already-logged (model, battery, prompt_id) triples are skipped
  # automatically on any run, so re-running after a crash is safe.

Output
------
  data/raw_outputs.jsonl — one JSON line per (model_id, battery, prompt_id, turn).
  Written incrementally — each line is flushed immediately so a crash never
  loses progress already made.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Path setup — make sure src/ is importable when running from project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from model_clients import call_model  # noqa: E402  (after sys.path insert)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONFIG_DIR = PROJECT_ROOT / "config"
MODELS_YAML = CONFIG_DIR / "models.yaml"
PROMPTS_DIR = CONFIG_DIR / "prompts"
RAW_OUTPUTS = PROJECT_ROOT / "data" / "raw_outputs.jsonl"

BATTERY_FILES = {
    "capability":      PROMPTS_DIR / "capability_proxy.jsonl",
    "overconfidence":  PROMPTS_DIR / "reliability_overconfidence.jsonl",
    "sycophancy":      PROMPTS_DIR / "reliability_sycophancy.jsonl",
    "overcompliance":  PROMPTS_DIR / "reliability_overcompliance.jsonl",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_models() -> list[dict]:
    with open(MODELS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["models"]


def load_battery(path: Path) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_already_logged(output_path: Path) -> set[tuple]:
    """
    Return a set of (model_id, battery, prompt_id, turn) tuples already present
    in raw_outputs.jsonl so we can skip them on resume.
    """
    done: set[tuple] = set()
    if not output_path.exists():
        return done
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                done.add((
                    record["model_id"],
                    record["battery"],
                    record["prompt_id"],
                    record.get("turn", "single"),
                ))
            except (json.JSONDecodeError, KeyError):
                pass  # malformed line — ignore
    return done


def append_record(output_path: Path, record: dict) -> None:
    """Write a single record as a JSON line, flushing immediately."""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Battery-specific runners
# ---------------------------------------------------------------------------

def run_standard_battery(
    model: dict,
    battery_name: str,
    items: list[dict],
    already_logged: set[tuple],
    output_path: Path,
    smoke_test: bool,
) -> int:
    """
    Run a single-turn battery (capability, overconfidence, overcompliance).
    Returns the number of new items logged.
    """
    logged = 0
    if smoke_test:
        items = items[:2]

    for item in items:
        prompt_id = item["id"]
        key = (model["id"], battery_name, prompt_id, "single")

        if key in already_logged:
            logger.info("  [skip] %s / %s / %s (already logged)", model["id"], battery_name, prompt_id)
            continue

        prompt = item["prompt"]
        logger.info("  %s / %s / %s …", model["id"], battery_name, prompt_id)

        try:
            response = call_model(model, prompt)
        except Exception as exc:
            logger.error("    FAILED: %s", exc)
            response = f"__ERROR__: {exc}"

        record = {
            "model_id":    model["id"],
            "model_name":  model["model_name"],
            "tier":        model.get("tier", ""),
            "battery":     battery_name,
            "prompt_id":   prompt_id,
            "turn":        "single",
            "prompt":      prompt,
            "response":    response,
        }
        append_record(output_path, record)
        already_logged.add(key)
        logged += 1

    return logged


def run_sycophancy_battery(
    model: dict,
    items: list[dict],
    already_logged: set[tuple],
    output_path: Path,
    smoke_test: bool,
) -> int:
    """
    Run the two-turn sycophancy battery.

    Turn structure:
      turn_1  → model answers the initial question
      turn_2  → model answers the pushback, seeing turn_1 exchange in history

    Returns the number of new turns logged.
    """
    logged = 0
    if smoke_test:
        items = items[:2]

    for item in items:
        prompt_id = item["id"]
        battery = "sycophancy"

        # --- Turn 1 ---
        key_t1 = (model["id"], battery, prompt_id, "turn_1")
        if key_t1 not in already_logged:
            logger.info("  %s / sycophancy / %s / turn_1 …", model["id"], prompt_id)
            try:
                response_t1 = call_model(model, item["turn_1"])
            except Exception as exc:
                logger.error("    FAILED turn_1: %s", exc)
                response_t1 = f"__ERROR__: {exc}"

            record_t1 = {
                "model_id":    model["id"],
                "model_name":  model["model_name"],
                "tier":        model.get("tier", ""),
                "battery":     battery,
                "prompt_id":   prompt_id,
                "turn":        "turn_1",
                "prompt":      item["turn_1"],
                "response":    response_t1,
            }
            append_record(output_path, record_t1)
            already_logged.add(key_t1)
            logged += 1
        else:
            # Load turn_1 response from file so we can build history for turn_2
            response_t1 = _load_turn_response(output_path, model["id"], battery, prompt_id, "turn_1")
            logger.info("  [skip] %s / sycophancy / %s / turn_1", model["id"], prompt_id)

        # --- Turn 2 ---
        key_t2 = (model["id"], battery, prompt_id, "turn_2")
        if key_t2 not in already_logged:
            logger.info("  %s / sycophancy / %s / turn_2 …", model["id"], prompt_id)
            history = [
                {"role": "user",      "content": item["turn_1"]},
                {"role": "assistant", "content": response_t1 or ""},
            ]
            try:
                response_t2 = call_model(model, item["turn_2_pushback"], conversation_history=history)
            except Exception as exc:
                logger.error("    FAILED turn_2: %s", exc)
                response_t2 = f"__ERROR__: {exc}"

            record_t2 = {
                "model_id":    model["id"],
                "model_name":  model["model_name"],
                "tier":        model.get("tier", ""),
                "battery":     battery,
                "prompt_id":   prompt_id,
                "turn":        "turn_2",
                "prompt":      item["turn_2_pushback"],
                "response":    response_t2,
                # preserve ground truth metadata for scorer convenience
                "ground_truth":           item.get("ground_truth", ""),
                "correct_initial_stance": item.get("correct_initial_stance", ""),
            }
            append_record(output_path, record_t2)
            already_logged.add(key_t2)
            logged += 1
        else:
            logger.info("  [skip] %s / sycophancy / %s / turn_2", model["id"], prompt_id)

    return logged


def _load_turn_response(
    output_path: Path,
    model_id: str,
    battery: str,
    prompt_id: str,
    turn: str,
) -> Optional[str]:
    """Scan raw_outputs.jsonl and return the stored response for a specific turn."""
    if not output_path.exists():
        return None
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if (r.get("model_id") == model_id
                        and r.get("battery") == battery
                        and r.get("prompt_id") == prompt_id
                        and r.get("turn") == turn):
                    return r.get("response", "")
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(smoke_test: bool = False) -> None:
    # Load .env if python-dotenv is available (optional convenience)
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        logger.info("Loaded .env")
    except ImportError:
        pass  # dotenv not installed — env vars must already be set

    models = load_models()
    batteries = {name: load_battery(path) for name, path in BATTERY_FILES.items()}

    if smoke_test:
        models = models[:1]  # only first model
        logger.info("=== SMOKE TEST MODE: 1 model, 2 items per battery ===")
    else:
        logger.info("=== FULL RUN: %d models, %d batteries ===", len(models), len(batteries))

    RAW_OUTPUTS.parent.mkdir(parents=True, exist_ok=True)
    already_logged = load_already_logged(RAW_OUTPUTS)
    logger.info("Already logged: %d records (will skip these)", len(already_logged))

    total_logged = 0

    for model in models:
        logger.info("── Model: %s (%s) ──", model["id"], model["model_name"])

        for battery_name, items in batteries.items():
            if battery_name == "sycophancy":
                n = run_sycophancy_battery(
                    model=model,
                    items=items,
                    already_logged=already_logged,
                    output_path=RAW_OUTPUTS,
                    smoke_test=smoke_test,
                )
            else:
                n = run_standard_battery(
                    model=model,
                    battery_name=battery_name,
                    items=items,
                    already_logged=already_logged,
                    output_path=RAW_OUTPUTS,
                    smoke_test=smoke_test,
                )
            total_logged += n
            logger.info("    → %d new records logged for %s / %s", n, model["id"], battery_name)

    logger.info("Done. Total new records written: %d", total_logged)
    logger.info("Output: %s", RAW_OUTPUTS)

    if smoke_test:
        logger.info("")
        logger.info("=== SMOKE TEST COMPLETE ===")
        logger.info("Review %s before running the full matrix.", RAW_OUTPUTS)
        _print_smoke_preview(RAW_OUTPUTS)


def _print_smoke_preview(output_path: Path) -> None:
    """Print a compact preview of all records in raw_outputs.jsonl."""
    if not output_path.exists():
        logger.warning("No output file found at %s", output_path)
        return

    print("\n" + "=" * 72)
    print("SMOKE TEST PREVIEW — raw_outputs.jsonl")
    print("=" * 72)

    with open(output_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                print(
                    f"\n[{i+1}] model={r['model_id']}  battery={r['battery']}  "
                    f"id={r['prompt_id']}  turn={r.get('turn','single')}"
                )
                print(f"  PROMPT  : {r['prompt'][:120]}{'…' if len(r['prompt']) > 120 else ''}")
                print(f"  RESPONSE: {r['response'][:200]}{'…' if len(r['response']) > 200 else ''}")
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"[{i+1}] (malformed line: {exc})")

    print("\n" + "=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the evaluation matrix.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run only 1 model × 2 items per battery to verify the pipeline end-to-end.",
    )
    args = parser.parse_args()
    main(smoke_test=args.smoke_test)
