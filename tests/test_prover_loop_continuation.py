"""Prover multi-round continuation: driver stop conditions + safety negatives.

The kernel stays the gate: every round is re-validated, nothing upgrades
proof/source/evidence status, and the loop fails closed. See
docs/goals/PROVER_MULTI_ROUND_CONTINUATION_PLAN.md.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.orchestrator import (
    _resolve_and_build_loop,
    orchestrate_run,
    resume_run,
)
import espresso_proof.orchestration.prover_loop_driver as drv
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from tests.test_evidence_aware_grade import FakeClaude

_FORBIDDEN = ("proof-close", "proof complete", "we proved", "proved the", "this establishes the theorem")


class _ProverClient:
    """Injectable prover/reviewer client (provider must be a real enum value)."""

    provider = "stub"
    model = "stub-prover"

    def __init__(self, *, objections, prover_summary="candidate route step"):
        self._objections = objections
        self._prover_summary = prover_summary

    def complete_json(self, *, system: str, prompt: str) -> dict:
        s = system.lower()
        if "reviewer" in s or "verifier" in s or "review" in s:
            # Tie ALL blocker-feeding fields to objections so an empty objection
            # set yields empty blockers (the "clean" path).
            return {
                "summary": "review",
                "steps": ["check the route"],
                "objections": list(self._objections),
                "source_requests": list(self._objections),
                "missing_hypotheses": [],
                "risks": list(self._objections),
                "verdict": "needs_revision" if self._objections else "needs_sources",
                "recommended_human_action": "revise",
            }
        return {
            "summary": self._prover_summary,
            "steps": ["state the route as a conditional argument"],
            "source_requests": ["exact locator for the key lemma"],
            "risks": ["candidate route"],
            "verdict": "candidate",
            "recommended_human_action": "revise",
        }


def _to_resolved_loop(tmp_path: Path, *, effort: str) -> tuple[Path, str]:
    """Orchestrate to the gate, record a human choice, resolve + build prover_loop."""
    claim = tmp_path / "claim.md"
    claim.write_text("Every group of order p^2, where p is a prime, is abelian.\n", encoding="utf-8")
    result = orchestrate_run(
        project="pml",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = result["run_dir"]
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    claim_obj = read_yaml(run_dir / artifacts["locked_claim"])
    human_choice = {
        "claim_id": claim_obj["id"],
        "status": "selected",
        "selected_strategy": selected,
        "effort": effort,
        "taste_judgment": "",
        "continue_decision": "continue",
        "human_confirmed": True,
    }
    write_yaml(run_dir / artifacts["human_choice"], human_choice)
    _resolve_and_build_loop(run_dir, decision="continue", reason="test", human_choice=human_choice)
    return run_dir, selected


def _bump_budget(run_dir: Path, *, max_rounds: int, max_agent_calls: int) -> None:
    """Raise the loop budget so the driver's no-progress / clean stops (not the
    budget) are the ones exercised. Test-only; the run stays kernel-valid."""
    loop_path = run_dir / "prover_loop.yaml"
    loop = read_yaml(loop_path)
    loop["budget"]["max_rounds"] = max_rounds
    loop["budget"]["max_agent_calls"] = max_agent_calls
    write_yaml(loop_path, loop)
    validate_run(run_dir)


# --- Reachability matrix ----------------------------------------------------

def test_stop_no_progress(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _bump_budget(run_dir, max_rounds=6, max_agent_calls=60)  # let no-progress fire first
    client = _ProverClient(objections=["loc X", "hyp Y"])  # identical every round
    result = continue_prover_loop(run_dir=run_dir, client=client)
    validate_run(run_dir)
    assert result["stop_reason"] == "no_progress", result
    assert result["rounds_run"] >= 1


def test_stop_budget_rounds(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")  # max_rounds=2
    client = _ProverClient(objections=["loc X"])
    result = continue_prover_loop(run_dir=run_dir, client=client)
    validate_run(run_dir)
    assert result["stop_reason"] == "budget_rounds", result
    loop = result["loop_report"]
    assert loop["budget"]["rounds_used"] == loop["budget"]["max_rounds"] == 2


def test_stop_clean_when_no_blockers(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _bump_budget(run_dir, max_rounds=6, max_agent_calls=60)
    client = _ProverClient(objections=[])  # reviewers raise nothing -> empty blockers
    result = continue_prover_loop(run_dir=run_dir, client=client)
    validate_run(run_dir)
    assert result["stop_reason"] == "clean", result


def test_stop_ask_human_does_not_append(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    # pre-seed loop_decision = ask_human (a human-gate signal); driver must halt.
    loop_path = run_dir / "prover_loop.yaml"
    loop = read_yaml(loop_path)
    loop["loop_decision"] = "ask_human"
    write_yaml(loop_path, loop)
    validate_run(run_dir)
    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["x"]))
    assert result["stop_reason"] == "loop_decision_ask_human"
    assert result["rounds_run"] == 0


def test_validation_error_mid_loop_rolls_back(tmp_path: Path, monkeypatch) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _bump_budget(run_dir, max_rounds=6, max_agent_calls=60)
    before = read_yaml(run_dir / "prover_loop.yaml")
    rounds_before = before["budget"]["rounds_used"]
    attempts_before = len(before["attempts"])
    files_before = len(list((run_dir / "prover_rounds").glob("*.yaml")))

    real_validate = drv.validate_run
    calls = {"n": 0}

    def flaky(rd):  # pre-loop validate passes; the post-append validate raises
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KernelValidationError("injected mid-loop validation failure")
        return real_validate(rd)

    monkeypatch.setattr(drv, "validate_run", flaky)
    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["loc X"]))
    assert result["stop_reason"] == "validation_error"
    assert result["rounds_run"] == 0
    # rolled back to the last valid state: real kernel validation passes again.
    real_validate(run_dir)
    after = read_yaml(run_dir / "prover_loop.yaml")
    assert after["budget"]["rounds_used"] == rounds_before
    assert len(after["attempts"]) == attempts_before
    assert len(list((run_dir / "prover_rounds").glob("*.yaml"))) == files_before


def test_max_rounds_arg_caps_below_budget(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    # cap at the current rounds_used (1) -> no append.
    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["x"]), max_rounds=1)
    assert result["rounds_run"] == 0
    assert result["stop_reason"] == "budget_rounds"


# --- Safety negatives -------------------------------------------------------

def test_round_does_not_upgrade_status(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["loc X", "hyp Y"]))
    validate_run(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    for attempt in loop["attempts"]:
        assert attempt["status"] in {"candidate", "needs_repair"}
    # round artifacts: prover status pinned needs_human; no upgraded status fields.
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    for entry in index["rounds"]:
        rep = read_yaml(run_dir / entry["artifact_path"])
        assert rep["prover_attempt"]["status"] == "needs_human"
        # round artifact structurally carries no proof/source/evidence status field.
        assert "proof_language_status" not in rep
        assert "source_grounding" not in rep
        assert "idea_grade" not in rep
        # no field VALUE is source_backed (the safety_note may mention the word).
        assert rep["prover_attempt"].get("status") != "source_backed"
    # idea grade proof status unchanged (still not a checked/verified proof).
    grade = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["idea_grade"])
    assert grade["proof_language_status"] == "idea_sketch"
    assert grade["status"] != "refuted"  # a loop cannot refute the claim


def test_forbidden_proof_language_never_persists(tmp_path: Path) -> None:
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    nasty = _ProverClient(
        objections=["loc X"],
        prover_summary="We proved the theorem; the proof is complete. proof-close.",
    )
    continue_prover_loop(run_dir=run_dir, client=nasty)
    validate_run(run_dir)
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    for entry in index["rounds"]:
        blob = (run_dir / entry["artifact_path"]).read_text(encoding="utf-8").lower()
        for phrase in _FORBIDDEN:
            assert phrase not in blob, f"round artifact leaked {phrase!r}"


def test_unresolved_gate_does_not_append(tmp_path: Path) -> None:
    # orchestrate to the gate (NOT resolved): prover_loop.loop_decision == ask_human.
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2 is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="pml", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = res["run_dir"]
    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["x"]))
    assert result["rounds_run"] == 0
    assert result["stop_reason"] in {"loop_decision_ask_human", "loop_decision_stop"}


def test_resume_none_selection_runs_zero_rounds(tmp_path: Path) -> None:
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2 is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="pml", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = res["run_dir"]
    memo = resume_run(run_dir=run_dir, selected_strategy="none", effort="quick_test")
    validate_run(run_dir)
    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert agent_run["human_gate"]["status"] == "resolved"
    assert agent_run["human_gate"]["decision"] == "stop"
    assert loop["loop_decision"] == "stop"
    assert loop["selected_strategy_id"] is None
    for attempt in loop["attempts"]:
        assert attempt["time_allocation"] == "stop"
        assert attempt["status"] == "stopped"
        assert attempt["next_action"].startswith("Stop before spending another prover round")
    assert "Loop:" not in memo  # no rounds ran for a 'none'/stop selection


def test_resume_is_idempotent(tmp_path: Path) -> None:
    run_dir, selected = _to_resolved_loop_via_resume(tmp_path)
    loop_before = read_yaml(run_dir / "prover_loop.yaml")
    n_before = len(loop_before["attempts"])
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass",
               prover_client=_ProverClient(objections=["loc X"]))
    loop_after = read_yaml(run_dir / "prover_loop.yaml")
    assert len(loop_after["attempts"]) == n_before  # no double-append


def test_resume_runs_multiple_rounds_and_memo_is_honest(tmp_path: Path) -> None:
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2, where p is a prime, is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="pml", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = res["run_dir"]
    brief = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    memo = resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass",
                      prover_client=_ProverClient(objections=["loc X", "hyp Y"]))
    validate_run(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert loop["budget"]["rounds_used"] >= 2  # ran >1 round
    assert "Prover loop: ran" in memo
    assert "stopped because" in memo
    assert "budget_rounds" not in memo  # stop reason renders as words (Goal 31)
    lower = memo.lower()
    for phrase in _FORBIDDEN + ("progress", "improved", "closer"):
        assert phrase not in lower


def _to_resolved_loop_via_resume(tmp_path: Path) -> tuple[Path, str]:
    """A run already taken through resume once (terminal COMPLETED_MEMO)."""
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2 is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="pml", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = res["run_dir"]
    brief = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass",
               prover_client=_ProverClient(objections=["loc X"]))
    return run_dir, selected


# --- Goal 44-53 autofix #2: the strategy human_choice and the LLM-agent human gate
#     are DISTINCT gates. A 'selected'+'continue' human_choice (which model-requires
#     human_confirmed=true) may legitimately coexist with a pending prover-pass gate
#     (the human picked a route; the prover output still awaits review). The kernel
#     already forbids prover WORK on a pending gate; it must NOT, however, reject this
#     legitimate selected+pending state. This guard locks that so the Goal-47
#     'fabricated human_confirmed' misread is never "fixed" with an unsafe cross-check.


def test_selected_continue_with_pending_prover_gate_is_valid(tmp_path: Path) -> None:
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2 is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="pml", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = res["run_dir"]
    art = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    validate_run(run_dir)  # at the gate: valid
    # Record a 'continue' selection (human picked a route) WITHOUT resolving the
    # prover-pass gate. prover_loop stays in its pending/ask_human form.
    brief = read_yaml(run_dir / art["strategy_brief"])
    hc = read_yaml(run_dir / art["human_choice"])
    hc.update({
        "status": "selected",
        "selected_strategy": brief["top_strategies"][0]["id"],
        "effort": "normal_pass",
        "continue_decision": "continue",
        "human_confirmed": True,
    })
    write_yaml(run_dir / art["human_choice"], hc)
    # This legitimate intermediate state MUST remain valid (distinct gates).
    validate_run(run_dir)
    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    assert agent_run["human_gate"]["status"] == "pending"  # prover gate still pending
    loop = read_yaml(run_dir / "prover_loop.yaml")
    # ...and the kernel still forbids prover WORK on that pending gate:
    assert loop["loop_decision"] in {"ask_human", "stop"}
    for attempt in loop["attempts"]:
        assert attempt["time_allocation"] in {"ask_human", "stop"}
        assert attempt["status"] in {"waiting_human", "stopped"}


# --- Goal 44-53 escalation, now gated by Phase 4's STRICT blocker classification:
#     the one-extra-round grant fires ONLY when the whole latest blocker set is
#     bounded-repairable (proof-step / computation). Human-gated blockers (reviewer
#     objections, missing hypotheses, source requests) WITHHOLD it (route to human).


def _simulate_quick_test_budget(run_dir: Path) -> None:
    """Shrink the loop budget to a spent quick_test (1 round, 4 agent calls) so the
    effort budget -- not an explicit cap -- is what would stop the loop at round 1."""
    loop_path = run_dir / "prover_loop.yaml"
    loop = read_yaml(loop_path)
    loop["budget"]["max_rounds"] = 1
    loop["budget"]["max_agent_calls"] = loop["budget"]["agent_calls_used"]
    write_yaml(loop_path, loop)
    validate_run(run_dir)


def test_no_escalation_when_blockers_are_human_gated(tmp_path: Path) -> None:
    # Phase 4 STRICT: the standard standing-risk blockers (reviewer objections,
    # missing hypotheses, source requests) are all human-gated, so the bounded
    # extra round is WITHHELD -- the loop routes to a human instead of burning a
    # round on a fix it can never make. (This corrects the old Goals 44-53 behavior
    # that escalated on any unresolved blocker.)
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _simulate_quick_test_budget(run_dir)
    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["loc X", "hyp Y"]))
    validate_run(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    # NO extra round was granted: stayed at round 1, stopped on the budget.
    assert {a["round_index"] for a in loop["attempts"]} == {1}
    assert loop["budget"]["max_rounds"] == 1  # no budget grant
    assert result["stop_reason"] in {"budget_rounds", "budget_agent_calls"}
    assert any("no escalation: human-gated" in n for n in result["notes"]), result["notes"]


def test_escalation_fires_when_gate_allows(tmp_path: Path, monkeypatch) -> None:
    # Positive direction: when the STRICT gate deems the latest blockers repairable,
    # the one extra round IS granted -- proving the gate is wired INTO the escalation
    # decision (not just the human-gated withhold path). Forcing the gate True lets
    # us exercise the escalate branch without fragile hand-crafted repairable rounds
    # (validate_run cross-checks reviewer_blockers vs attempt blockers).
    import espresso_proof.orchestration.prover_loop_driver as drv

    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _simulate_quick_test_budget(run_dir)
    monkeypatch.setattr(drv, "_blockers_all_repairable", lambda texts, prov: bool(texts))

    result = continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["loc X", "hyp Y"]))
    validate_run(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert {a["round_index"] for a in loop["attempts"]} >= {1, 2}  # escalated to round 2
    assert loop["budget"]["max_rounds"] <= 2  # still capped at one extra round
    assert any("escalated" in n for n in result["notes"]), result["notes"]


def test_escalation_is_capped_at_one_extra_round(tmp_path: Path) -> None:
    # invariant: the grant never exceeds one extra round, whatever the blockers.
    run_dir, _ = _to_resolved_loop(tmp_path, effort="normal_pass")
    _simulate_quick_test_budget(run_dir)
    continue_prover_loop(run_dir=run_dir, client=_ProverClient(objections=["loc X", "hyp Y"]))
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert max(a["round_index"] for a in loop["attempts"]) <= 2
    assert loop["budget"]["max_rounds"] <= 2  # never grants more than one round


def test_blockers_all_repairable_gate() -> None:
    # Phase 4 STRICT gate decision (unit): all-repairable -> True; any human-gated
    # or empty -> False.
    from espresso_proof.orchestration.prover_loop_driver import _blockers_all_repairable

    assert _blockers_all_repairable(["a proof step does not follow"], {}) is True
    assert _blockers_all_repairable(["recompute the order check at step 2"], {}) is True
    # a source-gap blocker (keyword) makes the set non-repairable
    assert _blockers_all_repairable(["a proof step", "need exact citation"], {}) is False
    # reviewer-objection / missing-hypothesis provenance -> human-gated -> False
    assert _blockers_all_repairable(["x"], {"x": {"objection"}}) is False
    assert _blockers_all_repairable(["x"], {"x": {"missing_hypothesis"}}) is False
    # no blockers -> not escalatable
    assert _blockers_all_repairable([], {}) is False
