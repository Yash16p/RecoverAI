# RecoverAI

## Revenue Recovery Control Plane

An event-driven AI system that detects revenue leakage across payments, checkout, subscriptions and receivables.

## Features

- Payment Failures, Checkout Abandonment, Subscription Halted, Overdue Receivables
- Economic Scoring (P0, Pa, Uplift, Net Value)
- Deterministic Policy Gate
- One-Next-Action Decisioning  
- Freshness Checks
- MCP Execution Boundary

See ARCHITECTURE.md for detailed diagrams.

## Quick Start

Backend: cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8000
Worker: python -m app.worker
Frontend: cd frontend && npm install && npm run

## Tech Stack

FastAPI, Redis, PostgreSQL, LangGraph, Groq LLM, Razorpay, Langfuse, React

---
Razorpay AI Buildathon 2026 - Track 03
