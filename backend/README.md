# RecoverAI — Backend

The FastAPI gateway, async worker, LangGraph orchestrator, MCP execution boundary, and all recovery logic live here.

For the full system description, architecture, and benchmark results, see the [root README](../README.md).

---

## Quick Start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your credentials, then:

```bash
# Apply DB migrations
alembic upgrade head

# API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Worker (separate terminal)
python -m app.worker

# Scheduler — polls for due scheduled actions every 30s (separate terminal)
python -m app.scheduler
```

## Expose via ngrok (for Razorpay webhook delivery)

```bash
ngrok http 8000
```

1. Copy the ngrok URL into `.env` as `PUBLIC_BASE_URL`
2. Register `https://YOUR-NGROK-ID.ngrok-free.app/webhooks/razorpay` in Razorpay dashboard → Settings → Webhooks
3. Copy the webhook secret into `.env` as `RAZORPAY_WEBHOOK_SECRET`

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check |
| POST | `/webhooks/razorpay` | Razorpay webhook receiver (HMAC verified) |
| GET | `/docs` | Swagger UI |
| POST | `/test/payment-failed` | Simulate a payment failure |
| POST | `/test/subscription-halted` | Simulate a halted subscription |
| POST | `/test/invoice-overdue` | Simulate an overdue invoice |
| POST | `/storefront/create-order` | Create order from demo storefront |
| POST | `/storefront/abandon` | Simulate checkout abandonment |
| GET | `/dashboard/summary` | Overview metrics |
| GET | `/dashboard/cases` | Paginated case list |

## Running the Benchmark

```bash
# Generate synthetic data
python -m app.synthetic.generator --count 500 --seed 42 --split 0.8

# Evaluate RecoverAI against baselines
python test_matrix.py

# Side-by-side comparison
python -m app.baseline.compare \
  --dataset data/synthetic/eval_seed42_n100.jsonl \
  --recoverai data/recoverai/eval_report_seed42.json
```
