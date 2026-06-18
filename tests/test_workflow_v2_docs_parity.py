"""Workflow V2 docs and diagram parity guards."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def _normalized(path: str) -> str:
    return _read(path).lower()


def _combined_docs() -> str:
    return "\n".join(
        _normalized(path)
        for path in (
            "AGENT_FLOW.md",
            "docs/engineering/ESPRESSO_AGENT_PROTOCOL.md",
            "README.md",
        )
    )


def test_unclear_claim_blocks_downstream_swarm() -> None:
    docs = _combined_docs()

    assert "unclear claim blocks literature, examples, counterexamples, strategy, prover, and reviewers" in docs
    assert "stop before literature / examples / counterexamples / strategy / prover / reviewers" in docs


def test_examples_and_counterexamples_are_not_strategy_prerequisites() -> None:
    agent_flow = _normalized("AGENT_FLOW.md")
    protocol = _normalized("docs/engineering/ESPRESSO_AGENT_PROTOCOL.md")

    assert "may still be pending while strategy generator runs" in agent_flow
    assert "they are not mandatory before strategy generator" in agent_flow
    assert "not mandatory strategy generator prerequisites" in protocol
    assert "after literature/examples/counterexamples context exists" not in agent_flow
    assert "after the initial literature, examples, and\ncounterexamples exist" not in agent_flow


def test_pending_pressure_is_not_evidence() -> None:
    docs = _combined_docs()

    assert "pending examples are not survived_tests" in docs
    assert "pending counterexamples are not failed_tests" in docs
    assert "do not mean no counterexample pressure" in docs
    assert "pending pressure as survived/no-pressure evidence" in docs


def test_docs_match_runtime_source_theorem_before_strategy_order() -> None:
    from espresso_proof.front_half.registry import FRONT_HALF_STAGES
    from espresso_proof.orchestration.orchestrator import FAST_CORE_STAGES

    agent_flow = _normalized("AGENT_FLOW.md")
    protocol = _normalized("docs/engineering/ESPRESSO_AGENT_PROTOCOL.md")
    dot = _normalized("docs/diagrams/espresso_agent_flow.dot")

    assert FRONT_HALF_STAGES.index("source_theorem") < FRONT_HALF_STAGES.index("strategy")
    assert FAST_CORE_STAGES.index("source_theorem") < FAST_CORE_STAGES.index("strategy")
    assert "current runtime runs source_theorem before strategy" in _combined_docs()
    assert "source_theorem -> theorem" in dot
    assert "regrade -> strategy" in dot
    assert "after `examples/counterexamples attacker`" not in agent_flow
    assert "-> check if references match strategies" not in protocol
    assert "references -> theorem" not in dot


def test_time_allocation_trigger_and_order_are_v2() -> None:
    docs = _combined_docs()
    dot = _normalized("docs/diagrams/espresso_agent_flow.dot")

    assert "strategy generator alone does not start time allocation" in docs
    assert "strategy grader is the first time allocation trigger" in docs
    assert "time allocation is not only after proof, review, or grade" in docs
    assert "grader -> time" in dot
    assert "strategy -> time" not in dot


def test_docs_preserve_status_boundary_language() -> None:
    docs = _combined_docs()

    for required in (
        "a source candidate is not source-backed",
        "theorem applicability does not prove a route or theorem",
        "a supported/applicable proof step does not prove a route or theorem",
        "human strategy preference is optional steering, not evidence",
        "reviewer approval does not prove a theorem",
        "memory is advisory, not evidence",
        "update inbox is scheduling-only and advisory-only",
    ):
        assert required in docs


def test_diagram_contains_v2_boundary_nodes() -> None:
    dot = _normalized("docs/diagrams/espresso_agent_flow.dot")

    for required in (
        "claim clear?",
        "no downstream swarm",
        "source candidates only",
        "not source-backed",
        "runtime before strategy",
        "not mandatory",
        "pending pressure",
        "is not evidence",
        "first time allocation trigger",
        "update inbox",
        "scheduling-only",
        "advisory-only",
        "optional human",
        "steering, not evidence",
        "cannot prove route/theorem",
        "not proof completion",
        "advisory, not evidence",
    ):
        assert required in dot
