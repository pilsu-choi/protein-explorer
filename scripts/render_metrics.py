#!/usr/bin/env python3
"""Render recorded measurements; requires matplotlib in the plotting environment."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/assets/validation.png"))
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    rows = report["prediction"]["results"]
    evaluation = report["search"]["same_uniprot_accession"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "#f7f8f2", "axes.facecolor": "#f7f8f2"})
    figure, axes = plt.subplots(1, 3, figsize=(13, 4.4), constrained_layout=True)
    labels = [str(row["length"]) for row in rows]
    for axis, values, title, ylabel in [
        (axes[0], [row["prediction_seconds"] for row in rows], "Actual GPU predictions", "Prediction time (seconds)"),
        (axes[1], [row["device_peak_used_mib"] / 1024 for row in rows], "Device memory observed", "Peak device usage (GiB)"),
    ]:
        bars = axis.bar(labels, values, color="#257c70", width=.65)
        axis.bar_label(bars, fmt="%.1f", padding=4)
        axis.set(title=title, xlabel="Sequence length (residues)", ylabel=ylabel,
                 ylim=(0, max(values) * 1.23))
    values = [evaluation[key]["recall_at_k"] * 100 for key in
              ("esm2_recall_at_k", "three_mer_jaccard_recall_at_k")]
    bars = axes[2].bar(["ESM-2 8M", "3-mer Jaccard"], values, color=["#257c70", "#a4b950"], width=.55)
    axes[2].bar_label(bars, fmt="%.2f%%", padding=4)
    axes[2].set(title="Same-accession retrieval", ylabel="Mean Recall@10 (%)", ylim=(0, 110))
    figure.suptitle("Protein Explorer | RTX 4070 | measured 2026-10-19", fontsize=15, fontweight="bold")
    figure.supxlabel("One run per sample; memory includes desktop allocations. 256 retrieval queries; self excluded.\nSame-accession evaluation is not held-out family generalization.", fontsize=9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160)
    plt.close(figure)


if __name__ == "__main__":
    main()
