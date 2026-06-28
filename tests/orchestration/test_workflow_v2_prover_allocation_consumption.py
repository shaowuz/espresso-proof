from __future__ import annotations

from pathlib import Path

import pytest

import espresso_proof.orchestration.prover_loop_driver as drv
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.agents.llm_agents import run_llm_agent_pass
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.orchestration.orchestrator import _resolve_and_build_loop, orchestrate_run
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run
from tests.kernel.test_evidence_aware_grade import FakeClaude


CLAIM = "Every group of order p^2, where p is a prime, is abelian."


class _ProverClient:
    provider = "stub"
    model = "stub-prover"

    def __init__(self, *, objections: list[str]) -> None:
        self._objections = objections

    def complete_json(self, *, system: str, prompt: str) -> dict:
        if "reviewer" in system.lower() or "verifier" in system.lower():
            return {
                "summary": "review",
                "steps": ["check candidate route"],
                "source_requests": list(self._objections),
                "missing_hypotheses": [],
                "risks": list(self._objections),
                "verdict": "needs_sources",
                "recommended_human_action": "revise",
            }
        return {
            "summary": "candidate route step",
            "steps": ["state the route as a conditional argument"],
            "source_requests": ["exact locator for the key lemma"],
            "missing_hypotheses": [],
            "risks": ["candidate route"],
            "verdict": "candidate",
            "recommended_human_action": "revise",
        }


def _write_claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _run_to_prover_gate(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="task06a",
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    return run_dir


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


def _first_round_path(run_dir: Path) -> Path:
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    return run_dir / index["rounds"][0]["artifact_path"]


def _append_prover_step(run_dir: Path, text: str) -> None:
    path = _first_round_path(run_dir)
    report = read_yaml(path)
    report["prover_attempt"]["proposed_steps"].append(text)
    write_yaml(path, report)
    agent_path = run_dir / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["prover_attempt"]["proposed_steps"].append(text)
    write_yaml(agent_path, agent)


def _append_reviewer_objection(run_dir: Path, text: str) -> None:
    path = _first_round_path(run_dir)
    report = read_yaml(path)
    report["verifier_reviews"][0]["objections"].append(text)
    write_yaml(path, report)


def _resolve_for_append(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    selected = brief["top_strategies"][0]["id"]
    human_choice = {
        "claim_id": claim["id"],
        "status": "selected",
        "selected_strategy": selected,
        "effort": "normal_pass",
        "taste_judgment": "",
        "continue_decision": "continue",
        "human_confirmed": True,
    }
    write_yaml(run_dir / artifacts["human_choice"], human_choice)
    _resolve_and_build_loop(
        run_dir,
        decision="continue",
        reason="task06a test",
        human_choice=human_choice,
    )
    validate_run(run_dir)
    return selected


def test_prover_starts_from_time_allocation_plan_and_records_missing_context(
    tmp_path: Path,
) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent = read_yaml(run_dir / "llm_agent_run.yaml")
    round_report = read_yaml(_first_round_path(run_dir))

    assert manifest["optional_artifacts"]["time_allocation"] == "time_allocation.yaml"
    assert "time_allocation.yaml" in agent["input_artifacts"]
    assert "time_allocation.yaml" in agent["prover_attempt"]["input_artifacts"]
    assert "time_allocation.yaml" in round_report["input_artifacts"]
    assert any(
        "Missing source context" in item
        for item in agent["prover_attempt"]["source_requests"]
    )
    assert any(
        "Missing theorem context" in item
        for item in agent["prover_attempt"]["source_requests"]
    )
    assert any(
        "Unchecked examples cannot count" in item
        for item in agent["prover_attempt"]["risks"]
    )


def test_raw_strategy_generator_output_alone_cannot_start_prover(tmp_path: Path) -> None:
    claim = _write_claim(tmp_path)
    run_dir = run_mock_critique("task06a_raw", claim, tmp_path)
    _mark_claim_clear(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.get("optional_artifacts", {}).pop("time_allocation", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    time_path = run_dir / "time_allocation.yaml"
    if time_path.exists():
        time_path.unlink()
    write_yaml(
        run_dir / manifest["artifacts"]["strategy_brief"],
        {
            "claim_id": "claim_001",
            "generated_after_runs": [manifest["run_id"]],
            "top_strategies": [{"id": "raw_route_001", "title": "Raw route"}],
            "question_for_mathematician": "Raw route?",
            "allowed_human_choices": [
                {"strategy_id": "raw_route_001", "effort_options": ["normal_pass"]},
                {"strategy_id": "none", "effort_options": ["quick_test"]},
            ],
        },
    )
    write_yaml(
        run_dir / manifest["artifacts"]["strategy_synthesis"],
        {
            "claim_id": "claim_001",
            "synthesized_from": ["generator_only"],
            "routes": [{"id": "raw_route_001", "title": "Raw route"}],
            "synthesis_note": "Generator-only output with no grader fields.",
        },
    )

    with pytest.raises(KernelValidationError, match="time_allocation.yaml"):
        run_llm_agent_pass(run_dir=run_dir, provider="stub", model="stub")


def test_prover_proof_complete_language_is_downgraded_or_rejected(tmp_path: Path) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    agent = read_yaml(run_dir / "llm_agent_run.yaml")
    leaked = " ".join(
        [agent["prover_attempt"]["output_summary"], *agent["prover_attempt"]["proposed_steps"]]
    ).lower()

    assert "proof-close" not in leaked
    assert "proof is complete" not in leaked

    _append_prover_step(run_dir, "This proof is complete. proof-close.")
    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "text,match",
    [
        ("An unchecked citation supports the proof candidate.", "unchecked citation"),
        ("An unchecked example supports the proof candidate.", "unchecked example"),
        (
            "Pending theorem applicability supports the proof candidate.",
            "pending theorem applicability",
        ),
    ],
)
def test_unchecked_context_cannot_support_proof(
    tmp_path: Path,
    text: str,
    match: str,
) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    _append_prover_step(run_dir, text)

    with pytest.raises(KernelValidationError, match=match):
        validate_run(run_dir)


def test_unchecked_context_boundary_disclaimer_can_mention_support(tmp_path: Path) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    _append_prover_step(
        run_dir,
        "Unchecked examples and pressure tests do not contribute line-level support "
        "for the proof candidate.",
    )

    validate_run(run_dir)


def test_source_misuse_routes_to_source_repair_not_proof(tmp_path: Path) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    selected = _resolve_for_append(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    attempt = next(item for item in loop["attempts"] if item["strategy_id"] == selected)
    grade = read_yaml(run_dir / artifacts["idea_grade"])

    assert attempt["time_allocation"] == "repair"
    assert attempt["status"] in {"candidate", "needs_repair"}
    assert grade["proof_language_status"] == "idea_sketch"
    assert grade["status"] != "refuted"

    _append_prover_step(run_dir, "source_status: source_backed")
    with pytest.raises(KernelValidationError, match="source_status"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "text,where,match",
    [
        ("This route is source_backed.", "prover", "source_backed"),
        ("source_backed: true", "prover", "source_backed"),
        ("proof_language_status: computation_checked", "prover", "proof_language_status"),
        ("The proof_language_status is computation_checked.", "prover", "proof_language_status"),
        ("The proof status is computation checked.", "prover", "proof_status"),
        ("The source_backed_status is true.", "prover", "source_backed_status"),
        ("The grade_status is promising.", "prover", "grade_status"),
        ("theorem_applicability: applicable", "prover", "theorem_applicability"),
        ("counterexample_refutes_claim: true", "prover", "counterexample_refutes_claim"),
        ("The theorem applicability status is applicable.", "reviewer", "theorem_applicability"),
        ("Checked evidence supports this route.", "reviewer", "checked_evidence"),
        ("Examples survived, so the route is supported.", "reviewer", "example_status"),
        ("No counterexamples found, so there is no pressure.", "reviewer", "counterexample_pressure"),
        ("A counterexample refutes the claim.", "reviewer", "counterexample_refutes_claim"),
    ],
)
def test_prover_and_reviewer_text_cannot_certify_owned_statuses(
    tmp_path: Path,
    text: str,
    where: str,
    match: str,
) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    if where == "prover":
        _append_prover_step(run_dir, text)
    else:
        _append_reviewer_objection(run_dir, text)

    with pytest.raises(KernelValidationError, match=match):
        validate_run(run_dir)


def test_unchecked_support_negation_does_not_mask_later_assertion(tmp_path: Path) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    _append_prover_step(
        run_dir,
        "Unchecked citation cannot support anything. "
        "Unchecked citation supports the proof candidate.",
    )

    with pytest.raises(KernelValidationError, match="unchecked citation"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "text,match",
    [
        ("This is not evidence, source_backed: true", "source_backed"),
        (
            "This is not a proof, proof_language_status: computation_checked",
            "proof_language_status",
        ),
        (
            "Unchecked citation cannot support anything, unchecked citation supports the proof candidate.",
            "unchecked citation",
        ),
        (
            "Pending theorem applicability cannot support anything, "
            "pending theorem applicability supports the proof candidate.",
            "pending theorem applicability",
        ),
    ],
)
def test_comma_disclaimers_do_not_mask_later_status_assertions(
    tmp_path: Path,
    text: str,
    match: str,
) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    _append_prover_step(run_dir, text)

    with pytest.raises(KernelValidationError, match=match):
        validate_run(run_dir)


def test_failed_append_rolls_back_generated_time_allocation_for_old_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    _resolve_for_append(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.get("optional_artifacts", {}).pop("time_allocation", None)
    manifest.get("dependencies", {}).pop("time_allocation", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    allocation_path = run_dir / "time_allocation.yaml"
    if allocation_path.exists():
        allocation_path.unlink()
    inbox_path = run_dir / "update_inbox.yaml"
    if inbox_path.exists():
        inbox = read_yaml(inbox_path)
        for entry in inbox.get("entries", []):
            entry["applied_at"] = None
        write_yaml(inbox_path, inbox)
        refresh_final_report_disclosures(run_dir)
    agent_path = run_dir / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["input_artifacts"] = [
        item for item in agent["input_artifacts"] if item != "time_allocation.yaml"
    ]
    agent["prover_attempt"]["input_artifacts"] = [
        item
        for item in agent["prover_attempt"]["input_artifacts"]
        if item != "time_allocation.yaml"
    ]
    write_yaml(agent_path, agent)
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    for entry in index["rounds"]:
        round_path = run_dir / entry["artifact_path"]
        report = read_yaml(round_path)
        report["input_artifacts"] = [
            item for item in report["input_artifacts"] if item != "time_allocation.yaml"
        ]
        report["prover_attempt"]["input_artifacts"] = [
            item
            for item in report["prover_attempt"]["input_artifacts"]
            if item != "time_allocation.yaml"
        ]
        write_yaml(round_path, report)
    validate_run(run_dir)

    real_validate = drv.validate_run
    calls = {"n": 0}

    def flaky(rd: Path) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KernelValidationError("injected post-append validation failure")
        real_validate(rd)

    monkeypatch.setattr(drv, "validate_run", flaky)
    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ProverClient(objections=["exact source missing"]),
    )

    assert result["stop_reason"] == "validation_error"
    assert result["rounds_run"] == 0
    real_validate(run_dir)
    after_manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "time_allocation" not in after_manifest.get("optional_artifacts", {})
    assert not allocation_path.exists()


def test_line_level_gap_keeps_proof_candidate_unverified(tmp_path: Path) -> None:
    run_dir = _run_to_prover_gate(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade = read_yaml(run_dir / manifest["artifacts"]["idea_grade"])
    round_report = read_yaml(_first_round_path(run_dir))

    assert round_report["status"] in {"candidate", "needs_repair", "waiting_human"}
    assert grade["proof_language_status"] == "idea_sketch"
    assert grade["status"] not in {"promising", "refuted"}

    _append_prover_step(run_dir, "A line-level gap supports the proof candidate.")
    with pytest.raises(KernelValidationError, match="line-level gap"):
        validate_run(run_dir)
