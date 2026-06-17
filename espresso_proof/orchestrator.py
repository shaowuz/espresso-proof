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

from .agent_run_status import init_agent_run_status
from .claim_clear import (
    CLAIM_CLEAR,
    WAITING_CLAIM_CLARIFICATION,
    ensure_claim_clear_for_downstream,
    evaluate_claim_clear,
    write_claim_clear_gate,
)
from .front_half_agents import run_front_half_agent_pass, write_front_half_run
from .validation.front_half import validate_front_half_run
from .io import read_text, read_yaml, write_yaml
from .literature_discovery import discover_literature as run_literature_discovery
from .llm_agents import (
    build_client,
    build_prover_loop_report,
    external_agent_diagnostic,
    openai_agent_diagnostic,
    resolve_agent_gate,
    route_initial_reviewer_blockers_to_update_inbox,
    run_llm_agent_pass,
    write_initial_prover_round_artifacts,
)
from .mock_pipeline import run_mock_critique
from .observability import (
    append_agent_event,
    monotonic_ms,
    now_iso,
    register_observability_artifacts,
    write_timings,
)
from .pressure_status import build_pressure_status
from .run_events import append_event
from .prover_loop_driver import continue_prover_loop
from .run_dashboard import write_run_dashboard
from .run_lock import run_writer_lock
from .stage_runner import StageRunner
from .surface_warnings import refresh_final_report_disclosures
from .validation.errors import KernelValidationError
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
    "WAITING_CLAIM_CLARIFICATION",
    "FRONT_HALF_DONE",
    "CLAIM_CLEAR",
    "LITERATURE_DONE",
    "WAITING_LLM_AGENT_HUMAN_GATE",
    "COMPLETED_MEMO",
]

# Goal 27 fast core pass: the minimum front-half stages needed to show an initial
# claim judgment + preliminary routes + a conservative grade, WITHOUT waiting for
# the optional evidence agents (literature / examples / counterexamples / failure-
# pattern audit). Those run later, on demand or in parallel, via ``run-agent``.
FAST_CORE_STAGES: list[str] = [
    "understanding_claim",  # S1: locked_claim + hypothesis_ledger (+ claim_precision)
    "source_theorem",       # S7: candidate source matching (candidate-only)
    "regrade",              # evidence-aware grade recompute (before strategy)
    "strategy",             # S5: strategy generator + grader + brief
    "grade_memory_memo",    # S8: grade refresh + memory + concise answer
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
    validate_run(run_dir)
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
    agent_network_preflight: bool = False,
    run_dir: Path | None = None,
    front_half_client=None,
    fast: bool = False,
) -> dict:
    """Drive a run from a fresh claim to the human gate, validating each stage.

    Returns a dict with the run_dir, the final lifecycle state, and the human
    gate status. Resumable: pass an existing ``run_dir`` to continue from its
    recorded state (completed stages are skipped).

    This is the single canonical runtime path. ``agent_network_preflight=True``
    performs a real provider call before scaffolding (so an unreachable live
    provider fails closed before any artifact is written) and threads the
    resulting diagnostic into the agent pass.
    """

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

    # Stage 0 — scaffold a kernel-valid "unclear/needs_human" run for any claim.
    # The genesis stage creates the run; from here every stage advances lifecycle
    # state ONLY through the transactional StageRunner (snapshot -> mutate ->
    # validate -> advance, with rollback on failure).
    if run_dir is None:
        run_dir = run_mock_critique(project, input_path, output_root)
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

    if _reached(run_dir, CLAIM_CLEAR):
        resume_gate = evaluate_claim_clear(run_dir, require_recorded_clear_gate=True)
        if not resume_gate.clear:
            gate_report: dict = {}

            def _stale_claim_gate() -> None:
                nonlocal gate_report
                gate_report = write_claim_clear_gate(run_dir, resume_gate)

            runner.run(
                name="claim_clear_gate",
                mutate=_stale_claim_gate,
                validate=lambda: validate_run(run_dir),
                state_after=WAITING_CLAIM_CLARIFICATION,
                snapshot_paths=["claim_clear_gate.yaml"],
            )
            return _finish_without_llm_gate(run_dir, claim_gate=gate_report)

    # Goal 27 fast core pass: run only the FAST_CORE_STAGES in the front half and
    # DEFER literature discovery + the evidence stages (examples / counterexamples),
    # so the user sees an initial judgment + preliminary routes quickly. The
    # deferred agents run later via ``run-agent``.
    front_half_stages = FAST_CORE_STAGES if fast else None
    ran_front_half_agents = front_half != "mock" or front_half_client is not None

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

    gate_result = evaluate_claim_clear(run_dir)
    gate_report = write_claim_clear_gate(run_dir, gate_result)
    if not gate_result.clear:
        _set_state(run_dir, WAITING_CLAIM_CLARIFICATION)
        validate_run(run_dir)
        return _finish_without_llm_gate(run_dir, claim_gate=gate_report)
    if not _reached(run_dir, CLAIM_CLEAR):
        _set_state(run_dir, CLAIM_CLEAR)
        validate_run(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    project_dir = run_dir.parents[1]

    # Stage 2 — literature discovery (candidate references only; validated).
    # Goal 27: in the fast core pass literature is a DEFERRED evidence agent --
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
            write_yaml(run_dir / artifacts["literature_review"], literature)

        runner.run(
            name="literature",
            mutate=_literature,
            validate=lambda: validate_run(run_dir),
            state_after="LITERATURE_DONE",
            snapshot_paths=[artifacts["literature_review"]],
        )

    # Goal 42E cycle 3: execute any structured scoped-verification requests
    # the front half proposed BEFORE the prover/reviewer pass, so round 1
    # already sees the trusted machine evidence and computation requests can
    # be machine-answered instead of recurring. Idempotent and fail-closed
    # (re-validates; a run without requests wires nothing; a confirming
    # result earns instance_checked only — never a universal status).
    if not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        from .proof_verification import auto_wire_scoped_verifications

        auto_wire_scoped_verifications(run_dir)

    # Stage 3 — live Prover + Reviewers; stop at the human gate.
    if not _reached(run_dir, "WAITING_LLM_AGENT_HUMAN_GATE"):
        def _llm_prover_reviewers() -> None:
            # Goal 54A: bracket the deep Prover+Reviewers model pass with provenance
            # so a stall in the model call (vs validation/rendering) is visible.
            _started = now_iso()
            _t0 = monotonic_ms()
            append_event(run_dir, "agent_call_started", stage="llm_prover_reviewers")
            # Goal 65B: record a "started" agent_event BEFORE the heavy external
            # call so a mid-call PROCESS death (the order-3 postmortem failure)
            # leaves a dangling started record — the only evidence of where the
            # run died, recoverable via observability.latest_unfinished_agent_call.
            append_agent_event(
                run_dir, agent_name="llm_prover_reviewers", provider=provider,
                adapter=provider, model=model, stage="llm_prover_reviewers",
                agent_status="started", started_at=_started,
            )
            try:
                agent_report = run_llm_agent_pass(
                    run_dir=run_dir,
                    provider=provider,
                    model=model,
                    api_key_env=api_key_env,
                    external_agent_command_env=external_agent_command_env,
                    reviewer_set=reviewer_set,
                    provider_diagnostic=provider_diagnostic,
                )
            except Exception as exc:  # noqa: BLE001 - record provenance, then re-raise
                append_agent_event(
                    run_dir, agent_name="llm_prover_reviewers", provider=provider,
                    adapter=provider, model=model, stage="llm_prover_reviewers",
                    agent_status="failed", started_at=_started, finished_at=now_iso(),
                    duration_ms=monotonic_ms() - _t0, error_summary=str(exc),
                )
                append_event(run_dir, "agent_call_failed", stage="llm_prover_reviewers", detail=str(exc))
                raise
            append_agent_event(
                run_dir, agent_name="llm_prover_reviewers", provider=provider, adapter=provider,
                model=model, stage="llm_prover_reviewers", agent_status="finished",
                started_at=_started, finished_at=now_iso(), duration_ms=monotonic_ms() - _t0,
                output_artifact_path="llm_agent_run.yaml",
            )
            append_event(run_dir, "agent_call_finished", stage="llm_prover_reviewers")
            LLMAgentRunReport.model_validate(agent_report)
            loop_report = build_prover_loop_report(
                agent_report=agent_report,
                strategy_brief=read_yaml(run_dir / artifacts["strategy_brief"]),
                human_choice=read_yaml(run_dir / artifacts["human_choice"]),
                run_dir=run_dir,
            )
            ProverLoopReport.model_validate(loop_report)
            stage_manifest = read_yaml(run_dir / "run_manifest.yaml")
            stage_manifest.setdefault("optional_artifacts", {})["llm_agent_run"] = "llm_agent_run.yaml"
            stage_manifest["optional_artifacts"]["prover_loop"] = "prover_loop.yaml"
            stage_manifest["optional_artifacts"]["prover_round_index"] = "prover_rounds/index.yaml"
            write_yaml(run_dir / "llm_agent_run.yaml", agent_report)
            write_yaml(run_dir / "prover_loop.yaml", loop_report)
            write_initial_prover_round_artifacts(
                run_dir=run_dir,
                agent_report=agent_report,
                loop_report=loop_report,
            )
            write_yaml(run_dir / "run_manifest.yaml", stage_manifest)

        # Whole-run snapshot: this stage creates the llm/prover artifacts and the
        # prover_rounds/ directory, all of which must be dropped on rollback.
        runner.run(
            name="llm_prover_reviewers",
            mutate=_llm_prover_reviewers,
            validate=lambda: validate_run(run_dir),
            state_after="WAITING_LLM_AGENT_HUMAN_GATE",
        )

    # Goal 27 — initialize the per-agent run-status board. In the fast core pass
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
        validate_run(run_dir)

    # Goal 54A: aggregate run timings + register the per-run observability logs as
    # optional artifacts (diagnostic only; both helpers fail closed and never gate).
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    final_manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    dashboard = write_run_dashboard(run_dir)
    return {
        "run_dir": run_dir,
        "state": final_manifest.get("current_state"),
        "human_gate": agent_run.get("human_gate", {}),
        "dashboard": dashboard,
    }


def _resolve_and_build_loop(run_dir: Path, *, decision: str, reason: str, human_choice: dict) -> None:
    """Resolve the LLM-agent human gate and (re)build prover_loop with the real
    human choice (mirrors the CLI ``resolve-agent-gate`` path)."""

    report = resolve_agent_gate(run_dir=run_dir, decision=decision, reason=reason)
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


def resume_run(*, run_dir: Path, **kwargs) -> str:
    """Record a human choice, then run a BOUNDED Prover<->Reviewer loop and memo.

    The human gate may direct ranking / time allocation only — it can NEVER
    upgrade proof / source / evidence status (the kernel re-validates throughout).
    Idempotent: a run already at COMPLETED_MEMO is not re-run. Returns the memo.

    Holds the cross-process run writer lock for the whole resume, so a second
    concurrent resume (CLI or server) serializes or fails with a friendly
    message instead of interleaving prover rounds (Goal 35B).
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
) -> str:
    from .memo import build_concise_memo  # local import to avoid a cycle

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    state = manifest.get("current_state", "")
    if state == "COMPLETED_MEMO":
        # idempotent: do not re-resolve / re-append rounds. But a pre-loop step on
        # this resume (e.g. --auto-wire-verifications / --audit-evidence) may have
        # appended scoped evidence before the early return, so refresh the derived
        # proof-search graph (fail closed) before the dashboard/memo so all three
        # surfaces stay coherent. This never upgrades status: the graph
        # re-validates through the kernel and only reflects already-earned evidence.
        if (run_dir / "proof_goal_graph.yaml").exists():
            from .subgoal_graph import build_subgoal_graph, remove_subgoal_graph
            from .theorem_condition_ledger import build_theorem_condition_ledger

            try:
                # Goal 57A: ensure the advisory theorem-condition ledger exists
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
    elif continue_decision == "stop":
        _resolve_and_build_loop(
            run_dir,
            decision="stop",
            reason="human selected no strategy to continue",
            human_choice=human_choice,
        )

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

    # Subgoal Graph (advisory search-management state): build a durable graph of
    # subgoals from the validated artifacts. It NEVER upgrades proof / source /
    # evidence / grade status. Fail closed: if the freshly built graph cannot pass
    # the full run validation, drop it (rather than leave an invalid artifact) and
    # continue — the memo/dashboard simply render without a graph.
    from .subgoal_graph import build_subgoal_graph, remove_subgoal_graph
    from .theorem_condition_ledger import build_theorem_condition_ledger

    try:
        # Goal 57A: build the advisory theorem-condition ledger first so the
        # graph can turn its entries into named repair subgoals. Idempotent and
        # writes an empty ledger when there is no llm_agent_run.
        build_theorem_condition_ledger(run_dir)
        build_subgoal_graph(run_dir)
        validate_run(run_dir)
    except Exception:  # noqa: BLE001 - advisory layer must fail closed, not block resume
        remove_subgoal_graph(run_dir)
        validate_run(run_dir)

    _set_state(run_dir, "COMPLETED_MEMO")
    write_timings(run_dir)
    register_observability_artifacts(run_dir)
    memo = build_concise_memo(run_dir)
    write_run_dashboard(run_dir)
    if loop_summary is not None:
        # Controlled wording only (no model free text; no scrub needed). The stop
        # reason renders as plain product copy, never the raw token (Goal 31).
        from .descriptions import loop_stop_phrase

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
    return memo


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
