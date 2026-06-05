"""Concurrent-safe bank append V0.1.

``append_bank_item`` writes through a temp file and atomically + exclusively
places it at a free ordinal, retrying a later ordinal on a collision and never
overwriting an existing item file. Loading behaviour is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof import research_bank as rb
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.research_bank import append_bank_item, load_bank
from espresso_proof.validation.errors import KernelValidationError

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )


def _bank(run_dir: Path, *, kind: str, list_field: str, src_name: str, meta_extra: dict | None = None) -> Path:
    source = read_yaml(run_dir / f"{src_name}.yaml")
    bank = run_dir / f"{src_name}_bank"
    bank.mkdir()
    meta = {"kind": kind, "claim_id": source["claim_id"]}
    meta.update(meta_extra or {})
    write_yaml(bank / "_meta.yaml", meta)
    for index, item in enumerate(source[list_field]):
        write_yaml(bank / f"{index:02d}-{item['id']}.yaml", item)
    return bank


def _examples_bank(run_dir: Path) -> Path:
    return _bank(run_dir, kind="examples", list_field="examples", src_name="examples")


def _new_example(run_dir: Path, item_id: str) -> dict:
    item = dict(read_yaml(run_dir / "examples.yaml")["examples"][0])
    item["id"] = item_id
    return item


# ---------------------------------------------------------------------------
# 1) Collision retry writes a later ordinal.
# ---------------------------------------------------------------------------


def test_collision_retry_writes_later_ordinal(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    n_before = len(list(bank.glob("[0-9]*.yaml")))

    # Simulate a racing appender that already grabbed the first candidate slot:
    # the exclusive placement fails once, then succeeds at the next ordinal.
    original = rb._atomic_link
    calls = {"n": 0}

    def flaky_link(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # slot taken by a "concurrent" appender
        return original(src, dst)

    monkeypatch.setattr(rb, "_atomic_link", flaky_link)
    new_path = append_bank_item(bank, _new_example(run_dir, "racer"))
    assert calls["n"] == 2  # one collision, one success
    # the written ordinal is strictly later than the first candidate ordinal.
    assert new_path.name == f"{n_before + 1:02d}-racer.yaml"
    assert len(load_bank(bank).examples) == n_before + 1


# ---------------------------------------------------------------------------
# 2) Temp file is cleaned up on failure.
# ---------------------------------------------------------------------------


def test_temp_file_cleaned_up_on_placement_failure(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    monkeypatch.setattr(rb, "_MAX_APPEND_ATTEMPTS", 5)
    monkeypatch.setattr(rb, "_atomic_link", lambda src, dst: False)  # never succeeds
    with pytest.raises(KernelValidationError):
        append_bank_item(bank, _new_example(run_dir, "doomed"))
    assert not list(bank.glob(".tmp-bankitem-*")), "temp file left behind"
    assert not list(bank.glob("*doomed*")), "a final item file was written despite failure"


def test_temp_file_cleaned_up_on_aggregate_rollback(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    # Force the post-placement re-aggregation to fail; the placed file must roll
    # back and no temp file may remain.
    def boom(_bank_dir):
        raise KernelValidationError("forced aggregate failure")

    monkeypatch.setattr(rb, "load_bank", boom)
    with pytest.raises(KernelValidationError, match="forced aggregate"):
        append_bank_item(bank, _new_example(run_dir, "rollback_me"))
    assert not list(bank.glob(".tmp-bankitem-*"))
    assert not list(bank.glob("*rollback_me*"))


# ---------------------------------------------------------------------------
# 3) Existing files are byte-identical after append.
# ---------------------------------------------------------------------------


def test_existing_files_byte_identical_after_append(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    before = {p.name: p.read_bytes() for p in bank.glob("*.yaml")}
    new_path = append_bank_item(bank, _new_example(run_dir, "extra_item"))
    after = {p.name: p.read_bytes() for p in bank.glob("*.yaml")}
    assert set(after) - set(before) == {new_path.name}
    for name, data in before.items():
        assert after[name] == data, f"{name} was rewritten"


# ---------------------------------------------------------------------------
# 4-5) Duplicate id fails; invalid item fails before writing a final file.
# ---------------------------------------------------------------------------


def test_duplicate_id_fails(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    existing_id = read_yaml(run_dir / "examples.yaml")["examples"][0]["id"]
    with pytest.raises(KernelValidationError, match="duplicate"):
        append_bank_item(bank, _new_example(run_dir, existing_id))


def test_invalid_item_fails_before_writing_any_file(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    bank = _examples_bank(run_dir)
    before = {p.name for p in bank.glob("*.yaml")}
    with pytest.raises(KernelValidationError):
        append_bank_item(bank, {"id": "broken", "not_a_real_field": True})
    after = {p.name for p in bank.glob("*.yaml")}
    assert after == before  # nothing written
    assert not list(bank.glob(".tmp-bankitem-*"))  # no temp left


# ---------------------------------------------------------------------------
# 6) Examples / counterexamples / strategies append still work.
# ---------------------------------------------------------------------------


def test_append_works_for_all_three_kinds(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    ex_bank = _examples_bank(run_dir)
    ce_bank = _bank(run_dir, kind="counterexamples", list_field="tests", src_name="counterexamples")
    ss = read_yaml(run_dir / "strategy_synthesis.yaml")
    st_bank = _bank(
        run_dir, kind="strategies", list_field="routes", src_name="strategy_synthesis",
        meta_extra={
            "synthesized_from": ss["synthesized_from"],
            "discarded_routes": ss.get("discarded_routes", []),
            "synthesis_note": ss["synthesis_note"],
        },
    )
    ex_item = _new_example(run_dir, "ex_extra")
    ce_item = {**dict(read_yaml(run_dir / "counterexamples.yaml")["tests"][0]), "id": "ce_extra"}
    st_item = {**dict(ss["routes"][0]), "id": "st_extra"}
    assert append_bank_item(ex_bank, ex_item).name.endswith("ex_extra.yaml")
    assert append_bank_item(ce_bank, ce_item).name.endswith("ce_extra.yaml")
    assert append_bank_item(st_bank, st_item).name.endswith("st_extra.yaml")
    assert len(load_bank(ex_bank).examples) == len(read_yaml(run_dir / "examples.yaml")["examples"]) + 1
    assert len(load_bank(ce_bank).tests) == len(read_yaml(run_dir / "counterexamples.yaml")["tests"]) + 1
    assert len(load_bank(st_bank).routes) == len(ss["routes"]) + 1


# ---------------------------------------------------------------------------
# 7) validate_run still works with bank directories and old single YAML.
# ---------------------------------------------------------------------------


def test_validate_run_with_banks_and_single_files(tmp_path: Path) -> None:
    from espresso_proof.validators import validate_run

    # single-file run validates unchanged
    single = _run(tmp_path / "single")
    validate_run(single)

    # bank-directory run validates (manifest + agent_contracts consistent)
    run_dir = _run(tmp_path / "bank")
    for key, dir_name, kind, list_field, meta_extra in (
        ("examples", "examples", "examples", "examples", None),
        ("counterexamples", "counterexamples", "counterexamples", "tests", None),
    ):
        source = read_yaml(run_dir / f"{key}.yaml")
        bank = run_dir / dir_name
        bank.mkdir()
        meta = {"kind": kind, "claim_id": source["claim_id"], **(meta_extra or {})}
        write_yaml(bank / "_meta.yaml", meta)
        for index, item in enumerate(source[list_field]):
            write_yaml(bank / f"{index:02d}-{item['id']}.yaml", item)
        (run_dir / f"{key}.yaml").unlink()
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        old_value = manifest["artifacts"][key]
        manifest["artifacts"][key] = dir_name
        write_yaml(run_dir / "run_manifest.yaml", manifest)
        ac = read_yaml(run_dir / "agent_contracts.yaml")
        for contract in ac["contracts"]:
            if contract.get("output_artifact") == old_value:
                contract["output_artifact"] = dir_name
        write_yaml(run_dir / "agent_contracts.yaml", ac)
    validate_run(run_dir)
