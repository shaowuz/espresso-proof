from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _guard_windows_run_path_length,
    _refresh_process_status,
    _require_claim_clear_for_cli,
    _resolve_front_half_mode,
    _state_for_human_decision,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.literature.literature_scout import run_literature_scout
from espresso_proof.literature.retrieval_grounding import retrieve_and_ground
from espresso_proof.agents.llm_agents import resolve_agent_gate as resolve_llm_agent_gate
from espresso_proof.agents.llm_agents import build_client as build_llm_client
from espresso_proof.agents.llm_agents import build_prover_loop_report
from espresso_proof.orchestration.orchestrator import (
    auto_select_strategy,
    orchestrate_run,
    resume_run,
)
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.agents.llm_agents import route_initial_reviewer_blockers_to_update_inbox
from espresso_proof.agents.llm_agents import write_initial_prover_round_artifacts
from espresso_proof.orchestration.run_dashboard import write_run_dashboard
# NOTE: literature_retrieval.retrieve_and_ground (corpus_dir/query, older
# retrieve-sources --ground path) is a DIFFERENT function from
# Retrieval_grounding.retrieve_and_ground (candidate/local_corpus). Alias
# This one so it does not shadow the import above (which the
# `retrieve-and-ground` command depends on).
from espresso_proof.verification.proof_verification import (
    auto_wire_scoped_verifications,
)
from espresso_proof.claim.claim_clear import CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS
from espresso_proof.kernel.surface_warnings import (
    refresh_final_report_disclosures,
)
from espresso_proof.memory.memory import load_memory_index, write_memory_entry
from espresso_proof.literature.source_grounding_stage import prepare_source_grounding
from espresso_proof.verification.verifier import audit_evidence
from espresso_proof.kernel.models import (
    ContinueDecision,
    ExplorationMode,
    HumanChoice,
    LLMAgentRunReport,
    MemoryEntry,
    ProverLoopReport,
)
from espresso_proof.kernel.validators import KernelValidationError, validate_run









def _provider_was_explicit() -> bool:
    """True when the caller actually typed --provider on this invocation."""

    import sys

    return any(a == "--provider" or a.startswith("--provider=") for a in sys.argv)


def _recorded_run_provider(run_dir: Path) -> str | None:
    """The provider a run was created with, as recorded in its own artifacts.

    Read-only and fail-soft: a run whose artifacts do not name a provider (or
    cannot be read) returns None and the caller keeps whatever was passed.
    """

    from espresso_proof.kernel.io import read_yaml

    for name in ("llm_agent_run.yaml", "front_half_run.yaml"):
        path = Path(run_dir) / name
        if not path.exists():
            continue
        try:
            recorded = (read_yaml(path) or {}).get("provider")
        except Exception:
            continue
        if isinstance(recorded, str) and recorded in {"stub", "openai", "external"}:
            return recorded
    return None


@app.command("orchestrate")
def orchestrate(
    input: Path | None = typer.Option(None, "--input", "-i", dir_okay=False),
    project: str = typer.Option("demo", "--project", "-p"),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("auto", "--front-half"),
    token_budget: int | None = typer.Option(
        None,
        "--token-budget",
        help=(
            "Total model tokens you are willing to spend on this run. Raises the "
            "effort tiers a route may be approved for; without it each route "
            "offers only the tier the grader estimated. Never spends on its own."
        ),
    ),
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
    auto_continue: bool = typer.Option(False, "--auto-continue", help="Continue past a PENDING prover human gate in the same process: machinery selects the top-scored strategy, recorded honestly (selected_by=auto, human_confirmed=false). Earlier human questions (claim clarification, ask_human) still stop the run. With --resume, replaces --select-strategy."),
) -> None:
    """Run the Core Flow end-to-end for an arbitrary claim, stopping at the human gate.

    Fresh run: provide --input. The orchestrator scaffolds the run, dispatches the
    front-half agents, runs literature + the live Prover/Reviewers, validates after
    each stage, and stops at the human gate — or, with --auto-continue, keeps going
    past a pending prover gate with an honestly-recorded automated selection.
    Resume: provide --resume <run_dir> with --select-strategy (and --effort) to
    record the human choice — or --auto-continue for the automated selection — and
    render the concise memo. The kernel validates every stage; nothing here
    upgrades proof/source/evidence status, and an automated choice can never claim
    human confirmation.
    """
    if resume is not None:
        if select_strategy and auto_continue:
            raise typer.BadParameter(
                "--select-strategy records a HUMAN choice; drop it or drop --auto-continue"
            )
        if not select_strategy and not auto_continue:
            raise typer.BadParameter(
                "--resume requires --select-strategy (or --auto-continue for an "
                "automated, honestly-recorded selection)"
            )
        if provider not in {"stub", "openai", "external"}:
            raise typer.BadParameter("provider must be stub, openai, or external")
        validate_run(resume)
        # A run records the provider that produced it. --provider defaults to
        # "stub", so resuming a real-agent run without repeating the flag used to
        # silently downgrade it: the resumed round ran on deterministic stubs,
        # recorded model gpt-4.1, made no model call, and still appended a round
        # whose reviewer objections looked like progress. Inherit instead, and
        # refuse an explicit conflict rather than picking a winner.
        recorded_provider = _recorded_run_provider(resume)
        if recorded_provider:
            explicit = _provider_was_explicit()
            if not explicit:
                provider = recorded_provider
            elif provider != recorded_provider:
                raise typer.BadParameter(
                    f"--provider {provider!r} conflicts with the provider this run "
                    f"was created with ({recorded_provider!r}). Resuming a run under "
                    "a different provider would mix real and stub evidence in one "
                    "run; start a new run instead."
                )
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
        if auto_continue:
            choice = auto_select_strategy(run_dir=resume)
            select_strategy = choice["selected_strategy"]
            if effort is None:
                effort = choice["effort"]
            typer.echo(
                f"Auto-continue: machinery selected strategy {select_strategy!r} "
                f"(score={choice['score']}, effort={effort}); recorded honestly as "
                "selected_by=auto, human_confirmed=false"
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
            selected_by="auto" if auto_continue else "human",
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
        token_budget=token_budget,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        literature_mode=literature_mode,
        fast=fast,
        auto_continue=auto_continue,
        rounds=rounds,
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
    auto_report = result.get("auto_continue")
    if auto_report and auto_report.get("attempted"):
        typer.echo(
            f"Auto-continue: machinery selected strategy "
            f"{auto_report['selected_strategy']!r} (effort={auto_report['effort']}, "
            f"decision={auto_report['decision']}); recorded honestly as "
            "selected_by=auto, human_confirmed=false"
        )
        typer.echo(result["memo"])
        return
    if auto_report is not None:
        # honest skip: an earlier human question stopped the run; machinery
        # must not answer it.
        typer.echo(f"Auto-continue skipped: {auto_report['reason']}")
    elif auto_continue:
        # the run returned before the prover gate (e.g. claim clarification);
        # that question is a human's to answer.
        typer.echo(
            "Auto-continue skipped: the run stopped at an earlier human "
            "question, which machinery must not answer"
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
    # Cross-process writer lock: the human-choice + manifest-state
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
            # Explicit provenance: a typed choose-strategy command is a real
            # human at the terminal. Never rely on the model default.
            "selected_by": "human",
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
    # Cross-process writer lock: gate resolution writes the loop +
    # initial round artifacts and must not race a concurrent resume.
    with run_writer_lock(run, action="gate resolution"):
        manifest = read_yaml(run / "run_manifest.yaml")
        artifacts = manifest["artifacts"]
        human_choice = read_yaml(run / artifacts["human_choice"])
        # A human-confirmed gate resolution on top of an auto-selected strategy
        # would trip the kernel's choice/gate provenance cross-check. Refuse up
        # front with a usable message instead of failing validate_run afterwards.
        if human_choice.get("selected_by") == "auto":
            raise typer.BadParameter(
                "this run's strategy was auto-selected (human_choice selected_by='auto'); "
                "a human gate resolution cannot be recorded on top of it — resume the run "
                "with --auto-continue instead"
            )
        report = resolve_llm_agent_gate(
            run_dir=run, decision=decision, reason=reason, resolved_by="human"
        )
        LLMAgentRunReport.model_validate(report)
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
    """Write a structured line_numbered_proof.yaml (each line has a statement
    + justification + dependencies) -- a real proof object, NOT a route. Advisory only;
    it is the ONLY artifact the agent-consensus mock check may seal."""
    from espresso_proof.orchestration.proof_writer import write_line_numbered_proof

    validate_run(run)
    _require_claim_clear_for_cli(run)
    with run_writer_lock(run, action="line-numbered proof writer"):
        try:
            record = write_line_numbered_proof(
                run,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
            )
        except (RuntimeError, ValueError, KernelValidationError) as exc:
            # Same friendly fail-closed surface as seal-consensus: covers the
            # external agent dying/timing out (RuntimeError incl.
            # ExternalAgentTimeoutError), a schema-invalid writer reply
            # (pydantic ValidationError ⊂ ValueError), and validate_run
            # failures. Anything else is a real bug and keeps its traceback.
            typer.echo(f"No proof written (failed closed; run rolled back): {exc}")
            _refresh_process_status(run)
            raise typer.Exit(code=1) from exc
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


# Register the developer / evaluation / verify / legacy command groups on the
# shared ``app``: Typer registration is an import side effect, so bare module
# imports suffice (import a command from its defining module if you need the
# function itself). See espresso_proof.orchestration.orchestrator for the
# runtime dataflow.
import espresso_proof.cli_verify  # noqa: E402,F401
import espresso_proof.cli_eval  # noqa: E402,F401
import espresso_proof.cli_dev  # noqa: E402,F401
import espresso_proof.cli_legacy  # noqa: E402,F401


if __name__ == "__main__":
    app()
