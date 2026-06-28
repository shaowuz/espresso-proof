from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from espresso_proof.agents.llm_agents import load_agent_context
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.strategy.brief import build_strategy_artifacts, write_strategy_artifacts
from espresso_proof.strategy.generation import generate_strategy_candidates
from espresso_proof.strategy.grading import StrategyGradingResult, grade_strategy_candidates
from espresso_proof.strategy.time_allocation import (
    TIME_ALLOCATION_FILENAME,
    build_time_allocation_plan,
    write_time_allocation_after_strategy_grader,
)

CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"


class _FakeStrategyClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, prompt: str) -> dict:
        self.calls.append({"system": system, "prompt": prompt})
        return {
            "routes": [
                {
                    "title": "Use the center quotient route",
                    "why_this_route": "The class equation suggests a standard route.",
                    "main_risk": "The route still needs exact lemma support.",
                    "estimated_effort": "deep_exploration",
                    "next_loop_plan": "Pin the center lemma and test the quotient step.",
                }
            ],
            "question_for_mathematician": "Spend time on the center route?",
            "synthesis_note": "Candidate route only.",
        }


def _run(tmp_path: Path) -> Path:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    return run_mock_critique("strategy_services", claim_path, tmp_path)


def _fingerprint(run_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(run_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_strategy_generator_is_pure_and_does_not_start_allocation(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    before = _fingerprint(run_dir)
    client = _FakeStrategyClient()

    raw = generate_strategy_candidates(client=client, context=context)

    assert raw["routes"][0]["title"] == "Use the center quotient route"
    assert client.calls
    assert "Strategy Generator" in client.calls[0]["system"]
    assert "generator + grader + brief" not in client.calls[0]["system"]
    prompt = json.loads(client.calls[0]["prompt"])
    assert prompt["task"] == "generate_candidate_strategies"
    rendered_prompt = json.dumps(prompt, sort_keys=True).lower()
    assert "generate_and_grade" not in rendered_prompt
    assert "strategy_brief" not in rendered_prompt
    assert "time_allocation" not in rendered_prompt
    assert "score" not in json.dumps(prompt["required_json_schema"]).lower()
    assert _fingerprint(run_dir) == before
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "time_allocation" not in manifest.get("optional_artifacts", {})


def test_strategy_grader_is_pure_and_builds_kernel_shaped_routes(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    raw = generate_strategy_candidates(client=_FakeStrategyClient(), context=context)
    before = _fingerprint(run_dir)

    grading = grade_strategy_candidates(raw=raw, context=context)

    assert grading.trigger_stage == "strategy_grader"
    assert grading.idea_status == "promising"
    assert grading.routes[0]["title"] == "Use the center quotient route"
    assert grading.routes[0]["estimated_effort"] == "normal_pass"
    assert grading.routes[0]["source_status"] == "candidate"
    assert _fingerprint(run_dir) == before
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()


def test_strategy_brief_write_does_not_start_time_allocation(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    raw = generate_strategy_candidates(client=_FakeStrategyClient(), context=context)
    grading = grade_strategy_candidates(raw=raw, context=context)
    artifacts = build_strategy_artifacts(context=context, raw=raw, grading=grading)

    write_strategy_artifacts(run_dir, context=context, artifacts=artifacts)

    validate_run(run_dir)
    assert read_yaml(run_dir / "strategy_synthesis.yaml")["routes"] == grading.routes
    assert read_yaml(run_dir / "strategy_brief.yaml")["top_strategies"] == grading.routes
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()


def test_generator_only_artifact_cannot_start_allocation(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    write_yaml(
        run_dir / manifest["artifacts"]["strategy_synthesis"],
        {
            "claim_id": "claim_001",
            "synthesized_from": ["strategy_generator"],
            "routes": [{"id": "raw_route_001", "title": "Unscored route"}],
            "synthesis_note": "Generator-only output with no grader fields.",
        },
    )
    (run_dir / manifest["artifacts"]["strategy_brief"]).unlink()

    assert build_time_allocation_plan(run_dir) is None
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()


def test_time_allocation_service_requires_strategy_grader_trigger(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    raw = generate_strategy_candidates(client=_FakeStrategyClient(), context=context)
    grading = grade_strategy_candidates(raw=raw, context=context)
    artifacts = build_strategy_artifacts(context=context, raw=raw, grading=grading)
    write_strategy_artifacts(run_dir, context=context, artifacts=artifacts)
    bad_trigger = StrategyGradingResult(
        routes=grading.routes,
        idea_status=grading.idea_status,
        trigger_stage="strategy_generator",  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="strategy_grader"):
        write_time_allocation_after_strategy_grader(run_dir, grading=bad_trigger)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "time_allocation" not in manifest.get("optional_artifacts", {})


def test_time_allocation_service_requires_graded_routes(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    raw = generate_strategy_candidates(client=_FakeStrategyClient(), context=context)
    grading = grade_strategy_candidates(raw=raw, context=context)
    artifacts = build_strategy_artifacts(context=context, raw=raw, grading=grading)
    write_strategy_artifacts(run_dir, context=context, artifacts=artifacts)
    forged_grading = StrategyGradingResult(
        routes=[],
        idea_status=grading.idea_status,
    )

    with pytest.raises(ValueError, match="graded strategy routes"):
        write_time_allocation_after_strategy_grader(run_dir, grading=forged_grading)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "time_allocation" not in manifest.get("optional_artifacts", {})
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()


def test_strategy_grader_is_first_time_allocation_trigger(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    context = load_agent_context(run_dir)
    raw = generate_strategy_candidates(client=_FakeStrategyClient(), context=context)
    grading = grade_strategy_candidates(raw=raw, context=context)
    artifacts = build_strategy_artifacts(context=context, raw=raw, grading=grading)
    write_strategy_artifacts(run_dir, context=context, artifacts=artifacts)

    plan = write_time_allocation_after_strategy_grader(run_dir, grading=grading)

    assert plan is not None
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["time_allocation"] == TIME_ALLOCATION_FILENAME
    stored = read_yaml(run_dir / TIME_ALLOCATION_FILENAME)
    assert stored["trigger"] == "strategy_grader"
    assert stored["allocation_state"] == "TIME_ALLOCATION_STARTED"
    assert stored["effect"] == "scheduling_only"
    assert stored["advisory_only"] is True
