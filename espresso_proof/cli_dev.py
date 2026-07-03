"""Developer / diagnostic commands (not part of the product runtime).

Registered on the shared Typer ``app`` from ``espresso_proof.cli_app``
(flat command names, identical behavior). Imported for its side effects at
the bottom of cli.py."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from espresso_proof.kernel.contracts import default_agent_contracts
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
from espresso_proof.agents.llm_agents import build_client as build_llm_client
from espresso_proof.agents.llm_agents import build_prover_loop_report
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.agents.llm_agents import external_agent_diagnostic
from espresso_proof.agents.llm_agents import openai_agent_diagnostic
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
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.kernel.subgoal_graph import build_subgoal_graph
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.kernel.proof_language import scrub_proof_language
from espresso_proof.kernel.surface_warnings import (
    collect_surface_warnings,
    refresh_final_report_disclosures,
    render_warning_lines,
)
from espresso_proof.evaluation.reviewer_checklist import review_checklist
from espresso_proof.memory.memory import summarize_memory
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.literature.pdf_ingestion import (
    extract_candidate_locators,
    extract_source_text,
    ingest_source_for_theorems,
    read_source_bytes,
)
from espresso_proof.kernel.external_tool_runner import (
    DEFAULT_TIMEOUT_SECONDS,
    FAKE_TOOLS,
    UnknownToolError,
    run_and_record_external_tool,
    run_completed_cleanly,
)
from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
from espresso_proof.kernel.models import (
    AdmissionCheck,
    AgentContracts,
    ExampleFeedbackSummary,
    HumanExampleFeedback,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    ProverLoopReport,
    ProofGapResolutionReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
    WorkflowSpec,
)
from espresso_proof.kernel.schema_export import check_schemas, write_schemas
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.orchestration.workflow_generator import generate_workflow_spec

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _default_feedback_actions,
    _focused_prover_loop_attempt,
    _guard_windows_run_path_length,
    _mark_failure_patterns_done_if_board_exists,
    _next_feedback_id,
    _optional_artifact_path,
    _preflight_agent_provider,
    _refresh_derived_views,
    _refresh_process_status,
    _require_claim_clear_for_cli,
)


@app.command("export-schemas")
def export_schemas_command(
    check: bool = typer.Option(
        False,
        "--check",
        help="Fail if local exported schemas are stale.",
    ),
    out_dir: Path = typer.Option(Path("schemas"), "--out-dir", file_okay=False),
) -> None:
    """Export local JSON Schemas from the package model registry."""

    if check:
        raise typer.Exit(code=check_schemas(out_dir))
    write_schemas(out_dir)
    typer.echo(f"Exported schemas to {out_dir}")


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
    _guard_windows_run_path_length(output_root, project)
    run_dir = run_mock_critique(project, input, output_root)
    validate_run(run_dir)
    typer.echo(f"Wrote run artifacts to {run_dir}")


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
    pattern_audit = audit_failure_patterns(run)
    _mark_failure_patterns_done_if_board_exists(run)
    _refresh_process_status(run)
    categories = ", ".join(item["category"] for item in report["classifications"]) or "none"
    typer.echo(f"Classified {len(report['classifications'])} failure(s): {categories}")
    typer.echo(f"-> {run / 'failure_classification.yaml'}")
    typer.echo(
        f"Pattern audit: {len(pattern_audit['matches'])} match(es) -> "
        f"{run / 'failure_pattern_audit.yaml'}"
    )


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
    from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

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
    _mark_failure_patterns_done_if_board_exists(run)
    _refresh_process_status(run)
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
    """Render a dev-only static HTML dashboard of a run's research banks.

    Reads examples / counterexamples / strategies through the bank-aware loaders
    (bank directories OR single YAML) and never mutates any artifact. Statuses are
    shown as recorded in human-readable wording; no proof-complete language.
    Canonical product viewing is the React cockpit served by ``espresso serve``.
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
    """Render the legacy/dev-only static dashboard for one run.

    The canonical human-facing run view is the React cockpit served by
    ``espresso serve``. This command remains for diagnostics/static snapshots and
    is read-only with respect to mathematical artifacts; it never changes
    proof/source/evidence status.
    """
    validate_run(run)
    if out is None:
        out = write_run_dashboard(run)
    else:
        write_text(out, render_run_dashboard(run))
    typer.echo(f"Wrote run dashboard -> {out}")


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
    tests = _artifact("tests")
    theorem_applicability = {}
    theorem_applicability_path = _optional_artifact_path(
        run, optional_artifacts, "theorem_applicability"
    )
    if theorem_applicability_path is not None:
        theorem_applicability = read_yaml(theorem_applicability_path)
    update_inbox = {}
    update_inbox_path = _optional_artifact_path(run, optional_artifacts, "update_inbox")
    if update_inbox_path is not None:
        update_inbox = read_yaml(update_inbox_path)
    line_numbered_proof = {}
    line_numbered_proof_path = _optional_artifact_path(
        run, optional_artifacts, "line_numbered_proof"
    )
    if line_numbered_proof_path is not None:
        line_numbered_proof = read_yaml(line_numbered_proof_path)
    agent_consensus_review = {}
    agent_consensus_review_path = _optional_artifact_path(
        run, optional_artifacts, "agent_consensus_review"
    )
    if agent_consensus_review_path is not None:
        agent_consensus_review = read_yaml(agent_consensus_review_path)

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
    warning_lines = render_warning_lines(collect_surface_warnings(
        grade=grade,
        review=review,
        examples=examples,
        counterexamples=counterexamples,
        tests=tests,
        source_grounding=source_grounding,
        theorem_applicability=theorem_applicability,
        update_inbox=update_inbox,
        line_numbered_proof=line_numbered_proof,
        agent_consensus_review=agent_consensus_review,
    ))
    if warning_lines:
        _echo("")
        _echo("Open Blockers / Pending Checks")
        for line in warning_lines:
            _echo(f"- {line}")
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
    if theorem_applicability_path is not None:
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
    refresh_final_report_disclosures(run)
    validate_run(run)
    typer.echo(f"Wrote theorem applicability audit to {run / 'theorem_applicability.yaml'}")


@app.command("review-theorem-applicability")
def review_theorem_applicability_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
) -> None:
    """Run source-match and hypothesis LLM reviewers over theorem applicability.

    Writes theorem_applicability_review.yaml and folds the structured reviewer
    verdict into theorem_applicability.yaml. This does not upgrade source status.
    """
    from espresso_proof.verification.theorem_applicability import (
        review_theorem_applicability_with_llm,
    )

    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    report = review_theorem_applicability_with_llm(
        run,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    _refresh_process_status(run)
    typer.echo(
        f"Reviewed theorem applicability with {len(report['reviews'])} reviewer result(s); "
        "source status unchanged."
    )
    typer.echo(f"-> {run / 'theorem_applicability_review.yaml'}")


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


@app.command("resolve-proof-gaps")
def resolve_proof_gaps_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    provider: str = typer.Option("stub", "--provider"),
    model: str = typer.Option("gpt-4.1", "--model"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND", "--external-agent-command-env"
    ),
    human_allow: list[str] | None = typer.Option(
        None,
        "--human-allow",
        help="Repeatable explicit advisory theorem allowance: LINE_ID or LINE_ID@DEPENDENCY_REF, then |REF|THEOREM_STATEMENT.",
    ),
) -> None:
    """Resolve line-numbered proof gaps into candidate repairs.

    Default mode calls a resolver agent and attaches candidate theorem refs
    only; it does not clear gaps or seal anything. ``--human-allow`` records an
    explicit user-allowed theorem ref for advisory consensus, not source-grounded
    or machine-verified proof.
    """

    from espresso_proof.orchestration.proof_gap_resolver import resolve_proof_gaps

    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    validate_run(run)
    _require_claim_clear_for_cli(run)
    with run_writer_lock(run, action="proof gap resolver"):
        try:
            report = resolve_proof_gaps(
                run,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
                human_allow=human_allow or [],
            )
            ProofGapResolutionReport.model_validate(report)
        except (RuntimeError, ValueError) as exc:
            # Friendly fail-closed surface (same as write-proof/seal-consensus):
            # a dying/timing-out external agent must not print a raw traceback.
            typer.echo(f"No repair plan written (failed closed): {exc}")
            _refresh_process_status(run)
            raise typer.Exit(code=1) from exc
    _refresh_process_status(run)
    attached = sum(
        1
        for item in report["candidates"]
        if item["action"]
        in {"attach_candidate_theorem_ref", "attach_human_allowed_theorem_ref"}
    )
    typer.echo(
        f"Wrote proof_gap_resolution.yaml ({len(report['candidates'])} candidate repair(s), "
        f"{attached} attached to proof graph)."
    )
    typer.echo(
        "Candidate source repairs stay advisory; rerun seal-consensus only after "
        "source/applicability review or explicit human allowance."
    )


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
