from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .models import LiteratureReviewAgentReport, SourceGroundingReport, TheoremApplicabilityReport


def audit_theorem_applicability(run_dir: Path) -> dict:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    optional = manifest.get("optional_artifacts", {})
    source_grounding = SourceGroundingReport.model_validate(
        read_yaml(run_dir / artifacts["source_grounding"])
    )
    literature_report = _load_literature_report(run_dir, optional)
    usages = []
    for step in source_grounding.steps:
        usages.append(_usage_for_step(step, source_grounding, literature_report))
    overall = _overall_status(usages)
    report = {
        "schema_version": 1,
        "claim_id": source_grounding.claim_id,
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_grounding_artifact": artifacts["source_grounding"],
        "literature_review_agent_artifact": optional.get("literature_review_agent"),
        "usages": usages,
        "overall_status": overall,
        "notes": [
            "Theorem Applicability Auditor records condition checks for theorem usage.",
            "It does not upgrade source_backed; it can only confirm or block theorem applicability.",
        ],
    }
    TheoremApplicabilityReport.model_validate(report)
    write_yaml(run_dir / "theorem_applicability.yaml", report)
    return report


def _load_literature_report(run_dir: Path, optional: dict) -> LiteratureReviewAgentReport | None:
    name = optional.get("literature_review_agent")
    if not name:
        return None
    return LiteratureReviewAgentReport.model_validate(read_yaml(run_dir / name))


def _usage_for_step(step, source_grounding: SourceGroundingReport, literature_report):
    comparison = _best_comparison(step, literature_report)
    ref_id = step.source_refs[0] if step.source_refs else "missing_source_ref"
    source_ref = next((ref for ref in source_grounding.source_refs if ref.id == ref_id), None)
    locator = (
        comparison_locator(comparison)
        if comparison is not None
        else (source_ref.locator.model_dump(mode="json") if source_ref else {"kind": "unknown", "value": ""})
    )
    statement = (
        _theorem_statement(comparison, literature_report)
        if comparison is not None
        else step.needed_theorem
    )
    conditions = (
        _conditions_from_comparison(comparison)
        if comparison is not None
        else _conditions_from_step(step)
    )
    conclusion_status = (
        _conclusion_status(comparison)
        if comparison is not None
        else _fallback_conclusion_status(step)
    )
    applicability_status = _applicability_status(conditions, conclusion_status, step.status.value)
    return {
        "proof_step_id": step.proof_step_id,
        "source_ref_id": ref_id,
        "theorem_id": comparison.theorem_id if comparison is not None else None,
        "theorem_label": _theorem_label(comparison, literature_report) if comparison is not None else None,
        "theorem_statement": statement,
        "locator": locator,
        "source_status": step.status.value,
        "conclusion_status": conclusion_status,
        "conditions": conditions,
        "applicability_status": applicability_status,
        "blocks_source_backed": applicability_status != "applicable",
        "notes": _usage_notes(comparison, step.status.value),
    }


def _best_comparison(step, literature_report):
    if literature_report is None:
        return None
    haystack = step.needed_theorem.lower()
    for comparison in literature_report.theorem_comparisons:
        theorem = _theorem_by_id(comparison.theorem_id, literature_report)
        if theorem is None:
            continue
        text = f"{theorem.label} {theorem.statement_exact} {comparison.relevance.summary}".lower()
        if any(term in text for term in _significant_terms(haystack)):
            return comparison
    return literature_report.theorem_comparisons[0] if literature_report.theorem_comparisons else None


def comparison_locator(comparison):
    return comparison.hypothesis_matches[0].support_locator.model_dump(mode="json") if (
        comparison.hypothesis_matches and comparison.hypothesis_matches[0].support_locator
    ) else {"kind": "unknown", "value": comparison.theorem_id}


def _theorem_statement(comparison, literature_report) -> str:
    theorem = _theorem_by_id(comparison.theorem_id, literature_report)
    return theorem.statement_exact if theorem is not None else comparison.relevance.summary


def _theorem_label(comparison, literature_report) -> str | None:
    theorem = _theorem_by_id(comparison.theorem_id, literature_report)
    return theorem.label if theorem is not None else None


def _theorem_by_id(theorem_id: str, literature_report):
    return next(
        (theorem for theorem in literature_report.extracted_theorems if theorem.id == theorem_id),
        None,
    )


def _conditions_from_comparison(comparison) -> list[dict]:
    conditions = []
    for index, match in enumerate(comparison.hypothesis_matches, start=1):
        conditions.append(
            {
                "id": f"cond_{index:03d}",
                "text": match.claim_hypothesis,
                "condition_type": _condition_type(match.claim_hypothesis),
                "status": _condition_status_from_relation(match.relation),
                "evidence": match.reason,
                "definition_locator": (
                    match.support_locator.model_dump(mode="json")
                    if match.support_locator is not None
                    else None
                ),
            }
        )
    if not conditions:
        conditions.append(
            {
                "id": "cond_001",
                "text": "No theorem hypotheses were extracted for comparison.",
                "condition_type": "other",
                "status": "not_checked",
                "evidence": "Literature review did not provide theorem hypothesis matches.",
                "definition_locator": None,
            }
        )
    return conditions


def _conditions_from_step(step) -> list[dict]:
    mismatches = list(step.hypothesis_match.mismatches)
    if step.hypothesis_match.matches is True:
        return [
            {
                "id": "cond_001",
                "text": "Source grounding hypothesis_match reports matching hypotheses.",
                "condition_type": "other",
                "status": "satisfied",
                "evidence": step.note,
                "definition_locator": None,
            }
        ]
    return [
        {
            "id": f"cond_{index:03d}",
            "text": mismatch,
            "condition_type": _condition_type(mismatch),
            "status": "unclear" if step.hypothesis_match.matches == "unclear" else "mismatched",
            "evidence": step.note,
            "definition_locator": None,
        }
        for index, mismatch in enumerate(mismatches or ["hypothesis match was not checked"], start=1)
    ]


def _condition_status_from_relation(relation: str) -> str:
    return {
        "same": "satisfied",
        "stronger": "satisfied",
        "weaker": "unclear",
        "missing_in_theorem": "missing",
        "extra_in_theorem": "missing",
        "incompatible": "mismatched",
        "unclear": "unclear",
    }.get(relation, "unclear")


def _condition_type(text: str) -> str:
    lowered = text.lower()
    if "smooth" in lowered or "nonsingular" in lowered:
        return "smoothness"
    if "proper" in lowered or "projective" in lowered:
        return "properness"
    if "flat" in lowered:
        return "flatness"
    if "coherent" in lowered:
        return "coherence"
    if "field" in lowered or "over" in lowered:
        return "base_field"
    if "very general" in lowered or "generic" in lowered:
        return "genericity"
    if "morphism" in lowered or "map" in lowered:
        return "morphism"
    if "definition" in lowered:
        return "definition"
    return "other"


def _conclusion_status(comparison) -> str:
    if comparison is None:
        return "unclear"
    return {
        "same": "compatible",
        "stronger": "stronger",
        "weaker": "weaker",
        "orthogonal": "orthogonal",
        "incompatible": "contradicts",
        "unclear": "unclear",
    }[comparison.conclusion_match.relation]


def _fallback_conclusion_status(step) -> str:
    if step.status.value == "source_backed" and step.hypothesis_match.matches is True:
        return "compatible"
    return "unclear"


def _applicability_status(conditions: list[dict], conclusion_status: str, source_status: str) -> str:
    statuses = {condition["status"] for condition in conditions}
    if "mismatched" in statuses or conclusion_status == "contradicts" or source_status == "misapplied":
        return "misapplied"
    if statuses.intersection({"missing", "not_checked"}):
        return "blocked"
    if "unclear" in statuses or conclusion_status in {"orthogonal", "unclear"}:
        return "unclear"
    return "applicable"


def _usage_notes(comparison, source_status: str) -> list[str]:
    notes = [f"Source grounding status was {source_status}."]
    if comparison is None:
        notes.append("No Literature Review Agent theorem comparison was available; used source grounding fallback.")
    else:
        notes.append(f"Used theorem comparison status {comparison.comparison_status}.")
    return notes


def _overall_status(usages: list[dict]) -> str:
    statuses = {usage["applicability_status"] for usage in usages}
    if "misapplied" in statuses:
        return "misapplied"
    if "blocked" in statuses:
        return "blocked"
    if "unclear" in statuses:
        return "unclear"
    return "all_applicable"


def _significant_terms(text: str) -> list[str]:
    stop = {"and", "for", "the", "this", "with", "that", "from", "source", "theorem"}
    return [token for token in text.split() if len(token) > 3 and token not in stop][:12]
