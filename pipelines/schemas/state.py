"""
pipelines/schemas/state.py
---------------------------
LangGraph shared state and all supporting Pydantic models.

This state is the single source of truth passed between graph nodes.
Field names align 1:1 with Dataverse column logical names from Guide 01
so that serialisation to/from Dataverse is unambiguous.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations (match Dataverse Choice option set values from Guide 01)
# ---------------------------------------------------------------------------

class Channel(IntEnum):
    WEB = 100000000
    TEAMS = 100000001
    D365 = 100000002


class TestLevel(str):
    """L1 = HTTP direct to Core flow; L2-L4 = Direct Line."""
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class SafetySeverity(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class FailureSeverity(str):
    LOW = "LOW"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DeploymentDecision(str):
    PASS = "PASS"
    BLOCK = "BLOCK"
    PENDING_HUMAN = "PENDING_HUMAN"


# ---------------------------------------------------------------------------
# TestCase — mirrors new_TestCase Dataverse table (Guide 01 / Guide 06)
# ---------------------------------------------------------------------------

class TestCase(BaseModel):
    """One row from new_TestCase in Dataverse."""
    id: str = Field(..., description="new_testcaseid GUID")
    name: str = Field(..., description="new_name — human-readable label")
    test_set: str = Field(..., description="new_testsetname: Golden | Safety | Performance")
    test_level: str = Field(default="L1", description="L1–L4 classification from Guide 06 Section 2")
    agent_id: str = Field(..., description="FK to bot/agent under test")
    channel: int = Field(default=Channel.WEB)

    # File input
    input_file_name: str = Field(default="")
    input_file_content_b64: str = Field(default="", description="Base64 file content for L1 tests")
    input_user_intent: str = Field(default="Import")

    # Expected outcome (ground truth)
    expected_task_success: bool = Field(default=True)
    expected_parse_result: Optional[str] = Field(default=None, description="JSON of expected parse output")
    expected_valid_rows: Optional[int] = Field(default=None)
    expected_invalid_rows: Optional[int] = Field(default=None)
    expected_gate_failed: Optional[str] = Field(default=None, description="Gate name if flow is EXPECTED to fail")

    class Config:
        use_enum_values = True


# ---------------------------------------------------------------------------
# EvalResultRecord — mirrors new_EvalResult Dataverse table (Guide 01)
# ---------------------------------------------------------------------------

class EvalResultRecord(BaseModel):
    """Evaluation result for one test case execution."""
    test_case_id: str
    test_case_name: str
    test_level: str

    # OutputContract values (Guide 02 Section 2A)
    flow_status: Optional[str] = None        # "Success" | "Failed"
    gate_failed: Optional[str] = None
    valid_rows: Optional[int] = None
    invalid_rows: Optional[int] = None
    exception_report_url: Optional[str] = None
    latency_ms: Optional[int] = None
    conversation_id: Optional[str] = None

    # LLM-computed scores (Guide 03 Section 3 — LLM variant)
    task_success: Optional[bool] = None
    grounding_score: Optional[float] = None      # 0.00–1.00
    tool_correctness: Optional[float] = None     # 0.00–1.00
    upsert_correctness: Optional[float] = None   # 0.00–1.00
    ux_score: Optional[float] = None             # 0.00–1.00
    overall_score: Optional[float] = None        # 0–100

    # Safety Gate (Guide 03 Section 4)
    safety_severity: int = SafetySeverity.NONE
    is_hard_fail: bool = False

    # Failure detail
    failure_reason: Optional[str] = None
    failure_details: Optional[str] = None

    # Status
    skipped: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# EvalRunSummary — mirrors new_EvalRun Dataverse table (Guide 01)
# ---------------------------------------------------------------------------

class EvalRunSummary(BaseModel):
    """Aggregate summary for the entire test run."""
    run_id: str
    agent_id: str
    agent_version: str
    dataset_name: str
    environment: str
    started_on: Optional[datetime] = None
    completed_on: Optional[datetime] = None
    total_test_cases: int = 0
    pass_count: int = 0
    fail_count: int = 0
    hard_fail_count: int = 0
    overall_score: float = 0.0
    baseline_run_id: Optional[str] = None
    score_delta: Optional[float] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# GateVerdicts — 7 CI/CD gates from Guide 06 Section 7
# ---------------------------------------------------------------------------

class GateVerdicts(BaseModel):
    gate1_hard_fail: bool = False          # Any IsHardFail = True
    gate2_task_success_rate: bool = False  # TaskSuccess rate < threshold
    gate3_validation_pass_rate: bool = False
    gate4_upsert_correctness: bool = False
    gate5_p95_latency: bool = False
    gate6_score_regression: bool = False
    gate7_previously_passing: bool = False  # Regression on a test that previously passed

    @property
    def any_blocked(self) -> bool:
        return any([
            self.gate1_hard_fail,
            self.gate2_task_success_rate,
            self.gate3_validation_pass_rate,
            self.gate4_upsert_correctness,
            self.gate5_p95_latency,
            self.gate6_score_regression,
            self.gate7_previously_passing,
        ])

    def blocked_gate_names(self) -> list[str]:
        names = []
        if self.gate1_hard_fail:
            names.append("Gate 1: Hard Fail Present")
        if self.gate2_task_success_rate:
            names.append("Gate 2: Task Success Rate Below Threshold")
        if self.gate3_validation_pass_rate:
            names.append("Gate 3: Validation Pass Rate Below Threshold")
        if self.gate4_upsert_correctness:
            names.append("Gate 4: Upsert Correctness Below Threshold")
        if self.gate5_p95_latency:
            names.append("Gate 5: P95 Latency Exceeds SLO")
        if self.gate6_score_regression:
            names.append("Gate 6: Score Regression vs Baseline")
        if self.gate7_previously_passing:
            names.append("Gate 7: Previously Passing Test Now Fails")
        return names


# ---------------------------------------------------------------------------
# LangGraph Shared State (TypedDict — required by StateGraph)
# ---------------------------------------------------------------------------

class TestOrchestratorState(TypedDict, total=False):
    """
    Shared state object threaded through every node in the LangGraph.
    Fields map to Dataverse tables where noted.
    """
    # --- Run identity ---
    run_id: str                             # new_EvalRun.new_evalrunid
    agent_id: str                           # new_EvalRun.new_agentid
    agent_version: str                      # new_EvalRun.new_agentversion
    environment: str                        # "Dev" | "UAT" | "Prod"
    dataset_name: str                       # new_EvalRun.new_datasetname

    # --- Scope (populated by scope_node) ---
    test_config: dict[str, Any]            # new_AgentTestConfig row
    test_queue: list[TestCase]             # new_TestCase rows to execute

    # --- Platform health (populated by platform_health_node) ---
    smoke_test_status: str                 # "OK" | "DEGRADED" | "FAILED" | "STALE"
    smoke_test_last_run: Optional[datetime]

    # --- Test execution (populated by test_dispatch_node) ---
    results: list[EvalResultRecord]        # One per TestCase executed

    # --- Evaluation (populated by evaluation_node) ---
    eval_run_summary: EvalRunSummary

    # --- Gate decision (populated by gate_decision_node) ---
    gate_verdicts: GateVerdicts
    deployment_decision: str               # DeploymentDecision values

    # --- Root cause (populated by root_cause_node) ---
    root_cause_log: list[str]
    failure_severity: str                  # FailureSeverity values
    retry_count: int

    # --- Human review ---
    human_review_required: bool
    human_decision: Optional[str]         # "approve" | "reject"

    # --- Test generation (populated by test_gen_node) ---
    generated_test_cases: list[dict[str, Any]]

    # --- Error tracking ---
    errors: list[str]
