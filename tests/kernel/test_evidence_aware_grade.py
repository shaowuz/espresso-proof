"""Evidence-aware Grade + Strategy activation, and concrete-English status text.

These exercise the new behavior: Grade is recomputed from live artifacts so a
precise claim activates meaningful strategy routes, while a vague claim still
stops at claim-locking, source gaps / counterexample pressure stay honest, and
no proof / source / evidence status is upgraded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.kernel.io import read_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.kernel.validators import validate_run

ABSTRACT_LABELS = ("unclear", "fragile", "promising", "likely_false", "refuted", "proved")
PROOF_COMPLETE = ("proof-close", "proof complete", "we proved", "proved the", "this establishes the theorem")


class FakeClaude:
    """A per-stage fake reasoning client, routed by system-prompt keywords."""

    provider = "fake"
    model = "fake-claude"

    def __init__(
        self,
        *,
        precision: str = "precise",
        with_route: bool = True,
        source_backed_attempt: bool = False,
        very_general_to_all: bool = False,
        counterexample_drop: bool = False,
        refute_attempt: bool = False,
    ) -> None:
        self.precision = precision
        self.with_route = with_route
        self.source_backed_attempt = source_backed_attempt
        self.very_general_to_all = very_general_to_all
        self.counterexample_drop = counterexample_drop
        self.refute_attempt = refute_attempt

    def complete_json(self, *, system: str, prompt: str) -> dict:
        s = system.lower()
        if "understanding-claim" in s:
            if self.precision == "precise":
                return {
                    "normalized_statement": "For every prime p, every group of order p^2 is abelian.",
                    "objects": ["finite groups of order p^2", "primes p"],
                    "hypotheses": [
                        {"name": "G has order p^2", "status": "explicit"},
                        {"name": "p is prime", "status": "explicit"},
                    ],
                    "conclusion": "G is abelian.",
                    "ambiguous_terms": [],
                    "missing_or_unclear": [],
                    "claim_precision": "precise",
                    "notes": [],
                }
            return {
                "normalized_statement": "Some Galois group construction should work.",
                "objects": ["unspecified objects"],
                "hypotheses": [{"name": "construction", "status": "unclear"}],
                "conclusion": "Unclear conclusion.",
                "ambiguous_terms": ["construction", "should work"],
                "missing_or_unclear": ["objects", "hypotheses", "conclusion"],
                "claim_precision": "imprecise",
                "notes": [],
            }
        if "strategy agent" in s:
            if not self.with_route:
                return {}
            return {
                "routes": [
                    {
                        "title": "Use the nontrivial center of a finite p-group",
                        "why_this_route": "A nontrivial center with G/Z(G) cyclic gives an abelian group.",
                        "main_risk": "The class-equation lemma must be pinned to a source.",
                        "estimated_effort": "normal_pass",
                        "next_loop_plan": "Pin the center lemma and test p=2,3.",
                    }
                ],
                "question_for_mathematician": "Pursue the center route?",
                "synthesis_note": "center route",
            }
        if "source-matching" in s:
            status = "source_backed" if self.source_backed_attempt else "candidate"
            summary = (
                "Proves the result for very general objects only"
                if self.very_general_to_all
                else "candidate source for the center lemma"
            )
            return {
                "source_refs": [
                    {
                        "type": "book",
                        "title": "Group theory text",
                        "authors": ["Author"],
                        "locator": {"kind": "theorem", "value": "Thm 1.2"},
                        "statement_or_summary": summary,
                        "status": status,
                    }
                ],
                "steps": [
                    {
                        "proof_step_id": "center_lemma",
                        "needed_theorem": "center of a p-group is nontrivial",
                        "uses_source_index": 1,
                        "status": status,
                        "note": summary,
                    }
                ],
            }
        if "counterexamples agent" in s:
            if not (self.counterexample_drop or self.refute_attempt):
                return {}
            return {
                "counterexamples": [
                    {
                        "name": "Drops the prime-order hypothesis (order 6)",
                        "satisfies_all_claim_hypotheses": False,
                        "target": "hypothesis",
                        "missing_hypothesis": "order is p^2 for a prime p",
                        "explanation": "S_3 has order 6 and is nonabelian",
                        "refutes_claim": True if self.refute_attempt else False,
                    }
                ],
                "tests": [],
                "summary": "pressure",
            }
        # examples / attacker / literature / grade: keep minimal/empty.
        return {}


def _scaffold(tmp_path: Path, text: str, name: str = "claim") -> Path:
    claim = tmp_path / f"{name}.md"
    claim.write_text(text, encoding="utf-8")
    return run_mock_critique("signoff_demo", claim, tmp_path)


def _artifacts(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")["artifacts"]


def test_clear_claim_activates_strategy_route(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(run_dir=run_dir, client=FakeClaude(precision="precise"))
    validate_run(run_dir)
    a = _artifacts(run_dir)
    grade = read_yaml(run_dir / a["idea_grade"])
    brief = read_yaml(run_dir / a["strategy_brief"])
    ids = [r["id"] for r in brief["top_strategies"]]
    assert grade["status"] != "unclear"
    assert any(i != "strategy_lock_claim" for i in ids), ids
    assert grade["proof_language_status"] == "idea_sketch"  # no proof claimed


def test_vague_claim_stays_at_claim_locking(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Some Galois group construction should work.")
    run_front_half_agent_pass(run_dir=run_dir, client=FakeClaude(precision="imprecise"))
    validate_run(run_dir)
    a = _artifacts(run_dir)
    grade = read_yaml(run_dir / a["idea_grade"])
    brief = read_yaml(run_dir / a["strategy_brief"])
    assert grade["status"] == "unclear"
    assert [r["id"] for r in brief["top_strategies"]] == ["strategy_lock_claim"]


def test_source_gap_does_not_become_source_backed(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(
        run_dir=run_dir,
        client=FakeClaude(precision="precise", source_backed_attempt=True),
    )
    validate_run(run_dir)
    a = _artifacts(run_dir)
    sg = read_yaml(run_dir / a["source_grounding"])
    grade = read_yaml(run_dir / a["idea_grade"])
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    assert all(r["status"] != "source_backed" for r in sg["source_refs"])
    assert grade["status"] != "promising"  # no checked support -> not promising
    assert grade["source_gaps"]  # source gap recorded


def test_very_general_to_all_stays_source_gap(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(
        run_dir=run_dir,
        client=FakeClaude(
            precision="precise", source_backed_attempt=True, very_general_to_all=True
        ),
    )
    validate_run(run_dir)
    a = _artifacts(run_dir)
    sg = read_yaml(run_dir / a["source_grounding"])
    # a "very general only" source can never be source_backed for an "all" claim.
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    grade = read_yaml(run_dir / a["idea_grade"])
    assert grade["status"] in {"fragile", "likely_false"}  # precise but gapped


def test_counterexample_pressure_does_not_refute(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(
        run_dir=run_dir,
        client=FakeClaude(precision="precise", counterexample_drop=True, refute_attempt=True),
    )
    validate_run(run_dir)
    a = _artifacts(run_dir)
    cex = read_yaml(run_dir / a["counterexamples"])
    grade = read_yaml(run_dir / a["idea_grade"])
    # a dropped-hypothesis counterexample is pressure, never a refutation.
    assert all(c["refutes_claim"] is False for c in cex["tests"])
    assert grade["status"] != "refuted"


def test_human_preference_cannot_upgrade(tmp_path: Path) -> None:
    result = orchestrate_run(
        project="signoff_demo",
        input_path=_write(tmp_path, "Every group of order p^2 is abelian.", "c"),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = result["run_dir"]
    a = _artifacts(run_dir)
    before = read_yaml(run_dir / a["idea_grade"])
    strategy_brief = read_yaml(run_dir / a["strategy_brief"])
    selected = strategy_brief["allowed_human_choices"][0]["strategy_id"]
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass")
    after = read_yaml(run_dir / a["idea_grade"])
    sg = read_yaml(run_dir / a["source_grounding"])
    claim = read_yaml(run_dir / a["locked_claim"])
    assert after["status"] == before["status"]
    assert after["proof_language_status"] == before["proof_language_status"]
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    assert claim["human_confirmed"] is False  # gate never confirms the claim


def test_memory_cannot_upgrade_grade_or_source(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(run_dir=run_dir, client=FakeClaude(precision="precise"))
    validate_run(run_dir)
    a = _artifacts(run_dir)
    grade = read_yaml(run_dir / a["idea_grade"])
    sg = read_yaml(run_dir / a["source_grounding"])
    # grade_memory_memo ran (writes advisory memory); it must not have upgraded.
    assert grade["proof_language_status"] == "idea_sketch"
    assert grade["status"] != "promising"
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    # memory entries are advisory-only (no status fields) -> none can carry source_backed.
    memory_dir = run_dir.parents[1] / "memory"
    if memory_dir.exists():
        for entry in memory_dir.glob("*.yaml"):
            assert "source_backed" not in entry.read_text(encoding="utf-8")


def test_user_visible_text_has_no_abstract_labels(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(run_dir=run_dir, client=FakeClaude(precision="precise"))
    validate_run(run_dir)
    memo = CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    assert memo.exit_code == 0, memo.output
    memo_low = memo.output.lower()
    for label in ABSTRACT_LABELS:
        assert label not in memo_low, f"memo exposed abstract label {label!r}"
    # inspect Grade line is concrete English too.
    inspected = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert inspected.exit_code == 0, inspected.output
    grade_section = inspected.output.split("Grade", 1)[1].split("Strongest Objection", 1)[0].lower()
    for label in ABSTRACT_LABELS:
        assert label not in grade_section, f"inspect Grade exposed {label!r}"


def test_no_proof_complete_language(tmp_path: Path) -> None:
    run_dir = _scaffold(tmp_path, "Every group of order p^2, where p is a prime, is abelian.")
    run_front_half_agent_pass(run_dir=run_dir, client=FakeClaude(precision="precise"))
    validate_run(run_dir)
    memo = CliRunner().invoke(app, ["memo", "--run", str(run_dir)]).output.lower()
    inspected = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)]).output.lower()
    for phrase in PROOF_COMPLETE:
        assert phrase not in memo
        assert phrase not in inspected


def test_full_orchestrator_run_nontrivial_brief_for_clear_claim(tmp_path: Path) -> None:
    result = orchestrate_run(
        project="signoff_demo",
        input_path=_write(tmp_path, "Every group of order p^2 is abelian.", "c"),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = result["run_dir"]
    assert result["state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    validate_run(run_dir)
    brief = read_yaml(run_dir / _artifacts(run_dir)["strategy_brief"])
    ids = [r["id"] for r in brief["top_strategies"]]
    assert any(i != "strategy_lock_claim" for i in ids), ids


def _write(tmp_path: Path, text: str, name: str) -> Path:
    p = tmp_path / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return p
