from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .claim_clear import ensure_claim_clear_for_downstream
from .io import read_yaml
from .pdf_ingestion import extract_source_text, extract_theorems, locator_for_source, read_source_bytes
from .source_verification import expanded_hypothesis_terms, significant_terms, term_covered
from .models import LiteratureReviewAgentReport


@dataclass(frozen=True)
class ReviewSourceSpec:
    source: str
    source_id: str | None = None
    title: str | None = None
    authors: list[str] | None = None
    source_type: str = "paper"


def run_literature_review_agent(
    *,
    run_dir: Path,
    source_specs: list[ReviewSourceSpec] | None = None,
) -> dict:
    """Read candidate sources and emit a conservative literature-review artifact.

    This agent is stronger than metadata discovery because it reads supplied
    source text/PDFs and compares extracted theorem statements against the
    locked claim. It still does not write source_backed or misapplied; those
    remain Referencing decisions.
    """

    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    ledger = read_yaml(run_dir / artifacts["hypothesis_ledger"])
    literature_review = read_yaml(run_dir / artifacts["literature_review"])

    specs = source_specs or []
    input_sources = []
    reviewed_theorems = []
    notes = [
        "Literature Review Agent reads candidate sources and compares extracted theorem statements.",
        "Applicability judgments are review-level only; Referencing still owns source_backed and misapplied status.",
    ]

    if specs:
        for index, spec in enumerate(specs, start=1):
            reviewed_source, raw_theorems, source_notes = read_review_source(spec, index)
            input_sources.append(reviewed_source)
            notes.extend(source_notes)
            if reviewed_source["access"]["status"] == "full_text_read":
                reviewed_theorems.extend(
                    reviewed_theorem_from_extraction(
                        raw_theorem,
                        source_ref_id=reviewed_source["source_ref"]["id"],
                        source_access=reviewed_source["access"]["status"],
                    )
                    for raw_theorem in raw_theorems
                )
    else:
        notes.append("No readable source paths were supplied; candidate references are recorded as metadata only.")
        input_sources.extend(metadata_only_sources(literature_review))

    comparisons = [
        compare_theorem_to_claim(theorem, claim=claim, ledger=ledger)
        for theorem in reviewed_theorems
    ]
    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "input_sources": input_sources,
        "extracted_theorems": reviewed_theorems,
        "theorem_comparisons": comparisons,
        "relevance_summary": summarize_relevance(input_sources, reviewed_theorems, comparisons),
        "uncertainty": summarize_uncertainty(input_sources, reviewed_theorems, comparisons),
        "notes": notes,
    }
    LiteratureReviewAgentReport.model_validate(report)
    return report


def read_review_source(spec: ReviewSourceSpec, index: int) -> tuple[dict, list[dict], list[str]]:
    source_id = spec.source_id or f"review_src_{index:03d}"
    title = spec.title or infer_title(spec.source)
    notes = []
    try:
        raw = read_source_bytes(spec.source)
        text, extraction_notes = extract_source_text(raw, spec.source)
        notes.extend(extraction_notes)
    except Exception as exc:
        source_ref = make_source_ref(
            source_id=source_id,
            source_type=spec.source_type,
            title=title,
            authors=spec.authors or [],
            source=spec.source,
            summary=f"Source could not be read: {exc}",
            confidence="low",
        )
        return (
            {
                "source_ref": source_ref,
                "access": {
                    "status": "unreadable",
                    "access_path": spec.source,
                    "pages_or_sections_read": [],
                    "extraction_method": "unknown",
                },
                "bibliographic_confidence": "low",
            },
            [],
            [f"Could not read {spec.source}: {exc}"],
        )

    theorems = extract_theorems(text, source_ref_id=source_id)
    source_ref = make_source_ref(
        source_id=source_id,
        source_type=spec.source_type,
        title=title,
        authors=spec.authors or [],
        source=spec.source,
        summary=f"Full text read for literature review; {len(theorems)} theorem-like statement(s) extracted.",
        confidence="medium",
    )
    return (
        {
            "source_ref": source_ref,
            "access": {
                "status": "full_text_read",
                "access_path": spec.source,
                "pages_or_sections_read": ["full_text"],
                "extraction_method": extraction_method_for_source(spec.source),
            },
            "bibliographic_confidence": "medium",
        },
        theorems,
        notes,
    )


def make_source_ref(
    *,
    source_id: str,
    source_type: str,
    title: str,
    authors: list[str],
    source: str,
    summary: str,
    confidence: str,
) -> dict:
    return {
        "id": source_id,
        "type": source_type,
        "title": title,
        "authors": authors,
        "locator": locator_for_source(source),
        "statement_or_summary": summary,
        "hypothesis_match": {"matches": "unclear", "mismatches": []},
        "confidence": confidence,
        "status": "candidate",
    }


def metadata_only_sources(literature_review: dict) -> list[dict]:
    sources = []
    for index, item in enumerate(literature_review.get("candidate_references", []), start=1):
        sources.append(
            {
                "source_ref": {
                    "id": item.get("id") or f"candidate_ref_{index:03d}",
                    "type": "paper",
                    "title": item.get("title", "Untitled candidate source"),
                    "authors": item.get("authors", []),
                    "locator": item.get("locator", {"kind": "unknown", "value": "to_be_filled"}),
                    "statement_or_summary": item.get("why_relevant", "Candidate source from literature discovery."),
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                    "confidence": "low",
                    "status": "candidate",
                },
                "access": {
                    "status": "metadata_only",
                    "access_path": None,
                    "pages_or_sections_read": [],
                    "extraction_method": "metadata",
                },
                "bibliographic_confidence": "low",
            }
        )
    return sources


def reviewed_theorem_from_extraction(
    theorem: dict,
    *,
    source_ref_id: str,
    source_access: str,
) -> dict:
    statement = theorem["statement"]
    return {
        "id": theorem["id"],
        "source_ref_id": source_ref_id,
        "kind": theorem["kind"],
        "label": theorem["label"],
        "locator": theorem["locator"],
        "statement_exact": statement,
        "statement_is_verbatim": source_access == "full_text_read",
        "statement_span": {"page": None, "section": None, "char_start": None, "char_end": None},
        "theorem_hypotheses": theorem_hypotheses(statement, theorem.get("hypothesis_terms", [])),
        "theorem_conclusion": theorem_conclusion(statement),
        "extraction_confidence": theorem.get("confidence", "medium"),
    }


def theorem_hypotheses(statement: str, hypothesis_terms: list[str]) -> list[dict]:
    hypotheses = []
    for index, term in enumerate(hypothesis_terms, start=1):
        hypotheses.append(
            {
                "id": f"hyp_{index:03d}",
                "text": term,
                "normalized": term.lower(),
                "definition_refs": [],
                "status": "explicit",
            }
        )
    prefix = hypothesis_prefix(statement)
    if prefix and not hypotheses:
        hypotheses.append(
            {
                "id": "hyp_001",
                "text": prefix,
                "normalized": prefix.lower(),
                "definition_refs": [],
                "status": "inferred",
            }
        )
    return hypotheses


def hypothesis_prefix(statement: str) -> str:
    lowered = statement.lower()
    for marker in [" then ", " we have ", " one has "]:
        position = lowered.find(marker)
        if 20 < position < 400:
            return statement[:position].strip(" ,.;")
    return ""


def theorem_conclusion(statement: str) -> str:
    lowered = statement.lower()
    for marker in [" then ", " we have ", " one has "]:
        position = lowered.find(marker)
        if 20 < position < len(statement) - 10:
            return statement[position + len(marker) :].strip(" ,.;")
    return statement


def compare_theorem_to_claim(theorem: dict, *, claim: dict, ledger: dict) -> dict:
    statement = theorem["statement_exact"]
    lowered = statement.lower()
    hypothesis_matches = []
    failure_modes: set[str] = set()
    extra_source_terms = {hypothesis["normalized"] for hypothesis in theorem.get("theorem_hypotheses", [])}

    for hypothesis in required_claim_hypotheses(claim):
        comparison = compare_claim_hypothesis(hypothesis, theorem, lowered, claim)
        hypothesis_matches.append(comparison)
        if comparison["relation"] in {"missing_in_theorem", "incompatible", "unclear"}:
            failure_modes.add("hypothesis_mismatch")

    claim_text = " ".join(
        [
            claim.get("raw_text", ""),
            claim.get("normalized_statement", ""),
            claim.get("conclusion", ""),
            " ".join(claim.get("objects", [])),
            " ".join(hypothesis.get("name", "") for hypothesis in claim.get("hypotheses", [])),
        ]
    ).lower()
    claim_terms = {
        term
        for hypothesis in required_claim_hypotheses(claim)
        for term in hypothesis_terms(hypothesis.get("name", ""))
    }
    for term in sorted(extra_source_terms - claim_terms):
        if term in claim_text:
            continue
        if term in {"very general", "generic", "general"}:
            continue
        hypothesis_matches.append(
            {
                "claim_hypothesis": f"extra source hypothesis: {term}",
                "theorem_hypothesis_id": theorem_hypothesis_id(theorem, term),
                "relation": "extra_in_theorem",
                "reason": f"The theorem statement includes the additional hypothesis term {term!r}.",
                "support_locator": theorem["locator"],
            }
        )

    conclusion_match = compare_conclusion(statement, claim)
    if conclusion_match["relation"] in {"orthogonal", "incompatible", "unclear"}:
        failure_modes.add("ambiguous_statement")

    if not theorem["locator"]["value"] or theorem["locator"]["kind"] == "unknown":
        failure_modes.add("missing_exact_locator")

    if ledger.get("missing_or_unclear"):
        failure_modes.add("translation_unclear")

    status = comparison_status(hypothesis_matches, conclusion_match)
    confidence = "high" if status == "applicable" and not failure_modes else "medium"
    if status == "unclear" and len(failure_modes) > 1:
        confidence = "low"

    return {
        "theorem_id": theorem["id"],
        "claim_id": claim["id"],
        "comparison_status": status,
        "hypothesis_matches": hypothesis_matches,
        "conclusion_match": conclusion_match,
        "relevance": theorem_relevance(statement, claim, status),
        "uncertainty": {
            "confidence": confidence,
            "failure_modes": sorted(failure_modes),
            "explanation": uncertainty_explanation(status, failure_modes, ledger),
        },
    }


def required_claim_hypotheses(claim: dict) -> list[dict]:
    return [
        hypothesis
        for hypothesis in claim.get("hypotheses", [])
        if hypothesis.get("status") in {"explicit", "inferred"}
    ]


def hypothesis_terms(text: str) -> list[str]:
    return sorted(dict.fromkeys(significant_terms(text) + expanded_hypothesis_terms(text)))


def compare_claim_hypothesis(hypothesis: dict, theorem: dict, lowered_statement: str, claim: dict) -> dict:
    name = hypothesis.get("name", "")
    if all_to_very_general_mismatch(claim, lowered_statement):
        return {
            "claim_hypothesis": name,
            "theorem_hypothesis_id": theorem_hypothesis_id(theorem, "very general"),
            "relation": "incompatible",
            "reason": "The locked claim is universal, but the source theorem is restricted to a very general or generic case.",
            "support_locator": theorem["locator"],
        }

    missing = [
        term
        for term in hypothesis_terms(name)
        if term not in lowered_statement and not term_covered(term, lowered_statement)
    ]
    if missing:
        return {
            "claim_hypothesis": name,
            "theorem_hypothesis_id": None,
            "relation": "missing_in_theorem",
            "reason": f"Required claim hypothesis term(s) not found in the theorem statement: {', '.join(missing)}.",
            "support_locator": theorem["locator"],
        }
    return {
        "claim_hypothesis": name,
        "theorem_hypothesis_id": theorem_hypothesis_id(theorem, name),
        "relation": "same",
        "reason": "The theorem statement contains the required claim-hypothesis terms or accepted aliases.",
        "support_locator": theorem["locator"],
    }


def all_to_very_general_mismatch(claim: dict, theorem_statement: str) -> bool:
    claim_text = " ".join(
        [
            claim.get("raw_text", ""),
            claim.get("normalized_statement", ""),
            claim.get("conclusion", ""),
        ]
    ).lower()
    universal = any(term in claim_text for term in ["for all", "all ", "every "])
    restricted = any(term in theorem_statement for term in ["very general", "generic", "general member"])
    return universal and restricted


def theorem_hypothesis_id(theorem: dict, text: str) -> str | None:
    terms = set(hypothesis_terms(text))
    for hypothesis in theorem.get("theorem_hypotheses", []):
        haystack = " ".join([hypothesis["text"], hypothesis["normalized"]]).lower()
        if hypothesis["normalized"] in text.lower() or any(term in haystack for term in terms):
            return hypothesis["id"]
    return None


def compare_conclusion(statement: str, claim: dict) -> dict:
    claim_text = " ".join([claim.get("conclusion", ""), claim.get("normalized_statement", "")]).lower()
    statement_terms = set(significant_terms(statement))
    claim_terms = set(significant_terms(claim_text))
    if not claim_terms:
        return {"relation": "unclear", "reason": "The locked claim conclusion has no comparable terms."}
    overlap = statement_terms & claim_terms
    if {"irrationality", "irrational"} & (statement_terms | claim_terms):
        return {"relation": "same", "reason": "The theorem and claim both address irrationality."}
    if len(overlap) >= 2:
        return {"relation": "same", "reason": f"The theorem conclusion overlaps the claim on: {', '.join(sorted(overlap)[:5])}."}
    if overlap:
        return {"relation": "unclear", "reason": f"Only weak conclusion overlap was found: {', '.join(sorted(overlap))}."}
    return {"relation": "orthogonal", "reason": "No meaningful conclusion overlap was found."}


def comparison_status(hypothesis_matches: list[dict], conclusion_match: dict) -> str:
    relations = {match["relation"] for match in hypothesis_matches}
    if "incompatible" in relations or conclusion_match["relation"] == "incompatible":
        return "weaker_than_needed"
    if "missing_in_theorem" in relations or conclusion_match["relation"] in {"orthogonal", "unclear"}:
        return "unclear"
    if "extra_in_theorem" in relations:
        return "stronger_than_needed"
    return "applicable"


def theorem_relevance(statement: str, claim: dict, status: str) -> dict:
    claim_text = " ".join([claim.get("normalized_statement", ""), claim.get("conclusion", "")])
    overlap = set(significant_terms(statement)) & set(significant_terms(claim_text))
    if status == "applicable":
        role = "key_theorem"
    elif len(overlap) >= 2:
        role = "supporting_lemma"
    else:
        role = "background"
    return {
        "role": role,
        "summary": f"Overlap with locked claim terms: {', '.join(sorted(overlap)[:8]) or 'none'}.",
    }


def uncertainty_explanation(status: str, failure_modes: set[str], ledger: dict) -> str:
    if status == "applicable" and not failure_modes:
        return "Exact extracted statement matched the currently explicit/inferred claim hypotheses."
    parts = []
    if failure_modes:
        parts.append(f"Blocking failure modes: {', '.join(sorted(failure_modes))}.")
    if ledger.get("missing_or_unclear"):
        parts.append(f"Hypothesis ledger still lists unclear items: {', '.join(ledger['missing_or_unclear'])}.")
    return " ".join(parts) or "Applicability remains uncertain."


def summarize_relevance(input_sources: list[dict], theorems: list[dict], comparisons: list[dict]) -> dict:
    strongest = [
        {
            "source_ref_id": theorem["source_ref_id"],
            "theorem_id": theorem["id"],
            "reason": comparison["relevance"]["summary"],
        }
        for theorem, comparison in zip(theorems, comparisons, strict=False)
        if comparison["relevance"]["role"] in {"key_theorem", "supporting_lemma"}
    ][:5]
    blockers = [
        comparison["uncertainty"]["explanation"]
        for comparison in comparisons
        if comparison["comparison_status"] != "applicable"
    ]
    if not input_sources:
        overall = "no_relevant_source"
        blockers.append("No candidate sources were available to review.")
    elif not theorems:
        overall = "no_relevant_source"
        blockers.append("No theorem-like statements were extracted from the reviewed sources.")
    elif any(comparison["comparison_status"] == "applicable" for comparison in comparisons):
        overall = "supports_route"
    elif strongest:
        overall = "unclear"
    else:
        overall = "background_only"
    return {
        "overall_status": overall,
        "strongest_sources": strongest,
        "main_blockers": sorted(dict.fromkeys(blockers)),
    }


def summarize_uncertainty(input_sources: list[dict], theorems: list[dict], comparisons: list[dict]) -> dict:
    blockers = []
    human_review = []
    if any(source["access"]["status"] != "full_text_read" for source in input_sources):
        blockers.append("At least one candidate source was not read as full text.")
    if not theorems:
        blockers.append("No exact theorem statement was extracted.")
    for comparison in comparisons:
        if comparison["uncertainty"]["confidence"] != "high":
            blockers.append(comparison["uncertainty"]["explanation"])
    if blockers:
        human_review.append("Review extracted theorem statements and hypothesis matches before proof use.")
    confidence = "high" if comparisons and not blockers else "medium" if theorems else "low"
    return {
        "overall_confidence": confidence,
        "blocking_uncertainties": sorted(dict.fromkeys(blockers)),
        "needs_human_review": human_review,
    }


def infer_title(source: str) -> str:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        name = Path(parsed.path).name
        return name or parsed.netloc
    return Path(source).stem or "Ingested source"


def extraction_method_for_source(source: str) -> str:
    lowered = source.lower()
    if lowered.endswith(".pdf"):
        return "pdf_text"
    if lowered.endswith((".tex", ".latex")):
        return "tex_source"
    if lowered.endswith((".html", ".htm")):
        return "html"
    return "plain_text"
