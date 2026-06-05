"""Per-item research bank V0 (storage/layout only).

A bank directory (``_meta.yaml`` + ``NN-<id>.yaml`` files) aggregates to the same
existing model as the old single YAML artifact. Backward compatible; changes no
proof/source/evidence/strategy/counterexample semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import (
    CounterexampleReport,
    ExampleSelection,
    StrategySynthesis,
)
from espresso_proof.research_bank import (
    append_bank_item,
    load_bank,
    load_counterexamples_artifact,
    load_examples_artifact,
    load_strategy_synthesis_artifact,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )


def _make_bank(bank_dir: Path, *, kind: str, list_field: str, source: dict, meta_extra: dict | None = None) -> None:
    bank_dir.mkdir()
    meta = {"kind": kind, "claim_id": source["claim_id"]}
    meta.update(meta_extra or {})
    write_yaml(bank_dir / "_meta.yaml", meta)
    for index, item in enumerate(source[list_field]):
        write_yaml(bank_dir / f"{index:02d}-{item['id']}.yaml", item)


# ---------------------------------------------------------------------------
# 1-3) Bank directories aggregate to the existing models.
# ---------------------------------------------------------------------------


def test_examples_bank_aggregates_to_example_selection(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "examples.yaml")
    bank = run_dir / "examples_bank"
    _make_bank(bank, kind="examples", list_field="examples", source=source)
    agg = load_bank(bank)
    assert isinstance(agg, ExampleSelection)
    assert agg.model_dump() == ExampleSelection.model_validate(source).model_dump()


def test_counterexamples_bank_aggregates_to_report(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "counterexamples.yaml")
    bank = run_dir / "counterexamples_bank"
    _make_bank(bank, kind="counterexamples", list_field="tests", source=source)
    agg = load_bank(bank)
    assert isinstance(agg, CounterexampleReport)
    assert agg.model_dump() == CounterexampleReport.model_validate(source).model_dump()


def test_strategies_bank_aggregates_to_strategy_synthesis(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "strategy_synthesis.yaml")
    bank = run_dir / "strategies_bank"
    _make_bank(
        bank,
        kind="strategies",
        list_field="routes",
        source=source,
        meta_extra={
            "synthesized_from": source["synthesized_from"],
            "discarded_routes": source.get("discarded_routes", []),
            "synthesis_note": source["synthesis_note"],
        },
    )
    agg = load_bank(bank)
    assert isinstance(agg, StrategySynthesis)
    assert agg.model_dump() == StrategySynthesis.model_validate(source).model_dump()


# ---------------------------------------------------------------------------
# 4) Single-file artifacts still validate exactly as before.
# ---------------------------------------------------------------------------


def test_single_file_artifacts_still_validate(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    ex = load_examples_artifact(run_dir / "examples.yaml")
    ce = load_counterexamples_artifact(run_dir / "counterexamples.yaml")
    ss = load_strategy_synthesis_artifact(run_dir / "strategy_synthesis.yaml")
    assert isinstance(ex, ExampleSelection)
    assert isinstance(ce, CounterexampleReport)
    assert isinstance(ss, StrategySynthesis)


# ---------------------------------------------------------------------------
# 5-6) Invalid item / duplicate id fail.
# ---------------------------------------------------------------------------


def test_invalid_item_fails(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "examples.yaml")
    bank = run_dir / "examples_bank"
    _make_bank(bank, kind="examples", list_field="examples", source=source)
    # an item missing required fields / wrong shape.
    write_yaml(bank / "99-broken.yaml", {"id": "broken", "not_a_field": True})
    with pytest.raises(KernelValidationError):
        load_bank(bank)


def test_duplicate_item_id_fails(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "examples.yaml")
    bank = run_dir / "examples_bank"
    _make_bank(bank, kind="examples", list_field="examples", source=source)
    dup = dict(source["examples"][0])  # same id as 00-...
    write_yaml(bank / f"98-{dup['id']}.yaml", dup)
    with pytest.raises(KernelValidationError, match="duplicate"):
        load_bank(bank)


# ---------------------------------------------------------------------------
# 7-9) Append one item writes exactly one new file; existing files unchanged.
# ---------------------------------------------------------------------------


def _append_writes_one_new_file(bank: Path, item: dict) -> None:
    before = {p.name: p.read_bytes() for p in bank.glob("*.yaml")}
    new_path = append_bank_item(bank, item)
    after = {p.name: p.read_bytes() for p in bank.glob("*.yaml")}
    new_files = set(after) - set(before)
    assert new_files == {new_path.name}
    # existing files are byte-identical (not rewritten).
    for name, data in before.items():
        assert after[name] == data


def test_append_one_example_writes_one_new_file(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "examples.yaml")
    bank = run_dir / "examples_bank"
    _make_bank(bank, kind="examples", list_field="examples", source=source)
    item = dict(source["examples"][0])
    item["id"] = "appended_example_x"
    _append_writes_one_new_file(bank, item)


def test_append_one_counterexample_writes_one_new_file(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "counterexamples.yaml")
    bank = run_dir / "counterexamples_bank"
    _make_bank(bank, kind="counterexamples", list_field="tests", source=source)
    item = dict(source["tests"][0])
    item["id"] = "appended_ce_x"
    _append_writes_one_new_file(bank, item)


def test_append_one_strategy_writes_one_new_file(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "strategy_synthesis.yaml")
    bank = run_dir / "strategies_bank"
    _make_bank(
        bank,
        kind="strategies",
        list_field="routes",
        source=source,
        meta_extra={
            "synthesized_from": source["synthesized_from"],
            "discarded_routes": source.get("discarded_routes", []),
            "synthesis_note": source["synthesis_note"],
        },
    )
    item = dict(source["routes"][0])
    item["id"] = "appended_route_x"
    _append_writes_one_new_file(bank, item)


# ---------------------------------------------------------------------------
# 10-11) validate_run with bank directories and with single files.
# ---------------------------------------------------------------------------


def _switch_to_bank(run_dir: Path, *, artifact_key: str, dir_name: str, kind: str, list_field: str, meta_extra=None) -> None:
    source = read_yaml(run_dir / f"{artifact_key}.yaml")
    bank = run_dir / dir_name
    _make_bank(bank, kind=kind, list_field=list_field, source=source, meta_extra=meta_extra)
    (run_dir / f"{artifact_key}.yaml").unlink()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    old_value = manifest["artifacts"][artifact_key]
    manifest["artifacts"][artifact_key] = dir_name
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ac = read_yaml(run_dir / "agent_contracts.yaml")
    for contract in ac["contracts"]:
        if contract.get("output_artifact") == old_value:
            contract["output_artifact"] = dir_name
    write_yaml(run_dir / "agent_contracts.yaml", ac)


def test_validate_run_with_bank_directories(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _switch_to_bank(run_dir, artifact_key="examples", dir_name="examples", kind="examples", list_field="examples")
    _switch_to_bank(
        run_dir, artifact_key="counterexamples", dir_name="counterexamples",
        kind="counterexamples", list_field="tests",
    )
    ss = read_yaml(run_dir / "strategy_synthesis.yaml")
    _switch_to_bank(
        run_dir, artifact_key="strategy_synthesis", dir_name="strategies",
        kind="strategies", list_field="routes",
        meta_extra={
            "synthesized_from": ss["synthesized_from"],
            "discarded_routes": ss.get("discarded_routes", []),
            "synthesis_note": ss["synthesis_note"],
        },
    )
    validate_run(run_dir)  # fail-closed kernel gate over the aggregated models


def test_validate_run_still_works_with_single_files(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    validate_run(run_dir)  # unchanged single-file path


# ---------------------------------------------------------------------------
# 12) No bank item may upgrade proof/source/evidence status beyond model rules.
# ---------------------------------------------------------------------------


def test_bank_item_cannot_bypass_counterexample_refutation_rule(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    source = read_yaml(run_dir / "counterexamples.yaml")
    bank = run_dir / "counterexamples_bank"
    _make_bank(bank, kind="counterexamples", list_field="tests", source=source)
    # A dropped-hypothesis test that tries to refute the original claim: the
    # EXISTING CounterexampleTest validator must reject it on append.
    unsafe = {
        "id": "unsafe_refutation",
        "operator_id": "op_x",
        "example_id": "ex_x",
        "satisfies_all_claim_hypotheses": False,
        "target": "theorem",
        "result": "failed",
        "refutes_claim": True,
        "refutes_step_only": False,
        "exposes_missing_hypothesis": True,
        "missing_hypothesis": "smoothness",
        "explanation": "drops a hypothesis but claims to refute the theorem",
    }
    with pytest.raises(KernelValidationError):
        append_bank_item(bank, unsafe)
    # nothing was written for the rejected item.
    assert not list(bank.glob("*unsafe_refutation*"))
