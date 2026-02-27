"""
pipelines/nodes/test_dispatch.py
----------------------------------
Node 3: TestDispatchNode

Executes all test cases in test_queue:
  - L1 tests:      HTTP POST to CloudFlow-FileUpload-Core (Guide 02 Section 3A)
  - L2/L3/L4 tests: Stubbed as skipped in Phase 1 (Phase 2 = Direct Line)

Runs L1 tests in batches of MAX_PARALLEL_L1 using concurrent.futures.
Each invocation returns an EvalResultRecord with raw flow outputs.
Scores are NOT computed here — that is evaluation_node's responsibility.

References:
  - Guide 02 Section 2A: InputContract / OutputContract
  - Guide 02 Section 3: IsTestMode flag
  - Guide 06 Section 3A: L1 HTTP invocation
  - Guide 06 Section 3B: L2-L4 Direct Line (Phase 2)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipelines.config import settings
from pipelines.schemas.state import EvalResultRecord, TestCase, TestOrchestratorState
from pipelines.tools.power_automate import CoreFlowClient

logger = logging.getLogger(__name__)


def test_dispatch_node(state: TestOrchestratorState) -> dict:
    """
    Dispatch all test cases and collect raw OutputContract results.
    """
    test_queue: list[TestCase] = state.get("test_queue", [])
    agent_id: str = state.get("agent_id", "")
    agent_version: str = state.get("agent_version", "unknown")
    errors: list[str] = list(state.get("errors", []))

    if not test_queue:
        msg = "[TestDispatchNode] test_queue is empty — nothing to execute"
        logger.warning(msg)
        errors.append(msg)
        return {"results": [], "errors": errors}

    l1_cases = [tc for tc in test_queue if tc.test_level == "L1"]
    other_cases = [tc for tc in test_queue if tc.test_level != "L1"]

    logger.info(
        "[TestDispatchNode] Dispatching %d L1 tests and %d L2-L4 tests (stubbed)",
        len(l1_cases), len(other_cases)
    )

    client = CoreFlowClient()
    results: list[EvalResultRecord] = []

    # --- L1 Tests: parallel HTTP invocations ---
    if l1_cases:
        max_workers = min(settings.max_parallel_l1, len(l1_cases))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    client.invoke_l1_test,
                    tc,
                    agent_id,
                    agent_version,
                ): tc
                for tc in l1_cases
            }
            for future in as_completed(futures):
                tc = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.debug("[TestDispatchNode] %s completed", tc.name)
                except Exception as exc:
                    msg = f"[TestDispatchNode] Unhandled exception for {tc.name}: {exc}"
                    logger.error(msg)
                    errors.append(msg)
                    results.append(EvalResultRecord(
                        test_case_id=tc.id,
                        test_case_name=tc.name,
                        test_level=tc.test_level,
                        flow_status="Failed",
                        error=str(exc),
                    ))

    # --- L2/L3/L4 Tests: stubbed (Phase 2 will implement Direct Line) ---
    for tc in other_cases:
        results.append(CoreFlowClient.stub_l2_l4_test(tc))
        logger.info("[TestDispatchNode] Stubbed %s test: %s", tc.test_level, tc.name)

    logger.info(
        "[TestDispatchNode] Dispatch complete: %d results collected (%d L1, %d stubbed)",
        len(results), len(l1_cases), len(other_cases)
    )

    return {"results": results, "errors": errors}
