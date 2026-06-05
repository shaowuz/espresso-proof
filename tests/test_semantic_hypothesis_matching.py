"""Conservative Semantic Hypothesis Matcher V0.

Mathematically equivalent *notation* may match during source grounding (e.g.
"group of order p^2" vs "|G| = p^2"), but semantic matching alone NEVER earns
source_backed: an exact locator and attribution are still required. Opposite or
weaker phrasings must not match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.hypothesis_matching import match_hypothesis_semantically
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import SourceGroundingReport, SourceGroundingStep
from espresso_proof.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.source_verification import (
    check_hypothesis_applicability,
    term_present,
    verify_source_grounding_from_theorems,
)
from espresso_proof.theorem_applicability import _conditions_from_step

FIXTURES = Path("tests/fixtures")

# ---------------------------------------------------------------------------
# 1) Direct matcher: required safe positives.
# ---------------------------------------------------------------------------

REQUIRED_POSITIVES = [
    ("group of order p^2", "Let G be a group with |G| = p^2 where p is prime."),
    ("p is prime", "Let p be a prime p and consider |G| = p^2."),
    ("center of G", "The center Z(G) of a p-group is nontrivial."),
    ("G/Z(G) is cyclic", "If the quotient by the center is cyclic then G is abelian."),
    ("center is nontrivial", "For a finite p-group, Z(G) != 1."),
]


@pytest.mark.parametrize("claim, source", REQUIRED_POSITIVES)
def test_required_positive_matches(claim: str, source: str) -> None:
    reason = match_hypothesis_semantically(claim, source)
    assert reason is not None, (claim, source)
    assert reason.startswith(f'"{claim}" matched "')


def test_prime_matches_in_both_directions() -> None:
    assert match_hypothesis_semantically("p is prime", "prime p divides the order")
    assert match_hypothesis_semantically("prime p", "p is prime")


# ---------------------------------------------------------------------------
# 2) Direct matcher: required negatives must NOT match.
# ---------------------------------------------------------------------------

REQUIRED_NEGATIVES = [
    ("very general", "the statement holds for all objects"),
    ("generic", "for an arbitrary object"),
    ("smooth", "the variety is singular"),
    ("algebraically closed field", "over an arbitrary field k"),
    ("finite group", "let G be an infinite group"),
    ("characteristic zero", "in arbitrary characteristic"),
]


@pytest.mark.parametrize("claim, source", REQUIRED_NEGATIVES)
def test_required_negative_matches(claim: str, source: str) -> None:
    assert match_hypothesis_semantically(claim, source) is None, (claim, source)


def test_finite_is_not_a_substring_match_for_infinite() -> None:
    # Regression: whole-word coverage, "finite" must not match inside "infinite".
    assert term_present("group", "let G be an infinite group") is True
    assert term_present("finite", "let G be an infinite group") is False


# ---------------------------------------------------------------------------
# 3) Integration: check_hypothesis_applicability.
# ---------------------------------------------------------------------------


def _hyp(name: str, status: str = "explicit") -> dict:
    return {"name": name, "status": status, "source": "user"}


def test_applicability_matches_p2_group_via_notation() -> None:
    statement = "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then G is abelian."
    result = check_hypothesis_applicability(
        statement,
        [_hyp("group of order p^2"), _hyp("p is prime")],
        [],
    )
    assert result["matches"] is True
    assert result["mismatches"] == []
    assert any('group of order p^2" matched "|G| = p^2' in r for r in result["matched"])
    assert any('p is prime" matched "' in r for r in result["matched"])


def test_applicability_extra_missing_hypothesis_stays_unclear() -> None:
    statement = "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then G is abelian."
    result = check_hypothesis_applicability(
        statement,
        [_hyp("group of order p^2"), _hyp("the group is solvable and metacyclic")],
        [],
    )
    assert result["matches"] == "unclear"
    assert result["mismatches"]


def test_applicability_finite_does_not_match_infinite() -> None:
    result = check_hypothesis_applicability(
        "Let G be an infinite group with trivial center.",
        [_hyp("finite group")],
        [],
    )
    assert result["matches"] == "unclear"


# ---------------------------------------------------------------------------
# 4) Hard safety boundary: semantic match alone cannot earn source_backed.
# ---------------------------------------------------------------------------


def _ref(*, locator_kind: str, authors: list[str], title: str = "A Source") -> dict:
    return {
        "id": "src_x",
        "type": "paper",
        "title": title,
        "authors": authors,
        "locator": {"kind": locator_kind, "value": "Theorem 1.1" if locator_kind != "unknown" else ""},
        "statement_or_summary": "Let G be a group with |G| = p^2 where p is prime.",
        "hypothesis_match": {
            "matches": True,
            "mismatches": [],
            "matched": ['"group of order p^2" matched "|G| = p^2"'],
        },
        "confidence": "high",
        "status": "source_backed",
    }


def _report(ref: dict) -> dict:
    return {"claim_id": "claim_001", "source_refs": [ref], "steps": []}


def test_semantic_match_without_exact_locator_cannot_be_source_backed() -> None:
    with pytest.raises(ValueError, match="exact locator"):
        SourceGroundingReport.model_validate(
            _report(_ref(locator_kind="unknown", authors=["A. Author"]))
        )


def test_semantic_match_without_attribution_cannot_be_source_backed() -> None:
    with pytest.raises(ValueError, match="attributed author"):
        SourceGroundingReport.model_validate(
            _report(_ref(locator_kind="theorem", authors=[]))
        )


def test_semantic_match_with_locator_and_attribution_is_accepted() -> None:
    # The positive control: matched + exact locator + attribution validates.
    SourceGroundingReport.model_validate(
        _report(_ref(locator_kind="theorem", authors=["A. Author"]))
    )


# ---------------------------------------------------------------------------
# 5) Surfacing: matched reasons become the "Why it applies" conditions.
# ---------------------------------------------------------------------------


def test_matched_reasons_surface_as_satisfied_conditions() -> None:
    step = SourceGroundingStep.model_validate(
        {
            "proof_step_id": "s1",
            "needed_theorem": "groups of order p^2 are abelian",
            "status": "source_backed",
            "source_refs": ["verified_thm_001"],
            "hypothesis_match": {
                "matches": True,
                "mismatches": [],
                "matched": [
                    '"group of order p^2" matched "|G| = p^2"',
                    '"p is prime" matched "prime p"',
                ],
            },
            "note": "Exact theorem locator Theorem 1.1 matched; ...",
        }
    )
    conditions = _conditions_from_step(step)
    assert [c["status"] for c in conditions] == ["satisfied", "satisfied"]
    texts = [c["text"] for c in conditions]
    assert texts[0] == '"group of order p^2" matched "|G| = p^2"'
    assert texts[1] == '"p is prime" matched "prime p"'


# ---------------------------------------------------------------------------
# 6) End-to-end earn path through the real grounding writer.
# ---------------------------------------------------------------------------


def test_end_to_end_semantic_notation_earns_source_backed(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )
    # Re-target the claim hypotheses to a p^2-group statement phrased one way and
    # the cited theorem phrased in equivalent notation, so only the semantic
    # matcher (not raw tokens) can cover the hypotheses.
    claim = read_yaml(run_dir / "locked_claim.yaml")
    claim["hypotheses"] = [_hyp("group of order p^2"), _hyp("p is prime")]
    write_yaml(run_dir / "locked_claim.yaml", claim)

    grounding = read_yaml(run_dir / "source_grounding.yaml")
    grounding["steps"][0]["needed_theorem"] = "group order abelian center"
    grounding["steps"][0]["status"] = "candidate"
    grounding["steps"][0]["source_refs"] = []
    grounding["steps"][0]["hypothesis_match"] = {"matches": "unclear", "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", grounding)

    text = (
        "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then "
        "the center Z(G) is nontrivial and G is abelian."
    )
    src = run_dir / "src.txt"
    src.write_text(text, encoding="utf-8")
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="src_group",
        title="A Note on p-groups",
        authors=["A. Mathematician"],
        source_type="paper",
    )
    ext_path = run_dir / "theorem_extraction.yaml"
    write_yaml(ext_path, report)

    verify_source_grounding_from_theorems(run_dir=run_dir, theorem_extraction_path=ext_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")

    step = sg["steps"][0]
    assert step["status"] == "source_backed", step
    assert step["hypothesis_match"]["matches"] is True
    assert any("matched" in r for r in step["hypothesis_match"]["matched"])
    assert "|G| = p^2" in step["note"]
    backed = [r for r in sg["source_refs"] if r["status"] == "source_backed"]
    assert backed and backed[0]["locator"]["kind"] != "unknown"
    assert [a for a in backed[0]["authors"] if a.strip()]
