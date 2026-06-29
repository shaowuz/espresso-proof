from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer

from espresso_proof.kernel.contracts import DEFAULT_AGENT_FLOW, default_agent_contracts
from espresso_proof.ui.descriptions import (
    describe_grade,
    describe_proof_language,
    evidence_phrase,
    judgment_tier_phrase,
    objection_phrase,
)
from espresso_proof.kernel.io import read_text, read_yaml, write_text, write_yaml
from espresso_proof.literature.literature_discovery import discover_literature as run_literature_discovery
from espresso_proof.literature.literature_review_agent import ReviewSourceSpec, run_literature_review_agent
from espresso_proof.literature.literature_scout import run_literature_scout
from espresso_proof.literature.retrieval_grounding import retrieve_and_ground
from espresso_proof.agents.llm_agents import append_prover_loop_round
from espresso_proof.agents.llm_agents import resolve_agent_gate as resolve_llm_agent_gate
from espresso_proof.agents.llm_agents import build_client as build_llm_client
from espresso_proof.agents.llm_agents import build_prover_loop_report
from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.agents.llm_agents import external_agent_diagnostic
from espresso_proof.agents.llm_agents import openai_agent_diagnostic
from espresso_proof.agents.llm_agents import route_initial_reviewer_blockers_to_update_inbox
from espresso_proof.agents.llm_agents import run_llm_agent_pass
from espresso_proof.agents.llm_agents import write_initial_prover_round_artifacts
from espresso_proof.orchestration.failure_classifier import classify_failures
from espresso_proof.kernel.failure_pattern_bank import audit_failure_patterns, seed_failure_pattern_bank
from espresso_proof.literature.autonomous_retrieval import run_autonomous_retrieval
from espresso_proof.ui.bank_dashboard import render_bank_dashboard
from espresso_proof.orchestration.run_dashboard import render_run_dashboard, write_run_dashboard
from espresso_proof.kernel.research_bank import (
    _BANKABLE_KINDS,
    append_bank_item,
    load_bankable_dict,
    materialize_run_banks,
)
# NOTE: literature_retrieval.retrieve_and_ground (corpus_dir/query, older
# retrieve-sources --ground path) is a DIFFERENT function from
# retrieval_grounding.retrieve_and_ground (Goal 03, candidate/local_corpus). Alias
# this one so it does not shadow the Goal 03 import above (which the
# `retrieve-and-ground` command depends on).
from espresso_proof.literature.literature_retrieval import retrieve_and_ground as retrieve_and_ground_from_corpus
from espresso_proof.literature.literature_retrieval import retrieve_sources
from espresso_proof.verification.mathematical_judgment import judge_run
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.kernel.subgoal_graph import build_subgoal_graph
from espresso_proof.verification.finite_group_verifier import verify_identity_on_table
from espresso_proof.verification.proof_verification import (
    auto_wire_scoped_verifications,
    verify_tensor_left_exact_counterexample_in_run,
    verify_finite_groups,
    verify_gl2_f3_solvable_in_run,
    verify_identity_table_in_run,
    verify_matrix_rank_in_run,
    verify_ring_identity_in_run,
)
from espresso_proof.verification.symbolic_identity import SUPPORTED_RING_IDENTITIES
from espresso_proof.evaluation.benchmark import run_benchmark
from espresso_proof.evaluation.calibration import run_calibration
from espresso_proof.evaluation.real_math_benchmark import (
    build_real_math_capability_report,
    case_from_run as real_math_case_from_run,
)
from espresso_proof.claim.claim_clear import (
    CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS,
    ClaimClearBlockedError,
    ensure_claim_clear_for_downstream,
)
from espresso_proof.evaluation.scorecard import render_scorecard_markdown, run_scorecard
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.kernel.proof_language import scrub_proof_language
from espresso_proof.kernel.surface_warnings import (
    collect_surface_warnings,
    refresh_final_report_disclosures,
    render_warning_lines,
)
from espresso_proof.evaluation.reviewer_checklist import review_checklist
from espresso_proof.memory.memory import load_memory_index, summarize_memory, write_memory_entry
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.literature.pdf_ingestion import (
    extract_candidate_locators,
    extract_source_text,
    ingest_source_for_theorems,
    read_source_bytes,
)
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.literature.source_grounding_stage import prepare_source_grounding
from espresso_proof.kernel.external_tool_runner import (
    DEFAULT_TIMEOUT_SECONDS,
    FAKE_TOOLS,
    UnknownToolError,
    run_and_record_external_tool,
    run_completed_cleanly,
)
from espresso_proof.verification.external_tool_adapters import run_exact_modexp_adapter
from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
from espresso_proof.verification.verifier import audit_evidence, verify_computation_checks
from espresso_proof.kernel.models import (
    AdmissionCheck,
    AgentContracts,
    ContinueDecision,
    ExampleFeedbackSummary,
    ExplorationMode,
    HumanChoice,
    HumanExampleFeedback,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    MemoryEntry,
    ProverLoopReport,
    ProofGapResolutionReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
    WorkflowSpec,
)
from espresso_proof.kernel.schema_export import check_schemas, write_schemas
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.orchestration.workflow_generator import generate_workflow_spec

app = typer.Typer(help="Espresso Proof local kernel.")

_WINDOWS_RUN_PATH_LIMIT = 240




def _guard_windows_run_path_length(output_root: Path, project: str) -> None:
    """Fail early when a fresh run path is likely to exceed legacy MAX_PATH."""

    if os.name != "nt":
        return
    sample_run_id = f"{project}-YYYYMMDD-HHMMSS-xxxxxxxx"
    try:
        root = str(output_root.resolve(strict=False))
    except Exception:  # noqa: BLE001 - pathlib can reject WindowsPath on non-Windows tests
        root = str(output_root)
    sample = os.path.join(
        root,
        "projects",
        project,
        "runs",
        sample_run_id,
        "prover_rounds",
        "round_001_initial_llm_prover_review_attempt.yaml",
    )
    if len(sample) >= _WINDOWS_RUN_PATH_LIMIT:
        raise typer.BadParameter(
            "Windows run path would likely exceed MAX_PATH. Use a shorter "
            "--output-root such as C:\\tmp\\espresso_runs or a shorter --project."
        )


def _resolve_front_half_mode(*, provider: str, front_half: str, command_name: str) -> str:
    """Resolve the user-facing front-half setting.

    ``auto`` keeps deterministic stub runs on the developer path, while real
    providers use a real claim-understanding agent by default. ``run-flow`` keeps
    its legacy stub fixture default (mock) so old diagnostic tests stay stable.
    Explicitly pairing a real provider with mock/stub front-half remains
    available for fixture/debug parity and is surfaced to the user.
    """

    if front_half == "auto":
        if provider == "external":
            return "external"
        if provider == "openai":
            return "openai"
        return "mock" if command_name == "run-flow" else "stub"
    if provider in {"openai", "external"} and front_half in {"mock", "stub"}:
        typer.echo(
            f"{command_name}: using --front-half {front_half} with provider={provider}. "
            f"This run used {front_half} front-half; arbitrary math claims may stop at "
            "clarification. Omit --front-half to use the real claim-understanding "
            "agent, or pass --front-half external for the external adapter."
        )
    return front_half


def _refresh_process_status(run: Path) -> None:
    """Refresh AGENT_FLOW process projection after manual CLI artifact writes."""

    from espresso_proof.orchestration.agent_flow_status import (
        write_stage_status_projection,
    )

    write_stage_status_projection(run, include_artifact_hashes=False)
    validate_run(run)


def _mark_failure_patterns_done_if_board_exists(run: Path) -> None:
    """Keep manual failure-audit commands aligned with the visible process board."""

    from espresso_proof.kernel.agent_run_status import has_status, set_agent_status

    if not has_status(run):
        return
    now = datetime.now(UTC).isoformat()
    set_agent_status(
        run,
        "failure_patterns",
        "done",
        started_at=now,
        finished_at=now,
        increment_attempt=True,
    )


def _require_claim_clear_for_cli(run: Path) -> None:
    try:
        ensure_claim_clear_for_downstream(run, require_recorded_clear_gate=True)
    except ClaimClearBlockedError as exc:
        raise typer.BadParameter(str(exc)) from exc








@app.command("orchestrate")
def orchestrate(
    input: Path | None = typer.Option(None, "--input", "-i", dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("auto", "--front-half"),
    model: str = typer.Option("gpt-4.1", "--model"),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
    literature_mode: str = typer.Option("local", "--literature-mode"),
    output_root: Path = typer.Option(Path("."), "--output-root", file_okay=False),
    resume: Path | None = typer.Option(None, "--resume", file_okay=False),
    select_strategy: str | None = typer.Option(None, "--select-strategy"),
    effort: str | None = typer.Option(None, "--effort"),
    rounds: int | None = typer.Option(None, "--rounds", min=0, max=50),
    ground_source: Path | None = typer.Option(None, "--ground-source", help="Local PDF/text path or HTTPS URL of a source to ground steps against (resume)."),
    source_id: str = typer.Option("source_001", "--source-id"),
    source_title: str = typer.Option("Ingested source", "--source-title"),
    source_author: list[str] | None = typer.Option(None, "--source-author"),
    source_type: str = typer.Option("paper", "--source-type"),
    run_evidence_audit: bool = typer.Option(False, "--audit-evidence", help="Machine-check computations (earn computation_checked) before the loop (resume)."),
    run_auto_wire_verifications: bool = typer.Option(False, "--auto-wire-verifications", help="Run the strategy/prover stage's explicit scoped finite/computational checks (earn instance_checked) before the loop (resume)."),
    run_lit_scout: bool = typer.Option(False, "--literature-scout", help="Run the LLM Literature Scout (candidate sources per proof step) before the loop (resume)."),
    auto_ground: Path | None = typer.Option(None, "--auto-ground", file_okay=False, help="Local corpus directory: auto resolve + ground a source from it before the loop (resume; fail-closed)."),
    fast: bool = typer.Option(False, "--fast", help="Fast core pass: show an initial claim judgment + preliminary routes quickly; DEFER literature/examples/counterexamples to on-demand 'run-agent' (fresh run only)."),
) -> None:
    """Run the Core Flow end-to-end for an arbitrary claim, stopping at the human gate.

    Fresh run: provide --input. The orchestrator scaffolds the run, dispatches the
    front-half agents, runs literature + the live Prover/Reviewers, validates after
    each stage, and stops at the human gate. Resume: provide --resume <run_dir>
    with --select-strategy (and --effort) to record the human choice and render the
    concise memo. The kernel validates every stage; nothing here upgrades
    proof/source/evidence status.
    """
    if resume is not None:
        if not select_strategy:
            raise typer.BadParameter("--resume requires --select-strategy")
        if provider not in {"stub", "openai", "external"}:
            raise typer.BadParameter("provider must be stub, openai, or external")
        validate_run(resume)
        _require_claim_clear_for_cli(resume)
        if ground_source is not None:
            # Ground steps against a HUMAN-PROVIDED source BEFORE the loop via the
            # single auditable, transactional source-grounding stage: a step earns
            # source_backed only with exact locator + hypothesis match +
            # attribution (author+title). Fails closed (run stays at this gate).
            prepare_source_grounding(
                run_dir=resume,
                ground_source=ground_source,
                source_id=source_id,
                title=source_title,
                authors=source_author,
                source_type=source_type,
            )
            typer.echo(f"Grounded against {ground_source} (a reference supports a step only with exact locator, hypothesis match, and attribution)")
        if run_evidence_audit:
            # Earn computation_checked from confirming machine results BEFORE the loop.
            audit_evidence(resume)
            typer.echo("Evidence audited (a computation is checked only by a confirming machine result)")
        if run_auto_wire_verifications:
            # Run the strategy/prover stage's explicit scoped finite/computational
            # checks BEFORE the loop; a confirming result earns instance_checked
            # (finite/computational scope only, never a universal proof).
            summary = auto_wire_scoped_verifications(resume)
            typer.echo(
                f"Auto-wired scoped checks: {len(summary['appended'])} run, "
                f"{len(summary['skipped'])} skipped (checked in this finite/computational "
                "scope only; NOT a universal proof)"
            )
        if run_lit_scout:
            # LLM Literature Scout: candidate sources per proof step (advisory,
            # candidate-only; never earns source_backed). Uses the resume provider.
            scout_client = build_llm_client(
                provider=provider, model=model, api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
            )
            scout = run_literature_scout(run_dir=resume, client=scout_client)
            n = sum(len(s["candidates"]) for s in scout["proof_step_candidates"])
            typer.echo(f"Literature scout: {n} candidate source(s) proposed (candidate-only, never source_backed)")
        if auto_ground is not None:
            # Auto resolve + ground a source from a LOCAL corpus before the loop.
            # Fail-closed by construction: an unresolved/failed retrieval leaves the
            # run valid and the step candidate (support is earned only by the
            # grounding gate: exact locator + attribution + hypothesis match).
            ag = retrieve_and_ground(
                run_dir=resume, local_corpus=auto_ground,
                source_id=source_id, title=source_title, authors=source_author or [],
                source_type="local_note",
            )
            typer.echo(
                f"Auto-ground (local-first): resolution={ag['resolution_status']} "
                f"(retrieval never sets source_backed; the grounding gate decides)"
            )
        memo = resume_run(
            run_dir=resume,
            selected_strategy=select_strategy,
            effort=effort,
            rounds=rounds,
            prover_provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            reviewer_set=reviewer_set,
        )
        typer.echo(memo)
        dashboard = write_run_dashboard(resume)
        typer.echo(f"Dashboard: {dashboard}")
        return

    if input is None:
        raise typer.BadParameter("provide --input for a fresh run, or --resume <run_dir>")
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    if front_half not in {"auto", "mock", "stub", "openai", "external"}:
        raise typer.BadParameter("front-half must be auto, mock, stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    front_half = _resolve_front_half_mode(
        provider=provider, front_half=front_half, command_name="orchestrate"
    )

    _guard_windows_run_path_length(output_root, project)
    result = orchestrate_run(
        project=project,
        input_path=input,
        output_root=output_root,
        provider=provider,
        front_half=front_half,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        literature_mode=literature_mode,
        fast=fast,
    )
    typer.echo(f"Wrote run artifacts to {result['run_dir']}")
    typer.echo(f"Dashboard: {result['dashboard']}")
    typer.echo(f"State: {result['state']}")
    typer.echo(f"Human gate: {result['human_gate'].get('status', 'unknown')}")
    if fast:
        typer.echo(
            "Fast core pass: routes are PRELIMINARY. Run evidence agents on demand:\n"
            f"  espresso run-agent --run {result['run_dir']} --agent examples\n"
            f"  espresso run-agent --run {result['run_dir']} --agent counterexamples\n"
            f"  espresso run-agent --run {result['run_dir']} --agent literature\n"
            f"  espresso run-agent --run {result['run_dir']} --agent failure-patterns"
        )
    typer.echo(
        f"Resume with: espresso orchestrate --resume {result['run_dir']} "
        "--select-strategy <id> [--effort <mode>]"
    )


@app.command("run-agent")
def run_agent_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    agent: str = typer.Option(..., "--agent", help="examples | counterexamples | literature | failure-patterns"),
    skip: bool = typer.Option(False, "--skip", help="Mark this agent skipped (contributes no evidence) instead of running it."),
    reason: str | None = typer.Option(None, "--reason", help="Optional note recorded when skipping."),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Run (or skip) ONE evidence agent on demand and update agent_run_status.yaml.

    Writes only that agent's artifact, never rewrites unrelated artifacts, records
    retries, and fails closed (a failed run keeps the previous valid run intact).
    The kernel validates the whole run after the agent; nothing here upgrades
    proof/source/evidence/grade/claim status. Run agents in any order or in
    parallel processes -- the final valid state is the same.
    """
    from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS, run_single_agent, skip_agent

    # Accept the hyphenated CLI spelling for the failure-pattern audit.
    agent_key = "failure_patterns" if agent in {"failure-patterns", "failure_patterns"} else agent
    if agent_key not in ON_DEMAND_AGENTS:
        raise typer.BadParameter(
            "--agent must be one of: examples, counterexamples, literature, failure-patterns"
        )
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")

    if skip:
        skip_agent(run, agent_key, reason=reason)
        typer.echo(f"Agent {agent_key}: skipped (contributes no evidence).")
        return
    if agent_key in CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS:
        validate_run(run)
        _require_claim_clear_for_cli(run)
    result = run_single_agent(
        run,
        agent_key,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    if result["status"] == "failed":
        typer.echo(
            f"Agent {agent_key}: FAILED and rolled back (previous valid run preserved). "
            f"Recorded as 'failed'. {result.get('error', '')}"
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"Agent {agent_key}: done. Wrote only its artifact; updated agent_run_status.yaml. "
        "The kernel re-validated the run (no status upgraded)."
    )


@app.command("run-agents")
def run_agents_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    agents: str | None = typer.Option(
        None,
        "--agents",
        help="Comma-separated subset (examples,counterexamples,literature,failure-patterns). Default: all.",
    ),
    serial: bool = typer.Option(
        False, "--serial", help="Force the serial path (default: parallel compute, serial commit)."
    ),
    max_workers: int = typer.Option(4, "--max-workers", help="Bound on concurrent lane compute()s."),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Run a BATCH of on-demand evidence agents (parallel compute, serial commit).

    The default path computes the context lanes concurrently off the run writer
    lock, then commits them serially on this thread in the deterministic order
    (counterexamples then examples; they share tests.yaml). It converges to the
    SAME valid run as running each agent serially -- the kernel re-validates the
    whole run after every commit and nothing here upgrades any status. This is
    opt-in: the fast-core orchestrate pass still defers evidence (stays
    PRELIMINARY) until you invoke a batch / run-agent.
    """
    from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS
    from espresso_proof.orchestration.evidence_coordinator import (
        run_evidence_batch,
        run_evidence_batch_auto,
    )

    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")

    if agents is None:
        requested = list(ON_DEMAND_AGENTS)
    else:
        requested = []
        for raw in agents.split(","):
            name = raw.strip()
            if not name:
                continue
            key = "failure_patterns" if name in {"failure-patterns", "failure_patterns"} else name
            if key not in ON_DEMAND_AGENTS:
                raise typer.BadParameter(
                    "--agents entries must be among: examples, counterexamples, literature, failure-patterns"
                )
            requested.append(key)
        if not requested:
            raise typer.BadParameter("--agents resolved to an empty set")

    # Same friendly precheck as run-agent: the claim must be clear before the
    # claim-clear-gated lanes run (run_single_agent also enforces this at commit).
    if any(a in CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS for a in requested):
        validate_run(run)
        _require_claim_clear_for_cli(run)

    if serial:
        # The serial path takes no pool; --max-workers is a no-op there.
        out = run_evidence_batch(
            run,
            requested,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )
    else:
        out = run_evidence_batch_auto(
            run,
            requested,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            max_workers=max_workers,
        )

    agent_results = out["agents"]
    failed = [a for a, r in agent_results.items() if r.get("status") == "failed"]
    for agent_key, result in agent_results.items():
        status = result.get("status")
        if status == "failed":
            typer.echo(f"Agent {agent_key}: FAILED and rolled back. {result.get('error', '')}")
        else:
            typer.echo(f"Agent {agent_key}: {status}.")
    path_word = "serial" if serial else "parallel"
    typer.echo(
        f"Batch ({path_word}) done over {len(agent_results)} agent(s); the kernel re-validated "
        "the run after every commit (no status upgraded). Per-lane timing in agent_events.jsonl."
    )
    if failed:
        raise typer.Exit(code=1)




def _preflight_agent_provider(
    *,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    network_check: bool,
) -> dict | None:
    if provider == "stub":
        return None
    if provider == "openai":
        report = openai_agent_diagnostic(
            model=model,
            api_key_env=api_key_env,
            network_check=network_check,
        )
    elif provider == "external":
        report = external_agent_diagnostic(
            model=model,
            command_env=external_agent_command_env,
            network_check=network_check,
        )
    else:
        raise RuntimeError(f"unsupported provider for preflight: {provider}")
    if report["status"] not in {"configured", "ready"}:
        message = report.get("error") or report.get("message") or f"{provider} provider is not ready."
        raise RuntimeError(str(message))
    return report


















@app.command("serve")
def serve_command(
    runs_root: Path = typer.Option(Path("."), "--runs-root", file_okay=False, help="Project-runs root (where projects/<name>/runs/<id> live or will be created)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost by default)."),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    provider: str = typer.Option("external", "--provider", help="Internal provider for the Codex adapter. Only external is supported."),
) -> None:
    """Start the local Product Workbench server (interactive browser UI).

    Type a claim in the browser, get a quick initial read + routes, then click
    Run Examples / Counterexamples / Literature, Continue a route, or Skip an
    agent — no terminal commands. Every action goes through the existing kernel
    path (validate_run); the server never upgrades proof/source/evidence status,
    confines every run id to the runs root, and never runs an arbitrary shell
    command from browser input.

    This is the canonical product entry. It uses the external Codex-agent path.
    """
    from espresso_proof.server import serve

    if provider != "external":
        raise typer.BadParameter("espresso serve supports only provider=external (Codex adapter)")
    serve(runs_root, host=host, port=port, provider=provider)










def _refresh_derived_views(run: Path) -> None:
    """Rebuild the proof-search graph + dashboard after a command appends scoped
    evidence, so the memo, graph, and dashboard stay coherent for one run.

    Only refreshes views that ALREADY exist (never fabricates new artifacts), and
    fails closed on the graph: if the rebuilt graph cannot pass validation, drop it
    and keep the run valid. This never upgrades any status -- the graph re-validates
    through the same kernel gate and only reflects evidence the kernel already
    earned.
    """
    from espresso_proof.kernel.subgoal_graph import build_subgoal_graph, remove_subgoal_graph
    from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

    if (run / "proof_goal_graph.yaml").exists():
        try:
            # Goal 57A: refresh the advisory ledger before the graph reads it.
            build_theorem_condition_ledger(run)
            build_subgoal_graph(run)
            validate_run(run)
        except Exception:  # noqa: BLE001 - advisory view must fail closed, not block
            remove_subgoal_graph(run)
            validate_run(run)
    if (run / "research_dashboard.html").exists():
        write_run_dashboard(run)
    _refresh_process_status(run)






























def _optional_artifact_path(run_dir: Path, optional_artifacts: dict, key: str) -> Path | None:
    rel_path = optional_artifacts.get(key)
    if not rel_path:
        return None
    path = run_dir / rel_path
    return path if path.exists() else None


def _focused_prover_loop_attempt(prover_loop: dict) -> dict:
    attempts = prover_loop.get("attempts", [])
    if not attempts:
        return {}
    selected_strategy_id = prover_loop.get("selected_strategy_id")
    candidates = [
        attempt
        for attempt in attempts
        if selected_strategy_id is None or attempt.get("strategy_id") == selected_strategy_id
    ]
    if not candidates:
        candidates = attempts
    return sorted(
        candidates,
        key=lambda attempt: (attempt.get("round_index", 0), attempt.get("attempt_id", "")),
    )[-1]


@app.command("choose-strategy")
def choose_strategy(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    strategy: str = typer.Option(..., "--strategy", help="Strategy id from strategy_brief.yaml, or 'none'."),
    effort: ExplorationMode = typer.Option(ExplorationMode.normal_pass, "--effort"),
    decision: ContinueDecision = typer.Option(ContinueDecision.continue_run, "--decision"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """Record a validated Human Choice and persist it as user-decision memory."""
    if not reason.strip():
        raise typer.BadParameter("reason cannot be empty")
    validate_run(run)
    # Cross-process writer lock (Goal 35B): the human-choice + manifest-state
    # write pair must not race a concurrent server resume on the same run.
    with run_writer_lock(run, action="strategy choice"):
        manifest_path = run / "run_manifest.yaml"
        manifest = read_yaml(manifest_path)
        artifacts = manifest["artifacts"]
        strategy_brief = read_yaml(run / artifacts["strategy_brief"])
        original_choice_text = (run / artifacts["human_choice"]).read_text(encoding="utf-8")
        original_manifest_text = manifest_path.read_text(encoding="utf-8")

        choice = {
            "claim_id": strategy_brief["claim_id"],
            "status": "selected",
            "selected_strategy": strategy,
            "effort": effort.value,
            "taste_judgment": reason.strip(),
            "continue_decision": decision.value,
            "human_confirmed": True,
        }
        HumanChoice.model_validate(choice)
        run_id = manifest["run_id"]
        entry = {
            "id": f"user_decision_{run_id}",
            "timestamp": datetime.now(UTC).isoformat(),
            "project": manifest["project_id"],
            "type": "user_decision",
            "claim_id": strategy_brief["claim_id"],
            "summary": (
                f"Human chose strategy id {strategy} with {effort.value}; "
                f"decision={decision.value}."
            ),
            "provenance": [
                f"projects/{manifest['project_id']}/runs/{run_id}/{artifacts['human_choice']}",
                f"projects/{manifest['project_id']}/runs/{run_id}/{artifacts['strategy_brief']}",
            ],
            "reusable_lesson": reason.strip(),
        }
        MemoryEntry.model_validate(entry)
        load_memory_index(run.parents[1])
        write_yaml(run / artifacts["human_choice"], choice)
        manifest["current_state"] = _state_for_human_decision(decision)
        write_yaml(manifest_path, manifest)
        try:
            validate_run(run)
        except Exception:
            (run / artifacts["human_choice"]).write_text(original_choice_text, encoding="utf-8")
            manifest_path.write_text(original_manifest_text, encoding="utf-8")
            raise

    project_dir = run.parents[1]
    memory_path = write_memory_entry(
        project_dir,
        entry,
        filename=f"{run_id}_human_choice.yaml",
        tags=[strategy, effort.value, decision.value],
    )
    typer.echo(
        f"Recorded human choice {strategy} with {effort.value} effort; "
        f"memory: {memory_path}"
    )


























@app.command("resolve-agent-gate")
def resolve_agent_gate(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    decision: str = typer.Option(..., "--decision"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """Record a human decision for llm_agent_run.yaml without upgrading status."""
    if decision not in {"continue", "revise", "stop"}:
        raise typer.BadParameter("decision must be continue, revise, or stop")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    # Cross-process writer lock (Goal 35B): gate resolution writes the loop +
    # initial round artifacts and must not race a concurrent resume.
    with run_writer_lock(run, action="gate resolution"):
        report = resolve_llm_agent_gate(run_dir=run, decision=decision, reason=reason)
        LLMAgentRunReport.model_validate(report)
        manifest = read_yaml(run / "run_manifest.yaml")
        artifacts = manifest["artifacts"]
        human_choice = read_yaml(run / artifacts["human_choice"])
        if decision == "continue":
            route_initial_reviewer_blockers_to_update_inbox(
                run_dir=run,
                agent_report=report,
                selected_strategy_id=human_choice.get("selected_strategy"),
            )
        loop_report = build_prover_loop_report(
            agent_report=report,
            strategy_brief=read_yaml(run / artifacts["strategy_brief"]),
            human_choice=human_choice,
            run_dir=run,
        )
        ProverLoopReport.model_validate(loop_report)
        manifest = read_yaml(run / "run_manifest.yaml")
        manifest.setdefault("optional_artifacts", {})["prover_loop"] = "prover_loop.yaml"
        manifest.setdefault("optional_artifacts", {})["prover_round_index"] = "prover_rounds/index.yaml"
        write_yaml(run / "prover_loop.yaml", loop_report)
        write_initial_prover_round_artifacts(
            run_dir=run,
            agent_report=report,
            loop_report=loop_report,
        )
        write_yaml(run / "run_manifest.yaml", manifest)
        refresh_final_report_disclosures(run)
        validate_run(run)
    typer.echo(f"Resolved LLM agent human gate as {decision}")


@app.command("write-proof")
def write_proof(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Goal 69: write a structured line_numbered_proof.yaml (each line has a statement
    + justification + dependencies) -- a real proof object, NOT a route. Advisory only;
    it is the ONLY artifact the agent-consensus mock check may seal."""
    from espresso_proof.orchestration.proof_writer import write_line_numbered_proof

    validate_run(run)
    _require_claim_clear_for_cli(run)
    with run_writer_lock(run, action="line-numbered proof writer"):
        record = write_line_numbered_proof(
            run,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )
    _refresh_process_status(run)
    typer.echo(
        f"Wrote line_numbered_proof.yaml ({len(record['lines'])} lines, "
        f"kind={record['proof_kind']}, advisory only)."
    )


@app.command("seal-consensus")
def seal_consensus(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Run the three proof reviewers over the line_numbered_proof and SEAL the run with
    the agent-consensus mock check (proof_language_status=agent_consensus_mock_checked)
    iff all three find no fatal flaw and cite proof line_ids. Fails closed if there is
    no line-numbered proof (route-only). Amber/advisory, NOT machine-verified."""
    from espresso_proof.orchestration.agent_consensus_seal import (
        NoLineNumberedProofError,
        seal_agent_consensus,
    )

    validate_run(run)
    _require_claim_clear_for_cli(run)
    with run_writer_lock(run, action="agent consensus seal"):
        try:
            record = seal_agent_consensus(
                run,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
            )
        except NoLineNumberedProofError as exc:
            typer.echo(f"Not sealed: {exc}")
            _refresh_process_status(run)
            raise typer.Exit(code=1) from exc
        except RuntimeError as exc:
            typer.echo(f"Not sealed: {exc}")
            try:
                from espresso_proof.orchestration.proof_gap_resolver import resolve_proof_gaps

                report = resolve_proof_gaps(
                    run,
                    provider=provider,
                    model=model,
                    api_key_env=api_key_env,
                    external_agent_command_env=external_agent_command_env,
                )
                typer.echo(
                    "Generated proof-gap repair plan: "
                    f"{len(report.get('candidates', []))} candidate(s), "
                    f"{len(report.get('repair_plan', []))} planned action(s)."
                )
            except Exception as repair_exc:  # noqa: BLE001 - seal still fails closed.
                typer.echo(f"Repair plan generation also failed: {repair_exc}")
            _refresh_process_status(run)
            raise typer.Exit(code=1) from exc
    _refresh_process_status(run)
    if record["consensus"]:
        typer.echo(
            "Sealed: agent_consensus_mock_checked (amber, NOT machine-verified). "
            "Reviewer residual risks may remain; see agent_consensus_review.yaml."
        )
    else:
        blocking = [e["reviewer_id"] for e in record["entries"] if e["verdict"] != "no_fatal_flaw_found"]
        typer.echo(f"Not sealed: no unanimous consensus (blocking reviewers: {blocking}).")


























def _validate_seed_files() -> None:
    WorkflowSpec.model_validate(
        generate_workflow_spec("Hodge atom mirror symmetry", project_name="generated_hodge")
    )
    WorkflowSpec.model_validate(
        generate_workflow_spec("flat coherent base change", project_name="generated_base_change")
    )
    AgentContracts.model_validate(default_agent_contracts("scratch"))


def _next_feedback_id(existing: list[dict]) -> str:
    return f"feedback_{len(existing) + 1:03d}"


def _default_feedback_actions(labels: list[str]) -> list[str]:
    actions: set[str] = set()
    for label in labels:
        if label == "dangerous":
            actions.add("prioritize_counterexample_testing")
        elif label == "needs_source":
            actions.add("run_referencing")
        elif label == "needs_computation":
            actions.add("run_evidence_auditor")
        elif label in {"misleading", "too_easy", "irrelevant"}:
            actions.add("demote_ranking")
        elif label in {"interesting", "canonical", "favorite"}:
            actions.add("promote_ranking")
        if label == "favorite":
            actions.add("save_to_memory")
    return sorted(actions)


def _state_for_human_decision(decision: ContinueDecision) -> str:
    if decision == ContinueDecision.continue_run:
        return "HUMAN_CHOICE_CONTINUE_SELECTED"
    if decision == ContinueDecision.revise:
        return "HUMAN_CHOICE_REVISE_SELECTED"
    return "HUMAN_CHOICE_STOP_SELECTED"


def _strategy_title(strategy_brief: dict, strategy_id: str) -> str:
    if strategy_id == "none":
        return "none"
    for strategy in strategy_brief.get("top_strategies", []):
        if strategy.get("id") == strategy_id:
            return strategy.get("title") or strategy_id
    return strategy_id


def _assert_fixture_expectations(run_dir: Path, expected: dict) -> None:
    required = expected.get("required", {})
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    idea_grade = read_yaml(run_dir / "idea_grade.yaml")
    counterexamples = read_yaml(run_dir / "counterexamples.yaml")
    computations = read_yaml(run_dir / "computation_checks.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    strategy_brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_synthesis = read_yaml(run_dir / "strategy_synthesis.yaml")
    tests = read_yaml(run_dir / "tests.yaml")
    final_report = read_text(run_dir / "final_report.md").lower()

    if "idea_status" in required and idea_grade["status"] not in required["idea_status"]:
        raise KernelValidationError(
            f"idea status {idea_grade['status']!r} not in {required['idea_status']!r}"
        )

    for phrase in required.get("forbidden_final_report_phrases", []):
        if phrase in final_report:
            raise KernelValidationError(f"final report contains forbidden phrase {phrase!r}")

    for candidate in required.get("required_counterexample_candidates", []):
        if candidate not in idea_grade.get("counterexample_candidates", []):
            raise KernelValidationError(f"missing counterexample candidate {candidate!r}")

    for test_id in required.get("required_survived_tests", []):
        if test_id not in idea_grade.get("survived_tests", []):
            raise KernelValidationError(f"missing survived evidence test {test_id!r}")

    for test_id in required.get("required_failed_tests", []):
        if test_id not in idea_grade.get("failed_tests", []):
            raise KernelValidationError(f"missing failed evidence test {test_id!r}")

    if "meaningful_checked_support" in required:
        support = required["meaningful_checked_support"]
        allowed_statuses = set(support.get("statuses", ["checked", "source_checked"]))
        excluded = set(support.get("exclude_check_ids", []))
        required_check_ids = set(support.get("required_check_ids", []))
        eligible_checks = [
            check
            for check in computations.get("checks", [])
            if check.get("id") not in excluded and check.get("status") in allowed_statuses
        ]
        eligible_ids = {check.get("id") for check in eligible_checks}
        if len(eligible_checks) < support.get("min_count", 1):
            raise KernelValidationError("missing meaningful checked/source_checked support")
        missing_check_ids = required_check_ids.difference(eligible_ids)
        if missing_check_ids:
            raise KernelValidationError(
                f"missing required support checks {sorted(missing_check_ids)}"
            )

    if "counterexample_test" in required:
        expected_test = required["counterexample_test"]
        actual = _find_by_id(counterexamples.get("tests", []), expected_test["id"])
        for key, value in expected_test.items():
            if actual.get(key) != value:
                raise KernelValidationError(
                    f"counterexample test {expected_test['id']} expected {key}={value!r}, got {actual.get(key)!r}"
                )

    statuses = {
        ref.get("status")
        for ref in source_grounding.get("source_refs", [])
    } | {step.get("status") for step in source_grounding.get("steps", [])}
    for status in required.get("required_source_status", []):
        if status not in statuses:
            raise KernelValidationError(f"missing source status {status!r}")
    if required.get("source_backed_allowed") is False and "source_backed" in statuses:
        raise KernelValidationError("source_backed was not allowed for this fixture")

    if "human_feedback" in required:
        _assert_human_feedback_expectations(
            run_dir,
            manifest,
            required["human_feedback"],
            idea_grade,
            source_grounding,
        )

    computation_statuses = {check.get("status") for check in computations.get("checks", [])}
    expected_computation_statuses = set(required.get("computation_status", []))
    if expected_computation_statuses and not computation_statuses.intersection(expected_computation_statuses):
        raise KernelValidationError(
            f"missing expected computation statuses {sorted(expected_computation_statuses)}"
        )

    if required.get("no_survived_or_failed_from_unchecked_examples"):
        if idea_grade.get("survived_tests") or idea_grade.get("failed_tests"):
            raise KernelValidationError("unchecked examples entered survived/failed idea evidence")
        for test in tests.get("tests", []):
            if test.get("evidence_status") in {"human_needed", "inconclusive"} and test.get("result") in {
                "survived",
                "failed",
            }:
                raise KernelValidationError("unchecked examples entered survived/failed test evidence")

    if not strategy_brief.get("top_strategies"):
        raise KernelValidationError("strategy_brief must contain at least one top strategy")
    synthesis_ids = {route.get("id") for route in strategy_synthesis.get("routes", [])}
    brief_ids = {route.get("id") for route in strategy_brief.get("top_strategies", [])}
    if not brief_ids.issubset(synthesis_ids):
        raise KernelValidationError("strategy_brief includes a strategy not produced by synthesis")

    for strategy_id in required.get("required_strategy_ids", []):
        if strategy_id not in brief_ids:
            raise KernelValidationError(f"missing strategy brief route {strategy_id!r}")

    if required.get("strategy_risk_required"):
        for route in strategy_brief.get("top_strategies", []):
            if not str(route.get("main_risk", "")).strip():
                raise KernelValidationError(f"strategy {route.get('id')!r} is missing main_risk")

    for status in required.get("required_strategy_status", []):
        if status not in {route.get("status") for route in strategy_brief.get("top_strategies", [])}:
            raise KernelValidationError(f"missing strategy status {status!r}")

    for phrase in required.get("forbidden_strategy_phrases", []):
        lowered = phrase.lower()
        for route in strategy_brief.get("top_strategies", []):
            route_text = " ".join(str(value) for value in route.values()).lower()
            if lowered in route_text:
                raise KernelValidationError(f"strategy brief contains forbidden phrase {phrase!r}")


def _find_by_id(items: list[dict], item_id: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise KernelValidationError(f"missing item id {item_id!r}")


def _assert_human_feedback_expectations(
    run_dir: Path,
    manifest: dict,
    required: dict,
    idea_grade: dict,
    source_grounding: dict,
) -> None:
    optional = manifest.get("optional_artifacts", {})
    feedback_name = optional.get("human_example_feedback")
    if not feedback_name:
        raise KernelValidationError("expected human_example_feedback optional artifact")
    feedback = read_yaml(run_dir / feedback_name)
    items = feedback.get("feedback", [])
    if not items:
        raise KernelValidationError("expected at least one human feedback item")
    target_id = required.get("target_id")
    target_type = required.get("target_type")
    labels = set(required.get("labels", []))
    matching = [
        item
        for item in items
        if (target_id is None or item.get("target_id") == target_id)
        and (target_type is None or item.get("target_type") == target_type)
    ]
    if not matching:
        raise KernelValidationError("missing expected human feedback target")
    if labels and not any(labels.issubset(set(item.get("labels", []))) for item in matching):
        raise KernelValidationError(f"missing expected human feedback labels {sorted(labels)}")
    if required.get("summary_required"):
        summary_name = optional.get("example_feedback_summary")
        if not summary_name:
            raise KernelValidationError("expected example_feedback_summary optional artifact")
        summary = read_yaml(run_dir / summary_name)
        summarized = set(summary.get("summarized_from", []))
        feedback_ids = {item.get("feedback_id") for item in matching}
        if not feedback_ids.intersection(summarized):
            raise KernelValidationError("feedback summary does not cite expected feedback")
    if required.get("no_status_upgrade"):
        if idea_grade.get("status") == "promising":
            raise KernelValidationError("human feedback upgraded idea status to promising")
        if idea_grade.get("survived_tests") or idea_grade.get("failed_tests"):
            raise KernelValidationError("human feedback changed survived/failed evidence")
        source_statuses = {
            ref.get("status")
            for ref in source_grounding.get("source_refs", [])
        } | {step.get("status") for step in source_grounding.get("steps", [])}
        if "source_backed" in source_statuses:
            raise KernelValidationError("human feedback upgraded source status")


# Register + re-export the developer / evaluation / verify / legacy command
# groups on the shared ``app`` (flat command names; behavior unchanged). The
# re-export keeps `from espresso_proof.cli import <command>` working.
# See docs/engineering/architecture-spine.md.
from espresso_proof.cli_verify import (  # noqa: E402,F401
    verify_exact_modexp_command,
    verify_finite_groups_command,
    verify_gl2_f3_solvable_in_run_command,
    verify_identity_command,
    verify_identity_in_run_command,
    verify_ring_identity_in_run_command,
    verify_matrix_rank_in_run_command,
    verify_tensor_left_exact_counterexample_in_run_command,
    auto_wire_verifications_command,
    verify_sources,
    verify_computations_command,
)
from espresso_proof.cli_eval import (  # noqa: E402,F401
    judge_command,
    benchmark_command,
    real_math_capability_command,
    real_agent_smoke_command,
    calibration,
    scorecard,
    eval,
)
from espresso_proof.cli_dev import (  # noqa: E402,F401
    export_schemas_command,
    init,
    new_run,
    agent_doctor,
    classify_failures_command,
    build_subgoal_graph_command,
    memory_context_command,
    audit_patterns_command,
    retrieve_sources_command,
    retrieve_autonomous_command,
    add_bank_item_command,
    materialize_banks_command,
    bank_dashboard_command,
    run_dashboard_command,
    run_external_tool_command,
    show_pattern_bank_command,
    memo,
    review_checklist_command,
    inspect,
    discover_literature,
    ingest_pdf,
    extract_locators_command,
    audit_theorem_applicability_command,
    review_theorem_applicability_command,
    audit_evidence_command,
    review_literature_agent,
    retrieve_and_ground_command,
    literature_scout_command,
    run_llm_agents,
    resolve_proof_gaps_command,
    run_prover_loop_round,
    feedback_example,
    summarize_feedback,
    generate_agent,
    improve_workflow,
)
from espresso_proof.cli_legacy import (  # noqa: E402,F401
    critique_sketch,
    run_flow,
)
