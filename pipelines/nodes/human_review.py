"""
pipelines/nodes/human_review.py
---------------------------------
Node 7: HumanReviewNode

Implements a LangGraph interrupt() — the graph pauses here and returns
control to the calling process (CLI, API, or notebook). The human
reviews the gate violations and root cause log, then resumes the graph
with an approval decision of "approve" or "reject".

This maps to the pre-deployment approval gate in Guide 06 Section 7.

Usage (local / CLI):
    The run_graph entrypoint detects the NodeInterrupt, prints the
    review summary, prompts the user for input, then calls
    graph.invoke({"human_decision": "approve"}, config=config)
    to resume from the checkpoint.

LangGraph checkpointing:
    Uses MemorySaver for local exploration.
    In production, swap to PostgresSaver or a durable Azure-backed store.
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from pipelines.schemas.state import EvalRunSummary, GateVerdicts, TestOrchestratorState

logger = logging.getLogger(__name__)


def human_review_node(state: TestOrchestratorState) -> dict:
    """
    Pause the graph with interrupt() and present a review summary to the operator.
    On resume, reads human_decision from state and sets deployment_decision.
    """
    gate_verdicts: GateVerdicts = state.get("gate_verdicts", GateVerdicts())
    root_cause_log: list[str] = state.get("root_cause_log", [])
    summary: EvalRunSummary = state.get("eval_run_summary")
    errors: list[str] = list(state.get("errors", []))

    # --- Build review payload presented to the human ---
    blocked_gates = gate_verdicts.blocked_gate_names()

    review_payload = {
        "title": "HUMAN REVIEW REQUIRED — Copilot Studio Agent Deployment Gate",
        "agent_id": summary.agent_id if summary else "unknown",
        "agent_version": summary.agent_version if summary else "unknown",
        "environment": summary.environment if summary else "unknown",
        "overall_score": summary.overall_score if summary else 0.0,
        "score_delta": summary.score_delta if summary else None,
        "blocked_gates": blocked_gates,
        "root_cause_log": root_cause_log,
        "instructions": (
            "Review the blocked gates and root cause log above. "
            "Respond with: 'approve' to override and allow deployment, "
            "or 'reject' to confirm the block."
        ),
    }

    logger.info("[HumanReviewNode] Suspending graph — awaiting human decision")

    # LangGraph interrupt: execution suspends here until the graph is resumed
    # The value passed to interrupt() is presented to the caller
    human_input: str = interrupt(review_payload)

    decision = str(human_input).strip().lower()

    if decision == "approve":
        logger.info("[HumanReviewNode] Human APPROVED deployment — overriding gate block")
        return {
            "human_decision": "approve",
            "deployment_decision": "PASS",
            "errors": errors,
        }
    else:
        logger.info("[HumanReviewNode] Human REJECTED deployment — block confirmed")
        return {
            "human_decision": "reject",
            "deployment_decision": "BLOCK",
            "errors": errors,
        }
