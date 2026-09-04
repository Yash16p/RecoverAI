"""
RecoverAI Scheduler
====================
Polls PostgreSQL every 30 seconds for due scheduled_actions.
For each due action:
  1. Freshness check — is case still open? mandate still valid? already paid?
  2. If valid → push to execution queue
  3. If stale → mark CANCELLED (the "best failure demo")

Run with:
    .venv\Scripts\python -m app.scheduler

The "best failure demo" scenario:
  10:00  Orchestrator chooses WAIT_AND_RETRY → schedules for 10:30
  10:15  Customer pays manually → payment.captured → RECOVERED
  10:30  Scheduler wakes up → freshness check → case is RECOVERED → CANCELLED
  Result: No duplicate charge. System never assumed future state.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("recoverai.scheduler")

EXECUTION_QUEUE = "recoverai:execution-queue"
POLL_INTERVAL   = 30   # seconds


async def run_scheduler() -> None:
    db_url    = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    pg = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=3, command_timeout=30)
    redis = aioredis.from_url(
        redis_url, encoding="utf-8", decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5,
    )

    logger.info(f"Scheduler running — polling every {POLL_INTERVAL}s")

    while True:
        try:
            await _process_due_actions(pg, redis)
        except Exception as e:
            logger.exception(f"Scheduler error: {e}")
        await asyncio.sleep(POLL_INTERVAL)

    await pg.close()
    await redis.aclose()


async def _process_due_actions(pg: asyncpg.Pool, redis: aioredis.Redis) -> None:
    """Find and process all due scheduled actions."""
    async with pg.acquire() as conn:
        due = await conn.fetch(
            """
            SELECT id, case_id, action_type, idempotency_key, scheduled_for
            FROM scheduled_actions
            WHERE status = 'PENDING'
              AND scheduled_for <= now()
            ORDER BY scheduled_for ASC
            LIMIT 20
            """,
        )

    if not due:
        return

    logger.info(f"Found {len(due)} due scheduled action(s)")

    for action in due:
        await _handle_scheduled_action(pg, redis, action)


async def _handle_scheduled_action(
    pg: asyncpg.Pool,
    redis: aioredis.Redis,
    action: asyncpg.Record,
) -> None:
    action_id      = action["id"]
    case_id        = action["case_id"]
    action_type    = action["action_type"]
    idempotency_key = action["idempotency_key"]
    scheduled_for  = action["scheduled_for"]

    logger.info(
        f"[scheduler] Processing | action_id={action_id}  "
        f"case_id={case_id}  type={action_type}  due={scheduled_for}"
    )

    # ── Freshness check ───────────────────────────────────────────────────────
    async with pg.acquire() as conn:
        case = await conn.fetchrow(
            "SELECT id, case_ref, status, mandate_status FROM recovery_cases WHERE id = $1",
            case_id,
        )

    if not case:
        await _mark_action(pg, action_id, "CANCELLED", "Case no longer exists")
        return

    case_ref   = case["case_ref"]
    case_status = case["status"]

    # The key scenario: customer already paid manually
    if case_status == "RECOVERED":
        await _mark_action(pg, action_id, "CANCELLED",
                          "Case already RECOVERED — stale action cancelled")
        logger.info(
            f"[scheduler] ✔ STALE ACTION CANCELLED | case={case_ref}  "
            f"customer already paid — no duplicate action taken"
        )
        return

    if case_status in ("STOPPED", "ESCALATED"):
        await _mark_action(pg, action_id, "CANCELLED", f"Case is {case_status}")
        return

    # Mandate revoked since decision time
    if action_type in ("RETRY_PAYMENT", "WAIT_AND_RETRY"):
        if case["mandate_status"] and case["mandate_status"] != "ACTIVE":
            await _mark_action(pg, action_id, "CANCELLED",
                              f"Mandate revoked ({case['mandate_status']})")
            logger.info(f"[scheduler] Mandate revoked for case={case_ref} — cancelled")
            return

    # ── All checks passed → push to execution queue ───────────────────────────
    await _mark_action(pg, action_id, "EXECUTING", None)

    exec_msg = json.dumps({
        "case_id":        case_id,
        "scheduled_action_id": action_id,
        "idempotency_key": idempotency_key,
    })
    await redis.rpush(EXECUTION_QUEUE, exec_msg)
    logger.info(
        f"[scheduler] ✔ Pushed to execution | case={case_ref}  "
        f"action={action_type}  idempotency={idempotency_key[:16]}…"
    )


async def _mark_action(
    pg: asyncpg.Pool,
    action_id: int,
    status: str,
    reason: str | None,
) -> None:
    async with pg.acquire() as conn:
        await conn.execute(
            """
            UPDATE scheduled_actions
            SET status = $1, executed_at = now(),
                cancellation_reason = $2
            WHERE id = $3
            """,
            status, reason, action_id,
        )


# ── Schedule a future action (called by executor for WAIT_AND_RETRY) ─────────

async def schedule_action(
    pg: asyncpg.Pool,
    case_id: int,
    action_type: str,
    delay_minutes: int = 30,
) -> int:
    """
    Persist a future action to scheduled_actions.
    Returns the scheduled_action id.
    """
    idem_key      = f"sched_{case_id}_{uuid.uuid4().hex[:16]}"
    scheduled_for = f"now() + interval '{delay_minutes} minutes'"

    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO scheduled_actions
                (case_id, action_type, scheduled_for, status, idempotency_key)
            VALUES ($1, $2, {scheduled_for}, 'PENDING', $3)
            RETURNING id, scheduled_for
            """,
            case_id, action_type, idem_key,
        )

    logger.info(
        f"[scheduler] Scheduled | case_id={case_id}  "
        f"action={action_type}  at={row['scheduled_for']}  "
        f"idempotency={idem_key[:16]}…"
    )
    return row["id"]


if __name__ == "__main__":
    asyncio.run(run_scheduler())
