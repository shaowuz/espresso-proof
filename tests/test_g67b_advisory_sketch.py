"""Goal 67B — non-discarding capture of a prover attempt's reasoning.

A prover step may do the mathematics correctly yet smuggle a status field
(``proof_status: ...`` / ``source_backed: true``) that ``safe_text`` does not
strip (it only redacts the value, not the key). The kernel then rejects the round
and the StageRunner rolls the whole run back, discarding the attempt. These tests
prove the advisory sidecar (a) captures the reasoning, (b) is scrubbed so it can
never itself carry forbidden proof/status language, (c) SURVIVES the rollback,
and (d) is surfaced status-neutrally — never as a proof.
"""

from __future__ import annotations

import pytest

from espresso_proof.advisory_proof_sketch import (
    ADVISORY_PROOF_SKETCH_FILENAME,
    _scrub_sketch_text,
    capture_advisory_proof_sketch,
    read_advisory_proof_sketch,
)
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.stage_runner import StageRunner, StageError, _SNAPSHOT_EXCLUDED_NAMES
from espresso_proof.validators import (
    KernelValidationError,
    _first_forbidden_proof_language,
    _prover_status_smuggling_field,
    _prover_unchecked_support_misuse,
)


# --------------------------------------------------------------------------- #
# scrubbing — the sink can never carry forbidden proof/status language
# --------------------------------------------------------------------------- #

def _assert_kernel_clean(text: str) -> None:
    """The exact guards validators._validate_prover_round_language applies."""
    assert _first_forbidden_proof_language(text.lower()) is None, text
    assert _prover_status_smuggling_field(text) is None, text
    assert _prover_unchecked_support_misuse(text) is None, text


def test_scrub_neutralizes_status_field_smuggling():
    raw = "By Lagrange every non-identity element has order 3, so G is cyclic. proof_status: proved"
    out = _scrub_sketch_text(raw)
    assert "proof_status" not in out.lower()
    _assert_kernel_clean(out)


def test_scrub_neutralizes_source_backed_assignment():
    out = _scrub_sketch_text("Apply orbit-stabilizer. source_backed: true")
    assert "source_backed: true" not in out.lower()
    _assert_kernel_clean(out)


def test_scrub_keeps_clean_mathematics():
    raw = "Let g be a non-identity element; its order divides 3, hence equals 3, so <g> = G."
    out = _scrub_sketch_text(raw)
    assert "order divides 3" in out
    _assert_kernel_clean(out)


@pytest.mark.parametrize(
    "raw",
    [
        "This completes the proof. proof-close.",
        "Therefore the theorem is proved and source_backed.",
        "checked_evidence = yes; the result is now verified.",
        "evidence_status: confirmed and the claim is refuted.",
    ],
)
def test_scrub_output_always_passes_kernel_guards(raw):
    # Whatever a model writes, the captured text must pass the kernel's own
    # language guards (round-trip safety: the sink is provably non-upgrading).
    _assert_kernel_clean(_scrub_sketch_text(raw))


# --------------------------------------------------------------------------- #
# capture — shape, naming, fail-closed
# --------------------------------------------------------------------------- #

def _agent_report(steps, summary="Sketch."):
    return {"prover_attempt": {"output_summary": summary, "proposed_steps": steps}}


def test_capture_writes_advisory_sink(tmp_path):
    report = _agent_report(
        ["By Lagrange the order divides 3. proof_status: proved", "Hence G is cyclic."]
    )
    path = capture_advisory_proof_sketch(tmp_path, report)
    assert path is not None and path.name == ADVISORY_PROOF_SKETCH_FILENAME
    data = read_yaml(path)
    assert data["kind"] == "advisory_proof_sketch"
    # MUST NOT be the ProofLanguageStatus.proof_candidate enum member.
    assert data["kind"] != "proof_candidate"
    assert data["trusted"] is False
    assert data["advisory_only"] is True
    for step in data["steps"]:
        _assert_kernel_clean(step)
    _assert_kernel_clean(data["summary"])


def test_capture_returns_none_when_nothing_to_capture(tmp_path):
    assert capture_advisory_proof_sketch(tmp_path, _agent_report([], summary="")) is None
    assert not (tmp_path / ADVISORY_PROOF_SKETCH_FILENAME).exists()


def test_capture_is_fail_closed_on_garbage(tmp_path):
    # Never raises, whatever the input shape.
    assert capture_advisory_proof_sketch(tmp_path, None) is None
    assert capture_advisory_proof_sketch(tmp_path, {"prover_attempt": "not-a-dict"}) is None
    assert capture_advisory_proof_sketch(tmp_path, {"prover_attempt": {"proposed_steps": 5}}) is None


# --------------------------------------------------------------------------- #
# the core property — the sidecar survives a StageRunner rollback
# --------------------------------------------------------------------------- #

def test_sidecar_survives_stage_runner_rollback(tmp_path):
    write_yaml(tmp_path / "run_manifest.yaml", {"current_state": "BEFORE"})
    runner = StageRunner(tmp_path)
    report = _agent_report(["Good math here. proof_status: proved"])

    def mutate():
        # a "kernel artifact" the rollback must drop ...
        write_yaml(tmp_path / "llm_agent_run.yaml", {"smuggled": "proof_status: proved"})
        # ... and the advisory sidecar, which must survive.
        assert capture_advisory_proof_sketch(tmp_path, report) is not None

    def validate():
        raise KernelValidationError("prover round text cannot set proof_status")

    with pytest.raises(StageError):
        runner.run(name="llm_prover_reviewers", mutate=mutate, validate=validate,
                   state_after="AFTER")

    # the kernel artifact was rolled back ...
    assert not (tmp_path / "llm_agent_run.yaml").exists()
    # ... the state did not advance ...
    assert read_yaml(tmp_path / "run_manifest.yaml")["current_state"] == "BEFORE"
    # ... but the reasoning survived in the advisory sidecar.
    sketch = read_advisory_proof_sketch(tmp_path)
    assert sketch is not None
    assert "Good math here" in sketch["steps"][0]
    _assert_kernel_clean(sketch["steps"][0])


def test_filename_is_registered_as_snapshot_excluded():
    assert ADVISORY_PROOF_SKETCH_FILENAME in _SNAPSHOT_EXCLUDED_NAMES


# --------------------------------------------------------------------------- #
# surface — status-neutral
# --------------------------------------------------------------------------- #

def test_status_summary_surfaces_sketch_without_status(tmp_path):
    from espresso_proof.run_dashboard import _advisory_sketch_summary

    assert _advisory_sketch_summary(tmp_path) == {"present": False}
    capture_advisory_proof_sketch(tmp_path, _agent_report(["Order divides 3."]))
    out = _advisory_sketch_summary(tmp_path)
    assert out["present"] is True
    assert out["advisory_only"] is True
    assert out["step_count"] == 1
    # carries no mathematical-status field
    assert "proof_status" not in out
    assert "proof_label" not in out
