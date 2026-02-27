"""
pipelines/schemas/contracts.py
--------------------------------
InputContract and OutputContract — the stable HTTP interface between the
LangGraph harness and CloudFlow-FileUpload-Core.

These schemas are defined in Guide 02, Section 2A and MUST remain stable
so that the Power Automate Adapter and any external callers (this harness,
Azure DevOps, Postman) all speak the same contract.
Changes to these schemas must be additive (backward-compatible) only.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class InputContract(BaseModel):
    """
    Guide 02 Section 2A — INPUTCONTRACT.
    Sent as JSON body in HTTP POST to CloudFlow-FileUpload-Core trigger URL.
    """
    # Conversation context
    ConversationId: str = Field(..., description="Unique conversation ID for this test invocation")
    SessionId: str = Field(default="", description="Optional session ID")

    # File payload
    FileName: str = Field(..., description="Original file name with extension")
    FileContentBase64: str = Field(..., description="Base64-encoded file content")
    FileMimeType: str = Field(default="text/csv")

    # Intent
    UserIntent: str = Field(
        default="Import",
        description="Classified intent. Values: Import | Summarize | Classify | Compare"
    )
    UserMessage: str = Field(default="", description="Raw user message from topic")

    # Agent context
    AgentId: str = Field(..., description="Bot/agent GUID invoking the flow")
    AgentVersion: str = Field(default="")
    Channel: str = Field(default="Web", description="Web | Teams | D365")

    # Test mode control (Guide 02 Section 3: IsTestMode flag pattern)
    IsTestMode: bool = Field(
        default=True,
        description=(
            "When True, the flow executes fully but writes are tagged IsTestData=True "
            "and no real business commits are made. See Guide 02 Section 3."
        )
    )
    TestCaseId: Optional[str] = Field(
        default=None,
        description="Test case GUID for correlation in Dataverse EvalResult"
    )

    # Schema version (Guide 02 — additive versioning)
    ContractVersion: str = Field(default="2.0")


class OutputContract(BaseModel):
    """
    Guide 02 Section 2A — OUTPUTCONTRACT.
    Returned synchronously in HTTP 200 body by CloudFlow-FileUpload-Core.
    """
    # Status
    FlowStatus: str = Field(..., description="Success | Failed")
    GateFailed: Optional[str] = Field(
        default=None,
        description="Which gate blocked execution. e.g. 'Gate1_FileType'"
    )

    # File processing outcomes
    TotalRows: Optional[int] = Field(default=None)
    ValidRows: Optional[int] = Field(default=None)
    InvalidRows: Optional[int] = Field(default=None)
    ParseSuccess: Optional[bool] = Field(default=None)

    # Scoring (Gate 7 — computed by the Core flow per Guide 03)
    OverallScore: Optional[float] = Field(default=None, description="0–100 weighted score")
    TaskSuccess: Optional[bool] = Field(default=None)
    GroundingScore: Optional[float] = Field(default=None)
    ToolCorrectness: Optional[float] = Field(default=None)
    UpsertCorrectness: Optional[float] = Field(default=None)
    IsHardFail: bool = Field(default=False)
    SafetySeverity: int = Field(default=0)

    # Telemetry
    LatencyMs: Optional[int] = Field(default=None)
    ConversationId: Optional[str] = Field(default=None)

    # Artifacts
    ExceptionReportUrl: Optional[str] = Field(default=None)
    CleanedFileUrl: Optional[str] = Field(default=None)

    # Error detail
    ErrorMessage: Optional[str] = Field(default=None)
    ErrorCode: Optional[str] = Field(default=None)

    # Echo-back
    ContractVersion: str = Field(default="2.0")
    TestCaseId: Optional[str] = Field(default=None)
