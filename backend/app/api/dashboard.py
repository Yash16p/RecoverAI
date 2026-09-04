"""
Dashboard API
=============
Endpoints consumed by the React dashboard.

GET  /dashboard/summary              — merchant overview metrics
GET  /dashboard/cases                — paginated case list
GET  /dashboard/cases/{case_id}      — single case detail
GET  /dashboard/cases/{case_id}/timeline — full audit timeline
GET  /dashboard/benchmark            — RecoverAI vs baseline numbers
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.database.connection import get_db

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary(conn: asyncpg.Connection = Depends(get_db)) -> dict[str, Any]:
    """Merchant overview — revenue at risk, recovered, incremental lift."""

    totals = await conn.fetchrow("""
        SELECT
            COUNT(*)                                           AS total_cases,
            COUNT(*) FILTER (WHERE status = 'RECOVERED')      AS recovered_cases,
            COUNT(*) FILTER (WHERE status = 'STOPPED')        AS stopped_cases,
            COUNT(*) FILTER (WHERE status = 'ESCALATED')      AS escalated_cases,
            COUNT(*) FILTER (WHERE status = 'POLICY_APPROVED')AS approved_cases,
            COUNT(*) FILTER (WHERE status = 'EXECUTED')       AS executed_cases,
            COALESCE(SUM(amount), 0)                          AS total_at_risk_paise,
            COALESCE(SUM(amount) FILTER (WHERE status = 'RECOVERED'), 0) AS gross_recovered_paise,
            COALESCE(SUM(expected_net_recovery), 0)           AS expected_net_recovery_paise,
            COALESCE(AVG(p0), 0)                              AS avg_p0
        FROM recovery_cases
    """)

    # Attempt stats
    attempt_stats = await conn.fetchrow("""
        SELECT
            COUNT(*)                                              AS total_attempts,
            COUNT(*) FILTER (WHERE execution_status = 'EXECUTED') AS executed_attempts,
            COUNT(*) FILTER (WHERE execution_status = 'CANCELLED') AS cancelled_attempts,
            COUNT(*) FILTER (WHERE outcome = 'PAID')              AS paid_outcomes
        FROM recovery_attempts
    """)

    # Surface breakdown
    surfaces = await conn.fetch("""
        SELECT case_type,
               COUNT(*)                                         AS count,
               COALESCE(SUM(amount), 0)                         AS at_risk_paise,
               COUNT(*) FILTER (WHERE status = 'RECOVERED')     AS recovered
        FROM recovery_cases
        GROUP BY case_type
        ORDER BY at_risk_paise DESC
    """)

    return {
        "overview": {
            "total_cases":          totals["total_cases"],
            "recovered_cases":      totals["recovered_cases"],
            "stopped_cases":        totals["stopped_cases"],
            "escalated_cases":      totals["escalated_cases"],
            "total_at_risk_paise":  totals["total_at_risk_paise"],
            "gross_recovered_paise": totals["gross_recovered_paise"],
            "expected_net_recovery_paise": totals["expected_net_recovery_paise"],
            "recovery_rate":        round(
                totals["recovered_cases"] / totals["total_cases"], 4
            ) if totals["total_cases"] > 0 else 0,
            "avg_p0":               round(float(totals["avg_p0"] or 0), 4),
        },
        "attempts": {
            "total":     attempt_stats["total_attempts"],
            "executed":  attempt_stats["executed_attempts"],
            "cancelled": attempt_stats["cancelled_attempts"],
            "paid":      attempt_stats["paid_outcomes"],
        },
        "by_surface": [
            {
                "surface":          r["case_type"],
                "count":            r["count"],
                "at_risk_paise":    r["at_risk_paise"],
                "recovered":        r["recovered"],
            }
            for r in surfaces
        ],
    }


# ── Case list ─────────────────────────────────────────────────────────────────

@router.get("/cases")
async def list_cases(
    page:    int   = Query(1, ge=1),
    limit:   int   = Query(20, ge=1, le=100),
    status:  str | None = Query(None),
    surface: str | None = Query(None),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    offset = (page - 1) * limit
    where_clauses = []
    params: list[Any] = []

    if status:
        params.append(status)
        where_clauses.append(f"status = ${len(params)}")
    if surface:
        params.append(surface)
        where_clauses.append(f"case_type = ${len(params)}")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    params += [limit, offset]
    rows = await conn.fetch(f"""
        SELECT id, case_ref, case_type, status, amount, currency,
               failure_reason, recommended_action,
               p0, recommended_action_pa, expected_net_recovery,
               attempt_count, created_at, updated_at, resolved_at
        FROM recovery_cases
        {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)-1} OFFSET ${len(params)}
    """, *params)

    count_params = params[:-2]
    total = await conn.fetchval(
        f"SELECT COUNT(*) FROM recovery_cases {where}", *count_params
    )

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "cases": [dict(r) for r in rows],
    }


# ── Single case detail ────────────────────────────────────────────────────────

@router.get("/cases/{case_id}")
async def get_case(
    case_id: int,
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    case = await conn.fetchrow(
        "SELECT * FROM recovery_cases WHERE id = $1", case_id
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    attempts = await conn.fetch(
        """
        SELECT id, attempt_number, action, policy_decision, policy_reason,
               execution_status, razorpay_reference, outcome,
               p0_at_decision, pa_at_decision, expected_net_recovery_at_decision,
               intervention_cost, freshness_passed, freshness_checked_at,
               attempted_at, executed_at, outcome_confirmed_at,
               orchestrator_trace
        FROM recovery_attempts
        WHERE case_id = $1
        ORDER BY attempt_number ASC
        """,
        case_id,
    )

    return {
        "case":     dict(case),
        "attempts": [dict(a) for a in attempts],
    }


# ── Case timeline (audit view) ────────────────────────────────────────────────

@router.get("/cases/{case_id}/timeline")
async def get_case_timeline(
    case_id: int,
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    case = await conn.fetchrow(
        "SELECT id, case_ref, case_type, status, amount, failure_reason, created_at "
        "FROM recovery_cases WHERE id = $1",
        case_id,
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Get source webhook
    webhook = await conn.fetchrow(
        """
        SELECT we.razorpay_event_id, we.event_type, we.received_at, we.status
        FROM webhook_events we
        JOIN recovery_cases rc ON rc.source_event_id = we.id
        WHERE rc.id = $1
        """,
        case_id,
    )

    # Get all attempts with traces
    attempts = await conn.fetch(
        """
        SELECT attempt_number, action, policy_decision, policy_reason,
               execution_status, razorpay_reference, outcome,
               p0_at_decision, pa_at_decision, expected_net_recovery_at_decision,
               freshness_passed, freshness_checked_at,
               attempted_at, executed_at, outcome_confirmed_at,
               orchestrator_trace
        FROM recovery_attempts
        WHERE case_id = $1 ORDER BY attempt_number ASC
        """,
        case_id,
    )

    # Build timeline events
    events = []

    if webhook:
        events.append({
            "ts":    str(webhook["received_at"]),
            "event": "WEBHOOK_RECEIVED",
            "detail": f"{webhook['event_type']}  id={webhook['razorpay_event_id'][:20]}…",
        })

    events.append({
        "ts":    str(case["created_at"]),
        "event": "CASE_CREATED",
        "detail": f"{case['case_ref']}  {case['case_type']}  ₹{(case['amount'] or 0)//100}",
    })

    for a in attempts:
        trace = a["orchestrator_trace"] or {}
        if isinstance(trace, str):
            import json as _json
            try:
                trace = _json.loads(trace)
            except Exception:
                trace = {}
        events.append({
            "ts":    str(a["attempted_at"]),
            "event": "ORCHESTRATOR_RUN",
            "detail": {
                "attempt":      a["attempt_number"],
                "failure_category": trace.get("failure_category"),
                "context":      trace.get("context_summary", "")[:120],
                "reasoning":    trace.get("llm_reasoning", "")[:120],
                "p0":           float(a["p0_at_decision"] or 0),
                "pa":           float(a["pa_at_decision"] or 0),
                "net":          a["expected_net_recovery_at_decision"],
                "permitted":    trace.get("permitted_actions", []),
                "blocked":      trace.get("blocked_actions", []),
            },
        })

        events.append({
            "ts":    str(a["attempted_at"]),
            "event": "POLICY_DECISION",
            "detail": f"{a['policy_decision']}  action={a['action']}  reason={a['policy_reason'] or 'approved'}",
        })

        if a["executed_at"]:
            events.append({
                "ts":    str(a["executed_at"]),
                "event": "MCP_EXECUTION",
                "detail": {
                    "action":       a["action"],
                    "status":       a["execution_status"],
                    "razorpay_ref": a["razorpay_reference"],
                    "freshness":    a["freshness_passed"],
                },
            })

        if a["outcome_confirmed_at"]:
            events.append({
                "ts":    str(a["outcome_confirmed_at"]),
                "event": "OUTCOME",
                "detail": a["outcome"],
            })

    events.append({
        "ts":    str(case["created_at"]),
        "event": "CURRENT_STATUS",
        "detail": case["status"],
    })

    return {
        "case_ref": case["case_ref"],
        "status":   case["status"],
        "timeline": sorted(events, key=lambda e: e["ts"]),
    }


# ── Benchmark ─────────────────────────────────────────────────────────────────

@router.get("/benchmark")
async def get_benchmark() -> dict[str, Any]:
    """Load the saved eval report and baseline for comparison."""
    import json
    from pathlib import Path

    reports = {}
    for name, path in [
        ("fixed_retry",  "data/baseline/fixed_retry_eval_seed42.json"),
        ("oracle",       "data/baseline/oracle_eval_seed42.json"),
        ("recoverai",    "data/recoverai/eval_report_seed42.json"),
    ]:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                reports[name] = json.load(f)

    return {"benchmark": reports}
