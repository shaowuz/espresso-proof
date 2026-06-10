"""Goal 20: renderer proof-language sanitizer + display vocabulary.

User-visible output (memo / inspect / dashboard / memory summary) must never
render global proof-complete language ("proof-close", "Espresso proved the theorem",
"proof complete", ...), even if an echoed free-text artifact field carries it.
This is display-layer defense in depth; it never upgrades any status. Scoped /
negated phrasing ("the source proves Lemma 2.1", "not a checked proof") is
preserved.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.memo import build_concise_memo
from espresso_proof.memory import summarize_memory, write_memory_entry
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.proof_language import contains_proof_language, scrub_proof_language
from espresso_proof.run_dashboard import render_run_dashboard
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")
_OVERCLAIMS = ["proof-close", "Espresso proved the theorem", "proof complete", "we proved"]


# --- unit ------------------------------------------------------------------
def test_scrub_redacts_global_overclaims_and_preserves_scoped_language() -> None:
    text = (
        "The source proves Lemma 2.1; this is not a checked proof. "
        "We proved the theorem. proof-close. The proof is complete."
    )
    out = scrub_proof_language(text)
    assert contains_proof_language(out) is None
    # the global overclaim is replaced as a whole unit (no dangling "the theorem")
    assert "the theorem" not in out
    # legitimate scoped / negated phrasing survives
    assert "proves Lemma 2.1" in out
    assert "not a checked proof" in out


def test_scrub_preserves_honest_negated_scoped_and_prefix_text() -> None:
    # These honest statements must NOT be redacted (they are not Espresso
    # overclaiming a whole-theorem proof). Regression for the over-redaction /
    # negation-inversion / word-prefix bugs.
    preserved = [
        "We have not proved the theorem.",
        "The cited source proves the theorem in the abelian case.",
        "No single step establishes the theorem.",
        "It is false that the theorem is established.",
        "proof completeness is a separate notion",
        "We proved Lemma 2.1 and a weaker result.",
        "a proof sketch; not yet verified by a checker",
        "the reference proves Proposition 3",
        "this is not a verified theorem yet",
        "oedipus is an unrelated identifier",
    ]
    for text in preserved:
        assert contains_proof_language(text) is None, text
        assert scrub_proof_language(text) == text, text


def test_contains_proof_language_detects_each_overclaim() -> None:
    for phrase in (
        "proof-close",
        "proof complete",
        "the proof is complete",
        "no gaps remain",
        "Espresso proved the theorem",
        "we proved the claim",
        "espresso has proven the theorem",
    ):
        assert contains_proof_language(f"prefix {phrase} suffix") is not None, phrase


# --- helpers ---------------------------------------------------------------
def _run_with_forged_overclaims(tmp_path: Path) -> tuple[Path, dict]:
    run = run_mock_critique("g20", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    artifacts = read_yaml(run / "run_manifest.yaml")["artifacts"]
    review = read_yaml(run / artifacts["review"])
    review["strongest_objection"] = "Actually we proved the theorem. proof-close."
    write_yaml(run / artifacts["review"], review)
    grade = read_yaml(run / artifacts["idea_grade"])
    grade["next_cheapest_test"] = "None needed: Espresso proved the claim."
    grade["source_gaps"] = ["proof-close; no gaps remain."]
    write_yaml(run / artifacts["idea_grade"], grade)
    validate_run(run)  # forged free-text fields are not cross-validated; run stays valid
    return run, artifacts


# --- renderers -------------------------------------------------------------
def test_memo_does_not_render_global_proof_language(tmp_path: Path) -> None:
    run, artifacts = _run_with_forged_overclaims(tmp_path)
    grade_before = read_yaml(run / artifacts["idea_grade"])
    memo = build_concise_memo(run)
    assert contains_proof_language(memo) is None
    assert "[withheld proof-like phrase]" in memo  # something was redacted
    # render is read-only / does not upgrade status
    assert read_yaml(run / artifacts["idea_grade"]) == grade_before


def test_dashboard_does_not_render_global_proof_language(tmp_path: Path) -> None:
    run, _ = _run_with_forged_overclaims(tmp_path)
    html = render_run_dashboard(run)
    assert contains_proof_language(html) is None


def test_inspect_does_not_render_global_proof_language(tmp_path: Path) -> None:
    run, _ = _run_with_forged_overclaims(tmp_path)
    result = CliRunner().invoke(app, ["inspect", "--run", str(run)])
    assert result.exit_code == 0, result.stdout
    assert contains_proof_language(result.stdout) is None
    assert "Run is valid" in result.stdout


def test_memory_summary_does_not_render_global_proof_language(tmp_path: Path) -> None:
    run = run_mock_critique("g20mem", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    project_dir = run.parent.parent
    write_memory_entry(
        project_dir,
        {
            "id": "mem_overclaim",
            "timestamp": "2026-06-10T00:00:00+00:00",
            "project": project_dir.name,
            "type": "reviewer_objection",
            "claim_id": "claim_001",
            "summary": "A prior run noted: we proved the theorem, proof-close.",
            "provenance": ["projects/x/runs/y/review.yaml"],
            "reusable_lesson": "Espresso proved the claim last time.",
        },
    )
    summary = summarize_memory(project_dir, claim_text="group order p^2 abelian")
    assert summary  # non-empty
    assert contains_proof_language(summary) is None


# --- behavioral safety -----------------------------------------------------
def test_ai_smoke_check_route_stays_candidate_not_proved(tmp_path: Path) -> None:
    # A run whose routes are candidate-only (heuristic critique) must not show any
    # proved/checked/source-backed status, and rendering must not upgrade it.
    run = run_mock_critique("g20smoke", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    artifacts = read_yaml(run / "run_manifest.yaml")["artifacts"]
    sg = read_yaml(run / artifacts["source_grounding"])
    assert all(s.get("status") != "source_backed" for s in sg.get("steps", []))
    memo = build_concise_memo(run)
    assert contains_proof_language(memo) is None
    # no source-backed step was invented by rendering
    assert all(
        s.get("status") != "source_backed"
        for s in read_yaml(run / artifacts["source_grounding"]).get("steps", [])
    )


def test_memo_verdict_uses_human_phrase_not_raw_enum(tmp_path: Path) -> None:
    run = run_mock_critique("g20enum", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    memo = build_concise_memo(run)
    verdict_line = next(l for l in memo.splitlines() if l.startswith("Verdict:"))
    # the verdict is a human sentence, not a bare internal enum token
    for raw in ("likely_false", "idea_sketch", "source_backed", "proof_candidate"):
        assert raw not in verdict_line
