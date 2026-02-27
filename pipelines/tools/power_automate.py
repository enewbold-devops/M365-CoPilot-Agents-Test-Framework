"""
pipelines/tools/power_automate.py
-----------------------------------
HTTP client for CloudFlow-FileUpload-Core (Guide 02, Section 2C).

The Core flow exposes an HTTP trigger URL with a SAS token in the
query string. This client issues authenticated POST requests using the
InputContract schema and parses the synchronous OutputContract response.

In LangGraph test_dispatch_node, this is called for every L1 test case.
L2/L3/L4 tests (Direct Line) are stubbed and handled separately.

References:
  - Guide 02 Section 2A: InputContract / OutputContract
  - Guide 02 Section 3: IsTestMode flag pattern
  - Guide 02 Section 5: Testing CloudFlow-FileUpload-Core in isolation
"""

from __future__ import annotations

import logging
import time
from typing import Optional
from uuid import uuid4

import httpx

from pipelines.config import settings
from pipelines.schemas.contracts import InputContract, OutputContract
from pipelines.schemas.state import EvalResultRecord, TestCase

logger = logging.getLogger(__name__)


class CoreFlowClient:
    """
    Calls CloudFlow-FileUpload-Core for L1 test case execution.
    Each call is synchronous: HTTP POST → HTTP 200 + OutputContract body.
    """

    def __init__(self) -> None:
        self._url = settings.core_flow_url
        self._sas = settings.core_flow_sas_key
        self._timeout = httpx.Timeout(120.0, connect=10.0)

    def _build_url(self) -> str:
        """Append the SAS key if configured (Power Automate HTTP trigger pattern)."""
        if self._sas and "?" not in self._url:
            return f"{self._url}?{self._sas}"
        return self._url

    def invoke_l1_test(
        self,
        test_case: TestCase,
        agent_id: str,
        agent_version: str,
        channel: str = "Web",
    ) -> EvalResultRecord:
        """
        Execute a single L1 test case against CloudFlow-FileUpload-Core.

        Constructs the InputContract, posts to the HTTP trigger, parses the
        OutputContract, and returns a populated EvalResultRecord ready for
        the evaluation_node to score.

        Args:
            test_case:      TestCase to execute.
            agent_id:       Bot/agent GUID.
            agent_version:  Version label for the eval record.
            channel:        Channel name (default "Web").

        Returns:
            EvalResultRecord with raw flow outputs. Scores are NOT yet set —
            those are filled by evaluation_node (Node 4).
        """
        conversation_id = str(uuid4())
        start_ms = time.monotonic()

        contract = InputContract(
            ConversationId=conversation_id,
            FileName=test_case.input_file_name,
            FileContentBase64=test_case.input_file_content_b64,
            UserIntent=test_case.input_user_intent,
            AgentId=agent_id,
            AgentVersion=agent_version,
            Channel=channel,
            IsTestMode=True,
            TestCaseId=test_case.id,
        )

        result = EvalResultRecord(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            test_level=test_case.test_level,
            conversation_id=conversation_id,
        )

        if not self._url:
            # Core flow URL not configured — return a clearly skipped result
            result.skipped = True
            result.error = "CORE_FLOW_URL not configured in .env"
            logger.warning("[CoreFlowClient] %s — no CORE_FLOW_URL, skipping", test_case.name)
            return result

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    url=self._build_url(),
                    json=contract.model_dump(),
                    headers={"Content-Type": "application/json"},
                )
                elapsed_ms = int((time.monotonic() - start_ms) * 1000)

                if response.status_code != 200:
                    result.error = f"HTTP {response.status_code}: {response.text[:500]}"
                    result.flow_status = "Failed"
                    result.latency_ms = elapsed_ms
                    logger.error(
                        "[CoreFlowClient] %s — HTTP error %d",
                        test_case.name, response.status_code
                    )
                    return result

                output = OutputContract.model_validate(response.json())

                result.flow_status = output.FlowStatus
                result.gate_failed = output.GateFailed
                result.valid_rows = output.ValidRows
                result.invalid_rows = output.InvalidRows
                result.latency_ms = output.LatencyMs or elapsed_ms
                result.exception_report_url = output.ExceptionReportUrl
                result.is_hard_fail = output.IsHardFail
                result.safety_severity = output.SafetySeverity

                logger.info(
                    "[CoreFlowClient] %s → %s (gate: %s, latency: %dms)",
                    test_case.name,
                    output.FlowStatus,
                    output.GateFailed or "none",
                    result.latency_ms,
                )

        except httpx.TimeoutException:
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            result.error = f"Request timeout after {elapsed_ms}ms"
            result.flow_status = "Failed"
            result.latency_ms = elapsed_ms
            logger.error("[CoreFlowClient] %s — timeout", test_case.name)

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            result.error = str(exc)
            result.flow_status = "Failed"
            result.latency_ms = elapsed_ms
            logger.error("[CoreFlowClient] %s — exception: %s", test_case.name, exc)

        return result

    @staticmethod
    def stub_l2_l4_test(test_case: TestCase) -> EvalResultRecord:
        """
        Placeholder for L2/L3/L4 Direct Line tests.
        In Phase 1 (local exploration) these are recorded as skipped.
        Phase 2 will implement the Direct Line polling pattern from
        Guide 06 Section 3B using the microsoft-agents-copilotstudio-client package.
        """
        return EvalResultRecord(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            test_level=test_case.test_level,
            skipped=True,
            error=f"Direct Line invocation not yet implemented for {test_case.test_level} tests (Phase 2)",
        )
