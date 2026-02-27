"""
pipelines/nodes/scope.py
--------------------------
Node 1: ScopeNode

Reads agent configuration and test cases from Dataverse to populate
the test queue. This is the mandatory first node in the graph.

Dataverse reads:
  - new_AgentTestConfig   → gate thresholds
  - new_TestCase          → test cases to run
  - new_EvalRun (baseline) → prior score for regression delta

On failure: populates state.errors and allows the graph to exit cleanly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from pipelines.config import settings
from pipelines.schemas.state import EvalRunSummary, TestOrchestratorState
from pipelines.tools.dataverse import DataverseClient

logger = logging.getLogger(__name__)


def scope_node(state: TestOrchestratorState) -> dict:
    """
    Populate test_queue, test_config, and eval_run_summary.

    Reads from Guide 01 Dataverse tables:
      - new_AgentTestConfig for thresholds
      - new_TestCase for the test corpus
      - new_EvalRun for the baseline run (regression delta)
    """
    agent_id = state.get("agent_id", "")
    agent_version = state.get("agent_version", "unknown")
    environment = state.get("environment", "Dev")
    dataset_name = state.get("dataset_name", "Golden")
    errors: list[str] = list(state.get("errors", []))

    logger.info("[ScopeNode] Starting scope resolution for agent: %s", agent_id)

    client = DataverseClient()

    # --- 1. Load AgentTestConfig ---
    test_config: dict = {}
    try:
        test_config = client.get_agent_test_config(agent_id)
        logger.info("[ScopeNode] Loaded test config: %s", test_config)
    except Exception as exc:
        msg = f"[ScopeNode] AgentTestConfig load failed: {exc}"
        logger.warning(msg)
        errors.append(msg)

    # --- 2. Load TestCases ---
    test_queue = []
    try:
        test_queue = client.get_test_cases(agent_id=agent_id)
        logger.info("[ScopeNode] Loaded %d test cases", len(test_queue))
    except Exception as exc:
        msg = f"[ScopeNode] TestCase load failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    # --- 3. Load baseline EvalRun for regression delta ---
    baseline_run_id: str | None = None
    try:
        baseline = client.get_baseline_eval_run(agent_id)
        if baseline:
            p = settings.dataverse_publisher_prefix
            baseline_run_id = baseline.get(f"{p}evalrunid")
            logger.info("[ScopeNode] Baseline EvalRun: %s (score: %s)", baseline_run_id, baseline.get(f"{p}overallscore"))
    except Exception as exc:
        logger.warning("[ScopeNode] Baseline fetch failed: %s", exc)

    # --- 4. Create initial EvalRunSummary ---
    run_id = state.get("run_id") or str(uuid4())
    summary = EvalRunSummary(
        run_id=run_id,
        agent_id=agent_id,
        agent_version=agent_version,
        dataset_name=dataset_name,
        environment=environment,
        started_on=datetime.now(timezone.utc),
        baseline_run_id=baseline_run_id,
        total_test_cases=len(test_queue),
        notes=f"LangGraph harness run — {len(test_queue)} test cases loaded",
    )

    return {
        "run_id": run_id,
        "test_config": test_config,
        "test_queue": test_queue,
        "eval_run_summary": summary,
        "results": [],
        "root_cause_log": [],
        "retry_count": state.get("retry_count", 0),
        "errors": errors,
        "human_review_required": False,
        "deployment_decision": "PENDING_HUMAN",
        "generated_test_cases": [],
    }
