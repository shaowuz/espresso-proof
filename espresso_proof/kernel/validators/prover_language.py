"""Prover-round assertion parsing and status-smuggling detection."""

from __future__ import annotations

import re


def _prover_assertion_clauses(text: str) -> list[str]:
    normalized = " ".join(str(text).lower().split())
    rough = re.split(
        r"(?:[.;!?]\s+|,\s+|\n+|\s+\b(?:but|however|while|although|whereas)\b\s+)",
        normalized,
    )
    return [part.strip() for part in rough if part.strip()]

def _prover_clause_is_status_boundary_disclaimer(clause: str) -> bool:
    return any(
        phrase in clause
        for phrase in (
            "does not upgrade",
            "do not upgrade",
            "cannot upgrade",
            "can never upgrade",
            "never upgrades",
            "never upgrade",
            "does not set",
            "cannot set",
            "cannot write",
            "must not upgrade",
            "not proof status",
            "not evidence",
            "not a proof",
            "is not proof",
            "not applicable",
            "not checked",
            "not refuted",
            "advisory only",
            "advisory-only",
            "scheduling-only",
        )
    )

def _prover_status_smuggling_field(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        for field in (
            "proof_status",
            "proof_language_status",
            "agent_consensus_mock_checked",
            "agent_consensus",
            "mock_checked",
            "source_status",
            "source_backed_status",
            "source_backed",
            "evidence_status",
            "checked_evidence",
            "theorem_applicability",
            "theorem_applicability_status",
            "applicability_status",
            "refutation_status",
            "counterexample_refutes_claim",
            "refutes_claim",
            "grade_status",
        ):
            if re.search(rf"\b{re.escape(field)}\s*[:=]", clause):
                return field
        owner_status_patterns = [
            (r"\bproof[_ -]?language[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "proof_language_status"),
            (r"\bproof\s+status\s+(?:is|are|was|were)\s+[\w -]+", "proof_status"),
            (r"\bsource[_ -]?backed[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "source_backed_status"),
            (r"\bsource\s+status\s+(?:is|are|was|were)\s+[\w -]+", "source_status"),
            (r"\bevidence\s+status\s+(?:is|are|was|were)\s+[\w -]+", "evidence_status"),
            (r"\bchecked[_ -]?evidence\s+(?:is|are|was|were)\s+[\w -]+", "checked_evidence"),
            (r"\btheorem[_ -]?applicability[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\btheorem\s+applicability\s+status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\brefutation\s+status\s+(?:is|are|was|were)\s+[\w -]+", "refutation_status"),
            (r"\bgrade[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
            (r"\bgrade\s+status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
        ]
        for pattern, name in owner_status_patterns:
            if re.search(pattern, clause):
                return name
        if _prover_clause_is_status_boundary_disclaimer(clause):
            continue
        status_patterns = [
            (r"\b(?:is|are|as|becomes|became|counts?\s+as|treats?\s+as)\s+source[_ -]?backed\b", "source_backed"),
            (r"\bsource\s+(?:status\s+)?(?:is|=|:)\s*(?:backed|checked|supported)\b", "source_status"),
            (r"\bchecked[_ -]?evidence\s+(?:supports?|establishes?|proves?|is|are)\b", "checked_evidence"),
            (r"\b(?:is|are|as|counts?\s+as|treats?\s+as)\s+checked[_ -]?evidence\b", "checked_evidence"),
            (r"\bevidence\s+(?:status\s+)?(?:is|=|:)\s*(?:checked|verified)\b", "evidence_status"),
            (r"\btheorem\s+applicability(?:\s+status)?\s+(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\bapplicability[_ ]status\s*(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\ball[_ ]applicable\b", "theorem_applicability"),
            (r"\bexamples?\s+(?:have\s+)?survived\b", "example_status"),
            (r"\bsurvived\s+examples?\b", "example_status"),
            (r"\bno\s+counterexamples?\s+(?:remain|exist|found|pressure)\b", "counterexample_pressure"),
            (r"\bmissing\s+counterexamples?\s+(?:imply|mean|show|prove)\s+no\s+pressure\b", "counterexample_pressure"),
            (r"\bcounterexamples?\s+refutes?\b", "counterexample_refutes_claim"),
            (r"\bcounterexample[_ -]?refutes[_ -]?claim\b", "counterexample_refutes_claim"),
            (r"\b(?:claim|route|statement)\s+is\s+refuted\b", "refutation_status"),
            (r"\brefuted\b", "refutation_status"),
        ]
        for pattern, name in status_patterns:
            if re.search(pattern, clause):
                return name
    return None

def _prover_clause_is_support_disclaimer(clause: str) -> bool:
    return any(
        phrase in clause
        for phrase in (
            "cannot support",
            "can't support",
            "does not support",
            "doesn't support",
            "not support",
            "never support",
            "cannot contribute",
            "can't contribute",
            "do not contribute",
            "does not contribute",
            "doesn't contribute",
            "not contribute",
            "cannot count",
            "can't count",
            "not evidence",
            # "carry/carries no evidence", "no evidence has been", "provides no
            # evidence": the negation precedes the noun instead of the verb, and
            # the phrase list missed that form entirely. Found live: the
            # kernel's OWN disclosure line "no deterministic verifier has been
            # run for them and they carry no evidence: <reviewer-request-id>"
            # was flagged as treating advisory context as evidence — the id
            # contains "reviewer" and the clause contains "evidence", and the
            # sentence saying NO evidence was read as claiming evidence.
            "no evidence",
            "carry no",
            "carries no",
            "is not proof",
            "not a proof",
        )
    )

def _prover_unchecked_support_misuse(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        if _prover_clause_is_support_disclaimer(clause):
            continue
        supportish = re.search(r"\b(?:support|supports|establish|establishes|prove|proves)\b", clause)
        if not supportish:
            continue
        if "unchecked" in clause and any(
            noun in clause
            for noun in ("citation", "citations", "source", "sources", "reference", "references")
        ):
            return "unchecked citation/source"
        if "unchecked" in clause and "example" in clause:
            return "unchecked example"
        if "pending theorem applicability" in clause:
            return "pending theorem applicability"
        if "line-level gap" in clause:
            return "line-level gap"
    return None
