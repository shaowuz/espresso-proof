"""On-Demand + Parallel Evidence Agents V1.

These tests pin the fast core pass, the per-agent status board, the single-agent
on-demand runner, parallel/independent execution, and the honest dashboard/memo
rendering. The recurring invariant: agent run-status is process audit only and
never upgrades any proof/source/evidence/grade/claim status -- the kernel
(``validate_run``) remains the gate, and a running/failed/skipped/missing agent
never counts as evidence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import espresso_proof.orchestration.agent_runner as agent_runner
from espresso_proof.kernel import agent_run_status as ars
from espresso_proof.orchestration.agent_runner import run_single_agent, skip_agent
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.kernel.models import AgentRunStatusEntry
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.kernel.validators import KernelValidationError, validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _fast_run(tmp_path: Path, name: str = "p2") -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project=name,
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return result["run_dir"]


def _sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else "MISSING"


# --------------------------------------------------------------------------- #
# Fast core pass
# --------------------------------------------------------------------------- #


def test_fast_core_pass_is_valid_without_evidence_agents(tmp_path: Path) -> None:
    """The fast core pass reaches the human gate, validates, and marks the
    evidence agents not_run (no examples/counterexamples/literature enrichment)."""
    run_dir = _fast_run(tmp_path)
    validate_run(run_dir)
    board = ars.load_agent_run_status(run_dir)
    assert board is not None
    by_agent = {e["agent"]: e["status"] for e in board["agents"]}
    assert by_agent["claim_understanding"] == "done"
    for agent in ("literature", "examples", "counterexamples", "failure_patterns"):
        assert by_agent[agent] == "not_run"
    # scaffolded evidence artifacts are empty (not enriched).
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    arts = manifest["artifacts"]
    assert not (read_yaml(run_dir / arts["examples"]).get("examples") or [])
    assert not (read_yaml(run_dir / arts["counterexamples"]).get("tests") or [])


def test_fast_core_routes_marked_preliminary(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    assert ars.evidence_incomplete(run_dir) is True
    memo = build_concise_memo(run_dir)
    assert "PRELIMINARY" in memo
    html = render_run_dashboard(run_dir)
    assert "preliminary" in html.lower()


# --------------------------------------------------------------------------- #
# Dashboard / memo honesty
# --------------------------------------------------------------------------- #


def test_dashboard_shows_missing_agents_as_not_run(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    html = render_run_dashboard(run_dir)
    assert "Evidence Agents" in html
    assert "Not run" in html
    # an unfinished agent must not be rendered as a finished success.
    assert "examples survived" not in html.lower()
    assert "no counterexamples found" not in html.lower()


def test_done_card_note_does_not_overclaim(tmp_path: Path) -> None:
    """A finished evidence agent's card must not read as mathematical verification
    (an examples/counterexamples artifact can be Done yet empty)."""
    run_dir = _fast_run(tmp_path)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir)
    assert "its artifact is checked by the kernel" not in html
    assert "not a proof" in html.lower()
    # the actual evidence line stays honest even with an empty Done artifact.
    assert "examples survived" not in html.lower()


def test_memo_does_not_claim_unfinished_evidence(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    memo = build_concise_memo(run_dir).lower()
    assert "not run" in memo
    assert "examples survived" not in memo
    assert "no counterexamples found" not in memo


# --------------------------------------------------------------------------- #
# Single-agent runner: isolation
# --------------------------------------------------------------------------- #


def test_running_examples_writes_only_examples(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    ce0 = _sha(run_dir / "counterexamples.yaml")
    lit0 = _sha(run_dir / "literature_review.yaml")
    out = run_single_agent(run_dir, "examples")
    assert out["status"] == "done"
    assert ars.agent_status(run_dir, "examples") == "done"
    # the OTHER evidence artifacts are byte-unchanged.
    assert _sha(run_dir / "counterexamples.yaml") == ce0
    assert _sha(run_dir / "literature_review.yaml") == lit0
    # other agents stay not_run.
    assert ars.agent_status(run_dir, "counterexamples") == "not_run"
    assert ars.agent_status(run_dir, "literature") == "not_run"
    validate_run(run_dir)


def test_running_counterexamples_writes_only_counterexamples(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    ex0 = _sha(run_dir / "examples.yaml")
    lit0 = _sha(run_dir / "literature_review.yaml")
    out = run_single_agent(run_dir, "counterexamples")
    assert out["status"] == "done"
    assert ars.agent_status(run_dir, "counterexamples") == "done"
    assert _sha(run_dir / "examples.yaml") == ex0
    assert _sha(run_dir / "literature_review.yaml") == lit0
    validate_run(run_dir)


def test_running_literature_grants_no_source_support(tmp_path: Path) -> None:
    """Running literature enriches only literature_review (candidate-only). It must
    NOT grant source_backed support -- that is earned by grounding, not literature."""
    run_dir = _fast_run(tmp_path)
    ex0 = _sha(run_dir / "examples.yaml")
    ce0 = _sha(run_dir / "counterexamples.yaml")
    out = run_single_agent(run_dir, "literature")
    assert out["status"] == "done"
    assert _sha(run_dir / "examples.yaml") == ex0
    assert _sha(run_dir / "counterexamples.yaml") == ce0
    validate_run(run_dir)
    # no step is source_backed purely from running the literature agent.
    sg = read_yaml(run_dir / "source_grounding.yaml")
    statuses = {s.get("status") for s in sg.get("steps", [])}
    assert "source_backed" not in statuses


# --------------------------------------------------------------------------- #
# Fail closed / retries / skip
# --------------------------------------------------------------------------- #


def test_failed_agent_preserves_previous_valid_artifact(tmp_path: Path, monkeypatch) -> None:
    run_dir = _fast_run(tmp_path)
    run_single_agent(run_dir, "examples")
    good = _sha(run_dir / "examples.yaml")

    def boom(run_dir, agent, *, client):  # noqa: ANN001
        write_yaml(Path(run_dir) / "examples.yaml", {"claim_id": "BOGUS", "examples": []})
        raise RuntimeError("simulated agent crash")

    monkeypatch.setattr(agent_runner, "_run_agent_body", boom)
    out = run_single_agent(run_dir, "examples")
    assert out["status"] == "failed"
    # the previous valid artifact is intact and the run still validates.
    assert _sha(run_dir / "examples.yaml") == good
    validate_run(run_dir)
    board = ars.load_agent_run_status(run_dir)
    entry = next(e for e in board["agents"] if e["agent"] == "examples")
    assert entry["status"] == "failed"
    assert entry["attempts"] == 2  # one good + one failed run recorded
    assert entry["error_summary"]


def test_skipped_agent_contributes_no_evidence(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    skip_agent(run_dir, "counterexamples", reason="not needed")
    assert ars.agent_status(run_dir, "counterexamples") == "skipped"
    assert ars.evidence_ready(run_dir, "counterexamples") is False
    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "Skipped" in memo


# --------------------------------------------------------------------------- #
# Order independence
# --------------------------------------------------------------------------- #


def test_completion_order_does_not_matter(tmp_path: Path) -> None:
    run_a = _fast_run(tmp_path, "ord_a")
    run_b = _fast_run(tmp_path, "ord_b")
    for agent in ("examples", "counterexamples", "literature", "failure_patterns"):
        run_single_agent(run_a, agent)
    for agent in ("failure_patterns", "literature", "counterexamples", "examples"):
        run_single_agent(run_b, agent)
    validate_run(run_a)
    validate_run(run_b)
    status_a = {e["agent"]: e["status"] for e in ars.load_agent_run_status(run_a)["agents"]}
    status_b = {e["agent"]: e["status"] for e in ars.load_agent_run_status(run_b)["agents"]}
    assert status_a == status_b
    assert all(v == "done" for k, v in status_a.items())
    assert ars.evidence_incomplete(run_a) is False
    assert ars.evidence_incomplete(run_b) is False


# --------------------------------------------------------------------------- #
# Continue-this-route works before optional evidence finishes
# --------------------------------------------------------------------------- #


def test_continue_route_works_before_evidence_finishes(tmp_path: Path) -> None:
    from espresso_proof.orchestration.orchestrator import resume_run

    run_dir = _fast_run(tmp_path)
    # no evidence agent has run; continue the (preliminary) route anyway.
    memo = resume_run(run_dir=run_dir, selected_strategy="none", effort="quick_test")
    validate_run(run_dir)
    # the memo still clearly marks the missing evidence agents.
    assert "Not run" in memo
    assert "Evidence agents:" in memo


# --------------------------------------------------------------------------- #
# Safety: status board cannot upgrade any mathematical status
# --------------------------------------------------------------------------- #


def test_status_board_rejects_status_upgrade_fields(tmp_path: Path) -> None:
    """A board that smuggles a mathematical status field is rejected at the model
    (StrictBaseModel forbids extras)."""
    base = {
        "agent": "examples",
        "status": "done",
        "last_updated": "2026-06-10T00:00:00+00:00",
    }
    for bad in ("source_backed", "proof_status", "computation_checked", "refutes_claim", "grade"):
        with pytest.raises(Exception):
            AgentRunStatusEntry.model_validate({**base, bad: True})


def test_safe_to_use_as_evidence_requires_done(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        AgentRunStatusEntry.model_validate(
            {
                "agent": "examples",
                "status": "running",
                "last_updated": "2026-06-10T00:00:00+00:00",
                "safe_to_use_as_evidence": True,
            }
        )


def test_board_does_not_change_grade_or_proof_status(tmp_path: Path) -> None:
    """Running every evidence agent must not upgrade the grade or proof status."""
    run_dir = _fast_run(tmp_path)
    grade0 = read_yaml(run_dir / "idea_grade.yaml")
    for agent in ("examples", "counterexamples", "literature", "failure_patterns"):
        run_single_agent(run_dir, agent)
    grade1 = read_yaml(run_dir / "idea_grade.yaml")
    assert grade1.get("status") == grade0.get("status")
    assert grade1.get("score") == grade0.get("score")
    assert grade1.get("proof_language_status") == grade0.get("proof_language_status")


def test_validator_rejects_safe_evidence_with_missing_artifact(tmp_path: Path) -> None:
    """If the board claims an agent is safe_to_use_as_evidence but its artifact is
    gone, validate_run fails closed."""
    run_dir = _fast_run(tmp_path)
    run_single_agent(run_dir, "examples")
    board = read_yaml(run_dir / "agent_run_status.yaml")
    for entry in board["agents"]:
        if entry["agent"] == "examples":
            entry["artifact_path"] = "does_not_exist.yaml"
            entry["safe_to_use_as_evidence"] = True
    write_yaml(run_dir / "agent_run_status.yaml", board)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)
