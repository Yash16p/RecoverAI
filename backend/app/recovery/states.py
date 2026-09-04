"""
RecoveryCase state machine — single source of truth.

Every status value used in the DB must be defined here.
The orchestrator (LangGraph) and the worker both import from this module.
"""
from __future__ import annotations


# ── Case statuses ─────────────────────────────────────────────────────────────

class CaseStatus:
    CREATED             = "CREATED"
    DIAGNOSED           = "DIAGNOSED"
    SCORED              = "SCORED"
    ACTION_RECOMMENDED  = "ACTION_RECOMMENDED"
    POLICY_APPROVED     = "POLICY_APPROVED"
    POLICY_BLOCKED      = "POLICY_BLOCKED"
    SCHEDULED           = "SCHEDULED"
    EXECUTING           = "EXECUTING"
    EXECUTED            = "EXECUTED"
    RE_EVALUATE         = "RE_EVALUATE"
    # ── Terminal ──────────────────────────────────────────────────────────────
    RECOVERED           = "RECOVERED"
    STOPPED             = "STOPPED"
    ESCALATED           = "ESCALATED"


TERMINAL_STATUSES: frozenset[str] = frozenset({
    CaseStatus.RECOVERED,
    CaseStatus.STOPPED,
    CaseStatus.ESCALATED,
})

# Allowed transitions: {from_status: {to_status, ...}}
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CaseStatus.CREATED:            frozenset({CaseStatus.DIAGNOSED}),
    CaseStatus.DIAGNOSED:          frozenset({CaseStatus.SCORED, CaseStatus.STOPPED}),
    CaseStatus.SCORED:             frozenset({CaseStatus.ACTION_RECOMMENDED, CaseStatus.STOPPED}),
    CaseStatus.ACTION_RECOMMENDED: frozenset({CaseStatus.POLICY_APPROVED, CaseStatus.POLICY_BLOCKED}),
    CaseStatus.POLICY_APPROVED:    frozenset({CaseStatus.SCHEDULED, CaseStatus.EXECUTING}),
    CaseStatus.POLICY_BLOCKED:     frozenset({CaseStatus.SCORED, CaseStatus.STOPPED, CaseStatus.ESCALATED}),
    CaseStatus.SCHEDULED:          frozenset({CaseStatus.EXECUTING, CaseStatus.STOPPED}),
    CaseStatus.EXECUTING:          frozenset({CaseStatus.EXECUTED}),
    CaseStatus.EXECUTED:           frozenset({CaseStatus.RECOVERED, CaseStatus.RE_EVALUATE, CaseStatus.STOPPED}),
    CaseStatus.RE_EVALUATE:        frozenset({CaseStatus.SCORED, CaseStatus.STOPPED, CaseStatus.ESCALATED}),
    CaseStatus.RECOVERED:          frozenset(),
    CaseStatus.STOPPED:            frozenset(),
    CaseStatus.ESCALATED:          frozenset(),
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def assert_valid_transition(from_status: str, to_status: str) -> None:
    if not is_valid_transition(from_status, to_status):
        raise ValueError(
            f"Invalid case transition: {from_status!r} -> {to_status!r}. "
            f"Allowed from {from_status!r}: {sorted(ALLOWED_TRANSITIONS.get(from_status, set()))}"
        )


# ── Recovery actions ──────────────────────────────────────────────────────────

class RecoveryAction:
    RETRY_PAYMENT      = "RETRY_PAYMENT"
    SEND_PAYMENT_LINK  = "SEND_PAYMENT_LINK"
    OFFER_DISCOUNT     = "OFFER_DISCOUNT"
    WAIT_AND_RETRY     = "WAIT_AND_RETRY"
    SEND_REMINDER      = "SEND_REMINDER"      # Receivables: polite payment reminder
    ESCALATE           = "ESCALATE"
    NO_ACTION          = "NO_ACTION"


ALL_ACTIONS: tuple[str, ...] = (
    RecoveryAction.RETRY_PAYMENT,
    RecoveryAction.SEND_PAYMENT_LINK,
    RecoveryAction.OFFER_DISCOUNT,
    RecoveryAction.WAIT_AND_RETRY,
    RecoveryAction.SEND_REMINDER,
    RecoveryAction.ESCALATE,
    RecoveryAction.NO_ACTION,
)


# ── Attempt outcomes ──────────────────────────────────────────────────────────

class AttemptOutcome:
    PAID      = "PAID"
    FAILED    = "FAILED"
    EXPIRED   = "EXPIRED"
    PENDING   = "PENDING"
    CANCELLED = "CANCELLED"
    UNKNOWN   = "UNKNOWN"


# ── Policy decisions ──────────────────────────────────────────────────────────

class PolicyDecision:
    APPROVED = "APPROVED"
    BLOCKED  = "BLOCKED"


# ── Stop reasons ──────────────────────────────────────────────────────────────

class StopReason:
    RETRY_BUDGET_EXHAUSTED   = "RETRY_BUDGET_EXHAUSTED"
    CONTACT_LIMIT_REACHED    = "CONTACT_LIMIT_REACHED"
    RECOVERY_WINDOW_EXPIRED  = "RECOVERY_WINDOW_EXPIRED"
    UNECONOMIC               = "UNECONOMIC"
    ALL_ACTIONS_BLOCKED      = "ALL_ACTIONS_BLOCKED"
    CASE_DISPUTED            = "CASE_DISPUTED"
    EXECUTION_FAILURE        = "EXECUTION_FAILURE"
    MANDATE_REVOKED          = "MANDATE_REVOKED"
    ALREADY_PAID             = "ALREADY_PAID"
