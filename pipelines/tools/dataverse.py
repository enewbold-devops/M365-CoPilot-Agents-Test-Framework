"""
pipelines/tools/dataverse.py
------------------------------
Dataverse OData v4 client for the test harness.

Handles:
  - Entra ID token acquisition via MSAL (client credentials / App Registration)
  - Reading: TestCase, AgentTestConfig, SmokeTestLog, EvalRun (baseline)
  - Writing: EvalRun, EvalResult records
  - Triggering Power BI dataset refresh (Guide 04 Section 6)

All table names are resolved from config.settings.table[] using the
publisher prefix defined in Guide 01 (default: "new_").

References:
  - Guide 01: Dataverse Schema and Setup
  - Guide 04: Power BI Monitoring Dashboard (Section 6 — refresh API)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import msal
import requests

from pipelines.config import settings
from pipelines.schemas.state import EvalResultRecord, EvalRunSummary, TestCase

logger = logging.getLogger(__name__)


class DataverseClient:
    """Thin OData v4 client for the Dataverse evaluation tables."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._base_url = settings.dataverse_base_url.rstrip("/")
        self._prefix = settings.dataverse_publisher_prefix

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Acquire or return a cached Entra ID access token (client credentials)."""
        now = datetime.now(timezone.utc)

        if self._token and self._token_expiry and now < self._token_expiry:
            return self._token

        app = msal.ConfidentialClientApplication(
            client_id=settings.dataverse_client_id,
            client_credential=settings.dataverse_client_secret,
            authority=f"https://login.microsoftonline.com/{settings.dataverse_tenant_id}",
        )
        result = app.acquire_token_for_client(scopes=[settings.dataverse_scope])

        if "access_token" not in result:
            error = result.get("error_description", "Unknown MSAL error")
            msg = f"[DataverseClient] Token acquisition failed: {error}"
            logger.error(msg)
            raise RuntimeError(msg)

        self._token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        from datetime import timedelta
        self._token_expiry = now + timedelta(seconds=expires_in - 60)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = requests.post(url, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _patch(self, path: str, body: dict[str, Any]) -> None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = requests.patch(url, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Read: TestCase (Guide 01 / Guide 06 Section 2)
    # ------------------------------------------------------------------

    def get_test_cases(
        self,
        agent_id: str,
        test_set: Optional[str] = None,
    ) -> list[TestCase]:
        """
        Retrieve test cases for a given agent from new_TestCase.
        Optionally filter by test set name (Golden | Safety | Performance).
        """
        table = settings.table["test_case"]
        p = self._prefix

        filter_parts = [f"{p}agentid eq '{agent_id}'"]
        if test_set:
            filter_parts.append(f"{p}testsetname eq '{test_set}'")

        params = {
            "$filter": " and ".join(filter_parts),
            "$select": ",".join([
                f"{p}testcaseid",
                f"{p}name",
                f"{p}testsetname",
                f"{p}testlevel",
                f"{p}agentid",
                f"{p}channel",
                f"{p}inputfilename",
                f"{p}inputfilecontentb64",
                f"{p}inputuserintent",
                f"{p}expectedtasksuccess",
                f"{p}expectedparseresult",
                f"{p}expectedvalidrows",
                f"{p}expectedinvalidrows",
                f"{p}expectedgatefailed",
            ]),
            "$orderby": f"{p}testsetname asc, {p}name asc",
        }

        data = self._get(table, params=params)
        rows = data.get("value", [])

        cases: list[TestCase] = []
        for row in rows:
            try:
                cases.append(TestCase(
                    id=row.get(f"{p}testcaseid", str(uuid4())),
                    name=row.get(f"{p}name", "Unknown"),
                    test_set=row.get(f"{p}testsetname", "Golden"),
                    test_level=row.get(f"{p}testlevel", "L1"),
                    agent_id=agent_id,
                    channel=row.get(f"{p}channel", 100000000),
                    input_file_name=row.get(f"{p}inputfilename", ""),
                    input_file_content_b64=row.get(f"{p}inputfilecontentb64", ""),
                    input_user_intent=row.get(f"{p}inputuserintent", "Import"),
                    expected_task_success=bool(row.get(f"{p}expectedtasksuccess", True)),
                    expected_parse_result=row.get(f"{p}expectedparseresult"),
                    expected_valid_rows=row.get(f"{p}expectedvalidrows"),
                    expected_invalid_rows=row.get(f"{p}expectedinvalidrows"),
                    expected_gate_failed=row.get(f"{p}expectedgatefailed"),
                ))
            except Exception as exc:
                logger.warning("[DataverseClient] Skipped malformed TestCase row: %s", exc)

        logger.info("[DataverseClient] Loaded %d test cases for agent %s", len(cases), agent_id)
        return cases

    # ------------------------------------------------------------------
    # Read: AgentTestConfig (thresholds for gate decisions)
    # ------------------------------------------------------------------

    def get_agent_test_config(self, agent_id: str) -> dict[str, Any]:
        """
        Read the threshold configuration for a given agent.
        Returns a dict with keys mapped from new_AgentTestConfig columns.
        Falls back to sensible defaults aligned with Guide 06 Section 7 if not found.
        """
        table = settings.table["agent_test_config"]
        p = self._prefix
        params = {
            "$filter": f"{p}agentid eq '{agent_id}'",
            "$top": "1",
        }

        defaults: dict[str, Any] = {
            "min_task_success_rate": 0.90,
            "min_validation_pass_rate": 0.95,
            "min_upsert_correctness": 0.95,
            "max_p95_latency_ms": 30_000,
            "score_drop_threshold": -5.0,
        }

        try:
            data = self._get(table, params=params)
            rows = data.get("value", [])
            if not rows:
                logger.warning("[DataverseClient] No AgentTestConfig found for %s — using defaults", agent_id)
                return defaults
            row = rows[0]
            return {
                "min_task_success_rate": row.get(f"{p}mintasksuccessrate", defaults["min_task_success_rate"]),
                "min_validation_pass_rate": row.get(f"{p}minvalidationpassrate", defaults["min_validation_pass_rate"]),
                "min_upsert_correctness": row.get(f"{p}minupsertcorrectness", defaults["min_upsert_correctness"]),
                "max_p95_latency_ms": row.get(f"{p}maxp95latencyms", defaults["max_p95_latency_ms"]),
                "score_drop_threshold": row.get(f"{p}scoredroptreshold", defaults["score_drop_threshold"]),
            }
        except Exception as exc:
            logger.warning("[DataverseClient] AgentTestConfig fetch failed (%s) — using defaults", exc)
            return defaults

    # ------------------------------------------------------------------
    # Read: Smoke Test Log (Guide 05 Section 7)
    # ------------------------------------------------------------------

    def get_latest_smoke_test(self) -> dict[str, Any]:
        """
        Return the most recent SmokeTestRun record.
        Returns a dict with keys: status, last_run (datetime), latency_ms.
        """
        table = settings.table["smoke_test_log"]
        p = self._prefix
        params = {
            "$top": "1",
            "$orderby": f"{p}createdon desc",
            "$select": f"{p}smoketestlogid,{p}overallstatus,{p}createdon,{p}latencyms",
        }

        try:
            data = self._get(table, params=params)
            rows = data.get("value", [])
            if not rows:
                return {"status": "STALE", "last_run": None, "latency_ms": None}
            row = rows[0]
            last_run_str = row.get(f"{p}createdon", "")
            last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00")) if last_run_str else None
            return {
                "status": row.get(f"{p}overallstatus", "STALE"),
                "last_run": last_run,
                "latency_ms": row.get(f"{p}latencyms"),
            }
        except Exception as exc:
            logger.warning("[DataverseClient] SmokeTestLog fetch failed (%s) — treating as STALE", exc)
            return {"status": "STALE", "last_run": None, "latency_ms": None}

    # ------------------------------------------------------------------
    # Read: Baseline EvalRun (Guide 03 Section 5)
    # ------------------------------------------------------------------

    def get_baseline_eval_run(self, agent_id: str) -> Optional[dict[str, Any]]:
        """Return the most recent baseline EvalRun for regression delta calculation."""
        table = settings.table["eval_run"]
        p = self._prefix
        params = {
            "$filter": (
                f"{p}agentid eq '{agent_id}' and "
                f"{p}status eq 100000001"   # Completed
            ),
            "$orderby": f"{p}completedon desc",
            "$top": "1",
            "$select": f"{p}evalrunid,{p}overallscore,{p}completedon",
        }
        try:
            data = self._get(table, params=params)
            rows = data.get("value", [])
            return rows[0] if rows else None
        except Exception as exc:
            logger.warning("[DataverseClient] Baseline EvalRun fetch failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Write: EvalRun (Guide 01)
    # ------------------------------------------------------------------

    def create_eval_run(self, summary: EvalRunSummary) -> str:
        """
        Write a new EvalRun record to Dataverse.
        Returns the created record GUID.
        """
        table = settings.table["eval_run"]
        p = self._prefix

        body = {
            f"{p}name": f"{summary.environment} Run — {summary.dataset_name} — {summary.agent_version}",
            f"{p}agentid": summary.agent_id,
            f"{p}agentversion": summary.agent_version,
            f"{p}datasetname": summary.dataset_name,
            f"{p}trigger": 100000001,  # Pipeline
            f"{p}status": 100000000,   # Running
            f"{p}startedon": summary.started_on.isoformat() if summary.started_on else datetime.now(timezone.utc).isoformat(),
        }

        result = self._post(table, body)
        run_id = result.get(f"{p}evalrunid", str(uuid4()))
        logger.info("[DataverseClient] Created EvalRun: %s", run_id)
        return run_id

    def update_eval_run(self, run_id: str, summary: EvalRunSummary) -> None:
        """Update an existing EvalRun with final scores and status."""
        table = settings.table["eval_run"]
        p = self._prefix

        body = {
            f"{p}status": 100000001,  # Completed
            f"{p}completedon": summary.completed_on.isoformat() if summary.completed_on else datetime.now(timezone.utc).isoformat(),
            f"{p}totaltestcases": summary.total_test_cases,
            f"{p}passcount": summary.pass_count,
            f"{p}failcount": summary.fail_count,
            f"{p}hardfailcount": summary.hard_fail_count,
            f"{p}overallscore": summary.overall_score,
            f"{p}scoredelta": summary.score_delta,
            f"{p}notes": summary.notes,
        }

        self._patch(f"{table}({run_id})", body)
        logger.info("[DataverseClient] Updated EvalRun: %s", run_id)

    # ------------------------------------------------------------------
    # Write: EvalResult (Guide 01)
    # ------------------------------------------------------------------

    def write_eval_result(self, run_id: str, result: EvalResultRecord) -> str:
        """Write one EvalResult row to Dataverse. Returns created GUID."""
        table = settings.table["eval_result"]
        p = self._prefix

        body = {
            f"{p}name": result.test_case_name,
            f"{p}evalrunid@odata.bind": f"/{settings.table['eval_run']}({run_id})",
            f"{p}tasksuccess": result.task_success,
            f"{p}groundingscore": result.grounding_score,
            f"{p}toolcorrectness": result.tool_correctness,
            f"{p}latencyms": result.latency_ms,
            f"{p}userfeedback": 0,   # None — automated run
            f"{p}safetyseverity": result.safety_severity,
            f"{p}ishardfail": result.is_hard_fail,
            f"{p}overallscore": result.overall_score,
            f"{p}failuredetails": result.failure_details,
            f"{p}conversationid": result.conversation_id,
        }

        result_row = self._post(table, body)
        record_id = result_row.get(f"{p}evalresultid", str(uuid4()))
        logger.debug("[DataverseClient] Wrote EvalResult: %s", record_id)
        return record_id
