from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.literature.autonomous_retrieval import run_autonomous_retrieval
from espresso_proof.cli import app
from espresso_proof.claim.claim_clear import ClaimClearBlockedError, ensure_claim_clear_for_downstream
from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.kernel import snapshot as kernel_snapshot
import espresso_proof.agents.llm_agents as llm_agents
from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.literature.retrieval_grounding import retrieve_and_ground
from espresso_proof.orchestration.run_dashboard import (
    render_run_dashboard,
    render_topbar_status_html,
    status_summary,
)
from espresso_proof.ui.cockpit_view import build_snapshot
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
from espresso_proof.kernel.validators import validate_run
from espresso_proof.verification.verifier import audit_evidence


UNCLEAR_CLAIM = "Prove the thing about the nice mirror object."
CLEAR_CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _claim_file(tmp_path: Path, text: str, name: str = "claim.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _front_half_statuses(run_dir: Path) -> dict[str, str]:
    return {
        item["stage"]: item["status"]
        for item in read_yaml(run_dir / "front_half_run.yaml")["stages"]
    }


def _artifact_bytes(run_dir: Path, *artifact_keys: str) -> dict[str, bytes]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return {
        key: (run_dir / manifest["artifacts"][key]).read_bytes()
        for key in artifact_keys
    }


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


def _clear_run_with_recorded_gate(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, CLEAR_CLAIM, "clear.md"),
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def _stale_ledger_after_clear_gate(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["notes"] = [*ledger.get("notes", []), "Changed after claim-clear gate."]
    write_yaml(ledger_path, ledger)


def _first_strategy_id(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    return brief["top_strategies"][0]["id"]


def _write_theorem_extraction(run_dir: Path) -> Path:
    source = run_dir / "source.txt"
    source.write_text(
        "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
        "over base field C for cubic fourfolds.",
        encoding="utf-8",
    )
    claim = read_yaml(run_dir / "locked_claim.yaml")
    extraction = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(source),
        source_id="src_gate",
        title="Gate source",
        authors=["A. Author"],
        source_type="paper",
    )
    write_yaml(run_dir / "theorem_extraction.yaml", extraction)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return run_dir / "theorem_extraction.yaml"


def _write_machine_check(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    write_yaml(
        run_dir / manifest["artifacts"]["computation_checks"],
        {
            "claim_id": claim_id,
            "checks": [
                {
                    "id": "comp_gate",
                    "example_id": "ex_gate",
                    "claim_id": claim_id,
                    "computed_quantity": "2+2",
                    "input_data": [],
                    "method": "manual_review_needed",
                    "tool_or_source": "test fixture",
                    "result": "4",
                    "cross_check": [],
                    "status": "human_needed",
                    "machine_check": {
                        "kind": "exact_arithmetic",
                        "expression": "2+2",
                        "expected": "4",
                        "notes": "",
                    },
                    "notes": "",
                }
            ],
        },
    )


def _corpus_with_source(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "source.txt").write_text(
        "Theorem 1.1. A precise candidate theorem statement.", encoding="utf-8"
    )
    write_yaml(
        corpus / "source.meta.yaml",
        {
            "title": "Candidate source",
            "authors": ["A. Author"],
            "locator": {"kind": "theorem", "value": "Theorem 1.1"},
        },
    )
    return corpus


def test_unclear_claim_blocks_orchestrator_downstream_stages(tmp_path: Path) -> None:
    result = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, UNCLEAR_CLAIM),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )

    run_dir = result["run_dir"]
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert result["state"] == "WAITING_CLAIM_CLARIFICATION"
    assert manifest["current_state"] == "WAITING_CLAIM_CLARIFICATION"
    assert result["human_gate"]["status"] == "waiting_claim_clarification"

    gate = read_yaml(run_dir / "claim_clear_gate.yaml")
    assert gate["state"] == "WAITING_CLAIM_CLARIFICATION"
    assert gate["effect"] == "scheduling_gate_only"
    assert gate["mathematical_status_authority"] == "none"
    assert gate["blocking_reasons"]

    statuses = _front_half_statuses(run_dir)
    assert statuses["understanding_claim"] == "ok"
    for stage in ("literature_review", "examples", "counterexamples", "strategy"):
        assert statuses[stage] == "skipped"

    optional = manifest.get("optional_artifacts", {})
    assert "llm_agent_run" not in optional
    assert "prover_loop" not in optional
    assert "prover_round_index" not in optional
    assert not (run_dir / "llm_agent_run.yaml").exists()
    assert not (run_dir / "prover_loop.yaml").exists()
    assert not (run_dir / "prover_rounds").exists()

    board = read_yaml(run_dir / "agent_run_status.yaml")
    by_agent = {entry["agent"]: entry for entry in board["agents"]}
    for agent in ("literature", "examples", "counterexamples"):
        assert by_agent[agent]["status"] == "skipped"
        assert by_agent[agent]["safe_to_use_as_evidence"] is False


def test_unclear_claim_runtime_ui_kernel_and_vscode_snapshots_agree(
    tmp_path: Path,
) -> None:
    result = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, UNCLEAR_CLAIM),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    run_dir = Path(result["run_dir"])

    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    summary = status_summary(run_dir)
    dashboard = render_run_dashboard(run_dir)
    topbar = render_topbar_status_html(run_dir)
    cockpit = build_snapshot(run_dir, run_dir.name, summary)
    kernel = kernel_snapshot(tmp_path, run_dir)
    bridge_result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "snapshot",
            "--workspace",
            str(tmp_path),
            "--run",
            str(run_dir),
        ],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    vscode = json.loads(bridge_result.stdout)["payload"]

    assert manifest["current_state"] == "WAITING_CLAIM_CLARIFICATION"
    assert summary["state"] == "Waiting Claim Clarification"
    assert cockpit["status"]["state"] == summary["state"]
    assert kernel["current"]["validation"]["status"] == "valid"
    assert vscode["current"]["validation"]["status"] == "valid"
    assert kernel["current"]["artifacts"]["run_manifest"]["current_state"] == (
        "WAITING_CLAIM_CLARIFICATION"
    )
    assert vscode["current"]["artifacts"]["run_manifest"]["current_state"] == (
        "WAITING_CLAIM_CLARIFICATION"
    )

    agent_status = {row["agent"]: row["status"] for row in summary["agents"]}
    for agent in ("literature", "examples", "counterexamples"):
        assert agent_status[agent] == "skipped"
    optional = manifest.get("optional_artifacts", {})
    assert "llm_agent_run" not in optional
    assert "prover_loop" not in optional
    assert "prover_round_index" not in optional
    assert not (run_dir / "prover_rounds").exists()

    warning_categories = {item["category"] for item in summary["open_warnings"]}
    assert {"source_gaps", "examples_pending", "counterexamples_pending"}.issubset(
        warning_categories
    )
    assert cockpit["open_warnings"] == summary["open_warnings"]
    assert "Open blockers / pending checks" in dashboard
    assert "Waiting Claim Clarification" in topbar
    assert summary["evidence"] == {"reference_supported_steps": 0, "machine_checks": 0}
    for rendered in (
        dashboard,
        topbar,
        json.dumps(cockpit, sort_keys=True),
    ):
        assert "source_backed" not in rendered
        assert "checked_evidence" not in rendered


@pytest.mark.parametrize("stage", ["literature_review", "examples", "counterexamples", "strategy"])
def test_unclear_claim_blocks_direct_front_half_stage_selection(
    tmp_path: Path,
    stage: str,
) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    before = deepcopy(read_yaml(run_dir / artifacts["strategy_brief"]))

    report = run_front_half_agent_pass(
        run_dir=run_dir,
        client=StubLLMClient(),
        stages=[stage],
    )
    validate_run(run_dir)

    assert report["stages"] == [
        {
            "stage": stage,
            "status": "skipped",
            "validation": "not_run",
            "errors": report["stages"][0]["errors"],
            "unsafe_language": [],
        }
    ]
    assert "blocked by claim-clear gate" in report["stages"][0]["errors"][0]
    assert read_yaml(run_dir / artifacts["strategy_brief"]) == before
    manifest_after = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest_after["current_state"] == "WAITING_CLAIM_CLARIFICATION"
    board = read_yaml(run_dir / "agent_run_status.yaml")
    by_agent = {entry["agent"]: entry for entry in board["agents"]}
    for agent in ("literature", "examples", "counterexamples"):
        assert by_agent[agent]["status"] == "skipped"
        assert by_agent[agent]["safe_to_use_as_evidence"] is False


def test_run_flow_cannot_bypass_claim_clear_gate(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(_claim_file(tmp_path, UNCLEAR_CLAIM)),
            "--project",
            "v2_claim_gate",
            "--provider",
            "stub",
            "--front-half",
            "stub",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.splitlines()[0].split("Wrote run artifacts to ")[-1])
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "WAITING_CLAIM_CLARIFICATION"
    assert "Human gate: waiting_claim_clarification" in result.output
    assert not (run_dir / "llm_agent_run.yaml").exists()


def test_human_confirmed_cannot_override_unclear_claim(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["human_confirmed"] = True
    write_yaml(claim_path, claim)

    result = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, UNCLEAR_CLAIM, "again.md"),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        run_dir=run_dir,
    )

    validate_run(run_dir)
    assert result["state"] == "WAITING_CLAIM_CLARIFICATION"
    gate = read_yaml(run_dir / "claim_clear_gate.yaml")
    assert "hypothesis_ledger.claim_precision is not precise" in gate["blocking_reasons"]


@pytest.mark.parametrize(
    ("agent", "artifact_keys"),
    [
        ("literature", ("literature_review",)),
        ("examples", ("examples", "computation_checks", "tests")),
        ("counterexamples", ("counterexamples", "tests")),
    ],
)
def test_unclear_claim_blocks_on_demand_agents_without_mutating_artifacts(
    tmp_path: Path,
    agent: str,
    artifact_keys: tuple[str, ...],
) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    before = _artifact_bytes(run_dir, *artifact_keys)

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        run_single_agent(run_dir, agent, provider="stub")

    validate_run(run_dir)
    assert _artifact_bytes(run_dir, *artifact_keys) == before
    board = read_yaml(run_dir / "agent_run_status.yaml")
    by_agent = {entry["agent"]: entry for entry in board["agents"]}
    assert by_agent[agent]["status"] == "skipped"
    assert by_agent[agent]["safe_to_use_as_evidence"] is False


def test_run_agent_cli_cannot_bypass_claim_clear_gate(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    before = _artifact_bytes(run_dir, "examples", "computation_checks", "tests")

    result = CliRunner().invoke(
        app,
        ["run-agent", "--run", str(run_dir), "--agent", "examples", "--provider", "stub"],
    )

    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    validate_run(run_dir)
    assert _artifact_bytes(run_dir, "examples", "computation_checks", "tests") == before


def test_discover_literature_cli_cannot_bypass_claim_clear_gate(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    before = _artifact_bytes(run_dir, "literature_review")

    result = CliRunner().invoke(
        app,
        ["discover-literature", "--run", str(run_dir), "--mode", "local"],
    )

    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    validate_run(run_dir)
    assert _artifact_bytes(run_dir, "literature_review") == before


def test_unclear_claim_blocks_direct_llm_agent_pass_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    called = False

    def fake_build_client(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        nonlocal called
        called = True
        raise AssertionError("client should not be built")

    monkeypatch.setattr(llm_agents, "build_client", fake_build_client)

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        llm_agents.run_llm_agent_pass(
            run_dir=run_dir,
            provider="stub",
            model="stub-espresso-agent",
        )

    assert called is False
    assert not (run_dir / "llm_agent_run.yaml").exists()
    assert not (run_dir / "prover_loop.yaml").exists()


def test_run_llm_agents_cli_cannot_bypass_claim_clear_gate(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )

    result = CliRunner().invoke(
        app,
        ["run-llm-agents", "--run", str(run_dir), "--provider", "stub"],
    )

    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    validate_run(run_dir)
    assert not (run_dir / "llm_agent_run.yaml").exists()
    assert not (run_dir / "prover_loop.yaml").exists()
    assert not (run_dir / "prover_rounds").exists()


def test_changed_claim_revision_blocks_downstream_reuse(tmp_path: Path) -> None:
    first = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, CLEAR_CLAIM),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = first["run_dir"]
    validate_run(run_dir)
    assert first["state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    assert (run_dir / "llm_agent_run.yaml").exists()

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["normalized_statement"] = claim["normalized_statement"] + " Changed after gate."
    write_yaml(claim_path, claim)

    second = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, CLEAR_CLAIM, "clear-again.md"),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        run_dir=run_dir,
    )

    validate_run(run_dir)
    assert second["state"] == "WAITING_CLAIM_CLARIFICATION"
    gate = read_yaml(run_dir / "claim_clear_gate.yaml")
    assert "locked_claim changed after claim-clear gate" in gate["blocking_reasons"]
    board = read_yaml(run_dir / "agent_run_status.yaml")
    by_agent = {entry["agent"]: entry for entry in board["agents"]}
    for agent in ("literature", "examples", "counterexamples"):
        assert by_agent[agent]["status"] == "skipped"
        assert by_agent[agent]["safe_to_use_as_evidence"] is False


def test_changed_hypothesis_ledger_blocks_resume_and_direct_prover_paths(
    tmp_path: Path,
) -> None:
    first = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, CLEAR_CLAIM),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = first["run_dir"]
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["notes"] = [*ledger.get("notes", []), "Changed after claim clear."]
    write_yaml(ledger_path, ledger)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        resume_run(
            run_dir=run_dir,
            selected_strategy=_first_strategy_id(run_dir),
            effort="quick_test",
        )
    validate_run(run_dir)
    assert read_yaml(run_dir / "run_manifest.yaml")["current_state"] == "WAITING_CLAIM_CLARIFICATION"

    report = read_yaml(run_dir / "llm_agent_run.yaml")
    assert report["human_gate"]["status"] != "resolved"
    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        llm_agents.resolve_agent_gate(
            run_dir=run_dir,
            decision="continue",
            reason="should be blocked",
        )
    assert read_yaml(run_dir / "llm_agent_run.yaml")["human_gate"]["status"] != "resolved"

    index_before = (run_dir / "prover_rounds" / "index.yaml").read_bytes()
    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        continue_prover_loop(run_dir=run_dir, provider="stub", max_rounds=1)
    assert (run_dir / "prover_rounds" / "index.yaml").read_bytes() == index_before


def test_stale_claim_blocks_resume_cli_and_loop_commands(tmp_path: Path) -> None:
    first = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, CLEAR_CLAIM),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = first["run_dir"]
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["normalized_statement"] = claim["normalized_statement"] + " Changed after gate."
    write_yaml(claim_path, claim)

    resume_result = CliRunner().invoke(
        app,
        [
            "orchestrate",
            "--resume",
            str(run_dir),
            "--select-strategy",
            _first_strategy_id(run_dir),
            "--effort",
            "quick_test",
        ],
    )
    assert resume_result.exit_code != 0
    assert "claim-clear gate" in resume_result.output

    gate_result = CliRunner().invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "continue",
            "--reason",
            "should be blocked",
        ],
    )
    assert gate_result.exit_code != 0
    assert "claim-clear gate" in gate_result.output

    round_index_before = (run_dir / "prover_rounds" / "index.yaml").read_bytes()
    round_result = CliRunner().invoke(
        app,
        ["run-prover-loop-round", "--run", str(run_dir), "--provider", "stub"],
    )
    assert round_result.exit_code != 0
    assert "claim-clear gate" in round_result.output
    assert (run_dir / "prover_rounds" / "index.yaml").read_bytes() == round_index_before


def test_unclear_claim_blocks_verify_sources_and_ground_run_status_writes(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    extraction_path = _write_theorem_extraction(run_dir)
    source_before = (run_dir / "source_grounding.yaml").read_bytes()

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        ground_run(run_dir=run_dir, theorem_extraction_path=extraction_path)

    assert (run_dir / "source_grounding.yaml").read_bytes() == source_before
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)

    result = CliRunner().invoke(app, ["verify-sources", "--run", str(run_dir)])
    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    assert (run_dir / "source_grounding.yaml").read_bytes() == source_before
    assert not (run_dir / "theorem_applicability.yaml").exists()


def test_stale_claim_blocks_ground_run_reuse_before_theorem_read(tmp_path: Path) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    source_before = (run_dir / "source_grounding.yaml").read_bytes()
    _stale_ledger_after_clear_gate(run_dir)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        ground_run(run_dir=run_dir, theorem_extraction_path=tmp_path / "missing.yaml")

    assert (run_dir / "source_grounding.yaml").read_bytes() == source_before
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)


def test_unclear_claim_blocks_retrieve_and_ground_status_writes(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    source_before = (run_dir / "source_grounding.yaml").read_bytes()
    source = tmp_path / "source.txt"
    source.write_text("Theorem 1.1. An exact statement.", encoding="utf-8")

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        retrieve_and_ground(
            run_dir=run_dir,
            local_source=source,
            title="Gate source",
            authors=["A. Author"],
        )

    assert (run_dir / "source_grounding.yaml").read_bytes() == source_before
    assert not (run_dir / "retrieval_resolution.yaml").exists()
    assert not (run_dir / "theorem_extraction.yaml").exists()
    validate_run(run_dir)


def test_stale_claim_blocks_retrieve_and_ground_before_resolution_write(tmp_path: Path) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    source_before = (run_dir / "source_grounding.yaml").read_bytes()
    _stale_ledger_after_clear_gate(run_dir)
    source = tmp_path / "source.txt"
    source.write_text("Theorem 1.1. An exact statement.", encoding="utf-8")

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        retrieve_and_ground(
            run_dir=run_dir,
            local_source=source,
            title="Gate source",
            authors=["A. Author"],
        )

    assert (run_dir / "source_grounding.yaml").read_bytes() == source_before
    assert not (run_dir / "retrieval_resolution.yaml").exists()
    assert not (run_dir / "theorem_extraction.yaml").exists()
    validate_run(run_dir)


def test_unclear_claim_blocks_audit_evidence_checked_status(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    _write_machine_check(run_dir)
    checks_before = (run_dir / "computation_checks.yaml").read_bytes()

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        audit_evidence(run_dir)

    assert (run_dir / "computation_checks.yaml").read_bytes() == checks_before
    assert not (run_dir / "computation_verification.yaml").exists()
    validate_run(run_dir)


def test_stale_claim_blocks_audit_evidence_before_verification_write(tmp_path: Path) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    _write_machine_check(run_dir)
    checks_before = (run_dir / "computation_checks.yaml").read_bytes()
    _stale_ledger_after_clear_gate(run_dir)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        audit_evidence(run_dir)

    assert (run_dir / "computation_checks.yaml").read_bytes() == checks_before
    assert not (run_dir / "computation_verification.yaml").exists()
    validate_run(run_dir)


@pytest.mark.parametrize("ground", [False, True])
def test_unclear_claim_blocks_legacy_retrieve_sources_before_writes(
    tmp_path: Path,
    ground: bool,
) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )
    args = ["retrieve-sources", "--run", str(run_dir), "--corpus", str(_corpus_with_source(tmp_path))]
    if ground:
        args.append("--ground")

    result = CliRunner().invoke(app, args)

    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    assert not (run_dir / "literature_retrieval.yaml").exists()
    assert not (run_dir / "theorem_extraction.yaml").exists()
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)


@pytest.mark.parametrize("ground", [False, True])
def test_stale_claim_blocks_legacy_retrieve_sources_before_writes(
    tmp_path: Path,
    ground: bool,
) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    _stale_ledger_after_clear_gate(run_dir)
    args = ["retrieve-sources", "--run", str(run_dir), "--corpus", str(_corpus_with_source(tmp_path))]
    if ground:
        args.append("--ground")

    result = CliRunner().invoke(app, args)

    assert result.exit_code != 0
    assert "hypothesis_ledger changed" in result.output
    assert not (run_dir / "literature_retrieval.yaml").exists()
    assert not (run_dir / "theorem_extraction.yaml").exists()
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)


def test_unclear_claim_blocks_autonomous_retrieval_before_planning(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        run_autonomous_retrieval(
            run_dir,
            client=StubLLMClient(),
            corpus_dir=_corpus_with_source(tmp_path),
            providers=["local_corpus"],
        )

    assert not (run_dir / "literature_search_plan.yaml").exists()
    assert not (run_dir / "autonomous_literature_retrieval.yaml").exists()
    assert not (run_dir / "literature_retrieval.yaml").exists()
    validate_run(run_dir)


def test_stale_claim_blocks_autonomous_retrieval_before_planning(tmp_path: Path) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    _stale_ledger_after_clear_gate(run_dir)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        run_autonomous_retrieval(
            run_dir,
            client=StubLLMClient(),
            corpus_dir=_corpus_with_source(tmp_path),
            providers=["local_corpus"],
        )

    assert not (run_dir / "literature_search_plan.yaml").exists()
    assert not (run_dir / "autonomous_literature_retrieval.yaml").exists()
    assert not (run_dir / "literature_retrieval.yaml").exists()
    validate_run(run_dir)


def test_stale_claim_blocks_direct_prover_round_append_before_client(tmp_path: Path) -> None:
    first = orchestrate_run(
        project="v2_claim_gate",
        input_path=_claim_file(tmp_path, CLEAR_CLAIM, "clear-orchestrated.md"),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = first["run_dir"]
    index_path = run_dir / "prover_rounds" / "index.yaml"
    index_before = index_path.read_bytes()
    _stale_ledger_after_clear_gate(run_dir)
    called = False

    class _TrackingClient(StubLLMClient):
        def complete_json(self, *, system: str, prompt: str) -> dict:
            nonlocal called
            called = True
            return super().complete_json(system=system, prompt=prompt)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        llm_agents.append_prover_loop_round(
            run_dir=run_dir,
            provider="stub",
            model="stub-espresso-agent",
            client=_TrackingClient(),
        )

    assert called is False
    assert index_path.read_bytes() == index_before
    validate_run(run_dir)


def _verification_command_args(run_dir: Path, tmp_path: Path, command: str) -> list[str]:
    table_path = tmp_path / "table.yaml"
    write_yaml(table_path, [[0, 1], [1, 0]])
    commands = {
        "verify-exact-modexp": [
            "verify-exact-modexp",
            "--run",
            str(run_dir),
            "--base",
            "2",
            "--exp",
            "10",
            "--mod",
            "1000",
            "--expected",
            "24",
            "--id",
            "modexp_gate",
        ],
        "verify-finite-groups": ["verify-finite-groups", "--run", str(run_dir), "--prime", "2"],
        "verify-identity-in-run": [
            "verify-identity-in-run",
            "--run",
            str(run_dir),
            "--table",
            str(table_path),
            "--identity",
            "commutative",
            "--id",
            "table_gate",
        ],
        "verify-ring-identity-in-run": [
            "verify-ring-identity-in-run",
            "--run",
            str(run_dir),
            "--identity",
            "freshmans_dream",
            "--modulus",
            "5",
            "--id",
            "ring_gate",
        ],
        "verify-matrix-rank-in-run": [
            "verify-matrix-rank-in-run",
            "--run",
            str(run_dir),
            "--matrix",
            "[[1,0],[0,1]]",
            "--id",
            "matrix_gate",
            "--expected-rank",
            "2",
        ],
        "auto-wire-verifications": ["auto-wire-verifications", "--run", str(run_dir)],
    }
    return commands[command]


@pytest.mark.parametrize(
    "command",
    [
        "verify-exact-modexp",
        "verify-finite-groups",
        "verify-identity-in-run",
        "verify-ring-identity-in-run",
        "verify-matrix-rank-in-run",
        "auto-wire-verifications",
    ],
)
def test_unclear_claim_blocks_scoped_verification_cli_commands(
    tmp_path: Path,
    command: str,
) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )

    result = CliRunner().invoke(app, _verification_command_args(run_dir, tmp_path, command))

    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    assert not (run_dir / "proof_verification.yaml").exists()
    validate_run(run_dir)


@pytest.mark.parametrize(
    "command",
    [
        "verify-exact-modexp",
        "verify-finite-groups",
        "verify-identity-in-run",
        "verify-ring-identity-in-run",
        "verify-matrix-rank-in-run",
        "auto-wire-verifications",
    ],
)
def test_stale_claim_blocks_scoped_verification_cli_commands(
    tmp_path: Path,
    command: str,
) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    _stale_ledger_after_clear_gate(run_dir)

    result = CliRunner().invoke(app, _verification_command_args(run_dir, tmp_path, command))

    assert result.exit_code != 0
    assert "hypothesis_ledger changed" in result.output
    assert not (run_dir / "proof_verification.yaml").exists()
    validate_run(run_dir)


def test_unclear_claim_blocks_theorem_applicability_write(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        audit_theorem_applicability(run_dir)

    assert not (run_dir / "theorem_applicability.yaml").exists()
    result = CliRunner().invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])
    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)


def test_stale_claim_blocks_theorem_applicability_refresh(tmp_path: Path) -> None:
    run_dir = _clear_run_with_recorded_gate(tmp_path)
    _stale_ledger_after_clear_gate(run_dir)

    with pytest.raises(ClaimClearBlockedError, match="hypothesis_ledger changed"):
        audit_theorem_applicability(run_dir)

    assert not (run_dir / "theorem_applicability.yaml").exists()
    result = CliRunner().invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])
    assert result.exit_code != 0
    assert "claim-clear gate" in result.output
    assert not (run_dir / "theorem_applicability.yaml").exists()
    validate_run(run_dir)


def test_existing_mock_scaffold_still_validates(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "v2_claim_gate",
        _claim_file(tmp_path, UNCLEAR_CLAIM),
        tmp_path,
    )

    validate_run(run_dir)
