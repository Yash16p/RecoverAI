import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
import uvicorn

from app.config import settings
from app.razorpay_client import verify_webhook_signature
from app.database.connection import init_db, close_db, get_redis
import app.database.connection as _db_conn
from app.api.dashboard import router as dashboard_router

from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("recoverai")

RECOVERY_QUEUE = "recoverai:recovery-events"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RecoverAI backend starting up")
    logger.info(f"Environment : {settings.APP_ENV}")
    logger.info(f"Public URL  : {settings.PUBLIC_BASE_URL}")
    await init_db()
    yield
    logger.info("RecoverAI backend shutting down")
    await close_db()


app = FastAPI(
    title="RecoverAI - Revenue Recovery Control Plane",
    description="Event-driven AI system that detects revenue leakage and executes bounded recovery actions.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(dashboard_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Storefront endpoints ---------

from pydantic import BaseModel
from typing import Optional

class OrderRequest(BaseModel):
    amount: int
    product_name: str = "RecoverAI Product"

class AbandonRequest(BaseModel):
    checkout_id: str
    amount: int
    product_name: str
    customer_email: str = "demo@recoverai.test"
    customer_phone: str = "9999999999"

@app.post("/storefront/create-order", tags=["Storefront"])
async def create_storefront_order(req: OrderRequest):
    from app.razorpay_client import create_order
    order = create_order(
        amount_paise=req.amount,
        currency="INR",
        receipt=f"store_{uuid.uuid4().hex[:8]}",
    )
    return order

@app.post("/storefront/abandon", tags=["Storefront"])
async def storefront_abandon_checkout(req: AbandonRequest):
    """
    Simulate a checkout abandonment event.
    """
    event_id = f"evt_abandon_{uuid.uuid4().hex[:12]}"
    payload = {
        "id": event_id,
        "event": "checkout.abandoned",
        "payload": {
            "checkout": {
                "entity": {
                    "id": req.checkout_id,
                    "amount": req.amount,
                    "currency": "INR",
                    "customer_email": req.customer_email,
                    "customer_phone": req.customer_phone,
                    "product_name": req.product_name,
                }
            }
        }
    }

    async with _db_conn._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhook_events (razorpay_event_id, event_type, payload, status)
            VALUES ($1, $2, $3, 'RECEIVED')
            ON CONFLICT (razorpay_event_id) DO UPDATE SET status = 'DUPLICATE'
            RETURNING id, status, (xmax = 0) AS is_new
            """,
            event_id, "checkout.abandoned", json.dumps(payload),
        )

    db_id = row["id"]
    is_new = row["is_new"]

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": db_id}

    queue_msg = json.dumps({"event_id": db_id, "event_type": "checkout.abandoned"})
    redis = get_redis()
    await redis.rpush(RECOVERY_QUEUE, queue_msg)

    async with _db_conn._pg_pool.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='QUEUED' WHERE id=$1", db_id)

    logger.info(f"Checkout abandoned | checkout_id={req.checkout_id} amount={req.amount} queued")

    return {"received": True, "event_id": db_id, "queued": True, "checkout_id": req.checkout_id}


# --------- Test/Synthetic Event Endpoints ---------

class PaymentFailedRequest(BaseModel):
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    amount: int = 100000
    error_code: str = "BAD_REQUEST_ERROR"
    error_description: str = "Card declined"
    method: str = "card"
    customer_id: str = "cust_test123"

class SubscriptionHaltedRequest(BaseModel):
    subscription_id: Optional[str] = None
    plan_id: str = "plan_monthly"
    customer_id: str = "cust_sub123"
    amount: int = 99900
    reason: str = "insufficient_funds"

class InvoiceOverdueRequest(BaseModel):
    invoice_id: Optional[str] = None
    customer_id: str = "cust_inv123"
    amount: int = 150000
    description: str = "Monthly Invoice"
    due_date: Optional[str] = None


@app.post("/test/payment-failed", tags=["Testing"])
async def test_payment_failed(req: PaymentFailedRequest):
    """
    Simulate a payment.failed event for testing.
    Creates a recovery case for PAYMENT_FAILURE.
    """
    payment_id = req.payment_id or f"pay_test_{uuid.uuid4().hex[:12]}"
    order_id = req.order_id or f"order_test_{uuid.uuid4().hex[:8]}"
    event_id = f"evt_payfail_{uuid.uuid4().hex[:12]}"
    
    payload = {
        "id": event_id,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": req.amount,
                    "currency": "INR",
                    "status": "failed",
                    "error_code": req.error_code,
                    "error_description": req.error_description,
                    "method": req.method,
                    "notes": {"customer_id": req.customer_id}
                }
            }
        }
    }

    async with _db_conn._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhook_events (razorpay_event_id, event_type, payload, status)
            VALUES ($1, $2, $3, 'RECEIVED')
            ON CONFLICT (razorpay_event_id) DO UPDATE SET status = 'DUPLICATE'
            RETURNING id, status, (xmax = 0) AS is_new
            """,
            event_id, "payment.failed", json.dumps(payload),
        )

    db_id = row["id"]
    is_new = row["is_new"]

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": db_id}

    queue_msg = json.dumps({"event_id": db_id, "event_type": "payment.failed"})
    redis = get_redis()
    await redis.rpush(RECOVERY_QUEUE, queue_msg)

    async with _db_conn._pg_pool.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='QUEUED' WHERE id=$1", db_id)

    logger.info(f"Test payment.failed | payment_id={payment_id} amount={req.amount} queued")

    return {"received": True, "event_id": db_id, "queued": True, "payment_id": payment_id, "order_id": order_id}


@app.post("/test/subscription-halted", tags=["Testing"])
async def test_subscription_halted(req: SubscriptionHaltedRequest):
    """
    Simulate a subscription.halted event for testing.
    Creates a recovery case for SUBSCRIPTION_FAILURE.
    """
    subscription_id = req.subscription_id or f"sub_test_{uuid.uuid4().hex[:12]}"
    event_id = f"evt_subhalt_{uuid.uuid4().hex[:12]}"
    
    payload = {
        "id": event_id,
        "event": "subscription.halted",
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription_id,
                    "plan_id": req.plan_id,
                    "customer_id": req.customer_id,
                    "status": "halted",
                    "current_start": 1693526400,
                    "current_end": 1696118400,
                    "charge_at": 1696118400,
                    "total_count": 12,
                    "paid_count": 3,
                    "notes": {"reason": req.reason, "amount": req.amount}
                }
            }
        }
    }

    async with _db_conn._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhook_events (razorpay_event_id, event_type, payload, status)
            VALUES ($1, $2, $3, 'RECEIVED')
            ON CONFLICT (razorpay_event_id) DO UPDATE SET status = 'DUPLICATE'
            RETURNING id, status, (xmax = 0) AS is_new
            """,
            event_id, "subscription.halted", json.dumps(payload),
        )

    db_id = row["id"]
    is_new = row["is_new"]

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": db_id}

    queue_msg = json.dumps({"event_id": db_id, "event_type": "subscription.halted"})
    redis = get_redis()
    await redis.rpush(RECOVERY_QUEUE, queue_msg)

    async with _db_conn._pg_pool.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='QUEUED' WHERE id=$1", db_id)

    logger.info(f"Test subscription.halted | subscription_id={subscription_id} amount={req.amount} queued")

    return {"received": True, "event_id": db_id, "queued": True, "subscription_id": subscription_id}


@app.post("/test/invoice-overdue", tags=["Testing"])
async def test_invoice_overdue(req: InvoiceOverdueRequest):
    """
    Simulate an invoice.overdue event for testing receivables recovery.
    Creates a recovery case for RECEIVABLES_OVERDUE.
    """
    invoice_id = req.invoice_id or f"inv_test_{uuid.uuid4().hex[:12]}"
    event_id = f"evt_invodue_{uuid.uuid4().hex[:12]}"
    
    payload = {
        "id": event_id,
        "event": "invoice.overdue",
        "payload": {
            "invoice": {
                "entity": {
                    "id": invoice_id,
                    "customer_id": req.customer_id,
                    "amount": req.amount,
                    "currency": "INR",
                    "status": "issued",
                    "due_by": req.due_date or 1693526400,
                    "description": req.description,
                    "notes": {}
                }
            }
        }
    }

    async with _db_conn._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhook_events (razorpay_event_id, event_type, payload, status)
            VALUES ($1, $2, $3, 'RECEIVED')
            ON CONFLICT (razorpay_event_id) DO UPDATE SET status = 'DUPLICATE'
            RETURNING id, status, (xmax = 0) AS is_new
            """,
            event_id, "invoice.overdue", json.dumps(payload),
        )

    db_id = row["id"]
    is_new = row["is_new"]

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": db_id}

    queue_msg = json.dumps({"event_id": db_id, "event_type": "invoice.overdue"})
    redis = get_redis()
    await redis.rpush(RECOVERY_QUEUE, queue_msg)

    async with _db_conn._pg_pool.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='QUEUED' WHERE id=$1", db_id)

    logger.info(f"Test invoice.overdue | invoice_id={invoice_id} amount={req.amount} queued")

    return {"received": True, "event_id": db_id, "queued": True, "invoice_id": invoice_id}


# --------- Health ---------

@app.get("/health", tags=["Meta"])
async def health():
    return {"status": "ok", "env": settings.APP_ENV}


# --------- Webhook receiver ---------

@app.post("/webhooks/razorpay", tags=["Webhooks"])
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
):
    raw_body = await request.body()

    if not x_razorpay_signature:
        logger.warning("Webhook missing X-Razorpay-Signature header")
        raise HTTPException(status_code=400, detail="Missing signature header")

    if not verify_webhook_signature(raw_body, x_razorpay_signature):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = payload.get("event", "unknown")
    razorpay_event_id: str = payload.get("id") or x_razorpay_signature

    logger.info(f"Webhook received | event={event_type} razorpay_event_id={razorpay_event_id[:20]}...")

    async with _db_conn._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhook_events (razorpay_event_id, event_type, payload, status)
            VALUES ($1, $2, $3, 'RECEIVED')
            ON CONFLICT (razorpay_event_id) DO UPDATE SET status = 'DUPLICATE'
            RETURNING id, status, (xmax = 0) AS is_new
            """,
            razorpay_event_id, event_type, json.dumps(payload),
        )

    db_id = row["id"]
    is_new = row["is_new"]

    if not is_new:
        logger.info(f"Duplicate webhook ignored | db_id={db_id}")
        return JSONResponse(status_code=200, content={"received": True, "duplicate": True, "event_id": db_id})

    logger.info(f"WebhookEvent persisted | db_id={db_id} event={event_type}")

    OUTCOME_EVENTS = {"payment.captured", "order.paid", "payment_link.paid", "subscription.charged", "invoice.paid"}
    if event_type in OUTCOME_EVENTS:
        await _route_outcome(event_type, payload)
        async with _db_conn._pg_pool.acquire() as conn:
            await conn.execute("UPDATE webhook_events SET status='PROCESSED', processed_at=now() WHERE id=$1", db_id)
        logger.info(f"Resolution event handled inline | db_id={db_id} event={event_type}")
        return JSONResponse(status_code=200, content={"received": True, "event_id": db_id, "resolved": True})

    queue_msg = json.dumps({"event_id": db_id, "event_type": event_type})
    redis = get_redis()
    await redis.rpush(RECOVERY_QUEUE, queue_msg)
    logger.info(f"Event queued | db_id={db_id} queue={RECOVERY_QUEUE}")

    async with _db_conn._pg_pool.acquire() as conn:
        await conn.execute("UPDATE webhook_events SET status='QUEUED' WHERE id=$1", db_id)

    return JSONResponse(status_code=200, content={"received": True, "event_id": db_id, "queued": True})


async def _route_outcome(event_type: str, payload: dict) -> None:
    from app.executor import handle_payment_outcome, handle_payment_link_outcome

    try:
        if event_type == "payment.captured":
            entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            await handle_payment_outcome(_db_conn._pg_pool, payment_id=entity.get("id"), order_id=entity.get("order_id"), outcome="PAID")
        elif event_type == "order.paid":
            entity = payload.get("payload", {}).get("order", {}).get("entity", {})
            await handle_payment_outcome(_db_conn._pg_pool, payment_id=None, order_id=entity.get("id"), outcome="PAID")
        elif event_type == "payment_link.paid":
            entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            await handle_payment_link_outcome(_db_conn._pg_pool, payment_link_id=entity.get("id"), outcome="PAID")
        elif event_type == "payment.failed":
            entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            await handle_payment_outcome(_db_conn._pg_pool, payment_id=entity.get("id"), order_id=entity.get("order_id"), outcome="FAILED")
    except Exception as e:
        logger.warning(f"Outcome routing error for {event_type}: {e}")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
