"""
Oracle Baseline
================
The best possible outcome — picks the action with highest expected
net recovery for every case, ignoring all policy constraints.

Used as the upper bound ceiling.
RecoverAI's target is to land between fixed_retry and oracle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.synthetic.generator import ACTION_COSTS_PAISE


@dataclass
class OracleReport:
    dataset_path: str
    total_cases: int

    recovered_count: int
    recovery_rate: float

    total_at_risk_paise: int
    gross_recovery_paise: int
    total_cost_paise: int
    net_recovery_paise: int
    natural_recovery_paise: float
    incremental_net_paise: float
    incremental_lift_pct: float

    def print_summary(self) -> None:
        sep = "─" * 58
        print(f"\n{sep}")
        print(f"  ORACLE BASELINE  (perfect action, no policy constraints)")
        print(f"  Dataset : {self.dataset_path}")
        print(sep)
        print(f"  Cases              : {self.total_cases}")
        print(f"  Recovery rate      : {self.recovery_rate*100:.1f}%  ({self.recovered_count}/{self.total_cases})")
        print()
        print(f"  Total at risk      : ₹{self.total_at_risk_paise//100:>10,}")
        print(f"  Gross recovered    : ₹{self.gross_recovery_paise//100:>10,}")
        print(f"  Intervention cost  : ₹{self.total_cost_paise//100:>10,}")
        print(f"  Net recovered      : ₹{self.net_recovery_paise//100:>10,}")
        print()
        print(f"  Natural recovery   : ₹{int(self.natural_recovery_paise)//100:>10,}")
        print(f"  Incremental net    : ₹{int(self.incremental_net_paise)//100:>10,}")
        print(f"  Incremental lift   : {self.incremental_lift_pct:>+.1f}%")
        print(sep)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": "oracle",
            "dataset_path": self.dataset_path,
            "total_cases": self.total_cases,
            "recovered_count": self.recovered_count,
            "recovery_rate": round(self.recovery_rate, 4),
            "total_at_risk_paise": self.total_at_risk_paise,
            "gross_recovery_paise": self.gross_recovery_paise,
            "total_cost_paise": self.total_cost_paise,
            "net_recovery_paise": self.net_recovery_paise,
            "natural_recovery_paise": round(self.natural_recovery_paise, 2),
            "incremental_net_paise": round(self.incremental_net_paise, 2),
            "incremental_lift_pct": round(self.incremental_lift_pct, 2),
        }


def run_oracle(dataset_path: str | Path) -> OracleReport:
    path = Path(dataset_path)
    with open(path, encoding="utf-8") as f:
        raw_cases = [json.loads(line) for line in f if line.strip()]

    total          = len(raw_cases)
    recovered      = 0
    total_at_risk  = 0
    gross_recovery = 0
    total_cost     = 0
    natural_value  = 0.0

    for raw in raw_cases:
        cf         = raw["counterfactual"]
        amount     = raw["amount_paise"]
        p_natural  = cf["p_natural_recovery"]
        best_action = cf["best_action"]
        actual_recovered = cf["actual_recovered"]

        cost  = ACTION_COSTS_PAISE.get(best_action, 0)
        if best_action == "OFFER_DISCOUNT":
            cost = int(amount * raw.get("discount_pct_offered", 0.05))

        gross = amount if actual_recovered else 0

        total_at_risk  += amount
        gross_recovery += gross
        total_cost     += cost
        natural_value  += p_natural * amount
        if actual_recovered:
            recovered += 1

    net_recovery  = gross_recovery - total_cost
    incr_net      = net_recovery - natural_value
    incr_lift_pct = (incr_net / natural_value * 100) if natural_value > 0 else 0.0

    return OracleReport(
        dataset_path           = str(path),
        total_cases            = total,
        recovered_count        = recovered,
        recovery_rate          = recovered / total,
        total_at_risk_paise    = total_at_risk,
        gross_recovery_paise   = gross_recovery,
        total_cost_paise       = total_cost,
        net_recovery_paise     = net_recovery,
        natural_recovery_paise = natural_value,
        incremental_net_paise  = incr_net,
        incremental_lift_pct   = incr_lift_pct,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--save", default=None)
    args = parser.parse_args()

    report = run_oracle(args.dataset)
    report.print_summary()

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"  Report saved → {out}")
