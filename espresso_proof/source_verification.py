from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .hypothesis_matching import match_hypothesis_semantically
from .io import read_yaml, write_yaml
from .models import SourceGroundingReport, TheoremExtractionReport


def verify_source_grounding_from_theorems(
    *,
    run_dir: Path,
    theorem_extraction_path: Path,
) -> dict:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    extraction = TheoremExtractionReport.model_validate(read_yaml(theorem_extraction_path))
    source_by_id = {source.id: source for source in extraction.sources}
    existing_refs = {ref["id"]: ref for ref in source_grounding.get("source_refs", [])}

    for step in source_grounding.get("steps", []):
        theorem = best_theorem_for_step(step, extraction.theorems)
        if theorem is None:
            continue
        source = source_by_id[theorem.source_ref_id]
        hypothesis_match = check_hypothesis_applicability(
            theorem.statement,
            claim.get("hypotheses", []),
            ledger.get("missing_or_unclear", []),
        )
        exact_locator = theorem.locator.is_exact
        # Anti-plagiarism: source_backed requires attribution (author + title), in
        # addition to an exact locator and a hypothesis match. Fail closed to
        # candidate when attribution is incomplete (the SourceRef model validator
        # is the hard boundary; this is the friendly early downgrade).
        attributed = bool([a for a in source.authors if str(a).strip()]) and bool(
            str(source.title).strip()
        )
        status = (
            "source_backed"
            if exact_locator and hypothesis_match["matches"] is True and attributed
            else "candidate"
        )
        ref_id = f"verified_{theorem.id}"
        existing_refs[ref_id] = {
            "id": ref_id,
            "type": source.type.value if hasattr(source.type, "value") else source.type,
            "title": source.title,
            "authors": source.authors,
            "locator": theorem.locator.model_dump(mode="json"),
            "statement_or_summary": theorem.statement,
            "hypothesis_match": hypothesis_match,
            "confidence": "high" if status == "source_backed" else "medium",
            "status": status,
            # Goal 41: bind the quoted statement to the extracted theorem (the
            # validator re-checks both the self-hash and membership in the
            # theorem_extraction artifact).
            "statement_sha256": hashlib.sha256(theorem.statement.encode("utf-8")).hexdigest(),
        }
        step["needed_theorem"] = theorem.statement[:240]
        step["status"] = status
        step["source_refs"] = [ref_id]
        step["hypothesis_match"] = hypothesis_match
        if status == "source_backed":
            matched_reasons = hypothesis_match.get("matched", [])
            why = ("; ".join(matched_reasons)) if matched_reasons else (
                "required hypotheses were found in the extracted statement"
            )
            step["note"] = (
                f"Exact {theorem.kind} locator {theorem.label} matched; {why}."
            )
        else:
            if not attributed:
                reason = "missing attribution (author/title) for the cited source"
            elif not exact_locator:
                reason = "no exact locator"
            else:
                reason = ", ".join(hypothesis_match.get("mismatches", [])) or "unclear hypothesis match"
            step["note"] = f"Candidate theorem found, but source-backed verification is blocked by {reason}."

    source_grounding["source_refs"] = list(existing_refs.values())
    SourceGroundingReport.model_validate(source_grounding)
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    return source_grounding


def ground_run(*, run_dir: Path, theorem_extraction_path: Path) -> dict:
    """Verify source grounding from extracted theorems, run the theorem
    applicability audit, register it, and re-validate the run (fail closed).

    Single reusable entry point shared by the ``verify-sources`` CLI and the
    orchestrated ``--ground-source`` path so there is one grounding pipeline.
    """

    from .theorem_applicability import audit_theorem_applicability
    from .validators import validate_run

    verify_source_grounding_from_theorems(
        run_dir=run_dir, theorem_extraction_path=theorem_extraction_path
    )
    audit_theorem_applicability(run_dir)  # writes theorem_applicability.yaml
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = (
        "theorem_applicability.yaml"
    )
    write_yaml(manifest_path, manifest)
    validate_run(run_dir)
    return read_yaml(run_dir / "source_grounding.yaml")


def best_theorem_for_step(step: dict, theorems) -> object | None:
    query_terms = significant_terms(step.get("needed_theorem", ""))
    best = None
    best_score = 0
    for theorem in theorems:
        if not theorem.locator.is_exact:
            continue
        haystack = " ".join([theorem.label, theorem.statement, " ".join(theorem.hypothesis_terms)]).lower()
        score = sum(1 for term in query_terms if term in haystack)
        if score > best_score:
            best = theorem
            best_score = score
    return best if best_score >= 2 else None


def check_hypothesis_applicability(
    statement: str,
    claim_hypotheses: list[dict],
    missing_or_unclear: list[str],
) -> dict:
    """Decide whether a cited source statement covers the required claim
    hypotheses.

    A required hypothesis is covered when either:

    1. a conservative, explicit semantic-equivalence rule recognises the same
       statement in different notation (e.g. ``"group of order p^2"`` vs
       ``"|G| = p^2"``); or
    2. every significant whole word of the hypothesis appears in the source
       statement (the original, conservative token coverage, now whole-word
       only so ``finite`` cannot match inside ``infinite``).

    Returns ``matches: True`` with a list of ``matched`` human-readable reasons
    only when ALL required hypotheses are covered; otherwise ``matches:
    "unclear"`` with mismatches. Coverage here gates hypothesis compatibility
    only — it never by itself earns ``source_backed`` (the exact locator and
    attribution gates remain in :func:`verify_source_grounding_from_theorems`
    and the :class:`SourceRef` validator).
    """

    _ = missing_or_unclear
    matched_reasons: list[str] = []
    mismatches: list[str] = []
    unclear = False
    for hypothesis in claim_hypotheses:
        if hypothesis.get("status") not in {"explicit", "inferred"}:
            continue
        name = (hypothesis.get("name") or "").strip()
        if not name:
            continue
        # 1) Conservative semantic equivalence (explicit, test-backed rules).
        reason = match_hypothesis_semantically(name, statement)
        if reason is not None:
            matched_reasons.append(reason)
            continue
        # 2) Fall back to conservative whole-word token coverage.
        terms = list(dict.fromkeys(significant_terms(name) + expanded_hypothesis_terms(name)))
        missing_terms = [term for term in terms if not term_present(term, statement)]
        if missing_terms:
            unclear = True
            mismatches.extend(
                f"required hypothesis term not found in source statement: {term}"
                for term in missing_terms
            )
        elif terms:
            matched_reasons.append(f'"{name}" terms found in the source statement')
        else:
            unclear = True
            mismatches.append(
                f"required hypothesis has no checkable terms or semantic match: {name}"
            )
    if unclear:
        return {"matches": "unclear", "mismatches": mismatches, "matched": matched_reasons}
    return {"matches": True, "mismatches": [], "matched": matched_reasons}


def term_present(term: str, statement: str) -> bool:
    """Whole-word coverage of ``term`` in ``statement`` (case-insensitive).

    Whole-word matching is deliberate: it keeps ``finite`` from matching inside
    ``infinite`` and ``singular`` from matching inside other words. A NEGATED
    occurrence ("non-proper", "non proper", "not proper") never counts as
    coverage (Goal 41): a theorem about non-proper varieties cannot support a
    properness hypothesis. Falls back to the explicit :func:`term_covered`
    notation aliases. A token that fails to match is treated as missing, which
    downgrades to a candidate — the safe direction.
    """

    lowered = statement.lower()
    if _unnegated_word_present(term, lowered):
        return True
    return term_covered(term, lowered)


def _unnegated_word_present(term: str, lowered_statement: str) -> bool:
    """True iff ``term`` occurs as a whole word NOT immediately negated."""

    for match in re.finditer(r"\b" + re.escape(term) + r"\b", lowered_statement):
        before = lowered_statement[: match.start()]
        # whitespace/hyphen-tolerant immediate negators: non-/non/not/no
        if re.search(r"(?:\bnon[-\u2011]?|\bnot|\bno)\s*$", before):
            continue
        return True
    return False


def significant_terms(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text.lower())
        if token not in STOPWORDS
    ]


def term_covered(term: str, statement: str) -> bool:
    # Goal 41: aliases and rule tokens match as whole words, never substrings —
    # "ample" must not fire inside "example"/"sample", and the proper+ample
    # projectivity rule must not be satisfied by negated occurrences.
    for alias in TERM_ALIASES.get(term, []):
        if _unnegated_word_present(alias, statement):
            return True
    if term == "field":
        return _unnegated_word_present("over", statement) or _unnegated_word_present(
            "base field", statement
        )
    if term == "projective":
        return _unnegated_word_present("proper", statement) and _unnegated_word_present(
            "ample", statement
        )
    return False


def expanded_hypothesis_terms(text: str) -> list[str]:
    lowered = text.lower()
    expanded = []
    for term, aliases in TERM_ALIASES.items():
        if term in lowered or any(alias in lowered for alias in aliases):
            expanded.append(term)
    return expanded


TERM_ALIASES = {
    "cubic": ["degree 3", "degree-three"],
    "field": ["over", "base field"],
    "fourfold": ["fourfolds", "four-dimensional"],
    "hodge": ["hodgetheoretic", "hodge-theoretic"],
    "proper": ["complete"],
    "smooth": ["nonsingular", "non-singular"],
}


STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "hypothesis",
    "source",
    "the",
    "this",
    "with",
}
