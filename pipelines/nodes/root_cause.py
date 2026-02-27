"""
pipelines/nodes/root_cause.py
-------------------------------
Node 6: RootCauseNode

Called when one or more CI/CD gates fail.
Uses the LLM to analyse the full results set and gate verdicts and
produce a structured RootCauseOutput with:
  - Dominant failure pattern
  - Affected test cases
  - Recommended actions
  - Severity (LOW | HIGH | CRITICAL)
  - Whether a retry is likely to help

Conditional routing after this node:
  - severity=LOW  + retry_likely → back to test_dispatch_node (retry loop)
  - severity=HIGH               → human_review_node (LangGraph interrupt)
  - severity=CRITICAL           → END (immediate BLOCK)

References:
  - Guide 03 Section 4: Safety Gate
  - Guide 06 Section 7: CI/CD gate definitions
  - Guide 03 Section 6: Regression detection
"""

from __future__ import annotations

import json
import logging

from openai import AzureOpenAI

from pipelines.config import settings
from pipelines.schemas.scoring import RootCauseOutput
from pipelines.schemas.state import (
    EvalResultRecord,
    GateVerdicts,
    TestOrchestratorState,
)

logger = logging.getLogger(__name__)


def _get_llm_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )


def _build_root_cause_prompt(
    results: list[EvalResultRecord],
    verdicts: GateVerdicts,
    retry_count: int,
) -> str:
    """Build root cause analysis prompt."""
    failing = [r for r in results if not r.skipped and not r.task_success]
    failed_summary = [
        {
            "test_case": r.test_case_name,
            "flow_status": r.flow_status,
            "gate_failed": r.gate_failed,
            "overall_score": r.overall_score,
            "is_hard_fail": r.is_hard_fail,
            "safety_severity": r.safety_severity,
            "failure_reason": r.failure_reason,
            "failure_details": r.failure_details,
            "latency_ms": r.latency_ms,
        }
        for r in failing
    ]

    return f"""
You are a root cause analyst for a Microsoft Copilot Studio agent testing framework.

Gate violations detected:
{json.dumps(verdicts.blocked_gate_names(), indent=2)}

Failing test cases ({len(failing)} of {len(results)} active):
{json.dumps(failed_summary, indent=2)}

Current retry count: {retry_count} (max: {settings.max_retry_count})

Analyse the failures and return a RootCauseOutput JSON object. 

Severity guidelines:
  - LOW:      Isolated failures, likely transient, retry may help. No safety issues.
  - HIGH:     Multiple failures, deterministic root cause, needs human review.
  - CRITICAL: Safety gate triggered, hard fails present, or >50% task failure rate.

For retry_likely_to_help:
  - True only if the failure looks transient (latency spike, service blip, single HTTP error).
  - False if the failure is deterministic (schema bug, gate logic error, config issue).

Note: If retry_count >= {settings.max_retry_count}, set retry_likely_to_help=False
to prevent infinite loops.
""".strip()


def root_cause_node(state: TestOrchestratorState) -> dict:
    """
    LLM-powered root cause analysis. Updates failure_severity and root_cause_log.
    """
    results: list[EvalResultRecord] = state.get("results", [])
    verdicts: GateVerdicts = state.get("gate_verdicts", GateVerdicts())
    retry_count: int = state.get("retry_count", 0)
    root_cause_log: list[str] = list(state.get("root_cause_log", []))
    errors: list[str] = list(state.get("errors", []))

    # Force CRITICAL if retry limit exhausted
    if retry_count >= settings.max_retry_count:
        msg = f"[RootCauseNode] Retry limit ({settings.max_retry_count}) reached — forcing CRITICAL"
        logger.warning(msg)
        root_cause_log.append(msg)
        return {
            "failure_severity": "CRITICAL",
            "root_cause_log": root_cause_log,
            "deployment_decision": "BLOCK",
            "errors": errors,
        }

    llm = _get_llm_client()
    failure_severity = "HIGH"
    summary_text = "Root cause analysis unavailable"

    try:
        prompt = _build_root_cause_prompt(results, verdicts, retry_count)
        response = llm.beta.chat.completions.parse(
            model=settings.azure_openai_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise root cause analyst for AI agent test pipelines. "
                        "Return only valid JSON matching the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=RootCauseOutput,
            temperature=0.0,
        )

        rca: RootCauseOutput = response.choices[0].message.parsed

        failure_severity = rca.severity
        summary_text = (
            f"[Attempt {retry_count + 1}] {rca.summary} | "
            f"Pattern: {rca.failure_pattern} | "
            f"Severity: {rca.severity} | "
            f"Retry helpful: {rca.retry_likely_to_help} | "
            f"Actions: {'; '.join(rca.recommended_actions)}"
        )

        root_cause_log.append(summary_text)
        logger.info("[RootCauseNode] %s", summary_text)

        # Override deployment_decision based on LLM recommendation
        deployment_decision = rca.deployment_recommendation

        # If retry is recommended and we haven't hit the limit: reset for retry loop
        should_retry = (
            rca.retry_likely_to_help
            and rca.severity == "LOW"
            and retry_count < settings.max_retry_count
        )

        return {
            "failure_severity": failure_severity,
            "root_cause_log": root_cause_log,
            "deployment_decision": deployment_decision,
            "retry_count": retry_count + (1 if should_retry else 0),
            "human_review_required": rca.severity == "HIGH",
            "errors": errors,
        }

    except Exception as exc:
        msg = f"[RootCauseNode] LLM root cause analysis failed: {exc}"
        logger.error(msg)
        errors.append(msg)
        root_cause_log.append(msg)

    return {
        "failure_severity": failure_severity,
        "root_cause_log": root_cause_log,
        "deployment_decision": "BLOCK",
        "errors": errors,
    }


def route_after_root_cause(state: TestOrchestratorState) -> str:
    """
    Conditional edge after root_cause_node.
    Routes to: retry (test_dispatch_node), human_review, or end (BLOCK).
    """
    severity = state.get("failure_severity", "HIGH")
    retry_count = state.get("retry_count", 0)
    human_required = state.get("human_review_required", False)

    if severity == "CRITICAL":
        logger.info("[Router] CRITICAL severity — routing to blocked END")
        return "critical"

    if human_required or severity == "HIGH":
        logger.info("[Router] HIGH severity — routing to human_review_node")
        return "high"

    if severity == "LOW" and retry_count <= settings.max_retry_count:
        logger.info("[Router] LOW severity — routing to retry (test_dispatch_node)")
        return "retry"

    logger.info("[Router] Default block routing")
    return "critical"
