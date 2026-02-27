"""
pipelines/run_graph.py
------------------------
CLI entrypoint for the Copilot Studio LangGraph test orchestrator.

Usage:
    python -m pipelines.run_graph \\
        --agent-id  <AGENT_GUID> \\
        --version   <VERSION_LABEL> \\
        --env       Dev|UAT|Prod \\
        --dataset   Golden|Safety|Performance \\
        [--thread-id <UUID>]     # resume a checkpointed run

Human-in-the-loop:
    When human_review_node fires, the CLI will display the review summary
    and prompt for "approve" or "reject". The graph is resumed from the
    checkpoint with the human's decision.

Environment:
    Copy pipelines/.env.example to pipelines/.env and fill in your values
    before running.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import NodeInterrupt

from pipelines.graph import build_graph
from pipelines.schemas.state import TestOrchestratorState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("run_graph")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LangGraph Copilot Studio Test Orchestrator"
    )
    p.add_argument("--agent-id", required=True, help="Bot/agent GUID from Dataverse")
    p.add_argument("--version", default="unknown", help="Agent version label")
    p.add_argument("--env", default="Dev", choices=["Dev", "UAT", "Prod"])
    p.add_argument("--dataset", default="Golden", help="Test dataset name (e.g. Golden, Safety)")
    p.add_argument("--thread-id", default=None, help="Resume a checkpointed run by thread ID")
    return p.parse_args()


def _print_section(title: str, content) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    if isinstance(content, (dict, list)):
        print(json.dumps(content, indent=2, default=str))
    else:
        print(content)


def _print_final_report(final_state: TestOrchestratorState) -> None:
    summary = final_state.get("eval_run_summary")
    verdicts = final_state.get("gate_verdicts")
    decision = final_state.get("deployment_decision", "UNKNOWN")
    errors = final_state.get("errors", [])
    generated = final_state.get("generated_test_cases", [])
    root_cause_log = final_state.get("root_cause_log", [])

    _print_section("FINAL REPORT — Copilot Studio Agent Test Run", "")

    if summary:
        print(f"  Agent:           {summary.agent_id} v{summary.agent_version}")
        print(f"  Environment:     {summary.environment}")
        print(f"  Dataset:         {summary.dataset_name}")
        print(f"  Total Tests:     {summary.total_test_cases}")
        print(f"  Pass:            {summary.pass_count}")
        print(f"  Fail:            {summary.fail_count}")
        print(f"  Hard Fail:       {summary.hard_fail_count}")
        print(f"  Overall Score:   {summary.overall_score:.1f}/100")
        if summary.score_delta is not None:
            print(f"  Score Delta:     {summary.score_delta:+.1f}")
        print(f"  Notes:           {summary.notes}")

    print(f"\n  DEPLOYMENT:  {'✅ APPROVED' if decision == 'PASS' else '❌ BLOCKED'}")

    if verdicts and verdicts.any_blocked:
        _print_section("Blocked Gates", verdicts.blocked_gate_names())

    if root_cause_log:
        _print_section("Root Cause Log", root_cause_log)

    if generated:
        _print_section(
            f"Generated Test Cases ({len(generated)} — pending human review before Dataverse write)",
            generated,
        )

    if errors:
        _print_section("Errors / Warnings", errors)


def main() -> None:
    args = _parse_args()
    thread_id = args.thread_id or str(uuid4())

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: TestOrchestratorState = {
        "run_id": str(uuid4()),
        "agent_id": args.agent_id,
        "agent_version": args.version,
        "environment": args.env,
        "dataset_name": args.dataset,
        "retry_count": 0,
        "errors": [],
        "root_cause_log": [],
        "generated_test_cases": [],
        "human_review_required": False,
    }

    logger.info(
        "Starting test run — agent: %s | version: %s | env: %s | dataset: %s | thread: %s",
        args.agent_id, args.version, args.env, args.dataset, thread_id
    )

    final_state: TestOrchestratorState | None = None

    while True:
        try:
            # Stream events — print node-level progress
            for event in graph.stream(initial_state, config=config, stream_mode="values"):
                node_name = list(event.keys())[-1] if event else "unknown"
                logger.info("Node completed: %s", node_name)

            # Retrieve final state after streaming completes
            final_state = graph.get_state(config).values
            break

        except NodeInterrupt as interrupt_exc:
            # --- Human-in-the-loop: display review payload ---
            payload = interrupt_exc.value if hasattr(interrupt_exc, "value") else {}
            _print_section("HUMAN REVIEW REQUIRED", payload)

            while True:
                raw = input("\n  Enter decision [approve / reject]: ").strip().lower()
                if raw in ("approve", "reject"):
                    break
                print("  Invalid input. Please enter 'approve' or 'reject'.")

            # Resume the graph with the human's decision
            # Clear initial_state so LangGraph uses the checkpoint
            initial_state = {"human_decision": raw}
            logger.info("Resuming graph with human decision: %s", raw)

        except KeyboardInterrupt:
            logger.warning("Run cancelled by user")
            sys.exit(1)

        except Exception as exc:
            logger.error("Graph execution failed: %s", exc, exc_info=True)
            sys.exit(1)

    if final_state:
        _print_final_report(final_state)
    else:
        logger.error("No final state produced — run may have failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
