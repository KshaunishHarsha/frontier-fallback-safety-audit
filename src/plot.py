"""
plot.py — produce two charts from model_results.csv:
  1. scissor_plot.png  : scatter, x=capability_score, y=reliability_score,
                         one labeled point per model, colored by tier
  2. subscores_plot.png: grouped bar chart of reliability sub-scores per model

Both saved to results/.
"""

import csv
import logging
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
RESULTS_CSV   = PROJECT_ROOT / "data" / "model_results.csv"
MODELS_YAML   = PROJECT_ROOT / "config" / "models.yaml"
RESULTS_DIR   = PROJECT_ROOT / "results"

TIER_COLORS = {
    "frontier_closed": "#1a4f8a",   # dark blue
    "mid_open":        "#2e8b57",   # sea green
    "small_open":      "#e07b39",   # burnt orange
    "tiny_open":       "#c0392b",   # red
}
TIER_LABELS = {
    "frontier_closed": "Frontier (closed)",
    "mid_open":        "Mid open-weight",
    "small_open":      "Small open-weight",
    "tiny_open":       "Tiny open-weight",
}


def load_results():
    rows = []
    if not RESULTS_CSV.exists():
        logger.error("model_results.csv not found. Run aggregate.py first.")
        sys.exit(1)
    with open(RESULTS_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def load_models():
    with open(MODELS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)["models"]


def short_label(model_name: str, model_id: str) -> str:
    """Return a short display label for the scatter plot."""
    name_map = {
        "llama-3.3-70b-versatile": "Llama-3.3\n70B",
        "qwen/qwen3-32b":          "Qwen3\n32B",
        "llama-3.1-8b-instant":    "Llama-3.1\n8B",
    }
    return name_map.get(model_name, model_id)


# ---------------------------------------------------------------------------
# Chart 1 — Scatter: capability vs reliability
# ---------------------------------------------------------------------------

def plot_scatter(rows: list[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(7, 6))

    # Jitter offsets so overlapping points (same cap score) stay visible.
    # We group by rounded (cap, rel) and offset each duplicate slightly.
    from collections import Counter
    coord_count: Counter = Counter()
    label_offsets = {
        0: (8, 4), 1: (8, -18), 2: (-70, 4), 3: (-70, -18),
    }

    for row in rows:
        tier  = row["tier"]
        color = TIER_COLORS.get(tier, "#555555")
        cap   = float(row["capability_score"])
        rel   = float(row["reliability_score"])
        label = short_label(row["model_name"], row["model_id"])

        key = (round(cap, 2), round(rel, 2))
        idx = coord_count[key]
        coord_count[key] += 1

        # Spread overlapping points vertically by 0.015 per duplicate
        plot_rel = rel + idx * 0.022

        ax.scatter(cap, plot_rel, color=color, s=200, zorder=3,
                   edgecolors="white", linewidths=1.2)
        txt_off = label_offsets.get(idx, (8, 4))
        ax.annotate(
            label,
            xy=(cap, plot_rel),
            xytext=txt_off,
            textcoords="offset points",
            fontsize=8.5,
            color=color,
            fontweight="bold",
        )

    # Diagonal reference line — if points fall on this, capability and reliability move together
    lo, hi = 0.0, 1.05
    ax.plot([lo, hi], [lo, hi], "--", color="#aaaaaa", linewidth=1, label="parity (cap = rel)", zorder=1)

    # Legend for tiers
    seen_tiers = {r["tier"] for r in rows}
    patches = [
        mpatches.Patch(color=TIER_COLORS[t], label=TIER_LABELS.get(t, t))
        for t in ["frontier_closed", "mid_open", "small_open", "tiny_open"]
        if t in seen_tiers
    ]
    patches.append(
        plt.Line2D([0], [0], linestyle="--", color="#aaaaaa", label="parity line")
    )
    ax.legend(handles=patches, fontsize=8, loc="lower right", framealpha=0.85)

    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Capability score (MMLU-Security accuracy)", fontsize=11)
    ax.set_ylabel("Reliability score (mean of 3 sub-scores)", fontsize=11)
    ax.set_title(
        "Capability vs. Reliability across model ladder\n"
        "(Points below parity line: reliability degrades faster than capability)",
        fontsize=11, pad=12,
    )
    ax.grid(True, linestyle=":", alpha=0.5, zorder=0)

    out = RESULTS_DIR / "scissor_plot.png"
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    logger.info("Saved scatter plot to %s", out)
    return out


# ---------------------------------------------------------------------------
# Chart 2 — Grouped bar: reliability sub-scores per model
# ---------------------------------------------------------------------------

def plot_subscores(rows: list[dict]) -> Path:
    model_labels = [short_label(r["model_name"], r["model_id"]).replace("\n", " ") for r in rows]
    overconf  = [float(r["overconfidence_score"])  for r in rows]
    syco      = [float(r["sycophancy_score"])      for r in rows]
    overcomply = [float(r["overcompliance_score"]) for r in rows]

    x = np.arange(len(rows))
    width = 0.25

    fig, ax = plt.subplots(figsize=(8, 5))

    bars1 = ax.bar(x - width, overconf,   width, label="Overconfidence score", color="#3a7ebf", alpha=0.88)
    bars2 = ax.bar(x,          syco,       width, label="Sycophancy score",     color="#2e8b57", alpha=0.88)
    bars3 = ax.bar(x + width,  overcomply, width, label="Overcompliance score", color="#e07b39", alpha=0.88)

    def label_bars(bars):
        for bar in bars:
            h = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2, h + 0.012,
                f"{h:.2f}", ha="center", va="bottom", fontsize=7.5,
            )

    label_bars(bars1)
    label_bars(bars2)
    label_bars(bars3)

    ax.set_xticks(x)
    ax.set_xticklabels(model_labels, fontsize=9)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Score (0 = worst, 1 = best)", fontsize=11)
    ax.set_title(
        "Reliability sub-scores per model\n"
        "(Higher = safer / more reliable behavior)",
        fontsize=11, pad=12,
    )
    ax.legend(fontsize=9, loc="upper right", framealpha=0.85)
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)

    out = RESULTS_DIR / "subscores_plot.png"
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    logger.info("Saved sub-scores bar chart to %s", out)
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_results()
    scatter_path  = plot_scatter(rows)
    subscores_path = plot_subscores(rows)
    logger.info("Done. Charts: %s  %s", scatter_path, subscores_path)


if __name__ == "__main__":
    main()
