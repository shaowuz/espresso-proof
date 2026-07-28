"""Ambient Object Hypothesis Normalization V0.

A fully-expanded group-axiom set (closure, associativity, identity, inverses)
collapses to the single ambient hypothesis "G is a group" so a cited theorem
that writes "Let G be a group" can match conservatively. A weaker/different
structure (monoid, semigroup, magma, ring, module) is NEVER treated as a group,
and no source-backed hard gate is weakened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.claim.hypothesis_matching import match_hypothesis_semantically
from espresso_proof.claim.hypothesis_normalization import (
    AMBIENT_GROUP_HYPOTHESIS,
    normalize_ambient_object_hypotheses,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import SourceGroundingReport
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.verification.source_verification import (
    check_hypothesis_applicability,
    ground_run,
)
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _hyp(name: str, status: str = "explicit") -> dict:
    return {"name": name, "status": status, "source": "agent"}


def _names(hyps: list[dict]) -> list[str]:
    return [h["name"] for h in hyps]


# ---------------------------------------------------------------------------
# 1) Normalization: positive collapses.
# ---------------------------------------------------------------------------


def test_single_hypothesis_with_all_four_axioms_collapses() -> None:
    out = normalize_ambient_object_hypotheses(
        [
            _hyp("p is prime"),
            _hyp("G is a group (closure, associativity, identity, inverses)"),
        ]
    )
    assert _names(out) == ["p is prime", AMBIENT_GROUP_HYPOTHESIS]


def test_separate_axiom_hypotheses_collapse_to_group() -> None:
    out = normalize_ambient_object_hypotheses(
        [
            _hyp("closure"),
            _hyp("associativity"),
            _hyp("identity", status="inferred"),
            _hyp("inverses"),
        ]
    )
    assert _names(out) == [AMBIENT_GROUP_HYPOTHESIS]
    assert out[0]["status"] == "explicit"  # any explicit -> explicit


def test_normalization_is_idempotent_and_does_not_mutate_input() -> None:
    src = [_hyp("G is a group (closure, associativity, identity, inverses)")]
    once = normalize_ambient_object_hypotheses(src)
    twice = normalize_ambient_object_hypotheses(once)
    assert _names(once) == _names(twice) == [AMBIENT_GROUP_HYPOTHESIS]
    assert src[0]["name"].startswith("G is a group (")  # input untouched


# ---------------------------------------------------------------------------
# 2) Normalization: negatives (must NOT collapse).
# ---------------------------------------------------------------------------


def test_partial_axioms_do_not_collapse() -> None:
    out = normalize_ambient_object_hypotheses(
        [_hyp("closure"), _hyp("associativity"), _hyp("identity")]
    )
    assert _names(out) == ["closure", "associativity", "identity"]


@pytest.mark.parametrize("structure", ["monoid", "semigroup", "magma", "ring", "module"])
def test_other_structures_never_collapse_to_group(structure: str) -> None:
    # Even if all four group axioms are textually present, a different structure
    # is never renamed to "G is a group".
    name = f"X is a {structure} (closure, associativity, identity, inverses)"
    out = normalize_ambient_object_hypotheses([_hyp(name)])
    assert _names(out) == [name]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("G is a finite group (closure, associativity, identity, inverses)", "G is a finite group"),
        ("G is an abelian group (closure, associativity, identity, inverses)", "G is an abelian group"),
        ("G is a p-group (closure, associativity, identity, inverses)", "G is a p-group"),
        ("G is a finite simple group (closure, associativity, identity, inverses)", "G is a finite simple group"),
    ],
)
def test_qualifier_outside_axiom_parenthetical_is_preserved(name: str, expected: str) -> None:
    # The axiom enumeration is stripped but the qualifier (finite/abelian/...) is
    # kept — it must NOT collapse to a bare "G is a group".
    out = normalize_ambient_object_hypotheses([_hyp(name)])
    assert _names(out) == [expected]
    assert _names(out) != [AMBIENT_GROUP_HYPOTHESIS]


def test_axioms_with_nonparenthetical_qualifier_are_not_dropped() -> None:
    # All four axioms present but a qualifier sits OUTSIDE any parenthetical:
    # do not silently drop "finite" by renaming to bare "G is a group".
    name = "G is a finite group with closure, associativity, identity, inverses"
    out = normalize_ambient_object_hypotheses([_hyp(name)])
    assert _names(out) == [name]


def test_axiom_hypothesis_with_other_math_content_is_not_swallowed() -> None:
    # A hypothesis that also carries non-axiom content must not be consolidated.
    out = normalize_ambient_object_hypotheses(
        [
            _hyp("closure"),
            _hyp("associativity"),
            _hyp("identity"),
            _hyp("inverses exist and the order |G| = p^2"),
        ]
    )
    assert AMBIENT_GROUP_HYPOTHESIS not in _names(out)


# ---------------------------------------------------------------------------
# 3) Matcher: object-type positives and structure negatives.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "Let G be a group with |G| = p^2 where p is prime",
        "the group G acts on the set X",
        "every group of order p^2 is abelian",
    ],
)
def test_g_is_a_group_matches_object_type_sources(source: str) -> None:
    reason = match_hypothesis_semantically("G is a group", source)
    assert reason is not None
    assert reason.startswith('"G is a group" matched "')


@pytest.mark.parametrize(
    "source",
    [
        "Let G be a monoid",
        "Let G be a semigroup",
        "Let G be a magma",
        "Let R be a ring",
        "Let M be a module",
    ],
)
def test_g_is_a_group_does_not_match_weaker_structures(source: str) -> None:
    assert match_hypothesis_semantically("G is a group", source) is None


# ---------------------------------------------------------------------------
# 4) Integration: front-half understanding-claim normalizes before writing.
# ---------------------------------------------------------------------------


class _FakeClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def test_understanding_claim_normalizes_group_axioms(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(
        "Every group of order p^2, where p is prime, is abelian.", encoding="utf-8"
    )
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    client = _FakeClient(
        {
            "normalized_statement": "For every prime p and every group G with |G| = p^2, G is abelian.",
            "objects": ["a prime p", "a group G of order p^2"],
            "hypotheses": [
                {"name": "p is prime", "status": "explicit"},
                {"name": "|G| = p^2", "status": "explicit"},
                {
                    "name": "G is a group (closure, associativity, identity, inverses)",
                    "status": "explicit",
                },
            ],
            "conclusion": "G is abelian.",
            "claim_precision": "precise",
        }
    )
    run_front_half_agent_pass(
        run_dir=run_dir, client=client, stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    ledger = read_yaml(run_dir / artifacts["hypothesis_ledger"])
    assert AMBIENT_GROUP_HYPOTHESIS in _names(claim["hypotheses"])
    assert AMBIENT_GROUP_HYPOTHESIS in _names(ledger["hypotheses"])
    # the decomposed-axiom phrasing is gone from both artifacts.
    for hyps in (claim["hypotheses"], ledger["hypotheses"]):
        assert not any("closure" in h["name"] for h in hyps)


# ---------------------------------------------------------------------------
# 5) check_hypothesis_applicability with the normalized ambient hypothesis.
# ---------------------------------------------------------------------------


def test_applicability_matches_with_normalized_group_hypothesis() -> None:
    statement = (
        "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then G is abelian."
    )
    result = check_hypothesis_applicability(
        statement,
        [_hyp("p is prime"), _hyp("|G| = p^2"), _hyp(AMBIENT_GROUP_HYPOTHESIS)],
        [],
    )
    assert result["matches"] is True
    assert any('"G is a group" matched "Let G be a group"' in r for r in result["matched"])


def test_separate_axioms_block_without_normalization_but_pass_after() -> None:
    # Bare separate axioms carry no "group" word, so neither the token coverage
    # nor the object-type matcher can cover them -> the step stays a candidate.
    statement = (
        "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then G is abelian."
    )
    expanded = [
        _hyp("p is prime"),
        _hyp("closure"),
        _hyp("associativity"),
        _hyp("identity"),
        _hyp("inverses"),
    ]
    assert check_hypothesis_applicability(statement, expanded, [])["matches"] == "unclear"
    # After normalization the four axioms collapse to "G is a group", which the
    # object-type matcher covers against "Let G be a group".
    normalized = normalize_ambient_object_hypotheses(expanded)
    assert check_hypothesis_applicability(statement, normalized, [])["matches"] is True


# ---------------------------------------------------------------------------
# 6) End-to-end earn + memo "Why it applies", and unchanged hard gates.
# ---------------------------------------------------------------------------


def _p2_run_with_normalized_claim(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "linear_algebra", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path
    )
    claim = read_yaml(run_dir / "locked_claim.yaml")
    # The front-half would have normalized the decomposed axioms to "G is a group".
    claim["hypotheses"] = normalize_ambient_object_hypotheses(
        [
            _hyp("p is prime"),
            _hyp("group of order p^2"),
            _hyp("G is a group (closure, associativity, identity, inverses)"),
        ]
    )
    write_yaml(run_dir / "locked_claim.yaml", claim)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["source_refs"] = []
    sg["steps"] = [
        {
            "proof_step_id": "ps_center",
            "needed_theorem": "a group of order p^2 with p prime is abelian via its center",
            "status": "candidate",
            "source_refs": [],
            "hypothesis_match": {"matches": "unclear", "mismatches": []},
            "note": "Proposed proof-route step; needs a cited source.",
        }
    ]
    write_yaml(run_dir / "source_grounding.yaml", sg)
    return run_dir


_P2_SOURCE = (
    "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then the "
    "center Z(G) is nontrivial and G is abelian."
)


def _ingest(run_dir: Path, *, authors: list[str], title: str = "A Note on p-groups") -> Path:
    src = run_dir / "src.txt"
    src.write_text(_P2_SOURCE, encoding="utf-8")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="src_p2",
        title=title,
        authors=authors,
        source_type="paper",
    )
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return run_dir / "theorem_extraction.yaml"


def test_p2_grounding_earns_reference_support_after_normalization(tmp_path: Path) -> None:
    run_dir = _p2_run_with_normalized_claim(tmp_path)
    ext = _ingest(run_dir, authors=["A. Mathematician"])
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)

    sg = read_yaml(run_dir / "source_grounding.yaml")
    step = sg["steps"][0]
    assert step["status"] == "source_backed", step
    matched = step["hypothesis_match"]["matched"]
    assert any('"group of order p^2" matched "|G| = p^2"' in r for r in matched)
    assert any('"G is a group" matched "Let G be a group"' in r for r in matched)

    # the matched reason surfaces in the memo "Why it applies".
    memo = build_concise_memo(run_dir)
    assert "Why it applies:" in memo
    assert '"G is a group" matched "Let G be a group"' in memo
    lower = memo.lower()
    for phrase in ("proof-close", "proof complete", "we proved", "proved the claim"):
        assert phrase not in lower


def test_normalized_match_without_exact_locator_stays_candidate(tmp_path: Path) -> None:
    run_dir = _p2_run_with_normalized_claim(tmp_path)
    # An informal source with NO theorem label -> no exact locator.
    src = run_dir / "src.txt"
    src.write_text(
        "Informally, for a group G with |G| = p^2 and p prime, G is abelian.",
        encoding="utf-8",
    )
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"], source=str(src), source_id="src_p2",
        title="Informal note", authors=["A. Mathematician"], source_type="paper",
    )
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])


def test_normalized_match_without_attribution_stays_candidate(tmp_path: Path) -> None:
    run_dir = _p2_run_with_normalized_claim(tmp_path)
    ext = _ingest(run_dir, authors=[])  # no attribution
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    SourceGroundingReport.model_validate(sg)  # still kernel-valid
