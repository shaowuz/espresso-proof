"""S0 tests: generic scaffolder + front-half live-agent plumbing.

These cover the harness only (no stage normalizers yet): an arbitrary claim
scaffolds into a kernel-valid run, the all-skipped front-half pass leaves a run
valid, and ``run-flow --front-half stub`` stays green.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.front_half_agents import (
    FRONT_HALF_STAGES,
    registered_stages,
    run_front_half_agent_pass,
)
from espresso_proof.io import read_yaml
from espresso_proof.llm_agents import StubLLMClient
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.validators import validate_run

ARBITRARY_CLAIM = (
    "The Mathieu group M23 is realizable as a Galois group over Q.\n\n"
    "Please attempt to prove this."
)


def test_new_run_scaffolds_valid_run_for_arbitrary_claim(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "new-run",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.strip().split("Wrote run artifacts to ")[-1])
    assert run_dir.exists()
    # Scaffolder fell back to the generic "unclear" builder and is kernel-valid.
    validate_run(run_dir)


def test_front_half_pass_runs_registered_skips_rest_and_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())

    assert [s["stage"] for s in report["stages"]] == FRONT_HALF_STAGES
    by_stage = {s["stage"]: s["status"] for s in report["stages"]}
    registered = set(registered_stages())
    for stage, status in by_stage.items():
        if stage in registered:
            assert status == "ok", report
        else:
            assert status == "skipped", report
    validate_run(run_dir)


class _FakeClient:
    """A client returning a fixed payload, to probe normalizer safety."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def test_understanding_claim_strips_unsafe_model_fields(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    original = read_yaml(run_dir / artifacts["locked_claim"])

    # A hostile model: tries to self-confirm, claim source-attributed hypotheses,
    # use an out-of-enum status, and overwrite id/raw_text.
    malicious = _FakeClient(
        {
            "id": "attacker_claim",
            "raw_text": "TOTALLY DIFFERENT TEXT",
            "normalized_statement": "M23 is realizable as a Galois group over Q.",
            "objects": ["the Mathieu group M23", "the field Q"],
            "hypotheses": [
                {"name": "G = M23", "status": "explicit", "source": "source"},
                {"name": "base field is Q", "status": "totally-bogus", "source": "user"},
            ],
            "conclusion": "There exists a Galois extension of Q with group M23.",
            "ambiguous_terms": ["realizable"],
            "missing_or_unclear": ["explicit polynomial or construction"],
            "human_confirmed": True,
            "locked": False,
            "proof_status": "proved",
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    # id + raw_text preserved (downstream refs stable; user text not overwritten).
    assert claim["id"] == original["id"]
    assert claim["raw_text"] == original["raw_text"]
    # agent can never self-confirm; claim stays locked.
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True
    # every hypothesis is agent-attributed; out-of-enum status coerced to unclear.
    assert claim["hypotheses"], claim
    for hyp in claim["hypotheses"]:
        assert hyp["source"] == "agent"
        assert hyp["status"] in {"explicit", "inferred", "missing", "unclear"}
    statuses = [h["status"] for h in claim["hypotheses"]]
    assert "unclear" in statuses  # the "totally-bogus" status landed here
    # no stray model fields leaked into the artifact.
    assert "proof_status" not in claim
    # the refined statement is surfaced and ambiguity recorded.
    assert "M23" in claim["normalized_statement"]
    assert claim["ambiguous_terms"]


def test_literature_review_downgrades_source_backed_to_candidate(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to certify references as source_backed / misapplied.
    malicious = _FakeClient(
        {
            "query_summary": "M23 inverse Galois candidates.",
            "candidate_references": [
                {
                    "title": "Topics in Galois Theory (Serre)",
                    "authors": ["J.-P. Serre"],
                    "locator": {"kind": "theorem", "value": "Thm 3.2.1"},
                    "relevance": "core",
                    "status": "source_backed",  # must be downgraded
                    "why_relevant": "rigidity method overview",
                },
                {
                    "title": "Inverse Galois Theory (Malle-Matzat)",
                    "authors": ["Malle", "Matzat"],
                    "locator": {"kind": "unknown", "value": ""},
                    "relevance": "background",
                    "status": "misapplied",  # must be downgraded
                    "why_relevant": "general theory",
                },
            ],
            "known_examples": ["M11, M12 realized over Q"],
            "known_counterexample_families": [],
            "notes": [],
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["literature_review"]
    )
    validate_run(run_dir)

    lit = read_yaml(run_dir / artifacts["literature_review"])
    assert lit["claim_id"] == read_yaml(run_dir / artifacts["locked_claim"])["id"]
    assert len(lit["candidate_references"]) == 2
    for ref in lit["candidate_references"]:
        # never source_backed/misapplied; agent-attributed.
        assert ref["status"] in {"candidate", "unverified", "not_found"}
        assert ref["found_by"] == "literature_review"
    assert lit["known_examples"] == ["M11, M12 realized over Q"]


def test_literature_review_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["literature_review"]
    )
    validate_run(run_dir)


def test_examples_cannot_self_certify_as_evidence(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to declare an example "survived" and a computation "checked".
    malicious = _FakeClient(
        {
            "examples": [
                {
                    "name": "Quotient construction giving M23",
                    "satisfies_all_claim_hypotheses": True,
                    "hypothesis_mismatches": [],
                    "what_it_tests": ["realizability over Q"],
                    "expected_conclusion": "M23 occurs as a Galois group",
                    "observed_or_inferred_behavior": "construction succeeds",
                    "nontriviality": "research_level",
                    "source_or_reason": "agent reasoning",
                    "result_for_current_idea": "survived",  # must be neutralized
                }
            ],
            "computation_checks": [
                {
                    "example_name": "Quotient construction giving M23",
                    "computed_quantity": "Galois group order",
                    "input_data": ["polynomial f"],
                    "result": "|M23| = 10200960",
                    "method": "exact_arithmetic",  # must be neutralized
                    "status": "checked",  # must be neutralized
                    "notes": "trust me",
                }
            ],
        }
    )

    run_front_half_agent_pass(run_dir=run_dir, client=malicious, stages=["examples"])
    validate_run(run_dir)

    examples = read_yaml(run_dir / artifacts["examples"])
    comps = read_yaml(run_dir / artifacts["computation_checks"])
    assert examples["examples"], examples
    for ex in examples["examples"]:
        # an agent-proposed example is never auto-counted as survived/failed evidence.
        assert ex["result_for_current_idea"] == "not_tested"
    assert comps["checks"], comps
    for ch in comps["checks"]:
        # agent computations are proposals, never self-certified checked evidence.
        assert ch["status"] == "human_needed"
        assert ch["method"] == "manual_review_needed"
        assert ch["example_id"] in {ex["id"] for ex in examples["examples"]}


def test_examples_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["examples"]
    )
    validate_run(run_dir)


def test_counterexamples_dropped_hypothesis_and_proof_step_cannot_refute(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to refute the claim via a dropped hypothesis AND via a
    # proof-step attack.
    malicious = _FakeClient(
        {
            "counterexamples": [
                {
                    "name": "Drops the over-Q hypothesis (works over C)",
                    "satisfies_all_claim_hypotheses": False,  # dropped hypothesis
                    "target": "hypothesis",
                    "missing_hypothesis": "base field is Q",
                    "support": "M23 is realizable over C-bar trivially",
                    "explanation": "this refutes it",
                    "refutes_claim": True,  # must be neutralized
                },
                {
                    "name": "Attacks the rigidity step",
                    "satisfies_all_claim_hypotheses": True,
                    "target": "proof_step",
                    "explanation": "the rigidity computation fails",
                    "refutes_claim": True,  # must be neutralized
                    "refutes_step_only": True,
                },
            ],
            "tests": [
                {
                    "description": "Run both counterexamples",
                    "uses_counterexamples": ["cex_001", "cex_002", "bogus_id"],
                    "main_observation": "claim is false",
                    "next_action": "declare refuted",
                    "result": "failed",  # must be neutralized
                    "evidence_status": "checked",  # must be neutralized
                }
            ],
            "summary": "Refuted.",
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["counterexamples"]
    )
    validate_run(run_dir)

    cex = read_yaml(run_dir / artifacts["counterexamples"])
    tests = read_yaml(run_dir / artifacts["tests"])
    assert len(cex["tests"]) == 2
    for t in cex["tests"]:
        # no agent counterexample can auto-refute the claim.
        assert t["refutes_claim"] is False
        assert t["result"] == "not_tested"
    dropped = cex["tests"][0]
    assert dropped["satisfies_all_claim_hypotheses"] is False
    assert dropped["exposes_missing_hypothesis"] is True
    assert dropped["missing_hypothesis"]
    step = cex["tests"][1]
    assert step["target"] == "proof_step"
    assert step["refutes_step_only"] is True
    # admissibility tests stay unverified and only reference known counterexamples.
    for ti in tests["tests"]:
        assert ti["result"] == "not_tested"
        assert ti["evidence_status"] == "human_needed"
        assert "bogus_id" not in ti["uses_counterexamples"]


def test_counterexamples_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["counterexamples"]
    )
    validate_run(run_dir)


def test_strategy_unclear_claim_forced_to_lock_route(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")  # -> idea_grade unclear
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model pushing high-confidence deep routes for a still-unclear claim.
    malicious = _FakeClient(
        {
            "routes": [
                {
                    "title": "Just prove it directly",
                    "why_this_route": "looks doable",
                    "main_risk": "none",
                    "estimated_effort": "deep_exploration",
                    "next_loop_plan": "finish the proof",
                },
                {
                    "title": "Another confident route",
                    "why_this_route": "also doable",
                    "main_risk": "none",
                    "estimated_effort": "long_watch",
                    "next_loop_plan": "grind",
                },
            ],
            "question_for_mathematician": "Which one?",
            "synthesis_note": "These will work.",
        }
    )

    run_front_half_agent_pass(run_dir=run_dir, client=malicious, stages=["strategy"])
    validate_run(run_dir)

    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    # unclear claim => only the canonical claim-lock route is allowed.
    assert [s["id"] for s in brief["top_strategies"]] == ["strategy_lock_claim"]
    route = brief["top_strategies"][0]
    assert route["score"] < 75
    assert route["estimated_effort"] != "deep_exploration"
    # brief routes must match synthesis routes (kernel identity rule).
    assert synthesis["routes"] == brief["top_strategies"]
    # human-choice options include 'none'.
    assert any(c["strategy_id"] == "none" for c in brief["allowed_human_choices"])


def test_strategy_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["strategy"]
    )
    validate_run(run_dir)


def test_attacker_cannot_upgrade_evidence_or_refute(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # First seed counterexamples + admissibility tests (S4).
    seed = _FakeClient(
        {
            "counterexamples": [
                {
                    "name": "Pressure cex A",
                    "satisfies_all_claim_hypotheses": "unclear",
                    "target": "theorem",
                    "explanation": "candidate pressure",
                }
            ],
            "tests": [
                {
                    "description": "seed admissibility test",
                    "uses_counterexamples": ["cex_001"],
                    "main_observation": "seed",
                    "next_action": "run",
                }
            ],
            "summary": "seed",
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=seed, stages=["counterexamples"])
    tests_before = read_yaml(run_dir / artifacts["tests"])
    seed_ids = {t["id"] for t in tests_before["tests"]}

    # Now the attacker tries to declare a refutation with checked/failed evidence.
    attacker = _FakeClient(
        {
            "attacks": [
                {
                    "target_id": "cex_001",
                    "attacks": "proof_step",
                    "survives": True,
                    "observation": "this breaks the proof so the claim is refuted",
                    "next_action": "declare refuted",
                    "result": "failed",  # must be neutralized
                    "evidence_status": "checked",  # must be neutralized
                }
            ],
            "route_blockers": ["rigidity route blocked"],
            "summary": "Refuted via proof-step attack.",
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=attacker, stages=["attacker"])
    validate_run(run_dir)

    tests_after = read_yaml(run_dir / artifacts["tests"])
    after_ids = {t["id"] for t in tests_after["tests"]}
    # existing (seed) test ids preserved.
    assert seed_ids.issubset(after_ids)
    # the attacker's appended observation stays unverified.
    appended = [t for t in tests_after["tests"] if t["id"] not in seed_ids]
    assert appended
    for t in appended:
        assert t["result"] == "not_tested"
        assert t["evidence_status"] == "human_needed"


def test_attacker_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["attacker"]
    )
    validate_run(run_dir)


def test_source_theorem_cannot_self_certify_source_backed(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to certify a source as source_backed with an exact locator
    # and a verified hypothesis match.
    malicious = _FakeClient(
        {
            "source_refs": [
                {
                    "type": "book",
                    "title": "Serre, Topics in Galois Theory",
                    "authors": ["J.-P. Serre"],
                    "locator": {"kind": "theorem", "value": "Thm 3.2.1"},
                    "statement_or_summary": "rigidity gives realizability",
                    "hypothesis_match_note": "matches exactly",
                    "status": "source_backed",  # must be downgraded
                }
            ],
            "steps": [
                {
                    "proof_step_id": "step_rigidity",
                    "needed_theorem": "rigidity criterion",
                    "uses_source_index": 1,
                    "status": "source_backed",  # must be downgraded
                    "hypothesis_match_note": "all hypotheses satisfied",
                    "note": "done",
                }
            ],
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["source_theorem"]
    )
    validate_run(run_dir)

    sg = read_yaml(run_dir / artifacts["source_grounding"])
    assert sg["source_refs"], sg
    for ref in sg["source_refs"]:
        assert ref["status"] != "source_backed"
        assert ref["hypothesis_match"]["matches"] != True  # noqa: E712
    for step in sg["steps"]:
        assert step["status"] != "source_backed"
    # no source_backed steps => no theorem_applicability artifact required.
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "theorem_applicability" not in manifest.get("optional_artifacts", {})


def test_source_theorem_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["source_theorem"]
    )
    validate_run(run_dir)


def test_understanding_claim_with_stub_keeps_vague_claim_honest(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text("Prove the thing about the nice mirror object.", encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True


def test_run_flow_front_half_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--provider",
            "stub",
            "--front-half",
            "stub",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Front-half agents: mode=stub" in result.output
    run_dir = Path(result.output.splitlines()[0].split("Wrote run artifacts to ")[-1])
    validate_run(run_dir)


def test_run_flow_front_half_rejects_bad_mode(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--front-half",
            "bogus",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
