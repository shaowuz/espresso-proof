from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from espresso_proof.kernel.proof_language import (
    is_denied_occurrence,
    scrub_source_support_language,
)


SOURCE_GAPS = "source_gaps"
EXAMPLES_PENDING = "examples_pending"
COUNTEREXAMPLES_PENDING = "counterexamples_pending"
THEOREM_BLOCKERS = "theorem_blockers"
REVIEWER_BLOCKERS = "reviewer_blockers"
PROVER_REVIEWER_BLOCKERS = "prover_reviewer_blockers"
UPDATE_INBOX_PENDING = "update_inbox_pending"
AGENT_PROPOSED_CHECKS_PENDING = "agent_proposed_checks_pending"

# Single source of truth for the front-half placeholder wording: the producer
# (front_half/safety.py) and the supersession logic below must share it, or a
# wording edit silently breaks the supersession.
FRONT_HALF_REVIEW_PENDING_NOTE = (
    "Front-half review pending; real reviewers run in the Prover/Reviewers pass."
)


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _text_items(value: Any) -> list[str]:
    return [_display_text(item).rstrip(".") for item in (value or []) if str(item).strip()]


def _display_text(value: Any) -> str:
    text = scrub_source_support_language(str(value).strip())
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
    # A DENIED projection is the sentence this guard exists to allow: "a
    # literature candidate is not evidence for this step" says exactly what the
    # trust boundary says, and wiping it left the reader with nothing. Only an
    # undenied projection counts as attributing evidence to advisory context.
    projections = [
        match for match in status_projection.finditer(text)
        if not is_denied_occurrence(text, match.start())
    ]
    if any(term in text.lower() for term in advisory_context_terms) and projections:
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


def theorem_blocker_ids(
    theorem_applicability: dict[str, Any],
    *,
    used_source_ref_ids: set[str] | None = None,
) -> list[str]:
    blocked: list[str] = []
    if not theorem_applicability:
        return blocked
    usages = list(theorem_applicability.get("usages") or [])
    usages.extend(theorem_applicability.get("ledger_usages") or [])
    for usage in _items(usages):
        if used_source_ref_ids:
            source_ref_id = str(usage.get("source_ref_id") or "")
            if source_ref_id not in used_source_ref_ids:
                continue
        if usage.get("blocks_source_backed") or usage.get("applicability_status") in {
            "blocked",
            "misapplied",
            "unclear",
            "needs_human_assumption",
            "wrong_source",
        }:
            blocked.append(str(usage.get("proof_step_id") or "unnamed theorem usage"))
    if theorem_applicability.get("overall_status") in {
        "blocked",
        "misapplied",
        "unclear",
        "needs_human_assumption",
        "wrong_source",
    } and not blocked and not used_source_ref_ids:
        blocked.append(str(theorem_applicability.get("overall_status")))
    return list(dict.fromkeys(blocked))


def _reviewer_row_pieces(review: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    for reviewer in _items(review.get("reviewers")):
        verdict = reviewer.get("verdict")
        if verdict == "fail":
            pieces.append(f"{reviewer.get('reviewer_id')}: blocking objection")
        elif verdict == "unclear":
            pieces.append(f"{reviewer.get('reviewer_id')}: needs more review")
    return pieces


def _review_field_pieces(review: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
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
    return pieces


def reviewer_blocker_summary(review: dict[str, Any]) -> str:
    return "; ".join(_reviewer_row_pieces(review) + _review_field_pieces(review))


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
    has_source_backed_ref = any(
        source_ref.get("status") == "source_backed"
        for source_ref in _items(source_grounding.get("source_refs"))
    )
    gaps = [
        gap
        for gap in _text_items(grade.get("source_gaps"))
        if not (has_source_backed_ref and _is_stale_no_source_gap(gap))
    ]
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


def _is_stale_no_source_gap(text: str) -> bool:
    lowered = text.lower()
    stale_markers = (
        "no source is cited",
        "no source cited",
        "no exact locator",
        "no hypothesis match",
        "no source support",
        "no source-backed",
        "no source backed",
    )
    return any(marker in lowered for marker in stale_markers)


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
    line_numbered_proof: dict[str, Any] | None = None,
    agent_consensus_review: dict[str, Any] | None = None,
    prover_loop: dict[str, Any] | None = None,
    pending_agent_checks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    prover_attempts = _items((prover_loop or {}).get("attempts"))
    if prover_attempts:
        # Real reviewers have run: the front-half placeholder review must not
        # resurrect as a live warning. Supersede by IDENTITY with the shared
        # constant (no prose heuristics), on a COPY of the review.
        review = dict(review)
        review["downgrade_reasons"] = [
            reason
            for reason in (review.get("downgrade_reasons") or [])
            if str(reason).strip() != FRONT_HALF_REVIEW_PENDING_NOTE
        ]
        if str(review.get("strongest_objection") or "").strip() == FRONT_HALF_REVIEW_PENDING_NOTE:
            review["strongest_objection"] = "none"
        # Same supersession for the scaffold's reviewer ROWS. A row with verdict
        # "unclear" and zero objections is an unrun reviewer, not an open
        # blocker; once prover-pass reviewers exist, listing all three as
        # "needs more review" and then admitting in the same sentence that they
        # have been superseded says nothing a reader can act on, while the very
        # next warning reports the real prover-pass blockers.
        review["reviewers"] = [
            reviewer
            for reviewer in _items(review.get("reviewers"))
            if not (
                str(reviewer.get("verdict") or "") == "unclear"
                and not _items(reviewer.get("objections"))
            )
        ]
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
    theorem_blockers = theorem_blocker_ids(
        theorem_applicability or {},
        used_source_ref_ids=_proof_theorem_ref_ids(line_numbered_proof or {}),
    )
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
    # Two different facts under one heading: what an individual reviewer said,
    # and the review artifact's own aggregate fields. When no reviewer row
    # survives, the heading names what is actually left — a line that opened
    # "Reviewer blockers still open: downgrade reasons: ..." attributed the
    # artifact's summary to a reviewer who never said it.
    row_pieces = _reviewer_row_pieces(review)
    field_pieces = _review_field_pieces(review)
    reviewer_summary = "; ".join(row_pieces + field_pieces)
    reviewer_title = (
        "Reviewer blockers still open" if row_pieces else "Review-level blockers still open"
    )
    if reviewer_summary:
        # An agent-granted consensus used to DELETE this disclosure. It is amber
        # by construction — three language models agreeing, never machine-checked
        # — so letting it erase the record of what reviewers objected to made the
        # weakest evidence in the system silence the strongest warning. It now
        # annotates instead: the reader sees both the objections and the fact
        # that a consensus was recorded over them.
        consensus_note = (
            " (an agent consensus was recorded over these; agent consensus is never "
            "machine-checked and does not resolve them)"
            if (agent_consensus_review or {}).get("consensus") is True
            else ""
        )
        # No placeholder suffix any more: an unrun scaffold row is filtered out
        # above, so whatever reaches here is a reviewer that actually objected.
        suffix = consensus_note
        warnings.append(
            {
                "category": REVIEWER_BLOCKERS,
                "title": reviewer_title,
                "text": f"{reviewer_title}: {reviewer_summary}.{suffix}",
                "ids": [reviewer_summary],
            }
        )
    if prover_attempts:
        latest = max(prover_attempts, key=lambda a: int(a.get("round_index") or 0))
        # A reviewer whose call failed did not review. Saying so is the whole
        # point of the no_reply verdict: the round looked fully reviewed while
        # one of three reviewers had produced nothing.
        silent = [
            str(pointer.get("verifier_id") or "?")
            for pointer in _items(latest.get("reviewer_objections"))
            if str(pointer.get("verdict") or "") == "no_reply"
        ]
        if silent:
            warnings.append(
                {
                    "category": REVIEWER_BLOCKERS,
                    "title": "A reviewer did not reply",
                    "text": (
                        f"A reviewer did not reply: {', '.join(silent)} in round "
                        f"{latest.get('round_index')} returned no review (the model "
                        "call failed); that round was reviewed by fewer reviewers "
                        "than it appears."
                    ),
                    "ids": silent,
                }
            )
        open_blockers = [str(b) for b in (latest.get("blockers") or []) if str(b).strip()]
        if open_blockers:
            reviewer_count = len(latest.get("reviewer_ids") or [])
            warnings.append(
                {
                    "category": PROVER_REVIEWER_BLOCKERS,
                    "title": "Prover-pass reviewer blockers still open",
                    "text": (
                        f"Prover-pass reviewer blockers still open: {len(open_blockers)} "
                        f"open blocker(s) from round {latest.get('round_index')} "
                        f"({reviewer_count} reviewer(s)); see the prover loop record."
                    ),
                    "ids": open_blockers,
                }
            )
    pending_checks = [
        request
        for request in _items(pending_agent_checks)
        if str(request.get("id") or "").strip()
    ]
    if pending_checks:
        ids = [str(request["id"]).strip() for request in pending_checks]
        kinds = sorted({str(request.get("kind") or "unspecified") for request in pending_checks})
        warnings.append(
            {
                "category": AGENT_PROPOSED_CHECKS_PENDING,
                "title": "Agent-proposed machine checks not yet harvested",
                "text": (
                    f"Agent-proposed machine checks not yet harvested: {len(ids)} request(s) "
                    f"({', '.join(kinds)}) recorded by the prover/reviewer pass are absent from "
                    "this run's scoped-verification requests, so no deterministic verifier has "
                    "been run for them and they carry no evidence: "
                    + ", ".join(ids[:6])
                    + "."
                ),
                "ids": ids,
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


def _proof_theorem_ref_ids(line_numbered_proof: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for line in _items(line_numbered_proof.get("lines")):
        refs.update(str(ref) for ref in (line.get("source_refs") or []) if str(ref).strip())
        for dep in _items(line.get("dependencies")):
            if dep.get("type") == "theorem_ref" and str(dep.get("ref") or "").strip():
                refs.add(str(dep["ref"]))
    return refs



# --- blocker ranking honesty -------------------------------------------------
_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}



def latest_prover_round_reviews(run_dir: Path) -> dict[str, Any]:
    """The reviewer record of the LATEST prover round, for display ranking.

    Rounds appended by the loop live in prover_rounds/round_*.yaml; the initial
    pass lives in llm_agent_run.yaml. Ranking the initial pass while round 3 is
    the live state would surface stale objections, so prefer the
    highest-numbered round file and fall back to the initial record. Read-only
    and fail-soft: unreadable files yield {}.
    """

    from espresso_proof.kernel.io import read_yaml

    run_dir = Path(run_dir)
    rounds_dir = run_dir / "prover_rounds"
    candidates = sorted(rounds_dir.glob("round_*.yaml")) if rounds_dir.is_dir() else []
    for path in reversed(candidates):
        try:
            doc = read_yaml(path) or {}
        except Exception:
            continue
        if doc.get("verifier_reviews"):
            return doc
    initial = run_dir / "llm_agent_run.yaml"
    if initial.exists():
        try:
            return read_yaml(initial) or {}
        except Exception:
            return {}
    return {}

def pending_agent_verification_requests(run_dir: Path) -> list[dict[str, Any]]:
    """Agent-proposed verification requests that no deterministic check has seen.

    The general channel lets any prover/reviewer propose a machine check, but the
    stage that harvests those proposals into ``scoped_verification_requests.yaml``
    runs BEFORE the prover/reviewer pass, and its guard stops once the run reaches
    the human gate. So on a first pass a request exists only inside
    ``llm_agent_run.yaml`` / ``prover_rounds/*``: real, recorded, and unexecuted.
    Observed in a live run — a reviewer asked for a finite-group lookup and the
    surface said nothing about it.

    Reporting the difference keeps the omission visible instead of implying the
    reviewers asked for nothing. Read-only; never claims a request will run.
    """

    from espresso_proof.kernel.io import read_yaml

    run_dir = Path(run_dir)
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml") or {}
    except Exception:
        manifest = {}
    optional = manifest.get("optional_artifacts") or {}

    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            doc = read_yaml(path)
        except Exception:
            return {}
        # A well-formed YAML file whose top level is not a mapping is still
        # unreadable for this purpose; treating it as one raises later.
        return doc if isinstance(doc, dict) else {}

    harvested: set[str] = set()
    requests_rel = optional.get("scoped_verification_requests") or "scoped_verification_requests.yaml"
    for request in _items(_load(run_dir / requests_rel).get("requests")):
        harvested.add(str(request.get("id") or "").strip())

    sources = [run_dir / (optional.get("llm_agent_run") or "llm_agent_run.yaml")]
    rounds_dir = run_dir / "prover_rounds"
    if rounds_dir.is_dir():
        sources.extend(sorted(rounds_dir.glob("round_*.yaml")))

    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sources:
        doc = _load(path)
        records = [doc.get("prover_attempt") or {}] + list(_items(doc.get("verifier_reviews")))
        for record in records:
            if not isinstance(record, dict):
                continue
            for request in _items(record.get("verification_requests")):
                request_id = str(request.get("id") or "").strip()
                if not request_id or request_id in harvested or request_id in seen:
                    continue
                seen.add(request_id)
                pending.append(request)
    return pending


def rank_scoped_objections(llm_agent_run: dict[str, Any] | None) -> dict[str, Any]:
    """Report whether the reviewers actually ranked their objections.

    Deterministic and display-only: it reads recorded severities and never
    assigns one. When every objection carries the same severity the reviewers
    have ranked nothing, and saying so is more useful than presenting the
    schema default as if it were a judgement — that is the state a real run
    produced (19 objections, all P2, every suggested_next_action empty).
    """

    objections: list[dict[str, Any]] = []
    for review in _items((llm_agent_run or {}).get("verifier_reviews")):
        for obj in _items(review.get("scoped_objections")):
            if isinstance(obj, dict):
                objections.append(obj)
    if not objections:
        return {"total": 0, "differentiated": False, "levels": {}, "top": []}

    levels: dict[str, int] = {}
    for obj in objections:
        sev = str(obj.get("severity") or "P2")
        levels[sev] = levels.get(sev, 0) + 1
    differentiated = len(levels) > 1

    top: list[dict[str, Any]] = []
    if differentiated:
        blocking = [o for o in objections if str(o.get("severity")) in ("P0", "P1")]
        blocking.sort(key=lambda o: _SEVERITY_ORDER.get(str(o.get("severity")), 9))
        top = blocking[:3]
    return {
        "total": len(objections),
        "differentiated": differentiated,
        "levels": levels,
        "top": top,
        # An action the reviewer named, not one this function invented.
        "actionable": sum(1 for o in objections if str(o.get("suggested_next_action") or "").strip()),
    }


def render_blocker_ranking(ranking: dict[str, Any]) -> list[str]:
    """Lines for the memo. Says 'unranked' rather than faking an order."""

    total = ranking.get("total") or 0
    if not total:
        return []
    if not ranking.get("differentiated"):
        only = next(iter(ranking.get("levels") or {"P2": 0}))
        return [
            f"Blocker ranking: none. All {total} reviewer objections carry the same "
            f"severity ({only}), so no next action has been prioritised — the order "
            "below is arbitrary."
        ]
    lines = ["Blocker ranking (reviewer-assigned severity):"]
    for obj in ranking.get("top") or []:
        sev = obj.get("severity")
        # Scrubbed like the objection text beside it: this is agent free text
        # reaching the user, and printing it raw let proof-complete language and
        # markup through on the one line the ranking exists to highlight.
        action = _display_text(str(obj.get("suggested_next_action") or "")).strip()
        text = _display_text(obj.get("text"))[:160]
        lines.append(f"  [{sev}] {text}")
        if action:
            lines.append(f"        next: {action[:160]}")
    if not ranking.get("top"):
        lines.append("  (no P0/P1 objection: nothing is blocking the current round)")
    return lines


def render_warning_lines(warnings: list[dict[str, Any]]) -> list[str]:
    """Render one line per warning, WITHOUT re-running the advisory-projection
    guard over the joined text.

    That guard asks whether a sentence attributes evidence to advisory context,
    and its verdict is only meaningful sentence by sentence. Applied to a line
    that joins six source gaps, one gap's "candidate source needed" and a
    different gap's "no step is reference-supported" combined into a match and
    the whole disclosure was replaced by "[withheld advisory status phrase]" —
    four honest gap descriptions erased by a pair of words that never appeared
    together. Every constituent item was already scrubbed where it was built,
    so the remaining job here is only proof-language.
    """

    return [
        scrub_source_support_language(str(warning.get("text") or "")).strip()
        for warning in warnings
        if warning.get("text")
    ]


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
    # Compare against what the renderer WRITES. The two used different text
    # processing for a moment and every run failed validation for "missing"
    # disclosures that were present verbatim: whatever render_warning_lines
    # produces is the only thing the report can be expected to contain.
    rendered = {
        str(warning.get("category") or ""): line
        for warning, line in zip(warnings, render_warning_lines(warnings), strict=False)
    }
    for warning in warnings:
        category = str(warning.get("category") or "")
        required = " ".join(rendered.get(category, "").lower().split())
        if required and required not in lowered:
            missing.append(category)
    return missing


def collect_surface_warnings_from_run(run_dir: Path) -> list[dict[str, Any]]:
    from espresso_proof.kernel.io import read_yaml

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict[str, Any]:
        rel = artifacts.get(key)
        path = run_dir / rel if rel else None
        if not path or not path.exists():
            return {}
        if path.is_dir() and key in {"examples", "counterexamples"}:
            from espresso_proof.kernel.research_bank import (
                load_counterexamples_artifact,
                load_examples_artifact,
            )

            loader = (
                load_examples_artifact
                if key == "examples"
                else load_counterexamples_artifact
            )
            return loader(path).model_dump(mode="json")
        return read_yaml(path)

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
        line_numbered_proof=optional_artifact("line_numbered_proof"),
        agent_consensus_review=optional_artifact("agent_consensus_review"),
        prover_loop=optional_artifact("prover_loop"),
        pending_agent_checks=pending_agent_verification_requests(run_dir),
    )


def run_output_block(run_dir: Path) -> str:
    """What this run actually produced, in the same honest register as the gaps.

    Observed on a live run: two rounds executed, a correct discrete-telescoping
    route was selected, three reviewers raised 28 substantive points — and the
    whole report was eight lines of what was missing. A reader could not tell
    whether the system had produced anything at all.

    Every line here is copied from a validated artifact and carries no status:
    it says which route was selected and what the prover attempted, never that
    any of it is proved, sourced, or checked. Rebuilt on each refresh, so it
    cannot go stale against the artifacts it summarizes.
    """

    from espresso_proof.kernel.io import read_yaml

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = dict(manifest.get("artifacts") or {})
    optional = dict(manifest.get("optional_artifacts") or {})

    def load(rel: str | None) -> dict[str, Any]:
        if not rel:
            return {}
        path = run_dir / rel
        return read_yaml(path) if path.is_file() else {}

    def load_counterexamples(rel: str | None) -> dict[str, Any]:
        """Counterexamples may be a single file OR a materialized bank
        directory; the bank loader handles both, and read_yaml on a directory
        raises."""

        if not rel:
            return {}
        path = run_dir / rel
        if not path.exists():
            return {}
        try:
            from espresso_proof.kernel.research_bank import load_counterexamples_artifact

            return load_counterexamples_artifact(path).model_dump(mode="json")
        except Exception:  # noqa: BLE001 — a display block never breaks a run
            return read_yaml(path) if path.is_file() else {}

    brief = load(artifacts.get("strategy_brief"))
    loop = load(optional.get("prover_loop") or "prover_loop.yaml")
    proof = load(optional.get("line_numbered_proof"))
    counterexamples = load_counterexamples(artifacts.get("counterexamples"))

    lines: list[str] = []
    routes = _items(brief.get("top_strategies"))
    selected = next(
        (route for route in routes if route.get("selected")),
        None,
    ) or next(
        (
            route
            for route in routes
            if route.get("id") == loop.get("selected_strategy_id")
        ),
        None,
    )
    if selected:
        lines.append(
            "Route under work: " + _display_text(selected.get("title")).rstrip(".") + "."
        )
    if routes:
        lines.append(f"Routes proposed: {len(routes)}.")

    attempts = _items(loop.get("attempts"))
    if attempts:
        # A round can carry several attempts (one per route), so the two counts
        # are different facts and are reported as such.
        rounds = {
            attempt.get("round_index")
            for attempt in attempts
            if attempt.get("round_index") is not None
        }
        reviews = sum(len(_items(attempt.get("reviewer_objections"))) for attempt in attempts)
        lines.append(
            f"Prover rounds run: {len(rounds) or len(attempts)} "
            f"({len(attempts)} route attempt(s)); reviewer passes recorded: {reviews}. "
            "Each attempt and the objections against it are in the prover loop record."
        )
    # Counterexample pressure, said in words a reader recognizes. A live run on
    # a FALSE claim proposed S_3 and Q_8 — both correct — selected a route
    # titled "Counterexample verification via S_3", and never told the reader
    # their claim looked false: the only mention was under "Counterexample
    # checks pending/unavailable ... no-counterexample pressure is not
    # established", which describes missing support FOR the claim and reads
    # backwards. The conditional is what keeps this honest: whether a candidate
    # is admissible is the kernel's decision, not this sentence's.
    candidates = _items(counterexamples.get("tests"))
    if candidates:
        ids = ", ".join(str(test.get("id")) for test in candidates[:6] if test.get("id"))
        # Wording that survives being quoted out of context. An earlier draft
        # ended "...the claim is false", which reads as a verdict the moment the
        # conditional in front of it is dropped — and the memory boundary, which
        # sees clauses rather than sentences, refused to store it and rolled a
        # whole front half back.
        lines.append(
            f"Counterexample pressure: {len(candidates)} candidate(s) proposed and not "
            f"yet checked ({ids}). Each one is a proposed refutation of the claim; "
            "whether any is admissible against the locked hypotheses is undecided here."
        )
    if proof.get("lines"):
        lines.append(
            f"A line-numbered proof object exists with {len(_items(proof.get('lines')))} line(s); "
            "its status is whatever the kernel recorded for it, and it is not certified here."
        )
    if not lines:
        return ""
    body = "\n".join(f"- {scrub_source_support_language(line)}" for line in lines)
    return "\n\nWhat this run produced:\n" + body + "\n"


# What would actually close each kind of blocker. Every run so far ended with
# tens of open blockers and no way to tell a run that got far from one that got
# nowhere; grouping them by the ACTION that would close them is the difference
# between a list and a next step.
_CLOSED_BY = {
    SOURCE_GAPS: "an exact source locator (author, year, theorem/page)",
    THEOREM_BLOCKERS: "an exact source locator (author, year, theorem/page)",
    EXAMPLES_PENDING: "a machine check the kernel can run",
    COUNTEREXAMPLES_PENDING: "a machine check the kernel can run",
    AGENT_PROPOSED_CHECKS_PENDING: "a machine check the kernel can run",
    REVIEWER_BLOCKERS: "a human decision",
    PROVER_REVIEWER_BLOCKERS: "a human decision",
    UPDATE_INBOX_PENDING: "a human decision",
}
# Cheapest first: a machine check costs nothing but compute, a locator costs
# reading, a human decision costs the one resource the system cannot spend.
_RESIDUAL_ORDER = [
    "a machine check the kernel can run",
    "an exact source locator (author, year, theorem/page)",
    "a human decision",
]


def residual_block(warnings: list[dict[str, Any]]) -> str:
    """The open blockers restated by what would close them.

    Display only: it introduces no status and invents no blocker — every count
    comes from a warning already listed below it.
    """

    by_action: dict[str, int] = {}
    for warning in warnings:
        action = _CLOSED_BY.get(str(warning.get("category") or ""))
        if not action:
            continue
        ids = [str(item) for item in (warning.get("ids") or []) if str(item).strip()]
        by_action[action] = by_action.get(action, 0) + (len(ids) or 1)
    if not by_action:
        return ""
    lines = [
        f"- {count} item(s) waiting on {action}."
        for action in _RESIDUAL_ORDER
        if (count := by_action.get(action))
    ]
    cheapest = next((action for action in _RESIDUAL_ORDER if action in by_action), "")
    if cheapest:
        lines.append(f"- Cheapest next step: supply {cheapest}.")
    return "\n\nWhat would close what is open:\n" + "\n".join(lines) + "\n"


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
    from espresso_proof.kernel.io import read_yaml, write_text

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = manifest.get("artifacts", {}).get("final_report")
    if not rel:
        return
    path = run_dir / rel
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    consensus_block = ""
    consensus_match = re.search(r"\n\nAgent-consensus mock check:.*\Z", text, re.DOTALL)
    if consensus_match:
        consensus_block = consensus_match.group(0).rstrip() + "\n"
        text = text[: consensus_match.start()]
    base = _sanitize_legacy_final_body(
        text.split("\n\nWhat this run produced:", 1)[0]
        .split("\n\nWhat would close what is open:", 1)[0]
        .split("\n\nOpen blockers / pending checks:", 1)[0]
        .rstrip()
    )
    warnings = collect_surface_warnings_from_run(run_dir)
    write_text(
        path,
        base
        + run_output_block(run_dir)
        + final_report_disclosure_block(warnings)
        + residual_block(warnings)
        + consensus_block,
    )
