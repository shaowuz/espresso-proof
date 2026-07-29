"""Stage-0 genesis: scaffold a kernel-valid run shell for any claim (LIVE).

Every run starts here: ``orchestrate_run`` calls ``run_mock_critique`` to
create the initial artifact set, seeded from the deterministic scenario
TEMPLATES in ``orchestration.scenario`` land — i.e. ``mock_pipeline`` — and
validated fail-closed before any agent runs. Extracted from mock_pipeline so
live product code no longer lives in a file named "mock".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from espresso_proof.kernel.contracts import default_agent_contracts
from espresso_proof.kernel.io import read_text, write_text, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.memory.memory import write_run_memory
from espresso_proof.orchestration.mock_pipeline import (
    _artifacts_for_scenario,
    _detect_scenario,
    _pending_human_choice,
    _with_final_report_disclosures,
)
from espresso_proof.verification.counterexample_probes import propose_deterministic_counterexamples
from espresso_proof.verification.verification_request_probes import (
    propose_named_witness_requests,
    propose_scoped_verification_requests,
)


def run_mock_critique(project_id: str, input_path: Path, output_root: Path) -> Path:
    text = read_text(input_path)
    scenario = _detect_scenario(input_path, text)
    run_id = f"{project_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    run_dir = output_root / "projects" / project_id / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_text(run_dir / "input.md", text)
    artifacts = _artifacts_for_scenario(scenario, text, run_id, project_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]

    # Deterministic machine counterexample probes run in the
    # mock path too, so a concrete false claim ("every group of order 6 is
    # abelian") carries a Cayley-table-verified counterexample instead of an
    # empty bank. The probe fails closed (nothing for symbolic/true/modified
    # claims), and because the vague-claim path leaves the hypothesis list
    # undetermined, the record it adds here is DOWNGRADED (satisfies=unclear,
    # refutes_claim=False): an undetermined ledger never earns a refutation.
    probe_tests = propose_deterministic_counterexamples(artifacts["locked_claim.yaml"])
    if probe_tests:
        bank = artifacts.get("counterexamples.yaml", {"claim_id": claim_id, "tests": []})
        existing_ids = {t.get("id") for t in bank.get("tests", [])}
        bank.setdefault("tests", []).extend(
            t for t in probe_tests if t["id"] not in existing_ids
        )
        artifacts["counterexamples.yaml"] = bank

    # Deterministic scoped-verification REQUESTS run in the
    # mock path too, so the symbolic p^2-abelian dogfood claim proposes its
    # concrete commutativity instance checks. Requests are proposals only
    # (no status); auto-wiring through the trusted backend is what can earn
    # instance_checked, and the probe fails closed on any other claim.
    verification_requests = propose_scoped_verification_requests(
        artifacts["locked_claim.yaml"]
    ) + propose_named_witness_requests(artifacts["locked_claim.yaml"])
    if verification_requests:
        artifacts["scoped_verification_requests.yaml"] = {
            "claim_id": claim_id,
            "requests": verification_requests,
        }
    artifacts["final_report.md"] = _with_final_report_disclosures(
        str(artifacts.get("final_report.md") or ""),
        artifacts,
    )
    artifacts["agent_contracts.yaml"] = default_agent_contracts(project_id)
    artifacts.setdefault("human_choice.yaml", _pending_human_choice(claim_id))
    optional_artifacts = {
        "human_example_feedback": "human_example_feedback.yaml",
        "example_feedback_summary": "example_feedback_summary.yaml",
        "scoped_verification_requests": "scoped_verification_requests.yaml",
    }
    optional_manifest = {
        key: filename
        for key, filename in optional_artifacts.items()
        if filename in artifacts
    }
    for filename, data in artifacts.items():
        if filename.endswith(".yaml"):
            write_yaml(run_dir / filename, data)
        else:
            write_text(run_dir / filename, data)

    manifest = {
        "run_id": run_id,
        "project_id": project_id,
        "created_at": datetime.now(UTC).isoformat(),
        "current_state": "WAITING_HUMAN_MAJOR_DECISION",
        "input_file": "input.md",
        "artifacts": {
            "locked_claim": "locked_claim.yaml",
            "agent_contracts": "agent_contracts.yaml",
            "hypothesis_ledger": "hypothesis_ledger.yaml",
            "literature_review": "literature_review.yaml",
            "examples": "examples.yaml",
            "computation_checks": "computation_checks.yaml",
            "counterexamples": "counterexamples.yaml",
            "tests": "tests.yaml",
            "source_grounding": "source_grounding.yaml",
            "review": "review.yaml",
            "idea_grade": "idea_grade.yaml",
            "loop_control": "loop_control.yaml",
            "strategy_synthesis": "strategy_synthesis.yaml",
            "strategy_brief": "strategy_brief.yaml",
            "human_choice": "human_choice.yaml",
            "final_report": "final_report.md",
        },
        "optional_artifacts": optional_manifest,
        "dependencies": {
            "idea_grade": {
                "depends_on": [
                    "locked_claim",
                    "hypothesis_ledger",
                    "literature_review",
                    "examples",
                    "computation_checks",
                    "counterexamples",
                    "tests",
                    "source_grounding",
                    "review",
                ]
            },
            "source_grounding": {
                "depends_on": ["literature_review", "tests"]
            },
            "loop_control": {
                "depends_on": ["tests", "source_grounding", "review", "idea_grade"]
            },
            "strategy_synthesis": {
                "depends_on": ["tests", "source_grounding", "review", "idea_grade", "loop_control"]
            },
            "strategy_brief": {
                "depends_on": ["strategy_synthesis"]
            },
            "human_choice": {
                "depends_on": ["strategy_brief"]
            },
            "final_report": {
                "depends_on": ["idea_grade", "strategy_brief", "human_choice"]
            },
        },
        "retries": {},
    }
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)
    _write_memory_entry(run_dir, output_root, project_id, run_id)
    return run_dir


def _write_memory_entry(run_dir: Path, output_root: Path, project_id: str, run_id: str) -> None:
    # Unified writer: all memory writing/index logic lives in espresso_proof.memory.memory.
    write_run_memory(
        output_root / "projects" / project_id,
        run_dir,
        project_id=project_id,
        run_id=run_id,
    )
