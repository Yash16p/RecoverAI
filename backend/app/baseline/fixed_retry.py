"""
Fixed-Retry Baseline
=====================
The dumbest possible recovery strategy:
  - Every failed payment → retry once after a fixed delay
  - No diagnosis, no scoring, no policy reasoning
  - No economic calculation
  - Just: failed? retry. always.

This is the baseline RecoverAI must beat.

Why this specific baseline?
  Most real merchant integrations do exactly this — one automatic retry
  after N minutes, regardless of failure reason, amount, or customer history.
  If RecoverAI cannot beat this, the AI adds no value.

Metrics produced:
  - baseline_recovery_rate        : % of cases where retry succeeded
  - baseline_gross_recovery_paise : total amount recovered (paise)
  - baseline_net_recovery_paise   : gross minus intervention costs
  - baseline_cost_paise           : total cost of all retries attempted
  - natural_recovery_paise        : what would have recovered with NO action
  - incremental_net_paise         : baseline_net - natural_recovery
  - per_case breakdown            : for audit trail
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Fixed retry cost (same as RETRY_PAYMENT in the action cost table)
RETRY_COST_PAISE = 200   # ₹2 per retry attempt

# Fixed retry delay (not simulated here — just a label for the strategy)
RETRY_DELAY_MINUTES = 30


@dataclass
class CaseBaselineResult:
    case_id: str
    amount_paise: int
    failure_code: str
    customer_segment: str
    is_subscription: bool

    # What the baseline does
    action_taken: str                  # always "RETRY_PAYMENT"
    retry_cost_paise: int              # always RETRY_COST_PAISE

    # Ground-truth outcomes
    p_natural: float                   # P(recovery | no action)
    p_retry: float                     # P(recovery | retry)
    actual_recovered: bool             # from counterfactual draw

    # Financials
    gross_recovery_paise: int          # amount_paise if recovered, else 0
    net_recovery_paise: int            # gross - cost
    natural_recovery_value: float      # p_natural * amount_paise (expected)
    incremental_net_paise: float       # net_recovery - natural_recovery_value


@dataclass
class BaselineReport:
    strategy: str
    dataset_path: str
    total_cases: int
    retry_delay_minutes: int
    retry_cost_paise: int

    # Recovery counts
    recovered_count: int
    not_recovered_count: int
    recovery_rate: float               # recovered / total

    # Financials (paise)
    total_at_risk_paise: int
    gross_recovery_paise: int
    total_cost_paise: int
    net_recovery_paise: int
    natural_recovery_paise: float      # expected value of doing nothing
    incremental_net_paise: float       # net_recovery - natural_recovery

    # Rates
    incremental_lift_pct: float        # incremental_net / natural_recovery * 100

    # Per-case results (for audit)
    cases: list[CaseBaselineResult]

    def print_summary(self) -> None:
        sep = "─" * 58
        print(f"\n{sep}")
        print(f"  FIXED-RETRY BASELINE  ({self.strategy})")
        print(f"  Dataset : {self.dataset_path}")
        print(sep)
        print(f"  Cases evaluated        : {self.total_cases}")
        print(f"  Retry delay            : {self.retry_delay_minutes} min")
        print(f"  Retry cost per attempt : ₹{self.retry_cost_paise/100:.2f}")
        print()
        print(f"  Recovery rate          : {self.recovery_rate*100:.1f}%  "
              f"({self.recovered_count}/{self.total_cases})")
        print()
        print(f"  Total at risk          : ₹{self.total_at_risk_paise//100:>10,}")
        print(f"  Gross recovered        : ₹{self.gross_recovery_paise//100:>10,}")
        print(f"  Intervention cost      : ₹{self.total_cost_paise//100:>10,}")
        print(f"  Net recovered          : ₹{self.net_recovery_paise//100:>10,}")
        print()
        print(f"  Natural recovery (est.): ₹{int(self.natural_recovery_paise)//100:>10,}  "
              f"← expected value of doing NOTHING")
        print(f"  Incremental net        : ₹{int(self.incremental_net_paise)//100:>10,}  "
              f"← value the baseline ADDS over doing nothing")
        print(f"  Incremental lift       : {self.incremental_lift_pct:>+.1f}%")
        print()

        # Breakdown by failure category
        from collections import defaultdict
        by_tier: dict[str, list[CaseBaselineResult]] = defaultdict(list)
        for c in self.cases:
            by_tier[c.failure_code].append(c)

        print(f"  Recovery rate by failure code:")
        for code in sorted(by_tier, key=lambda x: -len(by_tier[x])):
            cases = by_tier[code]
            rec   = sum(1 for c in cases if c.actual_recovered)
            print(f"    {code:<30} {rec:>3}/{len(cases):>3}  "
                  f"({rec/len(cases)*100:.0f}%)")
        print(sep)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":              self.strategy,
            "dataset_path":          self.dataset_path,
            "total_cases":           self.total_cases,
            "retry_delay_minutes":   self.retry_delay_minutes,
            "retry_cost_paise":      self.retry_cost_paise,
            "recovered_count":       self.recovered_count,
            "not_recovered_count":   self.not_recovered_count,
            "recovery_rate":         round(self.recovery_rate, 4),
            "total_at_risk_paise":   self.total_at_risk_paise,
            "gross_recovery_paise":  self.gross_recovery_paise,
            "total_cost_paise":      self.total_cost_paise,
            "net_recovery_paise":    self.net_recovery_paise,
            "natural_recovery_paise": round(self.natural_recovery_paise, 2),
            "incremental_net_paise": round(self.incremental_net_paise, 2),
            "incremental_lift_pct":  round(self.incremental_lift_pct, 2),
        }


def run_baseline(dataset_path: str | Path) -> BaselineReport:
    """
    Run the fixed-retry baseline against a dataset file.

    Parameters
    ----------
    dataset_path : path to a JSONL file produced by synthetic.generator

    Returns
    -------
    BaselineReport with full metrics + per-case breakdown
    """
    path = Path(dataset_path)
    with open(path, encoding="utf-8") as f:
        raw_cases = [json.loads(line) for line in f if line.strip()]

    results: list[CaseBaselineResult] = []

    for raw in raw_cases:
        cf        = raw["counterfactual"]
        amount    = raw["amount_paise"]
        p_natural = cf["p_natural_recovery"]
        p_retry   = cf["p_by_action"].get("RETRY_PAYMENT", p_natural)

        # Baseline always retries — actual outcome uses the ground-truth draw
        # We use actual_recovered from the dataset but re-attribute it to retry
        # (conservative: we assume retry is the action that caused recovery)
        actual_recovered = cf["actual_recovered"]

        gross = amount if actual_recovered else 0
        net   = gross - RETRY_COST_PAISE
        nat_value = p_natural * amount
        incr_net  = net - nat_value

        results.append(CaseBaselineResult(
            case_id              = raw["case_id"],
            amount_paise         = amount,
            failure_code         = raw["failure_code"],
            customer_segment     = raw["customer_segment"],
            is_subscription      = raw["is_subscription"],
            action_taken         = "RETRY_PAYMENT",
            retry_cost_paise     = RETRY_COST_PAISE,
            p_natural            = p_natural,
            p_retry              = p_retry,
            actual_recovered     = actual_recovered,
            gross_recovery_paise = gross,
            net_recovery_paise   = net,
            natural_recovery_value = nat_value,
            incremental_net_paise  = incr_net,
        ))

    # Aggregate
    total          = len(results)
    recovered      = sum(1 for r in results if r.actual_recovered)
    total_at_risk  = sum(r.amount_paise for r in results)
    gross_recovery = sum(r.gross_recovery_paise for r in results)
    total_cost     = total * RETRY_COST_PAISE
    net_recovery   = gross_recovery - total_cost
    natural_value  = sum(r.natural_recovery_value for r in results)
    incr_net       = net_recovery - natural_value
    incr_lift_pct  = (incr_net / natural_value * 100) if natural_value > 0 else 0.0

    return BaselineReport(
        strategy             = f"fixed_retry_{RETRY_DELAY_MINUTES}min",
        dataset_path         = str(path),
        total_cases          = total,
        retry_delay_minutes  = RETRY_DELAY_MINUTES,
        retry_cost_paise     = RETRY_COST_PAISE,
        recovered_count      = recovered,
        not_recovered_count  = total - recovered,
        recovery_rate        = recovered / total,
        total_at_risk_paise  = total_at_risk,
        gross_recovery_paise = gross_recovery,
        total_cost_paise     = total_cost,
        net_recovery_paise   = net_recovery,
        natural_recovery_paise = natural_value,
        incremental_net_paise  = incr_net,
        incremental_lift_pct   = incr_lift_pct,
        cases                = results,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Run fixed-retry baseline")
    parser.add_argument("--dataset", required=True, help="Path to eval JSONL file")
    parser.add_argument("--save",    default=None,  help="Save report JSON to this path")
    args = parser.parse_args()

    report = run_baseline(args.dataset)
    report.print_summary()

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"  Report saved → {out}")
