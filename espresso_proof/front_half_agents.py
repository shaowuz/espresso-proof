"""Front-half live-agent pass.

The end-to-end ``run-flow`` pipeline currently fills the front-half artifacts
(locked claim, hypothesis ledger, examples, counterexamples, strategy brief, …)
from ``mock_pipeline``. This module lets those artifacts be (re)produced by a
real reasoning client (substrate A: Claude subagents via the ``external``
adapter, or the deterministic stub) while the Python kernel stays the trusted
validator.

Design (see docs/goals/AGENT_INTEGRATION_BUILD_PLAN.md):

* One ordered list of front-half stages, run in Core-Flow order.
* A registry mapping a stage to a *normalizer* ``callable(run_dir, client,
  context) -> dict``. A normalizer asks the client for content, then converts
  the raw model JSON into the kernel artifact using the existing safety helpers
  — it must NEVER trust model-supplied status fields (no upgrade to
  proved / source_backed / checked / survived).
* Stages with no registered normalizer are reported ``skipped`` and the existing
  (mock) artifact is left untouched, so enabling the pass before every stage is
  implemented can never corrupt a run.
* After every stage the run is re-validated with the kernel; on any validation
  error the pass fails closed (``strict=True``).

Slices S1–S8 each register one (or a small group of) stage normalizers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .llm_agents import LLMClient, load_agent_context
from .validators import validate_run

# Front-half stages in Core-Flow order. Names are stable identifiers used by the
# registry, the CLI ``--front-half`` plumbing, and the orchestrator.
FRONT_HALF_STAGES: list[str] = [
    "understanding_claim",  # S1: locked_claim + hypothesis_ledger
    "literature_review",    # S2: candidate references only
    "examples",             # S3: examples + computation_checks
    "counterexamples",      # S4: counterexamples + admissibility tests
    "strategy",             # S5: strategy generator + grader + brief
    "attacker",             # S6: example/counterexample attacker
    "source_theorem",       # S7: source matching + theorem applicability
    "grade_memory_memo",    # S8: grade + memory + concise answer
]

# stage name -> normalizer(run_dir=..., client=..., context=...) -> dict
# A normalizer returns an outcome dict; recognized keys: "unsafe_language" (list)
# and "notes" (list). Slices populate this registry via ``register_stage``.
StageNormalizer = Callable[..., dict]
_STAGE_REGISTRY: dict[str, StageNormalizer] = {}


def register_stage(name: str) -> Callable[[StageNormalizer], StageNormalizer]:
    """Decorator: register a normalizer for a front-half stage."""

    if name not in FRONT_HALF_STAGES:
        raise ValueError(f"unknown front-half stage: {name}")

    def _decorator(fn: StageNormalizer) -> StageNormalizer:
        _STAGE_REGISTRY[name] = fn
        return fn

    return _decorator


def registered_stages() -> list[str]:
    """Stages that currently have a normalizer, in Core-Flow order."""

    return [stage for stage in FRONT_HALF_STAGES if stage in _STAGE_REGISTRY]


def run_front_half_agent_pass(
    *,
    run_dir: Path,
    client: LLMClient,
    stages: list[str] | None = None,
    strict: bool = True,
) -> dict:
    """Run the front-half stages, validating the run after each.

    Unimplemented stages are skipped (mock artifact untouched). With
    ``strict=True`` (default) any normalizer error or post-stage validation
    error aborts the pass — the kernel is the gate, so we fail closed rather
    than leave a run in an unvalidated state.
    """

    stage_list = list(stages) if stages is not None else list(FRONT_HALF_STAGES)
    generated_at = datetime.now(UTC).isoformat()
    results: list[dict] = []

    for stage in stage_list:
        normalizer = _STAGE_REGISTRY.get(stage)
        if normalizer is None:
            results.append(
                {"stage": stage, "status": "skipped", "errors": [], "unsafe_language": []}
            )
            continue
        try:
            context = load_agent_context(run_dir)
            outcome = normalizer(run_dir=run_dir, client=client, context=context) or {}
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed, record, optionally re-raise
            results.append(
                {"stage": stage, "status": "error", "errors": [str(exc)], "unsafe_language": []}
            )
            if strict:
                raise
            continue
        results.append(
            {
                "stage": stage,
                "status": "ok",
                "errors": [],
                "unsafe_language": list(outcome.get("unsafe_language", [])),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "provider": getattr(client, "provider", "unknown"),
        "model": getattr(client, "model", "unknown"),
        "stages": results,
        "notes": [
            "Front-half agent pass; outputs are candidate planning artifacts only.",
            "This pass must not upgrade source_backed, checked evidence, or proof status.",
            "Every stage is re-validated by the Python kernel before being accepted.",
        ],
    }
