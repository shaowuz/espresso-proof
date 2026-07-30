"""00 Orchestrator — a resumable, checkpointed driver for an Espresso run.

The orchestrator coordinates the Core Flow without ever becoming a proof writer:
it scaffolds a run for an arbitrary claim, dispatches the front-half agent
stages, runs literature discovery and the live Prover/Reviewers pass, validates
the run after every stage, records lifecycle state in ``run_manifest.yaml``, and
STOPS at the human gate by default (``auto_continue=True`` continues past a
pending prover gate with honest auto provenance — selected_by='auto',
human_confirmed=false; earlier human questions still stop the run). It is
resumable from the recorded state and never
upgrades proof / source / evidence status (the kernel remains the gate).

"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.orchestration.advisory_proof_sketch import capture_advisory_proof_sketch
from espresso_proof.kernel.agent_run_status import init_agent_run_status
from espresso_proof.claim.claim_clear import (
    CLAIM_CLEAR,
    WAITING_CLAIM_CLARIFICATION,
    ensure_claim_clear_for_downstream,
    evaluate_claim_clear,
    write_claim_clear_gate,
)
from espresso_proof.claim.persisted_claim_normalization import normalize_persisted_claim_ledger
from espresso_proof.front_half import run_front_half_agent_pass, write_front_half_run
from espresso_proof.front_half.runner import front_half_stage_ok
from espresso_proof.validation.front_half import validate_front_half_run
from espresso_proof.kernel.io import read_text, read_yaml, write_yaml
from espresso_proof.literature.literature_discovery import (
    discover_literature as run_literature_discovery,
    merge_agent_and_local_literature,
)
from espresso_proof.agents.llm_agents import (
    recorded_model_for_events,
    build_client,
    build_prover_loop_report,
    external_agent_diagnostic,
    ExternalAgentTimeoutError,
    openai_agent_diagnostic,
    resolve_agent_gate,
    route_initial_reviewer_blockers_to_update_inbox,
    run_llm_agent_pass,
    write_initial_prover_round_artifacts,
)
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.observability import (
    append_agent_event,
    monotonic_ms,
    now_iso,
    register_observability_artifacts,
    write_timings,
    usage_sink_for_run,
)
from espresso_proof.kernel.pressure_status import build_pressure_status
from espresso_proof.kernel.run_events import append_event
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.orchestration.run_dashboard import write_run_dashboard
from espresso_proof.orchestration.agent_flow_driver import (
    front_half_stages_for_runner,
    record_agent_flow_stage_status,
)
from espresso_proof.kernel.agent_flow_legacy_map import LEGACY_FRONT_HALF_FAST_ORDER
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.kernel.stage_runner import StageError, StageRunner
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.models import (
    HumanChoice,
    LiteratureReviewReport,
    LLMAgentRunReport,
    ProverLoopReport,
)
from espresso_proof.kernel.validators import validate_run

# Ordered lifecycle states. A stage runs only when the run has not advanced past
# it, which makes the orchestrator idempotent and resumable.
ORCHESTRATION_STATES: list[str] = [
    "SCAFFOLDED",
    "WAITING_CLAIM_CLARIFICATION",
    "FRONT_HALF_DONE",
    "CLAIM_CLEAR",
    "LITERATURE_DONE",
    "WAITING_LLM_AGENT_HUMAN_GATE",
    # The human's strategy decision (recorded by `choose-strategy`, cli.py) is a
    # persisted current_state >= the human gate. These MUST be in the ordered list,
    # otherwise _state_index returns -1 and resume_run's boundary check
    # (`_state_index(state) < _state_index(WAITING_LLM_AGENT_HUMAN_GATE)`) falsely
    # rejects a re-resume of a run interrupted between recording the choice and
    # COMPLETED_MEMO. They are mutually exclusive; relative order among the three
    # does not matter, only that each sits after the gate and before COMPLETED_MEMO.
    "HUMAN_CHOICE_CONTINUE_SELECTED",
    "HUMAN_CHOICE_REVISE_SELECTED",
    "HUMAN_CHOICE_STOP_SELECTED",
    # Honest auto-provenance peer of HUMAN_CHOICE_CONTINUE_SELECTED (same
    # ordering semantics: after the gate, before COMPLETED_MEMO). Machinery —
    # not a human — selected the continue route; the matching human_choice.yaml
    # carries selected_by='auto' and human_confirmed=false. Written by
    # ``orchestrate_run(auto_continue=True)`` just before the same-process
    # resume, so a crash mid-loop leaves the truthful state behind.
    "AUTO_CHOICE_CONTINUE_SELECTED",
    "COMPLETED_MEMO",
]

# Fast core pass: the minimum front-half stages needed to show an initial
# claim judgment + preliminary routes + a conservative grade, WITHOUT waiting for
# the optional evidence agents (literature / examples / counterexamples / failure-
# pattern audit). Those run later, on demand or in parallel, via ``run-agent``.
FAST_CORE_STAGES: list[str] = list(LEGACY_FRONT_HALF_FAST_ORDER)


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


def _record_claim_clear_gate_transaction(
    run_dir: Path,
    *,
    runner: StageRunner,
    gate_result,
) -> dict:
    """Persist claim-clear scheduling state inside a StageRunner transaction."""

    gate_report: dict = {}

    def _mutate_claim_gate() -> None:
        nonlocal gate_report
        gate_report = write_claim_clear_gate(run_dir, gate_result)
        _set_state(run_dir, gate_result.state)

    runner.run(
        name="claim_clear_gate",
        mutate=_mutate_claim_gate,
        validate=lambda: validate_run(run_dir),
        snapshot_paths=["claim_clear_gate.yaml"],
    )
    return gate_report


# Control-flow: next_action values for which the front half issued a HARD
# terminal stop, so launching an expensive external Prover+Reviewers pass is
# clearly wrong. ``ask_human`` is deliberately NOT here: the first Prover pass
# runs BEFORE the human gate by design (the human needs its output to decide),
# the stub/front half emits ``ask_human`` broadly — even for clear claims — and
# ``should_stop``+``ask_human`` correctly governs loop CONTINUATION (enforced in
# the prover-loop driver / resume), not the first pass. ``run_strategy_brief`` and
# ``continue_loop`` likewise keep the normal Prover pass. Gating only the hard
# ``stop`` is the correct, non-breaking control-flow guard.
_STOP_BEFORE_PROVER_ACTIONS = frozenset({"stop"})


def _stop_before_prover(run_dir: Path) -> dict:
    """Control-flow fix: decide whether to SKIP the
    expensive, crash-prone Prover+Reviewers pass because the front half issued a
    hard terminal stop (``loop_control.should_stop`` with ``next_action`` ==
    ``stop``). Returns ``{"stop": bool, "next_action": str | None}``. Fails closed
    to ``stop=False`` (run the pass) if ``loop_control`` is absent/unreadable, so
    the normal flow is preserved whenever the signal is unavailable. Control flow
    only; reads no status and changes nothing.

    NOTE: ``ask_human`` does NOT skip the first pass — see
    ``_STOP_BEFORE_PROVER_ACTIONS`` — because that pass runs before the human gate
    by design and ``ask_human`` is emitted broadly. Crash protection for that case
    is server survival, not skipping the pass."""
    try:
        loop_control = read_yaml(run_dir / "loop_control.yaml") or {}
        next_action = loop_control.get("next_action")
        stop = bool(loop_control.get("should_stop")) and next_action in _STOP_BEFORE_PROVER_ACTIONS
        return {"stop": stop, "next_action": next_action}
    except Exception:  # noqa: BLE001 - absent/unreadable loop_control => run the pass
        return {"stop": False, "next_action": None}


def _finish_without_llm_gate(
    run_dir: Path,
    *,
    claim_gate: dict,
) -> dict:
    init_agent_run_status(
        run_dir,
        done={"claim_understanding"},
        skipped={"literature", "examples", "counterexamples"},
        notes=[
            "Claim clarity blocked downstream scheduling.",
            "Skipped evidence agents contribute no evidence or status.",
        ],
    )
    build_pressure_status(run_dir, late_after_strategy=False)
    record_agent_flow_stage_status(run_dir)
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    dashboard = write_run_dashboard(run_dir)
    final_manifest = read_yaml(run_dir / "run_manifest.yaml")
    return {
        "run_dir": run_dir,
        "state": final_manifest.get("current_state"),
        "human_gate": {
            "status": "waiting_claim_clarification",
            "human_confirmed": False,
            "reason": "; ".join(claim_gate.get("blocking_reasons") or []),
        },
        "dashboard": dashboard,
    }


def _preflight_provider(
    provider: str, *, model: str, api_key_env: str, command_env: str, network_check: bool = False
) -> dict | None:
    """Fail early (with a clear message) if a live provider is not configured.

    Returns the provider diagnostic report (or ``None`` for the stub provider) so
    a caller can thread it into the agent pass. With ``network_check=True`` the
    diagnostic performs a real provider call and the run fails closed BEFORE any
    artifact is written if it is unreachable.
    """

    if provider == "stub":
        return None
    if provider == "openai":
        report = openai_agent_diagnostic(model=model, api_key_env=api_key_env, network_check=network_check)
    elif provider == "external":
        report = external_agent_diagnostic(model=model, command_env=command_env, network_check=network_check)
    else:
        raise RuntimeError(f"unsupported provider: {provider}")
    if report["status"] not in {"configured", "ready"}:
        message = report.get("error") or report.get("message") or f"{provider} provider is not ready."
        raise RuntimeError(str(message))
    return report



def _record_token_budget(run_dir: Path, token_budget: int | None) -> None:
    """Persist the human's declared spend ceiling on the run.

    Advisory scheduling input only: it widens which effort tiers a route may be
    approved for and can never grant a mathematical status. A run with no
    declared budget records nothing and behaves as before.
    """

    if token_budget is None:
        return
    if int(token_budget) <= 0:
        raise KernelValidationError("--token-budget must be a positive number of tokens")
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["token_budget"] = int(token_budget)
    write_yaml(manifest_path, manifest)


def orchestrate_run(
    *,
    project: str,
    input_path: Path,
    output_root: Path = Path("."),
    provider: str = "stub",
    front_half: str = "stub",
    token_budget: int | None = None,
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    literature_mode: str = "local",
    literature_providers: list[str] | None = None,
    max_references: int = 8,
    agent_network_preflight: bool = False,
    run_dir: Path | None = None,
    front_half_client=None,
    fast: bool = False,
    auto_continue: bool = False,
    rounds: int | None = None,
) -> dict:
    """Drive a run from a fresh claim to the human gate, validating each stage.

    Returns a dict with the run_dir, the final lifecycle state, and the human
    gate status. Resumable: pass an existing ``run_dir`` to continue from its
    recorded state (completed stages are skipped).

    ``auto_continue=True`` keeps going in the SAME process once the initial
    round reaches a pending prover human gate: machinery picks the top-scored
    strategy from the strategy brief and runs the bounded loop + memo, with
    honest provenance (state AUTO_CHOICE_CONTINUE_SELECTED, selected_by /
    resolved_by = 'auto', human_confirmed=false — auto can never claim human
    confirmation). Any earlier stop (claim clarification, front-half
    ask_human, claim-not-clear) is a HUMAN question and is NOT auto-answered:
    the run halts exactly where the default path halts. ``rounds`` caps the
    auto loop's budget further, as in ``resume_run``.

    This is the single canonical runtime path. ``agent_network_preflight=True``
    performs a real provider call before scaffolding (so an unreachable live
    provider fails closed before any artifact is written) and threads the
    resulting diagnostic into the agent pass.
    """
    # A fresh run is a dataflow over artifacts: claim artifacts -> candidate
    # context -> prover attempt -> reviewer reports -> human gate.
    provider_diagnostic = verify_agent_providers_before_run_exists(
        provider=provider,
        front_half=front_half,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        agent_network_preflight=agent_network_preflight,
    )
    run_dir, runner = create_or_attach_genesis_run(
        run_dir=run_dir,
        project=project,
        input_path=input_path,
        output_root=output_root,
        token_budget=token_budget,
    )

    early = finish_if_resumed_claim_is_no_longer_clear(run_dir=run_dir, runner=runner)
    if early is not None:
        return early

    # Fast core pass: run only the FAST_CORE_STAGES in the front half and
    # DEFER literature discovery + the evidence stages (examples / counterexamples),
    # so the user sees an initial judgment + preliminary routes quickly. The
    # deferred agents run later via ``run-agent``.
    front_half_stages = front_half_stages_for_runner(fast=fast)
    ran_front_half_agents = front_half != "mock" or front_half_client is not None

    build_front_half_context_artifacts(
        run_dir=run_dir,
        runner=runner,
        front_half=front_half,
        front_half_client=front_half_client,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        front_half_stages=front_half_stages,
    )

    early, artifacts, project_dir = stop_if_claim_is_not_clear(run_dir=run_dir, runner=runner)
    if early is not None:
        return early

    collect_candidate_literature(
        run_dir=run_dir,
        runner=runner,
        fast=fast,
        project_dir=project_dir,
        artifacts=artifacts,
        max_references=max_references,
        literature_mode=literature_mode,
        literature_providers=literature_providers,
    )

    execute_scoped_machine_checks(run_dir=run_dir)

    run_initial_prover_reviewer_round_to_human_gate(
        run_dir=run_dir,
        runner=runner,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        provider_diagnostic=provider_diagnostic,
        artifacts=artifacts,
    )

    publish_agent_process_board(
        run_dir=run_dir, fast=fast, ran_front_half_agents=ran_front_half_agents
    )
    record_agent_flow_stage_status(run_dir, runner=runner)

    summary = write_dashboard_and_return_run_summary(run_dir=run_dir)
    if not auto_continue:
        return summary
    return _auto_continue_from_gate(
        run_dir=run_dir,
        summary=summary,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        rounds=rounds,
    )


def verify_agent_providers_before_run_exists(
    *,
    provider: str,
    front_half: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    agent_network_preflight: bool,
) -> dict | None:
    """Fail closed if a live provider is unconfigured; return the prover diagnostic."""
    # Fail early if a live provider is requested but not configured (avoids
    # scaffolding a run that cannot complete its agent pass). The front-half
    # provider is only configuration-checked; the prover provider honours the
    # network-preflight flag and its diagnostic is reused by the agent pass.
    if front_half != "mock":
        _preflight_provider(
            front_half, model=model, api_key_env=api_key_env, command_env=external_agent_command_env
        )
    provider_diagnostic = _preflight_provider(
        provider,
        model=model,
        api_key_env=api_key_env,
        command_env=external_agent_command_env,
        network_check=agent_network_preflight,
    )
    return provider_diagnostic


def create_or_attach_genesis_run(
    *,
    run_dir: Path | None,
    project: str,
    input_path: Path,
    output_root: Path,
    token_budget: int | None = None,
) -> tuple[Path, StageRunner]:
    """Stage 0 — scaffold a fresh genesis run, or attach a runner to an existing run."""
    # Stage 0 — scaffold a kernel-valid "unclear/needs_human" run for any claim.
    # The genesis stage creates the run; from here every stage advances lifecycle
    # state ONLY through the transactional StageRunner (snapshot -> mutate ->
    # validate -> advance, with rollback on failure).
    if run_dir is None:
        run_dir = run_mock_critique(project, input_path, output_root)
        _record_token_budget(run_dir, token_budget)
        runner = StageRunner(run_dir)
        runner.run(
            name="scaffold",
            mutate=lambda: None,
            validate=lambda: validate_run(run_dir),
            state_after="SCAFFOLDED",
            snapshot_paths=[],
        )
    else:
        runner = StageRunner(run_dir)
    return run_dir, runner


def finish_if_resumed_claim_is_no_longer_clear(*, run_dir: Path, runner: StageRunner) -> dict | None:
    """Finish at the claim-clarification gate if a resumed run's claim is no longer clear."""
    if _reached(run_dir, CLAIM_CLEAR):
        resume_gate = evaluate_claim_clear(run_dir, require_recorded_clear_gate=True)
        if not resume_gate.clear:
            gate_report = _record_claim_clear_gate_transaction(
                run_dir,
                runner=runner,
                gate_result=resume_gate,
            )
            return _finish_without_llm_gate(run_dir, claim_gate=gate_report)
    return None


def stop_if_claim_is_not_clear(
    *, run_dir: Path, runner: StageRunner
) -> tuple[dict | None, dict | None, Path | None]:
    """Record the claim-clear gate. Returns (early_finish | None, artifacts, project_dir)."""
    gate_result = evaluate_claim_clear(run_dir)
    gate_report = _record_claim_clear_gate_transaction(
        run_dir,
        runner=runner,
        gate_result=gate_result,
    )
    if not gate_result.clear:
        return _finish_without_llm_gate(run_dir, claim_gate=gate_report), None, None

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    project_dir = run_dir.parents[1]
    return None, artifacts, project_dir


def build_front_half_context_artifacts(
    *,
    run_dir: Path,
    runner: StageRunner,
    front_half: str,
    front_half_client,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    front_half_stages: list[str],
) -> None:
    # Stage 1 — front-half agents (S1–S8). Skipped when front_half == "mock".
    if not _reached(run_dir, "FRONT_HALF_DONE"):
        def _front_half() -> None:
            pass_report = None
            if front_half != "mock" or front_half_client is not None:
                client = front_half_client or build_client(
                    provider=front_half,
                    model=model,
                    api_key_env=api_key_env,
                    external_agent_command_env=external_agent_command_env,
                    usage_sink=usage_sink_for_run(run_dir),
                )
                pass_report = run_front_half_agent_pass(
                    run_dir=run_dir, client=client, stages=front_half_stages
                )
            # Persist the durable audit-only front_half_run.yaml inside the
            # transaction (rolled back with the stage if validation fails).
            write_front_half_run(
                run_dir, pass_report=pass_report, provider=front_half, model=model
            )

        def _validate_front_half() -> None:
            validate_run(run_dir)
            validate_front_half_run(run_dir)

        # Whole-run snapshot: the front-half pass may rewrite many artifacts.
        runner.run(
            name="front_half",
            mutate=_front_half,
            validate=_validate_front_half,
            state_after="FRONT_HALF_DONE",
        )


def collect_candidate_literature(
    *,
    run_dir: Path,
    runner: StageRunner,
    fast: bool,
    project_dir: Path,
    artifacts: dict,
    max_references: int,
    literature_mode: str,
    literature_providers: list[str] | None,
) -> None:
    # Stage 2 — literature discovery (candidate references only; validated).
    # In the fast core pass literature is a DEFERRED evidence agent --
    # skip discovery here (the scaffolded candidate-only literature_review stays
    # valid) and just advance lifecycle state. It is run later via
    # ``run-agent --agent literature``; until then the board marks it not_run and
    # the dashboard shows no reference-supported evidence.
    if not _reached(run_dir, "LITERATURE_DONE"):
        def _literature() -> None:
            if fast:
                return
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
            # Merge-not-clobber: a full live run's S2 agent literature output is
            # a validated producer artifact; the deterministic local scan merges
            # BESIDE it instead of overwriting it (fast/mock runs never record
            # the stage ok, so their scaffold-replace semantics are unchanged).
            if front_half_stage_ok(run_dir, "literature_review"):
                literature = merge_agent_and_local_literature(
                    read_yaml(run_dir / artifacts["literature_review"]),
                    literature,
                    max_references=max_references,
                )
            write_yaml(run_dir / artifacts["literature_review"], literature)

        runner.run(
            name="literature",
            mutate=_literature,
            validate=lambda: validate_run(run_dir),
            state_after="LITERATURE_DONE",
            snapshot_paths=[artifacts["literature_review"]],
        )


def execute_scoped_machine_checks(*, run_dir: Path) -> None:
    # EXECUTE any structured scoped-verification requests
    # the front half proposed BEFORE the prover/reviewer pass, so round 1
    # already sees the trusted machine evidence and computation requests can
    # be machine-answered instead of recurring. This stage can EARN trusted
    # instance_checked / computation_checked evidence — it is not passive
    # wiring. Idempotent and fail-closed (re-validates; a run without
    # requests wires nothing; a confirming result earns instance_checked
    # only — never a universal status).
    if not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        from espresso_proof.verification.proof_verification import auto_wire_scoped_verifications

        auto_wire_scoped_verifications(run_dir)


def run_initial_prover_reviewer_round_to_human_gate(
    *,
    run_dir: Path,
    runner: StageRunner,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    reviewer_set: str,
    provider_diagnostic: dict | None,
    artifacts: dict,
) -> None:
    # Stage 3 — live Prover + Reviewers; stop at the human gate.
    # Control-flow fix: if the front half
    # already concluded the claim needs a human BEFORE proof search
    # (loop_control.should_stop with next_action ask_human/stop), do NOT spend an
    # expensive, crash-prone Prover+Reviewers external pass. Halt at the current
    # state — kernel.classify_state already surfaces this as "stopped" — and let
    # the human decide. next_action run_strategy_brief / continue_loop keep the
    # normal prover flow. Control flow only; never changes proof/source/evidence/
    # grade status. Reading loop_control fails closed to the normal pass.
    _sbp = _stop_before_prover(run_dir)
    if _sbp["stop"] and not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        append_event(
            run_dir,
            "prover_pass_skipped",
            stage="llm_prover_reviewers",
            detail=(
                "front half asked to stop before proof search "
                f"(next_action={_sbp['next_action']}); awaiting human"
            ),
        )
    if not _sbp["stop"] and not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        llm_agent_event_summary: dict[str, object] = {}
        event_model = recorded_model_for_events(provider=provider, model=model, client=None)

        def _llm_prover_reviewers() -> None:
            # Bracket the deep Prover+Reviewers model pass with provenance
            # so a stall in the model call (vs validation/rendering) is visible.
            stage_manifest = read_yaml(run_dir / "run_manifest.yaml")
            run_id = str(stage_manifest["run_id"])
            _started = now_iso()
            _t0 = monotonic_ms()
            append_event(run_dir, "agent_call_started", stage="llm_prover_reviewers")
            # Record a "started" agent_event BEFORE the heavy external
            # call so a mid-call PROCESS death
            # leaves a dangling started record — the only evidence of where the
            # run died, recoverable via observability.latest_unfinished_agent_call.
            append_agent_event(
                run_dir, agent_name="llm_prover_reviewers", provider=provider,
                adapter=provider, model=event_model, stage="llm_prover_reviewers",
                agent_status="started", run_id=run_id, started_at=_started,
            )
            try:
                agent_report = run_llm_agent_pass(
                    run_dir=run_dir,
                    provider=provider,
                    model=event_model,
                    api_key_env=api_key_env,
                    external_agent_command_env=external_agent_command_env,
                    reviewer_set=reviewer_set,
                    provider_diagnostic=provider_diagnostic,
                )
            except Exception as exc:  # noqa: BLE001 - record provenance, then re-raise
                # When the external agent timed out (and its process
                # group was killed), record timeout metadata so a timeout is
                # distinguishable from any other failure in agent_events.jsonl.
                _timed_out = isinstance(exc, ExternalAgentTimeoutError)
                _timeout_ms = exc.timeout_seconds * 1000 if _timed_out else None
                append_agent_event(
                    run_dir, agent_name="llm_prover_reviewers", provider=provider,
                    adapter=provider, model=event_model, stage="llm_prover_reviewers",
                    agent_status="timed_out" if _timed_out else "failed",
                    run_id=run_id, started_at=_started, finished_at=now_iso(),
                    duration_ms=monotonic_ms() - _t0, error_summary=str(exc),
                    timed_out=_timed_out, timeout_ms=_timeout_ms,
                )
                append_event(run_dir, "agent_call_failed", stage="llm_prover_reviewers", detail=str(exc))
                raise
            llm_agent_event_summary.update(run_id=run_id, started_at=_started, t0=_t0)
            write_initial_prover_reviewer_artifacts(
                run_dir=run_dir,
                agent_report=agent_report,
                artifacts=artifacts,
            )
            # Capture the prover attempt's (scrubbed, advisory-only)
            # reasoning into a snapshot-EXCLUDED sidecar. If validate_run below
            # rejects this round (e.g. a step smuggled a status field), the whole
            # run rolls back and the attempt is dropped — but this sidecar
            # survives the rollback, so the mathematics is not lost. Pure sink:
            # never upgrades any status; fail-closed (never breaks the run).
            capture_advisory_proof_sketch(run_dir, agent_report)

        # Whole-run snapshot: this stage creates the llm/prover artifacts and the
        # prover_rounds/ directory, all of which must be dropped on rollback.
        try:
            runner.run(
                name="llm_prover_reviewers",
                mutate=_llm_prover_reviewers,
                validate=lambda: validate_run(run_dir),
                state_after="WAITING_LLM_AGENT_HUMAN_GATE",
            )
        except StageError as exc:
            if llm_agent_event_summary:
                append_agent_event(
                    run_dir,
                    agent_name="llm_prover_reviewers",
                    provider=provider,
                    adapter=provider,
                    model=event_model,
                    stage="llm_prover_reviewers",
                    agent_status="failed",
                    run_id=str(llm_agent_event_summary["run_id"]),
                    started_at=str(llm_agent_event_summary["started_at"]),
                    finished_at=now_iso(),
                    duration_ms=monotonic_ms() - int(llm_agent_event_summary["t0"]),
                    output_artifact_path="llm_agent_run.yaml",
                    validation_status="failed",
                    error_summary=str(exc),
                )
                append_event(
                    run_dir,
                    "agent_call_failed",
                    stage="llm_prover_reviewers",
                    detail=str(exc),
                )
            raise
        if llm_agent_event_summary:
            append_agent_event(
                run_dir,
                agent_name="llm_prover_reviewers",
                provider=provider,
                adapter=provider,
                model=event_model,
                stage="llm_prover_reviewers",
                agent_status="finished",
                run_id=str(llm_agent_event_summary["run_id"]),
                started_at=str(llm_agent_event_summary["started_at"]),
                finished_at=now_iso(),
                duration_ms=monotonic_ms() - int(llm_agent_event_summary["t0"]),
                output_artifact_path="llm_agent_run.yaml",
                validation_status="passed",
            )
            append_event(run_dir, "agent_call_finished", stage="llm_prover_reviewers")


def write_initial_prover_reviewer_artifacts(
    *,
    run_dir: Path,
    agent_report: dict,
    artifacts: dict,
) -> None:
    """Persist the initial Prover -> Reviewers dataflow.

    Writes:
    - ``llm_agent_run.yaml``: one prover attempt plus reviewer reports.
    - ``prover_loop.yaml``: route attempts and reviewer blockers derived from
      those reports.
    - ``prover_rounds/round_001_*.yaml`` + index: durable per-strategy round
      artifacts consumed by later resume/prover-loop steps.
    """

    LLMAgentRunReport.model_validate(agent_report)
    loop_report = build_prover_loop_report(
        agent_report=agent_report,
        strategy_brief=read_yaml(run_dir / artifacts["strategy_brief"]),
        human_choice=read_yaml(run_dir / artifacts["human_choice"]),
        run_dir=run_dir,
    )
    ProverLoopReport.model_validate(loop_report)
    stage_manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = stage_manifest.setdefault("optional_artifacts", {})
    optional["llm_agent_run"] = "llm_agent_run.yaml"
    optional["prover_loop"] = "prover_loop.yaml"
    optional["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "llm_agent_run.yaml", agent_report)
    write_yaml(run_dir / "prover_loop.yaml", loop_report)
    write_initial_prover_round_artifacts(
        run_dir=run_dir,
        agent_report=agent_report,
        loop_report=loop_report,
    )
    write_yaml(run_dir / "run_manifest.yaml", stage_manifest)
    # prover_loop.yaml now exists (possibly with open blockers): the memo must
    # disclose them BEFORE the stage's validate_run, which requires the
    # prover_reviewer_blockers disclosure whenever the loop has open blockers.
    refresh_final_report_disclosures(run_dir)


def publish_agent_process_board(*, run_dir: Path, fast: bool, ran_front_half_agents: bool) -> None:
    # Initialize the per-agent run-status board. In the fast core pass
    # only claim_understanding has run; the evidence agents are not_run (to be run
    # on demand / in parallel). In a full pass literature + the evidence stages
    # already ran, so they are recorded done; the failure-pattern audit runs later
    # (at resume) and stays not_run here. The board never upgrades status -- it is
    # process audit only and validate_run re-checks it.
    if not (run_dir / "agent_run_status.yaml").exists():
        done = {"claim_understanding"}
        if not fast:
            done.add("literature")
            if ran_front_half_agents:
                done.add("examples")
                done.add("counterexamples")
        init_agent_run_status(run_dir, done=done)
        build_pressure_status(run_dir, late_after_strategy=False)


def write_dashboard_and_return_run_summary(*, run_dir: Path) -> dict:
    # Aggregate run timings + register the per-run observability logs as
    # optional artifacts (diagnostic only; both helpers fail closed and never gate).
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    final_manifest = read_yaml(run_dir / "run_manifest.yaml")
    # Control-flow fix: the Prover pass is skipped when the front half
    # asked to stop before proof search, so llm_agent_run.yaml may not exist.
    _agent_run_path = run_dir / "llm_agent_run.yaml"
    agent_run = read_yaml(_agent_run_path) if _agent_run_path.exists() else {}
    dashboard = write_run_dashboard(run_dir)
    return {
        "run_dir": run_dir,
        "state": final_manifest.get("current_state"),
        "human_gate": agent_run.get("human_gate", {}),
        "dashboard": dashboard,
    }


def _resolve_and_build_loop(
    run_dir: Path,
    *,
    decision: str,
    reason: str,
    human_choice: dict,
    resolved_by: str,
) -> None:
    """Resolve the LLM-agent human gate and (re)build prover_loop with the real
    choice (mirrors the CLI ``resolve-agent-gate`` path). ``resolved_by`` is
    required (no default) and records honest provenance: 'auto' means no human
    resolved this gate and the artifact must carry human_confirmed=false."""

    report = resolve_agent_gate(
        run_dir=run_dir, decision=decision, reason=reason, resolved_by=resolved_by
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    selected_strategy = human_choice.get("selected_strategy")
    if decision == "continue":
        route_initial_reviewer_blockers_to_update_inbox(
            run_dir=run_dir,
            agent_report=report,
            selected_strategy_id=selected_strategy,
        )
    loop_report = build_prover_loop_report(
        agent_report=report,
        strategy_brief=read_yaml(run_dir / artifacts["strategy_brief"]),
        human_choice=human_choice,
        run_dir=run_dir,
    )
    ProverLoopReport.model_validate(loop_report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["prover_loop"] = "prover_loop.yaml"
    manifest["optional_artifacts"]["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "prover_loop.yaml", loop_report)
    write_initial_prover_round_artifacts(
        run_dir=run_dir, agent_report=report, loop_report=loop_report
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)


def _pick_auto_choice(strategy_brief: dict) -> dict:
    """Pure picking rule for an automated (no-human) strategy selection.

    Deterministic and never wider than a human's menu: only strategies offered
    in ``allowed_human_choices`` are eligible; the highest ``score`` wins with
    brief order breaking ties; effort is the strategy's ``estimated_effort``
    when the brief offers it, else the first offered option. No eligible
    strategy -> ``none`` (stop). Returns {selected_strategy, effort, score}.
    """
    allowed = {
        str(choice["strategy_id"]): [str(option) for option in (choice.get("effort_options") or [])]
        for choice in (strategy_brief.get("allowed_human_choices") or [])
        if choice.get("strategy_id")
    }
    candidates = [
        strategy
        for strategy in (strategy_brief.get("top_strategies") or [])
        if strategy.get("id") and allowed.get(str(strategy["id"]))
    ]
    if not candidates:
        # the 'none' choice option only offers quick_test (see resume); fall
        # back to it so the honest outcome is a recorded stop, not a crash.
        none_options = allowed.get("none") or ["quick_test"]
        return {"selected_strategy": "none", "effort": none_options[0], "score": None}
    top = max(candidates, key=lambda strategy: int(strategy.get("score", 0)))
    options = allowed[str(top["id"])]
    estimated = str(top.get("estimated_effort") or "")
    return {
        "selected_strategy": str(top["id"]),
        "effort": estimated if estimated in options else options[0],
        "score": int(top.get("score", 0)),
    }


def auto_select_strategy(*, run_dir: Path) -> dict:
    """Pick the top-scored strategy from the run's strategy brief — no human involved.

    The caller owns the honesty contract: pass the result to ``resume_run``
    with ``selected_by='auto'`` so the persisted human_choice / gate artifacts
    carry human_confirmed=false. The kernel rejects an auto selection that
    claims human confirmation.
    """
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    return _pick_auto_choice(brief)


def _auto_continue_from_gate(
    *,
    run_dir: Path,
    summary: dict,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    reviewer_set: str,
    rounds: int | None,
) -> dict:
    """Same-process continuation after the initial round, with honest auto provenance.

    Fail closed: continues ONLY from a pending prover human gate. Any other
    state (an earlier human question, an already-resolved gate, a completed
    run) is left exactly as the default path leaves it, with the skip recorded
    in the returned summary — machinery never answers a question that was
    addressed to a human.
    """
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    state = str(manifest.get("current_state", ""))
    gate_status = (summary.get("human_gate") or {}).get("status")
    if state != "WAITING_LLM_AGENT_HUMAN_GATE" or gate_status != "pending":
        summary["auto_continue"] = {
            "attempted": False,
            "reason": (
                f"run is not at a pending prover human gate "
                f"(state={state or 'unknown'!r}, gate={gate_status!r})"
            ),
        }
        return summary
    choice = auto_select_strategy(run_dir=run_dir)
    decision = "stop" if choice["selected_strategy"] == "none" else "continue"
    append_event(
        run_dir,
        "auto_choice_selected",
        stage="human_choice",
        detail=(
            f"machinery selected strategy {choice['selected_strategy']!r} "
            f"(score={choice['score']}, effort={choice['effort']}, decision={decision}); "
            "no human involved — recorded with selected_by=auto, human_confirmed=false"
        ),
    )
    if decision == "continue":
        # Honest peer of HUMAN_CHOICE_CONTINUE_SELECTED — an auto decision is
        # never spelled as a HUMAN_CHOICE_* state. Set BEFORE the loop so a
        # crash mid-loop leaves the truthful state behind; validate fail-closed.
        _set_state(run_dir, "AUTO_CHOICE_CONTINUE_SELECTED")
        validate_run(run_dir)
    memo = resume_run(
        run_dir=run_dir,
        selected_strategy=choice["selected_strategy"],
        effort=choice["effort"],
        rounds=rounds,
        prover_provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        selected_by="auto",
    )
    summary = write_dashboard_and_return_run_summary(run_dir=run_dir)
    summary["memo"] = memo
    summary["auto_continue"] = {
        "attempted": True,
        "selected_strategy": choice["selected_strategy"],
        "effort": choice["effort"],
        "decision": decision,
        "selected_by": "auto",
    }
    return summary


def resume_run(*, run_dir: Path, **kwargs) -> str:
    """Record a strategy choice, then run a BOUNDED Prover<->Reviewer loop and memo.

    ``selected_by`` (keyword-only, default ``"auto"``) records honest choice
    provenance. The default is deliberately FAIL-CLOSED: a caller that does not
    say who picked the strategy is recorded as machinery ("auto", carrying
    human_confirmed=false — an under-claim at worst). Pass
    ``selected_by="human"`` ONLY from an entry point where a real human made
    the selection (typed CLI choice, cockpit button); forgetting the kwarg must
    never mint human provenance.

    The gate may direct ranking / time allocation only — it can NEVER
    upgrade proof / source / evidence status (the kernel re-validates throughout).
    Idempotent: a run already at COMPLETED_MEMO is not re-run. Returns the memo.

    Holds the cross-process run writer lock for the whole resume, so a second
    concurrent resume (CLI or server) serializes or fails with a friendly
    Message instead of interleaving prover rounds.
    """

    run_dir = Path(run_dir)
    with run_writer_lock(run_dir, action="resume"):
        return _resume_run_locked(run_dir=run_dir, **kwargs)


def _resume_run_locked(
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
    # Fail-closed default: an unspecified caller is recorded as machinery.
    # Only real human entry points may pass selected_by="human".
    selected_by: str = "auto",
) -> str:
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    # Phase 2: bring an old / external / denormalized persisted run up to the
    # normalized (meaning-preserving) hypothesis shape BEFORE the claim-clear gate.
    # We hold the run writer lock here (resume_run), so the helper may write; it is
    # idempotent (no-op on already-normalized runs) and claim-clear NEUTRAL.
    normalize_persisted_claim_ledger(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    state = manifest.get("current_state", "")
    if state == "COMPLETED_MEMO":
        return _resume_idempotent_memo(run_dir=run_dir)
    if _state_index(state) < _state_index("WAITING_LLM_AGENT_HUMAN_GATE"):
        raise ValueError(
            f"run is not at the human gate (state={state!r}); finish the agent pass first"
        )

    # Record the human's strategy choice, run a bounded Prover<->Reviewer loop, then
    # the advisory layers. The kernel re-validates throughout and never upgrades
    # proof / source / evidence status.
    human_choice, continue_decision = _record_resume_human_choice(
        run_dir=run_dir,
        manifest=manifest,
        selected_strategy=selected_strategy,
        effort=effort,
        taste_judgment=taste_judgment,
        selected_by=selected_by,
    )
    loop_summary = _run_resume_prover_loop(
        run_dir=run_dir,
        human_choice=human_choice,
        continue_decision=continue_decision,
        selected_strategy=selected_strategy,
        prover_provider=prover_provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        rounds=rounds,
        prover_client=prover_client,
        selected_by=selected_by,
    )
    _run_resume_advisory_layers(run_dir=run_dir)
    return _finalize_resume_memo(run_dir=run_dir, loop_summary=loop_summary)


def _resume_idempotent_memo(*, run_dir: Path) -> str:
    """A run already at COMPLETED_MEMO: refresh the derived graph + dashboards, return the memo."""
    from espresso_proof.ui.memo import build_concise_memo
    # idempotent: do not re-resolve / re-append rounds. But a pre-loop step on
    # this resume (e.g. --auto-wire-verifications / --audit-evidence) may have
    # appended scoped evidence before the early return, so refresh the derived
    # proof-search graph (fail closed) before the dashboard/memo so all three
    # surfaces stay coherent. This never upgrades status: the graph
    # re-validates through the kernel and only reflects already-earned evidence.
    if (run_dir / "proof_goal_graph.yaml").exists():
        from espresso_proof.kernel.subgoal_graph import build_subgoal_graph, remove_subgoal_graph
        from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

        try:
            # Ensure the advisory theorem-condition ledger exists
            # before the graph reads it (idempotent; writes an empty ledger
            # when there is no llm_agent_run).
            build_theorem_condition_ledger(run_dir)
            build_subgoal_graph(run_dir)
            validate_run(run_dir)
        except Exception:  # noqa: BLE001 - advisory view must fail closed
            remove_subgoal_graph(run_dir)
            validate_run(run_dir)
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    write_run_dashboard(run_dir)
    # The last of the four silent no-op paths. This branch is idempotent by
    # design, but its output used to be indistinguishable from a resume that
    # actually drove rounds — three consecutive no-op resumes of a completed
    # Sz(8) run each printed a full memo and exited 0. State it instead.
    return build_concise_memo(run_dir) + (
        "\n\nProver loop: no round ran — this run is already complete "
        "(state COMPLETED_MEMO). Resuming again only refreshes dashboards; "
        "to do more work, start a new run or approve a larger effort on a "
        "route before the loop finishes."
    )


def _record_resume_human_choice(
    *,
    run_dir: Path,
    manifest: dict,
    selected_strategy: str,
    effort: str | None,
    taste_judgment: str,
    selected_by: str,
) -> tuple[dict, str]:
    """Validate + persist the strategy choice with honest provenance.

    ``selected_by`` records WHO picked the strategy: ``"human"`` (a real
    human) or ``"auto"`` (machinery, no human in the loop). It is REQUIRED —
    there is deliberately no default, so every caller must state provenance.
    ``human_confirmed`` is derived from it — an auto selection can never claim
    human confirmation (the kernel rejects auto + confirmed).
    Returns (human_choice, continue_decision).
    """
    if selected_by not in {"human", "auto"}:
        raise ValueError(f"selected_by must be 'human' or 'auto', got {selected_by!r}")
    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    # continue only makes sense with a real strategy; 'none' must stop.
    continue_decision = "stop" if selected_strategy == "none" else "continue"
    if selected_strategy == "none" and effort is None:
        # the 'none' human-choice option only offers quick_test.
        effort = "quick_test"
    strategy_brief = read_yaml(run_dir / artifacts["strategy_brief"])
    _validate_resume_choice(
        strategy_brief=strategy_brief,
        selected_strategy=selected_strategy,
        effort=effort,
        continue_decision=continue_decision,
    )
    human_choice = {
        "claim_id": claim["id"],
        "status": "selected",
        "selected_strategy": selected_strategy,
        "effort": effort,
        "taste_judgment": taste_judgment,
        "continue_decision": continue_decision,
        # human_confirmed records that a HUMAN made this strategy selection
        # (required by the model for status=selected + selected_by=human). An
        # auto selection must record human_confirmed=false — auto provenance
        # can never claim human confirmation. It is NOT claim confirmation
        # either way: the locked claim's own human_confirmed stays False, and
        # the gate never upgrades proof/source/evidence status.
        "human_confirmed": selected_by == "human",
        "selected_by": selected_by,
    }
    HumanChoice.model_validate(human_choice)
    write_yaml(run_dir / artifacts["human_choice"], human_choice)
    validate_run(run_dir)
    return human_choice, continue_decision


def _run_resume_prover_loop(
    *,
    run_dir: Path,
    human_choice: dict,
    continue_decision: str,
    selected_strategy: str,
    prover_provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    reviewer_set: str,
    rounds: int | None,
    prover_client,
    selected_by: str,
) -> tuple[int, str] | None:
    """Resolve the agent gate and run a bounded Prover<->Reviewer loop (or stop).

    ``selected_by`` is required (no default): provenance must flow explicitly
    from the recorded human_choice."""
    loop_summary: tuple[int, str] | None = None
    # The gate resolution inherits the choice's provenance: an auto-selected
    # strategy means the gate was ALSO resolved without a human.
    resolver = "human" if selected_by == "human" else "auto"
    if continue_decision == "continue":
        # Resolve the LLM-agent gate + rebuild prover_loop with the real choice,
        # then run a bounded Prover<->Reviewer loop. The budget (max_rounds) comes
        # from the approved effort; ``rounds`` caps it further if given.
        _resolve_and_build_loop(
            run_dir,
            decision="continue",
            reason=f"{resolver} selected strategy {selected_strategy!r}",
            human_choice=human_choice,
            resolved_by=resolver,
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
    elif continue_decision == "stop":
        _resolve_and_build_loop(
            run_dir,
            decision="stop",
            reason=f"{resolver} selected no strategy to continue",
            human_choice=human_choice,
            resolved_by=resolver,
        )
    return loop_summary


def _run_resume_advisory_layers(*, run_dir: Path) -> None:
    """Advisory layers (judgment / failure-pattern / memory / subgoal graph); each re-validates."""
    # Mathematical Judgment Layer (advisory): record conservative risk findings
    # from the validated artifacts before rendering the memo. This NEVER upgrades
    # proof/source/evidence status (the report carries no status field) and
    # validate_run re-checks; it only surfaces warnings / next checks.
    from espresso_proof.verification.mathematical_judgment import judge_run

    judge_run(run_dir)
    validate_run(run_dir)

    # Math Failure Pattern Bank (advisory): record which catalogued failure
    # patterns this run's signals match. Never upgrades status, never refutes;
    # hard matches only point at existing validators. validate_run re-checks.
    from espresso_proof.kernel.failure_pattern_bank import audit_failure_patterns

    audit_failure_patterns(run_dir)
    validate_run(run_dir)

    # Memory influence (advisory): match prior project memory to this claim and
    # record recommended uses (avoid / retest / request_source / prioritize /
    # ask_human). Never upgrades status or overrides current artifacts; the report
    # carries no status field and validate_run re-checks.
    from espresso_proof.memory.memory_context import build_memory_context

    build_memory_context(run_dir)
    validate_run(run_dir)

    # Subgoal Graph (advisory search-management state): build a durable graph of
    # subgoals from the validated artifacts. It NEVER upgrades proof / source /
    # evidence / grade status. Fail closed: if the freshly built graph cannot pass
    # the full run validation, drop it (rather than leave an invalid artifact) and
    # continue — the memo/dashboard simply render without a graph.
    from espresso_proof.kernel.subgoal_graph import build_subgoal_graph, remove_subgoal_graph
    from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

    try:
        # Build the advisory theorem-condition ledger first so the
        # graph can turn its entries into named repair subgoals. Idempotent and
        # writes an empty ledger when there is no llm_agent_run.
        build_theorem_condition_ledger(run_dir)
        build_subgoal_graph(run_dir)
        validate_run(run_dir)
    except Exception:  # noqa: BLE001 - advisory layer must fail closed, not block resume
        remove_subgoal_graph(run_dir)
        validate_run(run_dir)


def _finalize_resume_memo(*, run_dir: Path, loop_summary: tuple[int, str] | None) -> str:
    """Mark COMPLETED_MEMO, refresh dashboards, build the concise memo."""
    from espresso_proof.ui.memo import build_concise_memo
    _set_state(run_dir, "COMPLETED_MEMO")
    record_agent_flow_stage_status(run_dir)
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    memo = build_concise_memo(run_dir)
    write_run_dashboard(run_dir)
    if loop_summary is not None:
        # Controlled wording only (no model free text; no scrub needed). The stop
        # Reason renders as plain product copy, never the raw token.
        from espresso_proof.ui.descriptions import loop_stop_phrase

        rounds_run, stop_reason = loop_summary
        if rounds_run == 0 and str(stop_reason).startswith("budget"):
            memo += (
                "\n\nProver loop: the approved budget allowed no further rounds."
            )
        else:
            memo += (
                f"\n\nProver loop: ran {rounds_run} round(s); "
                f"stopped because {loop_stop_phrase(stop_reason)}."
            )
    else:
        # loop_summary is None when the resume drove no loop at all -- the human
        # gate was already resolved and the budget was spent. Before this the
        # command exited 0 and re-rendered the same memo, so a caller resuming
        # repeatedly saw four "successful" runs and no indication that nothing
        # had happened. Say it, and say what would change it.
        budget = _resume_budget_snapshot(run_dir)
        memo += (
            "\n\nProver loop: no round ran. "
            + (
                f"The approved budget is spent ({budget}). "
                if budget
                else "The approved budget allows no further rounds. "
            )
            + "Resuming again will not change this run; approve a larger effort "
            "on a route that permits it, or start a new run."
        )
    return memo


def _resume_budget_snapshot(run_dir: Path) -> str:
    """`rounds a/b, agent calls c/d` for the memo, or "" if unreadable."""

    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        rel = (manifest.get("optional_artifacts") or {}).get("prover_loop")
        if not rel:
            return ""
        budget = (read_yaml(run_dir / rel) or {}).get("budget") or {}
        return (
            f"rounds {budget.get('rounds_used')}/{budget.get('max_rounds')}, "
            f"agent calls {budget.get('agent_calls_used')}/{budget.get('max_agent_calls')}"
        )
    except Exception:
        return ""


def _validate_resume_choice(
    *,
    strategy_brief: dict,
    selected_strategy: str,
    effort: str | None,
    continue_decision: str,
) -> None:
    strategy_ids = {
        str(strategy.get("id"))
        for strategy in (strategy_brief.get("top_strategies") or [])
        if strategy.get("id")
    }
    choices = {
        str(choice.get("strategy_id")): {
            str(option) for option in (choice.get("effort_options") or [])
        }
        for choice in (strategy_brief.get("allowed_human_choices") or [])
        if choice.get("strategy_id")
    }
    if selected_strategy not in choices:
        raise KernelValidationError(
            f"human_choice selected strategy {selected_strategy!r} has no allowed choice option"
        )
    if selected_strategy != "none" and selected_strategy not in strategy_ids:
        raise KernelValidationError(
            f"human_choice selected unknown strategy {selected_strategy!r}"
        )
    if effort not in choices[selected_strategy]:
        raise KernelValidationError(
            f"human_choice effort {effort!r} is not allowed for {selected_strategy!r}"
        )
    if selected_strategy == "none" and continue_decision != "stop":
        raise KernelValidationError("human_choice cannot continue with selected_strategy='none'")
