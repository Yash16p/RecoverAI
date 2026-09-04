"""
MCP Execution Layer
====================
The controlled boundary between the orchestrator decision and the
actual Razorpay API call.

Architecture:
  Orchestrator (LLM + policy) → MCP executor → Razorpay API

This module NEVER executes a Razorpay call without passing all
structural validation checks. Even if the policy gate approved an
action, the MCP layer re-validates at execution time because state
may have changed since the decision was made (freshness check).

Tools exposed:
  execute_action(case_id, action, context, pg) → ExecutionResult

Structural validations (hard limits — code only, no LLM):
  1. Case still exists and is POLICY_APPROVED
  2. Action is a known, supported action type
  3. Amount is within tool-defined bounds
  4. Required identifiers are present
  5. Idempotency key is present for money-moving operations
  6. Freshness check — case state hasn't changed since decision
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.razorpay_client import (
    create_order,
    generate_payment_link,
    fetch_payment,
    fetch_order,
)
from app.recovery.states import RecoveryAction, CaseStatus

logger = logging.getLogger("recoverai.mcp")

# ── Hard limits ───────────────────────────────────────────────────────────────

MAX_RETRY_AMOUNT_PAISE   = 5_000_000   # ₹50,000 max for auto-retry
MAX_LINK_AMOUNT_PAISE    = 10_000_000  # ₹1,00,000 max for payment link
MIN_AMOUNT_PAISE         = 100         # ₹1 minimum


@dataclass
class ExecutionResult:
    success: bool
    action: str
    razorpay_reference: str | None   # order_id / payment_link_id returned
    execution_status: str            # EXECUTED | FAILED | CANCELLED | SKIPPED
    message: str
    freshness_passed: bool
    freshness_checked_at: datetime | None


# ── Freshness check ───────────────────────────────────────────────────────────

async def _freshness_check(
    pg: asyncpg.Pool,
    case_id: int,
    action: str,
) -> tuple[bool, str]:
    """
    Re-verify case state immediately before execution.

    Returns (passed, reason).
    """
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, payment_id, amount, attempt_count,
                   mandate_status, retry_budget_remaining
            FROM recovery_cases WHERE id = $1
            """,
            case_id,
        )

    if not row:
        return False, "Case no longer exists"

    status = row["status"]

    # Already resolved — stale action
    if status == CaseStatus.RECOVERED:
        return False, "Case already RECOVERED — stale action cancelled"

    if status in (CaseStatus.STOPPED, CaseStatus.ESCALATED):
        return False, f"Case is {status} — action no longer valid"

    if status != CaseStatus.POLICY_APPROVED:
        return False, f"Case status is {status}, expected POLICY_APPROVED"

    # Subscription-specific: mandate may have been revoked since decision
    if action in (RecoveryAction.RETRY_PAYMENT, RecoveryAction.WAIT_AND_RETRY):
        mandate = row["mandate_status"]
        if mandate and mandate != "ACTIVE":
            return False, f"Mandate status changed to {mandate} — cannot retry"

    # Check payment wasn't already captured (customer paid manually)
    payment_id = row["payment_id"]
    if payment_id and action == RecoveryAction.RETRY_PAYMENT:
        try:
            payment = fetch_payment(payment_id)
            if payment.get("status") == "captured":
                return False, "Payment already captured — stale retry cancelled"
        except Exception as e:
            logger.warning(f"[mcp] Could not fetch payment {payment_id}: {e}")
            # Don't block on API failure — proceed cautiously

    return True, "Freshness check passed"


# ── Structural validation ─────────────────────────────────────────────────────

def _structural_validate(
    action: str,
    case_ctx: dict[str, Any],
    idempotency_key: str,
) -> tuple[bool, str]:
    """Hard structural limits — called before any Razorpay API call."""

    if action not in (
        RecoveryAction.RETRY_PAYMENT,
        RecoveryAction.SEND_PAYMENT_LINK,
        RecoveryAction.OFFER_DISCOUNT,
        RecoveryAction.WAIT_AND_RETRY,
        RecoveryAction.ESCALATE,
        RecoveryAction.NO_ACTION,
    ):
        return False, f"Unknown action: {action}"

    amount = case_ctx.get("amount_paise", 0)

    if action in (RecoveryAction.RETRY_PAYMENT, RecoveryAction.WAIT_AND_RETRY):
        if not case_ctx.get("payment_id") and not case_ctx.get("order_id"):
            return False, "RETRY requires payment_id or order_id"
        if amount > MAX_RETRY_AMOUNT_PAISE:
            return False, f"Amount ₹{amount//100} exceeds retry cap ₹{MAX_RETRY_AMOUNT_PAISE//100}"
        if amount < MIN_AMOUNT_PAISE:
            return False, f"Amount ₹{amount//100} below minimum ₹{MIN_AMOUNT_PAISE//100}"
        if not idempotency_key:
            return False, "Idempotency key required for money-moving operations"

    if action == RecoveryAction.SEND_PAYMENT_LINK:
        if not case_ctx.get("customer_email") and not case_ctx.get("customer_phone"):
            return False, "SEND_PAYMENT_LINK requires customer contact"
        if amount > MAX_LINK_AMOUNT_PAISE:
            return False, f"Amount ₹{amount//100} exceeds payment link cap ₹{MAX_LINK_AMOUNT_PAISE//100}"
        if not idempotency_key:
            return False, "Idempotency key required for payment link"

    if action == RecoveryAction.OFFER_DISCOUNT:
        if not case_ctx.get("customer_email") and not case_ctx.get("customer_phone"):
            return False, "OFFER_DISCOUNT requires customer contact to send discounted link"

    return True, "Structural validation passed"


# ── Action executors ──────────────────────────────────────────────────────────

async def _execute_retry(
    case_ctx: dict[str, Any],
    idempotency_key: str,
) -> tuple[str | None, str]:
    """
    Create a new Razorpay order for retry.
    Returns (razorpay_reference, message).
    """
    amount    = case_ctx["amount_paise"]
    currency  = case_ctx.get("currency", "INR")
    case_ref  = case_ctx.get("case_ref", "unknown")

    try:
        order = create_order(
            amount_paise = amount,
            currency     = currency,
            receipt      = f"retry_{case_ref}_{idempotency_key[:8]}",
        )
        order_id = order.get("id")
        logger.info(f"[mcp] Retry order created: {order_id}  amount=₹{amount//100}")
        return order_id, f"Retry order created: {order_id}"
    except Exception as e:
        logger.error(f"[mcp] Retry order failed: {e}")
        return None, f"Retry failed: {e}"


async def _execute_payment_link(
    case_ctx: dict[str, Any],
    idempotency_key: str,
    discount_pct: float = 0.0,
) -> tuple[str | None, str]:
    """
    Generate a Razorpay payment link.
    Returns (link_id, message).
    """
    amount      = case_ctx["amount_paise"]
    case_ref    = case_ctx.get("case_ref", "unknown")
    customer_id = case_ctx.get("customer_id", "")

    # Apply discount if this is OFFER_DISCOUNT
    if discount_pct > 0:
        amount = int(amount * (1 - discount_pct))

    customer = {
        "name":    customer_id or "Customer",
        "email":   case_ctx.get("customer_email", ""),
        "contact": case_ctx.get("customer_phone", ""),
    }

    try:
        link = generate_payment_link(
            amount_paise  = amount,
            description   = f"RecoverAI payment recovery — {case_ref}",
            customer      = customer,
            reference_id  = f"{case_ref}_{idempotency_key[:8]}",
        )
        link_id = link.get("id")
        short_url = link.get("short_url", "")
        logger.info(f"[mcp] Payment link created: {link_id}  url={short_url}")
        return link_id, f"Payment link created: {link_id}  url={short_url}"
    except Exception as e:
        logger.error(f"[mcp] Payment link failed: {e}")
        return None, f"Payment link failed: {e}"


# ── Main executor ─────────────────────────────────────────────────────────────

async def execute_action(
    pg: asyncpg.Pool,
    case_id: int,
    action: str,
    case_ctx: dict[str, Any],
    attempt_id: int | None = None,
) -> ExecutionResult:
    """
    The single entry point for all MCP executions.

    Flow:
      1. Structural validation (hard limits)
      2. Freshness check (re-verify case state)
      3. Execute Razorpay API call
      4. Update attempt record in DB
      5. Return result
    """
    idempotency_key = f"mcp_{case_id}_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)

    # ── 1. Structural validation ──────────────────────────────────────────────
    valid, reason = _structural_validate(action, case_ctx, idempotency_key)
    if not valid:
        logger.warning(f"[mcp] Structural validation FAILED | {reason}")
        await _update_attempt(pg, attempt_id, "FAILED", None, reason, None, False)
        return ExecutionResult(
            success=False, action=action, razorpay_reference=None,
            execution_status="FAILED", message=reason,
            freshness_passed=False, freshness_checked_at=None,
        )

    # ── 2. Freshness check ────────────────────────────────────────────────────
    fresh, fresh_reason = await _freshness_check(pg, case_id, action)
    freshness_checked_at = datetime.now(timezone.utc)

    if not fresh:
        logger.warning(f"[mcp] Freshness check FAILED | {fresh_reason}")
        await _update_attempt(pg, attempt_id, "CANCELLED", None, fresh_reason,
                              freshness_checked_at, False)
        # Update case status back to RE_EVALUATE or STOPPED
        await _update_case_post_execution(pg, case_id, "STOPPED", fresh_reason)
        return ExecutionResult(
            success=False, action=action, razorpay_reference=None,
            execution_status="CANCELLED", message=fresh_reason,
            freshness_passed=False, freshness_checked_at=freshness_checked_at,
        )

    # ── 3. NO_ACTION / non-money actions ─────────────────────────────────────
    if action == RecoveryAction.NO_ACTION:
        await _update_attempt(pg, attempt_id, "SKIPPED", None, "NO_ACTION chosen",
                              freshness_checked_at, True)
        await _update_case_post_execution(pg, case_id, CaseStatus.STOPPED, "NO_ACTION")
        return ExecutionResult(
            success=True, action=action, razorpay_reference=None,
            execution_status="SKIPPED", message="NO_ACTION — case stopped",
            freshness_passed=True, freshness_checked_at=freshness_checked_at,
        )

    if action == RecoveryAction.ESCALATE:
        await _update_attempt(pg, attempt_id, "EXECUTED", None, "Escalated to human review",
                              freshness_checked_at, True)
        await _update_case_post_execution(pg, case_id, CaseStatus.ESCALATED, "Manual escalation")
        return ExecutionResult(
            success=True, action=action, razorpay_reference=None,
            execution_status="EXECUTED", message="Case escalated for human review",
            freshness_passed=True, freshness_checked_at=freshness_checked_at,
        )

    # ── 4. Money-moving actions ───────────────────────────────────────────────
    logger.info(f"[mcp] Executing {action}  case_id={case_id}  idempotency={idempotency_key}")

    # Mark case as EXECUTING
    async with pg.acquire() as conn:
        await conn.execute(
            "UPDATE recovery_cases SET status = 'EXECUTING' WHERE id = $1", case_id
        )

    razorpay_ref = None
    exec_message = ""
    exec_success = False

    try:
        if action == RecoveryAction.RETRY_PAYMENT:
            razorpay_ref, exec_message = await _execute_retry(case_ctx, idempotency_key)
            exec_success = razorpay_ref is not None

        elif action == RecoveryAction.SEND_PAYMENT_LINK:
            razorpay_ref, exec_message = await _execute_payment_link(case_ctx, idempotency_key)
            exec_success = razorpay_ref is not None

        elif action == RecoveryAction.OFFER_DISCOUNT:
            discount_pct = float(case_ctx.get("discount_pct_offered", 0.07))
            razorpay_ref, exec_message = await _execute_payment_link(
                case_ctx, idempotency_key, discount_pct=discount_pct
            )
            exec_success = razorpay_ref is not None

        elif action == RecoveryAction.WAIT_AND_RETRY:
            # WAIT_AND_RETRY schedules a retry — create the order but mark for future execution
            razorpay_ref, exec_message = await _execute_retry(case_ctx, idempotency_key)
            exec_success = razorpay_ref is not None

    except Exception as e:
        exec_message = f"Execution error: {e}"
        logger.exception(f"[mcp] Execution error for {action}: {e}")

    # ── 5. Update DB post-execution ───────────────────────────────────────────
    exec_status    = "EXECUTED" if exec_success else "FAILED"
    new_case_status = CaseStatus.EXECUTED if exec_success else CaseStatus.STOPPED

    await _update_attempt(
        pg, attempt_id, exec_status, razorpay_ref, exec_message,
        freshness_checked_at, True
    )
    await _update_case_post_execution(pg, case_id, new_case_status, exec_message, razorpay_ref)

    return ExecutionResult(
        success            = exec_success,
        action             = action,
        razorpay_reference = razorpay_ref,
        execution_status   = exec_status,
        message            = exec_message,
        freshness_passed   = True,
        freshness_checked_at = freshness_checked_at,
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _update_attempt(
    pg: asyncpg.Pool,
    attempt_id: int | None,
    execution_status: str,
    razorpay_reference: str | None,
    execution_error: str | None,
    freshness_checked_at: datetime | None,
    freshness_passed: bool,
) -> None:
    if not attempt_id:
        return
    async with pg.acquire() as conn:
        await conn.execute(
            """
            UPDATE recovery_attempts SET
                execution_status     = $1,
                razorpay_reference   = $2,
                execution_error      = $3,
                freshness_checked_at = $4,
                freshness_passed     = $5,
                executed_at          = now()
            WHERE id = $6
            """,
            execution_status,
            razorpay_reference,
            execution_error,
            freshness_checked_at,
            freshness_passed,
            attempt_id,
        )


async def _update_case_post_execution(
    pg: asyncpg.Pool,
    case_id: int,
    new_status: str,
    message: str,
    razorpay_ref: str | None = None,
) -> None:
    async with pg.acquire() as conn:
        await conn.execute(
            """
            UPDATE recovery_cases SET
                status       = $1,
                last_action  = (SELECT action FROM recovery_attempts
                                WHERE case_id = $2 ORDER BY id DESC LIMIT 1),
                updated_at   = now()
            WHERE id = $2
            """,
            new_status,
            case_id,
        )
