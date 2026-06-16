from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .proof_language import scrub_proof_language


SOURCE_GAPS = "source_gaps"
EXAMPLES_PENDING = "examples_pending"
COUNTEREXAMPLES_PENDING = "counterexamples_pending"
THEOREM_BLOCKERS = "theorem_blockers"
REVIEWER_BLOCKERS = "reviewer_blockers"
UPDATE_INBOX_PENDING = "update_inbox_pending"


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _text_items(value: Any) -> list[str]:
    return [_display_text(item).rstrip(".") for item in (value or []) if str(item).strip()]


def _display_text(value: Any) -> str:
    text = scrub_proof_language(str(value).strip()).replace(
        "source_backed", "reference-supported"
    )
    advisory_context_terms = (
        "memory",
        "prior run",
        "prior-run",
        "prior runs",
        "human preference",
        "human choice",
        "human confirmed",
        "mathematician preference",
        "strategy brief",
        "update inbox",
        "literature",
        "literature review",
        "literature candidate",
        "literature candidates",
        "candidate literature",
        "candidate source",
        "candidate sources",
        "candidate reference",
        "candidate references",
        "candidate theorem",
        "prover text",
        "prover output",
        "prover draft",
        "prover round",
        "reviewer text",
        "reviewer output",
        "reviewer approval",
        "reviewer objection",
        "reviewer report",
        "reviewer verdict",
        "reviewers passed",
        "autonomous retrieval",
        "retrieval hit",
        "proposed source",
        "proposed sources",
        "unchecked source",
        "unchecked citation",
    )
    status_projection = re.compile(
        r"\b(?:evidence|support(?:s|ed|ing)?|establish(?:es|ed|ing)?|"
        r"prove(?:s|d|n|ing)?|confirm(?:s|ed|ing)?|validat(?:es|ed|ing)?|"
        r"certif(?:y|ies|ied|ying)|verif(?:y|ies|ied|ying)|"
        r"show(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|"
        r"approv(?:e|es|ed|ing))\b",
        re.IGNORECASE,
    )
    if (
        any(term in text.lower() for term in advisory_context_terms)
        and status_projection.search(text)
    ):
        text = "[withheld advisory status phrase]"
    text = re.sub(
        (
            r"\b(?:route|strategy|prover|reviewer)\s+"
            r"(?:text|output|answer|response|draft|round|comment|assessment|"
            r"approval|objection|report|verdict)?\s*"
            r"(?:(?:is|are|was|were|becomes?|became)\s+)?"
            r"(?:evidence\s+for\s+|support(?:s|ed|ing)?|show(?:s|ed|ing)?|"
            r"(?:a\s+)?proof(?:\s+status)?|source\s+support|checked\s+evidence|"
            r"refutation(?:\s+status)?|"
            r"demonstrat(?:e|es|ed|ing)|"
            r"certif(?:y|ies|ied|ying)|verif(?:y|ies|ied|ying)|"
            r"prove(?:s|d|n|ing)?|establish(?:es|ed|ing)?|"
            r"confirm(?:s|ed|ing)?|validat(?:es|ed|ing)?|"
            r"approv(?:e|es|ed|ing)?)[^.。;；,\n]*"
        ),
        "[withheld advisory status phrase]",
        text,
        flags=re.IGNORECASE,
    )
    replacements = {
        r"\bq\.?e\.?d\.?\b": "proof-completion marker",
        r"\bverified\b": "checked",
        r"\bproof\s+complete\b": "proof-completion wording",
        r"\bcomplete\s+proof\b": "proof-completion wording",
        r"\bwe\s+proved\b": "the draft claimed",
        r"\bproved\b": "claimed",
        r"\bproven\b": "claimed",
        r"\bproves\b": "claims",
        r"\bthis\s+proof\b": "this argument",
        r"\b(?:settles?|settled)\s+the\s+(?:theorem|claim|result)\b": "claimed unresolved status",
        r"\bsettled\b": "claimed resolved",
        r"\bestablish(?:es|ed|ing)?\s+the\s+(?:theorem|claim|result)\b": "claimed unresolved status",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def pending_example_ids(examples: dict[str, Any]) -> list[str]:
    return [
        str(example.get("id"))
        for example in _items(examples.get("examples"))
        if example.get("result_for_current_idea") in {"inconclusive", "not_tested"}
    ]


def pending_counterexample_ids(
    counterexamples: dict[str, Any],
    tests: dict[str, Any],
) -> list[str]:
    pending = [
        str(test.get("id"))
        for test in _items(counterexamples.get("tests"))
        if test.get("result") in {"inconclusive", "not_tested"}
    ]
    pending.extend(
        str(item.get("id"))
        for item in _items(tests.get("tests"))
        if item.get("uses_counterexamples")
        and item.get("result") in {"inconclusive", "not_tested"}
    )
    return list(dict.fromkeys(pending))


def _example_pressure_gap_ids(examples: dict[str, Any]) -> list[str]:
    examples_list = _items(examples.get("examples"))
    if not examples_list:
        return ["no example checks recorded"]
    return pending_example_ids(examples)


def _counterexample_pressure_gap_ids(
    counterexamples: dict[str, Any],
    tests: dict[str, Any],
) -> list[str]:
    counterexample_tests = _items(counterexamples.get("tests"))
    test_items = _items(tests.get("tests"))
    gaps = pending_counterexample_ids(counterexamples, tests)
    has_counterexample_pressure = bool(counterexample_tests) or any(
        item.get("uses_counterexamples") for item in test_items
    )
    if not has_counterexample_pressure:
        gaps.append("no counterexample checks recorded")
    return list(dict.fromkeys(gaps))


def theorem_blocker_ids(theorem_applicability: dict[str, Any]) -> list[str]:
    blocked: list[str] = []
    if not theorem_applicability:
        return blocked
    usages = list(theorem_applicability.get("usages") or [])
    usages.extend(theorem_applicability.get("ledger_usages") or [])
    for usage in _items(usages):
        if usage.get("blocks_source_backed") or usage.get("applicability_status") in {
            "blocked",
            "misapplied",
            "unclear",
        }:
            blocked.append(str(usage.get("proof_step_id") or "unnamed theorem usage"))
    if theorem_applicability.get("overall_status") in {"blocked", "misapplied", "unclear"} and not blocked:
        blocked.append(str(theorem_applicability.get("overall_status")))
    return list(dict.fromkeys(blocked))


def reviewer_blocker_summary(review: dict[str, Any]) -> str:
    pieces: list[str] = []
    for reviewer in _items(review.get("reviewers")):
        verdict = reviewer.get("verdict")
        if verdict == "fail":
            pieces.append(f"{reviewer.get('reviewer_id')}: blocking objection")
        elif verdict == "unclear":
            pieces.append(f"{reviewer.get('reviewer_id')}: needs more review")
    for label, key in (
        ("theorem drift", "theorem_drift"),
        ("missing hypotheses", "missing_hypotheses"),
        ("source risks", "source_risks"),
        ("example risks", "example_risks"),
        ("downgrade reasons", "downgrade_reasons"),
    ):
        values = _text_items(review.get(key))
        if values:
            pieces.append(f"{label}: {', '.join(values[:2])}")
    return "; ".join(pieces)


def pending_update_inbox_ids(update_inbox: dict[str, Any]) -> list[str]:
    return [
        str(entry.get("id"))
        for entry in _items(update_inbox.get("entries"))
        if entry.get("applied_at") in {None, ""}
    ]


def _source_gap_items(
    grade: dict[str, Any],
    source_grounding: dict[str, Any],
) -> list[str]:
    gaps = _text_items(grade.get("source_gaps"))
    for source_ref in _items(source_grounding.get("source_refs")):
        status = source_ref.get("status")
        if status and status != "source_backed":
            ref_id = source_ref.get("id") or "unnamed source ref"
            gaps.append(f"{ref_id}: source ref status {status}")
    for step in _items(source_grounding.get("steps")):
        status = step.get("status")
        if status and status != "source_backed":
            step_id = step.get("proof_step_id") or "unnamed step"
            gaps.append(f"{step_id}: source status {status}")
    return list(dict.fromkeys(gaps))


def collect_surface_warnings(
    *,
    grade: dict[str, Any],
    review: dict[str, Any],
    examples: dict[str, Any],
    counterexamples: dict[str, Any],
    tests: dict[str, Any],
    source_grounding: dict[str, Any] | None = None,
    theorem_applicability: dict[str, Any] | None = None,
    update_inbox: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    source_gaps = _source_gap_items(grade, source_grounding or {})
    if source_gaps:
        warnings.append(
            {
                "category": SOURCE_GAPS,
                "title": "Source gaps still open",
                "text": f"Source gaps still open: {', '.join(source_gaps[:6])}.",
                "ids": source_gaps,
            }
        )
    pending_examples = _example_pressure_gap_ids(examples)
    if pending_examples:
        warnings.append(
            {
                "category": EXAMPLES_PENDING,
                "title": "Examples pending/unavailable",
                "text": (
                    "Examples pending/unavailable: "
                    + ", ".join(pending_examples)
                    + " (not survived evidence)."
                ),
                "ids": pending_examples,
            }
        )
    pending_counterexamples = _counterexample_pressure_gap_ids(counterexamples, tests)
    if pending_counterexamples:
        warnings.append(
            {
                "category": COUNTEREXAMPLES_PENDING,
                "title": "Counterexample checks pending/unavailable",
                "text": (
                    "Counterexample checks pending/unavailable: "
                    + ", ".join(pending_counterexamples)
                    + " (no-counterexample pressure is not established)."
                ),
                "ids": pending_counterexamples,
            }
        )
    theorem_blockers = theorem_blocker_ids(theorem_applicability or {})
    if theorem_blockers:
        warnings.append(
            {
                "category": THEOREM_BLOCKERS,
                "title": "Theorem applicability blockers still open",
                "text": (
                    "Theorem applicability blockers still open: "
                    + ", ".join(theorem_blockers)
                    + "."
                ),
                "ids": theorem_blockers,
            }
        )
    reviewer_summary = reviewer_blocker_summary(review)
    if reviewer_summary:
        warnings.append(
            {
                "category": REVIEWER_BLOCKERS,
                "title": "Reviewer blockers still open",
                "text": f"Reviewer blockers still open: {reviewer_summary}.",
                "ids": [reviewer_summary],
            }
        )
    pending_updates = pending_update_inbox_ids(update_inbox or {})
    if pending_updates:
        warnings.append(
            {
                "category": UPDATE_INBOX_PENDING,
                "title": "Update Inbox pending advisory signals",
                "text": (
                    "Update Inbox pending advisory signals: "
                    + ", ".join(pending_updates)
                    + " (scheduling only, not evidence)."
                ),
                "ids": pending_updates,
            }
        )
    return warnings


def render_warning_lines(warnings: list[dict[str, Any]]) -> list[str]:
    return [str(warning.get("text") or "").strip() for warning in warnings if warning.get("text")]


def final_report_missing_disclosures(
    text: str,
    warnings: list[dict[str, Any]],
    *,
    allow_missing_header: bool = False,
) -> list[str]:
    lowered = " ".join(str(text).lower().split())
    if "open blockers / pending checks:" not in lowered:
        if allow_missing_header:
            return []
        return [str(warning.get("category") or "") for warning in warnings if warning.get("category")]
    missing: list[str] = []
    for warning in warnings:
        category = str(warning.get("category") or "")
        required = " ".join(str(warning.get("text") or "").lower().split())
        if required and required not in lowered:
            missing.append(category)
    return missing


def collect_surface_warnings_from_run(run_dir: Path) -> list[dict[str, Any]]:
    from .io import read_yaml

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict[str, Any]:
        rel = artifacts.get(key)
        path = run_dir / rel if rel else None
        return read_yaml(path) if path and path.exists() else {}

    def optional_artifact(key: str) -> dict[str, Any]:
        rel = optional.get(key)
        path = run_dir / rel if rel else None
        return read_yaml(path) if path and path.exists() else {}

    return collect_surface_warnings(
        grade=artifact("idea_grade"),
        review=artifact("review"),
        examples=artifact("examples"),
        counterexamples=artifact("counterexamples"),
        tests=artifact("tests"),
        source_grounding=artifact("source_grounding"),
        theorem_applicability=optional_artifact("theorem_applicability"),
        update_inbox=optional_artifact("update_inbox"),
    )


def final_report_disclosure_block(warnings: list[dict[str, Any]]) -> str:
    lines = render_warning_lines(warnings)
    if not lines:
        return ""
    block = "\n".join(f"- {line}" for line in lines)
    return "\n\nOpen blockers / pending checks:\n" + block + "\n"


_LEGACY_FINAL_BODY_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bno\s+source\s+gaps?\b",
        r"\bno\s+open\s+source\s+gaps?\b",
        r"\b(?:open\s+)?source\s+gaps?\s*:\s*(?:none|zero|0)\b",
        r"\bthere\s+(?:are|is)\s+no\s+open\s+source\s+gaps?\b",
        r"\bthere\s+(?:are|is)\s+(?:zero|0)\s+(?:open\s+)?source\s+gaps?\b",
        r"\bsource\s+gaps?\s+(?:are\s+)?(?:resolved|cleared|closed)\b",
        r"\ball\s+source\s+gaps?\s+ha(?:s|ve)\s+been\s+(?:resolved|cleared|closed)\b",
        r"\ball\s+sources?\s+(?:checked|validated|verified|backed)\b",
        r"\bsource[-_ ]backed\s+(?:proof|argument|route|strategy)\b",
        r"\b(?:proof|argument|route|strategy)\s+"
        r"(?:is|are|was|were|becomes?|became)\s+(?:a\s+)?source[-_ ]backed\b",
        r"\bno\s+pending\s+examples?\b",
        r"\bno\s+examples?\s+(?:are|remain|left|exist)\s+pending\b",
        r"\bno\s+examples?\s+checks?\s+pending\b",
        r"\bno\s+examples?\s+checks?\s+(?:are|remain|left|exist)\s+pending\b",
        r"\bexamples?\s+pending\s*:\s*(?:none|zero|0)\b",
        r"\bexamples?\s+checks?\s*:\s*none\s+pending\b",
        r"\bexamples?\s+(?:are\s+)?not\s+pending\b",
        r"\bexamples?\s+ha(?:s|ve)\s+no\s+pending\s+checks?\b",
        r"\ball\s+examples?\s+(?:survived|passed|validated|checked)\b",
        r"\bexamples?\s+(?:all\s+)?(?:survived|passed)\b",
        r"\bsurvived\s+examples?\b",
        r"\bno\s+(?:failing|failed)\s+examples?\b",
        r"\bno\s+pending\s+counterexamples?\b",
        r"\bno\s+counterexamples?\s+(?:are|remain|left|exist)\s+pending\b",
        r"\bno\s+counterexamples?\s+checks?\s+pending\b",
        r"\bno\s+counterexamples?\s+checks?\s+(?:are|remain|left|exist)\s+pending\b",
        r"\bcounterexamples?\s+pending\s*:\s*(?:none|zero|0)\b",
        r"\bcounterexample\s+pressure\s*:\s*(?:none|zero|0)\b",
        r"\bcounterexamples?\s+checks?\s*:\s*none\s+pending\b",
        r"\bcounterexamples?\s+(?:are\s+)?not\s+pending\b",
        r"\bcounterexamples?\s+ha(?:s|ve)\s+no\s+pending\s+checks?\b",
        r"\bno\s+counterexample\s+pressure\b",
        r"\bno\s+counterexamples?\s+(?:remain|exist|found|were\s+found|detected)\b",
        r"\bcounterexample\s+search\s+(?:found\s+)?(?:none|nothing)\b",
        r"\bcounterexamples?\s+(?:are\s+)?(?:cleared|settled)\b",
        r"\bno\s+theorem\s+(?:blockers?|applicability\s+blockers?)\b",
        r"\ball\s+theorem\s+(?:conditions|hypotheses)\s+"
        r"(?:are\s+)?(?:satisfied|checked|met)\b",
        r"\btheorem\s+applicability\s+(?:is\s+)?"
        r"(?:clear|settled|all\s+applicable|applicable)\b",
        r"\btheorem\s+(?:applies|is\s+applicable)\b",
        r"\bno\s+(?:reviewer\s+)?(?:blockers?|objections?|risks?)\b",
        r"\breviewers?\s+(?:approved|passed|found\s+no)\b",
        r"\ball\s+reviewers?\s+(?:passed|approved)\b",
        r"\b(?:not|no\s+longer)\s+stale\b",
        r"\bnot\s+pending\b",
        r"\bno\s+(?:pending|stale|late|unapplied)\s+"
        r"(?:updates?|signals?|events?|entries?)\b",
        r"\bupdate inbox\s*:\s*(?:none|empty|clear|cleared|resolved)\b",
        r"\bupdate inbox\s+(?:has|contains|shows)\s+no\s+"
        r"(?:pending|stale|late|unapplied)?\s*(?:entries?|updates?|events?|signals?)\b",
        r"\bno\s+(?:pending|stale|late|unapplied)\s+update inbox\s+"
        r"(?:entries?|updates?|events?|signals?)\b",
        r"\ball\s+update inbox\s+(?:entries?|updates?|events?|signals?)\s+"
        r"(?:are\s+)?(?:resolved|cleared|applied)\b",
        r"\bupdate inbox\s+(?:is\s+)?(?:current|up[- ]to[- ]date|clear|cleared|resolved|empty)\b",
        r"\bno\s+(?:late|pending|unapplied)\s+advisory\s+signals?\s+remain\b",
        r"\b(?:late|pending|unapplied)\s+advisory\s+signals?\s+"
        r"(?:are\s+)?(?:resolved|cleared|applied)\b",
        r"\b(?:updates?|signals?|events?)\s+(?:are\s+)?(?:cleared|resolved)\b",
    )
)


def _sanitize_legacy_final_body(text: str) -> str:
    pieces = [
        piece.strip()
        for piece in re.split(r"(?<=[.!?。；;])\s+|\n+", text)
        if piece.strip()
    ]
    safe: list[str] = []
    for piece in pieces:
        cleaned = _display_text(piece).strip()
        if any(pattern.search(cleaned) for pattern in _LEGACY_FINAL_BODY_UNSAFE_PATTERNS):
            cleaned = "[withheld legacy final-report phrase]"
        safe.append(cleaned)
    return " ".join(safe).strip() or (
        "Mock critique: legacy final report body omitted; see current blocker disclosures below."
    )


def refresh_final_report_disclosures(run_dir: Path) -> None:
    from .io import read_yaml, write_text

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = manifest.get("artifacts", {}).get("final_report")
    if not rel:
        return
    path = run_dir / rel
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    base = _sanitize_legacy_final_body(
        text.split("\n\nOpen blockers / pending checks:", 1)[0].rstrip()
    )
    write_text(path, base + final_report_disclosure_block(collect_surface_warnings_from_run(run_dir)))
