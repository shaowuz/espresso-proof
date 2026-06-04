from __future__ import annotations

import re
from pathlib import Path

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
        }
        step["needed_theorem"] = theorem.statement[:240]
        step["status"] = status
        step["source_refs"] = [ref_id]
        step["hypothesis_match"] = hypothesis_match
        if status == "source_backed":
            step["note"] = (
                f"Exact {theorem.kind} locator {theorem.label} matched and required "
                "hypotheses were found in the extracted statement."
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
    lowered = statement.lower()
    required = []
    for hypothesis in claim_hypotheses:
        if hypothesis.get("status") in {"explicit", "inferred"}:
            required.extend(significant_terms(hypothesis.get("name", "")))
            required.extend(expanded_hypothesis_terms(hypothesis.get("name", "")))
    _ = missing_or_unclear
    missing = [
        term
        for term in sorted(dict.fromkeys(required))
        if term not in lowered and not term_covered(term, lowered)
    ]
    if missing:
        return {
            "matches": "unclear",
            "mismatches": [f"required hypothesis term not found in source statement: {term}" for term in missing],
        }
    return {"matches": True, "mismatches": []}


def significant_terms(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text.lower())
        if token not in STOPWORDS
    ]


def term_covered(term: str, statement: str) -> bool:
    for alias in TERM_ALIASES.get(term, []):
        if alias in statement:
            return True
    if term == "field":
        return "over" in statement or "base field" in statement
    if term == "projective":
        return "proper" in statement and "ample" in statement
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
