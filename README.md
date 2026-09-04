# RecoverAI

**An event-driven revenue recovery engine that turns payment failures into re-engagement opportunities — automatically, economically, and safely.**

---

## The Problem

Revenue leaks quietly. A card gets declined, a subscription halts, a checkout goes cold, an invoice ages past due. Left alone, most of these events just become lost revenue. The conventional response — blast a retry, send a generic email — ignores the economics entirely. A ₹500 intervention on a ₹300 recovery is not a win.

RecoverAI was built around one question: *given what we know about this customer, this failure, and this moment in time, what is the single most valuable action we can take right now?*

That is not a retrieval problem. It is an economic decision problem, and the answer is different for every case.

---

## How It Works

When a payment event arrives — a failed charge, a halted subscription, an abandoned checkout, an overdue invoice — RecoverAI does not jump to action. It reasons first.

### 1. Economic Scoring (no LLM)

Before any AI involvement, every candidate action is scored deterministically. For each action, the engine estimates:

- **P₀** — the probability the customer will pay on their own, without any intervention
- **Pₐ** — the probability of recovery if this specific action is taken
- **Uplift** — `Pₐ − P₀` (how much the action actually helps beyond natural recovery)
- **Cost** — intervention cost in paise (retries, links, discounts)
- **Expected net recovery** — `Uplift × Amount − Cost`

This produces a ranked list of economically viable actions before the LLM ever sees the case.

```
Example: ₹18,500 failed payment, INSUFFICIENT_FUNDS
─────────────────────────────────────────────────────────
Action              P₀     Pₐ    Uplift   Cost   Net
OFFER_DISCOUNT     0.35   0.73   +38pp   ₹925   ₹6,105
SEND_PAYMENT_LINK  0.35   0.55   +20pp     ₹5   ₹3,695
RETRY_PAYMENT      0.35   0.45   +10pp     ₹2   ₹1,848
```

### 2. Policy Gate (deterministic, no LLM)

A hard policy layer runs before the LLM. It enforces:

- Recovery window: 72 hours for payments, 30 days for receivables
- Retry budget: max 3 attempts per case
- Contact limits: max 2 customer contacts
- Action-specific rules: no direct retry on expired cards, mandate must be ACTIVE for subscription retries, discount percentage within merchant limits, escalation only above ₹1,000
- Economic viability: negative-net actions are blocked

This filter cannot be overridden by the LLM. The AI only ever sees the permitted subset.

### 3. LLM Analysis (Groq primary, Gemini fallback)

A single LLM call — Groq `llama-3.3-70b` by default, Gemini 2.5-flash as fallback — receives the case context, the scored permitted actions, and the blocked action list. It returns:

- Failure category (very_low / low / medium / high recoverability)
- A one-sentence context summary
- The recommended action from the permitted list
- The reasoning for that choice

If both LLMs fail, the engine falls back to a pure heuristic: the highest expected-net-recovery permitted action.

### 4. Validation

The LLM recommendation passes a final hard-code check. If it recommended an action not in the permitted list, or an unknown action entirely, the engine overrides to `NO_ACTION`. The LLM cannot escape the policy gate through its recommendation.

### 5. MCP Execution Boundary

If an action is approved, it executes through a controlled MCP layer that runs two final checks immediately before touching the Razorpay API:

**Freshness check** — the case state is re-read from the database. If the customer already paid through another channel while this decision was being made, the action is cancelled. No duplicate charges, no customer confusion.

**Structural validation** — amount bounds are enforced (max ₹50,000 for auto-retry, max ₹1,00,000 for payment links), required identifiers are verified, and an idempotency key is required for every money-moving operation.

Only after both checks pass does the Razorpay API get called.

### 6. Outcome and Re-evaluation

When Razorpay webhooks confirm the outcome, the system either closes the case as RECOVERED or queues it for re-evaluation. Re-evaluation runs the full engine again from fresh state — it never continues from a cached plan. One next action is always selected from the current moment, not from a sequence decided earlier.

---

## Architecture

```
Revenue Risk Event
    │
    ▼
FastAPI Gateway  ──HMAC verify──▶  PostgreSQL (idempotent store)
    │                                      │
    ▼                                      │
Redis Queue                                │
    │                                      │
    ▼                                      │
Async Worker ◀─────────────────────────────┘
    │
    ▼
RecoverAI Engine (LangGraph)
    │
    ├─ load_case        — hydrate from DB
    ├─ score_actions    — P₀, Pₐ, uplift, net (deterministic)
    ├─ filter_policy    — block uneconomic / rule-violating actions
    ├─ llm_analyze      — single LLM call: diagnose + recommend
    ├─ validate         — hard check: recommendation in permitted set?
    └─ persist_decision — write to DB, emit to execution queue
             │
             ▼
         MCP Boundary
             │
             ├─ Freshness check   — re-read case state
             ├─ Structural check  — amount bounds, IDs, idempotency
             └─ Execute           — Razorpay API call
                     │
                     ▼
               Razorpay Test Mode
                     │
                     ▼
               Outcome Webhook
                     │
                ┌────┴────┐
                ▼         ▼
           RECOVERED   RE_EVALUATE ──▶ Engine (fresh state)
```

### Technology Stack

| Layer | Technology |
|-------|------------|
| Gateway | FastAPI + asyncpg |
| Queue | Redis (BLPOP-based workers) |
| Persistence | PostgreSQL with Alembic migrations |
| Orchestrator | LangGraph single-graph engine |
| LLM (primary) | Groq — Llama 3.3 70B |
| LLM (fallback) | Google Gemini 2.5 Flash |
| Payments | Razorpay (Test Mode) |
| Observability | Langfuse |
| Frontend | React 19 + Tailwind CSS v4 + Vite |

### Database Schema

Four tables:

- `webhook_events` — every inbound Razorpay event, idempotent by `razorpay_event_id`
- `recovery_cases` — the central recovery object, full lifecycle from CREATED → RECOVERED / STOPPED / ESCALATED
- `recovery_attempts` — immutable append-only history of every action taken on a case
- `scheduled_actions` — future actions persisted to survive restarts, polled every 30s with freshness re-check before execution

---

## Recovery Case Lifecycle

```
CREATED
  └─▶ DIAGNOSED ──▶ SCORED ──▶ POLICY_CHECK
                                    │
                    ┌───────────────┴──────────────────┐
                    ▼                                  ▼
              POLICY_APPROVED                   STOPPED / ESCALATED
                    │
          ┌─────────┴────────────┐
          ▼                      ▼
      EXECUTING              SCHEDULED
          │                      │
          ▼                      ▼
       OBSERVED           FRESHNESS CHECK
          │                      │
     ┌────┴────┐         ┌───────┴────────┐
     ▼         ▼         ▼                ▼
RECOVERED  RE_EVALUATE  CANCELLED      EXECUTING
               │
               └─▶ DIAGNOSED (from fresh state)
```

The engine never pre-plans a sequence. Every subsequent action is selected from the current state of the case.

---

## Supported Revenue Surfaces

| Event | Case Type | Primary Recovery Actions |
|-------|-----------|--------------------------|
| `payment.failed` | PAYMENT_FAILURE | Retry, payment link, wait-and-retry, discount |
| `checkout.abandoned` | CHECKOUT_ABANDONMENT | Payment link, reminder, discount |
| `subscription.halted` | SUBSCRIPTION_FAILURE | Mandate retry, payment link, escalate |
| `invoice.overdue` | OVERDUE_RECEIVABLE | Reminder, payment link, discount, escalate |

---

## Benchmark Results

RecoverAI was evaluated against two baselines on a 100-case held-out synthetic dataset (seed 42):

- **Natural recovery** — no intervention at all, observe what customers do on their own
- **Fixed-retry baseline** — always retry immediately regardless of context or economics
- **Oracle ceiling** — optimal action per case using hidden ground-truth probabilities (unreachable in practice)

| Metric | Natural | Fixed Retry | RecoverAI | Oracle |
|--------|---------|-------------|-----------|--------|
| Recovery rate | ~20% | varies | **54%** | ceiling |
| Gross recovered | — | — | ₹3,12,950 | — |
| Intervention cost | ₹0 | — | ₹2,999 | — |
| Net recovered | — | — | ₹3,09,951 | — |
| Incremental net vs natural | ₹0 | baseline | **₹2,02,491** | — |
| Incremental lift | 0% | baseline | **+188%** | — |

The 188% incremental lift over natural recovery means RecoverAI is not just recovering revenue — it is recovering revenue that would have been permanently lost.

Action distribution on the eval set:

| Action | Cases |
|--------|-------|
| NO_ACTION (uneconomic) | 55 |
| OFFER_DISCOUNT | 23 |
| SEND_PAYMENT_LINK | 11 |
| WAIT_AND_RETRY | 7 |
| RETRY_PAYMENT | 2 |
| ESCALATE | 2 |

The high NO_ACTION rate is intentional and correct. For cases where P₀ is already high (customer will likely retry on their own) or the economics are negative, intervention destroys value. Doing nothing is the right call.

---

## Project Structure

```
RecoverAI-Razorpay/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI gateway, webhook receiver, storefront/test endpoints
│   │   ├── config.py            # Settings from .env
│   │   ├── worker.py            # Redis worker — event dispatch → orchestrator → execution queue
│   │   ├── executor.py          # Outcome routing (payment captured / link paid)
│   │   ├── razorpay_client.py   # Razorpay SDK wrapper
│   │   ├── scheduler.py         # Scheduled action poller (30s interval)
│   │   ├── orchestrator/
│   │   │   ├── graph.py         # LangGraph engine — 6 nodes
│   │   │   └── db_runner.py     # Hydrates DB case into orchestrator context
│   │   ├── recovery/
│   │   │   ├── actions.py       # Economic scoring — P₀, Pₐ, uplift, net
│   │   │   ├── policy.py        # Deterministic policy gate
│   │   │   └── states.py        # Status enums, action constants
│   │   ├── mcp/
│   │   │   └── executor.py      # MCP boundary — freshness + structural checks + Razorpay call
│   │   ├── database/
│   │   │   ├── models.py        # SQLAlchemy models
│   │   │   └── connection.py    # asyncpg pool + Redis client
│   │   ├── synthetic/
│   │   │   └── generator.py     # Reproducible synthetic dataset generator
│   │   ├── baseline/
│   │   │   ├── fixed_retry.py   # Fixed-retry baseline evaluator
│   │   │   ├── oracle.py        # Oracle ceiling evaluator
│   │   │   └── compare.py       # Side-by-side benchmark comparison
│   │   └── api/
│   │       └── dashboard.py     # Dashboard API endpoints
│   ├── alembic/                 # DB migrations
│   ├── data/
│   │   ├── synthetic/           # Generated datasets (.jsonl)
│   │   ├── baseline/            # Baseline evaluation reports
│   │   └── recoverai/           # RecoverAI evaluation reports
│   └── requirements.txt
└── frontend/
    └── src/
        ├── pages/
        │   ├── Dashboard.tsx    # Recovery metrics overview
        │   ├── Cases.tsx        # Case list with filters
        │   ├── CaseDetail.tsx   # Per-case timeline and attempt history
        │   ├── Benchmark.tsx    # Benchmark comparison chart
        │   └── Storefront.tsx   # Demo storefront to trigger test events
        └── api.ts               # Typed API client
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Node 20+ (frontend)
- ngrok (for Razorpay webhook delivery in development)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:

```env
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/recoverai
REDIS_URL=redis://localhost:6379/0

GROQ_API_KEY=...
GEMINI_API_KEY=...           # fallback only

LANGFUSE_PUBLIC_KEY=...      # optional, for tracing
LANGFUSE_SECRET_KEY=...
PUBLIC_BASE_URL=http://localhost:8000
```

Run migrations and start services:

```bash
# Apply DB migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In a separate terminal, start the worker
python -m app.worker

# In a separate terminal, start the scheduler (optional — for scheduled actions)
python -m app.scheduler
```

### Expose for Razorpay Webhooks

```bash
ngrok http 8000
```

1. Copy your ngrok URL into `.env` as `PUBLIC_BASE_URL`
2. Register `https://YOUR-NGROK-ID.ngrok-free.app/webhooks/razorpay` in the Razorpay dashboard under Settings → Webhooks
3. Copy the webhook secret into `.env` as `RAZORPAY_WEBHOOK_SECRET`

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dashboard runs at `http://localhost:5173`.

---

## API Reference

### Webhooks

| Method | Path | Description |
|--------|------|-------------|
| POST | `/webhooks/razorpay` | Razorpay event receiver (HMAC verified) |

### Test / Synthetic Events

| Method | Path | Description |
|--------|------|-------------|
| POST | `/test/payment-failed` | Simulate `payment.failed` |
| POST | `/test/subscription-halted` | Simulate `subscription.halted` |
| POST | `/test/invoice-overdue` | Simulate `invoice.overdue` |
| POST | `/storefront/create-order` | Create a Razorpay order from the demo storefront |
| POST | `/storefront/abandon` | Simulate a checkout abandonment |

### Dashboard API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard/summary` | Overview metrics — at-risk, recovered, rates |
| GET | `/dashboard/cases` | Paginated case list with filters |
| GET | `/dashboard/cases/{id}` | Case detail with full attempt history |
| GET | `/dashboard/benchmark` | Benchmark comparison data |
| GET | `/health` | Liveness check |
| GET | `/docs` | Auto-generated Swagger UI |

---

## Webhook Events Handled

| Event | Action |
|-------|--------|
| `payment.failed` | Create PAYMENT_FAILURE recovery case |
| `subscription.halted` | Create SUBSCRIPTION_FAILURE recovery case |
| `invoice.overdue` | Create OVERDUE_RECEIVABLE recovery case |
| `payment.captured` | Mark case RECOVERED |
| `order.paid` | Mark case RECOVERED |
| `payment_link.paid` | Mark case RECOVERED |
| `subscription.charged` | Mark subscription case RECOVERED |
| `invoice.paid` | Mark receivables case RECOVERED |

---

## Synthetic Dataset

RecoverAI includes a reproducible synthetic data generator for offline evaluation without needing real Razorpay traffic.

```bash
# Generate 500 cases (400 train / 100 eval), seed 42
python -m app.synthetic.generator --count 500 --seed 42 --split 0.8

# Generate only eval cases
python -m app.synthetic.generator --count 100 --seed 42 --eval-only
```

Each case has:
- Realistic payment context (failure code, amount, method, customer segment)
- Hidden ground-truth probabilities — `P(natural_recovery)` and `P(recovery | action)` for every candidate action
- A simulated actual outcome (Bernoulli draw on the optimal action's probability)

The orchestrator never sees the ground-truth values. It must estimate them from features, just as in production.

### Running the Benchmark

```bash
# Compare fixed_retry baseline vs oracle ceiling
python -m app.baseline.compare --dataset data/synthetic/eval_seed42_n100.jsonl

# Include a RecoverAI evaluation report
python -m app.baseline.compare \
  --dataset data/synthetic/eval_seed42_n100.jsonl \
  --recoverai data/recoverai/eval_report_seed42.json

# Run the full RecoverAI evaluation
python test_matrix.py
```

---

## Observability

LangGraph orchestrator runs are traced to Langfuse when credentials are configured. Each trace captures:
- The full case context passed to the engine
- Scored and permitted actions at decision time
- The LLM prompt and raw response
- The final action, validation result, and reasoning
- The MCP execution result and freshness check outcome

All orchestrator decisions and execution results are also persisted to PostgreSQL in `recovery_attempts` with `orchestrator_trace` (JSONB) containing the full reasoning snapshot.

---

## Design Principles

**Economics first, AI second.** The LLM does not decide what to do in a vacuum. It selects from a pre-scored, policy-filtered permitted set. The hard work of quantifying whether an action is worth taking happens before the prompt is written.

**One next action, always from fresh state.** RecoverAI never commits to a recovery plan. After every outcome, it re-evaluates from the current state of the case. The second action is never chosen from the context of the first.

**The MCP boundary is non-negotiable.** No Razorpay API call happens without passing both the freshness check and the structural validation at execution time. Policy approval at decision time is necessary but not sufficient.

**Doing nothing is a valid outcome.** If the economics are negative — meaning the cost of intervention exceeds the incremental recovery — the correct answer is NO_ACTION. The system is built to recognize and stop cleanly, not to manufacture activity.
