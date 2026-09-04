"""
Benchmark Comparison
=====================
Prints fixed_retry vs oracle side-by-side.
RecoverAI results will be inserted here once the orchestrator is built.

Usage:
    python -m app.baseline.compare --dataset data/synthetic/eval_seed42_n100.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.baseline.fixed_retry import run_baseline
from app.baseline.oracle import run_oracle


def compare(dataset_path: str, recoverai_report_path: str | None = None) -> None:
    baseline = run_baseline(dataset_path)
    oracle   = run_oracle(dataset_path)

    # Optional: load a RecoverAI report if it exists
    recoverai = None
    if recoverai_report_path:
        with open(recoverai_report_path) as f:
            recoverai = json.load(f)

    sep = "═" * 72
    print(f"\n{sep}")
    print(f"  RECOVERAI  BENCHMARK COMPARISON")
    print(f"  Dataset: {dataset_path}")
    print(sep)

    def fmt(paise: float) -> str:
        return f"₹{int(paise)//100:>10,}"

    def fmtr(rate: float) -> str:
        return f"{rate*100:>8.1f}%"

    def fmtlift(pct: float) -> str:
        return f"{pct:>+8.1f}%"

    col_w = 18
    headers = ["Metric", "Natural (no-op)", "Fixed Retry", "Oracle (ceiling)"]
    if recoverai:
        headers.append("RecoverAI")

    header_fmt = f"  {'Metric':<30}" + "".join(f"{h:>{col_w}}" for h in headers[1:])
    print(header_fmt)
    print("  " + "─" * (30 + col_w * (len(headers) - 1)))

    nat_net   = baseline.natural_recovery_paise
    nat_gross = baseline.natural_recovery_paise  # same — no cost

    rows = [
        ("Recovery rate",
            fmtr(baseline.recovered_count / baseline.total_cases),   # approx natural
            fmtr(baseline.recovery_rate),
            fmtr(oracle.recovery_rate),
        ),
        ("Gross recovered",
            fmt(nat_gross),
            fmt(baseline.gross_recovery_paise),
            fmt(oracle.gross_recovery_paise),
        ),
        ("Intervention cost",
            "        ₹0",
            fmt(baseline.total_cost_paise),
            fmt(oracle.total_cost_paise),
        ),
        ("Net recovered",
            fmt(nat_net),
            fmt(baseline.net_recovery_paise),
            fmt(oracle.net_recovery_paise),
        ),
        ("Incremental net vs natural",
            "        ₹0",
            fmt(baseline.incremental_net_paise),
            fmt(oracle.incremental_net_paise),
        ),
        ("Incremental lift %",
            "      +0.0%",
            fmtlift(baseline.incremental_lift_pct),
            fmtlift(oracle.incremental_lift_pct),
        ),
    ]

    for row in rows:
        line = f"  {row[0]:<30}" + "".join(f"{v:>{col_w}}" for v in row[1:])
        if recoverai:
            # placeholder until RecoverAI report is available
            val = recoverai.get(row[0].lower().replace(" ", "_"), "—")
            line += f"{'—':>{col_w}}"
        print(line)

    print(f"\n  Total at risk: {fmt(baseline.total_at_risk_paise)}")
    print()

    # RecoverAI target zone
    incr_gap = oracle.incremental_net_paise - baseline.incremental_net_paise
    print(f"  Gap for RecoverAI to close: {fmt(incr_gap)}  incremental net vs fixed-retry")
    print(f"  Matching oracle would mean: {fmtlift(oracle.incremental_lift_pct)} lift")
    print(f"\n  👉 RecoverAI target: beat fixed_retry incremental net of "
          f"{fmt(baseline.incremental_net_paise)}")
    print(sep + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",   required=True)
    parser.add_argument("--recoverai", default=None, help="Path to RecoverAI report JSON")
    args = parser.parse_args()
    compare(args.dataset, args.recoverai)
