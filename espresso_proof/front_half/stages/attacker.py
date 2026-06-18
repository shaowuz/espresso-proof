"""Front-half stage: attacker (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.hypothesis_normalization import normalize_ambient_object_hypotheses
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.agents.llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.memory.memory import write_run_memory
from espresso_proof.kernel.models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    FrontHalfRunReport,
    HypothesisLedger,
    IdeaGrade,
    LiteratureReviewReport,
    Review,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from espresso_proof.kernel.strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from espresso_proof.kernel.validators import validate_run
from ..registry import register_stage
from ..safety import (
    _CANDIDATE_SOURCE_STATUSES,
    _EXPLORATION_MODES,
    _HYPOTHESIS_STATUSES,
    _IDEA_STATUSES,
    _LITERATURE_RELEVANCE,
    _LOCATOR_KINDS,
    _NONTRIVIALITY,
    _coerce_satisfies,
    _default_investigation_route,
    _neutralize_review_if_failed,
    _normalize_locator,
    _safe_lock_route,
)


_ATTACK_TARGETS = {"claim", "proof_step", "hypothesis", "analogy"}

_ATTACKER_SYSTEM = (
    "You are the Espresso Proof Example/Counterexample Attacker. Adversarially "
    "pressure the proposed examples and counterexamples and record which survive. "
    "CRITICAL: a proof-step attack does NOT refute the theorem; a dropped-"
    "hypothesis example does NOT refute the claim; and you may NOT mark any test "
    "as checked/survived/failed evidence — only verified computations/sources can "
    "do that. Classify each attack's target (claim / proof_step / hypothesis / "
    "analogy). Respond with ONLY a JSON object."
)

_ATTACKER_OUTPUT_SCHEMA = {
    "attacks": [
        {
            "target_id": "string (an existing example or counterexample id)",
            "attacks": "claim|proof_step|hypothesis|analogy",
            "survives": "true|false",
            "observation": "string",
            "next_action": "string",
        }
    ],
    "route_blockers": ["string"],
    "summary": "string",
}


def _normalize_attacks(
    raw_attacks: object, *, example_ids: set[str], counterexample_ids: set[str]
) -> list[dict]:
    items: list[dict] = []
    if not isinstance(raw_attacks, list):
        return items
    for index, raw in enumerate(raw_attacks, start=1):
        if not isinstance(raw, dict):
            continue
        observation = safe_text(raw.get("observation", "")).strip()
        if not observation or observation == "No usable agent text returned.":
            continue
        target_id = safe_text(raw.get("target_id", "")).strip()
        attack_target = raw.get("attacks")
        if attack_target not in _ATTACK_TARGETS:
            attack_target = "claim"
        uses_examples = [target_id] if target_id in example_ids else []
        uses_counterexamples = [target_id] if target_id in counterexample_ids else []
        survives = raw.get("survives")
        survives_text = "survives" if survives in (True, "true", "yes") else "unresolved"
        items.append(
            {
                "id": f"test_atk_{index:03d}",
                "description": f"Adversarial pressure on {target_id or 'a proposed item'}",
                "uses_examples": uses_examples,
                "uses_counterexamples": uses_counterexamples,
                # SAFETY: an attack observation is never checked evidence.
                "result": "not_tested",
                "evidence_status": "human_needed",
                "main_observation": (
                    f"{observation} [attack target: {attack_target}; status: {survives_text}; "
                    "a proof-step or dropped-hypothesis attack is not a refutation]"
                ),
                "next_action": safe_text(raw.get("next_action", ""))
                or "verify under the admissibility/evidence gate before drawing conclusions",
            }
        )
    return items


@register_stage("attacker")
def _attacker(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    existing = context.get("tests") or {}
    counterexamples = (context.get("counterexamples") or {}).get("tests", [])
    examples = (context.get("examples") or {}).get("examples", [])
    cex_ids = {c["id"] for c in counterexamples}
    ex_ids = {e["id"] for e in examples}

    payload = {
        "task": "attack_examples_and_counterexamples",
        "claim": {"normalized_statement": claim.get("normalized_statement", "")},
        "counterexamples": [
            {"id": c["id"], "explanation": c.get("explanation", "")} for c in counterexamples
        ],
        "examples": [{"id": e["id"], "name": e.get("name", "")} for e in examples],
        "required_json_schema": _ATTACKER_OUTPUT_SCHEMA,
        "rules": [
            "a proof-step attack is not a refutation",
            "a dropped-hypothesis example is not a refutation",
            "do not mark any test checked/survived/failed",
        ],
    }
    raw = client.complete_json(
        system=_ATTACKER_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    attack_items = _normalize_attacks(
        raw.get("attacks"), example_ids=ex_ids, counterexample_ids=cex_ids
    )
    # Preserve existing test ids; append attacker observations.
    combined = list(existing.get("tests", [])) + attack_items
    blockers = safe_list(raw.get("route_blockers"))
    summary = safe_text(raw.get("summary", "")) or existing.get(
        "summary", "Adversarial pressure recorded; nothing verified as a refutation."
    )
    if blockers:
        summary = f"{summary} Route blockers: {'; '.join(blockers)}"

    test_report = {
        "claim_id": claim_id,
        "target": existing.get("target", "claim"),
        "tests": combined,
        "summary": safe_text(summary),
    }
    TestReport.model_validate(test_report)
    write_yaml(run_dir / artifacts["tests"], test_report)
    return {
        "unsafe_language": [],
        "notes": [
            f"attacker appended {len(attack_items)} adversarial observation(s) "
            f"(all not_tested/human_needed; attacks never refute the claim)"
        ],
    }


# ---------------------------------------------------------------------------
# S7 — 06 Source Matching + 07 Theorem Applicability
# ---------------------------------------------------------------------------

