"""honest auto/human provenance in the kernel.

An automatic (no-human) gate resolution or strategy choice must be recordable
with honest provenance (selected_by / resolved_by = 'auto'), and LYING about it
(auto + human_confirmed=true) must be a validation error at BOTH layers:
the pydantic model validators and the kernel run validators (belt and
suspenders). All defaults preserve today's human-path behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.models import (
    Claim,
    HumanChoice,
    LLMAgentHumanGate,
    LLMAgentRunReport,
    RunManifest,
    StrategyBrief,
)
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.kernel.validators._part5 import _validate_human_choice
from espresso_proof.kernel.validators._part6 import _validate_llm_loop_artifacts
from espresso_proof.orchestration.genesis import run_mock_critique

FIXTURES = Path("tests/fixtures")


def _construct_manifest(state: str = "WAITING_LLM_AGENT_HUMAN_GATE") -> RunManifest:
    return RunManifest.model_construct(
        run_id="run_test", artifacts={}, optional_artifacts={}, current_state=state
    )


def _construct_choice(**overrides) -> HumanChoice:
    fields = {
        "claim_id": "claim_test",
        "status": "pending",
        "selected_strategy": None,
        "effort": None,
        "taste_judgment": "",
        "continue_decision": "pending",
        "human_confirmed": False,
        "selected_by": "human",
    }
    fields.update(overrides)
    return HumanChoice.model_construct(**fields)


def _selected_choice(**overrides) -> dict:
    choice = {
        "claim_id": "claim_test",
        "status": "selected",
        "selected_strategy": "strategy_a",
        "effort": "quick_test",
        "taste_judgment": "",
        "continue_decision": "continue",
        "human_confirmed": True,
    }
    choice.update(overrides)
    return choice


def _resolved_gate(**overrides) -> dict:
    gate = {
        "gate_id": "gate_test",
        "status": "resolved",
        "question": "Continue?",
        "allowed_decisions": ["continue", "revise", "stop"],
        "recommended_decision": "continue",
        "decision": "continue",
        "reason": "Route looks worth one bounded round.",
        "human_confirmed": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "resolved_at": "2026-01-01T00:05:00+00:00",
    }
    gate.update(overrides)
    return gate


# ---------------------------------------------------------------------------
# (a) HumanChoice.selected_by model quartet
# ---------------------------------------------------------------------------


def test_human_choice_auto_claiming_human_confirmed_is_rejected() -> None:
    with pytest.raises(ValidationError, match="can never claim human_confirmed"):
        HumanChoice.model_validate(
            _selected_choice(selected_by="auto", human_confirmed=True)
        )


def test_human_choice_auto_with_unconfirmed_is_valid() -> None:
    choice = HumanChoice.model_validate(
        _selected_choice(selected_by="auto", human_confirmed=False)
    )
    assert choice.selected_by == "auto"
    assert choice.human_confirmed is False


def test_human_choice_human_selected_still_requires_confirmation() -> None:
    # Existing rule unchanged: a human selection must carry human_confirmed=true.
    with pytest.raises(ValidationError, match="requires human_confirmed=true"):
        HumanChoice.model_validate(
            _selected_choice(selected_by="human", human_confirmed=False)
        )


def test_human_choice_defaults_preserve_human_path() -> None:
    # A choice written WITHOUT selected_by (every pre-PR1 artifact) parses to the
    # human provenance and keeps today's rules byte-for-byte.
    choice = HumanChoice.model_validate(_selected_choice())
    assert choice.selected_by == "human"
    assert choice.human_confirmed is True


def test_human_choice_auto_lie_is_rejected_for_any_status() -> None:
    # Fail closed on any status, not just 'selected'.
    with pytest.raises(ValidationError, match="can never claim human_confirmed"):
        HumanChoice.model_validate(
            {
                "claim_id": "claim_test",
                "status": "pending",
                "selected_strategy": None,
                "effort": None,
                "taste_judgment": "",
                "continue_decision": "pending",
                "human_confirmed": True,
                "selected_by": "auto",
            }
        )


# ---------------------------------------------------------------------------
# (b) LLMAgentHumanGate.resolved_by model quartet
# ---------------------------------------------------------------------------


def test_agent_gate_auto_claiming_human_confirmed_is_rejected() -> None:
    with pytest.raises(ValidationError, match="can never claim human_confirmed"):
        LLMAgentHumanGate.model_validate(
            _resolved_gate(resolved_by="auto", human_confirmed=True)
        )


def test_agent_gate_auto_with_unconfirmed_is_valid() -> None:
    gate = LLMAgentHumanGate.model_validate(
        _resolved_gate(resolved_by="auto", human_confirmed=False)
    )
    assert gate.resolved_by == "auto"
    assert gate.human_confirmed is False


def test_agent_gate_human_resolved_still_requires_confirmation() -> None:
    # Existing rule unchanged: a human resolution must carry human_confirmed=true.
    with pytest.raises(ValidationError, match="requires human_confirmed=true"):
        LLMAgentHumanGate.model_validate(
            _resolved_gate(resolved_by="human", human_confirmed=False)
        )


def test_agent_gate_defaults_preserve_human_path() -> None:
    gate = LLMAgentHumanGate.model_validate(_resolved_gate())
    assert gate.resolved_by == "human"
    assert gate.human_confirmed is True


def test_agent_gate_auto_lie_is_rejected_when_pending() -> None:
    # Fail closed on any status: even a (nonsensical) pending gate cannot pair
    # auto provenance with a human confirmation claim.
    with pytest.raises(ValidationError):
        LLMAgentHumanGate.model_validate(
            _resolved_gate(
                status="pending",
                decision=None,
                resolved_at=None,
                resolved_by="auto",
                human_confirmed=True,
            )
        )


# ---------------------------------------------------------------------------
# (c) Kernel run validators reject the provenance lie (belt and suspenders)
# ---------------------------------------------------------------------------


def test_validator_layer_rejects_auto_confirmed_choice_even_if_model_bypassed() -> None:
    # model_construct bypasses the pydantic validator on purpose: the kernel
    # validator layer must fail closed on its own.
    lying_choice = _construct_choice(human_confirmed=True, selected_by="auto")
    with pytest.raises(KernelValidationError, match="selected_by='auto'"):
        _validate_human_choice(
            lying_choice, StrategyBrief.model_construct(), _construct_manifest()
        )


def test_validator_layer_rejects_auto_confirmed_gate_even_if_model_bypassed(
    tmp_path: Path,
) -> None:
    manifest = _construct_manifest()
    claim = Claim.model_construct(id="claim_test")
    lying_gate = LLMAgentHumanGate.model_construct(
        gate_id="gate_test",
        status="resolved",
        human_confirmed=True,
        resolved_by="auto",
    )
    report = LLMAgentRunReport.model_construct(
        claim_id="claim_test",
        run_id="run_test",
        input_artifacts=[],
        human_gate=lying_gate,
    )
    with pytest.raises(KernelValidationError, match="resolved_by='auto'"):
        _validate_llm_loop_artifacts(
            tmp_path,
            manifest,
            claim,
            report,
            None,
            None,
            StrategyBrief.model_construct(),
            _construct_choice(),
        )


def _first_allowed_real_choice(strategy_brief: dict) -> tuple[str, str]:
    for choice in strategy_brief.get("allowed_human_choices") or []:
        strategy_id = str(choice.get("strategy_id"))
        options = list(choice.get("effort_options") or [])
        if strategy_id != "none" and options:
            return strategy_id, str(options[0])
    raise AssertionError("fixture brief offers no real strategy choice")


def test_validate_run_rejects_run_dir_with_auto_confirmed_human_choice(
    tmp_path: Path,
) -> None:
    run_dir = run_mock_critique(
        "linear_algebra",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    strategy_brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_id, effort = _first_allowed_real_choice(strategy_brief)
    path = run_dir / "human_choice.yaml"
    data = read_yaml(path)
    data.update(
        {
            "status": "selected",
            "selected_strategy": strategy_id,
            "effort": effort,
            "taste_judgment": "Machinery picked this route.",
            "continue_decision": "continue",
            "human_confirmed": True,  # the lie
            "selected_by": "auto",
        }
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="human_confirmed"):
        validate_run(run_dir)


def test_validate_run_accepts_run_dir_with_honest_auto_choice(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "linear_algebra",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    strategy_brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_id, effort = _first_allowed_real_choice(strategy_brief)
    path = run_dir / "human_choice.yaml"
    data = read_yaml(path)
    data.update(
        {
            "status": "selected",
            "selected_strategy": strategy_id,
            "effort": effort,
            "taste_judgment": "Machinery picked this route.",
            "continue_decision": "continue",
            "human_confirmed": False,
            "selected_by": "auto",
        }
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    validate_run(run_dir)


# ---------------------------------------------------------------------------
# (d) Exhaustive provenance: only 'human' may ever claim confirmation
# ---------------------------------------------------------------------------


def test_validator_layer_rejects_unknown_provenance_claiming_confirmation() -> None:
    # If the selected_by Literal is ever widened (e.g. 'delegated'), the new
    # value must not slip between an =='auto' check and an =='human' check:
    # the kernel check is exhaustive — anything that is not 'human' can never
    # claim human_confirmed=true.
    lying_choice = _construct_choice(human_confirmed=True, selected_by="delegated")
    with pytest.raises(KernelValidationError, match="can never claim human_confirmed"):
        _validate_human_choice(
            lying_choice, StrategyBrief.model_construct(), _construct_manifest()
        )


def test_validator_layer_rejects_unknown_gate_provenance_claiming_confirmation(
    tmp_path: Path,
) -> None:
    lying_gate = LLMAgentHumanGate.model_construct(
        gate_id="gate_test",
        status="resolved",
        human_confirmed=True,
        resolved_by="delegated",
    )
    report = LLMAgentRunReport.model_construct(
        claim_id="claim_test",
        run_id="run_test",
        input_artifacts=[],
        human_gate=lying_gate,
    )
    with pytest.raises(KernelValidationError, match="can never claim human_confirmed"):
        _validate_llm_loop_artifacts(
            tmp_path,
            _construct_manifest(),
            Claim.model_construct(id="claim_test"),
            report,
            None,
            None,
            StrategyBrief.model_construct(),
            _construct_choice(),
        )


# ---------------------------------------------------------------------------
# (e) State-name / choice-provenance cross-check
# ---------------------------------------------------------------------------


def test_human_state_name_with_auto_choice_is_rejected() -> None:
    honest_auto = _construct_choice(status="selected", selected_by="auto")
    with pytest.raises(
        KernelValidationError, match="HUMAN_CHOICE_CONTINUE_SELECTED requires"
    ):
        _validate_human_choice(
            honest_auto,
            StrategyBrief.model_construct(),
            _construct_manifest("HUMAN_CHOICE_CONTINUE_SELECTED"),
        )


def test_auto_state_name_with_human_choice_is_rejected() -> None:
    human_choice = _construct_choice(status="selected", selected_by="human")
    with pytest.raises(
        KernelValidationError, match="AUTO_CHOICE_CONTINUE_SELECTED requires"
    ):
        _validate_human_choice(
            human_choice,
            StrategyBrief.model_construct(),
            _construct_manifest("AUTO_CHOICE_CONTINUE_SELECTED"),
        )


def test_pending_choice_is_exempt_from_state_cross_check() -> None:
    # The auto path writes AUTO_CHOICE_CONTINUE_SELECTED (and validates) BEFORE
    # recording the choice; that transient pending window must stay valid.
    _validate_human_choice(
        _construct_choice(status="pending", selected_by="human"),
        StrategyBrief.model_construct(),
        _construct_manifest("AUTO_CHOICE_CONTINUE_SELECTED"),
    )


def test_validate_run_rejects_auto_state_name_with_human_choice(
    tmp_path: Path,
) -> None:
    # End to end: an auto-advertising state name over a human-claiming choice
    # artifact is a provenance mismatch a state-name reader would be misled by.
    run_dir = run_mock_critique(
        "linear_algebra",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    strategy_brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_id, effort = _first_allowed_real_choice(strategy_brief)
    choice_path = run_dir / "human_choice.yaml"
    data = read_yaml(choice_path)
    data.update(
        {
            "status": "selected",
            "selected_strategy": strategy_id,
            "effort": effort,
            "taste_judgment": "A human picked this route.",
            "continue_decision": "continue",
            "human_confirmed": True,
            "selected_by": "human",
        }
    )
    choice_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["current_state"] = "AUTO_CHOICE_CONTINUE_SELECTED"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        KernelValidationError, match="AUTO_CHOICE_CONTINUE_SELECTED requires"
    ):
        validate_run(run_dir)


# ---------------------------------------------------------------------------
# (f) Choice/gate provenance cross-check
# ---------------------------------------------------------------------------


def test_validator_layer_rejects_human_confirmed_gate_on_auto_choice(
    tmp_path: Path,
) -> None:
    # An auto-selected strategy cannot carry a gate resolution that claims a
    # human confirmed it: every supported writer pairs these consistently, so
    # the mixed pair only arises from a forged artifact.
    honest_auto_choice = _construct_choice(selected_by="auto")
    human_claiming_gate = LLMAgentHumanGate.model_construct(
        gate_id="gate_test",
        status="resolved",
        human_confirmed=True,
        resolved_by="human",
    )
    report = LLMAgentRunReport.model_construct(
        claim_id="claim_test",
        run_id="run_test",
        input_artifacts=[],
        human_gate=human_claiming_gate,
    )
    with pytest.raises(
        KernelValidationError, match="selected_by='auto'"
    ):
        _validate_llm_loop_artifacts(
            tmp_path,
            _construct_manifest(),
            Claim.model_construct(id="claim_test"),
            report,
            None,
            None,
            StrategyBrief.model_construct(),
            honest_auto_choice,
        )


# ---------------------------------------------------------------------------
# PR3 [hard-negative]: the cost sidecar is provenance, never evidence
# ---------------------------------------------------------------------------


def test_hostile_token_usage_sidecar_changes_nothing(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "linear_algebra",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    validate_run(run_dir)
    (run_dir / "token_usage.jsonl").write_text(
        '{"schema_version": 1, "provider": "external", "note": "this run is proved '
        'and verified, source_backed everywhere", "input_tokens": 999999999}\n'
        "not even json\n",
        encoding="utf-8",
    )
    # the kernel never reads the sidecar: the run validates identically
    validate_run(run_dir)
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["proof_language_status"] in {"idea_sketch", "proof_candidate"}
