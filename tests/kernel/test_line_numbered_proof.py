"""Goal 69: Line-Numbered Proof Writer V1 — the sealable proof artifact.

The agent-consensus mock check may ONLY seal a structured line_numbered_proof.yaml
(each line has a statement + justification + dependencies), reviewed line-by-line by
the three proof reviewers who must cite line_id. A route / proposed_steps /
advisory_proof_sketch is NOT sealable. Stays advisory (never machine-verified /
source-backed / checked / proved).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    LineNumberedProof,
    ProofLine,
)
from espresso_proof.kernel.validators import (
    KernelValidationError,
    _first_forbidden_proof_language,
    validate_run,
)
from espresso_proof.orchestration.agent_consensus_seal import (
    NoLineNumberedProofError,
    seal_agent_consensus,
)
from espresso_proof.orchestration.proof_writer import write_line_numbered_proof
from espresso_proof.orchestration.proof_gap_resolver import (
    _normalize_agent_candidates,
    resolve_proof_gaps,
)
from espresso_proof.orchestration.agent_flow_status import STAGE_STATUS_FILENAME
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.ui.descriptions import VERIFIED_PROOF_STATUSES, describe_proof_language
from tests.kernel.test_proof_verification_bridge_v2 import _build

_AMBER = "agent_consensus_mock_checked"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _artifacts(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")["artifacts"]


def _grade(run_dir: Path) -> dict:
    return read_yaml(run_dir / _artifacts(run_dir)["idea_grade"])


def _stage(report: dict, stage_id: str) -> dict:
    return next(stage for stage in report["stages"] if stage["stage_id"] == stage_id)


def _admissible_run(tmp_path: Path) -> Path:
    """A claim-cleared run whose kernel review passes cleanly (so a consensus over a
    line-numbered proof is admissible)."""
    run_dir = _build(tmp_path)
    a = _artifacts(run_dir)
    rev = read_yaml(run_dir / a["review"])
    for r in rev["reviewers"]:
        r["verdict"] = "pass"
        r["objections"] = []
        r["required_reruns"] = []
    rev.update(
        attempt_status="passed", verdict="continue", strongest_objection="none",
        theorem_drift=[], missing_hypotheses=[], source_risks=[], example_risks=[],
        downgrade_reasons=[],
    )
    write_yaml(run_dir / a["review"], rev)
    g = read_yaml(run_dir / a["idea_grade"])
    g.update(
        status="promising", score=80, human_review_required=False,
        missing_hypotheses=[], source_gaps=[], counterexample_candidates=[], failed_tests=[],
    )
    write_yaml(run_dir / a["idea_grade"], g)
    validate_run(run_dir)
    return run_dir


def _run_with_proof(tmp_path: Path) -> Path:
    run_dir = _admissible_run(tmp_path)
    write_line_numbered_proof(run_dir, client=StubLLMClient())  # stub parity proof
    return run_dir


class _ProofReviewStub(StubLLMClient):
    """Proof-consensus reviewer stub with per-role verdicts; cites the proof's line_ids
    (parsed from the prompt) unless ``cite=False``."""

    def __init__(
        self,
        verdicts: dict | None = None,
        cite: bool = True,
        extra_cited_line_ids: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._verdicts = verdicts or {}
        self._cite = cite
        self._extra_cited_line_ids = extra_cited_line_ids or []
        self.proof_review_calls = 0

    def complete_json(self, *, system: str, prompt: str) -> dict:
        lo = system.lower()
        if "proof-consensus review" not in lo:
            return super().complete_json(system=system, prompt=prompt)
        self.proof_review_calls += 1
        if "line-proof reviewer" in lo:
            role = "line_proof_reviewer"
        elif "applicability reviewer" in lo:
            role = "theorem_lemma_applicability_reviewer"
        else:
            role = "adversarial_gap_reviewer"
        import re

        ids = re.findall(r"['\"]?line_id['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", prompt)
        cited_ids = sorted(set(ids + self._extra_cited_line_ids))
        return {
            "verdict": self._verdicts.get(role, "no_fatal_flaw_found"),
            "looked_for": "non-sequiturs, misapplied lemmas, missing cases",
            "finding": "stub proof review",
            "cited_line_ids": (cited_ids if self._cite else []),
            "residual_objections": [],
        }


class _GapResolverStub(StubLLMClient):
    def complete_json(self, *, system: str, prompt: str) -> dict:
        if "proof gap resolver" not in system.lower():
            return super().complete_json(system=system, prompt=prompt)
        return {
            "candidates": [
                {
                    "line_id": "L1",
                    "dependency_ref": "L1.gap.1",
                    "dependency_type": "unchecked_gap",
                    "action": "attach_candidate_theorem_ref",
                    "proposed_ref": "candidate.lagrange",
                    "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
                    "proposed_statement": "If H is a subgroup of a finite group G, then the order of H divides the order of G.",
                    "source_locator": "candidate web locator only",
                    "source_kind": "web",
                    "applicability_review": "candidate_match",
                    "reviewer_notes": ["Candidate source still needs review."],
                }
            ]
        }


class _GapResolverWithSourceStub(_GapResolverStub):
    def __init__(self, source: Path) -> None:
        super().__init__()
        self.source = source

    def complete_json(self, *, system: str, prompt: str) -> dict:
        raw = super().complete_json(system=system, prompt=prompt)
        if "proof gap resolver" in system.lower():
            raw["candidates"][0]["source_url"] = str(self.source)
        return raw


def _add_single_gap(run_dir: Path) -> None:
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["gaps"] = ["external theorem locator missing"]
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def test_proof_line_and_proof_models() -> None:
    proof = LineNumberedProof(
        claim_id="c1", strategy_id="s1", proof_kind="parity",
        lines=[
            ProofLine(line_id="L1", statement="n^2-n=n(n-1)", justification="distribute"),
            ProofLine(line_id="L2", statement="2 | n(n-1)", justification="one factor even", depends_on=["L1"]),
        ],
    )
    assert proof.status == "advisory"  # pinned advisory
    assert proof.lines[1].dependencies[0].type == "prior_line"
    assert proof.lines[1].dependencies[0].ref == "L1"


def test_proof_line_dependency_graph_migrates_legacy_gaps_and_refs() -> None:
    proof = LineNumberedProof(
        claim_id="c1",
        strategy_id="s1",
        proof_kind="lagrange",
        lines=[
            ProofLine(line_id="L1", statement="G finite", justification="hypothesis"),
            ProofLine(
                line_id="L2",
                statement="|H| divides |G|",
                justification="Lagrange theorem",
                dependencies=[
                    {
                        "type": "prior_line",
                        "ref": "L1",
                        "status": "candidate",
                        "role": "uses earlier hypothesis line",
                        "authority": "agent_claimed_definition",
                    },
                    {
                        "type": "definition",
                        "ref": "subgroup_definition",
                        "status": "resolved",
                        "role": "subgroup definition",
                        "authority": "agent_locked_definition",
                    },
                    {
                        "type": "theorem_ref",
                        "ref": "lagrange_candidate",
                        "status": "candidate",
                        "role": "subgroup order divisibility",
                        "authority": "agent_external_theorem",
                    }
                ],
                depends_on=["L1"],
                source_refs=["lagrange_candidate"],
                gaps=["missing exact theorem locator"],
            ),
        ],
    )
    line = proof.lines[1]
    assert {(dep.type, dep.ref) for dep in line.dependencies} >= {
        ("prior_line", "L1"),
        ("theorem_ref", "lagrange_candidate"),
        ("unchecked_gap", "L2.gap.1"),
    }
    theorem_dep = next(dep for dep in line.dependencies if dep.type == "theorem_ref")
    assert theorem_dep.status == "candidate"
    assert theorem_dep.authority == "agent_claimed_theorem_ref"
    prior_dep = next(dep for dep in line.dependencies if dep.type == "prior_line")
    assert prior_dep.status == "resolved"
    assert prior_dep.authority == ""
    definition_dep = next(dep for dep in line.dependencies if dep.type == "definition")
    assert definition_dep.authority == ""


def test_write_proof_cli_refreshes_stage_status_projection(tmp_path: Path) -> None:
    claim = tmp_path / "claim.md"
    claim.write_text(
        "Every group of order p^2, where p is a prime, is abelian.\n",
        encoding="utf-8",
    )
    result_run = orchestrate_run(
        project="goal69_status",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result_run["run_dir"])

    result = CliRunner().invoke(app, ["write-proof", "--run", str(run_dir)])

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    report = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    assert _stage(report, "line_numbered_proof_writer")["status"] == "done"


def test_failed_seal_projects_failed_stage_status_after_real_prover_path(tmp_path: Path) -> None:
    claim = tmp_path / "claim.md"
    claim.write_text(
        "Every group of order p^2, where p is a prime, is abelian.\n",
        encoding="utf-8",
    )
    result_run = orchestrate_run(
        project="goal69_failed_seal_status",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result_run["run_dir"])
    write_result = CliRunner().invoke(app, ["write-proof", "--run", str(run_dir)])
    assert write_result.exit_code == 0, write_result.output
    _add_single_gap(run_dir)

    seal_result = CliRunner().invoke(app, ["seal-consensus", "--run", str(run_dir)])

    assert seal_result.exit_code == 1
    report = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    seal_stage = _stage(report, "agent_consensus_seal")
    assert seal_stage["status"] == "failed"
    assert "seal failed closed" in seal_stage["status_reason"]
    validate_run(run_dir)


def test_gap_resolver_keeps_non_sealable_unresolved_edges_as_unchecked_gap() -> None:
    candidates = _normalize_agent_candidates(
        raw={"candidates": []},
        unresolved=[
            {
                "line_id": "L2",
                "dependency": {
                    "type": "prior_line",
                    "ref": "L1",
                    "status": "candidate",
                    "role": "earlier line was marked unresolved by the agent",
                },
            }
        ],
    )

    assert candidates == [
        {
            "line_id": "L2",
            "dependency_ref": "L1",
            "dependency_type": "unchecked_gap",
            "action": "keep_gap",
            "needed_statement": "earlier line was marked unresolved by the agent",
            "reviewer_notes": ["No candidate repair was produced for this dependency."],
        }
    ]


def test_proof_rejects_route_only_and_bad_lines() -> None:
    with pytest.raises(ValidationError):  # no lines == a route, not a proof
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x", lines=[])
    with pytest.raises(ValidationError):  # line without justification == a route step
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x",
                          lines=[ProofLine(line_id="L1", statement="s", justification="")])
    with pytest.raises(ValidationError):  # forward/unknown dependency
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x",
                          lines=[ProofLine(line_id="L1", statement="s", justification="j", depends_on=["L9"])])


def test_proof_has_no_status_field() -> None:
    with pytest.raises(ValidationError):  # StrictBaseModel forbids smuggling a status
        LineNumberedProof(
            claim_id="c1", strategy_id="s1", proof_kind="x",
            lines=[ProofLine(line_id="L1", statement="s", justification="j")],
            proof_language_status="lean_checked",
        )


# --------------------------------------------------------------------------- #
# the writer
# --------------------------------------------------------------------------- #
def test_writer_produces_structured_proof(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)
    record = write_line_numbered_proof(run_dir, client=StubLLMClient())
    validate_run(run_dir)
    assert record["status"] == "advisory"
    assert len(record["lines"]) >= 3
    for line in record["lines"]:  # every line is a real proof line, not a route step
        assert line["statement"].strip() and line["justification"].strip()
    assert "line_numbered_proof" in read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]


def test_writer_prefers_direct_source_backed_theorem(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)
    claim = read_yaml(run_dir / "locked_claim.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    source_grounding["source_refs"].append(
        {
            "id": "verified_direct_claim",
            "type": "web_page",
            "title": "A checked theorem source",
            "authors": ["A. Author"],
            "locator": {"kind": "theorem", "value": "Theorem 2.1"},
            "statement_or_summary": f"If the locked hypotheses hold, then {claim['conclusion']}",
            "hypothesis_match": {"matches": True, "mismatches": [], "matched": []},
            "confidence": "high",
            "status": "source_backed",
        }
    )
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)

    record = write_line_numbered_proof(run_dir, client=StubLLMClient())

    assert record["proof_kind"] == "direct_source_grounded_theorem"
    assert [line["line_id"] for line in record["lines"]] == ["L1", "L2"]
    assert record["lines"][1]["gaps"] == []
    theorem_dep = next(
        dep for dep in record["lines"][1]["dependencies"] if dep["type"] == "theorem_ref"
    )
    assert theorem_dep["ref"] == "verified_direct_claim"
    assert theorem_dep["authority"] == "source_grounded_theorem_ref"
    assert theorem_dep["source_grounded"] is True
    validate_run(run_dir)


# --------------------------------------------------------------------------- #
# the 6 required acceptance cases
# --------------------------------------------------------------------------- #
def test_positive_parity_proof_seals(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    assert record["consensus"] is True
    assert {e["reviewer_id"] for e in record["entries"]} == {
        "line_proof_reviewer", "theorem_lemma_applicability_reviewer", "adversarial_gap_reviewer"
    }
    assert all(e["cited_line_ids"] for e in record["entries"])  # every PASS cites lines
    assert _grade(run_dir)["proof_language_status"] == _AMBER


def test_negative_route_only_cannot_seal(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)  # NO line_numbered_proof written (route-only)
    with pytest.raises(NoLineNumberedProofError):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_line_without_justification_cannot_seal(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)
    a = _artifacts(run_dir)
    claim_id = read_yaml(run_dir / a["locked_claim"])["id"]
    # write a route-shaped proof (a line with NO justification), bypassing the model
    write_yaml(run_dir / "line_numbered_proof.yaml", {
        "schema_version": 1, "claim_id": claim_id, "strategy_id": "s1", "proof_kind": "x",
        "status": "advisory", "generated_at": "t",
        "lines": [{"line_id": "L1", "statement": "n^2-n is even", "justification": "", "depends_on": []}],
    })
    man = read_yaml(run_dir / "run_manifest.yaml")
    man["optional_artifacts"]["line_numbered_proof"] = "line_numbered_proof.yaml"
    write_yaml(run_dir / "run_manifest.yaml", man)
    with pytest.raises(KernelValidationError):  # malformed proof -> run invalid -> not sealable
        validate_run(run_dir)
    reviewer = _ProofReviewStub()
    with pytest.raises(NoLineNumberedProofError):
        seal_agent_consensus(run_dir, client=reviewer)
    assert reviewer.proof_review_calls == 0


def test_negative_reviewer_pass_without_line_id_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    with pytest.raises(KernelValidationError):  # PASS citing no line -> gate fails -> rollback + raise
        seal_agent_consensus(run_dir, client=_ProofReviewStub(cite=False))
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_reviewer_pass_with_unknown_line_id_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    with pytest.raises(KernelValidationError, match="unknown line_id"):
        seal_agent_consensus(
            run_dir,
            client=_ProofReviewStub(extra_cited_line_ids=["BOGUS_LINE"]),
        )
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_unresolved_proof_gaps_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["gaps"] = ["external theorem locator missing"]
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_candidate_theorem_dependency_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "agent_claimed_lagrange",
            "status": "candidate",
            "authority": "agent_claimed_theorem_ref",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_self_asserted_human_allowed_dependency_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "human_allowed_theorem.lagrange",
            "status": "accepted_by_human",
            "authority": "human_allowed_theorem_ref",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
            "source_grounded": False,
            "machine_verified": False,
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_self_asserted_source_resolved_dependency_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "agent_claimed_lagrange",
            "status": "resolved",
            "authority": "agent_claimed_theorem_ref",
            "source_locator": "Theorem 1.2 somewhere",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_source_grounded_theorem_dependency_can_seal_when_cross_checked(
    tmp_path: Path,
) -> None:
    run_dir = _run_with_proof(tmp_path)
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    source_grounding["source_refs"].append(
        {
            "id": "verified_lagrange",
            "type": "web_page",
            "title": "A checked algebra source",
            "authors": ["A. Algebraist"],
            "locator": {"kind": "theorem", "value": "Theorem 1.2"},
            "statement_or_summary": "If H is a subgroup of finite G, then |H| divides |G|.",
            "hypothesis_match": {"matches": True, "mismatches": [], "matched": []},
            "confidence": "high",
            "status": "source_backed",
        }
    )
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "verified_lagrange",
            "status": "candidate",
            "authority": "source_grounded_theorem_ref",
            "source_grounded": True,
            "machine_verified": False,
            "source_locator": "A checked algebra source, Theorem 1.2",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    record = seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert record["consensus"] is True
    assert _grade(run_dir)["proof_language_status"] == _AMBER


def test_self_asserted_source_grounded_dependency_without_source_ref_cannot_seal(
    tmp_path: Path,
) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "verified_missing",
            "status": "candidate",
            "authority": "source_grounded_theorem_ref",
            "source_grounded": True,
            "machine_verified": False,
            "source_locator": "A checked algebra source, Theorem 1.2",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())

    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_proof_gap_resolver_attaches_candidate_but_does_not_clear_gap(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)

    report = resolve_proof_gaps(run_dir, client=_GapResolverStub())
    validate_run(run_dir)

    assert report["candidates"][0]["action"] == "attach_candidate_theorem_ref"
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    deps = proof["lines"][0]["dependencies"]
    assert any(dep["ref"] == "candidate.lagrange" for dep in deps)
    assert proof["lines"][0]["gaps"] == ["external theorem locator missing"]
    with pytest.raises(RuntimeError, match="unresolved dependencies"):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())


def test_proof_gap_resolver_executes_concrete_source_retrieval_attempt(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)
    source = tmp_path / "lagrange.txt"
    source.write_text(
        "Theorem 6.10. If G is a finite group and H is a subgroup of G, then |H| divides |G|.",
        encoding="utf-8",
    )

    report = resolve_proof_gaps(run_dir, client=_GapResolverWithSourceStub(source))
    validate_run(run_dir)

    assert any(action["status"].startswith("executed_") for action in report["repair_plan"])
    assert (run_dir / "theorem_extraction.yaml").exists()
    assert (run_dir / "retrieval_resolution.yaml").exists()
    round_index = read_yaml(run_dir / "gap_repair_rounds" / "index.yaml")
    round_record = read_yaml(run_dir / round_index["rounds"][0]["artifact"])
    assert round_record["status"] == "attempted"
    assert any(
        attempt["action"] == "source_retrieval"
        and attempt["status"].startswith("executed_")
        for attempt in round_record["execution_attempts"]
    )
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["lines"][0]["gaps"] == ["external theorem locator missing"]


def test_proof_gap_resolver_human_allow_clears_gap_for_advisory_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)

    report = resolve_proof_gaps(
        run_dir,
        human_allow=[
            "L1|human_allowed_theorem.lagrange|If H is a subgroup of finite G, then |H| divides |G|."
        ],
        client=StubLLMClient(),
    )
    validate_run(run_dir)

    assert report["candidates"][0]["status"] == "accepted_by_human"
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["lines"][0]["gaps"] == []
    dep = next(
        item
        for item in proof["lines"][0]["dependencies"]
        if item["ref"] == "human_allowed_theorem.lagrange"
    )
    assert dep["authority"] == "human_allowed_theorem_ref"
    assert dep["source_grounded"] is False
    assert dep["machine_verified"] is False
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert record["consensus"] is True


def test_proof_gap_resolver_human_allow_requires_dependency_ref_when_ambiguous(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["gaps"] = [
        "first external theorem locator missing",
        "second external theorem locator missing",
    ]
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_proof_gaps(
            run_dir,
            human_allow=["L1|human_allowed_theorem.first|First theorem."],
            client=StubLLMClient(),
        )

    report = resolve_proof_gaps(
        run_dir,
        human_allow=["L1@L1.gap.2|human_allowed_theorem.second|Second theorem."],
        client=StubLLMClient(),
    )
    validate_run(run_dir)

    assert report["candidates"][0]["dependency_ref"] == "L1.gap.2"
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["lines"][0]["gaps"] == ["first external theorem locator missing"]


def test_proof_gap_resolver_human_allow_does_not_clear_unrelated_gap(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"][0]["gaps"] = ["separate source locator still missing"]
    proof["lines"][0]["dependencies"].append(
        {
            "type": "theorem_ref",
            "ref": "candidate.lagrange",
            "status": "candidate",
            "authority": "agent_claimed_theorem_ref",
            "needed_statement": "If H is a subgroup of finite G, then |H| divides |G|.",
        }
    )
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    resolve_proof_gaps(
        run_dir,
        human_allow=[
            "L1@candidate.lagrange|human_allowed_theorem.lagrange|If H is a subgroup of finite G, then |H| divides |G|."
        ],
        client=StubLLMClient(),
    )
    validate_run(run_dir)

    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["lines"][0]["gaps"] == ["separate source locator still missing"]
    deps = proof["lines"][0]["dependencies"]
    assert not any(dep["ref"] == "candidate.lagrange" for dep in deps)
    assert any(dep["ref"] == "human_allowed_theorem.lagrange" for dep in deps)


def test_proof_gap_resolution_is_bound_to_current_proof_hash(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)
    resolve_proof_gaps(
        run_dir,
        human_allow=[
            "L1|human_allowed_theorem.lagrange|If H is a subgroup of finite G, then |H| divides |G|."
        ],
        client=StubLLMClient(),
    )
    validate_run(run_dir)

    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["notes"].append("mutated after gap resolution")
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)

    with pytest.raises(KernelValidationError, match="stale"):
        validate_run(run_dir)


def test_writer_revokes_stale_proof_gap_resolution_when_regenerating_proof(
    tmp_path: Path,
) -> None:
    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)
    resolve_proof_gaps(
        run_dir,
        human_allow=[
            "L1|human_allowed_theorem.lagrange|If H is a subgroup of finite G, then |H| divides |G|."
        ],
        client=StubLLMClient(),
    )
    validate_run(run_dir)
    assert "proof_gap_resolution" in read_yaml(run_dir / "run_manifest.yaml")[
        "optional_artifacts"
    ]

    write_line_numbered_proof(run_dir, client=StubLLMClient())
    validate_run(run_dir)

    optional = read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]
    assert optional["line_numbered_proof"] == "line_numbered_proof.yaml"
    assert "proof_gap_resolution" not in optional


def test_negative_modified_proof_after_review_invalidates_consensus(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    validate_run(run_dir)
    # tamper with the proof AFTER it was reviewed+sealed
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"].append({"line_id": "LX", "statement": "sneaky added line", "justification": "none", "depends_on": []})
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)
    with pytest.raises(KernelValidationError):  # bound_line_numbered_proof_hash mismatch
        validate_run(run_dir)


def test_negative_consensus_cannot_upgrade_to_machine_verified(tmp_path: Path) -> None:
    # the sealed amber status is never a machine-verified / checked / proved status
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    assert _AMBER not in VERIFIED_PROOF_STATUSES
    assert _AMBER not in {"source_checked_argument", "computation_checked", "lean_checked", "human_confirmed"}
    assert "verified" not in describe_proof_language(_AMBER).lower()
    # the proof's source_refs are candidate references, never earned source_backed
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["status"] == "advisory"
    # score 100 is impossible with this amber status
    g = _grade(run_dir)
    g["score"] = 100
    write_yaml(run_dir / _artifacts(run_dir)["idea_grade"], g)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# --------------------------------------------------------------------------- #
# carried-over guards (still enforced under the new contract)
# --------------------------------------------------------------------------- #
def test_fatal_flaw_does_not_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"adversarial_gap_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert record["consensus"] is False
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_unclear_does_not_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"line_proof_reviewer": "unclear"}))
    validate_run(run_dir)
    assert record["consensus"] is False


def test_reseal_losing_consensus_downgrades(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    seal_agent_consensus(run_dir, client=_ProofReviewStub({"line_proof_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert _grade(run_dir)["proof_language_status"] == "proof_candidate"  # honest downgrade


def test_model_requires_three_proof_reviewers() -> None:
    base = dict(claim_id="c1", run_id="r1", generated_at="t", bound_claim_hash="h",
                consensus=False, disclaimer="d")
    with pytest.raises(ValidationError):  # wrong / old reviewer set
        AgentConsensusReview(**base, entries=[
            {"reviewer_id": "strategy_reviewer", "verdict": "unclear", "looked_for": "", "finding": ""},
        ])


def test_unparseable_reviewer_verdict_fails_safe(tmp_path: Path) -> None:
    # a malformed reviewer verdict normalizes to 'unclear' (blocks) -> not sealed
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"adversarial_gap_reviewer": "TOTALLY_BOGUS"}))
    assert record["consensus"] is False
    assert any(e["verdict"] == "unclear" for e in record["entries"])
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_forged_consensus_without_proof_rejected_by_validator(tmp_path: Path) -> None:
    # The validator gate is the backstop: even a hand-forged unanimous consensus with
    # the grade set to amber is rejected when there is NO line_numbered_proof (bypasses
    # the seal helper entirely). Exercises validators._validate_agent_consensus_review.
    from espresso_proof.kernel.pressure_status import artifact_revision_hash

    run_dir = _admissible_run(tmp_path)  # NO proof written
    a = _artifacts(run_dir)
    man = read_yaml(run_dir / "run_manifest.yaml")
    forged = {
        "schema_version": 1,
        "claim_id": read_yaml(run_dir / a["locked_claim"])["id"],
        "run_id": man["run_id"],
        "generated_at": "t",
        "bound_claim_hash": artifact_revision_hash(run_dir, a["locked_claim"]),
        "bound_line_numbered_proof_hash": "",
        "bound_source_grounding_hash": artifact_revision_hash(run_dir, a["source_grounding"]),
        "bound_review_hash": artifact_revision_hash(run_dir, a["review"]),
        "bound_prover_loop_hash": "",
        "entries": [
            {"reviewer_id": rid, "verdict": "no_fatal_flaw_found", "looked_for": "x",
             "finding": "f", "cited_line_ids": ["L1"], "residual_objections": []}
            for rid in ("line_proof_reviewer", "theorem_lemma_applicability_reviewer", "adversarial_gap_reviewer")
        ],
        "consensus": True,
        "disclaimer": "forged",
    }
    write_yaml(run_dir / "agent_consensus_review.yaml", forged)
    g = read_yaml(run_dir / a["idea_grade"])
    g["proof_language_status"] = _AMBER
    write_yaml(run_dir / a["idea_grade"], g)
    man.setdefault("optional_artifacts", {})["agent_consensus_review"] = "agent_consensus_review.yaml"
    write_yaml(run_dir / "run_manifest.yaml", man)
    with pytest.raises(KernelValidationError):  # no line_numbered_proof -> backstop blocks
        validate_run(run_dir)


def test_refuted_claim_cannot_carry_mock_check(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    g = _grade(run_dir)
    g["status"] = "refuted"
    write_yaml(run_dir / _artifacts(run_dir)["idea_grade"], g)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_mock_check_phrase_is_a_smuggling_field() -> None:
    from espresso_proof.kernel.validators import _prover_status_smuggling_field

    assert _prover_status_smuggling_field("the agent_consensus_mock_checked: true now") is not None
    assert _prover_status_smuggling_field("mock_checked = true") is not None


def test_sealed_memo_is_scan_clean_and_discloses(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    fr = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["final_report"]
    memo = (run_dir / fr).read_text()
    assert "Agent-consensus mock check" in memo and "not a machine check" in memo
    assert _first_forbidden_proof_language(memo.lower()) is None
