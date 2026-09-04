"""
Recovery Actions - definitions and economic scoring
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.recovery.states import RecoveryAction


# Cost table (paise)
ACTION_BASE_COSTS_PAISE: dict[str, int] = {
    RecoveryAction.RETRY_PAYMENT:     200,
    RecoveryAction.SEND_PAYMENT_LINK: 500,
    RecoveryAction.OFFER_DISCOUNT:    0,
    RecoveryAction.WAIT_AND_RETRY:    200,
    RecoveryAction.SEND_REMINDER:     150,  # email/SMS cost
    RecoveryAction.ESCALATE:          5000,
    RecoveryAction.NO_ACTION:         0,
}


UPLIFT_BY_CATEGORY: dict[str, dict[str, float]] = {
    "very_low": {
        RecoveryAction.RETRY_PAYMENT:     0.05,
        RecoveryAction.SEND_PAYMENT_LINK: 0.08,
        RecoveryAction.OFFER_DISCOUNT:    0.20,
        RecoveryAction.WAIT_AND_RETRY:    0.03,
        RecoveryAction.SEND_REMINDER:     0.06,
        RecoveryAction.ESCALATE:          0.10,
        RecoveryAction.NO_ACTION:         0.00,
    },
    "low": {
        RecoveryAction.RETRY_PAYMENT:     0.10,
        RecoveryAction.SEND_PAYMENT_LINK: 0.15,
        RecoveryAction.OFFER_DISCOUNT:    0.25,
        RecoveryAction.WAIT_AND_RETRY:    0.08,
        RecoveryAction.SEND_REMINDER:     0.12,
        RecoveryAction.ESCALATE:          0.12,
        RecoveryAction.NO_ACTION:         0.00,
    },
    "medium": {
        RecoveryAction.RETRY_PAYMENT:     0.15,
        RecoveryAction.SEND_PAYMENT_LINK: 0.20,
        RecoveryAction.OFFER_DISCOUNT:    0.28,
        RecoveryAction.WAIT_AND_RETRY:    0.12,
        RecoveryAction.SEND_REMINDER:     0.15,
        RecoveryAction.ESCALATE:          0.10,
        RecoveryAction.NO_ACTION:         0.00,
    },
    "high": {
        RecoveryAction.RETRY_PAYMENT:     0.20,
        RecoveryAction.SEND_PAYMENT_LINK: 0.18,
        RecoveryAction.OFFER_DISCOUNT:    0.22,
        RecoveryAction.WAIT_AND_RETRY:    0.25,
        RecoveryAction.SEND_REMINDER:     0.14,
        RecoveryAction.ESCALATE:          0.08,
        RecoveryAction.NO_ACTION:         0.00,
    },
    "receivable": {
        RecoveryAction.RETRY_PAYMENT:     0.02,
        RecoveryAction.SEND_PAYMENT_LINK: 0.25,
        RecoveryAction.OFFER_DISCOUNT:    0.30,
        RecoveryAction.WAIT_AND_RETRY:    0.05,
        RecoveryAction.SEND_REMINDER:     0.22,
        RecoveryAction.ESCALATE:          0.18,
        RecoveryAction.NO_ACTION:         0.00,
    },
}

FAILURE_CODE_CATEGORY: dict[str, str] = {
    "INSUFFICIENT_FUNDS":    "medium",
    "DO_NOT_HONOR":          "low",
    "BAD_REQUEST_ERROR":     "high",
    "GATEWAY_ERROR":         "high",
    "NETWORK_ERROR":         "high",
    "INVALID_CARD":          "low",
    "EXPIRED_CARD":          "very_low",
    "AUTHENTICATION_FAILED": "medium",
    "MANDATE_REVOKED":       "very_low",
    "LIMIT_EXCEEDED":        "medium",
    "OVERDUE":               "receivable",
    "PAST_DUE":              "receivable",
}


@dataclass
class ActionScore:
    action: str
    p0: float
    pa: float
    uplift: float
    cost_paise: int
    amount_paise: int
    expected_net_recovery_paise: int
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "p0": round(self.p0, 4),
            "pa": round(self.pa, 4),
            "uplift": round(self.uplift, 4),
            "cost_paise": self.cost_paise,
            "amount_paise": self.amount_paise,
            "expected_net_recovery_paise": self.expected_net_recovery_paise,
            "reasoning": self.reasoning,
        }


def estimate_p0(case_context: dict[str, Any]) -> float:
    failure_code = case_context.get("failure_code", "BAD_REQUEST_ERROR")
    segment = case_context.get("customer_segment", "regular")
    hours_since = float(case_context.get("hours_since_failure", 1.0))
    prev_failed = int(case_context.get("previous_failed_payments", 0))
    case_type = case_context.get("case_type", "PAYMENT_FAILURE")

    if case_type == "OVERDUE_RECEIVABLE":
        category = "receivable"
    else:
        category = FAILURE_CODE_CATEGORY.get(failure_code, "medium")

    base_p0_by_category = {
        "very_low": 0.08, "low": 0.15, "medium": 0.30,
        "high": 0.55, "receivable": 0.25,
    }
    p0 = base_p0_by_category.get(category, 0.30)

    segment_delta = {"new": 0.00, "regular": 0.05, "premium": 0.10, "dormant": -0.10, "at_risk": -0.15}
    p0 += segment_delta.get(segment, 0.0)

    if case_type == "OVERDUE_RECEIVABLE":
        time_decay = max(0.0, 1.0 - (hours_since / 720.0) * 0.4)
    else:
        time_decay = max(0.0, 1.0 - (hours_since / 96.0) * 0.3)
    p0 *= time_decay
    p0 -= prev_failed * 0.01

    return round(min(max(p0, 0.01), 0.95), 4)


def estimate_pa(action: str, p0: float, case_context: dict[str, Any]) -> float:
    failure_code = case_context.get("failure_code", "BAD_REQUEST_ERROR")
    case_type = case_context.get("case_type", "PAYMENT_FAILURE")

    if case_type == "OVERDUE_RECEIVABLE":
        category = "receivable"
    else:
        category = FAILURE_CODE_CATEGORY.get(failure_code, "medium")

    uplifts = UPLIFT_BY_CATEGORY.get(category, UPLIFT_BY_CATEGORY["medium"])
    uplift = uplifts.get(action, 0.0)

    if action == RecoveryAction.OFFER_DISCOUNT:
        discount_pct = float(case_context.get("discount_pct_offered", 0.05))
        uplift += discount_pct * 1.5

    return round(min(max(p0 + uplift, p0), 0.97), 4)


def compute_cost(action: str, case_context: dict[str, Any]) -> int:
    if action == RecoveryAction.OFFER_DISCOUNT:
        amount = int(case_context.get("amount_paise", 0))
        discount_pct = float(case_context.get("discount_pct_offered", 0.05))
        return int(amount * discount_pct)
    return ACTION_BASE_COSTS_PAISE.get(action, 0)


def score_action(action: str, p0: float, case_context: dict[str, Any]) -> ActionScore:
    amount = int(case_context.get("amount_paise", 0))
    pa = estimate_pa(action, p0, case_context)
    uplift = round(pa - p0, 4)
    cost = compute_cost(action, case_context)
    net = int(uplift * amount) - cost

    reasoning = (
        f"P(no-action)={p0:.3f} -> P({action})={pa:.3f}  "
        f"uplift={uplift:.3f}  amount=Rs{amount//100}  cost=Rs{cost//100}  "
        f"expected_net=Rs{net//100}"
    )

    return ActionScore(
        action=action, p0=p0, pa=pa, uplift=uplift,
        cost_paise=cost, amount_paise=amount,
        expected_net_recovery_paise=net, reasoning=reasoning,
    )


def score_all_actions(case_context: dict[str, Any]) -> list[ActionScore]:
    from app.recovery.states import ALL_ACTIONS
    p0 = estimate_p0(case_context)
    scores = [score_action(a, p0, case_context) for a in ALL_ACTIONS]
    scores.sort(key=lambda s: s.expected_net_recovery_paise, reverse=True)
    return scores
