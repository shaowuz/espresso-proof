"""Strategy Brief service boundary.

This service constructs and persists the existing strategy artifacts without
changing their names or schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from espresso_proof.kernel.proof_language import safe_text
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    ScopedVerificationRequests,
    StrategyBrief,
    StrategySynthesis,
)
from espresso_proof.strategy.grading import StrategyGradingResult


@dataclass(frozen=True)
class StrategyArtifacts:
    """The existing strategy artifacts built from generated + graded routes."""

    synthesis: dict
    brief: dict


def build_strategy_artifacts(
    *,
    context: dict,
    raw: dict,
    grading: StrategyGradingResult,
) -> StrategyArtifacts:
    """Build strategy_synthesis.yaml and strategy_brief.yaml payloads."""

    manifest = context["manifest"]
    run_id = manifest["run_id"]
    claim_id = context["claim"]["id"]
    routes = grading.routes
    allowed_choices = [
        {"strategy_id": route["id"], "effort_options": [route["estimated_effort"]]}
        for route in routes
    ]
    allowed_choices.append({"strategy_id": "none", "effort_options": ["quick_test"]})

    synthesis = {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": routes,
        "discarded_routes": [],
        "synthesis_note": safe_text(raw.get("synthesis_note", ""))
        or "Candidate routes proposed and graded conservatively; scores are not proof status.",
    }
    brief = {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": routes,
        "also_considered": [],
        "question_for_mathematician": safe_text(raw.get("question_for_mathematician", ""))
        or "Which route should receive time, if any?",
        "allowed_human_choices": allowed_choices,
    }
    StrategySynthesis.model_validate(synthesis)
    StrategyBrief.model_validate(brief)
    return StrategyArtifacts(synthesis=synthesis, brief=brief)


def write_strategy_artifacts(
    run_dir: Path,
    *,
    context: dict,
    artifacts: StrategyArtifacts,
) -> None:
    """Persist strategy_synthesis.yaml and strategy_brief.yaml."""

    run_dir = Path(run_dir)
    manifest_artifacts = context["manifest"]["artifacts"]
    write_yaml(run_dir / manifest_artifacts["strategy_synthesis"], artifacts.synthesis)
    write_yaml(run_dir / manifest_artifacts["strategy_brief"], artifacts.brief)


def write_scoped_verification_requests(run_dir: Path, *, context: dict) -> int:
    """Persist deterministic scoped-verification requests proposed from strategy context."""

    from espresso_proof.verification.verification_request_probes import (
        propose_scoped_verification_requests,
    )

    run_dir = Path(run_dir)
    claim = context["claim"]
    claim_id = claim["id"]
    requests = propose_scoped_verification_requests(claim)
    if not requests:
        return 0
    requests_doc = {"claim_id": claim_id, "requests": requests}
    ScopedVerificationRequests.model_validate(requests_doc)
    write_yaml(run_dir / "scoped_verification_requests.yaml", requests_doc)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["scoped_verification_requests"] = (
        "scoped_verification_requests.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return len(requests)


__all__ = [
    "StrategyArtifacts",
    "build_strategy_artifacts",
    "write_scoped_verification_requests",
    "write_strategy_artifacts",
]
