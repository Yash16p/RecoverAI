"""
Recovery Worker (Phase 2 - full pipeline)
"""
import asyncio
import json
import logging
import uuid
import os

import asyncpg
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("recoverai.worker")

RECOVERY_QUEUE  = "recoverai:recovery-events"
EXECUTION_QUEUE = "recoverai:execution-queue"
REEVAL_QUEUE    = "recoverai:re-evaluate"

TERMINAL = {"RECOVERED", "STOPPED", "ESCALATED"}


async def run_worker() -> None:
    db_url = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    logger.info("Worker starting up...")

    pg = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=5, command_timeout=30)
    redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True, socket_timeout=10, socket_connect_timeout=5)

    logger.info(f"Listening on queue: {RECOVERY_QUEUE}")

    while True:
        try:
            result = await redis.blpop([RECOVERY_QUEUE, REEVAL_QUEUE], timeout=5)
            if result is None:
                continue

            queue_name, raw_msg = result
            msg = json.loads(raw_msg)

            if queue_name == REEVAL_QUEUE:
                case_id = msg["case_id"]
                logger.info(f"RE_EVALUATE | case_id={case_id}")
                final_status = await _run_orchestrator(pg, case_id)
                logger.info(f"RE_EVALUATE done | case_id={case_id} status={final_status}")
                if final_status == "POLICY_APPROVED":
                    exec_msg = json.dumps({"case_id": case_id, "event_id": None})
                    await redis.rpush(EXECUTION_QUEUE, exec_msg)
                continue

            event_id = msg["event_id"]
            event_type = msg["event_type"]

            logger.info(f"Dequeued | event_id={event_id} event_type={event_type}")
            await process_event(pg, redis, event_id, event_type)

        except (asyncio.CancelledError, KeyboardInterrupt):
            break
        except Exception as exc:
            logger.exception(f"Worker error: {exc}")
            await asyncio.sleep(2)

    await pg.close()
    await redis.aclose()
    logger.info("Worker shut down")


async def process_event(pg: asyncpg.Pool, redis: aioredis.Redis, event_id: int, event_type: str) -> None:
    async with pg.acquire() as conn:
        webhook = await conn.fetchrow(
            "SELECT id, razorpay_event_id, event_type, payload, status FROM webhook_events WHERE id = $1",
            event_id,
        )

    if not webhook:
        logger.warning(f"WebhookEvent id={event_id} not found - skipping")
        return
    if webhook["status"] == "PROCESSED":
        logger.info(f"WebhookEvent id={event_id} already PROCESSED")
        return

    payload = webhook["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    case_id = await _dispatch_event(pg, webhook, payload, event_type)

    if case_id:
        final_status = await _run_orchestrator(pg, case_id)
        logger.info(f"Orchestrator done | case_id={case_id} status={final_status}")

        if final_status == "POLICY_APPROVED":
            exec_msg = json.dumps({"case_id": case_id, "event_id": event_id})
            await redis.rpush(EXECUTION_QUEUE, exec_msg)
            logger.info(f"Case {case_id} pushed to execution queue")

    async with pg.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='PROCESSED', processed_at=now() WHERE id=$1", event_id)
    logger.info(f"WebhookEvent id={event_id} -> PROCESSED")


async def _dispatch_event(pg: asyncpg.Pool, webhook, payload: dict, event_type: str) -> int | None:
    dispatchers = {
        "payment.failed":       _handle_payment_failed,
        "subscription.halted":  _handle_subscription_halted,
        "invoice.overdue":      _handle_invoice_overdue,
        "checkout.abandoned":   _handle_checkout_abandoned,
    }
    handler = dispatchers.get(event_type)
    if handler:
        return await handler(pg, webhook, payload)
    logger.info(f"No handler for event_type={event_type}")
    return None


async def _run_orchestrator(pg: asyncpg.Pool, case_id: int) -> str:
    try:
        from app.orchestrator.db_runner import run_orchestrator_for_case
        return await run_orchestrator_for_case(pg, case_id)
    except Exception as e:
        logger.exception(f"Orchestrator failed for case_id={case_id}: {e}")
        return "ERROR"


# --------- Event handlers ---------

async def _handle_payment_failed(pg: asyncpg.Pool, webhook, payload: dict) -> int | None:
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = entity.get("id")
    order_id = entity.get("order_id")
    customer_id = entity.get("customer_id")
    amount = entity.get("amount")
    failure_reason = entity.get("error_code") or entity.get("error_description")
    payment_method = entity.get("method")

    return await _create_recovery_case(
        pg, case_type="PAYMENT_FAILURE", payment_id=payment_id, order_id=order_id,
        customer_id=customer_id, amount=amount, failure_reason=failure_reason,
        payment_method=payment_method, source_event_id=webhook["id"],
    )


async def _handle_subscription_halted(pg: asyncpg.Pool, webhook, payload: dict) -> int | None:
    entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    subscription_id = entity.get("id")
    customer_id = entity.get("customer_id")
    plan = entity.get("plan_id")
    failure_reason = entity.get("status") or "SUBSCRIPTION_HALTED"
    amount = entity.get("total_count", 1) * entity.get("quantity", 1) * 100  # estimate

    return await _create_recovery_case(
        pg, case_type="SUBSCRIPTION_FAILURE", subscription_id=subscription_id,
        customer_id=customer_id, failure_reason=failure_reason, amount=amount,
        source_event_id=webhook["id"],
    )


async def _handle_invoice_overdue(pg: asyncpg.Pool, webhook, payload: dict) -> int | None:
    entity = payload.get("payload", {}).get("invoice", {}).get("entity", {})
    invoice_id = entity.get("id")
    customer_id = entity.get("customer_id")
    amount = entity.get("amount_due") or entity.get("amount")

    return await _create_recovery_case(
        pg, case_type="OVERDUE_RECEIVABLE", invoice_id=invoice_id,
        customer_id=customer_id, amount=amount, failure_reason="OVERDUE",
        source_event_id=webhook["id"],
    )


async def _handle_checkout_abandoned(pg: asyncpg.Pool, webhook, payload: dict) -> int | None:
    entity = payload.get("payload", {}).get("checkout", {}).get("entity", {})
    checkout_id = entity.get("id")
    amount = entity.get("amount")
    customer_email = entity.get("customer_email")
    customer_phone = entity.get("customer_phone")
    product_name = entity.get("product_name", "Unknown Product")

    return await _create_recovery_case(
        pg, case_type="CHECKOUT_ABANDONMENT", checkout_id=checkout_id,
        amount=amount, failure_reason="CHECKOUT_ABANDONED",
        customer_email=customer_email, customer_phone=customer_phone,
        source_event_id=webhook["id"],
    )


async def _create_recovery_case(
    pg: asyncpg.Pool,
    case_type: str,
    source_event_id: int,
    payment_id: str | None = None,
    order_id: str | None = None,
    customer_id: str | None = None,
    subscription_id: str | None = None,
    invoice_id: str | None = None,
    checkout_id: str | None = None,
    amount: int | None = None,
    failure_reason: str | None = None,
    payment_method: str | None = None,
    customer_email: str | None = None,
    customer_phone: str | None = None,
) -> int | None:
    async with pg.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id, case_ref, status FROM recovery_cases
            WHERE case_type = $1 AND status != ALL($7::text[])
              AND (
                ($2::text IS NOT NULL AND payment_id = $2) OR
                ($3::text IS NOT NULL AND order_id = $3) OR
                ($4::text IS NOT NULL AND subscription_id = $4) OR
                ($5::text IS NOT NULL AND invoice_id = $5) OR
                ($6::text IS NOT NULL AND checkout_id = $6)
              )
            LIMIT 1
            """,
            case_type, payment_id, order_id, subscription_id, invoice_id, checkout_id, list(TERMINAL),
        )

        if existing:
            logger.info(f"Open case exists | ref={existing['case_ref']} status={existing['status']} - skipping")
            return existing["id"]

        case_ref = f"RC_{uuid.uuid4().hex[:8].upper()}"
        row = await conn.fetchrow(
            """
            INSERT INTO recovery_cases
                (case_ref, case_type, payment_id, order_id, customer_id,
                 subscription_id, invoice_id, checkout_id, amount, currency, status,
                 failure_reason, payment_method, customer_email, customer_phone, source_event_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'INR','CREATED',$10,$11,$12,$13,$14)
            RETURNING id, case_ref
            """,
            case_ref, case_type, payment_id, order_id, customer_id,
            subscription_id, invoice_id, checkout_id, amount,
            failure_reason, payment_method, customer_email, customer_phone, source_event_id,
        )

    logger.info(f"RecoveryCase CREATED | ref={row['case_ref']} type={case_type} amount=Rs{(amount or 0)//100}")
    return row["id"]


if __name__ == "__main__":
    asyncio.run(run_worker())
