from __future__ import annotations

import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.update_inbox import (
    UPDATE_INBOX_FILENAME,
    UpdateInboxConflictError,
    append_update_inbox_event,
    canonical_dedupe_key,
    load_update_inbox,
)
from espresso_proof.surface_warnings import refresh_final_report_disclosures
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _write_claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _fast_run(tmp_path: Path, name: str = "update_inbox") -> Path:
    result = orchestrate_run(
        project=name,
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    return run_dir


def _claim_and_manifest(run_dir: Path) -> tuple[dict, dict]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    return claim, manifest


def _event(run_dir: Path, **overrides) -> dict:
    claim, manifest = _claim_and_manifest(run_dir)
    data = {
        "id": "upd_001",
        "type": "source_gap",
        "origin_stage": "source_theorem",
        "origin_artifact": "source_grounding.yaml",
        "origin_artifact_id_or_path": "step_001",
        "locked_claim_id": claim["id"],
        "route_id_or_step_id": "strategy_route_001",
        "artifact_revision_or_hash": "hash_001",
        "validator_ok": True,
        "created_at": "2026-06-15T00:00:00+00:00",
        "dedupe_key": f"{manifest['run_id']}:source_gap:step_001",
        "applied_at": None,
        "effect": "scheduling_only",
        "advisory_only": True,
    }
    data.update(overrides)
    return data


def _write_raw_inbox(run_dir: Path, entries: list[dict]) -> None:
    claim, manifest = _claim_and_manifest(run_dir)
    write_yaml(
        run_dir / UPDATE_INBOX_FILENAME,
        {"claim_id": claim["id"], "run_id": manifest["run_id"], "entries": entries},
    )
    manifest.setdefault("optional_artifacts", {})["update_inbox"] = UPDATE_INBOX_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def _refresh_and_validate(run_dir: Path) -> None:
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)


def test_old_run_without_update_inbox_validates(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")

    assert "update_inbox" not in manifest.get("optional_artifacts", {})
    validate_run(run_dir)


def test_duplicate_update_inbox_event_is_idempotent(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir)

    first = append_update_inbox_event(run_dir, event)
    second = append_update_inbox_event(run_dir, event)

    assert len(first.entries) == 1
    assert len(second.entries) == 1
    assert load_update_inbox(run_dir) is not None
    _refresh_and_validate(run_dir)


def test_whole_claim_event_allows_null_route_or_step_id(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(
        run_dir,
        id="upd_whole_claim",
        type="budget_signal",
        origin_artifact_id_or_path="whole_claim_budget",
        route_id_or_step_id=None,
        dedupe_key="caller_supplied_wrong_key",
    )

    report = append_update_inbox_event(run_dir, event)

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.route_id_or_step_id is None
    assert entry.dedupe_key == canonical_dedupe_key(entry)
    _refresh_and_validate(run_dir)


def test_mismatched_caller_dedupe_key_cannot_bypass_idempotency(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    first = _event(run_dir, dedupe_key="wrong_key_a")
    second = _event(run_dir, id="upd_renamed", created_at="2026-06-15T01:00:00+00:00", dedupe_key="wrong_key_b")

    append_update_inbox_event(run_dir, first)
    append_update_inbox_event(run_dir, second)

    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert len(inbox.entries) == 1
    assert inbox.entries[0].dedupe_key == canonical_dedupe_key(inbox.entries[0])
    _refresh_and_validate(run_dir)


def test_raw_update_inbox_with_noncanonical_dedupe_key_fails_validation(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir, dedupe_key="not_the_contract_key")
    _write_raw_inbox(run_dir, [event])

    with pytest.raises(KernelValidationError, match="dedupe_key is not canonical"):
        validate_run(run_dir)


def test_raw_duplicate_semantic_event_with_different_keys_fails_validation(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    first = _event(run_dir)
    first["dedupe_key"] = canonical_dedupe_key(first)
    second = dict(first)
    second["id"] = "upd_duplicate"
    second["created_at"] = "2026-06-15T01:00:00+00:00"
    second["dedupe_key"] = "different_noncanonical_key"
    _write_raw_inbox(run_dir, [first, second])

    with pytest.raises(KernelValidationError, match="dedupe_key is not canonical"):
        validate_run(run_dir)


def test_duplicate_applied_entry_keeps_first_applied_at(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir)
    first = append_update_inbox_event(run_dir, event)
    raw = first.model_dump(mode="json")
    raw["entries"][0]["applied_at"] = "2026-06-15T02:00:00+00:00"
    write_yaml(run_dir / UPDATE_INBOX_FILENAME, raw)

    retry = dict(event)
    retry["id"] = "upd_retry"
    retry["created_at"] = "2026-06-15T03:00:00+00:00"
    retry["applied_at"] = None
    report = append_update_inbox_event(run_dir, retry)

    assert len(report.entries) == 1
    assert report.entries[0].applied_at == "2026-06-15T02:00:00+00:00"
    validate_run(run_dir)


def test_same_dedupe_key_with_different_payload_conflicts(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir)
    append_update_inbox_event(run_dir, event)
    changed = dict(event)
    changed["origin_stage"] = "different_stage"

    with pytest.raises(UpdateInboxConflictError, match="dedupe_key"):
        append_update_inbox_event(run_dir, changed)

    assert len(load_update_inbox(run_dir).entries) == 1  # type: ignore[union-attr]
    _refresh_and_validate(run_dir)


def test_truncated_update_inbox_cannot_validate(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["update_inbox"] = UPDATE_INBOX_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    (run_dir / UPDATE_INBOX_FILENAME).write_text("entries: [", encoding="utf-8")

    with pytest.raises(Exception):
        validate_run(run_dir)


def test_concurrent_append_retry_does_not_lose_entries(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    errors: list[Exception] = []

    def append_one(index: int) -> None:
        try:
            append_update_inbox_event(
                run_dir,
                _event(
                    run_dir,
                    id=f"upd_{index:03d}",
                    origin_artifact_id_or_path=f"step_{index:03d}",
                    artifact_revision_or_hash=f"hash_{index:03d}",
                    dedupe_key=f"dedupe_{index:03d}",
                ),
                timeout_seconds=10,
            )
        except Exception as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=append_one, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert len(inbox.entries) == 8
    assert {entry.id for entry in inbox.entries} == {f"upd_{i:03d}" for i in range(8)}
    _refresh_and_validate(run_dir)


def test_failed_append_transaction_does_not_advance_manifest_state(tmp_path: Path, monkeypatch) -> None:
    import espresso_proof.update_inbox as update_inbox_mod

    run_dir = _fast_run(tmp_path)
    original_write_yaml = update_inbox_mod.write_yaml

    def fail_update_file(path: Path, data) -> None:  # noqa: ANN001
        if Path(path).name == UPDATE_INBOX_FILENAME:
            raise RuntimeError("injected write failure")
        original_write_yaml(path, data)

    monkeypatch.setattr(update_inbox_mod, "write_yaml", fail_update_file)
    with pytest.raises(RuntimeError, match="injected write failure"):
        append_update_inbox_event(run_dir, _event(run_dir))

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "update_inbox" not in manifest.get("optional_artifacts", {})
    validate_run(run_dir)


def test_validator_ok_true_does_not_transfer_status(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    claim, manifest = _claim_and_manifest(run_dir)
    source_before = read_yaml(run_dir / manifest["artifacts"]["source_grounding"])

    append_update_inbox_event(
        run_dir,
        _event(
            run_dir,
            type="source_status_signal",
            id="upd_validator_ok",
            dedupe_key="validator_ok_signal",
            validator_ok=True,
        ),
    )

    source_after = read_yaml(run_dir / manifest["artifacts"]["source_grounding"])
    assert source_after == source_before
    assert load_update_inbox(run_dir).entries[0].locked_claim_id == claim["id"]  # type: ignore[union-attr]
    _refresh_and_validate(run_dir)


@pytest.mark.parametrize(
    ("update_type", "forbidden_field"),
    [
        ("source_status_signal", "source_backed"),
        ("example_pressure", "checked_evidence"),
        ("example_pressure", "example_checked"),
        ("counterexample_pressure", "counterexample_refutes_claim"),
        ("counterexample_admissibility", "counterexample_refutes_claim"),
        ("reviewer_objection", "proof_status"),
        ("theorem_condition_blocker", "theorem_applicability_status"),
        ("human_preference", "checked_evidence"),
        ("advisory_memory_signal", "memory_evidence"),
    ],
)
def test_update_inbox_forbidden_ownership_fields_fail_validation(
    tmp_path: Path, update_type: str, forbidden_field: str
) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir, type=update_type)
    event[forbidden_field] = True
    _write_raw_inbox(run_dir, [event])

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "proof-close",
        "complete proof",
        "source_backed",
        "checked_evidence",
        "theorem_applicability_status",
        "counterexample_refutes_claim",
        "settled",
        "verified by examples",
    ],
)
def test_update_inbox_status_wording_cannot_upgrade_status(
    tmp_path: Path, phrase: str
) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(
        run_dir,
        type="prover_progress",
        id=f"upd_prover_{phrase.replace(' ', '_')}",
        dedupe_key=f"prover:{phrase}",
        origin_artifact_id_or_path=f"prover text says {phrase}",
    )
    _write_raw_inbox(run_dir, [event])

    with pytest.raises(KernelValidationError, match="update_inbox cannot write"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin_stage", "source_backed"),
        ("origin_artifact", "checked_evidence.yaml"),
        ("origin_artifact_id_or_path", "theorem_applicability_status"),
        ("artifact_revision_or_hash", "counterexample_refutes_claim"),
        ("route_id_or_step_id", "complete_proof"),
    ],
)
def test_update_inbox_string_fields_reject_raw_status_tokens(
    tmp_path: Path, field: str, value: str
) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir)
    event[field] = value
    _write_raw_inbox(run_dir, [event])

    with pytest.raises(KernelValidationError, match="update_inbox cannot write"):
        validate_run(run_dir)


def test_counterexample_update_cannot_make_grade_refuted(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    append_update_inbox_event(
        run_dir,
        _event(run_dir, type="counterexample_admissibility", id="upd_cex", dedupe_key="cex"),
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)
    refresh_final_report_disclosures(run_dir)

    with pytest.raises(KernelValidationError, match="requires an admissible checked counterexample"):
        validate_run(run_dir)


def test_budget_signal_cannot_override_validator_blocker(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    append_update_inbox_event(
        run_dir,
        _event(run_dir, type="budget_signal", id="upd_budget", dedupe_key="budget"),
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["score"] = 100
    write_yaml(grade_path, grade)
    refresh_final_report_disclosures(run_dir)

    with pytest.raises(KernelValidationError, match="Grade 10"):
        validate_run(run_dir)


def test_append_rejects_status_field_before_write(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir)
    event["source_status"] = "source_backed"

    with pytest.raises(ValidationError):
        append_update_inbox_event(run_dir, event)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "update_inbox" not in manifest.get("optional_artifacts", {})


def test_append_rejects_wrong_locked_claim_before_write(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    event = _event(run_dir, locked_claim_id="different_claim")

    with pytest.raises(ValueError, match="locked_claim_id"):
        append_update_inbox_event(run_dir, event)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "update_inbox" not in manifest.get("optional_artifacts", {})
