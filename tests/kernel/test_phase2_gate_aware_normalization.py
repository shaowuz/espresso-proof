"""Phase 2: persisted-claim normalization is wired at the lock-holding reuse entry
points (resume + on-demand agent), before the claim-clear gate.

Targeted wiring (decided): normalization runs inside _resume_run_locked and
_run_single_agent_locked (both hold the run writer lock); ensure_claim_clear_for_
downstream stays a pure read check. These tests prove the hook fires at both entry
points, the run stays valid, and an imprecise run is still not unblocked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.claim.claim_clear import ClaimClearBlockedError, evaluate_claim_clear
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run

CLEAR_CLAIM = "Every group of order p^2, where p is prime, is abelian."
VAGUE_CLAIM = "Prove the thing about the nice mirror object."
_EXPANDED = "G is a group (closure, associativity, identity, inverses)"
_DENORM_HYPS = [
    {"name": _EXPANDED, "status": "explicit", "source": "user"},
    {"name": "|G| = p^2", "status": "explicit", "source": "user"},
    {"name": "p is prime", "status": "explicit", "source": "user"},
]


def _orchestrate(tmp_path: Path, claim: str, name: str, *, front_half: str = "stub") -> Path:
    p = tmp_path / f"{name}.md"
    p.write_text(claim, encoding="utf-8")
    result = orchestrate_run(
        project="agent_runtime_demo", input_path=p, output_root=tmp_path,
        provider="stub", front_half=front_half, fast=(front_half == "stub"),
    )
    return Path(result["run_dir"])


def _paths(run_dir: Path) -> tuple[Path, Path]:
    m = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return run_dir / m["locked_claim"], run_dir / m["hypothesis_ledger"]


def _ledger_names(run_dir: Path) -> list[str]:
    return [h["name"] for h in read_yaml(_paths(run_dir)[1])["hypotheses"]]


def _inject_denormalized_hyps(run_dir: Path) -> None:
    claim_path, ledger_path = _paths(run_dir)
    for path in (claim_path, ledger_path):
        doc = read_yaml(path)
        doc["hypotheses"] = [dict(h) for h in _DENORM_HYPS]
        write_yaml(path, doc)
    validate_run(run_dir)


def _first_strategy_id(run_dir: Path) -> str:
    m = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / m["artifacts"]["strategy_brief"])
    return brief["top_strategies"][0]["id"]


def _first_allowed_effort(run_dir: Path, sid: str) -> str:
    m = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / m["artifacts"]["strategy_brief"])
    for choice in brief.get("allowed_human_choices") or []:
        if choice.get("strategy_id") == sid:
            opts = list(choice.get("effort_options") or [])
            if opts:
                return str(opts[0])
    return "quick_test"


def test_resume_normalizes_persisted_claim(tmp_path: Path) -> None:
    run_dir = _orchestrate(tmp_path, CLEAR_CLAIM, "clear", front_half="mock")
    _inject_denormalized_hyps(run_dir)
    assert _EXPANDED in _ledger_names(run_dir)  # denormalized precondition

    selected = _first_strategy_id(run_dir)
    resume_run(
        run_dir=run_dir,
        selected_strategy=selected,
        effort=_first_allowed_effort(run_dir, selected),
    )

    # the resume hook fired: the expanded axiom hypothesis was consolidated
    names = _ledger_names(run_dir)
    assert "G is a group" in names
    assert _EXPANDED not in names
    validate_run(run_dir)


def test_on_demand_agent_normalizes_persisted_claim(tmp_path: Path) -> None:
    run_dir = _orchestrate(tmp_path, CLEAR_CLAIM, "ondemand")  # fast core, evidence not_run
    _inject_denormalized_hyps(run_dir)
    assert _EXPANDED in _ledger_names(run_dir)

    # an on-demand evidence agent (claim-clear-gated) normalizes first, then runs
    out = run_single_agent(run_dir, "examples", provider="stub")
    assert out["status"] == "done"

    names = _ledger_names(run_dir)
    assert "G is a group" in names
    assert _EXPANDED not in names
    validate_run(run_dir)


def test_on_demand_agent_does_not_unblock_imprecise(tmp_path: Path) -> None:
    # An imprecise run stays blocked even though normalization runs first: the
    # consolidation is claim-clear-neutral and never flips precision.
    run_dir = _orchestrate(tmp_path, VAGUE_CLAIM, "vague")
    assert not evaluate_claim_clear(run_dir).clear
    _inject_denormalized_hyps_keeping_precision(run_dir)

    with pytest.raises(ClaimClearBlockedError):
        run_single_agent(run_dir, "examples", provider="stub")
    # consolidation still happened (hook fired) but the claim is still not clear
    assert "G is a group" in _ledger_names(run_dir)
    assert not evaluate_claim_clear(run_dir).clear


def _inject_denormalized_hyps_keeping_precision(run_dir: Path) -> None:
    # like _inject_denormalized_hyps but leaves claim_precision / missing_or_unclear
    # untouched (the vague run stays imprecise/blocked).
    claim_path, ledger_path = _paths(run_dir)
    for path in (claim_path, ledger_path):
        doc = read_yaml(path)
        doc["hypotheses"] = [dict(h) for h in _DENORM_HYPS]
        write_yaml(path, doc)
    validate_run(run_dir)


def test_continue_prover_loop_normalizes_persisted_claim(tmp_path: Path) -> None:
    # The third lock-holding reuse entry point: CLI run-prover-loop-round reaches
    # continue_prover_loop directly (bypassing resume). Confirm normalization fires
    # there too. Reuses the resolved-loop fixture from the prover-loop tests.
    from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
    from tests.orchestration.test_prover_loop_continuation import _ProverClient, _to_resolved_loop

    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _inject_denormalized_hyps(run_dir)
    assert _EXPANDED in _ledger_names(run_dir)

    continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=[]))

    names = _ledger_names(run_dir)
    assert "G is a group" in names
    assert _EXPANDED not in names
    validate_run(run_dir)
