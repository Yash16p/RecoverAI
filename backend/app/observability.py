"""
RecoverAI Observability — Langfuse tracing
==========================================
Every case gets a Langfuse trace showing the full lifecycle:
  webhook → detect → diagnose → score → policy → LLM → MCP → Razorpay → outcome

Updated for Langfuse SDK 4.x which uses start_observation/start_as_current_observation.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger("recoverai.observability")

# ── Langfuse client (lazy init, graceful if keys not set) ─────────────────────

_langfuse = None
_initialized = False

def get_langfuse():
    global _langfuse, _initialized
    if _initialized:
        return _langfuse
    _initialized = True
    
    if not settings.LANGFUSE_PUBLIC_KEY or settings.LANGFUSE_PUBLIC_KEY.startswith("your-"):
        logger.warning("Langfuse keys not configured - tracing disabled")
        return None
    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key = settings.LANGFUSE_PUBLIC_KEY,
            secret_key = settings.LANGFUSE_SECRET_KEY,
            host       = settings.LANGFUSE_HOST,
        )
        # Verify auth
        if _langfuse.auth_check():
            logger.info("Langfuse tracing initialised and authenticated")
        else:
            logger.warning("Langfuse auth check failed")
            _langfuse = None
        return _langfuse
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")
        return None


# ── Trace one orchestrator run (SDK 4.x compatible) ───────────────────────────

def trace_orchestrator_run(
    case_ref: str,
    case_context: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    """
    Create a Langfuse trace for one orchestrator execution.
    Returns the trace URL (or None if Langfuse not configured).
    Uses Langfuse SDK 4.x API with start_as_current_observation.
    """
    lf = get_langfuse()
    if not lf:
        return None

    try:
        # Get data for spans
        scored = result.get("scored_actions", [])
        top_action = scored[0] if scored else {}
        permitted = result.get("permitted_actions", [])
        blocked = result.get("blocked_actions", [])
        
        # Create main trace as a chain
        with lf.start_as_current_observation(
            name=f"recoverai-case-{case_ref}",
            as_type="chain",
            input={
                "case_ref": case_ref,
                "case_type": case_context.get("case_type", "PAYMENT_FAILURE"),
                "amount_paise": case_context.get("amount_paise", 0),
                "failure_code": case_context.get("failure_code", ""),
                "customer_id": case_context.get("customer_id", "unknown"),
            },
            metadata={
                "currency": case_context.get("currency", "INR"),
                "payment_method": case_context.get("payment_method", ""),
            }
        ) as trace:
            
            # Span 1: Economic scoring
            with lf.start_as_current_observation(
                name="economic-scoring",
                as_type="span",
                input={
                    "failure_code": case_context.get("failure_code"),
                    "amount_paise": case_context.get("amount_paise")
                },
                output={
                    "top_action": top_action.get("action"),
                    "p0": top_action.get("p0"),
                    "pa": top_action.get("pa"),
                    "expected_net_recovery_paise": top_action.get("expected_net_recovery_paise"),
                    "all_scores": [{"action": s["action"], "net": s.get("expected_net_recovery_paise")} for s in scored[:5]]
                }
            ):
                pass
            
            # Span 2: Policy gate
            with lf.start_as_current_observation(
                name="policy-gate",
                as_type="evaluator",
                input={
                    "all_actions": [s["action"] for s in scored]
                },
                output={
                    "permitted": [a["action"] for a in permitted],
                    "blocked": [{"action": b["action"], "reason": b.get("policy", {}).get("reason")} for b in blocked]
                }
            ):
                pass
            
            # Span 3: LLM analysis (as generation for LLM tracking)
            with lf.start_as_current_observation(
                name="llm-analyze",
                as_type="generation",
                model=settings.GROQ_MODEL,
                input={
                    "failure": case_context.get("failure_code"),
                    "permitted_count": len(permitted),
                    "amount": case_context.get("amount_paise")
                },
                output={
                    "failure_category": result.get("failure_category"),
                    "context_summary": result.get("context_summary"),
                    "recommended_action": result.get("llm_recommended_action"),
                    "reasoning": result.get("llm_reasoning")
                }
            ):
                pass
            
            # Span 4: Validation and final decision
            with lf.start_as_current_observation(
                name="validate-and-decide",
                as_type="span",
                input={
                    "llm_recommendation": result.get("llm_recommended_action")
                },
                output={
                    "final_action": result.get("final_action"),
                    "validation_passed": result.get("validation_passed"),
                    "final_status": result.get("final_status")
                }
            ):
                pass
            
            # Update trace with final output
            trace.update(output={
                "final_action": result.get("final_action"),
                "final_status": result.get("final_status"),
                "validation_passed": result.get("validation_passed", False)
            })
        
        lf.flush()
        trace_url = f"{settings.LANGFUSE_HOST}"
        logger.info(f"Langfuse trace created for {case_ref}")
        return trace_url

    except Exception as e:
        logger.warning(f"Langfuse tracing failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def trace_execution(
    case_ref: str,
    action: str,
    razorpay_reference: str | None,
    execution_status: str,
    freshness_passed: bool,
    outcome: str | None = None,
) -> None:
    """Record MCP execution event in Langfuse."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        span = lf.start_observation(
            name=f"mcp-execution-{action.lower()}",
            as_type="tool",
            input={
                "case_ref": case_ref,
                "action": action,
            },
            output={
                "razorpay_reference": razorpay_reference,
                "execution_status": execution_status,
                "freshness_passed": freshness_passed,
                "outcome": outcome,
            },
        )
        span.end()
        lf.flush()
    except Exception as e:
        logger.warning(f"Langfuse event failed: {e}")
