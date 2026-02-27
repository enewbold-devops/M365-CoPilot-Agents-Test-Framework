"""
pipelines/nodes/platform_health.py
------------------------------------
Node 2: PlatformHealthNode

Validates that the platform is healthy before executing any tests.
This implements the smoke test precondition check from Guide 05 Section 7.

Logic:
  1. Read the latest SmokeTestLog record from Dataverse.
  2. Check recency: must be within SMOKE_TEST_FRESHNESS_MINUTES (default 60).
  3. Check status: must be "OK" or "DEGRADED" (not "FAILED").
  4. If the check fails → set deployment_decision = BLOCK and bubble errors.

The graph's conditional edge after this node routes:
  - HEALTHY → test_dispatch_node
  - FAILED/STALE → END (early exit, deployment blocked)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from pipelines.config import settings
from pipelines.schemas.state import TestOrchestratorState
from pipelines.tools.dataverse import DataverseClient

logger = logging.getLogger(__name__)


def platform_health_node(state: TestOrchestratorState) -> dict:
    """
    Check smoke test freshness and status (Guide 05 Section 7).
    Sets smoke_test_status to one of: OK | DEGRADED | FAILED | STALE
    """
    errors: list[str] = list(state.get("errors", []))
    client = DataverseClient()

    smoke_status = "STALE"
    smoke_last_run: datetime | None = None

    try:
        record = client.get_latest_smoke_test()
        smoke_status = record.get("status", "STALE")
        smoke_last_run = record.get("last_run")

        logger.info(
            "[PlatformHealthNode] Smoke test — status: %s | last_run: %s",
            smoke_status, smoke_last_run
        )

        # --- Freshness check (Guide 05 Section 7: 60-minute window) ---
        if smoke_last_run is None:
            smoke_status = "STALE"
        else:
            now = datetime.now(timezone.utc)
            age_minutes = (now - smoke_last_run).total_seconds() / 60
            if age_minutes > settings.smoke_test_freshness_minutes:
                msg = (
                    f"[PlatformHealthNode] Smoke test is STALE — last run was "
                    f"{age_minutes:.0f} minutes ago (limit: {settings.smoke_test_freshness_minutes} min)"
                )
                logger.warning(msg)
                errors.append(msg)
                smoke_status = "STALE"

    except Exception as exc:
        msg = f"[PlatformHealthNode] Smoke test check failed: {exc}"
        logger.error(msg)
        errors.append(msg)
        smoke_status = "STALE"

    # --- Determine routing outcome ---
    if smoke_status in ("FAILED", "STALE"):
        block_reason = (
            f"Deployment BLOCKED by PlatformHealthNode: smoke test status is '{smoke_status}'. "
            "Ensure the smoke test agent (Guide 05) has run successfully within the last "
            f"{settings.smoke_test_freshness_minutes} minutes before running the regression harness."
        )
        logger.error(block_reason)
        errors.append(block_reason)
        return {
            "smoke_test_status": smoke_status,
            "smoke_test_last_run": smoke_last_run,
            "deployment_decision": "BLOCK",
            "errors": errors,
        }

    return {
        "smoke_test_status": smoke_status,
        "smoke_test_last_run": smoke_last_run,
        "errors": errors,
    }


def route_after_platform_health(state: TestOrchestratorState) -> str:
    """
    Conditional edge after platform_health_node.
    Routes to END (early block) or continues to test_dispatch_node.
    """
    status = state.get("smoke_test_status", "STALE")
    if status in ("FAILED", "STALE"):
        logger.info("[Router] Platform health BLOCKED — routing to END")
        return "blocked"
    logger.info("[Router] Platform health OK — routing to test_dispatch_node")
    return "healthy"
