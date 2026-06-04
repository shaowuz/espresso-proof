"""Concise Answer Agent: render a compact, user-facing research memo.

This is the first post-Kernel-Beta runtime slice (AGENT_AUTOMATION_ENGINEERING_PLAN
Step 1, contract docs/goals/agent_goals/22_CONCISE_ANSWER_AGENT_GOAL.md). It turns
already-validated run artifacts into the central product output: a short memo with
the grade, the strongest obstruction, source gaps, the top route, and the next
cheapest test -- without overstating the result.

Safety: this renderer only echoes fields from artifacts that already passed
``validate_run``. It never sets status, never invents facts, and never emits
proof-complete language on its own; truth labels (status enums) are surfaced
verbatim so source/evidence/proof separation stays visible.
"""

from __future__ import annotations

from pathlib import Path

from .descriptions import describe_grade, describe_proof_language
from .io import read_yaml


def build_concise_memo(run_dir: Path) -> str:
    """Render a compact memo string from a run's validated artifacts.

    Callers must run ``validate_run`` first; this function assumes the run is
    valid and only reads/echoes artifact fields via the run manifest.
    """
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    claim = artifact("locked_claim")
    grade = artifact("idea_grade")
    review = artifact("review")
    strategy_brief = artifact("strategy_brief")
    source_grounding = artifact("source_grounding")

    status = grade.get("status", "unknown")
    score = grade.get("score", "?")
    proof_status = grade.get("proof_language_status", "unknown")
    claim_text = (claim.get("normalized_statement") or "").strip()
    claim_state = "locked" if claim.get("locked") else "unlocked"
    objection = (review.get("strongest_objection") or "none recorded").strip()

    source_gaps = grade.get("source_gaps") or []
    source_statuses = sorted(
        {ref.get("status") for ref in source_grounding.get("source_refs", []) if ref.get("status")}
        | {step.get("status") for step in source_grounding.get("steps", []) if step.get("status")}
    )

    top = (strategy_brief.get("top_strategies") or [])
    next_test = (grade.get("next_cheapest_test") or "none recorded").strip()

    lines: list[str] = []
    lines.append(f"ESPRESSO MEMO — claim {grade.get('claim_id', claim.get('id', '?'))}")
    lines.append("")
    lines.append(f"Claim [{claim_state}]: {claim_text}")
    lines.append("")
    # 2-3 sentence summary in concrete English (no abstract status labels).
    assessment = describe_grade(status=status, proof_language_status=proof_status)
    lines.append(f"Summary: {assessment} Strongest obstruction: {objection}")
    if status == "unclear":
        lines.append("First action: lock/clarify the claim before testing it.")
    lines.append("")
    lines.append(f"Verdict: {assessment}")
    lines.append(
        f"Confidence score: {score} (internal heuristic; not a probability of truth)"
    )
    lines.append(f"Proof state: {describe_proof_language(proof_status)}")
    lines.append(
        "Source: gaps="
        + (", ".join(source_gaps) if source_gaps else "none")
        + "; statuses="
        + (", ".join(source_statuses) if source_statuses else "none")
    )
    # Attribution: a source_backed step is "grounded in <citation>" — a cited
    # result is never presented as Espresso's own proof.
    refs_by_id = {r.get("id"): r for r in source_grounding.get("source_refs", [])}
    for step in source_grounding.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        for ref_id in step.get("source_refs", []):
            ref = refs_by_id.get(ref_id)
            if not ref:
                continue
            loc = ref.get("locator", {})
            authors = ", ".join(ref.get("authors", [])) or "unknown author"
            lines.append(
                f"Grounded: {authors}, {ref.get('title', 'untitled')} "
                f"[{loc.get('kind')}:{loc.get('value')}] (cited support, not a proof)"
            )
    lines.append("")
    lines.append("Top routes:")
    if top:
        for index, route in enumerate(top[:3], start=1):
            lines.append(
                f"  {index}. {route.get('title')} "
                f"[score {route.get('score')}, effort {route.get('estimated_effort')}]"
            )
            if route.get("main_risk"):
                lines.append(f"     risk: {route.get('main_risk')}")
    else:
        lines.append("  none")
    lines.append("")
    lines.append(f"Next cheapest test: {next_test}")
    lines.append("")
    lines.append(
        "Note: artifact-backed memo; an assessment is not a proof. "
        "Full detail: `espresso inspect --run <dir>`."
    )
    return "\n".join(lines)
