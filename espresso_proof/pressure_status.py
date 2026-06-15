"""Async example/counterexample pressure sidecar for Workflow V2.

``pressure_status.yaml`` is derived scheduling context. It makes pending,
partial, inadmissible, and admissible pressure visible without turning missing
examples/counterexamples into evidence. Late markers are local and deterministic
so a later Update Inbox task can convert each marker exactly once.
"""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256

from .agent_run_status import agent_status, has_status
from .io import read_yaml, write_yaml
from .models import PressureStatusReport
from .observability import now_iso
from .research_bank import load_counterexamples_artifact, load_examples_artifact

PRESSURE_STATUS_FILENAME = "pressure_status.yaml"


def build_pressure_status(run_dir: Path, *, late_after_strategy: bool | None = None) -> dict:
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    generated_at = now_iso()
    lifecycle_state = str(manifest.get("current_state") or "")
    if late_after_strategy is None:
        late_after_strategy = _is_after_strategy(lifecycle_state)

    examples = load_examples_artifact(run_dir / artifacts["examples"])
    counterexamples = load_counterexamples_artifact(run_dir / artifacts["counterexamples"])

    entries = [
        _examples_entry(
            run_dir,
            artifacts["examples"],
            examples,
            claim["id"],
            generated_at,
            lifecycle_state,
            late_after_strategy,
        ),
        _counterexamples_entry(
            run_dir,
            artifacts["counterexamples"],
            counterexamples,
            claim["id"],
            generated_at,
            lifecycle_state,
            late_after_strategy,
        ),
    ]
    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": generated_at,
        "entries": entries,
        "notes": [
            "Async pressure context only; missing or pending pressure is not evidence.",
            "Late markers are local scheduling hints, not Update Inbox entries.",
        ],
    }
    PressureStatusReport.model_validate(report)
    write_yaml(run_dir / PRESSURE_STATUS_FILENAME, report)
    manifest.setdefault("optional_artifacts", {})["pressure_status"] = PRESSURE_STATUS_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


def _agent_status(run_dir: Path, kind: str) -> str:
    if not has_status(run_dir):
        return "no_board"
    return agent_status(run_dir, kind)


def artifact_revision_hash(run_dir: Path, artifact_path: str) -> str:
    path = run_dir / artifact_path
    if not path.exists():
        return "missing"
    if path.is_file():
        return sha256(path.read_bytes()).hexdigest()
    digest = sha256()
    for child in sorted(
        p for p in path.rglob("*") if p.is_file() and not p.name.startswith(".")
    ):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_after_strategy(state: str) -> bool:
    order = [
        "SCAFFOLDED",
        "FRONT_HALF_DONE",
        "CLAIM_CLEAR",
        "LITERATURE_DONE",
        "STRATEGIES_GENERATED",
        "WAITING_LLM_AGENT_HUMAN_GATE",
        "COMPLETED_MEMO",
    ]
    try:
        return order.index(state) >= order.index("STRATEGIES_GENERATED")
    except ValueError:
        return state in {
            "STRATEGY_GRADED",
            "TIME_ALLOCATION_STARTED",
            "UPDATE_INBOX_PENDING",
            "PROVER_ROUND_STARTED",
            "REVIEWERS_DONE",
            "GRADED",
            "MEMORY_WRITTEN",
            "COMPLETED_MEMO",
        }


def is_admissible_refutation(test) -> bool:
    return (
        test.refutes_claim
        and test.result == "failed"
        and test.satisfies_all_claim_hypotheses is True
        and test.target == "theorem"
        and not test.refutes_step_only
        and not exposes_missing_hypothesis_pressure(test)
    )


def exposes_missing_hypothesis_pressure(test) -> bool:
    return bool(test.exposes_missing_hypothesis or test.missing_hypothesis)


def needs_counterexample_admissibility(test) -> bool:
    return (
        test.result in {"not_tested", "inconclusive"}
        or test.satisfies_all_claim_hypotheses == "unclear"
        or (exposes_missing_hypothesis_pressure(test) and test.result != "failed")
    )


def is_inadmissible_counterexample_pressure(test) -> bool:
    return (
        test.result == "failed"
        and (
            test.satisfies_all_claim_hypotheses is False
            or test.refutes_step_only
            or test.target in {"proof_step", "hypothesis", "analogy"}
            or exposes_missing_hypothesis_pressure(test)
        )
    )


def derive_examples_pressure_state(records: list, agent_status: str) -> str:
    if agent_status != "done":
        return "pending_not_run_or_partial"
    if not records:
        return "completed_no_candidate_found_in_scope"
    if any(
        example.satisfies_all_claim_hypotheses == "unclear"
        or example.result_for_current_idea in {"inconclusive", "not_tested"}
        for example in records
    ):
        return "candidate_present_needs_admissibility"
    if any(
        example.satisfies_all_claim_hypotheses is False
        or example.result_for_current_idea == "failed"
        for example in records
    ):
        return "inadmissible_pressure"
    return "completed_no_candidate_found_in_scope"


def derive_counterexamples_pressure_state(tests: list, agent_status: str) -> str:
    if agent_status != "done":
        return "pending_not_run_or_partial"
    if not tests:
        return "completed_no_candidate_found_in_scope"
    if any(is_admissible_refutation(test) for test in tests):
        return "admissible_refutation"
    if any(needs_counterexample_admissibility(test) for test in tests):
        return "candidate_present_needs_admissibility"
    if any(is_inadmissible_counterexample_pressure(test) for test in tests):
        return "inadmissible_pressure"
    return "completed_no_candidate_found_in_scope"


def _examples_entry(
    run_dir: Path,
    artifact_path: str,
    examples,
    claim_id: str,
    generated_at: str,
    lifecycle_state: str,
    late_after_strategy: bool,
) -> dict:
    status = _agent_status(run_dir, "examples")
    records = list(examples.examples)
    state = derive_examples_pressure_state(records, status)
    needs_count = sum(
        1
        for example in records
        if example.satisfies_all_claim_hypotheses == "unclear"
        or example.result_for_current_idea in {"inconclusive", "not_tested"}
    )
    inadmissible_count = sum(
        1
        for example in records
        if example.satisfies_all_claim_hypotheses is False
        or example.result_for_current_idea == "failed"
    )
    return {
        "kind": "examples",
        "pressure_state": state,
        "agent_status": status,
        "artifact_path": artifact_path,
        "candidate_count": len(records),
        "admissible_refutation_count": 0,
        "inadmissible_pressure_count": inadmissible_count,
        "needs_admissibility_count": needs_count,
        "late_markers": _markers(
            kind="examples",
            artifact_path=artifact_path,
            artifact_hash=artifact_revision_hash(run_dir, artifact_path),
            claim_id=claim_id,
            item_ids=[example.id for example in records],
            state=state,
            generated_at=generated_at,
            lifecycle_state=lifecycle_state,
            late_after_strategy=late_after_strategy,
        ),
    }


def _counterexamples_entry(
    run_dir: Path,
    artifact_path: str,
    counterexamples,
    claim_id: str,
    generated_at: str,
    lifecycle_state: str,
    late_after_strategy: bool,
) -> dict:
    status = _agent_status(run_dir, "counterexamples")
    tests = list(counterexamples.tests)
    state = derive_counterexamples_pressure_state(tests, status)
    admissible_count = sum(
        1 for test in tests if is_admissible_refutation(test)
    )
    needs_count = sum(
        1 for test in tests if needs_counterexample_admissibility(test)
    )
    inadmissible_count = sum(
        1 for test in tests if is_inadmissible_counterexample_pressure(test)
    )
    return {
        "kind": "counterexamples",
        "pressure_state": state,
        "agent_status": status,
        "artifact_path": artifact_path,
        "candidate_count": len(tests),
        "admissible_refutation_count": admissible_count,
        "inadmissible_pressure_count": inadmissible_count,
        "needs_admissibility_count": needs_count,
        "late_markers": _markers(
            kind="counterexamples",
            artifact_path=artifact_path,
            artifact_hash=artifact_revision_hash(run_dir, artifact_path),
            claim_id=claim_id,
            item_ids=[test.id for test in tests],
            state=state,
            generated_at=generated_at,
            lifecycle_state=lifecycle_state,
            late_after_strategy=late_after_strategy,
        ),
    }


def _markers(
    *,
    kind: str,
    artifact_path: str,
    artifact_hash: str,
    claim_id: str,
    item_ids: list[str],
    state: str,
    generated_at: str,
    lifecycle_state: str,
    late_after_strategy: bool,
) -> list[dict]:
    if not late_after_strategy:
        return []
    if state in {
        "pending_not_run_or_partial",
        "completed_no_candidate_found_in_scope",
    }:
        return []
    marker_type = "example_pressure" if kind == "examples" else "counterexample_pressure"
    markers = []
    for item_id in item_ids:
        dedupe_key = f"{marker_type}:{claim_id}:{artifact_path}:{artifact_hash}:whole_claim:{item_id}:{state}"
        markers.append(
            {
                "id": f"late_{marker_type}_{item_id}",
                "type": marker_type,
                "origin_stage": kind,
                "origin_artifact": artifact_path,
                "origin_artifact_id": item_id,
                "artifact_revision_or_hash": artifact_hash,
                "locked_claim_id": claim_id,
                "route_id_or_step_id": "whole_claim",
                "scope": "whole_claim",
                "pressure_state": state,
                "lifecycle_state_at_creation": lifecycle_state,
                "late_after_strategy": late_after_strategy,
                "dedupe_key": dedupe_key,
                "created_at": generated_at,
                "effect": "scheduling_only",
                "advisory_only": True,
                "update_inbox_entry": False,
                "notes": [
                    "Local late-pressure marker for future Update Inbox conversion.",
                    "This marker has no direct scheduling effect in Task 03.",
                ],
            }
        )
    return markers


__all__ = [
    "PRESSURE_STATUS_FILENAME",
    "artifact_revision_hash",
    "build_pressure_status",
    "derive_counterexamples_pressure_state",
    "derive_examples_pressure_state",
    "exposes_missing_hypothesis_pressure",
    "is_admissible_refutation",
    "is_inadmissible_counterexample_pressure",
    "needs_counterexample_admissibility",
]
