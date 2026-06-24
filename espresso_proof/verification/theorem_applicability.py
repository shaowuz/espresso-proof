from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import LiteratureReviewAgentReport, SourceGroundingReport, TheoremApplicabilityReport


def audit_theorem_applicability(run_dir: Path) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    optional = manifest.get("optional_artifacts", {})
    source_grounding = SourceGroundingReport.model_validate(
        read_yaml(run_dir / artifacts["source_grounding"])
    )
    literature_report = _load_literature_report(run_dir, optional)
    # Goal 37 de-circularization: the audit keeps its OWN condition ledger,
    # recomputed from the primary inputs (claim hypotheses vs cited statement),
    # so a stored hypothesis_match=True can never become satisfied conditions
    # by itself in the fallback path.
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    claim_hypotheses = claim.get("hypotheses", [])
    usages = []
    for step in source_grounding.steps:
        usages.append(
            _usage_for_step(step, source_grounding, literature_report, claim_hypotheses)
        )
    # Goal 57A slice-2: in ADDITION to the grounding-derived usages, read the
    # optional theorem condition ledger and emit one DEFAULT-DENY usage per
    # ledger entry. These never earn source_backed and never flip overall to
    # all_applicable unless a condition is explicitly matched with an exact
    # locator. Absent/empty ledger => no ledger usages => behaviour unchanged.
    ledger_usages = _ledger_usages(run_dir, optional)
    overall = _overall_status(usages, ledger_usages)
    report = {
        "schema_version": 1,
        "claim_id": source_grounding.claim_id,
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_grounding_artifact": artifacts["source_grounding"],
        # Goal 41: bind this audit to the exact grounding bytes it audited; the
        # validator rejects a stale audit if the grounding changes afterwards.
        "source_grounding_sha256": hashlib.sha256(
            (run_dir / artifacts["source_grounding"]).read_bytes()
        ).hexdigest(),
        "literature_review_agent_artifact": optional.get("literature_review_agent"),
        "usages": usages,
        "ledger_usages": ledger_usages,
        "overall_status": overall,
        "notes": [
            "Theorem Applicability Auditor records condition checks for theorem usage.",
            "It does not upgrade source_backed; it can only confirm or block theorem applicability.",
        ],
    }
    TheoremApplicabilityReport.model_validate(report)
    write_yaml(run_dir / "theorem_applicability.yaml", report)
    return report


# Goal 57A slice-2: map a ledger condition's match_status onto the existing
# applicability condition status vocabulary. DEFAULT-DENY: only an explicit
# ``matched`` is ever "satisfied"; the slice-1 default ``unknown`` is "not_checked"
# (a bad/blocking status), and ``unmatched`` is "mismatched" (misapplied).
_LEDGER_CONDITION_STATUS = {
    "matched": "satisfied",
    "unmatched": "mismatched",
    "unknown": "not_checked",
}


def _ledger_usages(run_dir: Path, optional: dict) -> list[dict]:
    """Build DEFAULT-DENY theorem-applicability usages from the optional theorem
    condition ledger (reviewer ``source_requests``).

    SAFETY (Goal 57A): a ledger-derived usage NEVER earns ``source_backed`` and, in
    THIS slice, is UNCONDITIONALLY non-applicable (default-deny). The reason is
    structural, not data-dependent: ``_ledger_usage_for_entry`` hardcodes
    ``conclusion_status="unclear"``, and ``_applicability_status`` returns
    ``"unclear"`` whenever the conclusion is ``unclear`` regardless of the
    conditions. So even an all-``matched`` ledger entry can never reach
    ``applicable`` today, and the exact-locator gate below is therefore currently
    UNREACHABLE (dead-but-correct: it is the second, stricter line of defence).

    FUTURE SLICE: if a later slice sets ``conclusion_status`` from the ledger
    (rather than hardcoding ``unclear``), an all-``matched`` entry could reach
    ``applicable`` and the locator gate becomes LIVE — a matched-but-not-located
    entry would then still be default-denied to ``blocked``. That change MUST be
    re-reviewed, because it moves the applicability decision off the unconditional
    default-deny path. This audit does NO matching of its own; it only surfaces the
    ledger's recorded state.
    """

    name = optional.get("theorem_condition_ledger")
    if not name:
        return []
    ledger = read_yaml(run_dir / name)
    if not isinstance(ledger, dict):
        return []
    usages: list[dict] = []
    for entry in ledger.get("entries", []) or []:
        usages.append(_ledger_usage_for_entry(entry))
    return usages


def _ledger_usage_for_entry(entry: dict) -> dict:
    entry_id = str(entry.get("id", "ledger_entry"))
    locator_status = entry.get("locator_status", "needs_exact_locator")
    locator_value = entry.get("locator")
    locator_is_exact = locator_status == "exact" and bool(locator_value)
    locator = (
        {"kind": "theorem", "value": str(locator_value)}
        if locator_is_exact
        else {"kind": "unknown", "value": entry_id}
    )

    conditions: list[dict] = []
    for index, condition in enumerate(entry.get("conditions", []) or [], start=1):
        match_status = condition.get("match_status", "unknown")
        conditions.append(
            {
                "id": f"cond_{index:03d}",
                "text": str(condition.get("text", "")),
                "condition_type": _condition_type(str(condition.get("text", ""))),
                # DEFAULT-DENY mapping; unknown/unmatched are blocking statuses.
                "status": _LEDGER_CONDITION_STATUS.get(match_status, "not_checked"),
                "evidence": (
                    "Theorem condition ledger (advisory, reviewer source_request). "
                    f"match_status={match_status}; the audit does no matching itself "
                    "and earns no source_backed from this entry."
                ),
                "definition_locator": None,
            }
        )
    if not conditions:
        conditions.append(
            {
                "id": "cond_001",
                "text": "Hypotheses to check against the claim",
                "condition_type": "other",
                "status": "not_checked",
                "evidence": "Ledger entry carried no conditions; default-deny.",
                "definition_locator": None,
            }
        )

    # Reuse the existing applicability mapping. Because conclusion_status is
    # hardcoded "unclear", _applicability_status returns "unclear" for EVERY ledger
    # entry in this slice — so a ledger usage is UNCONDITIONALLY non-applicable
    # (default-deny), even when all conditions are matched. The locator gate just
    # below is consequently UNREACHABLE today; it stays as a stricter second
    # defence that only becomes live if a future slice sets conclusion_status from
    # the ledger (which must then be re-reviewed — see _ledger_usages docstring).
    applicability_status = _applicability_status(conditions, "unclear", "unverified")
    if applicability_status == "applicable" and not locator_is_exact:
        # needs-locator: a matched-but-not-located entry is still default-denied.
        # (Unreachable while conclusion_status is hardcoded "unclear", above.)
        applicability_status = "blocked"
    return {
        "proof_step_id": f"ledger:{entry_id}",
        "source_ref_id": "missing_source_ref",
        "theorem_id": None,
        "theorem_label": None,
        "theorem_statement": str(entry.get("requested_theorem", "")),
        "locator": locator,
        # NEVER source_backed: a ledger usage is unverified by construction.
        "source_status": "unverified",
        "conclusion_status": "unclear",
        "conditions": conditions,
        "applicability_status": applicability_status,
        # The usage model requires applicable => not blocks_source_backed; this
        # flag is internal to the usage shape and does NOT grant source_backed
        # (the validator only earns source_backed from source-grounding usages,
        # never from ledger_usages). Default-deny: blocks unless fully applicable.
        "blocks_source_backed": applicability_status != "applicable",
        "notes": [
            "Default-deny usage derived from the theorem condition ledger; advisory "
            "search-management bookkeeping, not source grounding. Never earns "
            "source_backed and never flips overall to all_applicable while "
            "unresolved.",
            f"locator_status={locator_status}.",
        ],
    }


def _load_literature_report(run_dir: Path, optional: dict) -> LiteratureReviewAgentReport | None:
    name = optional.get("literature_review_agent")
    if not name:
        return None
    return LiteratureReviewAgentReport.model_validate(read_yaml(run_dir / name))


def _usage_for_step(step, source_grounding: SourceGroundingReport, literature_report, claim_hypotheses):
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
        else (
            source_ref.statement_or_summary
            if source_ref is not None
            else step.needed_theorem
        )
    )
    if comparison is not None:
        conditions = _conditions_from_comparison(comparison)
        conclusion_status = _conclusion_status(comparison)
    else:
        conditions, independent_matches = _conditions_from_step(
            step, source_ref, claim_hypotheses
        )
        conclusion_status = _fallback_conclusion_status(step, independent_matches)
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


def _conditions_from_step(step, source_ref, claim_hypotheses) -> tuple[list[dict], object]:
    """Fallback condition ledger (no Literature Review Agent comparison).

    Goal 37 de-circularization: when the grounding recorded ``matches is True``,
    the audit does NOT convert that verdict into satisfied conditions. It
    recomputes hypothesis coverage from the primary inputs — the claim
    hypotheses against the cited statement (``statement_or_summary``) — using
    the same conservative checker the grounding itself uses. A hand-edited
    ``matches=True`` over a non-covering statement therefore fails closed.

    Returns ``(conditions, independent_matches)`` where ``independent_matches``
    is the audit's own verdict (True / "unclear" / False).
    """

    mismatches = list(step.hypothesis_match.mismatches)
    if step.hypothesis_match.matches is True:
        from espresso_proof.verification.source_verification import check_hypothesis_applicability

        statement = source_ref.statement_or_summary if source_ref is not None else ""
        recheck = check_hypothesis_applicability(
            statement,
            claim_hypotheses,
            [],
            required_statement=step.needed_theorem,
        )
        conditions: list[dict] = []
        index = 1
        for reason in recheck["matched"]:
            conditions.append(
                {
                    "id": f"cond_{index:03d}",
                    "text": reason,
                    "condition_type": _condition_type(reason),
                    "status": "satisfied",
                    "evidence": (
                        "Independent audit recheck: the cited statement covers this "
                        "claim hypothesis."
                    ),
                    "definition_locator": None,
                }
            )
            index += 1
        for mismatch in recheck["mismatches"]:
            conditions.append(
                {
                    "id": f"cond_{index:03d}",
                    "text": mismatch,
                    "condition_type": _condition_type(mismatch),
                    "status": "unclear",
                    "evidence": (
                        "Independent audit recheck could NOT confirm this hypothesis "
                        "in the cited statement, despite the grounding's stored "
                        "hypothesis_match; the audit does not trust prior grounding."
                    ),
                    "definition_locator": None,
                }
            )
            index += 1
        if not conditions:
            conditions.append(
                {
                    "id": "cond_001",
                    "text": "No explicit claim hypotheses required checking.",
                    "condition_type": "other",
                    "status": "satisfied" if recheck["matches"] is True else "not_checked",
                    "evidence": "Independent audit recheck found no explicit/inferred claim hypotheses.",
                    "definition_locator": None,
                }
            )
        return conditions, recheck["matches"]
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
    ], step.hypothesis_match.matches


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


def _fallback_conclusion_status(step, independent_matches) -> str:
    # Goal 37: "compatible" requires the audit's OWN recheck to confirm the
    # hypotheses — the stored source_backed status alone is not trusted.
    if step.status.value == "source_backed" and independent_matches is True:
        return "compatible"
    return "unclear"


def _applicability_status(conditions: list[dict], conclusion_status: str, source_status: str) -> str:
    statuses = {condition["status"] for condition in conditions}
    if "mismatched" in statuses or conclusion_status == "contradicts" or source_status == "misapplied":
        return "misapplied"
    if source_status != "source_backed":
        return "blocked"
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


def _overall_status(usages: list[dict], ledger_usages: list[dict] | None = None) -> str:
    statuses = {usage["applicability_status"] for usage in usages}
    # Goal 57A: ledger usages are default-deny; their applicability status (which
    # is never "applicable" while an obligation is unresolved) participates in the
    # overall verdict so a present-but-unresolved ledger can never read
    # all_applicable.
    statuses |= {usage["applicability_status"] for usage in ledger_usages or []}
    if "wrong_source" in statuses:
        return "wrong_source"
    if "misapplied" in statuses:
        return "misapplied"
    if "needs_human_assumption" in statuses:
        return "needs_human_assumption"
    if "blocked" in statuses:
        return "blocked"
    if "unclear" in statuses:
        return "unclear"
    return "all_applicable"


def _significant_terms(text: str) -> list[str]:
    stop = {"and", "for", "the", "this", "with", "that", "from", "source", "theorem"}
    return [token for token in text.split() if len(token) > 3 and token not in stop][:12]


_REVIEW_VERDICTS = {
    "applicable",
    "blocked",
    "needs_human_assumption",
    "wrong_source",
    "unclear",
}


def review_theorem_applicability_with_llm(
    run_dir: Path,
    *,
    provider: str = "stub",
    model: str = "stub-espresso-agent",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Run two LLM reviewers over theorem usages and fold their verdicts into
    ``theorem_applicability.yaml``.

    Safety boundary: this review never writes ``source_grounding.yaml`` and
    never changes source status. Even an ``applicable`` reviewer result remains
    only an applicability judgment over the cited theorem usage.
    """

    from espresso_proof.agents.llm_agents import build_client
    from espresso_proof.kernel.validators import validate_run

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    optional = manifest.setdefault("optional_artifacts", {})
    applicability_rel = optional.get("theorem_applicability") or "theorem_applicability.yaml"
    applicability_path = run_dir / applicability_rel
    review_path = run_dir / "theorem_applicability_review.yaml"
    final_report_rel = manifest.get("artifacts", {}).get("final_report")
    final_report_path = run_dir / final_report_rel if final_report_rel else None
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "had_applicability": applicability_path.exists(),
        "applicability": applicability_path.read_bytes() if applicability_path.exists() else None,
        "had_review": review_path.exists(),
        "review": review_path.read_bytes() if review_path.exists() else None,
        "had_final_report": bool(final_report_path and final_report_path.exists()),
        "final_report": (
            final_report_path.read_bytes()
            if final_report_path is not None and final_report_path.exists()
            else None
        ),
    }
    try:
        if (
            "theorem_applicability" not in optional
            or not (run_dir / optional["theorem_applicability"]).exists()
        ):
            audit_theorem_applicability(run_dir)
            manifest = read_yaml(manifest_path)
            optional = manifest.setdefault("optional_artifacts", {})
            optional["theorem_applicability"] = "theorem_applicability.yaml"
            write_yaml(manifest_path, manifest)

        applicability_path = run_dir / optional["theorem_applicability"]
        report = TheoremApplicabilityReport.model_validate(read_yaml(applicability_path)).model_dump(mode="json")
        claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
        client = client or build_client(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )
        review_records: list[dict] = []
        usage_updates: dict[str, dict] = {}
        for usage in report.get("usages", []):
            reviews = []
            for reviewer_id in ("source_match_reviewer", "hypothesis_reviewer"):
                raw = client.complete_json(
                    system=_applicability_reviewer_system_prompt(reviewer_id),
                    prompt=_applicability_reviewer_prompt(claim, usage),
                )
                review = _normalize_applicability_review(raw, reviewer_id, usage)
                reviews.append(review)
                review_records.append(review)
            usage_updates[usage["proof_step_id"]] = _merge_reviewer_verdicts(usage, reviews)

        for usage in report.get("usages", []):
            update = usage_updates.get(usage["proof_step_id"])
            if update:
                usage.update(update)
        report["overall_status"] = _overall_status(report.get("usages", []), report.get("ledger_usages", []))
        report["notes"] = list(report.get("notes", [])) + [
            "LLM theorem applicability reviewers ran source_match_reviewer and hypothesis_reviewer.",
            "Reviewer judgments do not upgrade source status; source-backed still requires source grounding validation.",
        ]
        TheoremApplicabilityReport.model_validate(report)
        write_yaml(applicability_path, report)
        review_artifact = {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": datetime.now(UTC).isoformat(),
            "reviewers": ["source_match_reviewer", "hypothesis_reviewer"],
            "reviews": review_records,
            "notes": [
                "LLM applicability review is advisory over theorem use.",
                "It cannot mark a source source-backed and cannot seal a proof.",
            ],
        }
        write_yaml(review_path, review_artifact)
        manifest = read_yaml(manifest_path)
        manifest.setdefault("optional_artifacts", {})["theorem_applicability_review"] = (
            "theorem_applicability_review.yaml"
        )
        write_yaml(manifest_path, manifest)
        from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures

        refresh_final_report_disclosures(run_dir)
        validate_run(run_dir)
        return review_artifact
    except Exception:
        manifest_path.write_bytes(snapshot["manifest"])
        if snapshot["had_applicability"]:
            applicability_path.write_bytes(snapshot["applicability"])
        elif applicability_path.exists():
            applicability_path.unlink()
        if snapshot["had_review"]:
            review_path.write_bytes(snapshot["review"])
        elif review_path.exists():
            review_path.unlink()
        if final_report_path is not None:
            if snapshot["had_final_report"]:
                final_report_path.write_bytes(snapshot["final_report"])
            elif final_report_path.exists():
                final_report_path.unlink()
        raise


def _applicability_reviewer_system_prompt(reviewer_id: str) -> str:
    if reviewer_id == "source_match_reviewer":
        return (
            "You are Espresso Proof's source-match reviewer. Check whether the cited "
            "theorem statement actually says the conclusion needed by the proof step. "
            "Return JSON only with verdict applicable|blocked|needs_human_assumption|"
            "wrong_source|unclear, conclusion_status, conditions, and notes. Never "
            "mark a source source_backed."
        )
    return (
        "You are Espresso Proof's hypothesis reviewer. Check base field, object class, "
        "finiteness/solvability/genericity, and extra theorem assumptions. Return JSON "
        "only with verdict applicable|blocked|needs_human_assumption|wrong_source|"
        "unclear, conclusion_status, conditions, and notes. Never mark a source source_backed."
    )


def _applicability_reviewer_prompt(claim: dict, usage: dict) -> str:
    return (
        "Locked claim:\n"
        f"{claim.get('normalized_statement')}\n\n"
        "Claim hypotheses:\n"
        f"{claim.get('hypotheses', [])}\n\n"
        "Theorem usage to review:\n"
        f"{usage}\n\n"
        "Return compact JSON."
    )


def _normalize_applicability_review(raw: object, reviewer_id: str, usage: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict") or raw.get("applicability_status") or "unclear")
    if verdict == "misapplied":
        verdict = "wrong_source"
    if verdict not in _REVIEW_VERDICTS:
        verdict = "unclear"
    conclusion_status = str(raw.get("conclusion_status") or usage.get("conclusion_status") or "unclear")
    if conclusion_status not in {"compatible", "weaker", "stronger", "orthogonal", "contradicts", "unclear"}:
        conclusion_status = "unclear"
    conditions = []
    for index, item in enumerate(raw.get("conditions") or [], start=1):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or ("satisfied" if verdict == "applicable" else "not_checked"))
        if status not in {"satisfied", "missing", "mismatched", "unclear", "not_checked"}:
            status = "not_checked"
        conditions.append(
            {
                "id": f"{reviewer_id}_cond_{index:03d}",
                "text": str(item.get("text") or item.get("condition") or "reviewer condition"),
                "condition_type": _condition_type(str(item.get("text") or "")),
                "status": status,
                "evidence": str(item.get("evidence") or item.get("reason") or "LLM reviewer judgment"),
                "definition_locator": None,
            }
        )
    return {
        "reviewer_id": reviewer_id,
        "proof_step_id": usage.get("proof_step_id"),
        "verdict": verdict,
        "conclusion_status": conclusion_status,
        "conditions": conditions,
        "notes": [str(x) for x in raw.get("notes", []) if str(x).strip()],
    }


def _merge_reviewer_verdicts(usage: dict, reviews: list[dict]) -> dict:
    verdicts = [review["verdict"] for review in reviews]
    if "wrong_source" in verdicts:
        status = "wrong_source"
    elif "needs_human_assumption" in verdicts:
        status = "needs_human_assumption"
    elif "blocked" in verdicts:
        status = "blocked"
    elif verdicts and all(v == "applicable" for v in verdicts):
        status = "applicable"
    else:
        status = "unclear"
    source_blocked = status == "applicable" and usage.get("source_status") != "source_backed"
    if source_blocked:
        status = "blocked"
    conditions = [
        condition
        for review in reviews
        for condition in review.get("conditions", [])
    ]
    if not conditions:
        conditions = list(usage.get("conditions") or [])
    if status == "applicable":
        conditions = [
            {**condition, "status": "satisfied"}
            for condition in (conditions or [{
                "id": "llm_review_cond_001",
                "text": "LLM reviewers found theorem statement and hypotheses applicable.",
                "condition_type": "other",
                "status": "satisfied",
                "evidence": "Both source_match_reviewer and hypothesis_reviewer returned applicable.",
                "definition_locator": None,
            }])
        ]
        conclusion_status = "compatible"
    elif status == "wrong_source":
        conditions = conditions or [{
            "id": "llm_review_wrong_source",
            "text": "The cited source does not support this proof step.",
            "condition_type": "other",
            "status": "mismatched",
            "evidence": "At least one theorem applicability reviewer returned wrong_source.",
            "definition_locator": None,
        }]
        conclusion_status = "contradicts" if usage.get("conclusion_status") == "contradicts" else "unclear"
    else:
        conditions = conditions or [{
            "id": "llm_review_blocker",
            "text": "The theorem usage still needs review or human assumption.",
            "condition_type": "other",
            "status": "not_checked",
            "evidence": f"Reviewer verdicts: {verdicts}",
            "definition_locator": None,
        }]
        conclusion_status = "unclear"
    notes = list(usage.get("notes", [])) + [
        "LLM reviewer verdicts: " + ", ".join(verdicts)
    ]
    if source_blocked:
        notes.append(
            "Applicability remains blocked because source_grounding has not earned source_backed for this theorem usage."
        )
    return {
        "conditions": conditions,
        "conclusion_status": conclusion_status,
        "applicability_status": status,
        "blocks_source_backed": status != "applicable",
        "notes": notes,
    }
