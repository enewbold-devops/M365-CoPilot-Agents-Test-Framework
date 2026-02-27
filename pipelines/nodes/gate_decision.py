"""
pipelines/nodes/gate_decision.py
----------------------------------
Node 5: GateDecisionNode

Evaluates all 7 CI/CD deployment gates from Guide 06 Section 7 against
the scored EvalResultRecords.

Gates:
  1. Hard fail present (any IsHardFail = True)
  2. Task success rate below threshold
  3. Validation pass rate below threshold
  4. Upsert correctness below threshold
  5. P95 latency exceeds SLO
  6. Score delta below regression threshold vs baseline
  7. Previously passing test case now fails

Sets gate_verdicts and deployment_decision in state.
Routing edges:
  - all_pass  → test_gen_node (generate new cases, then END)
  - gate_fail → root_cause_node
"""

from __future__ import annotations

import logging
from statistics import quantiles

from pipelines.schemas.state import (
    EvalResultRecord,
    EvalRunSummary,
    GateVerdicts,
    TestOrchestratorState,
)

logger = logging.getLogger(__name__)

# Default thresholds (overridden by AgentTestConfig from Dataverse if loaded)
_DEFAULT_THRESHOLDS = {
    "min_task_success_rate": 0.90,
    "min_validation_pass_rate": 0.95,
    "min_upsert_correctness": 0.95,
    "max_p95_latency_ms": 30_000,
    "score_drop_threshold": -5.0,
}


def gate_decision_node(state: TestOrchestratorState) -> dict:
    """
    Evaluate all 7 CI/CD gates. Populate gate_verdicts and deployment_decision.
    """
    results: list[EvalResultRecord] = state.get("results", [])
    summary: EvalRunSummary = state.get("eval_run_summary")
    test_config: dict = state.get("test_config", {})
    errors: list[str] = list(state.get("errors", []))

    # Merge thresholds: Dataverse config overrides defaults
    thresholds = {**_DEFAULT_THRESHOLDS, **test_config}

    # Only evaluate active (non-skipped) results
    active = [r for r in results if not r.skipped]
    total = len(active)

    if total == 0:
        msg = "[GateDecisionNode] No active results to evaluate — blocking by default"
        logger.warning(msg)
        errors.append(msg)
        verdicts = GateVerdicts(gate1_hard_fail=True)
        return {
            "gate_verdicts": verdicts,
            "deployment_decision": "BLOCK",
            "errors": errors,
        }

    verdicts = GateVerdicts()

    # --- GATE 1: Hard fail present (Guide 06 Section 7 Gate 1) ---
    hard_fail_count = sum(1 for r in active if r.is_hard_fail)
    if hard_fail_count > 0:
        verdicts.gate1_hard_fail = True
        logger.warning("[GateDecisionNode] GATE 1 FAILED — %d hard fail(s)", hard_fail_count)

    # --- GATE 2: Task success rate (Guide 06 Section 7 Gate 2) ---
    success_count = sum(1 for r in active if r.task_success)
    task_success_rate = success_count / total
    min_tsr = thresholds["min_task_success_rate"]
    if task_success_rate < min_tsr:
        verdicts.gate2_task_success_rate = True
        logger.warning(
            "[GateDecisionNode] GATE 2 FAILED — task success rate %.2f < %.2f",
            task_success_rate, min_tsr
        )

    # --- GATE 3: Validation pass rate (Guide 06 Section 7 Gate 3) ---
    # Proxy: proportion of results where tool_correctness >= 0.95
    validated = sum(1 for r in active if r.tool_correctness is not None and r.tool_correctness >= 0.95)
    validation_rate = validated / total
    min_vpr = thresholds["min_validation_pass_rate"]
    if validation_rate < min_vpr:
        verdicts.gate3_validation_pass_rate = True
        logger.warning(
            "[GateDecisionNode] GATE 3 FAILED — validation pass rate %.2f < %.2f",
            validation_rate, min_vpr
        )

    # --- GATE 4: Upsert correctness (Guide 06 Section 7 Gate 4) ---
    upsert_scores = [r.upsert_correctness for r in active if r.upsert_correctness is not None]
    avg_upsert = sum(upsert_scores) / len(upsert_scores) if upsert_scores else 1.0
    min_uc = thresholds["min_upsert_correctness"]
    if avg_upsert < min_uc:
        verdicts.gate4_upsert_correctness = True
        logger.warning(
            "[GateDecisionNode] GATE 4 FAILED — avg upsert correctness %.2f < %.2f",
            avg_upsert, min_uc
        )

    # --- GATE 5: P95 latency (Guide 06 Section 7 Gate 5) ---
    latencies = [r.latency_ms for r in active if r.latency_ms is not None]
    if len(latencies) >= 2:
        p95 = int(quantiles(latencies, n=20)[-1])
    elif len(latencies) == 1:
        p95 = latencies[0]
    else:
        p95 = 0
    max_latency = thresholds["max_p95_latency_ms"]
    if p95 > max_latency:
        verdicts.gate5_p95_latency = True
        logger.warning(
            "[GateDecisionNode] GATE 5 FAILED — P95 latency %dms > %dms SLO",
            p95, max_latency
        )

    # --- GATE 6: Score regression vs baseline (Guide 06 Section 7 Gate 6) ---
    if summary and summary.score_delta is not None:
        drop_threshold = thresholds["score_drop_threshold"]
        if summary.score_delta < drop_threshold:
            verdicts.gate6_score_regression = True
            logger.warning(
                "[GateDecisionNode] GATE 6 FAILED — score delta %.2f < threshold %.2f",
                summary.score_delta, drop_threshold
            )

    # --- GATE 7: Previously passing test now fails (Guide 06 Section 7 Gate 7) ---
    # In Phase 1 we check: any test that was expected to succeed but produced task_success=False
    newly_failing = [
        r for r in active
        if r.task_success is False
    ]
    if newly_failing:
        verdicts.gate7_previously_passing = True
        logger.warning(
            "[GateDecisionNode] GATE 7 FAILED — %d test(s) failing: %s",
            len(newly_failing),
            [r.test_case_name for r in newly_failing]
        )

    # --- Final decision ---
    decision = "BLOCK" if verdicts.any_blocked else "PASS"
    blocked_names = verdicts.blocked_gate_names()

    if blocked_names:
        logger.error(
            "[GateDecisionNode] Deployment BLOCKED by: %s",
            " | ".join(blocked_names)
        )
    else:
        logger.info("[GateDecisionNode] All gates PASSED — deployment approved")

    return {
        "gate_verdicts": verdicts,
        "deployment_decision": decision,
        "errors": errors,
    }


def route_after_gate_decision(state: TestOrchestratorState) -> str:
    """
    Conditional edge after gate_decision_node.
    Routes to root_cause_node or test_gen_node (all pass).
    """
    decision = state.get("deployment_decision", "BLOCK")
    if decision == "PASS":
        logger.info("[Router] All gates passed — routing to test_gen_node")
        return "all_pass"
    logger.info("[Router] Gate failure — routing to root_cause_node")
    return "gate_fail"
