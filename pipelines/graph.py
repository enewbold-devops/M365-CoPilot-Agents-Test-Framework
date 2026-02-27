"""
pipelines/graph.py
--------------------
Assembles the LangGraph StateGraph for the Copilot Studio test orchestrator.

Node sequence:
  START
    └─► scope_node              (Node 1 — load config & test cases)
          └─► platform_health_node   (Node 2 — smoke test gate)
                ├─► [STALE/FAILED] ─► END (blocked)
                └─► [HEALTHY] ─► test_dispatch_node   (Node 3 — execute tests)
                                    └─► evaluation_node         (Node 4 — LLM scoring)
                                          └─► gate_decision_node     (Node 5 — 7 CI/CD gates)
                                                ├─► [all_pass] ─► test_gen_node ─► END
                                                └─► [gate_fail] ─► root_cause_node  (Node 6)
                                                                      ├─► [critical] ─► test_gen_node ─► END
                                                                      ├─► [high] ─► human_review_node ─► test_gen_node ─► END
                                                                      └─► [retry] ─► test_dispatch_node (loop)

Human-in-the-loop:
  human_review_node uses LangGraph interrupt() — the graph suspends and
  waits for the operator to supply a decision before resuming.

Checkpointing:
  MemorySaver for local exploration.
  Swap to PostgresSaver or SqliteSaver for durable persistence.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from pipelines.nodes.scope import scope_node
from pipelines.nodes.platform_health import platform_health_node, route_after_platform_health
from pipelines.nodes.test_dispatch import test_dispatch_node
from pipelines.nodes.evaluation import evaluation_node
from pipelines.nodes.gate_decision import gate_decision_node, route_after_gate_decision
from pipelines.nodes.root_cause import root_cause_node, route_after_root_cause
from pipelines.nodes.human_review import human_review_node
from pipelines.nodes.test_gen import test_gen_node
from pipelines.schemas.state import TestOrchestratorState


def build_graph(checkpointer=None):
    """
    Build and compile the test orchestrator StateGraph.

    Args:
        checkpointer: LangGraph checkpointer. Defaults to MemorySaver (in-memory).
                      Pass a durable checkpointer (SqliteSaver, PostgresSaver)
                      for production use or multi-session Human-in-the-loop flows.

    Returns:
        Compiled LangGraph CompiledGraph ready to invoke.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    builder = StateGraph(TestOrchestratorState)

    # --- Register nodes ---
    builder.add_node("scope_node", scope_node)
    builder.add_node("platform_health_node", platform_health_node)
    builder.add_node("test_dispatch_node", test_dispatch_node)
    builder.add_node("evaluation_node", evaluation_node)
    builder.add_node("gate_decision_node", gate_decision_node)
    builder.add_node("root_cause_node", root_cause_node)
    builder.add_node("human_review_node", human_review_node)
    builder.add_node("test_gen_node", test_gen_node)

    # --- Linear edges ---
    builder.add_edge(START, "scope_node")
    builder.add_edge("scope_node", "platform_health_node")

    # --- Conditional: platform health gate ---
    builder.add_conditional_edges(
        "platform_health_node",
        route_after_platform_health,
        {
            "healthy": "test_dispatch_node",
            "blocked": END,
        },
    )

    # --- Linear: test execution → evaluation ---
    builder.add_edge("test_dispatch_node", "evaluation_node")
    builder.add_edge("evaluation_node", "gate_decision_node")

    # --- Conditional: gate decision ---
    builder.add_conditional_edges(
        "gate_decision_node",
        route_after_gate_decision,
        {
            "all_pass": "test_gen_node",
            "gate_fail": "root_cause_node",
        },
    )

    # --- Conditional: root cause severity routing ---
    builder.add_conditional_edges(
        "root_cause_node",
        route_after_root_cause,
        {
            "critical": "test_gen_node",       # Hard block — still generate new tests
            "high": "human_review_node",       # Needs human decision
            "retry": "test_dispatch_node",     # Re-run tests (max MAX_RETRY_COUNT times)
        },
    )

    # --- Human review → test gen → END ---
    builder.add_edge("human_review_node", "test_gen_node")
    builder.add_edge("test_gen_node", END)

    return builder.compile(checkpointer=checkpointer)


# Singleton for import convenience in the CLI
graph = build_graph()
