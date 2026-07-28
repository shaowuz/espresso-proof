"""run_manifest.current_state is a kernel-enforced closed set (RunState).

Guards that every real state validates, an out-of-alphabet state is rejected by the
kernel (fail-closed), and the orchestrator's ordered state list and the CLI's
human-decision writer only emit RunState members.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.cli import _state_for_human_decision
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import ContinueDecision, RunState
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.orchestration.orchestrator import ORCHESTRATION_STATES

FIXTURES = Path("tests/fixtures")


def _mock_run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "run_state_enum",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )


def _set_state(run_dir: Path, state: str) -> None:
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["current_state"] = state
    write_yaml(manifest_path, manifest)


@pytest.mark.parametrize("state", [s.value for s in RunState])
def test_every_runstate_member_validates(tmp_path: Path, state: str) -> None:
    run_dir = _mock_run(tmp_path / state.lower())
    _set_state(run_dir, state)
    validate_run(run_dir)  # must not raise for any member of the closed set


def test_out_of_alphabet_state_is_rejected(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path / "bogus")
    _set_state(run_dir, "NOT_A_REAL_STATE")
    with pytest.raises(Exception):
        validate_run(run_dir)


def test_plain_string_coerces_to_runstate() -> None:
    # YAML gives a plain string; the str-Enum must compare equal so all existing
    # `== "..."` / .index(...) call sites keep working.
    assert RunState("SCAFFOLDED") == "SCAFFOLDED"
    assert RunState.COMPLETED_MEMO == "COMPLETED_MEMO"


def test_orchestration_states_are_all_valid_runstates() -> None:
    valid = {s.value for s in RunState}
    for state in ORCHESTRATION_STATES:
        assert state in valid, f"{state} in ORCHESTRATION_STATES but not a RunState"


def test_cli_human_decision_states_are_runstates() -> None:
    valid = {s.value for s in RunState}
    for decision in ContinueDecision:
        state = _state_for_human_decision(decision)
        assert state in valid, f"cli writes {state} which is not a RunState"
