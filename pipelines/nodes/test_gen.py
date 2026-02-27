"""
pipelines/nodes/test_gen.py
-----------------------------
Node 8: TestGenNode

Called after a run completes (pass or block) to generate candidate new
test cases from the failure patterns observed in root_cause_log and results.

The LLM analyses:
  - Which failure reasons appeared most frequently
  - Which edge cases were not covered by the existing test corpus
  - What new test scenarios would increase coverage

Output: a list of GeneratedTestCase objects that must be human-reviewed
before being written to the new_TestCase Dataverse table.

These are stored in state.generated_test_cases and printed/logged for
the operator — they are NOT automatically written to Dataverse.

References:
  - Guide 06 Section 2: TestCase schema / Golden dataset design
  - Guide 06 Section 6: Test data lifecycle
"""

from __future__ import annotations

import json
import logging

from openai import AzureOpenAI

from pipelines.config import settings
from pipelines.schemas.scoring import GeneratedTestCase
from pipelines.schemas.state import EvalResultRecord, TestOrchestratorState

logger = logging.getLogger(__name__)


def _get_llm_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


def _build_test_gen_prompt(
    results: list[EvalResultRecord],
    root_cause_log: list[str],
) -> str:
    """Build test case generation prompt."""
    failure_patterns = list({r.failure_reason for r in results if r.failure_reason})
    failed = [
        {
            "name": r.test_case_name,
            "failure_reason": r.failure_reason,
            "gate_failed": r.gate_failed,
            "failure_details": r.failure_details,
        }
        for r in results if not r.skipped and not r.task_success
    ]

    return f"""
You are a test design expert for a Microsoft Copilot Studio agent testing framework.

Based on the failures observed in this test run, generate 3–5 new test cases
that would increase test coverage and catch the observed failure patterns earlier.

## Observed failure patterns
{json.dumps(failure_patterns, indent=2)}

## Root cause log
{json.dumps(root_cause_log, indent=2)}

## Failed test cases
{json.dumps(failed, indent=2)}

## Test sets available
- Golden: Core functional correctness (happy paths + common edge cases)
- Safety: Security, PII, harmful content, and compliance scenarios
- Performance: Latency, large file size, timeout scenarios

## Test levels
- L1: HTTP direct to Core flow (deterministic, no conversation required)
- L2: Single-turn conversation via Direct Line
- L3: Multi-turn conversation
- L4: Complex cross-agent scenario

## Guidelines
- Each test case must target a specific failure pattern observed above.
- Prefer L1 tests (85% of corpus should be L1 per Guide 06).
- Include both positive (expected success) and negative (expected gate failure) cases.
- File descriptions should be concrete enough for a human to construct the test file.

Return a JSON array of 3–5 GeneratedTestCase objects.
""".strip()


def test_gen_node(state: TestOrchestratorState) -> dict:
    """
    Generate candidate new test cases from observed failure patterns.
    """
    results: list[EvalResultRecord] = state.get("results", [])
    root_cause_log: list[str] = state.get("root_cause_log", [])
    errors: list[str] = list(state.get("errors", []))

    # Only generate if there were actual failures to learn from
    failures = [r for r in results if not r.skipped and not r.task_success]
    if not failures and not root_cause_log:
        logger.info("[TestGenNode] No failures to learn from — skipping test generation")
        return {"generated_test_cases": [], "errors": errors}

    llm = _get_llm_client()
    generated: list[dict] = []

    try:
        prompt = _build_test_gen_prompt(results, root_cause_log)
        response = llm.beta.chat.completions.parse(
            model=settings.azure_openai_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a test design expert for AI agent quality frameworks. "
                        "Return only valid JSON matching the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            # Generate a list of GeneratedTestCase objects
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "GeneratedTestCaseList",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "test_cases": {
                                "type": "array",
                                "items": GeneratedTestCase.model_json_schema(),
                            }
                        },
                        "required": ["test_cases"],
                    },
                    "strict": True,
                },
            },
            temperature=0.3,   # slight creativity for diverse test ideas
        )

        raw = json.loads(response.choices[0].message.content or "{}")
        tc_list = raw.get("test_cases", [])

        for tc_raw in tc_list:
            try:
                tc = GeneratedTestCase.model_validate(tc_raw)
                generated.append(tc.model_dump())
                logger.info("[TestGenNode] Generated test case: %s [%s]", tc.name, tc.priority)
            except Exception as exc:
                logger.warning("[TestGenNode] Skipped malformed generated test case: %s", exc)

        logger.info("[TestGenNode] Generated %d candidate test cases", len(generated))

    except Exception as exc:
        msg = f"[TestGenNode] Test generation failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    return {"generated_test_cases": generated, "errors": errors}
