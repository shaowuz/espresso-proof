"""LLM applicability review — an AGENT layer over the deterministic audit.

Moved out of ``espresso_proof.verification`` because that package's name is a
promise: everything in it is deterministic, offline machine checking. This
review builds an LLM client and asks two model reviewers for judgment, so it
belongs with the other model-running code. The safety contract is unchanged and
enforced by the kernel, not by location: the review folds ADVISORY verdicts into
``theorem_applicability.yaml``, never writes ``source_grounding.yaml``, and can
never mark anything source_backed.

``espresso_proof.verification.theorem_applicability`` re-exports
``review_theorem_applicability_with_llm`` so existing imports keep working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import TheoremApplicabilityReport
from espresso_proof.kernel.observability import usage_sink_for_run
from espresso_proof.verification.theorem_applicability import (
    _REVIEW_VERDICTS,
    _condition_type,
    _overall_status,
    audit_theorem_applicability,
)


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
            usage_sink=usage_sink_for_run(run_dir),
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
