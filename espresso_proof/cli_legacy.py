"""Legacy compatibility commands.

Registered on the shared Typer ``app`` from ``espresso_proof.cli_app``
(flat command names, identical behavior). Imported for its side effects at
the bottom of cli.py."""
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

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _guard_windows_run_path_length,
    _resolve_front_half_mode,
)


@app.command("critique-sketch")
def critique_sketch(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    mode: str = typer.Option("mock", "--mode"),
) -> None:
    """Run a schema-checked mock critique pass."""
    if mode != "mock":
        raise typer.BadParameter("P0 only supports --mode mock")
    _guard_windows_run_path_length(Path("."), project)
    run_dir = run_mock_critique(project, input, Path("."))
    typer.echo(f"Wrote run artifacts to {run_dir}")


@app.command("run-flow")
def run_flow(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    literature_mode: str = typer.Option("local", "--literature-mode"),
    literature_provider: list[str] | None = typer.Option(None, "--literature-provider"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("auto", "--front-half"),
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
    if front_half not in {"auto", "mock", "stub", "openai", "external"}:
        raise typer.BadParameter("front-half must be auto, mock, stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    front_half = _resolve_front_half_mode(
        provider=provider, front_half=front_half, command_name="run-flow"
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
        literature_providers=literature_provider,
        max_references=max_references,
        agent_network_preflight=agent_network_preflight,
    )

    typer.echo(f"Wrote run artifacts to {result['run_dir']}")
    typer.echo(f"Literature Discovery: {literature_mode}")
    typer.echo(f"Front-half agents: mode={front_half}")
    typer.echo(f"LLM agents: provider={provider}, reviewer_set={reviewer_set}")
    typer.echo(f"Human gate: {result['human_gate'].get('status', 'unknown')}")
