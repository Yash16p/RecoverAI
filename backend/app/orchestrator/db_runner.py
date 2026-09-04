"""
Orchestrator DB Runner
========================
Bridges the async worker (asyncpg) and the sync LangGraph orchestrator.

Responsibilities:
  1. Load a CREATED RecoveryCase from PostgreSQL into a context dict
  2. Call the LangGraph graph (sync, runs in a thread executor)
  3. Persist the decision back to recovery_cases + recovery_attempts
  4. Return the final status
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger("recoverai.orchestrator.db")


# ── Load case context from DB ─────────────────────────────────────────────────

async def load_case_context(pg: asyncpg.Pool, case_id: int) -> dict[str, Any] | None:
    """
    Fetch all fields needed by the orchestrator from recovery_cases.
    Returns None if the case does not exist or is already terminal.
    """
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                id, case_ref, case_type, status,
                payment_id, order_id, customer_id,
                subscription_id, invoice_id,
                amount, currency,
                failure_reason, payment_method, failure_category,
                attempt_count,
                mandate_id, mandate_status,
                notification_sent_at, retry_budget_remaining,
                source_event_id
            FROM recovery_cases
            WHERE id = $1
            """,
            case_id,
        )

    if not row:
        logger.warning(f"[db_runner] Case id={case_id} not found")
        return None

    if row["status"] not in ("CREATED", "RE_EVALUATE"):
        logger.info(
            f"[db_runner] Case {row['case_ref']} is {row['status']} — skipping orchestration"
        )
        return None

    # Build the context dict the orchestrator expects
    ctx: dict[str, Any] = {
        # Identifiers
        "case_ref":              row["case_ref"],
        "case_type":             row["case_type"],
        "payment_id":            row["payment_id"],
        "order_id":              row["order_id"],
        "customer_id":           row["customer_id"],
        "subscription_id":       row["subscription_id"],
        "invoice_id":            row["invoice_id"],

        # Financials
        "amount_paise":          row["amount"] or 0,
        "currency":              row["currency"] or "INR",

        # Failure context
        "failure_code":          row["failure_reason"] or "BAD_REQUEST_ERROR",
        "failure_reason":        row["failure_reason"],
        "payment_method":        row["payment_method"] or "card",
        "failure_category":      row["failure_category"] or "",

        # Lifecycle
        "status":                row["status"],
        "attempt_count":         row["attempt_count"] or 0,

        # Customer context — defaults (real data would come from CRM enrichment)
        "customer_segment":             "regular",
        "previous_successful_payments": 3,
        "previous_failed_payments":     row["attempt_count"] or 0,
        "hours_since_failure":          1.0,   # recency unknown without event timestamp
        "customer_email":               "customer@example.com",

        # Discount eligibility
        "discount_pct_offered":   0.07,
        "contact_limit_reached":  False,

        # Subscription / mandate
        "is_subscription":        row["case_type"] == "SUBSCRIPTION_FAILURE",
        "mandate_id":             row["mandate_id"],
        "mandate_status":         row["mandate_status"] or "ACTIVE",
        "notification_sent":      row["notification_sent_at"] is not None,
        "retry_budget_remaining": row["retry_budget_remaining"] if row["retry_budget_remaining"] is not None else 3,
    }

    return ctx


# ── Persist orchestrator decision ─────────────────────────────────────────────

async def persist_orchestrator_decision(
    pg: asyncpg.Pool,
    case_id: int,
    result: dict[str, Any],
) -> None:
    """
    Write the orchestrator's decision back to:
      - recovery_cases  (status, recommended_action, economic fields, reasoning)
      - recovery_attempts  (append-only record of this decision)
    """
    final_action  = result["final_action"]
    final_status  = result["final_status"]
    llm_reasoning = result.get("llm_reasoning", "")
    failure_cat   = result.get("failure_category", "")
    context_sum   = result.get("context_summary", "")

    # Find economic snapshot for the chosen action
    snap = next(
        (s for s in result.get("scored_actions", []) if s["action"] == final_action),
        None,
    )
    p0           = snap["p0"]  if snap else None
    pa           = snap["pa"]  if snap else None
    uplift       = snap["uplift"] if snap else None
    expected_net = snap["expected_net_recovery_paise"] if snap else None
    cost         = snap["cost_paise"] if snap else None

    # Get current attempt count for the new attempt number
    async with pg.acquire() as conn:
        current = await conn.fetchval(
            "SELECT attempt_count FROM recovery_cases WHERE id = $1", case_id
        )
        new_attempt_number = (current or 0) + 1

        # Find policy decision for the chosen action
        permitted_names = {a["action"] for a in result.get("permitted_actions", [])}
        policy_decision = "APPROVED" if final_action in permitted_names else "BLOCKED"
        policy_reason   = None
        if policy_decision == "BLOCKED":
            blocked = next(
                (b for b in result.get("blocked_actions", []) if b["action"] == final_action),
                None,
            )
            if blocked:
                policy_reason = blocked.get("policy", {}).get("reason")

        # Build orchestrator trace for audit
        trace = {
            "failure_category":  failure_cat,
            "context_summary":   context_sum,
            "llm_reasoning":     llm_reasoning,
            "validation_passed": result.get("validation_passed", False),
            "permitted_actions": [a["action"] for a in result.get("permitted_actions", [])],
            "blocked_actions":   [
                {"action": b["action"], "reason": b.get("policy", {}).get("reason")}
                for b in result.get("blocked_actions", [])
            ],
        }

        # ── Update recovery_cases ─────────────────────────────────────────────
        await conn.execute(
            """
            UPDATE recovery_cases SET
                status               = $1,
                recommended_action   = $2,
                failure_category     = $3,
                p0                   = $4,
                recommended_action_pa = $5,
                expected_uplift      = $6,
                expected_net_recovery = $7,
                attempt_count        = $8,
                updated_at           = now()
            WHERE id = $9
            """,
            final_status,
            final_action,
            failure_cat or None,
            p0,
            pa,
            uplift,
            expected_net,
            new_attempt_number,
            case_id,
        )

        # ── Insert recovery_attempts ──────────────────────────────────────────
        await conn.execute(
            """
            INSERT INTO recovery_attempts (
                case_id, attempt_number, action,
                p0_at_decision, pa_at_decision,
                expected_net_recovery_at_decision, intervention_cost,
                policy_decision, policy_reason,
                execution_status,
                orchestrator_trace, attempted_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PENDING',$10, now())
            """,
            case_id,
            new_attempt_number,
            final_action,
            p0,
            pa,
            expected_net,
            cost,
            policy_decision,
            policy_reason,
            json.dumps(trace),
        )

    logger.info(
        f"[db_runner] Persisted | case_id={case_id}  "
        f"action={final_action}  status={final_status}  "
        f"attempt=#{new_attempt_number}"
    )


# ── Main entry point called by the worker ────────────────────────────────────

async def run_orchestrator_for_case(pg: asyncpg.Pool, case_id: int) -> str:
    """
    Load case → run orchestrator → persist decision.
    Returns the final case status string.
    """
    # 1. Load context
    ctx = await load_case_context(pg, case_id)
    if ctx is None:
        return "SKIPPED"

    logger.info(
        f"[db_runner] Orchestrating case_id={case_id}  "
        f"ref={ctx['case_ref']}  amount=₹{ctx['amount_paise']//100}"
    )

    # 2. Run the LangGraph graph (it's synchronous — run in thread pool)
    from app.orchestrator.graph import run_case

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_case, ctx, case_id)

    # 3. Persist decision
    await persist_orchestrator_decision(pg, case_id, result)

    # 4. Langfuse trace
    try:
        from app.observability import trace_orchestrator_run
        trace_orchestrator_run(ctx["case_ref"], ctx, result)
    except Exception as e:
        logger.warning(f"[db_runner] Langfuse trace failed: {e}")

    return result["final_status"]
