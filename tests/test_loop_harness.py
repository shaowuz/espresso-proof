"""Goal 42A: dogfood loop runner.

The runner must produce the full Goal-42 cycle layout from real runtime runs,
record failures instead of hiding them, keep evaluator-only truth labels out
of Espresso inputs, update campaign memory across cycles, and never carry any
status-bearing field in a loop record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.loop_harness import DEFAULT_CASES, load_cases, run_loop_cycle
from espresso_proof.loop_models import (
    BlockerReport,
    BottleneckReport,
    CampaignIndex,
    CycleCases,
    CycleTimings,
    FailureReport,
    GapLedger,
    LoopMemory,
    LoopRunManifest,
    RefutationReport,
    StopGate,
    StrategyTraceReport,
    SubgoalProgressReport,
    TheoremUseLedger,
)

CYCLE_FILES_AND_MODELS = [
    ("cases.yaml", CycleCases),
    ("run_manifest.yaml", LoopRunManifest),
    ("timings.yaml", CycleTimings),
    ("bottleneck_report.yaml", BottleneckReport),
    ("failures.yaml", FailureReport),
    ("refutations.yaml", RefutationReport),
    ("blockers.yaml", BlockerReport),
    ("strategy_trace.yaml", StrategyTraceReport),
    ("subgoal_progress.yaml", SubgoalProgressReport),
    ("theorem_use_ledger.yaml", TheoremUseLedger),
]


@pytest.fixture(scope="module")
def cycle_env(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("loop") / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=3)
    return {"root": root, "cycle_dir": cycle_dir}


def test_cycle_layout_complete_and_schema_valid(cycle_env) -> None:
    cycle_dir = cycle_env["cycle_dir"]
    for name, model in CYCLE_FILES_AND_MODELS:
        path = cycle_dir / name
        assert path.exists(), f"missing {name}"
        model.model_validate(read_yaml(path))
    assert (cycle_dir / "cycle_summary.md").exists()
    assert (cycle_dir / "agent_outputs").is_dir()
    # every agent_events.jsonl line is valid JSON with the required fields.
    lines = (cycle_dir / "agent_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        event = json.loads(line)
        for field in ("timestamp", "cycle_id", "case_id", "run_id", "agent_name", "stage", "event"):
            assert field in event


def test_campaign_files_created_and_valid(cycle_env) -> None:
    root = cycle_env["root"]
    CampaignIndex.model_validate(read_yaml(root / "campaign_index.yaml"))
    GapLedger.model_validate(read_yaml(root / "gap_ledger.yaml"))
    LoopMemory.model_validate(read_yaml(root / "loop_memory.yaml"))
    gate = StopGate.model_validate(read_yaml(root / "stop_gate.yaml"))
    assert gate.status == "RUNNING"
    assert gate.cycles_completed == 1
    # budget exhaustion semantics live in the model: SUCCESS needs every gate.
    with pytest.raises(ValueError, match="SUCCESS requires"):
        StopGate.model_validate({**read_yaml(root / "stop_gate.yaml"), "status": "SUCCESS"})


def test_known_truth_status_never_reaches_espresso_inputs(cycle_env) -> None:
    # Evaluator-only labels are confined to cases.yaml: no run input, prompt
    # context, or run artifact may contain them.
    cycle_dir = cycle_env["cycle_dir"]
    for path in (cycle_dir / "runs").rglob("*"):
        if path.is_file() and path.suffix in {".md", ".yaml", ".yml", ".json", ".jsonl", ".html"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "known_truth_status" not in text, path
            assert "evaluator_only" not in text, path


def test_agent_outputs_mark_not_run_instead_of_fabricating(cycle_env) -> None:
    outputs = cycle_env["cycle_dir"] / "agent_outputs"
    payloads = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in outputs.glob("*.json")}
    assert payloads, "agent outputs missing"
    statuses = {name: data.get("status") for name, data in payloads.items()}
    assert set(statuses.values()) <= {"ran", "not_run"}


def test_loop_records_carry_no_status_fields(cycle_env) -> None:
    forbidden = {
        "proof_language_status", "source_backed", "earned_status", "idea_status",
        "evidence_status", "human_confirmed",
    }
    for name, _ in CYCLE_FILES_AND_MODELS:
        text = (cycle_env["cycle_dir"] / name).read_text(encoding="utf-8")
        data_keys = set()

        def walk(node):
            if isinstance(node, dict):
                data_keys.update(node.keys())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(read_yaml(cycle_env["cycle_dir"] / name))
        assert not (data_keys & forbidden), f"{name} carries a status-bearing key"
        assert "the whole theorem is proved" not in text.lower()


def test_failed_case_is_recorded_and_cycle_continues(tmp_path: Path, monkeypatch) -> None:
    import espresso_proof.loop_harness as harness

    real_orchestrate = None
    from espresso_proof import orchestrator

    real_orchestrate = orchestrator.orchestrate_run

    def exploding(*, project, **kwargs):
        if project == "order6_abelian_false":
            raise RuntimeError("injected provider failure")
        return real_orchestrate(project=project, **kwargs)

    monkeypatch.setattr(orchestrator, "orchestrate_run", exploding)
    cycle_dir = harness.run_loop_cycle(out_root=tmp_path / "loop_runs", mode="smoke", max_cases=2)
    failures = FailureReport.model_validate(read_yaml(cycle_dir / "failures.yaml"))
    assert any(
        f.case_id == "order6_abelian_false" and "injected provider failure" in f.error_summary
        for f in failures.failures
    )
    # the OTHER case still ran and was recorded.
    manifest = LoopRunManifest.model_validate(read_yaml(cycle_dir / "run_manifest.yaml"))
    ok_runs = [r for r in manifest.runs if r.validation_result == "valid"]
    assert ok_runs, "the non-failing case should still produce a valid run"
    # a failed run is never presented as a valid one.
    failed = next(r for r in manifest.runs if r.case_id == "order6_abelian_false")
    assert failed.validation_result == "not_run"
    # and its agent outputs are honestly marked not_run, never fabricated.
    failed_outputs = cycle_dir / "agent_outputs_root" / "order6_abelian_false"
    understanding = json.loads(
        (failed_outputs / "claim_understanding.json").read_text(encoding="utf-8")
    )
    assert understanding["status"] == "not_run"


def test_second_cycle_updates_campaign_and_recurrence(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    index = CampaignIndex.model_validate(read_yaml(root / "campaign_index.yaml"))
    assert [c.cycle_id for c in index.cycles] == ["cycle_001", "cycle_002"]
    gate = StopGate.model_validate(read_yaml(root / "stop_gate.yaml"))
    assert gate.cycles_completed == 2
    ledger = GapLedger.model_validate(read_yaml(root / "gap_ledger.yaml"))
    # the deterministic smoke run produces the same blockers, so cycle 2 must
    # have incremented recurrence instead of duplicating gaps.
    assert ledger.gaps, "expected auto-derived gaps from recurring blockers"
    assert any(g.recurrence_count >= 2 for g in ledger.gaps)
    blockers = BlockerReport.model_validate(
        read_yaml(root / "cycle_002" / "blockers.yaml")
    )
    if blockers.blockers:
        assert any(b.recurrence == "recurring" for b in blockers.blockers)


def test_default_cases_cover_required_families() -> None:
    families = {case["case_family"] for case in DEFAULT_CASES}
    assert families == {
        "true_theorem", "false_theorem", "missing_hypothesis",
        "source_sensitive", "computation_scoped", "hard_research_sketch",
    }
    # must_not_claim guards exist on every case.
    assert all(case["must_not_claim"] for case in DEFAULT_CASES)


def test_cases_file_override_and_bounds(tmp_path: Path) -> None:
    cases_file = tmp_path / "cases.yaml"
    write_yaml(cases_file, {"cases": DEFAULT_CASES[:1]})
    loaded = load_cases(cases_file)
    assert [c.case_id for c in loaded] == [DEFAULT_CASES[0]["case_id"]]
    with pytest.raises(ValueError, match="mode must be"):
        run_loop_cycle(out_root=tmp_path / "x", mode="turbo")


def test_proof_step_attack_recorded_separately_from_refutation(cycle_env) -> None:
    report = RefutationReport.model_validate(
        read_yaml(cycle_env["cycle_dir"] / "refutations.yaml")
    )
    for record in report.refutations:
        if record.is_proof_step_attack_only:
            assert record.failure_class != "claim_refuted"
        if record.failure_class == "claim_refuted":
            assert record.counterexample_checked
            assert record.satisfies_all_locked_hypotheses is True
