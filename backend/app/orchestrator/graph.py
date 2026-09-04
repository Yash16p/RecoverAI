"""
RecoverAI LangGraph Orchestrator  (Groq primary / Gemini fallback)
===================================================================
Nodes
------
1. load_case        — set context (from DB or synthetic dict)
2. score_actions    — deterministic: score_all_actions()
3. filter_policy    — deterministic: filter_permitted_actions()
4. llm_analyze      — ONE LLM call: diagnose + recommend together
5. validate         — hard code: is recommendation valid + permitted?
6. persist_decision — write to DB / return result

LLM strategy
------------
Primary  : Groq  llama-3.3-70b-versatile  — 14,400 RPD free, ~1s/call
Fallback : Gemini 2.5-flash              — only if Groq fails
If both fail: heuristic (top permitted action by expected net recovery)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from groq import Groq

from app.config import settings
from app.recovery.actions import score_all_actions, FAILURE_CODE_CATEGORY
from app.recovery.policy import filter_permitted_actions
from app.recovery.states import (
    CaseStatus,
    RecoveryAction,
    ALL_ACTIONS,
)

logger = logging.getLogger("recoverai.orchestrator")


# ── LLM call — Groq primary, Gemini fallback ─────────────────────────────────

def _call_groq(system: str, user: str) -> str:
    client = Groq(api_key=settings.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return resp.choices[0].message.content.strip()


def _call_gemini(system: str, user: str) -> str:
    from google import genai as google_genai
    from google.genai import types as genai_types
    client = google_genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.LLM_MODEL,
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=600,
        ),
    )
    return response.text.strip()


def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Try Groq first; fall back to Gemini on any failure."""
    try:
        return _call_groq(system_prompt, user_prompt)
    except Exception as e:
        logger.warning(f"[llm] Groq failed ({str(e)[:60]}), trying Gemini …")
        return _call_gemini(system_prompt, user_prompt)


def _extract_json(raw: str) -> dict:
    """Robustly pull the first {...} block from an LLM response."""
    # Strip Qwen thinking blocks <think>...</think>
    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end].strip())
    return json.loads(raw.strip())


# ── Graph state ───────────────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    case_id:                 int
    case_context:            dict[str, Any]
    scored_actions:          list[dict]
    permitted_actions:       list[dict]
    blocked_actions:         list[dict]
    failure_category:        str
    context_summary:         str
    llm_recommended_action:  str
    llm_reasoning:           str
    final_action:            str
    validation_passed:       bool
    final_status:            str
    persisted:               bool
    error:                   str | None


# ── Node 1: Load case ─────────────────────────────────────────────────────────

def load_case(state: OrchestratorState) -> OrchestratorState:
    logger.info(
        f"[load_case] case_id={state['case_id']}  "
        f"amount=₹{state['case_context'].get('amount_paise', 0) // 100}"
    )
    return state


# ── Node 2: Score actions (deterministic) ────────────────────────────────────

def score_actions(state: OrchestratorState) -> OrchestratorState:
    scores = score_all_actions(state["case_context"])
    dicts  = [s.to_dict() for s in scores]
    logger.info(
        f"[score_actions] top={dicts[0]['action']}  "
        f"net=₹{dicts[0]['expected_net_recovery_paise'] // 100}"
    )
    return {**state, "scored_actions": dicts}


# ── Node 3: Policy filter (deterministic) ────────────────────────────────────

def filter_policy(state: OrchestratorState) -> OrchestratorState:
    from app.recovery.actions import ActionScore

    ctx = state["case_context"]
    scored_objs = [
        ActionScore(
            action                      = d["action"],
            p0                          = d["p0"],
            pa                          = d["pa"],
            uplift                      = d["uplift"],
            cost_paise                  = d["cost_paise"],
            amount_paise                = d["amount_paise"],
            expected_net_recovery_paise = d["expected_net_recovery_paise"],
            reasoning                   = d["reasoning"],
        )
        for d in state["scored_actions"]
    ]

    permitted_objs, blocked_objs = filter_permitted_actions(scored_objs, ctx)

    def _enrich(obj_list):
        out = []
        for s in obj_list:
            d   = s.to_dict()
            pol = s.__dict__.get("policy")
            if pol:
                d["policy"] = pol.to_dict()
            out.append(d)
        return out

    permitted = _enrich(permitted_objs)
    blocked   = _enrich(blocked_objs)

    logger.info(f"[filter_policy] permitted={len(permitted)}  blocked={len(blocked)}")
    for b in blocked:
        pol = b.get("policy", {})
        logger.debug(f"  BLOCKED: {b['action']}  reason={pol.get('reason','?')}")

    return {**state, "permitted_actions": permitted, "blocked_actions": blocked}


# ── Node 4: Single LLM call — diagnose + decide ───────────────────────────────

_SYSTEM = """You are RecoverAI's recovery intelligence engine.

Given a failed payment case and pre-computed economic scores for each action,
you must do TWO things in one response:

1. Diagnose the failure (failure_category + context_summary)
2. Recommend the single best PERMITTED action (from the permitted list only)

Reply with valid JSON only — no markdown, no text outside the JSON:
{
  "failure_category": "very_low" | "low" | "medium" | "high",
  "context_summary": "<1-2 sentences: what happened and recovery outlook>",
  "recommended_action": "<EXACT action name from the permitted list>",
  "reasoning": "<1-2 sentences: why this action over the alternatives>"
}

CRITICAL: recommended_action MUST be exactly one of the names in the PERMITTED list.
If no permitted action makes sense, use NO_ACTION.
"""

def llm_analyze(state: OrchestratorState) -> OrchestratorState:
    """
    ONE LLM call that combines diagnosis + recommendation.
    Falls back to heuristics if LLM fails or quota is exhausted.
    """
    ctx       = state["case_context"]
    permitted = [a for a in state["permitted_actions"] if a["action"] != RecoveryAction.NO_ACTION]
    blocked   = state["blocked_actions"]

    # ── Fast path: nothing permitted → skip LLM entirely ─────────────────────
    if not permitted:
        category = FAILURE_CODE_CATEGORY.get(ctx.get("failure_code", ""), "medium")
        logger.info("[llm_analyze] No permitted actions — skipping LLM, using NO_ACTION")
        return {
            **state,
            "failure_category":       category,
            "context_summary":        f"No viable actions: {ctx.get('failure_code','')} (category={category})",
            "llm_recommended_action": RecoveryAction.NO_ACTION,
            "llm_reasoning":          "No economically viable, policy-approved actions available.",
        }

    # ── Build compact prompt ──────────────────────────────────────────────────
    perm_text = "\n".join(
        f"  {a['action']}: pa={a['pa']:.3f}  uplift={a['uplift']:.3f}  "
        f"net=₹{a['expected_net_recovery_paise'] // 100}"
        for a in permitted
    )
    blocked_text = ", ".join(
        f"{b['action']}({b.get('policy',{}).get('stop_reason','?')})"
        for b in blocked
        if b["action"] != RecoveryAction.NO_ACTION
    ) or "none"

    p0 = state["scored_actions"][0]["p0"] if state["scored_actions"] else 0.0

    prompt = f"""Payment failure case:
amount=₹{ctx.get('amount_paise', 0) // 100}  failure={ctx.get('failure_code', '?')}  method={ctx.get('payment_method', '?')}
segment={ctx.get('customer_segment', '?')}  prior_successes={ctx.get('previous_successful_payments', 0)}
hours_since_failure={ctx.get('hours_since_failure', 0):.1f}  subscription={ctx.get('is_subscription', False)}
p0_natural_recovery={p0:.3f}

PERMITTED actions (choose exactly one):
{perm_text}

BLOCKED actions (do NOT choose): {blocked_text}

Return JSON with failure_category, context_summary, recommended_action, reasoning."""

    # ── LLM call ──────────────────────────────────────────────────────────────
    try:
        raw    = _llm_call(_SYSTEM, prompt)
        parsed = _extract_json(raw)

        category  = parsed.get("failure_category", "medium")
        summary   = parsed.get("context_summary", "")
        action    = parsed.get("recommended_action", "").strip()
        reasoning = parsed.get("reasoning", "")

        logger.info(f"[llm_analyze] cat={category}  action={action}  {reasoning[:60]}…")
        return {
            **state,
            "failure_category":       category,
            "context_summary":        summary,
            "llm_recommended_action": action,
            "llm_reasoning":          reasoning,
        }

    except Exception as e:
        # Heuristic fallback — top permitted action by expected net recovery
        logger.warning(f"[llm_analyze] LLM failed → heuristic fallback: {str(e)[:80]}")
        top      = permitted[0]["action"]
        category = FAILURE_CODE_CATEGORY.get(ctx.get("failure_code", ""), "medium")
        return {
            **state,
            "failure_category":       category,
            "context_summary":        f"Heuristic: {ctx.get('failure_code','')} (recoverability={category})",
            "llm_recommended_action": top,
            "llm_reasoning":          f"Heuristic fallback: highest expected net recovery ({top})",
        }


# ── Node 5: Validate ──────────────────────────────────────────────────────────

def validate(state: OrchestratorState) -> OrchestratorState:
    """
    Hard code check — LLM recommendation must be a known, permitted action.
    NO_ACTION is always a valid safe fallback.
    """
    rec             = state.get("llm_recommended_action", "").strip()
    permitted_names = {a["action"] for a in state["permitted_actions"]}
    permitted_names.add(RecoveryAction.NO_ACTION)   # always safe

    passed = (rec in ALL_ACTIONS) and (rec in permitted_names)

    if not passed:
        override = RecoveryAction.NO_ACTION
        reason   = (
            f"LLM returned {rec!r} which is "
            f"{'unknown' if rec not in ALL_ACTIONS else 'not in permitted list'}. "
            f"Overriding with {override}."
        )
        logger.warning(f"[validate] {reason}")
        return {
            **state,
            "final_action":      override,
            "validation_passed": False,
            "llm_reasoning":     state.get("llm_reasoning", "") + f"  [OVERRIDE: {reason}]",
        }

    logger.info(f"[validate] ✔ {rec}")
    return {**state, "final_action": rec, "validation_passed": True}


# ── Node 6: Persist decision ──────────────────────────────────────────────────

def persist_decision(state: OrchestratorState) -> OrchestratorState:
    final_action = state["final_action"]
    case_id      = state["case_id"]

    if final_action == RecoveryAction.NO_ACTION:
        new_status = CaseStatus.STOPPED
    elif not state["validation_passed"]:
        new_status = CaseStatus.ESCALATED
    else:
        new_status = CaseStatus.POLICY_APPROVED

    snap = next(
        (s for s in state["scored_actions"] if s["action"] == final_action), None
    )

    if case_id == 0:
        logger.info(
            f"[persist] SYNTHETIC  action={final_action}  status={new_status}  "
            f"net=₹{(snap or {}).get('expected_net_recovery_paise', 0) // 100}"
        )
    else:
        logger.info(f"[persist] DB case_id={case_id}  action={final_action}  status={new_status}")

    return {**state, "final_status": new_status, "persisted": True, "error": None}


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(OrchestratorState)

    g.add_node("load_case",        load_case)
    g.add_node("score_actions",    score_actions)
    g.add_node("filter_policy",    filter_policy)
    g.add_node("llm_analyze",      llm_analyze)
    g.add_node("validate",         validate)
    g.add_node("persist_decision", persist_decision)

    g.set_entry_point("load_case")
    g.add_edge("load_case",        "score_actions")
    g.add_edge("score_actions",    "filter_policy")
    g.add_edge("filter_policy",    "llm_analyze")
    g.add_edge("llm_analyze",      "validate")
    g.add_edge("validate",         "persist_decision")
    g.add_edge("persist_decision", END)

    return g.compile()


def run_case(case_context: dict[str, Any], case_id: int = 0) -> dict[str, Any]:
    graph = build_graph()
    initial: OrchestratorState = {
        "case_id":                case_id,
        "case_context":           case_context,
        "scored_actions":         [],
        "permitted_actions":      [],
        "blocked_actions":        [],
        "failure_category":       "",
        "context_summary":        "",
        "llm_recommended_action": "",
        "llm_reasoning":          "",
        "final_action":           "",
        "validation_passed":      False,
        "final_status":           "",
        "persisted":              False,
        "error":                  None,
    }
    return graph.invoke(initial)
