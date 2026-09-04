"""
Policy Gate - deterministic, no LLM
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.recovery.states import (
    PolicyDecision,
    RecoveryAction,
    StopReason,
    TERMINAL_STATUSES,
)

MAX_RETRY_ATTEMPTS    = 3
MAX_CONTACT_ATTEMPTS  = 2
RECOVERY_WINDOW_HOURS = 72.0
RECEIVABLE_WINDOW_HOURS = 720.0  # 30 days for invoices
MAX_DISCOUNT_PCT      = 0.10
ESCALATION_MIN_PAISE  = 100000
MAX_REMINDERS         = 3  # Max reminder messages for receivables

NO_RETRY_CODES = {"INVALID_CARD", "EXPIRED_CARD", "MANDATE_REVOKED"}


@dataclass
class PolicyResult:
    decision: str
    reason: str | None
    stop_reason: str | None
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.decision == PolicyDecision.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "stop_reason": self.stop_reason,
            "checks": self.checks,
        }


def _approved(checks: dict[str, bool]) -> PolicyResult:
    return PolicyResult(PolicyDecision.APPROVED, None, None, checks)


def _blocked(reason: str, stop_reason: str, checks: dict[str, bool]) -> PolicyResult:
    return PolicyResult(PolicyDecision.BLOCKED, reason, stop_reason, checks)


def check_policy(
    action: str,
    case_context: dict[str, Any],
    expected_net_recovery_paise: int,
) -> PolicyResult:
    checks: dict[str, bool] = {}
    case_type = case_context.get("case_type", "PAYMENT_FAILURE")

    # Rule 1: Recovery window
    hours_since = float(case_context.get("hours_since_failure", 0.0))
    max_window = RECEIVABLE_WINDOW_HOURS if case_type == "OVERDUE_RECEIVABLE" else RECOVERY_WINDOW_HOURS
    window_open = hours_since <= max_window
    checks["recovery_window_open"] = window_open
    if not window_open:
        return _blocked(
            f"Recovery window expired ({hours_since:.1f} h since failure, max {max_window} h)",
            StopReason.RECOVERY_WINDOW_EXPIRED, checks,
        )

    # Rule 2: Case still open
    case_status = case_context.get("status", "CREATED")
    case_open = case_status not in TERMINAL_STATUSES
    checks["case_open"] = case_open
    if not case_open:
        return _blocked(f"Case is already in terminal state: {case_status}",
                        StopReason.ALREADY_PAID, checks)

    # Rule 3: Retry budget
    attempt_count = int(case_context.get("attempt_count", 0))
    budget_ok = attempt_count < MAX_RETRY_ATTEMPTS
    checks["retry_budget_remaining"] = budget_ok
    if not budget_ok:
        return _blocked(f"Retry budget exhausted ({attempt_count}/{MAX_RETRY_ATTEMPTS} attempts used)",
                        StopReason.RETRY_BUDGET_EXHAUSTED, checks)

    # Rule 4: Contact limit
    contact_limit = bool(case_context.get("contact_limit_reached", False))
    checks["contact_limit_ok"] = not contact_limit
    if contact_limit:
        return _blocked("Customer contact limit reached", StopReason.CONTACT_LIMIT_REACHED, checks)

    # Rule 5: Economic viability
    if action != RecoveryAction.NO_ACTION:
        economically_viable = expected_net_recovery_paise > 0
        checks["economically_viable"] = economically_viable
        if not economically_viable:
            return _blocked(f"Expected net recovery is non-positive (Rs{expected_net_recovery_paise//100})",
                            StopReason.UNECONOMIC, checks)

    # Rule 6: Action-specific checks
    failure_code = case_context.get("failure_code", "")

    if action == RecoveryAction.RETRY_PAYMENT:
        retryable = failure_code not in NO_RETRY_CODES
        checks["failure_code_retryable"] = retryable
        if not retryable:
            return _blocked(f"Failure code {failure_code!r} is not retryable via direct retry",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

    elif action == RecoveryAction.SEND_PAYMENT_LINK:
        has_contact = bool(case_context.get("customer_email") or case_context.get("customer_phone"))
        checks["customer_contact_available"] = has_contact
        if not has_contact:
            return _blocked("No customer contact information available for payment link",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

    elif action == RecoveryAction.OFFER_DISCOUNT:
        discount_pct = float(case_context.get("discount_pct_offered", 0.0))
        discount_ok = discount_pct <= MAX_DISCOUNT_PCT
        checks["discount_within_policy"] = discount_ok
        if not discount_ok:
            return _blocked(f"Discount {discount_pct*100:.1f}% exceeds merchant maximum {MAX_DISCOUNT_PCT*100:.0f}%",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

    elif action == RecoveryAction.WAIT_AND_RETRY:
        budget_for_wait = attempt_count < MAX_RETRY_ATTEMPTS - 1
        checks["budget_for_wait_and_retry"] = budget_for_wait
        if not budget_for_wait:
            return _blocked("Insufficient retry budget remaining for wait-and-retry",
                            StopReason.RETRY_BUDGET_EXHAUSTED, checks)

    elif action == RecoveryAction.SEND_REMINDER:
        # Reminder is primarily for receivables
        reminders_sent = int(case_context.get("reminders_sent", 0))
        reminder_ok = reminders_sent < MAX_REMINDERS
        checks["reminder_limit_ok"] = reminder_ok
        has_contact = bool(case_context.get("customer_email") or case_context.get("customer_phone"))
        checks["customer_contact_available"] = has_contact
        if not reminder_ok:
            return _blocked(f"Reminder limit reached ({reminders_sent}/{MAX_REMINDERS})",
                            StopReason.CONTACT_LIMIT_REACHED, checks)
        if not has_contact:
            return _blocked("No customer contact information available for reminder",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

    elif action == RecoveryAction.ESCALATE:
        amount_paise = int(case_context.get("amount_paise", 0))
        worth_escalating = amount_paise >= ESCALATION_MIN_PAISE
        checks["amount_justifies_escalation"] = worth_escalating
        if not worth_escalating:
            return _blocked(f"Amount Rs{amount_paise//100} below escalation threshold Rs{ESCALATION_MIN_PAISE//100}",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

    # Rule 7: Mandate / subscription checks
    is_subscription = bool(case_context.get("is_subscription", False))
    if is_subscription and action in (RecoveryAction.RETRY_PAYMENT, RecoveryAction.WAIT_AND_RETRY):
        mandate_status = case_context.get("mandate_status", "ACTIVE")
        mandate_ok = mandate_status == "ACTIVE"
        checks["mandate_active"] = mandate_ok
        if not mandate_ok:
            return _blocked(f"Mandate status is {mandate_status!r} - cannot retry",
                            StopReason.MANDATE_REVOKED, checks)

        notif_sent = bool(case_context.get("notification_sent", False))
        checks["notification_sent"] = notif_sent
        if not notif_sent:
            return _blocked("Mandate notification not yet sent to customer",
                            StopReason.ALL_ACTIONS_BLOCKED, checks)

        retry_budget = int(case_context.get("retry_budget_remaining", 0))
        checks["mandate_retry_budget"] = retry_budget > 0
        if retry_budget <= 0:
            return _blocked("Mandate retry budget exhausted for this billing cycle",
                            StopReason.RETRY_BUDGET_EXHAUSTED, checks)

    return _approved(checks)


def filter_permitted_actions(
    scored_actions: list,
    case_context: dict[str, Any],
) -> tuple[list, list]:
    permitted = []
    blocked = []

    for score in scored_actions:
        result = check_policy(
            action=score.action,
            case_context=case_context,
            expected_net_recovery_paise=score.expected_net_recovery_paise,
        )
        score.__dict__["policy"] = result
        if result.approved:
            permitted.append(score)
        else:
            blocked.append(score)

    return permitted, blocked
