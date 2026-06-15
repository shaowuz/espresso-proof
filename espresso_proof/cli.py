from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from .contracts import DEFAULT_AGENT_FLOW, default_agent_contracts
from .descriptions import (
    describe_grade,
    describe_proof_language,
    evidence_phrase,
    judgment_tier_phrase,
    objection_phrase,
)
from .io import read_text, read_yaml, write_text, write_yaml
from .literature_discovery import discover_literature as run_literature_discovery
from .literature_review_agent import ReviewSourceSpec, run_literature_review_agent
from .literature_scout import run_literature_scout
from .retrieval_grounding import retrieve_and_ground
from .llm_agents import append_prover_loop_round
from .llm_agents import resolve_agent_gate as resolve_llm_agent_gate
from .llm_agents import build_client as build_llm_client
from .llm_agents import build_prover_loop_report
from .front_half_agents import run_front_half_agent_pass
from .orchestrator import orchestrate_run, resume_run
from .prover_loop_driver import continue_prover_loop
from .run_lock import run_writer_lock
from .llm_agents import external_agent_diagnostic
from .llm_agents import openai_agent_diagnostic
from .llm_agents import route_initial_reviewer_blockers_to_update_inbox
from .llm_agents import run_llm_agent_pass
from .llm_agents import write_initial_prover_round_artifacts
from .failure_classifier import classify_failures
from .failure_pattern_bank import audit_failure_patterns, seed_failure_pattern_bank
from .autonomous_retrieval import run_autonomous_retrieval
from .bank_dashboard import render_bank_dashboard
from .run_dashboard import render_run_dashboard, write_run_dashboard
from .research_bank import (
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
from .literature_retrieval import retrieve_and_ground as retrieve_and_ground_from_corpus
from .literature_retrieval import retrieve_sources
from .mathematical_judgment import judge_run
from .memory_context import build_memory_context
from .subgoal_graph import build_subgoal_graph
from .finite_group_verifier import verify_identity_on_table
from .proof_verification import (
    auto_wire_scoped_verifications,
    verify_finite_groups,
    verify_identity_table_in_run,
    verify_matrix_rank_in_run,
    verify_ring_identity_in_run,
)
from .symbolic_identity import SUPPORTED_RING_IDENTITIES
from .benchmark import run_benchmark
from .calibration import run_calibration
from .claim_clear import (
    CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS,
    ClaimClearBlockedError,
    ensure_claim_clear_for_downstream,
)
from .scorecard import render_scorecard_markdown, run_scorecard
from .memo import build_concise_memo
from .proof_language import scrub_proof_language
from .reviewer_checklist import review_checklist
from .memory import load_memory_index, summarize_memory, write_memory_entry
from .mock_pipeline import run_mock_critique
from .pdf_ingestion import (
    extract_candidate_locators,
    extract_source_text,
    ingest_source_for_theorems,
    read_source_bytes,
)
from .source_verification import ground_run
from .source_grounding_stage import prepare_source_grounding
from .external_tool_runner import (
    DEFAULT_TIMEOUT_SECONDS,
    FAKE_TOOLS,
    UnknownToolError,
    run_and_record_external_tool,
    run_completed_cleanly,
)
from .external_tool_adapters import run_exact_modexp_adapter
from .theorem_applicability import audit_theorem_applicability
from .verifier import audit_evidence, verify_computation_checks
from .models import (
    AdmissionCheck,
    AgentContracts,
    ContinueDecision,
    ExampleBank,
    ExampleFeedbackSummary,
    ExplorationMode,
    HumanChoice,
    HumanExampleFeedback,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    MemoryEntry,
    ProverLoopReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
    WorkflowSpec,
)
from .validators import KernelValidationError, validate_run
from .workflow_generator import generate_workflow_spec

app = typer.Typer(help="Espresso Proof local kernel.")


def _require_claim_clear_for_cli(run: Path) -> None:
    try:
        ensure_claim_clear_for_downstream(run, require_recorded_clear_gate=True)
    except ClaimClearBlockedError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def init(project: str = "hodge_atoms") -> None:
    """Initialize the local project directories used by the P0 kernel."""
    project_dir = Path("projects") / project
    (project_dir / "runs").mkdir(parents=True, exist_ok=True)
    (project_dir / "memory").mkdir(parents=True, exist_ok=True)
    contracts = default_agent_contracts(project)
    AgentContracts.model_validate(contracts)
    contract_path = project_dir / "agent_contracts.yaml"
    if not contract_path.exists():
        write_yaml(contract_path, contracts)
    typer.echo(f"Initialized {project_dir}")


@app.command("critique-sketch")
def critique_sketch(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    mode: str = typer.Option("mock", "--mode"),
) -> None:
    """Run a schema-checked mock critique pass."""
    if mode != "mock":
        raise typer.BadParameter("P0 only supports --mode mock")
    run_dir = run_mock_critique(project, input, Path("."))
    typer.echo(f"Wrote run artifacts to {run_dir}")


@app.command("new-run")
def new_run(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    output_root: Path = typer.Option(Path("."), "--output-root", file_okay=False),
) -> None:
    """Scaffold a kernel-valid 'unclear / needs_human' run for an arbitrary claim.

    Any claim text becomes a minimal, honest run (claim text embedded, grade
    unclear, no source_backed, needs_human). This unblocks arbitrary new claims
    so front-half agents can enrich a real run.
    """
    run_dir = run_mock_critique(project, input, output_root)
    validate_run(run_dir)
    typer.echo(f"Wrote run artifacts to {run_dir}")


@app.command("orchestrate")
def orchestrate(
    input: Path | None = typer.Option(None, "--input", "-i", dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("stub", "--front-half"),
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
    if front_half not in {"mock", "stub", "external"}:
        raise typer.BadParameter("front-half must be mock, stub, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")

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
    from .agent_runner import ON_DEMAND_AGENTS, run_single_agent, skip_agent

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


@app.command("run-flow")
def run_flow(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    literature_mode: str = typer.Option("local", "--literature-mode"),
    literature_provider: list[str] | None = typer.Option(None, "--literature-provider"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("mock", "--front-half"),
    model: str = typer.Option("gpt-4.1", "--model"),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
    agent_network_preflight: bool = typer.Option(
        False,
        "--agent-network-preflight/--no-agent-network-preflight",
    ),
    output_root: Path = typer.Option(Path("."), "--output-root", file_okay=False),
) -> None:
    """Compatibility command: run the end-to-end critique flow to the human gate.

    ``run-flow`` is a thin compatibility wrapper. ``orchestrate`` is the canonical
    runtime path; this delegates to the SAME ``orchestrate_run`` lifecycle so the
    two entrypoints can never drift in stage sequencing or safety behavior. It
    cannot upgrade proof/source/evidence status or create human_confirmed=True.
    """
    if literature_mode not in {"local", "live"}:
        raise typer.BadParameter("literature-mode must be local or live")
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    if front_half not in {"mock", "stub", "external"}:
        raise typer.BadParameter("front-half must be mock, stub, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")

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
        literature_providers=literature_provider,
        max_references=max_references,
        agent_network_preflight=agent_network_preflight,
    )

    typer.echo(f"Wrote run artifacts to {result['run_dir']}")
    typer.echo(f"Literature Discovery: {literature_mode}")
    typer.echo(f"Front-half agents: mode={front_half}")
    typer.echo(f"LLM agents: provider={provider}, reviewer_set={reviewer_set}")
    typer.echo(f"Human gate: {result['human_gate'].get('status', 'unknown')}")


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


@app.command("agent-doctor")
def agent_doctor(
    provider: str = typer.Option("openai", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
    network_check: bool = typer.Option(False, "--network-check/--no-network-check"),
    timeout: int = typer.Option(30, "--timeout", min=1, max=300),
) -> None:
    """Check whether the real LLM provider path is configured and callable."""
    if provider == "openai":
        report = openai_agent_diagnostic(
            model=model,
            api_key_env=api_key_env,
            network_check=network_check,
            timeout=timeout,
        )
    elif provider == "external":
        report = external_agent_diagnostic(
            model=model,
            command_env=external_agent_command_env,
            network_check=network_check,
            timeout=timeout,
        )
    else:
        raise typer.BadParameter("agent-doctor supports provider=openai or provider=external")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@app.command("classify-failures")
def classify_failures_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Classify reviewer objections / failed tests into typed failure categories.

    Advisory routing only: writes failure_classification.yaml and never upgrades
    proof/source/evidence status. claim_refuted requires an admissible
    all-hypotheses counterexample.
    """
    report = classify_failures(run)
    validate_run(run)
    categories = ", ".join(item["category"] for item in report["classifications"]) or "none"
    typer.echo(f"Classified {len(report['classifications'])} failure(s): {categories}")
    typer.echo(f"-> {run / 'failure_classification.yaml'}")


@app.command("judge")
def judge_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Record conservative mathematical risk judgments for a run (advisory only).

    Writes mathematical_judgment.yaml and re-validates. The layer never upgrades
    proof/source/evidence status: it can only warn, request more evidence,
    downgrade confidence, or ask for human review. The kernel validators remain
    the only gate that earns any status.
    """
    report = judge_run(run)
    validate_run(run)
    kinds = ", ".join(sorted({item["kind"] for item in report["findings"]})) or "none"
    typer.echo(f"Recorded {len(report['findings'])} judgment finding(s): {kinds}")
    typer.echo(f"-> {run / 'mathematical_judgment.yaml'}")


@app.command("build-subgoal-graph")
def build_subgoal_graph_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Build the durable subgoal-graph proof-SEARCH state for a run (advisory only).

    Writes proof_goal_graph.yaml and re-validates. The graph is search-management
    state, NOT proof evidence: it can never set or upgrade idea_grade /
    proof_language / source / evidence / claim status, never claim a theorem is
    proved, and a node may carry source_supported / scoped_checked only when it
    references support the kernel already earned. The validators remain the gate.
    """
    from .theorem_condition_ledger import build_theorem_condition_ledger

    # Goal 57A: ensure the advisory theorem-condition ledger exists before the
    # graph reads it (idempotent; empty ledger when there is no llm_agent_run).
    build_theorem_condition_ledger(run)
    graph = build_subgoal_graph(run)
    validate_run(run)
    summary = graph["summary"]
    typer.echo(
        f"Built subgoal graph with {len(graph['nodes'])} node(s): "
        f"{summary['open']} open, {summary['sketched']} sketched, "
        f"{summary['source_supported']} reference-supported, "
        f"{summary['finite_or_computation_checked']} finite/computational-checked, "
        f"{summary['blocked']} blocked."
    )
    typer.echo(f"-> {run / 'proof_goal_graph.yaml'}")


@app.command("memory-context")
def memory_context_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Match prior project memory to this run's claim (advisory only).

    Writes memory_context.yaml and re-validates. Memory can only influence route
    ranking / time allocation / what to test first (avoid / retest /
    request_source / prioritize / ask_human). It never sets source_backed, checked
    evidence, proof status, or a refutation, and never overrides current artifacts.
    """
    report = build_memory_context(run)
    validate_run(run)
    typer.echo(
        f"Matched {len(report['matched_entries'])} prior-memory entr(ies) "
        "(advisory; never changes status)."
    )
    typer.echo(f"-> {run / 'memory_context.yaml'}")


@app.command("audit-patterns")
def audit_patterns_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Match a run's signals against the math failure pattern bank (advisory only).

    Writes failure_pattern_audit.yaml and re-validates. A pattern match never
    proves falsehood, never refutes the claim, and never upgrades
    proof/source/evidence status. candidate=warn; audited=ranking/time hint;
    hard=points at an existing kernel validator (no new blocking added).
    """
    report = audit_failure_patterns(run)
    validate_run(run)
    objections = "; ".join(sorted({objection_phrase(m["tier"]) for m in report["matches"]})) or "none"
    typer.echo(f"Matched {len(report['matches'])} failure pattern(s) [{objections}]")
    typer.echo(f"-> {run / 'failure_pattern_audit.yaml'}")


@app.command("retrieve-sources")
def retrieve_sources_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    corpus: Path | None = typer.Option(None, "--corpus", help="Local source corpus directory."),
    query: str | None = typer.Option(None, "--query"),
    provider: list[str] | None = typer.Option(None, "--provider", help="local_corpus | arxiv"),
    ground: bool = typer.Option(False, "--ground", help="Hand the top candidate to source grounding."),
) -> None:
    """Retrieve CANDIDATE sources for a run (advisory). With --ground, hand the top
    local-corpus candidate to the EXISTING grounding pipeline.

    Retrieval never sets source_backed; only Source Grounding (exact locator +
    hypothesis match + attribution) earns it. A failed/empty search fails closed.
    """
    _require_claim_clear_for_cli(run)
    providers = provider or ["local_corpus"]
    if ground:
        if corpus is None:
            raise typer.BadParameter("--ground requires --corpus")
        result = retrieve_and_ground_from_corpus(run, corpus_dir=corpus, query=query)
        validate_run(run)
        if result.get("grounded"):
            typer.echo("Retrieved a proposed source and handed it to source grounding "
                       "(a reference supports a step only with exact locator, hypothesis match, and attribution).")
        else:
            typer.echo(f"Retrieval found no groundable candidate ({result.get('reason')}); fail closed.")
        return
    report = retrieve_sources(run, corpus_dir=corpus, query=query, providers=providers)
    validate_run(run)
    typer.echo(f"Retrieved {len(report['hits'])} proposed source(s) [providers: {', '.join(providers)}]")
    for hit in report["hits"][:5]:
        typer.echo(f"- {hit['id']} ({evidence_phrase(hit['status'])}) {hit['title']} (relevance {hit['relevance']})")
    typer.echo(f"-> {run / 'literature_retrieval.yaml'}")


@app.command("retrieve-autonomous")
def retrieve_autonomous_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: list[str] | None = typer.Option(None, "--provider", help="local_corpus | arxiv (repeatable)"),
    corpus: Path | None = typer.Option(None, "--corpus", help="Local source corpus directory."),
    llm_provider: str = typer.Option("stub", "--llm-provider", help="stub | openai | external"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
    ground: bool = typer.Option(True, "--ground/--no-ground", help="Hand a readable local candidate to grounding."),
) -> None:
    """Autonomously find CANDIDATE sources with an LLM-guided planner, then hand a
    readable local candidate to the EXISTING grounding pipeline.

    The LLM plans queries and ranks candidates only. Retrieval never earns
    reference support; only Source Grounding (exact locator + hypothesis match +
    attribution) does. A failed/empty search fails closed and everything stays a
    candidate ("Needs a source").
    """
    if llm_provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("llm-provider must be stub, openai, or external")
    _require_claim_clear_for_cli(run)
    client = build_llm_client(
        provider=llm_provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    report = run_autonomous_retrieval(
        run,
        client=client,
        corpus_dir=corpus,
        providers=provider or ["local_corpus"],
        ground=ground,
    )
    typer.echo(
        f"Autonomous retrieval: {len(report['candidates'])} candidate source(s) "
        f"found by literature search [providers: {', '.join(report['providers'])}]."
    )
    for cand in report["candidates"][:5]:
        typer.echo(
            f"- {cand['id']} ({evidence_phrase(cand['status'])}) {cand['title']} "
            f"[{cand['provider']}, relevance {cand['relevance']:.2f}]"
        )
    if report["grounded"]:
        typer.echo(
            "Handed the top local candidate to source grounding (a reference supports a "
            "step only with exact locator, hypothesis match, and attribution)."
        )
    else:
        typer.echo("No candidate earned reference support yet; grounding stayed candidate / Needs a source.")
    typer.echo(f"-> {run / 'literature_search_plan.yaml'}")
    typer.echo(f"-> {run / 'autonomous_literature_retrieval.yaml'}")


@app.command("serve")
def serve_command(
    runs_root: Path = typer.Option(Path("."), "--runs-root", file_okay=False, help="Project-runs root (where projects/<name>/runs/<id> live or will be created)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost by default)."),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    provider: str = typer.Option("stub", "--provider", help="Agent provider for runs created from the UI (stub/openai/external)."),
) -> None:
    """Start the local Product Workbench server (interactive browser UI).

    Type a claim in the browser, get a quick initial read + routes, then click
    Run Examples / Counterexamples / Literature, Continue a route, or Skip an
    agent — no terminal commands. Every action goes through the existing kernel
    path (validate_run); the server never upgrades proof/source/evidence status,
    confines every run id to the runs root, and never runs an arbitrary shell
    command from browser input.
    """
    from .server import serve

    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    serve(runs_root, host=host, port=port, provider=provider)


@app.command("add-bank-item")
def add_bank_item_command(
    bank: Path = typer.Option(..., "--bank", exists=True, file_okay=False, help="A research bank directory (with _meta.yaml)."),
    item: Path = typer.Option(..., "--item", exists=True, dir_okay=False, help="A YAML file with one bank item."),
) -> None:
    """Append one validated item to a per-item research bank as a NEW file.

    Storage/layout only: the item is validated against the bank's existing item
    model and refused on a duplicate id; it never changes example/counterexample/
    strategy/source/evidence/proof status. Existing item files are not rewritten.
    """
    item_data = read_yaml(item)
    new_path = append_bank_item(bank, item_data)
    typer.echo(f"Appended one bank item -> {new_path}")


@app.command("materialize-banks")
def materialize_banks_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    artifact: list[str] | None = typer.Option(
        None, "--artifact",
        help="Artifact key(s) to convert: examples | counterexamples | strategy_synthesis. Default: all three.",
    ),
) -> None:
    """Opt-in: convert a run's single-file research artifacts into per-item bank
    directories (examples/, counterexamples/, strategies/) and repoint the
    manifest + agent_contracts at them.

    Storage/layout only: item content and all statuses are unchanged; the
    aggregated bank matches the old model shape. Default runs keep single-file
    output -- this command is the only way to switch a run to bank output.
    """
    converted = materialize_run_banks(run, keys=artifact)
    validate_run(run)
    if converted:
        typer.echo(f"Converted {len(converted)} artifact(s) to bank directories: {', '.join(converted)}")
    else:
        typer.echo("No artifacts converted (already bank directories or none requested).")


@app.command("bank-dashboard")
def bank_dashboard_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    out: Path | None = typer.Option(None, "--out", help="Write the HTML here; otherwise print to stdout."),
) -> None:
    """Render a read-only static HTML dashboard of a run's research banks.

    Reads examples / counterexamples / strategies through the bank-aware loaders
    (bank directories OR single YAML) and never mutates any artifact. Statuses are
    shown as recorded in human-readable wording; no proof-complete language.
    """
    validate_run(run)  # read-only; just confirms the run is valid before viewing
    html_text = render_bank_dashboard(run)
    if out is not None:
        write_text(out, html_text)
        typer.echo(f"Wrote research bank dashboard -> {out}")
    else:
        typer.echo(html_text)


@app.command("run-dashboard")
def run_dashboard_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    out: Path | None = typer.Option(None, "--out", help="Write the HTML here; default: <run>/research_dashboard.html."),
) -> None:
    """Render the minimal interactive product dashboard for one run.

    This is the primary human-facing run view: claim status, route picker,
    reviewer repair state, and next command. It is read-only with respect to
    mathematical artifacts; it never changes proof/source/evidence status.
    """
    validate_run(run)
    if out is None:
        out = write_run_dashboard(run)
    else:
        write_text(out, render_run_dashboard(run))
    typer.echo(f"Wrote run dashboard -> {out}")


def _refresh_derived_views(run: Path) -> None:
    """Rebuild the proof-search graph + dashboard after a command appends scoped
    evidence, so the memo, graph, and dashboard stay coherent for one run.

    Only refreshes views that ALREADY exist (never fabricates new artifacts), and
    fails closed on the graph: if the rebuilt graph cannot pass validation, drop it
    and keep the run valid. This never upgrades any status -- the graph re-validates
    through the same kernel gate and only reflects evidence the kernel already
    earned.
    """
    from .subgoal_graph import build_subgoal_graph, remove_subgoal_graph
    from .theorem_condition_ledger import build_theorem_condition_ledger

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


@app.command("run-external-tool")
def run_external_tool_command(
    run: Path | None = typer.Option(None, "--run", exists=True, file_okay=False),
    tool: str = typer.Option("", "--tool", help="Allowlisted fake tool name (see --list-tools)."),
    entry_id: str = typer.Option("", "--id", help="Unique id for this tool-run record."),
    statement: str = typer.Option("", "--statement", help="The local statement this run checks."),
    timeout: float = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout", help="Hard timeout (seconds)."),
    list_tools: bool = typer.Option(False, "--list-tools", help="List allowlisted fake tools and exit."),
) -> None:
    """Run an allowlisted DETERMINISTIC FAKE external tool in the sandbox executor
    and record an audited run (raw evidence only -- never a proof/source/grade
    status). Unknown tools are refused. Timeout / nonzero exit / malformed output
    fail closed (no parsed result). Slice 2: records only; earns nothing.
    """
    if list_tools:
        for name, spec in FAKE_TOOLS.items():
            typer.echo(f"{name}: {spec['scope_note']}")
        return
    if run is None or not tool or not entry_id:
        typer.echo("Provide --run, --tool, and --id (or use --list-tools).")
        raise typer.Exit(code=2)
    try:
        record = run_and_record_external_tool(
            run, tool, entry_id=entry_id, statement_checked=statement, timeout_seconds=timeout
        )
    except UnknownToolError as exc:
        typer.echo(f"Refused: {exc}")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:  # duplicate id / non-confined path -> clean message, not a traceback
        typer.echo(f"Refused: {exc}")
        raise typer.Exit(code=2) from exc
    _refresh_derived_views(run)
    clean = run_completed_cleanly(record.timed_out, record.exit_code)
    outcome = record.parsed_result if clean and record.parsed_result else "no evidence (fail-closed)"
    typer.echo(
        f"Ran {tool} [{ 'completed' if clean else 'did not complete cleanly' }]: {outcome} "
        f"(raw evidence only; not a proof; earns no status)."
    )
    typer.echo(f"-> {run / 'external_tool_runs.yaml'}")


@app.command("verify-exact-modexp")
def verify_exact_modexp_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    base: int = typer.Option(..., "--base"),
    exp: int = typer.Option(..., "--exp"),
    mod: int = typer.Option(..., "--mod"),
    expected: int = typer.Option(..., "--expected"),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this tool-run record."),
) -> None:
    """Allowlisted deterministic adapter: run the exact modular-exponentiation tool
    in the sandbox and, IF it confirms base^exp mod m == expected, earn a SCOPED
    computation_checked proof-verification entry THROUGH the kernel bridge (this
    one computation only; never a universal proof). A non-confirming or failed run
    earns nothing.
    """
    _require_claim_clear_for_cli(run)
    try:
        result = run_exact_modexp_adapter(
            run, base=base, exp=exp, mod=mod, expected=expected, entry_id=entry_id
        )
    except ValueError as exc:
        typer.echo(f"Refused: {exc}")
        raise typer.Exit(code=2) from exc
    _refresh_derived_views(run)
    if result["earned"]:
        typer.echo(
            f"Confirmed {base}^{exp} mod {mod} == {expected}: earned a SCOPED "
            "computation_checked entry (this computation only; NOT a universal proof)."
        )
        typer.echo(f"-> {run / 'proof_verification.yaml'}")
    else:
        typer.echo(f"Not confirmed: earned no status (fail-closed). {result['detail']}")
    typer.echo(f"-> {run / 'external_tool_runs.yaml'}")


@app.command("verify-finite-groups")
def verify_finite_groups_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    prime: list[int] | None = typer.Option(None, "--prime", help="Prime p; checks all groups of order p^2. Repeatable."),
) -> None:
    """Machine-verify (deterministically) that every group of order p^2 is abelian,
    for the given small primes -- a SCOPED finite check, not a universal proof.

    Earns instance_checked for the enumerated groups only. There is no 'proved'
    status: checking p=2,3 does not prove the claim for all primes p. Natural-
    language prover/reviewer approval is not proof verification.
    """
    _require_claim_clear_for_cli(run)
    primes = prime or [2, 3]
    report = verify_finite_groups(run, primes=primes)
    validate_run(run)
    _refresh_derived_views(run)
    checked = sum(1 for e in report["entries"] if e["earned_status"] == "instance_checked")
    typer.echo(
        f"Verified {checked}/{len(report['entries'])} finite group(s) abelian "
        f"for primes {primes} (checked in this finite case only; NOT a universal proof)."
    )
    typer.echo(f"-> {run / 'proof_verification.yaml'}")


@app.command("verify-identity")
def verify_identity_command(
    table: Path = typer.Option(..., "--table", exists=True, dir_okay=False, help="YAML file: a finite operation table (list of lists of ints)."),
    identity: str = typer.Option(..., "--identity", help="commutative | associative | idempotent | involutive"),
) -> None:
    """Exhaustively check an algebraic identity on an EXPLICITLY-GIVEN finite
    operation table -- a SCOPED finite/computational check, never a universal
    proof. Read-only; fails closed on a malformed table or unsupported identity.
    """
    data = read_yaml(table)
    result = verify_identity_on_table(data, identity)
    verdict = (
        f"holds on this finite table (order {result['order']}, {result['checked']} cases checked)"
        if result["holds"]
        else f"NOT established: {result['detail']}"
    )
    typer.echo(f"Identity '{identity}': {verdict}")
    typer.echo(f"Scope: {result['scope_note']}")


@app.command("verify-identity-in-run")
def verify_identity_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    table: Path = typer.Option(..., "--table", exists=True, dir_okay=False, help="YAML file: a finite operation table (list of lists of ints)."),
    identity: str = typer.Option(..., "--identity", help="commutative | associative | idempotent | involutive"),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Connect an explicit finite-table identity check into the run's proof-
    verification artifact. Earns instance_checked ONLY if the identity holds on
    this finite table; otherwise fails closed to unverified. Re-validates the run.
    There is no 'proved' status: this is a finite/computational check, not a
    universal proof.
    """
    _require_claim_clear_for_cli(run)
    data = read_yaml(table)
    entry = verify_identity_table_in_run(run, table=data, identity=identity, entry_id=entry_id, target=target)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        f"(checked in this finite case only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("verify-ring-identity-in-run")
def verify_ring_identity_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    identity: str = typer.Option(..., "--identity", help=f"One of: {', '.join(SUPPORTED_RING_IDENTITIES)}"),
    modulus: int = typer.Option(..., "--modulus", help="The modulus n; the identity is checked over all residues of Z/n."),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Connect an exhaustive Z/n algebraic-identity sanity check into the run's
    proof-verification artifact. Earns instance_checked ONLY if the identity holds
    for every residue mod n; otherwise fails closed to unverified. Re-validates the
    run. This is a finite/computational sanity check over Z/n, never a universal
    proof over a general ring or for all moduli.
    """
    _require_claim_clear_for_cli(run)
    entry = verify_ring_identity_in_run(run, identity=identity, modulus=modulus, entry_id=entry_id, target=target)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        f"(checked over Z/{modulus} only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("verify-matrix-rank-in-run")
def verify_matrix_rank_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    matrix: str = typer.Option(..., "--matrix", help='Integer matrix as JSON, e.g. "[[1,2],[2,4]]".'),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    expected_rank: int | None = typer.Option(None, "--expected-rank", help="Expected rank of the matrix."),
    expected_determinant: int | None = typer.Option(None, "--expected-determinant", help="Expected determinant (square matrix only)."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Machine-check the rank and/or determinant of ONE explicit integer matrix and
    record it in the run's proof-verification artifact. Earns instance_checked ONLY
    when every provided expectation matches; otherwise fails closed to unverified.
    This is a finite/computational check of this specific matrix (e.g. witnessing a
    concrete rank/dimension jump), NEVER a universal proof about any family.
    """
    _require_claim_clear_for_cli(run)
    try:
        parsed = json.loads(matrix)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--matrix must be valid JSON: {exc}")
    if expected_rank is None and expected_determinant is None:
        raise typer.BadParameter("provide --expected-rank and/or --expected-determinant")
    entry = verify_matrix_rank_in_run(
        run, matrix=parsed, entry_id=entry_id,
        expected_rank=expected_rank, expected_determinant=expected_determinant, target=target,
    )
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        "(checked for this specific matrix only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("auto-wire-verifications")
def auto_wire_verifications_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Run the strategy/prover stage's EXPLICIT scoped-verification requests
    (from scoped_verification_requests.yaml) through the trusted backend and append
    the results to proof_verification.yaml. A confirming result earns
    instance_checked; a non-confirming/malformed one fails closed to unverified; a
    structurally unusable / too-expensive / claim-mismatched request is skipped.

    Natural-language prose is never read -- only explicit structured requests -- so
    this cannot manufacture a checked status from text. There is no 'proved'
    status: every result is a finite/computational check, not a universal proof.
    """
    _require_claim_clear_for_cli(run)
    _earned_phrase = {
        "instance_checked": "checked in this finite/computational scope only",
        "unverified": "not established (no checked status)",
    }
    summary = auto_wire_scoped_verifications(run)
    _refresh_derived_views(run)
    for item in summary["appended"]:
        phrase = _earned_phrase.get(item["earned_status"], item["earned_status"])
        typer.echo(f"[run] {item['id']}: {phrase} [{item['earned_status']}]")
    for item in summary["skipped"]:
        typer.echo(f"[skip] {item['id']}: {item['reason']}")
    typer.echo(
        f"Auto-wired {len(summary['appended'])} scoped check(s), skipped "
        f"{len(summary['skipped'])} (checked in this finite/computational scope only; "
        "NOT a universal proof)."
    )


@app.command("show-pattern-bank")
def show_pattern_bank_command() -> None:
    """Print the catalogued math failure patterns (candidate / audited / hard)."""
    bank = seed_failure_pattern_bank()
    for tier in ("hard", "audited", "candidate"):
        rows = [p for p in bank.patterns if p.tier.value == tier]
        typer.echo(f"{tier} ({len(rows)}):")
        for p in rows:
            suffix = f" -> {p.enforced_by}" if p.enforced_by else ""
            typer.echo(f"  - {p.id} [{p.family}]: {p.summary}{suffix}")
    typer.echo(f"total: {len(bank.patterns)} patterns")


@app.command()
def memo(run: Path = typer.Option(..., "--run", exists=True, file_okay=False)) -> None:
    """Validate a run and print a compact, artifact-backed research memo.

    The concise user-facing product output: claim + grade, strongest obstruction,
    source gaps, top routes, and the next cheapest test. Never upgrades status or
    emits proof-complete language for weak artifacts.
    """
    validate_run(run)
    typer.echo(build_concise_memo(run))


@app.command("review-checklist")
def review_checklist_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Run the artifact-grounded reviewer calibration checklist on a run
    (read-only). Flags proof-complete language and invalid source_backed refs,
    each citing the artifact. Exits non-zero if a blocker is found; non-blocking
    suggestions never block. Earns no status; mutates nothing.
    """
    validate_run(run)
    result = review_checklist(run)
    if not result["findings"]:
        typer.echo("Reviewer checklist: no blocking findings (artifact-grounded, read-only).")
    for finding in result["findings"]:
        mark = "BLOCKER" if finding["severity"] == "blocker" else "non-blocker"
        typer.echo(f"[{mark}] {finding['text']}")
    typer.echo(f"merge_allowed: {result['merge_allowed']}")
    if not result["merge_allowed"]:
        raise typer.Exit(code=1)


@app.command()
def inspect(run: Path = typer.Option(..., "--run", exists=True, file_okay=False)) -> None:
    """Validate and print a human-readable run summary."""
    validate_run(run)

    def _echo(message: object = "") -> None:
        # Display-layer safety: redact any global proof-complete overclaim that an
        # echoed free-text artifact field might carry. Typed status phrases are
        # unaffected; never upgrades status.
        typer.echo(scrub_proof_language(str(message)))

    manifest = read_yaml(run / "run_manifest.yaml")
    optional_artifacts = manifest.get("optional_artifacts", {})
    artifacts = manifest.get("artifacts", {})
    _echo(f"Run is valid: {run}")

    def _artifact(key: str) -> dict:
        # Resolve through the run manifest (source of truth), not hardcoded names.
        # Bankable artifacts may be a single YAML file or a per-item bank
        # directory; load them through the bank-aware loader so a materialized run
        # works exactly like a single-file run.
        if key in _BANKABLE_KINDS:
            return load_bankable_dict(run, key, manifest)
        return read_yaml(run / artifacts[key])

    claim = _artifact("locked_claim")
    grade = _artifact("idea_grade")
    review = _artifact("review")
    strategy_brief = _artifact("strategy_brief")
    source_grounding = _artifact("source_grounding")
    human_choice = _artifact("human_choice")
    examples = _artifact("examples")
    counterexamples = _artifact("counterexamples")
    computations = _artifact("computation_checks")

    failed_reviewers = [
        reviewer["reviewer_id"]
        for reviewer in review.get("reviewers", [])
        if reviewer.get("verdict") == "fail"
    ]
    source_gaps = grade.get("source_gaps", [])
    source_statuses = sorted(
        {
            ref.get("status")
            for ref in source_grounding.get("source_refs", [])
            if ref.get("status")
        }
        | {
            step.get("status")
            for step in source_grounding.get("steps", [])
            if step.get("status")
        }
    )

    _echo("")
    _echo("Claim")
    _echo(f"- {claim.get('normalized_statement', '').strip()}")
    claim_state = "locked" if claim.get("locked") else "unlocked"
    if claim.get("human_confirmed"):
        claim_state += ", human_confirmed"
    if claim.get("repaired_from"):
        claim_state += f", repaired_from {claim.get('repaired_from')}"
    _echo(f"- status: {claim_state}")
    _echo("")
    _echo("Grade")
    _echo(
        f"- {describe_grade(status=grade.get('status'), proof_language_status=grade.get('proof_language_status'))}"
    )
    _echo(
        f"- confidence score {grade.get('score')} (internal heuristic); "
        f"proof state: {describe_proof_language(grade.get('proof_language_status'))}"
    )
    _echo("")
    _echo("Strongest Objection")
    _echo(f"- {review.get('strongest_objection')}")
    _echo("")
    _echo("Failed Reviewers")
    _echo(f"- {', '.join(failed_reviewers) if failed_reviewers else 'none'}")
    _echo("")
    _echo("Source Gaps")
    _echo(f"- {', '.join(source_gaps) if source_gaps else 'none'}")
    _evidence_summary = sorted({evidence_phrase(s) for s in source_statuses})
    _echo(
        "- evidence so far: "
        + ("; ".join(_evidence_summary) if _evidence_summary else "none recorded")
    )
    _refs_by_id = {r.get("id"): r for r in source_grounding.get("source_refs", [])}
    for step in source_grounding.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        for ref_id in step.get("source_refs", []):
            ref = _refs_by_id.get(ref_id)
            if not ref:
                continue
            loc = ref.get("locator", {})
            authors = ", ".join(ref.get("authors", [])) or "unknown author"
            _echo(
                f"- grounded in {authors}, {ref.get('title', 'untitled')} "
                f"[{loc.get('kind')}:{loc.get('value')}] (cited support, not a proof)"
            )
    for check in computations.get("checks", []):
        if check.get("status") != "checked":
            continue
        mc = check.get("machine_check") or {}
        if not mc:
            continue
        _echo(
            f"- machine-checked {check.get('id')} [{mc.get('kind')}] "
            f"{mc.get('expression')} = {mc.get('expected')} "
            "(machine-checked computation, not a proof)"
        )
    _echo("")
    _echo("Examples / Counterexamples")
    example_items = examples.get("examples", [])
    if example_items:
        for example in example_items[:3]:
            _echo(
                f"- example {example.get('id')}: {example.get('name')} "
                f"-> {example.get('result_for_current_idea')}"
            )
    else:
        _echo("- examples: none")
    cx_items = counterexamples.get("tests", [])
    if cx_items:
        for cx in cx_items[:3]:
            _echo(
                f"- counterexample {cx.get('id')}: {cx.get('result')} "
                f"(refutes_claim={cx.get('refutes_claim')}, "
                f"refutes_step_only={cx.get('refutes_step_only')})"
            )
    else:
        _echo("- counterexamples: none")
    theorem_applicability_path = _optional_artifact_path(
        run, optional_artifacts, "theorem_applicability"
    )
    if theorem_applicability_path is not None:
        theorem_applicability = read_yaml(theorem_applicability_path)
        usages = theorem_applicability.get("usages", [])
        blocking_usages = [
            usage
            for usage in usages
            if usage.get("applicability_status") != "applicable"
            or usage.get("blocks_source_backed") is True
        ]
        unclear_conditions = [
            condition
            for usage in usages
            for condition in usage.get("conditions", [])
            if condition.get("status") != "satisfied"
        ]
        _echo("")
        _echo("Theorem Applicability")
        _echo(
            f"- {theorem_applicability.get('overall_status')} / "
            f"{len(blocking_usages)} blocking usage(s), "
            f"{len(unclear_conditions)} unresolved condition(s)"
        )
        for usage in blocking_usages[:3]:
            _echo(
                f"- {usage.get('proof_step_id')}: "
                f"{usage.get('applicability_status')} "
                f"({usage.get('theorem_label') or usage.get('source_ref_id')})"
            )
            for condition in [
                condition
                for condition in usage.get("conditions", [])
                if condition.get("status") != "satisfied"
            ][:3]:
                locator = condition.get("definition_locator") or {}
                locator_text = (
                    f", locator {locator.get('kind')}:{locator.get('value')}"
                    if locator.get("value")
                    else ""
                )
                _echo(
                    f"  condition {condition.get('status')} "
                    f"[{condition.get('condition_type')}]: "
                    f"{condition.get('text')}"
                    f"{locator_text}"
                )
                if condition.get("evidence"):
                    _echo(f"  evidence: {condition.get('evidence')}")
        if not blocking_usages:
            _echo("- no theorem-condition blockers found")
        _echo("- applicability audit is not proof status")
    _echo("")
    _echo("Top Strategies")
    for index, strategy in enumerate(strategy_brief.get("top_strategies", [])[:3], start=1):
        _echo(
            f"{index}. {strategy.get('title')} "
            f"[score {strategy.get('score')}, effort {strategy.get('estimated_effort')}]"
        )
        _echo(f"   risk: {strategy.get('main_risk')}")
    _echo("")
    _echo("Human Choice")
    _echo(
        f"- {human_choice.get('status')} / {human_choice.get('selected_strategy') or 'none'}"
    )
    prover_loop_path = _optional_artifact_path(run, optional_artifacts, "prover_loop")
    if prover_loop_path is not None:
        prover_loop = read_yaml(prover_loop_path)
        round_index_path = _optional_artifact_path(run, optional_artifacts, "prover_round_index")
        round_index = read_yaml(round_index_path) if round_index_path is not None else None
        budget = prover_loop.get("budget", {})
        latest_attempt = _focused_prover_loop_attempt(prover_loop)
        blockers = latest_attempt.get("blockers", [])
        _echo("")
        _echo("Prover Loop")
        _echo(
            f"- decision {prover_loop.get('loop_decision')} / "
            f"strategy {prover_loop.get('selected_strategy_id') or 'none'} / "
            f"rounds {budget.get('rounds_used')}/{budget.get('max_rounds')}"
        )
        _echo(
            f"- human gate {prover_loop.get('human_gate_status')} / "
            f"effort {budget.get('approved_effort')} / "
            f"agent calls {budget.get('agent_calls_used')}/{budget.get('max_agent_calls')}"
        )
        _echo(
            f"- latest attempt: {latest_attempt.get('status', 'none')} / "
            f"attempt strategy {latest_attempt.get('strategy_id', 'none')} / "
            f"time allocation {latest_attempt.get('time_allocation', 'none')}"
        )
        if prover_loop.get("decision_reason"):
            _echo(f"- decision reason: {prover_loop.get('decision_reason')}")
        for review in latest_attempt.get("reviewer_objections", [])[:3]:
            _echo(
                f"- reviewer {review.get('verifier_id')}: "
                f"{review.get('verdict')} / "
                f"{review.get('objection_count')} objection(s), "
                f"action {review.get('recommended_human_action')}"
            )
        _echo(f"- blockers: {', '.join(blockers[:3]) if blockers else 'none'}")
        if round_index is not None:
            rounds = round_index.get("rounds", [])
            _echo(f"- durable round artifacts: {len(rounds)}")
            if len(rounds) > 3:
                _echo(f"  showing latest 3 of {len(rounds)}")
            for item in rounds[-3:]:
                _echo(
                    f"  {item.get('loop_attempt_id')}: {item.get('artifact_path')} "
                    f"({item.get('status')}, {item.get('time_allocation')})"
                )
    judgment_path = _optional_artifact_path(run, optional_artifacts, "mathematical_judgment")
    if judgment_path is not None:
        judgment = read_yaml(judgment_path)
        findings = judgment.get("findings", [])
        _echo("")
        _echo("Mathematical Judgment (advisory risks / next checks; not proof)")
        if findings:
            for finding in findings[:6]:
                _echo(
                    f"- {judgment_tier_phrase(finding.get('tier'))}: {finding.get('kind')} "
                    f"({finding.get('subject')}) -> {finding.get('recommended_action')}: "
                    f"{finding.get('explanation')}"
                )
        else:
            _echo("- no risks flagged")
        _echo("- judgment is advisory only; it never upgrades proof/source/evidence status")

    pattern_audit_path = _optional_artifact_path(run, optional_artifacts, "failure_pattern_audit")
    if pattern_audit_path is not None:
        audit = read_yaml(pattern_audit_path)
        pmatches = audit.get("matches", [])
        _echo("")
        _echo("Failure Pattern Audit (advisory; not a refutation, not a proof)")
        if pmatches:
            for match in pmatches[:8]:
                suffix = f" [enforced by {match.get('enforced_by')}]" if match.get("enforced_by") else ""
                _echo(
                    f"- {objection_phrase(match.get('tier'))} "
                    f"({match.get('pattern_id')}, {match.get('family')}): "
                    f"{match.get('explanation')}{suffix}"
                )
        else:
            _echo("- no patterns matched")
        _echo(
            "- objections are advisory: a match is a warning or a ranking/time hint, "
            "never a refutation and never a status change"
        )

    retrieval_path = _optional_artifact_path(run, optional_artifacts, "literature_retrieval")
    if retrieval_path is not None:
        retrieval = read_yaml(retrieval_path)
        rhits = retrieval.get("hits", [])
        _echo("")
        _echo("Literature Retrieval (proposed sources only; not yet reference-backed)")
        if rhits:
            for hit in rhits[:5]:
                loc = hit.get("locator", {})
                _echo(
                    f"- {hit.get('id')} ({evidence_phrase(hit.get('status'))}) {hit.get('title')} "
                    f"[{loc.get('kind')}:{loc.get('value')}] via {hit.get('provider')}"
                )
        else:
            _echo("- no proposed sources retrieved (fail closed)")
        _echo(
            "- retrieval only proposes sources; a reference earns support only after "
            "exact locator, hypothesis match, and attribution in source grounding"
        )

    proof_verification_path = _optional_artifact_path(run, optional_artifacts, "proof_verification")
    if proof_verification_path is not None:
        pv = read_yaml(proof_verification_path)
        entries = pv.get("entries", [])
        _echo("")
        _echo("Proof Verification Bridge (scoped machine checks; no universal proof)")
        if entries:
            for entry in entries[:8]:
                _echo(
                    f"- {entry.get('target')}: {entry.get('result')} "
                    f"({entry.get('verifier_type')}: {evidence_phrase(entry.get('earned_status'))})"
                )
        else:
            _echo("- no verifier runs")
        _echo(
            "- these checks cover only the enumerated finite instances; "
            "the universal claim is NOT proved"
        )

    memory_summary = summarize_memory(run.parents[1], claim_text=claim.get("normalized_statement", ""))
    if memory_summary:
        _echo("")
        _echo("Memory")
        for line in memory_summary.splitlines()[:10]:
            _echo(line)
    _echo("")
    _echo("Artifacts")
    for key, rel_path in manifest.get("artifacts", {}).items():
        _echo(f"- {key}: {run / rel_path}")
    for key, rel_path in optional_artifacts.items():
        _echo(f"- {key} (optional): {run / rel_path}")
    _echo("")
    _echo(f"Final report: {run / 'final_report.md'}")


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


@app.command("discover-literature")
def discover_literature(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    mode: str = typer.Option("local", "--mode"),
    query: str | None = typer.Option(None, "--query"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: list[str] | None = typer.Option(None, "--provider"),
    ads_token_env: str = typer.Option("ADS_API_TOKEN", "--ads-token-env"),
) -> None:
    """Run conservative Literature Discovery for an existing run."""
    if mode not in {"local", "live"}:
        raise typer.BadParameter("Literature Discovery supports --mode local or --mode live")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    artifacts = manifest["artifacts"]
    project_dir = run.parents[1]
    report = run_literature_discovery(
        project_dir=project_dir,
        locked_claim_path=run / artifacts["locked_claim"],
        hypothesis_ledger_path=run / artifacts["hypothesis_ledger"],
        max_references=max_references,
        query=query,
        mode=mode,
        providers=provider,
        ads_token_env=ads_token_env,
    )
    LiteratureReviewReport.model_validate(report)
    write_yaml(run / artifacts["literature_review"], report)
    validate_run(run)
    typer.echo(f"Wrote {mode} literature discovery to {run / artifacts['literature_review']}")
    typer.echo("Discovered references stay proposed (not yet checked); only source grounding earns reference support.")


@app.command("ingest-pdf")
def ingest_pdf(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    pdf: str = typer.Option(..., "--pdf", help="Local PDF/text path or HTTPS URL."),
    source_id: str = typer.Option("source_001", "--source-id"),
    title: str = typer.Option("Ingested source", "--title"),
    author: list[str] | None = typer.Option(None, "--author"),
    source_type: str = typer.Option("paper", "--source-type"),
) -> None:
    """Ingest a PDF/text source and write theorem_extraction.yaml."""
    validate_run(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run / manifest["artifacts"]["locked_claim"])
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=pdf,
        source_id=source_id,
        title=title,
        authors=author or [],
        source_type=source_type,
    )
    TheoremExtractionReport.model_validate(report)
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run / "theorem_extraction.yaml", report)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote theorem extraction to {run / 'theorem_extraction.yaml'}")


@app.command("extract-locators")
def extract_locators_command(
    source: str = typer.Option(..., "--source", help="Local full-text/PDF path or HTTPS URL."),
) -> None:
    """Extract CANDIDATE theorem/lemma/proposition/corollary blocks with precise
    locators from full text (read-only; candidate-only).

    Extraction never earns reference support: a block is shown as 'groundable'
    only when its locator is exact, and only Source Grounding (exact locator +
    hypothesis match + attribution) can earn source_backed. A read/parse failure
    fails closed (no fabricated locators)."""
    raw = read_source_bytes(source)
    text, notes = extract_source_text(raw, source)
    candidates = extract_candidate_locators(text)
    typer.echo(f"Found {len(candidates)} candidate block(s) in {source} (proposed, not checked):")
    for cand in candidates:
        groundable = "groundable locator" if cand["is_exact"] else "needs a clearer locator"
        typer.echo(
            f"- [{cand['locator']['kind']}:{cand['locator']['value']}] ({groundable}) "
            f"{cand['statement'][:80]}"
        )
    for note in notes:
        typer.echo(f"note: {note}")


@app.command("verify-sources")
def verify_sources(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    theorem_extraction: Path | None = typer.Option(None, "--theorem-extraction", exists=True, dir_okay=False),
) -> None:
    """Run conservative Referencing verification from extracted theorem statements."""
    validate_run(run)
    _require_claim_clear_for_cli(run)
    manifest = read_yaml(run / "run_manifest.yaml")
    extraction_path = theorem_extraction
    if extraction_path is None:
        extraction_name = manifest.get("optional_artifacts", {}).get("theorem_extraction")
        if not extraction_name:
            raise typer.BadParameter("run has no theorem_extraction optional artifact")
        extraction_path = run / extraction_name
    ground_run(run_dir=run, theorem_extraction_path=extraction_path)
    typer.echo(f"Wrote source verification to {run / 'source_grounding.yaml'}")
    typer.echo(f"Wrote theorem applicability audit to {run / 'theorem_applicability.yaml'}")


@app.command("audit-theorem-applicability")
def audit_theorem_applicability_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Write theorem_applicability.yaml without upgrading source or proof status."""
    try:
        validate_run(run)
    except KernelValidationError as exc:
        if "require theorem_applicability audit" not in str(exc):
            raise
    _require_claim_clear_for_cli(run)
    report = audit_theorem_applicability(run)
    TheoremApplicabilityReport.model_validate(report)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = "theorem_applicability.yaml"
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote theorem applicability audit to {run / 'theorem_applicability.yaml'}")


@app.command("verify-computations")
def verify_computations_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Execute machine_check specs with a real backend and write computation_verification.yaml.

    Does not upgrade status. It records confirmed/refuted/error/unsupported per
    check; validation then forbids a refuted check from claiming checked.
    """
    _require_claim_clear_for_cli(run)
    report = verify_computation_checks(run)
    validate_run(run)
    confirmed = sum(1 for r in report["results"] if r["verdict"] == "confirmed")
    refuted = sum(1 for r in report["results"] if r["verdict"] == "refuted")
    typer.echo(
        f"Verified {len(report['results'])} machine check(s): "
        f"{confirmed} confirmed, {refuted} refuted "
        f"-> {run / 'computation_verification.yaml'}"
    )


@app.command("audit-evidence")
def audit_evidence_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Evidence Auditor: a computation EARNS computation_checked only from a
    confirming machine result (refuted -> wrong, error/unsupported -> human_needed).
    The machine earns the status; the agent never self-certifies. idea_grade is
    untouched."""
    validate_run(run)
    _require_claim_clear_for_cli(run)
    computations = audit_evidence(run)
    checked = sum(1 for c in computations.get("checks", []) if c.get("status") == "checked")
    typer.echo(
        f"Evidence audit: {checked} computation(s) machine-checked "
        f"-> {run / 'computation_checks.yaml'}"
    )


@app.command("review-literature-agent")
def review_literature_agent(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    source: list[str] | None = typer.Option(None, "--source", help="Repeatable local PDF/text path or HTTPS URL."),
    source_id: list[str] | None = typer.Option(None, "--source-id", help="Optional repeatable id aligned with --source."),
    title: list[str] | None = typer.Option(None, "--title", help="Optional repeatable title aligned with --source."),
    author: list[str] | None = typer.Option(None, "--author", help="Optional author list applied to all supplied sources."),
    source_type: str = typer.Option("paper", "--source-type"),
) -> None:
    """Run the literature-review-first agent over candidate source text/PDFs."""
    validate_run(run)
    _require_claim_clear_for_cli(run)
    sources = source or []
    ids = source_id or []
    titles = title or []
    specs = [
        ReviewSourceSpec(
            source=value,
            source_id=ids[index] if index < len(ids) else None,
            title=titles[index] if index < len(titles) else None,
            authors=author or [],
            source_type=source_type,
        )
        for index, value in enumerate(sources)
    ]
    report = run_literature_review_agent(run_dir=run, source_specs=specs)
    LiteratureReviewAgentReport.model_validate(report)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["literature_review_agent"] = "literature_review_agent.yaml"
    write_yaml(run / "literature_review_agent.yaml", report)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote literature review agent report to {run / 'literature_review_agent.yaml'}")
    typer.echo("Review judgments stay proposed (not yet checked); only source grounding earns reference support.")


@app.command("retrieve-and-ground")
def retrieve_and_ground_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    candidate: str | None = typer.Option(None, "--candidate", help="A literature_candidates.yaml candidate id to resolve."),
    local_corpus: Path | None = typer.Option(None, "--local-corpus", file_okay=False, help="Directory of local source text/PDF files."),
    local_source: Path | None = typer.Option(None, "--local-source", dir_okay=False, help="Explicit local source text/PDF path."),
    url: str | None = typer.Option(None, "--url", help="Explicit URL or arXiv URL (only fetched with --network)."),
    network: bool = typer.Option(False, "--network/--no-network", help="Allow fetching a url/arxiv source (default off; fail closed)."),
    source_id: str = typer.Option("retrieved_src", "--source-id"),
    title: str = typer.Option("", "--source-title"),
    author: list[str] | None = typer.Option(None, "--source-author"),
    source_type: str = typer.Option("paper", "--source-type"),
) -> None:
    """Resolve a candidate source (local file / corpus / url / arXiv), extract
    theorems, and hand to the existing source-grounding pipeline; then validate.

    Candidate-only until grounding earns support (exact locator + attribution +
    hypothesis match). Fails closed: an unresolved candidate, disabled network, or
    a download/extraction error writes a failure/skip report and leaves the run
    valid without granting any source support. Records retrieval_resolution.yaml.
    """
    _require_claim_clear_for_cli(run)
    report = retrieve_and_ground(
        run_dir=run,
        candidate_id=candidate,
        local_corpus=local_corpus,
        local_source=local_source,
        url=url,
        source_id=source_id,
        title=title,
        authors=author or [],
        source_type=source_type,
        network_enabled=network,
    )
    typer.echo(
        f"Retrieval resolution: {report['resolution_status']} "
        f"(kind={report['source_kind']}, grounding_attempted={report['grounding_attempted']})"
    )
    typer.echo(
        "Retrieval never sets source_backed; a step is reference-supported only "
        "after exact locator + attribution + hypothesis match + validation."
    )


@app.command("literature-scout")
def literature_scout_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Run the LLM Literature Scout: propose CANDIDATE sources per proof step.

    LLM = scout, kernel = judge. Writes a candidate-only literature_candidates.yaml
    (no source_backed / checked / proof status — those are earned later by
    retrieval + grounding + validation). Re-validates the run (fail closed).
    """
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    client = build_llm_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    report = run_literature_scout(run_dir=run, client=client)
    n_steps = len(report["proof_step_candidates"])
    n_cands = sum(len(s["candidates"]) for s in report["proof_step_candidates"])
    typer.echo(
        f"Literature Scout wrote {n_cands} candidate(s) across {n_steps} proof step(s) "
        f"-> {run / 'literature_candidates.yaml'}"
    )
    typer.echo(
        "Candidates only; a source is reference-supported only after retrieval + "
        "exact locator + attribution + hypothesis match + validation."
    )


@app.command("run-llm-agents")
def run_llm_agents(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    verifier_count: int = typer.Option(1, "--verifier-count", min=1, max=3),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
) -> None:
    """Run human-gated prover/reviewer LLM agents and write llm_agent_run.yaml."""
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    provider_diagnostic = _preflight_agent_provider(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        network_check=False,
    )
    manifest_path = run / "run_manifest.yaml"
    report = run_llm_agent_pass(
        run_dir=run,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        verifier_count=verifier_count,
        reviewer_set=reviewer_set,
        provider_diagnostic=provider_diagnostic,
    )
    LLMAgentRunReport.model_validate(report)
    manifest = read_yaml(manifest_path)
    artifacts = manifest["artifacts"]
    loop_report = build_prover_loop_report(
        agent_report=report,
        strategy_brief=read_yaml(run / artifacts["strategy_brief"]),
        human_choice=read_yaml(run / artifacts["human_choice"]),
        run_dir=run,
    )
    ProverLoopReport.model_validate(loop_report)
    manifest.setdefault("optional_artifacts", {})["llm_agent_run"] = "llm_agent_run.yaml"
    manifest.setdefault("optional_artifacts", {})["prover_loop"] = "prover_loop.yaml"
    manifest.setdefault("optional_artifacts", {})["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run / "llm_agent_run.yaml", report)
    write_yaml(run / "prover_loop.yaml", loop_report)
    write_initial_prover_round_artifacts(
        run_dir=run,
        agent_report=report,
        loop_report=loop_report,
    )
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote LLM agent run to {run / 'llm_agent_run.yaml'}")
    typer.echo("Agent output is candidate-only and requires human gate resolution.")


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
        validate_run(run)
    typer.echo(f"Resolved LLM agent human gate as {decision}")


@app.command("run-prover-loop-round")
def run_prover_loop_round(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
) -> None:
    """Append one resolved-gate prover/reviewer loop round (delegates to the driver)."""
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    # Delegate to the single multi-round driver, capped to exactly one more round,
    # so there is one validate-per-round code path.
    manifest = read_yaml(run / "run_manifest.yaml")
    loop_path = manifest.get("optional_artifacts", {}).get("prover_loop")
    if not loop_path:
        raise typer.BadParameter("run has no prover_loop.yaml; resolve the human gate first")
    used = read_yaml(run / loop_path)["budget"]["rounds_used"]
    result = continue_prover_loop(
        run_dir=run,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        max_rounds=used + 1,
    )
    ProverLoopReport.model_validate(result["loop_report"])
    validate_run(run)
    typer.echo(
        f"Prover loop: ran {result['rounds_run']} round(s); stopped: {result['stop_reason']}"
    )


@app.command("feedback-example")
def feedback_example(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    target: str = typer.Option(..., "--target", help="Example id from examples.yaml."),
    label: list[str] | None = typer.Option(None, "--label", "-l", help="Repeatable taste label."),
    reason: str = typer.Option(..., "--reason"),
    scope: str = typer.Option("project", "--scope"),
    action: list[str] | None = typer.Option(None, "--action", "-a", help="Optional repeatable action."),
) -> None:
    """Record confirmed human taste feedback about an example."""
    validate_run(run)
    labels = label or []
    if not labels:
        raise typer.BadParameter("provide at least one --label")
    examples = load_bankable_dict(run, "examples")
    example_ids = {example["id"] for example in examples.get("examples", [])}
    if target not in example_ids:
        raise typer.BadParameter(f"unknown example target {target!r}")

    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    optional = manifest.setdefault("optional_artifacts", {})
    feedback_path = run / optional.get("human_example_feedback", "human_example_feedback.yaml")
    existing = read_yaml(feedback_path) if feedback_path.exists() else None
    if existing is None:
        feedback_data = {
            "schema_version": 1,
            "project_id": manifest["project_id"],
            "claim_id": examples["claim_id"],
            "run_id": manifest["run_id"],
            "created_at": datetime.now(UTC).isoformat(),
            "feedback": [],
        }
    else:
        feedback_data = existing
    next_id = _next_feedback_id(feedback_data.get("feedback", []))
    feedback_data["feedback"].append(
        {
            "feedback_id": next_id,
            "target_type": "example",
            "target_artifact": "examples.yaml",
            "target_id": target,
            "labels": labels,
            "reason": reason,
            "scope": scope,
            "requested_actions": action or _default_feedback_actions(labels),
            "human_confirmed": True,
            "supersedes_feedback_id": None,
        }
    )
    HumanExampleFeedback.model_validate(feedback_data)
    optional["human_example_feedback"] = feedback_path.name
    write_yaml(feedback_path, feedback_data)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote feedback {next_id} to {feedback_path}")


@app.command("summarize-feedback")
def summarize_feedback(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Summarize confirmed human feedback into ranking-only preferences."""
    validate_run(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    feedback_name = manifest.get("optional_artifacts", {}).get("human_example_feedback")
    if not feedback_name:
        raise typer.BadParameter("run has no human_example_feedback optional artifact")
    feedback_path = run / feedback_name
    feedback_data = read_yaml(feedback_path)
    feedback = HumanExampleFeedback.model_validate(feedback_data)
    adjustments = []
    project_preferences = []
    for item in feedback.feedback:
        label_values = {label.value for label in item.labels}
        direction = "down" if label_values & {"misleading", "too_easy", "irrelevant"} else "up"
        weight = "high" if label_values & {"dangerous", "canonical", "favorite"} else "medium"
        adjustments.append(
            {
                "target_id": item.target_id,
                "target_type": item.target_type.value,
                "target_artifact": item.target_artifact,
                "feedback_ids": [item.feedback_id],
                "direction": direction,
                "weight": weight,
                "reason": item.reason,
            }
        )
        project_preferences.append(
            f"{item.target_type.value}:{item.target_id} labeled "
            f"{', '.join(label.value for label in item.labels)}"
        )
    summary_data = {
        "schema_version": 1,
        "claim_id": feedback.claim_id,
        "run_id": feedback.run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": "summarize-feedback",
        "feedback_artifact_path": feedback_name,
        "summarized_from": [item.feedback_id for item in feedback.feedback],
        "input_artifact_digests": {},
        "ranking_adjustments": adjustments,
        "project_preferences": project_preferences,
        "playbook_tuning_candidates": [],
        "memory_entries": [],
    }
    ExampleFeedbackSummary.model_validate(summary_data)
    manifest.setdefault("optional_artifacts", {})["example_feedback_summary"] = "example_feedback_summary.yaml"
    write_yaml(run / "example_feedback_summary.yaml", summary_data)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote feedback summary to {run / 'example_feedback_summary.yaml'}")


@app.command("benchmark")
def benchmark_command(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where benchmark runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the full report as machine-readable JSON (Goal 40A)."
    ),
) -> None:
    """Run the regression benchmark (curated conservative-behaviour cases over the
    existing mock fixtures) and print a stable report. Exits non-zero if any case
    regresses. Never asserts a claim is proved.
    """
    report = run_benchmark(output_root)
    passed = sum(1 for row in report if row["passed"])
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps({"cases": report, "passed": passed, "total": len(report)},
                       indent=2, sort_keys=True),
            encoding="utf-8",
        )
    for row in report:
        mark = "PASS" if row["passed"] else "FAIL"
        typer.echo(f"[{mark}] {row['category']:24s} {row['fixture']:42s} status={row['idea_status']}")
        for problem in row["problems"]:
            typer.echo(f"        - {problem}")
    typer.echo(f"benchmark: {passed}/{len(report)} case(s) passed")
    if passed != len(report):
        raise typer.Exit(code=1)


@app.command()
def calibration(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where calibration runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the full report as machine-readable JSON (Goal 40A)."
    ),
) -> None:
    """Aggregate the product's core SAFETY metrics over the mock fixtures.

    Reports per-fixture validation, source-support false positives, proof-language
    violations, strategy high-score false positives, and memo honesty. All safety
    metrics must be 0; the command exits non-zero on any regression. Earns no
    status and never asserts a claim is proved.
    """
    report = run_calibration(output_root)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    m = report["metrics"]
    for row in report["rows"]:
        mark = "ok " if row["memo_honest"] and not row["source_support_false_positives"] \
            and not row["strategy_high_score_false_positives"] else "BAD"
        typer.echo(
            f"[{mark}] {row['fixture']:44s} validated={row['validated']} "
            f"src_fp={row['source_support_false_positives']} "
            f"hiscore_fp={row['strategy_high_score_false_positives']} "
            f"proof_lang={len(row['proof_language'])}"
        )
    typer.echo(
        f"calibration: {m['fixtures_validated']}/{m['fixtures_total']} validated; "
        f"source-support false positives={m['source_support_false_positives']}; "
        f"proof-language violations={m['proof_language_violations']}; "
        f"high-score false positives={m['strategy_high_score_false_positives']}; "
        f"memo honest={m['memo_honest']}/{m['fixtures_total']}"
    )
    if not m["clean"]:
        raise typer.Exit(code=1)


@app.command()
def scorecard(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where scorecard probe runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Write the full scorecard as machine-readable JSON."
    ),
    markdown_out: Path | None = typer.Option(
        None, "--markdown-out", help="Write the trend-friendly Markdown summary."
    ),
) -> None:
    """Autoprover Behavior Scorecard (Goal 40B): deterministic safety AND
    capability metrics over the mock fixtures — baseline regressions, earning-
    pipeline wiring, seeded-gold-flaw reviewer recall, and a sanitized dogfood
    replay. Exits non-zero on any regression. Metrics never earn or upgrade any
    mathematical status; no claim is ever 'proved' by a scorecard.
    """
    report = run_scorecard(output_root)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown = render_scorecard_markdown(report)
    if markdown_out is not None:
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    if not report["clean"]:
        raise typer.Exit(code=1)


@app.command("loop-cycle")
def loop_cycle(
    out: Path = typer.Option(..., "--out", help="Loop root directory (e.g. loop_runs). Local only; never committed."),
    cases: Path | None = typer.Option(None, "--cases", exists=True, dir_okay=False, help="Dogfood case YAML; defaults to the built-in family set."),
    mode: str = typer.Option("smoke", "--mode", help="smoke = deterministic stub run; deep = external provider."),
    cycle_id: str | None = typer.Option(None, "--cycle-id", help="Override the auto-numbered cycle id."),
    max_cases: int = typer.Option(5, "--max-cases", min=1, max=6),
) -> None:
    """Goal 42A: run ONE observational dogfood improvement cycle.

    Runs Espresso on 3-5 dogfood cases through the existing runtime and writes
    the durable cycle folder (cases, run manifest, agent events/outputs,
    timings, bottlenecks, failures, refutations, blockers, strategy trace,
    subgoal progress, theorem-use ledger, summary) plus campaign-level
    campaign_index / gap_ledger / loop_memory / stop_gate. Strictly
    observational: it never changes code, never opens PRs, and no loop record
    can upgrade any proof/source/evidence/grade/refutation/claim status.
    """
    from .loop_harness import run_loop_cycle

    cycle_dir = run_loop_cycle(
        out_root=out, cases_path=cases, mode=mode, cycle_id=cycle_id, max_cases=max_cases
    )
    typer.echo(f"Cycle recorded at {cycle_dir}")
    typer.echo(f"Campaign files updated under {out}")


@app.command("loop-closure-scorecard")
def loop_closure_scorecard_cmd(
    cycle: Path = typer.Option(..., "--cycle", exists=True, file_okay=False, help="A recorded loop cycle directory."),
    before: Path | None = typer.Option(None, "--before", exists=True, file_okay=False, help="A baseline cycle dir to compare against (before/after replay)."),
    require: list[str] = typer.Option(None, "--require", help="Extra handoff predicate(s) the gate must satisfy (repeatable), e.g. --require reviewer_objection_bound_to_subgoal."),
) -> None:
    """Goal 54D: machine-check whether the human-in-the-loop loop CLOSED.

    Reads a recorded dogfood cycle and writes loop_closure_scorecard.yaml +
    loop_closure_summary.md: SAFETY (no unauthorized proof/source/evidence/grade/
    claim/human_confirmed upgrade, no proof-complete language), HANDOFFS (human
    feedback used, subgoal targeted + bound, reviewer objection bound to a subgoal
    and written to the graph, ...), OBSERVABILITY (events/timings/agent_events),
    and optional before/after replay. This is a WORKFLOW REPLAY check only — it is
    NOT a proof of any theorem and upgrades no mathematical status. Exits non-zero
    when the workflow gate fails so it can be used as a CI/replay gate.

    Canonical replay command:
        espresso loop-cycle --out <root> --mode smoke      # baseline cycle
        # apply the selected fix, then:
        espresso loop-cycle --out <root> --mode smoke      # replay cycle
        espresso loop-closure-scorecard --cycle <replay_cycle> --before <baseline_cycle>
    """
    from .loop_closure_scorecard import write_loop_closure_scorecard

    required = list(require) if require else None
    scorecard = write_loop_closure_scorecard(cycle, required_handoffs=required, before_cycle_dir=before)
    verdict = "PASSED" if scorecard.workflow_gate_passed else "FAILED"
    typer.echo(f"Loop-closure workflow replay {verdict}: {cycle}")
    typer.echo(f"  scorecard: {cycle / 'loop_closure_scorecard.yaml'}")
    typer.echo(f"  summary:   {cycle / 'loop_closure_summary.md'}")
    for failure in scorecard.gate_failures:
        typer.echo(f"  - FAIL: {failure}")
    if not scorecard.workflow_gate_passed:
        raise typer.Exit(code=1)


@app.command("loop-review")
def loop_review(
    cycle: Path = typer.Option(..., "--cycle", exists=True, file_okay=False, help="A recorded loop cycle directory."),
) -> None:
    """Goal 42B: run the six independent artifact-grounded reviewers over a
    recorded cycle. Read-only over product behavior: writes only
    review_reports/ and the clearly-marked planted calibration probe inside
    the cycle folder. Actionable findings always cite evidence paths;
    unevidenced impressions are advisory and can never drive an autofix.
    """
    from .loop_reviewers import run_review_pass

    reports_dir = run_review_pass(cycle)
    typer.echo(f"Six review reports written to {reports_dir}")


@app.command("loop-triage")
def loop_triage(
    cycle: Path = typer.Option(..., "--cycle", exists=True, file_okay=False, help="A reviewed loop cycle directory."),
    plan_file: list[str] = typer.Option([], "--plan-file", help="Expected file to change (repeatable; max 8)."),
    plan_test: list[str] = typer.Option([], "--plan-test", help="Focused test for the fix (repeatable)."),
    replay_case: list[str] = typer.Option([], "--replay-case", help="Case id to replay before/after the fix (repeatable)."),
    why: str = typer.Option("", "--why", help="Why this gap now (required when planning)."),
    extra_reviewer: list[str] = typer.Option([], "--extra-reviewer", help="Additional required reviewer (repeatable)."),
) -> None:
    """Goal 42C: aggregate the six reviews and choose EXACTLY ONE evidenced
    gap (kernel-safety first, then status overclaims, then math capability).
    Writes triage.yaml; oversized findings become new-goal proposals instead
    of broad fixes. With --plan-* arguments it also writes the bounded
    autofix_plan.yaml (8 files / 800 lines, Engineering + Kernel Safety
    mandatory, replay cases required; protected safety surfaces refused).
    Never changes code.
    """
    from .loop_triage import build_autofix_plan, run_triage

    decision = run_triage(cycle)
    typer.echo(f"Selected gap: {decision.selected_gap_id} ({decision.selected_gap_kind})")
    if decision.new_goal_proposal_path:
        typer.echo(f"Oversized finding converted to goal proposal: {decision.new_goal_proposal_path}")
    if plan_file or plan_test or replay_case:
        if not (plan_file and plan_test and replay_case and why.strip()):
            typer.echo("Planning requires --plan-file, --plan-test, --replay-case and --why together.")
            raise typer.Exit(code=2)
        plan = build_autofix_plan(
            cycle,
            files_expected_to_change=list(plan_file),
            focused_tests=list(plan_test),
            replay_cases=list(replay_case),
            why_this_gap_now=why,
            extra_reviewers=list(extra_reviewer),
        )
        typer.echo(f"Bounded autofix plan written for {plan.selected_gap_id}")
    else:
        typer.echo("No autofix plan written (provide --plan-* after investigating the gap).")


@app.command("loop-replay")
def loop_replay(
    cycle: Path = typer.Option(..., "--cycle", exists=True, file_okay=False),
    checkpoint: str = typer.Option(..., "--checkpoint", help="baseline | pre_merge | post_merge"),
    probe: str = typer.Option(..., "--probe", help="Named deterministic problem probe."),
    case: str = typer.Option(..., "--case", help="Triggering case id."),
    sibling: list[str] = typer.Option([], "--sibling", help="Sibling case id (repeatable)."),
    observed: str = typer.Option(..., "--observed", help="The observed problem being fixed."),
    before_evidence: str = typer.Option(..., "--before-evidence", help="Evidence path of the BEFORE state."),
    mode: str = typer.Option("smoke", "--mode"),
) -> None:
    """Goal 42D: replay the triggering dogfood case (+ siblings/holdouts)
    through the real runtime and write replay_result.yaml. A replay without
    improvement or without sibling/holdout no-regression evidence is never
    successful; a failed pre-merge replay BLOCKS the merge (exit 1).
    """
    from .io import read_yaml as _read_yaml
    from .loop_harness import load_cases
    from .loop_replay import run_replay

    # blinded holdouts: never cases that were in the reviewed cycle (those
    # were used to select the fix), never the trigger/siblings.
    recorded = {
        c.get("case_id")
        for c in _read_yaml(cycle / "cases.yaml").get("cases", [])
    }
    holdouts = [
        c for c in load_cases(None)
        if c.case_id not in recorded and c.case_id not in {case, *sibling}
    ][:2]
    try:
        result = run_replay(
            cycle,
            checkpoint=checkpoint,
            probe=probe,
            triggering_case_id=case,
            sibling_case_ids=list(sibling),
            holdout_cases=holdouts,
            observed_problem=observed,
            before_evidence_path=before_evidence,
            mode=mode,
        )
    except RuntimeError as exc:
        typer.echo(f"REPLAY BLOCKED MERGE: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Replay [{checkpoint}] fixed={result.after.fixed} "
        f"successful={result.successful} ({result.after.observed_problem})"
    )
    if not result.successful and checkpoint != "baseline":
        raise typer.Exit(code=1)


@app.command("loop-stop-gate")
def loop_stop_gate_command(
    out: Path = typer.Option(..., "--out", exists=True, file_okay=False, help="Loop root directory."),
) -> None:
    """Goal 42D/E: recompute the machine-checkable campaign stop gate from
    artifacts and command results. SUCCESS requires >=5 cycles, two
    consecutive clean cycles on an unchanged commit, all eight gates, and no
    open P0/P1; hitting max_cycles/max_wall_time is STOP_BUDGET_EXHAUSTED,
    never acceptance.
    """
    from .loop_stop_gate import evaluate_stop_gate

    gate = evaluate_stop_gate(out, repo_root=Path.cwd())
    typer.echo(
        f"stop_gate: {gate.status} (cycles={gate.cycles_completed}, "
        f"clean streak={gate.consecutive_clean_cycles_observed}/"
        f"{gate.consecutive_clean_cycles_required})"
    )
    for name in ("safety", "capability", "ux", "dogfood_coverage", "replay", "holdout", "scorecard", "calibration"):
        state = getattr(gate, f"{name}_gate")
        typer.echo(f"  {name}_gate: {'PASS' if state.passed else 'fail'} — {state.note[:120]}")


@app.command()
def eval(
    mode: str = typer.Option("mock", "--mode"),
    schemas_only: bool = typer.Option(False, "--schemas-only"),
    fixture: Path | None = typer.Option(None, "--fixture", exists=True, file_okay=False),
) -> None:
    """Run deterministic P0 evals."""
    if schemas_only:
        _validate_seed_files()
        typer.echo("Schema-only validation passed")
        return
    if mode != "mock":
        raise typer.BadParameter("P0 eval only supports --mode mock")
    fixture_dirs = [fixture] if fixture else sorted(Path("tests/fixtures").iterdir())
    passed = 0
    for fixture_dir in fixture_dirs:
        if not fixture_dir.is_dir():
            continue
        input_path = fixture_dir / "input.md"
        expected_path = fixture_dir / "expected.yaml"
        rubric_path = fixture_dir / "rubric.md"
        missing = [
            path.name
            for path in (input_path, expected_path, rubric_path)
            if not path.exists()
        ]
        if missing:
            raise KernelValidationError(
                f"{fixture_dir}: fixture is missing required files {missing}"
            )
        run_dir = run_mock_critique("hodge_atoms", input_path, Path(".eval_runs") / fixture_dir.name)
        validate_run(run_dir)
        _assert_fixture_expectations(run_dir, read_yaml(expected_path))
        passed += 1
        typer.echo(f"PASS {fixture_dir.name}")
    typer.echo(f"{passed} fixture(s) passed")


@app.command("generate-agent")
def generate_agent(
    prompt: Path = typer.Option(..., "--prompt", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("generated_agent", "--project", "-p"),
    mode: str = typer.Option("v0", "--mode"),
) -> None:
    """Create a schema-checked draft agent workflow from a research prompt."""
    if mode not in {"v0", "stub"}:
        raise typer.BadParameter("P0 only supports --mode v0")
    text = read_text(prompt)
    project_dir = Path("projects") / project
    project_dir.mkdir(parents=True, exist_ok=True)
    spec = generate_workflow_spec(text, project_name=project)
    WorkflowSpec.model_validate(spec)
    contracts = default_agent_contracts(project)
    AgentContracts.model_validate(contracts)
    trial_dir = project_dir / "trial_fixture"
    trial_dir.mkdir(parents=True, exist_ok=True)
    trial_input = trial_dir / "input.md"
    if not trial_input.exists():
        write_text(
            trial_input,
            "Draft trial fixture. Replace with a representative proof sketch before admission.\n",
        )
    trial_expected = trial_dir / "expected.yaml"
    if not trial_expected.exists():
        write_yaml(
            trial_expected,
            {
                "fixture": "draft_trial_fixture",
                "required": {
                    "idea_status": ["fragile", "likely_false", "unclear"],
                    "forbidden_final_report_phrases": ["verified", "proof complete"],
                },
            },
        )
    admission = {
        "project_id": project,
        "workflow_spec_valid": True,
        "agent_contracts_exist": True,
        "required_artifacts_declared": True,
        "trial_fixture_exists": trial_input.exists() and trial_expected.exists(),
        "human_confirmed": False,
        "admitted": False,
        "blockers": ["human confirmation required before this workflow is reusable"],
    }
    AdmissionCheck.model_validate(admission)
    write_yaml(project_dir / "workflow_spec.yaml", spec)
    write_yaml(project_dir / "agent_contracts.yaml", contracts)
    write_yaml(project_dir / "admission_check.yaml", admission)
    write_text(
        project_dir / "playbook.md",
        "# Generated Agent Draft\n\n"
        "This is a deterministic P0 workflow draft. It is schema-valid but must be reviewed by the mathematician before use.\n\n"
        f"- Domain: {spec.get('domain')}\n"
        f"- Domain key: {spec.get('domain_key', 'unknown')}\n"
        f"- Uses live LLM: {spec.get('source', {}).get('uses_live_llm', False)}\n"
        f"- Uses network: {spec.get('source', {}).get('uses_network', False)}\n\n"
        "## Research Prompt\n\n"
        f"{text}\n",
    )
    typer.echo(f"Wrote draft agent to {project_dir}")
    typer.echo("Admission status: not admitted; human confirmation required")


@app.command("improve-workflow")
def improve_workflow(
    project: str = typer.Option(..., "--project", "-p"),
    note: str | None = typer.Option(None, "--note"),
    note_file: Path | None = typer.Option(None, "--note-file", exists=True, dir_okay=False),
) -> None:
    """Record a workflow improvement request without changing the checked spec automatically."""
    project_dir = Path("projects") / project
    spec_path = project_dir / "workflow_spec.yaml"
    if not spec_path.exists():
        raise typer.BadParameter(f"missing workflow spec: {spec_path}")
    WorkflowSpec.model_validate(read_yaml(spec_path))
    if note_file is not None:
        update = read_text(note_file)
    elif note is not None:
        update = note
    else:
        raise typer.BadParameter("provide --note or --note-file")
    update_path = project_dir / "workflow_updates.md"
    existing = read_text(update_path) if update_path.exists() else "# Workflow Updates\n\n"
    write_text(update_path, f"{existing.rstrip()}\n\n## Pending Update\n\n{update.strip()}\n")
    typer.echo(f"Recorded workflow update in {update_path}")


def _validate_seed_files() -> None:
    WorkflowSpec.model_validate(read_yaml(Path("projects/hodge_atoms/workflow_spec.yaml")))
    ExampleBank.model_validate(read_yaml(Path("projects/hodge_atoms/example_bank.yaml")))
    contract_path = Path("projects/hodge_atoms/agent_contracts.yaml")
    if contract_path.exists():
        AgentContracts.model_validate(read_yaml(contract_path))
    coherent_dir = Path("projects/coherent_base_change")
    if coherent_dir.exists():
        WorkflowSpec.model_validate(read_yaml(coherent_dir / "workflow_spec.yaml"))
        ExampleBank.model_validate(read_yaml(coherent_dir / "example_bank.yaml"))
        AgentContracts.model_validate(read_yaml(coherent_dir / "agent_contracts.yaml"))


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
