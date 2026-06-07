"""00 Orchestrator — a resumable, checkpointed driver for an Espresso run.

The orchestrator coordinates the Core Flow without ever becoming a proof writer:
it scaffolds a run for an arbitrary claim, dispatches the front-half agent
stages, runs literature discovery and the live Prover/Reviewers pass, validates
the run after every stage, records lifecycle state in ``run_manifest.yaml``, and
STOPS at the human gate. It is resumable from the recorded state and never
upgrades proof / source / evidence status (the kernel remains the gate).

See docs/goals/agent_goals/00_ORCHESTRATOR_GOAL.md and
docs/goals/AGENT_INTEGRATION_BUILD_PLAN.md (S9).
"""

from __future__ import annotations

from pathlib import Path

from .front_half_agents import run_front_half_agent_pass
from .io import read_text, read_yaml, write_yaml
from .literature_discovery import discover_literature as run_literature_discovery
from .llm_agents import (
    build_client,
    build_prover_loop_report,
    external_agent_diagnostic,
    openai_agent_diagnostic,
    resolve_agent_gate,
    run_llm_agent_pass,
    write_initial_prover_round_artifacts,
)
from .mock_pipeline import run_mock_critique
from .prover_loop_driver import continue_prover_loop
from .models import (
    HumanChoice,
    LiteratureReviewReport,
    LLMAgentRunReport,
    ProverLoopReport,
)
from .validators import validate_run

# Ordered lifecycle states. A stage runs only when the run has not advanced past
# it, which makes the orchestrator idempotent and resumable.
ORCHESTRATION_STATES: list[str] = [
    "SCAFFOLDED",
    "FRONT_HALF_DONE",
    "LITERATURE_DONE",
    "WAITING_LLM_AGENT_HUMAN_GATE",
    "COMPLETED_MEMO",
]


def _state_index(state: str) -> int:
    try:
        return ORCHESTRATION_STATES.index(state)
    except ValueError:
        return -1


def _set_state(run_dir: Path, state: str) -> None:
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["current_state"] = state
    write_yaml(manifest_path, manifest)


def _reached(run_dir: Path, state: str) -> bool:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return _state_index(manifest.get("current_state", "")) >= _state_index(state)


def _preflight_provider(
    provider: str, *, model: str, api_key_env: str, command_env: str
) -> None:
    """Fail early (with a clear message) if a live provider is not configured."""

    if provider == "stub":
        return
    if provider == "openai":
        report = openai_agent_diagnostic(model=model, api_key_env=api_key_env, network_check=False)
    elif provider == "external":
        report = external_agent_diagnostic(model=model, command_env=command_env, network_check=False)
    else:
        raise RuntimeError(f"unsupported provider: {provider}")
    if report["status"] not in {"configured", "ready"}:
        message = report.get("error") or report.get("message") or f"{provider} provider is not ready."
        raise RuntimeError(str(message))


def orchestrate_run(
    *,
    project: str,
    input_path: Path,
    output_root: Path = Path("."),
    provider: str = "stub",
    front_half: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    literature_mode: str = "local",
    literature_providers: list[str] | None = None,
    max_references: int = 8,
    run_dir: Path | None = None,
    front_half_client=None,
) -> dict:
    """Drive a run from a fresh claim to the human gate, validating each stage.

    Returns a dict with the run_dir, the final lifecycle state, and the human
    gate status. Resumable: pass an existing ``run_dir`` to continue from its
    recorded state (completed stages are skipped).
    """

    # Fail early if a live provider is requested but not configured (parity with
    # run-flow; avoids scaffolding a run that cannot complete its agent pass).
    if front_half != "mock":
        _preflight_provider(
            front_half, model=model, api_key_env=api_key_env, command_env=external_agent_command_env
        )
    _preflight_provider(
        provider, model=model, api_key_env=api_key_env, command_env=external_agent_command_env
    )

    # Stage 0 — scaffold a kernel-valid "unclear/needs_human" run for any claim.
    if run_dir is None:
        run_dir = run_mock_critique(project, input_path, output_root)
        validate_run(run_dir)
        _set_state(run_dir, "SCAFFOLDED")

    # Stage 1 — front-half agents (S1–S8). Skipped when front_half == "mock".
    if not _reached(run_dir, "FRONT_HALF_DONE"):
        if front_half != "mock" or front_half_client is not None:
            client = front_half_client or build_client(
                provider=front_half,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
            )
            run_front_half_agent_pass(run_dir=run_dir, client=client)
        # Always validate before advancing — a corrupt scaffold must fail closed
        # here rather than reaching a downstream stage.
        validate_run(run_dir)
        _set_state(run_dir, "FRONT_HALF_DONE")

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    project_dir = run_dir.parents[1]

    # Stage 2 — literature discovery (candidate references only; validated).
    if not _reached(run_dir, "LITERATURE_DONE"):
        literature = run_literature_discovery(
            project_dir=project_dir,
            locked_claim_path=run_dir / artifacts["locked_claim"],
            hypothesis_ledger_path=run_dir / artifacts["hypothesis_ledger"],
            max_references=max_references,
            query=read_text(run_dir / "input.md"),
            mode=literature_mode,
            providers=literature_providers,
        )
        LiteratureReviewReport.model_validate(literature)
        write_yaml(run_dir / artifacts["literature_review"], literature)
        validate_run(run_dir)
        _set_state(run_dir, "LITERATURE_DONE")

    # Stage 3 — live Prover + Reviewers; stop at the human gate.
    if not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        provider_diagnostic = None
        agent_report = run_llm_agent_pass(
            run_dir=run_dir,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            reviewer_set=reviewer_set,
            provider_diagnostic=provider_diagnostic,
        )
        LLMAgentRunReport.model_validate(agent_report)
        loop_report = build_prover_loop_report(
            agent_report=agent_report,
            strategy_brief=read_yaml(run_dir / artifacts["strategy_brief"]),
            human_choice=read_yaml(run_dir / artifacts["human_choice"]),
        )
        ProverLoopReport.model_validate(loop_report)
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        manifest.setdefault("optional_artifacts", {})["llm_agent_run"] = "llm_agent_run.yaml"
        manifest.setdefault("optional_artifacts", {})["prover_loop"] = "prover_loop.yaml"
        manifest.setdefault("optional_artifacts", {})["prover_round_index"] = (
            "prover_rounds/index.yaml"
        )
        write_yaml(run_dir / "llm_agent_run.yaml", agent_report)
        write_yaml(run_dir / "prover_loop.yaml", loop_report)
        write_initial_prover_round_artifacts(
            run_dir=run_dir,
            agent_report=agent_report,
            loop_report=loop_report,
        )
        write_yaml(run_dir / "run_manifest.yaml", manifest)
        validate_run(run_dir)
        _set_state(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE")

    final_manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    return {
        "run_dir": run_dir,
        "state": final_manifest.get("current_state"),
        "human_gate": agent_run.get("human_gate", {}),
    }


def _resolve_and_build_loop(run_dir: Path, *, decision: str, reason: str, human_choice: dict) -> None:
    """Resolve the LLM-agent human gate and (re)build prover_loop with the real
    human choice (mirrors the CLI ``resolve-agent-gate`` path)."""

    report = resolve_agent_gate(run_dir=run_dir, decision=decision, reason=reason)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    loop_report = build_prover_loop_report(
        agent_report=report,
        strategy_brief=read_yaml(run_dir / artifacts["strategy_brief"]),
        human_choice=human_choice,
    )
    ProverLoopReport.model_validate(loop_report)
    manifest.setdefault("optional_artifacts", {})["prover_loop"] = "prover_loop.yaml"
    manifest["optional_artifacts"]["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "prover_loop.yaml", loop_report)
    write_initial_prover_round_artifacts(
        run_dir=run_dir, agent_report=report, loop_report=loop_report
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)


def resume_run(
    *,
    run_dir: Path,
    selected_strategy: str,
    effort: str | None = None,
    taste_judgment: str = "",
    rounds: int | None = None,
    prover_provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    prover_client=None,
) -> str:
    """Record a human choice, then run a BOUNDED Prover<->Reviewer loop and memo.

    The human gate may direct ranking / time allocation only — it can NEVER
    upgrade proof / source / evidence status (the kernel re-validates throughout).
    Idempotent: a run already at COMPLETED_MEMO is not re-run. Returns the memo.
    """

    from .memo import build_concise_memo  # local import to avoid a cycle

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    state = manifest.get("current_state", "")
    if state == "COMPLETED_MEMO":
        # idempotent: do not re-resolve / re-append rounds.
        return build_concise_memo(run_dir)
    if _state_index(state) < _state_index("WAITING_LLM_AGENT_HUMAN_GATE"):
        raise ValueError(
            f"run is not at the human gate (state={state!r}); finish the agent pass first"
        )

    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    # continue only makes sense with a real strategy; 'none' must stop.
    continue_decision = "stop" if selected_strategy == "none" else "continue"
    if selected_strategy == "none" and effort is None:
        # the 'none' human-choice option only offers quick_test.
        effort = "quick_test"
    human_choice = {
        "claim_id": claim["id"],
        "status": "selected",
        "selected_strategy": selected_strategy,
        "effort": effort,
        "taste_judgment": taste_judgment,
        "continue_decision": continue_decision,
        # human_confirmed records that the human made THIS strategy selection
        # (required by the model for status=selected). It is NOT claim
        # confirmation: the locked claim's own human_confirmed stays False, and
        # the human gate never upgrades proof/source/evidence status.
        "human_confirmed": True,
    }
    HumanChoice.model_validate(human_choice)
    write_yaml(run_dir / artifacts["human_choice"], human_choice)
    validate_run(run_dir)

    loop_summary: tuple[int, str] | None = None
    if continue_decision == "continue":
        # Resolve the LLM-agent gate + rebuild prover_loop with the real choice,
        # then run a bounded Prover<->Reviewer loop. The budget (max_rounds) comes
        # from the approved effort; ``rounds`` caps it further if given.
        _resolve_and_build_loop(
            run_dir,
            decision="continue",
            reason=f"human selected strategy {selected_strategy!r}",
            human_choice=human_choice,
        )
        result = continue_prover_loop(
            run_dir=run_dir,
            provider=prover_provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            reviewer_set=reviewer_set,
            max_rounds=rounds,
            client=prover_client,
        )
        loop_summary = (result["rounds_run"], result["stop_reason"])

    # Mathematical Judgment Layer (advisory): record conservative risk findings
    # from the validated artifacts before rendering the memo. This NEVER upgrades
    # proof/source/evidence status (the report carries no status field) and
    # validate_run re-checks; it only surfaces warnings / next checks.
    from .mathematical_judgment import judge_run

    judge_run(run_dir)
    validate_run(run_dir)

    # Math Failure Pattern Bank (advisory): record which catalogued failure
    # patterns this run's signals match. Never upgrades status, never refutes;
    # hard matches only point at existing validators. validate_run re-checks.
    from .failure_pattern_bank import audit_failure_patterns

    audit_failure_patterns(run_dir)
    validate_run(run_dir)

    # Memory influence (advisory): match prior project memory to this claim and
    # record recommended uses (avoid / retest / request_source / prioritize /
    # ask_human). Never upgrades status or overrides current artifacts; the report
    # carries no status field and validate_run re-checks.
    from .memory_context import build_memory_context

    build_memory_context(run_dir)
    validate_run(run_dir)

    _set_state(run_dir, "COMPLETED_MEMO")
    memo = build_concise_memo(run_dir)
    if loop_summary is not None:
        # Controlled enum+integer line only (no model free text; no scrub needed).
        memo += f"\n\nLoop: ran {loop_summary[0]} round(s); stopped: {loop_summary[1]}"
    return memo
