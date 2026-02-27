"""
pipelines/schemas/scoring.py
------------------------------
Pydantic models for LLM-structured outputs in the Evaluation,
Root Cause, and Test Generation nodes.

Using structured_output (OpenAI response_format) to enforce JSON schema
from the LLM rather than fragile string parsing.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class LLMEvalOutput(BaseModel):
    """
    Structured output from evaluation_node LLM call (Node 4).
    LLM evaluates a single OutputContract against a TestCase ground truth
    and produces scores aligned with Guide 03 Section 3 KPI definitions.
    """
    # KPI 1 — Guide 03
    task_success: bool = Field(
        ...,
        description=(
            "True if the agent completed the user's intended task. "
            "For file upload: True if valid rows were processed and no unexpected gate failed."
        )
    )

    # KPI 2 — Guide 03
    grounding_score: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Faithfulness of AI-generated content to the input data. "
            "Set to 1.0 if no AI step was executed in the flow."
        )
    )

    # KPI 3 — Guide 03
    tool_correctness: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Did the right gates fire in the expected order with correct parameters? "
            "1.0 = all gates executed as expected, 0.0 = critical gate skipped or wrong."
        )
    )

    # KPI 6 — Guide 03
    upsert_correctness: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Of the valid rows, were Create/Update/Skip actions correct? "
            "Set to 1.0 if IsTestMode=True and no actual upsert was performed."
        )
    )

    # KPI 9+10 — UX component (Guide 03 Section 3 Component 5)
    ux_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="Combined UX quality: latency within SLO and turns to resolution."
    )

    # Computed overall (0–100) using Guide 03 formula:
    # TaskSuccess(30) + Grounding(15) + ToolCorrectness(20) + UpsertCorrectness(20) + UX(15)
    overall_score: float = Field(
        ..., ge=0.0, le=100.0,
        description="Weighted aggregate score 0–100 per Guide 03 Section 3."
    )

    # Safety Gate assessment (Guide 03 Section 4)
    is_hard_fail: bool = Field(
        default=False,
        description="True if any Safety Gate trigger condition was met."
    )
    safety_severity: int = Field(
        default=0,
        description="0=None, 1=Low, 2=Medium, 3=High (matches new_EvalResult.new_safetyseverity)"
    )

    # Failure taxonomy — matches new_EvalResult.new_failurereason choice values (Guide 01)
    failure_reason: Optional[str] = Field(
        default=None,
        description=(
            "Taxonomy: FileTypeRejected | FileSizeTooLarge | SchemaMismatch | "
            "ValidationFailure | UpsertError | AIStepFailure | SafetyViolation | "
            "TimeoutOrLatency | TopicHandoffFailure | OutputIntegrityFailure | Other"
        )
    )
    failure_details: Optional[str] = Field(
        default=None,
        description="Concise explanation of what failed and why, based on OutputContract content."
    )

    # Confidence in this evaluation (self-assessment)
    eval_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="LLM self-assessed confidence in its scoring. Low = may need human review."
    )


class RootCauseOutput(BaseModel):
    """
    Structured output from root_cause_node LLM call (Node 6).
    Analyses the full results set and gate verdicts to produce
    a prioritised failure explanation and recommended actions.
    """
    summary: str = Field(
        ...,
        description="1–3 sentence explanation of the primary failure pattern across all results."
    )

    failure_pattern: str = Field(
        ...,
        description=(
            "Dominant failure taxonomy label from the EvalResult failure_reason set. "
            "E.g. 'SchemaMismatch affecting 60% of test cases'."
        )
    )

    # Severity drives conditional edge routing in gate_decision_node
    severity: str = Field(
        ...,
        description="LOW | HIGH | CRITICAL — determines graph routing."
    )

    affected_test_cases: list[str] = Field(
        default_factory=list,
        description="List of test_case_id values that are failing."
    )

    recommended_actions: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of recommended remediation steps for the development team. "
            "Be specific: reference gate names, KPI names, and expected vs actual values."
        )
    )

    retry_likely_to_help: bool = Field(
        default=False,
        description=(
            "True if the failure looks transient (latency spike, service blip) "
            "and re-running the test suite may produce a pass. "
            "False if the root cause is deterministic (schema bug, config error)."
        )
    )

    deployment_recommendation: str = Field(
        ...,
        description="PASS | BLOCK — LLM recommendation independent of gate thresholds."
    )


class GeneratedTestCase(BaseModel):
    """
    A new test case generated by test_gen_node (Node 8) from failure patterns.
    Conforms to the new_TestCase Dataverse schema from Guide 01 / Guide 06 Section 2.
    """
    name: str = Field(..., description="Descriptive test case name")
    test_set: str = Field(..., description="Golden | Safety | Performance")
    test_level: str = Field(default="L1", description="L1–L4 classification")
    rationale: str = Field(
        ...,
        description="Why this test case was generated — which failure pattern it targets."
    )

    # File scenario
    input_file_description: str = Field(
        ...,
        description="Description of the file content to construct for this test case."
    )
    input_user_intent: str = Field(default="Import")

    # Ground truth
    expected_task_success: bool = Field(default=True)
    expected_gate_failed: Optional[str] = Field(
        default=None,
        description="If this tests a negative path, which gate should block it."
    )
    expected_valid_rows: Optional[int] = Field(default=None)
    expected_invalid_rows: Optional[int] = Field(default=None)

    # Priority for review queue
    priority: str = Field(
        default="Medium",
        description="High | Medium | Low — review priority before adding to Dataverse."
    )
