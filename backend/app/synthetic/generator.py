"""
Synthetic Payment Dataset Generator
====================================
Generates realistic failed-payment cases with KNOWN counterfactual outcomes.

Key design decisions:
  - Every case has a hidden P(natural_recovery) — what would happen with NO action
  - Every case has hidden P(recovery | action) for each candidate action
  - These ground-truth probabilities are used for evaluation ONLY
  - The orchestrator never sees them — it must estimate them from features

Versioned via GENERATOR_VERSION so we can reproduce any batch exactly.

Usage:
    python -m app.synthetic.generator --count 500 --seed 42 --split 0.8
    python -m app.synthetic.generator --count 50 --seed 99 --eval-only
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "1.0.0"

# ── Domain constants ──────────────────────────────────────────────────────────

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet", "emi"]

FAILURE_CODES = {
    # code: (label, base_natural_recovery, recoverability_tier)
    "INSUFFICIENT_FUNDS":     ("Insufficient funds",           0.30, "medium"),
    "DO_NOT_HONOR":           ("Card declined – do not honor", 0.15, "low"),
    "BAD_REQUEST_ERROR":      ("Bad request / generic error",  0.40, "high"),
    "GATEWAY_ERROR":          ("Payment gateway error",        0.55, "high"),
    "NETWORK_ERROR":          ("Network / timeout",            0.60, "high"),
    "INVALID_CARD":           ("Invalid card details",         0.10, "low"),
    "EXPIRED_CARD":           ("Card expired",                 0.05, "very_low"),
    "AUTHENTICATION_FAILED":  ("3DS authentication failed",    0.25, "medium"),
    "MANDATE_REVOKED":        ("Mandate revoked",              0.02, "very_low"),
    "LIMIT_EXCEEDED":         ("Transaction limit exceeded",   0.20, "medium"),
}

CUSTOMER_SEGMENTS = ["new", "regular", "premium", "dormant", "at_risk"]

# How much each segment shifts P(natural_recovery)
SEGMENT_P0_DELTA = {
    "new":      0.00,
    "regular":  0.05,
    "premium":  0.10,
    "dormant": -0.10,
    "at_risk": -0.15,
}

# Action cost in paise (₹)
ACTION_COSTS_PAISE = {
    "RETRY_PAYMENT":     200,    # ₹2
    "SEND_PAYMENT_LINK": 500,    # ₹5
    "OFFER_DISCOUNT":    0,      # cost = discount amount (computed separately)
    "WAIT_AND_RETRY":    200,    # ₹2
    "ESCALATE":          5000,   # ₹50  (human intervention)
    "NO_ACTION":         0,
}

# Discount tier in percentage points (random within range per case)
DISCOUNT_RANGE = (0.02, 0.15)  # 2% – 15%

# Base P(recovery | action) uplift over P(natural_recovery), by recoverability tier
UPLIFT_TABLE: dict[str, dict[str, float]] = {
    # tier         RETRY  LINK   DISC   WAIT   ESCAL  NONE
    "very_low": {
        "RETRY_PAYMENT":     0.05,
        "SEND_PAYMENT_LINK": 0.08,
        "OFFER_DISCOUNT":    0.20,
        "WAIT_AND_RETRY":    0.03,
        "ESCALATE":          0.10,
        "NO_ACTION":         0.00,
    },
    "low": {
        "RETRY_PAYMENT":     0.10,
        "SEND_PAYMENT_LINK": 0.15,
        "OFFER_DISCOUNT":    0.25,
        "WAIT_AND_RETRY":    0.08,
        "ESCALATE":          0.12,
        "NO_ACTION":         0.00,
    },
    "medium": {
        "RETRY_PAYMENT":     0.15,
        "SEND_PAYMENT_LINK": 0.20,
        "OFFER_DISCOUNT":    0.28,
        "WAIT_AND_RETRY":    0.12,
        "ESCALATE":          0.10,
        "NO_ACTION":         0.00,
    },
    "high": {
        "RETRY_PAYMENT":     0.20,
        "SEND_PAYMENT_LINK": 0.18,
        "OFFER_DISCOUNT":    0.22,
        "WAIT_AND_RETRY":    0.25,
        "ESCALATE":          0.08,
        "NO_ACTION":         0.00,
    },
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class CounterfactualOutcomes:
    """Ground-truth probabilities — NEVER shown to the orchestrator."""
    p_natural_recovery: float                        # P(recovery | no action)
    p_by_action: dict[str, float]                    # P(recovery | action)
    net_recovery_by_action: dict[str, int]           # expected net paise per action
    best_action: str                                  # globally optimal (ignoring policy)
    actual_recovered: bool                            # did customer eventually pay?
    actual_recovery_action: str | None               # which action led to recovery


@dataclass
class SyntheticCase:
    # ── Identifiers ───────────────────────────────────────────────────────────
    case_id: str
    split: str                   # "train" | "eval"
    generator_version: str

    # ── Payment context ───────────────────────────────────────────────────────
    payment_id: str
    order_id: str
    customer_id: str

    amount_paise: int            # e.g. 50000 = ₹500
    currency: str
    payment_method: str
    failure_code: str
    failure_label: str
    failure_category: str        # recoverability tier

    # ── Customer context ─────────────────────────────────────────────────────
    customer_segment: str
    previous_successful_payments: int
    previous_failed_payments: int
    previous_recovery_attempts: int  # on prior cases
    days_since_last_success: int
    customer_lifetime_value_paise: int

    # ── Timing ───────────────────────────────────────────────────────────────
    failed_at: str               # ISO-8601
    hours_since_failure: float   # how long ago the failure happened

    # ── Subscription context (only for SUBSCRIPTION_FAILURE cases) ───────────
    is_subscription: bool
    subscription_id: str | None
    mandate_id: str | None
    mandate_status: str | None           # ACTIVE | PAUSED | REVOKED
    notification_sent: bool
    retry_attempt_number: int            # 1 = first retry this billing cycle
    retry_budget_remaining: int          # attempts still allowed

    # ── Policy eligibility flags ─────────────────────────────────────────────
    discount_pct_offered: float          # 0.0 if no discount eligible
    discount_within_policy: bool         # merchant allows up to 10%
    contact_limit_reached: bool
    recovery_window_open: bool           # within 72 h of failure
    mandate_eligible_for_retry: bool

    # ── Ground truth (evaluation only) ───────────────────────────────────────
    counterfactual: CounterfactualOutcomes


# ── Generator ─────────────────────────────────────────────────────────────────

class SyntheticDatasetGenerator:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.seed = seed

    def generate(
        self,
        count: int = 500,
        train_fraction: float = 0.8,
    ) -> tuple[list[SyntheticCase], list[SyntheticCase]]:
        """
        Returns (train_cases, eval_cases).
        train_cases are used to calibrate the orchestrator.
        eval_cases are the held-out benchmark batch.
        """
        all_cases = [self._generate_case(i) for i in range(count)]
        split_idx = int(count * train_fraction)
        train = all_cases[:split_idx]
        eval_ = all_cases[split_idx:]
        # Mark splits
        for c in train:
            c.split = "train"
        for c in eval_:
            c.split = "eval"
        return train, eval_

    def _generate_case(self, idx: int) -> SyntheticCase:
        rng = self.rng

        # ── Identifiers ───────────────────────────────────────────────────────
        case_id     = f"SYN_{idx:05d}_{uuid.UUID(int=rng.getrandbits(128)).hex[:8].upper()}"
        payment_id  = f"pay_syn_{rng.randint(100000, 999999)}"
        order_id    = f"order_syn_{rng.randint(100000, 999999)}"
        customer_id = f"cust_syn_{rng.randint(1000, 9999)}"

        # ── Failure type ──────────────────────────────────────────────────────
        failure_code  = rng.choice(list(FAILURE_CODES.keys()))
        label, base_p0, tier = FAILURE_CODES[failure_code]

        # ── Payment details ───────────────────────────────────────────────────
        # Amounts roughly matching Indian payment distribution
        amount_tiers = [
            (0.35, (10000,  50000)),    # ₹100–₹500    small
            (0.30, (50000,  200000)),   # ₹500–₹2000   medium
            (0.20, (200000, 1000000)),  # ₹2000–₹10000 large
            (0.15, (1000000, 5000000)), # ₹10000–₹50000 high-value
        ]
        tier_choice = rng.random()
        cumulative = 0.0
        lo, hi = 10000, 50000
        for prob, (a, b) in amount_tiers:
            cumulative += prob
            if tier_choice <= cumulative:
                lo, hi = a, b
                break
        amount_paise = rng.randint(lo, hi)

        method = rng.choice(PAYMENT_METHODS)

        # ── Customer context ──────────────────────────────────────────────────
        segment            = rng.choice(CUSTOMER_SEGMENTS)
        prev_success       = rng.randint(0, 50)
        prev_failed        = rng.randint(0, 10)
        prev_recoveries    = rng.randint(0, min(prev_failed, 5))
        days_since_success = rng.randint(0, 365) if prev_success > 0 else 9999
        clv                = rng.randint(amount_paise, amount_paise * 20)

        # ── Timing ────────────────────────────────────────────────────────────
        hours_since = round(rng.uniform(0.1, 96.0), 2)
        failed_at   = (
            datetime.now(timezone.utc) - timedelta(hours=hours_since)
        ).isoformat()

        # ── Subscription context ──────────────────────────────────────────────
        is_sub     = rng.random() < 0.25   # 25% of cases are subscription failures
        sub_id     = f"sub_syn_{rng.randint(10000, 99999)}" if is_sub else None
        mandate_id = f"mand_syn_{rng.randint(10000, 99999)}" if is_sub else None
        mandate_statuses = ["ACTIVE", "ACTIVE", "ACTIVE", "PAUSED", "REVOKED"]
        mand_status = rng.choice(mandate_statuses) if is_sub else None
        notif_sent  = rng.random() < 0.7 if is_sub else False
        retry_num   = rng.randint(1, 3) if is_sub else 0
        retry_budget = max(0, 3 - retry_num) if is_sub else 3

        # ── Policy flags ──────────────────────────────────────────────────────
        discount_pct         = round(rng.uniform(*DISCOUNT_RANGE), 3)
        discount_in_policy   = discount_pct <= 0.10          # merchant max 10%
        contact_limit        = rng.random() < 0.05           # 5% already maxed
        recovery_window_open = hours_since <= 72.0
        mandate_ok = (
            is_sub and
            mand_status == "ACTIVE" and
            notif_sent and
            retry_budget > 0
        )

        # ── Counterfactual outcomes ───────────────────────────────────────────
        cf = self._compute_counterfactuals(
            base_p0      = base_p0,
            tier         = tier,
            segment      = segment,
            amount_paise = amount_paise,
            hours_since  = hours_since,
            prev_failed  = prev_failed,
            discount_pct = discount_pct if discount_in_policy else 0.0,
            rng          = rng,
        )

        return SyntheticCase(
            case_id                      = case_id,
            split                        = "train",        # overwritten by generate()
            generator_version            = GENERATOR_VERSION,
            payment_id                   = payment_id,
            order_id                     = order_id,
            customer_id                  = customer_id,
            amount_paise                 = amount_paise,
            currency                     = "INR",
            payment_method               = method,
            failure_code                 = failure_code,
            failure_label                = label,
            failure_category             = tier,
            customer_segment             = segment,
            previous_successful_payments = prev_success,
            previous_failed_payments     = prev_failed,
            previous_recovery_attempts   = prev_recoveries,
            days_since_last_success      = days_since_success,
            customer_lifetime_value_paise = clv,
            failed_at                    = failed_at,
            hours_since_failure          = hours_since,
            is_subscription              = is_sub,
            subscription_id              = sub_id,
            mandate_id                   = mandate_id,
            mandate_status               = mand_status,
            notification_sent            = notif_sent,
            retry_attempt_number         = retry_num,
            retry_budget_remaining       = retry_budget,
            discount_pct_offered         = discount_pct,
            discount_within_policy       = discount_in_policy,
            contact_limit_reached        = contact_limit,
            recovery_window_open         = recovery_window_open,
            mandate_eligible_for_retry   = mandate_ok,
            counterfactual               = cf,
        )

    def _compute_counterfactuals(
        self,
        base_p0: float,
        tier: str,
        segment: str,
        amount_paise: int,
        hours_since: float,
        prev_failed: int,
        discount_pct: float,
        rng: random.Random,
    ) -> CounterfactualOutcomes:
        """
        Compute P(recovery) values for every action.
        These are the hidden ground-truth values used only for evaluation.
        """
        # Adjust P(natural_recovery) for segment and timing
        p0 = base_p0 + SEGMENT_P0_DELTA.get(segment, 0.0)
        # Payments get harder to recover as time passes
        time_decay = max(0.0, 1.0 - (hours_since / 96.0) * 0.3)
        p0 = min(max(p0 * time_decay, 0.01), 0.95)
        # More prior failures = slightly lower natural recovery
        p0 = max(p0 - prev_failed * 0.01, 0.01)
        # Add a small noise term so synthetic data isn't perfectly deterministic
        p0 = min(max(p0 + rng.gauss(0, 0.03), 0.01), 0.95)

        # Compute P(recovery | action) = p0 + uplift, capped at 0.95
        uplifts = UPLIFT_TABLE.get(tier, UPLIFT_TABLE["medium"])
        p_by_action: dict[str, float] = {}
        for action, uplift in uplifts.items():
            noise = rng.gauss(0, 0.02)
            extra = 0.0
            if action == "OFFER_DISCOUNT":
                # Discount uplift scales with discount size
                extra = discount_pct * 1.5
            pa = min(max(p0 + uplift + extra + noise, p0), 0.97)
            p_by_action[action] = round(pa, 4)

        p0 = round(p0, 4)

        # Expected net recovery per action (paise)
        net_by_action: dict[str, int] = {}
        for action, pa in p_by_action.items():
            uplift   = pa - p0
            cost     = ACTION_COSTS_PAISE.get(action, 0)
            if action == "OFFER_DISCOUNT":
                cost = int(amount_paise * discount_pct)
            net      = int(uplift * amount_paise) - cost
            net_by_action[action] = net

        # Best action by expected net recovery (excluding NO_ACTION)
        best = max(
            (a for a in net_by_action if a != "NO_ACTION"),
            key=lambda a: net_by_action[a],
        )

        # Simulate actual outcome (Bernoulli draw on best available action's Pa)
        best_pa     = p_by_action[best]
        recovered   = rng.random() < best_pa
        recovery_action = best if recovered else None

        return CounterfactualOutcomes(
            p_natural_recovery   = p0,
            p_by_action          = p_by_action,
            net_recovery_by_action = net_by_action,
            best_action          = best,
            actual_recovered     = recovered,
            actual_recovery_action = recovery_action,
        )


# ── Serialisation helpers ─────────────────────────────────────────────────────

def case_to_dict(c: SyntheticCase) -> dict[str, Any]:
    d = asdict(c)
    return d


def save_dataset(
    cases: list[SyntheticCase],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(case_to_dict(c)) + "\n")
    print(f"  Saved {len(cases)} cases → {path}")


def load_dataset(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_summary(train: list[SyntheticCase], eval_: list[SyntheticCase]) -> None:
    all_cases = train + eval_
    total = len(all_cases)
    recovered = sum(1 for c in all_cases if c.counterfactual.actual_recovered)
    sub_cases  = sum(1 for c in all_cases if c.is_subscription)

    amounts = [c.amount_paise for c in all_cases]
    avg_amt  = sum(amounts) / total
    total_at_risk = sum(amounts)

    p0_values = [c.counterfactual.p_natural_recovery for c in all_cases]
    avg_p0 = sum(p0_values) / total

    print(f"\n{'─'*55}")
    print(f"  Synthetic dataset  v{GENERATOR_VERSION}")
    print(f"{'─'*55}")
    print(f"  Total cases        : {total}")
    print(f"  Train / Eval       : {len(train)} / {len(eval_)}")
    print(f"  Subscription cases : {sub_cases} ({sub_cases/total*100:.1f}%)")
    print(f"  Actually recovered : {recovered} ({recovered/total*100:.1f}%)")
    print(f"  Avg P(no-action)   : {avg_p0:.3f}")
    print(f"  Avg amount         : ₹{avg_amt/100:.0f}")
    print(f"  Total at risk      : ₹{total_at_risk/100:,.0f}")

    # Failure code distribution
    from collections import Counter
    code_counts = Counter(c.failure_code for c in all_cases)
    print(f"\n  Failure code distribution:")
    for code, cnt in code_counts.most_common():
        _, _, tier = FAILURE_CODES[code]
        print(f"    {code:<30} {cnt:>4}  ({tier})")

    # Best action distribution
    best_counts = Counter(c.counterfactual.best_action for c in all_cases)
    print(f"\n  Best action distribution (ground truth):")
    for action, cnt in best_counts.most_common():
        print(f"    {action:<25} {cnt:>4}")

    print(f"{'─'*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic RecoverAI dataset")
    parser.add_argument("--count",      type=int,   default=500,  help="Total cases to generate")
    parser.add_argument("--seed",       type=int,   default=42,   help="Random seed")
    parser.add_argument("--split",      type=float, default=0.8,  help="Train fraction (default 0.8)")
    parser.add_argument("--output-dir", type=str,   default="data/synthetic", help="Output directory")
    parser.add_argument("--eval-only",  action="store_true", help="Save only the eval split")
    args = parser.parse_args()

    gen = SyntheticDatasetGenerator(seed=args.seed)
    train, eval_ = gen.generate(count=args.count, train_fraction=args.split)

    out = Path(args.output_dir)
    if not args.eval_only:
        save_dataset(train, out / f"train_seed{args.seed}_n{len(train)}.jsonl")
    save_dataset(eval_, out / f"eval_seed{args.seed}_n{len(eval_)}.jsonl")

    _print_summary(train, eval_)
