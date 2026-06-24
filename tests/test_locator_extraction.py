"""Goal 04: source locator + theorem extraction reliability.

Covers >=5 locator shapes, explicit exact-locator predicate tests, multi-block
splitting (the Goal 01 concatenation bug), and the required negatives: an
abstract-only / unlabeled / malformed header cannot earn source support, the
extracted statement is the source text (not a fabricated summary), and a long
source statement is capped (not copied wholesale).
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.models import Locator
from espresso_proof.literature.pdf_ingestion import (
    extract_source_text,
    extract_theorems,
    ingest_source_for_theorems,
)
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.kernel.validators import validate_run

FIXTURE = Path(__file__).parent / "fixtures" / "hodge_all_cubic_fourfolds" / "input.md"


def _one(text: str) -> dict:
    ts = extract_theorems(text, source_ref_id="s")
    assert ts, f"expected an extracted block from: {text!r}"
    return ts[0]


def _exact(theorem: dict) -> bool:
    return Locator.model_validate(theorem["locator"]).is_exact


# --- >=5 locator shapes, each must yield an EXACT locator -------------------

def test_locator_shape_numeric_dotted() -> None:
    t = _one("Theorem 1.2. Let X be smooth; then X is rational.")
    assert t["locator"] == {"kind": "theorem", "value": "Theorem 1.2"}
    assert _exact(t)


def test_locator_shape_letter_dot_number() -> None:
    t = _one("Lemma A.3. Every finite group has a composition series.")
    assert t["locator"] == {"kind": "lemma", "value": "Lemma A.3"}
    assert _exact(t)


def test_locator_shape_single_number() -> None:
    t = _one("Proposition 5. The center of a p-group is nontrivial.")
    assert t["locator"] == {"kind": "proposition", "value": "Proposition 5"}
    assert _exact(t)


def test_locator_shape_parenthetical_name_only() -> None:
    t = _one("Definition (Smooth Variety). A variety is smooth if its local rings are regular.")
    assert t["locator"]["kind"] == "definition"
    assert t["locator"]["value"] == "Definition (Smooth Variety)"
    assert _exact(t)


def test_locator_shape_number_and_name() -> None:
    t = _one("Corollary 2.1 (Main result). The map is an isomorphism.")
    assert t["locator"]["value"] == "Corollary 2.1"
    assert _exact(t)


def test_locator_shape_tex_wrapped_number() -> None:
    t = _one(r"Theorem \(6.10\). Lagrange The order of H must divide the order of G.")
    assert t["locator"] == {"kind": "theorem", "value": "Theorem 6.10"}
    assert "order of H" in t["statement"]
    assert _exact(t)


def test_tex_boundary_before_header_splits_blocks() -> None:
    text = (
        r"Proposition \(6.9\) Let H be a subgroup of G. "
        r"Theorem \(6.10\). Lagrange Let G be finite and H a subgroup of G. "
        r"Then |G|/|H| is the number of left cosets."
    )
    ts = extract_theorems(text, source_ref_id="s")
    assert [t["locator"]["value"] for t in ts] == ["Proposition 6.9", "Theorem 6.10"]


def test_malformed_tex_boundary_before_header_splits_blocks() -> None:
    text = (
        r"Then the order of \(g\) must divide the number of elements in \(G\text{.}\ "
        r"Corollary \(6.12\) Let \(|G| = p\) with \(p\) a prime number. "
        r"Then \(G\) is cyclic."
    )
    ts = extract_theorems(text, source_ref_id="s")
    assert ts[0]["locator"] == {"kind": "corollary", "value": "Corollary 6.12"}
    assert "prime number" in ts[0]["statement"]
    assert "cyclic" in ts[0]["statement"]


def test_multi_block_splitting_keeps_three_exact_blocks() -> None:
    note = (
        "Theorem 1 (Nontrivial center). Let p be a prime and let G be a finite "
        "p-group with G nontrivial. Then the center Z(G) is nontrivial. "
        "Proposition 2 (Central quotient cyclic implies abelian). Let G be a "
        "group. If the quotient G/Z(G) is cyclic, then G is abelian. "
        "Lemma 3 (Prime order is cyclic). Let p be a prime. Every group of order "
        "p is cyclic."
    )
    ts = extract_theorems(note, source_ref_id="s")
    assert [t["kind"] for t in ts] == ["theorem", "proposition", "lemma"]
    assert [t["locator"]["value"] for t in ts] == ["Theorem 1", "Proposition 2", "Lemma 3"]
    assert all(_exact(t) for t in ts)
    # statements did NOT concatenate across blocks.
    assert "Proposition" not in ts[0]["statement"]
    assert "Lemma" not in ts[1]["statement"]


# --- explicit exact-locator predicate -------------------------------------

def test_unlabeled_header_is_not_exact() -> None:
    t = _one("Theorem. A vague statement with no label at all here.")
    assert t["locator"]["value"] == ""
    assert not _exact(t)


def test_malformed_label_does_not_masquerade_as_exact() -> None:
    # "in" is not a locator-like label token.
    t = _one("Theorem in chapter five about finite groups and their centers.")
    assert t["locator"]["value"] == ""
    assert not _exact(t)


def test_mid_sentence_reference_does_not_split_statement() -> None:
    ts = extract_theorems(
        "Theorem 1.1. We use the result, and by Theorem 5 we conclude the bound holds.",
        source_ref_id="s",
    )
    assert len(ts) == 1


def test_html_source_text_extracts_lagrange_theorem_locator() -> None:
    raw = b"""
    <!doctype html><html><head><title>Ignore</title><script>var x='Theorem 0';</script></head>
    <body>
      <h3>Theorem 6.10 (Lagrange's Theorem)</h3>
      <p>If G is a finite group and H is a subgroup of G, then |H| divides |G|.</p>
      <p>Proof. The left cosets of H partition G.</p>
    </body></html>
    """

    text, notes = extract_source_text(
        raw,
        "https://math.libretexts.org/Bookshelves/Abstract_Algebra/6.02_Lagrange",
    )
    ts = extract_theorems(text, source_ref_id="judson")

    assert any("Extracted HTML text" in note for note in notes)
    assert len(ts) == 1
    assert ts[0]["locator"] == {"kind": "theorem", "value": "Theorem 6.10"}
    assert "finite group" in ts[0]["statement"]
    assert "subgroup of G" in ts[0]["statement"]
    assert "|H| divides |G|" in ts[0]["statement"]


# --- statement fidelity + length cap --------------------------------------

def test_extracted_statement_is_source_text_not_a_summary() -> None:
    text = "Theorem 7.4. The specific source sentence about modular forms holds here."
    t = _one(text)
    assert "specific source sentence about modular forms" in t["statement"]


def test_long_source_statement_is_capped() -> None:
    long_tail = " padding clause that repeats" * 400  # >> 2500 chars
    t = _one(f"Theorem 1.1. A real statement.{long_tail}")
    assert len(t["statement"]) <= 2500


# --- end-to-end: extraction feeds the UNCHANGED source-backed gate ---------

def _hodge_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("hodge_atoms", FIXTURE, tmp_path)
    claim = read_yaml(run_dir / "locked_claim.yaml")
    claim["hypotheses"] = [
        {"name": "group of order p^2", "status": "explicit", "source": "user"},
        {"name": "p is prime", "status": "explicit", "source": "user"},
    ]
    write_yaml(run_dir / "locked_claim.yaml", claim)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["steps"][0]["needed_theorem"] = "group order abelian center"
    sg["steps"][0]["status"] = "candidate"
    sg["steps"][0]["source_refs"] = []
    sg["steps"][0]["hypothesis_match"] = {"matches": "unclear", "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", sg)
    return run_dir


def _ingest_and_ground(run_dir: Path, text: str, *, authors: list[str], title: str) -> None:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    ext = ingest_source_for_theorems(
        claim_id=claim["id"], source=_write(run_dir, text), source_id="src",
        title=title, authors=authors, source_type="paper",
    )
    ext_path = run_dir / "theorem_extraction.yaml"
    write_yaml(ext_path, ext)
    ground_run(run_dir=run_dir, theorem_extraction_path=ext_path)


def _write(run_dir: Path, text: str) -> str:
    p = run_dir / "src.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _step_backed(run_dir: Path) -> bool:
    sg = read_yaml(run_dir / "source_grounding.yaml")
    return sg["steps"][0]["status"] == "source_backed"


def test_labeled_matching_source_earns_support(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    _ingest_and_ground(
        run_dir,
        "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then the "
        "center Z(G) is nontrivial and G is abelian.",
        authors=["A. Mathematician"], title="A Note on p-groups",
    )
    validate_run(run_dir)
    assert _step_backed(run_dir)


def test_abstract_only_source_stays_candidate(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    _ingest_and_ground(
        run_dir,
        "Abstract. We survey groups of order p^2 where p is prime and discuss "
        "abelianness, but state no labeled theorem here.",
        authors=["A. Mathematician"], title="Survey",
    )
    validate_run(run_dir)
    assert not _step_backed(run_dir)


def test_unlabeled_paragraph_source_stays_candidate(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    _ingest_and_ground(
        run_dir,
        "Theorem. Let G be a group with |G| = p^2 where p is prime; then G is abelian.",
        authors=["A. Mathematician"], title="Unlabeled",
    )
    validate_run(run_dir)
    # No exact locator (bare 'Theorem') -> cannot be source_backed.
    assert not _step_backed(run_dir)
