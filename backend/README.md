# RecoverAI — Backend

Revenue Recovery Control Plane for Razorpay AI Buildathon 2026 · Track 03.

## Quick start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Expose via ngrok (for Razorpay webhook registration)

```bash
ngrok http 8000
```

Copy the `https://YOUR-NGROK-ID.ngrok-free.app` URL and:
1. Paste it into `.env` as `PUBLIC_BASE_URL`.
2. Register `https://YOUR-NGROK-ID.ngrok-free.app/webhook/razorpay` in the
   Razorpay dashboard → Settings → Webhooks.
3. Copy the webhook secret shown by Razorpay into `.env` as `RAZORPAY_WEBHOOK_SECRET`.

## Endpoints

| Method | Path                  | Description                        |
|--------|-----------------------|------------------------------------|
| GET    | `/health`             | Liveness check                     |
| POST   | `/webhook/razorpay`   | Razorpay webhook receiver          |
| GET    | `/docs`               | Auto-generated Swagger UI          |

## Webhook events handled

| Event                   | Action (stub)                        |
|-------------------------|--------------------------------------|
| `payment.failed`        | Create / update RecoveryCase         |
| `payment.captured`      | Mark RecoveryCase RECOVERED          |
| `order.paid`            | Mark RecoveryCase RECOVERED          |
| `subscription.halted`   | Create subscription RecoveryCase     |
| `subscription.charged`  | Mark subscription case RECOVERED     |
| `invoice.paid`          | Mark receivables case RECOVERED      |
| `payment_link.paid`     | Mark RecoveryCase RECOVERED          |

## Next steps

- Wire PostgreSQL (models + migrations via Alembic)
- Wire Redis (event bus + worker queues)
- Implement LangGraph recovery orchestrator
- Implement MCP execution boundary
