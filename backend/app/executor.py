"""
RecoverAI Execution Loop
=========================
Picks up POLICY_APPROVED cases from the execution queue,
runs the MCP executor (freshness check → Razorpay API),
and handles the outcome.

Pipeline:
  Redis  recoverai:execution-queue
    ↓ BLPOP {case_id, event_id}
    ↓ Load case context from PostgreSQL
    ↓ MCP executor:
        ├── Structural validation
        ├── Freshness check  (re-verify case state)
        └── Razorpay API call
    ↓ Update recovery_attempts (execution_status, razorpay_reference)
    ↓ Update recovery_cases    (status → EXECUTED | STOPPED | ESCALATED)
    ↓ Wait for outcome webhook
       (outcome confirmed via webhook handler in main.py)

Run with:
    .venv\Scripts\python -m app.executor
"""
import asyncio
import json
import logging
import os

import asyncpg
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("recoverai.executor")

EXECUTION_QUEUE = "recoverai:execution-queue"


# ── Main execution loop ───────────────────────────────────────────────────────

async def run_executor() -> None:
    db_url    = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    logger.info("Execution loop starting up …")

    pg = await asyncpg.create_pool(
        dsn=db_url, min_size=1, max_size=5, command_timeout=30
    )
    redis = aioredis.from_url(
        redis_url, encoding="utf-8", decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5,
    )

    logger.info(f"Listening on queue: {EXECUTION_QUEUE}")

    while True:
        try:
            result = await redis.blpop(EXECUTION_QUEUE, timeout=5)
            if result is None:
                continue

            _, raw_msg = result
            msg        = json.loads(raw_msg)
            case_id    = msg["case_id"]
            event_id   = msg.get("event_id")

            logger.info(f"Dequeued for execution | case_id={case_id}")
            await execute_case(pg, case_id)

        except (asyncio.CancelledError, KeyboardInterrupt):
            break
        except Exception as exc:
            logger.exception(f"Execution loop error: {exc}")
            await asyncio.sleep(2)

    await pg.close()
    await redis.aclose()
    logger.info("Execution loop shut down")


# ── Per-case executor ─────────────────────────────────────────────────────────

async def execute_case(pg: asyncpg.Pool, case_id: int) -> None:
    """
    Load the POLICY_APPROVED case, build context, run MCP executor.
    """
    # ── 1. Load case + latest attempt from DB ─────────────────────────────────
    async with pg.acquire() as conn:
        case = await conn.fetchrow(
            """
            SELECT id, case_ref, case_type, status,
                   payment_id, order_id, customer_id,
                   subscription_id, invoice_id,
                   amount, currency,
                   failure_reason, payment_method,
                   recommended_action, attempt_count,
                   mandate_status, retry_budget_remaining,
                   notification_sent_at
            FROM recovery_cases
            WHERE id = $1
            """,
            case_id,
        )
        attempt = await conn.fetchrow(
            """
            SELECT id, action, policy_decision
            FROM recovery_attempts
            WHERE case_id = $1
            ORDER BY id DESC LIMIT 1
            """,
            case_id,
        )

    if not case:
        logger.warning(f"[executor] Case id={case_id} not found")
        return

    if case["status"] != "POLICY_APPROVED":
        logger.info(
            f"[executor] Case {case['case_ref']} is {case['status']} "
            f"— not POLICY_APPROVED, skipping"
        )
        return

    action = (attempt["action"] if attempt else case["recommended_action"])
    if not action:
        logger.warning(f"[executor] No action found for case_id={case_id}")
        return

    attempt_id = attempt["id"] if attempt else None

    logger.info(
        f"[executor] Executing | case_ref={case['case_ref']}  "
        f"action={action}  amount=₹{(case['amount'] or 0) // 100}"
    )

    # ── 2. Build context dict for MCP executor ────────────────────────────────
    ctx = {
        "case_ref":              case["case_ref"],
        "case_type":             case["case_type"],
        "payment_id":            case["payment_id"],
        "order_id":              case["order_id"],
        "customer_id":           case["customer_id"],
        "subscription_id":       case["subscription_id"],
        "invoice_id":            case["invoice_id"],
        "amount_paise":          case["amount"] or 0,
        "currency":              case["currency"] or "INR",
        "failure_code":          case["failure_reason"] or "BAD_REQUEST_ERROR",
        "payment_method":        case["payment_method"] or "card",
        "mandate_status":        case["mandate_status"] or "ACTIVE",
        "retry_budget_remaining": case["retry_budget_remaining"] if case["retry_budget_remaining"] is not None else 3,
        "notification_sent":     case["notification_sent_at"] is not None,
        # Contact info — in production comes from CRM; default for test mode
        "customer_email":        "customer@recoverai.test",
        "customer_phone":        "",
        "discount_pct_offered":  0.07,
    }

    # ── 3. Run MCP executor ───────────────────────────────────────────────────
    from app.mcp.executor import execute_action

    result = await execute_action(
        pg         = pg,
        case_id    = case_id,
        action     = action,
        case_ctx   = ctx,
        attempt_id = attempt_id,
    )

    # ── 4. Log outcome ────────────────────────────────────────────────────────
    if result.success:
        logger.info(
            f"[executor] ✔ Executed | case_ref={case['case_ref']}  "
            f"action={action}  ref={result.razorpay_reference}  "
            f"status={result.execution_status}"
        )
    else:
        logger.warning(
            f"[executor] ✘ Failed | case_ref={case['case_ref']}  "
            f"action={action}  reason={result.message}  "
            f"freshness_passed={result.freshness_passed}"
        )


# ── Outcome handler (called from webhook) ────────────────────────────────────

async def handle_payment_outcome(
    pg: asyncpg.Pool,
    payment_id: str,
    order_id: str | None,
    outcome: str,                  # "PAID" | "FAILED" | "EXPIRED"
) -> None:
    """
    Called from the FastAPI webhook handler when a payment outcome arrives.
    Marks matching EXECUTED cases as RECOVERED or RE_EVALUATE.
    """
    async with pg.acquire() as conn:
        # Find the EXECUTED case
        case = await conn.fetchrow(
            """
            SELECT id, case_ref, attempt_count
            FROM recovery_cases
            WHERE status = 'EXECUTED'
              AND (
                    ($1::text IS NOT NULL AND payment_id = $1) OR
                    ($2::text IS NOT NULL AND order_id   = $2)
                  )
            LIMIT 1
            """,
            payment_id,
            order_id,
        )

        if not case:
            return

        await _resolve_case(pg, case["id"], case["case_ref"], outcome)


async def handle_payment_link_outcome(
    pg: asyncpg.Pool,
    payment_link_id: str,
    outcome: str,
) -> None:
    """
    Called when payment_link.paid arrives.
    Finds the case via razorpay_reference in recovery_attempts.
    """
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT rc.id, rc.case_ref
            FROM recovery_attempts ra
            JOIN recovery_cases rc ON rc.id = ra.case_id
            WHERE ra.razorpay_reference = $1
              AND rc.status = 'EXECUTED'
            LIMIT 1
            """,
            payment_link_id,
        )

    if not row:
        logger.info(f"[executor] No EXECUTED case found for plink {payment_link_id}")
        return

    await _resolve_case(pg, row["id"], row["case_ref"], outcome)


async def _resolve_case(pg: asyncpg.Pool, case_id: int, case_ref: str, outcome: str) -> None:
    """Mark a case RECOVERED or RE_EVALUATE based on outcome."""
    import os
    import redis as sync_redis

    async with pg.acquire() as conn:
        if outcome == "PAID":
            new_status = "RECOVERED"
            logger.info(f"[executor] Case {case_ref} → RECOVERED")
        else:
            new_status = "RE_EVALUATE"
            logger.info(f"[executor] Case {case_ref} → RE_EVALUATE (outcome={outcome})")

        await conn.execute(
            """
            UPDATE recovery_cases SET
                status      = $1::varchar,
                resolved_at = CASE WHEN $1 = 'RECOVERED' THEN now() ELSE NULL END,
                updated_at  = now()
            WHERE id = $2
            """,
            new_status, case_id,
        )

        await conn.execute(
            """
            UPDATE recovery_attempts SET
                outcome              = $1::varchar,
                outcome_confirmed_at = now()
            WHERE case_id = $2
              AND id = (SELECT id FROM recovery_attempts
                        WHERE case_id = $2 ORDER BY id DESC LIMIT 1)
            """,
            outcome, case_id,
        )

    # If RE_EVALUATE, push to re-evaluate queue for worker to pick up
    if new_status == "RE_EVALUATE":
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                encoding="utf-8", decode_responses=True,
            )
            import json
            await r.rpush("recoverai:re-evaluate", json.dumps({"case_id": case_id}))
            await r.aclose()
            logger.info(f"[executor] Case {case_ref} pushed to re-evaluate queue")
        except Exception as e:
            logger.warning(f"[executor] Could not push to re-evaluate queue: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(run_executor())
