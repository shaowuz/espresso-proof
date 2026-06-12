from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .io import read_text, read_yaml
from .research_bank import (
    load_counterexamples_artifact,
    load_examples_artifact,
    load_strategy_synthesis_artifact,
)
from .models import (
    AgentContracts,
    AgentRunStatusReport,
    AttemptStatus,
    AutonomousLiteratureRetrievalReport,
    Claim,
    ComputationChecks,
    ComputationVerificationReport,
    CounterexampleReport,
    ExternalToolCandidatesReport,
    ExternalToolRunsReport,
    FailureClassificationReport,
    FailurePatternAudit,
    LiteratureCandidates,
    MemoryContext,
    RetrievalResolutionReport,
    LiteratureRetrievalReport,
    LiteratureSearchPlan,
    MathematicalJudgmentReport,
    ProofGoalGraph,
    ProofVerificationReport,
    ProofVerificationStatus,
    SubgoalState,
    ContinueDecision,
    ExampleSelection,
    ExampleFeedbackSummary,
    HypothesisLedger,
    HumanChoice,
    HumanExampleFeedback,
    HumanChoiceStatus,
    IdeaGrade,
    IdeaStatus,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    LoopControl,
    ProofLanguageStatus,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
    Review,
    VerificationVerdict,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
)
from .strategy_rules import validate_strategy_rules
from .validation.errors import KernelValidationError
from .validation.front_half import validate_front_half_run
from .validation.evidence import (
    _validate_computation_verification,
    _validate_example_evidence,
    _validate_test_report_evidence,
)
from .validation.manifest import ManifestPathValidationError
from .validation.manifest import validate_manifest_paths


def validate_yaml_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc


def validate_run(run_dir: Path) -> None:
    manifest = validate_yaml_model(run_dir / "run_manifest.yaml", RunManifest)
    _validate_manifest_paths(run_dir, manifest)
    artifacts = manifest.artifacts

    claim = validate_yaml_model(run_dir / artifacts["locked_claim"], Claim)
    agent_contracts = validate_yaml_model(run_dir / artifacts["agent_contracts"], AgentContracts)
    hypothesis_ledger = validate_yaml_model(run_dir / artifacts["hypothesis_ledger"], HypothesisLedger)
    literature_review = validate_yaml_model(
        run_dir / artifacts["literature_review"], LiteratureReviewReport
    )
    examples = load_examples_artifact(run_dir / artifacts["examples"])
    computations = validate_yaml_model(run_dir / artifacts["computation_checks"], ComputationChecks)
    computation_verification = _load_optional_computation_verification(run_dir, manifest)
    failure_classification = _load_optional_failure_classification(run_dir, manifest)
    mathematical_judgment = _load_optional_mathematical_judgment(run_dir, manifest)
    failure_pattern_audit = _load_optional_failure_pattern_audit(run_dir, manifest)
    literature_retrieval = _load_optional_literature_retrieval(run_dir, manifest)
    proof_verification = _load_optional_proof_verification(run_dir, manifest)
    external_tool_candidates = _load_optional_external_tool_candidates(run_dir, manifest)
    external_tool_runs = _load_optional_external_tool_runs(run_dir, manifest)
    counterexamples = load_counterexamples_artifact(run_dir / artifacts["counterexamples"])
    tests = validate_yaml_model(run_dir / artifacts["tests"], TestReport)
    source_grounding = validate_yaml_model(run_dir / artifacts["source_grounding"], SourceGroundingReport)
    review = validate_yaml_model(run_dir / artifacts["review"], Review)
    idea_grade = validate_yaml_model(run_dir / artifacts["idea_grade"], IdeaGrade)
    loop_control = validate_yaml_model(run_dir / artifacts["loop_control"], LoopControl)
    strategy_synthesis = load_strategy_synthesis_artifact(
        run_dir / artifacts["strategy_synthesis"]
    )
    strategy_brief = validate_yaml_model(run_dir / artifacts["strategy_brief"], StrategyBrief)
    human_choice = validate_yaml_model(run_dir / artifacts["human_choice"], HumanChoice)
    theorem_extraction = _load_optional_theorem_extraction(run_dir, manifest)
    _load_optional_literature_review_agent(run_dir, manifest)
    _load_optional_literature_search_plan(run_dir, manifest)
    _load_optional_autonomous_literature_retrieval(run_dir, manifest)
    _load_optional_literature_candidates(run_dir, manifest)
    _load_optional_retrieval_resolution(run_dir, manifest)
    _load_optional_memory_context(run_dir, manifest)
    # Phase 03: content-validate the durable front_half_run audit artifact when
    # present (claim/run ids, stage names, no error-as-ok, path confinement). A
    # run without it still validates (old-run compatible).
    validate_front_half_run(run_dir)
    theorem_applicability = _load_optional_theorem_applicability(run_dir, manifest)
    llm_agent_run = _load_optional_llm_agent_run(run_dir, manifest)
    prover_loop = _load_optional_prover_loop(run_dir, manifest)
    prover_round_index = _load_optional_prover_round_index(run_dir, manifest)
    proof_goal_graph = _load_optional_proof_goal_graph(run_dir, manifest)
    agent_run_status = _load_optional_agent_run_status(run_dir, manifest)
    human_feedback, feedback_summary = _load_optional_human_feedback(run_dir, manifest)

    _validate_claim_id_consistency(
        claim,
        human_choice,
        hypothesis_ledger,
        literature_review,
        examples,
        computations,
        counterexamples,
        tests,
        source_grounding,
        review,
        idea_grade,
        loop_control,
        strategy_synthesis,
        strategy_brief,
    )
    _validate_agent_contracts(agent_contracts, artifacts)
    _validate_literature_review_separation(literature_review)
    _validate_test_report_evidence(computations, examples, counterexamples, tests)
    _validate_computation_verification(computations, computation_verification)
    _validate_failure_classification(failure_classification, counterexamples, claim)
    _validate_refuted_grade_admissible(idea_grade, counterexamples)
    _validate_mathematical_judgment(mathematical_judgment, claim, manifest)
    _validate_failure_pattern_audit(failure_pattern_audit, claim, manifest)
    _validate_literature_retrieval(literature_retrieval, claim, manifest)
    _validate_proof_verification(proof_verification, claim, manifest, idea_grade)
    _validate_grade_proof_language_backed(
        idea_grade, computation_verification, proof_verification
    )
    _validate_external_tool_artifacts(
        external_tool_candidates, external_tool_runs, claim, manifest
    )
    _validate_subgoal_graph(
        proof_goal_graph,
        claim,
        manifest,
        source_grounding,
        proof_verification,
        computation_verification,
        idea_grade,
        strategy_brief,
    )
    _validate_example_evidence(computations, counterexamples, idea_grade)
    _validate_source_backing(source_grounding)
    _validate_source_discipline_bindings(
        run_dir, manifest, source_grounding, theorem_extraction, theorem_applicability
    )
    _validate_theorem_applicability(manifest, claim, source_grounding, theorem_applicability)
    _validate_review_outcome(review, idea_grade)
    _validate_loop_control(loop_control)
    _validate_strategy_synthesis(
        strategy_synthesis,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )
    _validate_strategy_brief(
        strategy_brief,
        strategy_synthesis,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )
    _validate_human_choice(human_choice, strategy_brief)
    _validate_human_feedback(
        manifest,
        claim,
        examples,
        counterexamples,
        tests,
        source_grounding,
        strategy_brief,
        human_feedback,
        feedback_summary,
    )
    _validate_llm_loop_artifacts(
        run_dir,
        manifest,
        claim,
        llm_agent_run,
        prover_loop,
        prover_round_index,
        strategy_brief,
    )
    _validate_agent_run_status(agent_run_status, claim, manifest, run_dir)
    _validate_final_report_language(run_dir / artifacts["final_report"], idea_grade)


def _load_optional_agent_run_status(
    run_dir: Path,
    manifest: RunManifest,
) -> AgentRunStatusReport | None:
    optional = manifest.optional_artifacts
    if "agent_run_status" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["agent_run_status"],
        AgentRunStatusReport,
    )


def _validate_agent_run_status(
    agent_run_status: AgentRunStatusReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    """The per-agent run-status board (Goal 27) is advisory process state.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof/grade status field and StrictBaseModel forbids extras,
    so a board that smuggles in ``source_backed`` / checked evidence / a proof or
    grade status is already rejected at load (validate_yaml_model). Here we bind
    the board to THIS run and harden the evidence-gate flag:

    - claim_id must match the locked claim and run_id must match the manifest;
    - every recorded ``artifact_path`` must resolve inside the run directory;
    - ``safe_to_use_as_evidence`` (already gated to status='done' at the model)
      additionally requires the recorded artifact to exist on disk -- a board
      can never mark an absent/rolled-back artifact as usable evidence.

    The board cannot upgrade any status by construction; this binding just keeps
    the dashboard's "this agent is done" claim honest against the real artifacts.
    """
    if agent_run_status is None:
        return
    if agent_run_status.claim_id != claim.id:
        raise KernelValidationError(
            "agent_run_status claim_id does not match locked claim"
        )
    if agent_run_status.run_id != manifest.run_id:
        raise KernelValidationError(
            "agent_run_status run_id does not match run manifest"
        )
    run_root = run_dir.resolve()
    for entry in agent_run_status.agents:
        if entry.artifact_path is not None:
            resolved = (run_dir / entry.artifact_path).resolve()
            if resolved != run_root and run_root not in resolved.parents:
                raise KernelValidationError(
                    f"agent_run_status artifact_path escapes run directory: "
                    f"{entry.artifact_path!r}"
                )
            if entry.safe_to_use_as_evidence and not resolved.exists():
                raise KernelValidationError(
                    f"agent_run_status marks {entry.agent!r} safe_to_use_as_evidence "
                    "but its artifact is missing"
                )


def _load_optional_human_feedback(
    run_dir: Path,
    manifest: RunManifest,
) -> tuple[HumanExampleFeedback | None, ExampleFeedbackSummary | None]:
    optional = manifest.optional_artifacts
    feedback = None
    summary = None
    if "human_example_feedback" in optional:
        feedback = validate_yaml_model(
            run_dir / optional["human_example_feedback"],
            HumanExampleFeedback,
        )
    if "example_feedback_summary" in optional:
        summary = validate_yaml_model(
            run_dir / optional["example_feedback_summary"],
            ExampleFeedbackSummary,
        )
    return feedback, summary


def _load_optional_computation_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ComputationVerificationReport | None:
    optional = manifest.optional_artifacts
    if "computation_verification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["computation_verification"],
        ComputationVerificationReport,
    )


def _load_optional_failure_classification(
    run_dir: Path,
    manifest: RunManifest,
) -> FailureClassificationReport | None:
    optional = manifest.optional_artifacts
    if "failure_classification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_classification"],
        FailureClassificationReport,
    )


def _has_admissible_counterexample(counterexamples: CounterexampleReport) -> bool:
    """Single source of truth for "what counts as a real refutation".

    True iff some counterexample is an admissible CHECKED refutation: it satisfies
    every claim hypothesis (``satisfies_all_claim_hypotheses is True``) AND refutes
    the claim. ``refutes_claim`` already requires a checked ``failed`` result and
    forbids dropped-hypothesis / proof-step-only cases at the model level
    (``CounterexampleTest.validate_refutation_scope``). Both the failure-
    classification gate and the idea-grade ``refuted`` gate use this, so the
    refutation definition cannot drift between them.
    """
    return any(
        test.satisfies_all_claim_hypotheses is True and test.refutes_claim
        for test in counterexamples.tests
    )


def _validate_failure_classification(
    failure_classification: FailureClassificationReport | None,
    counterexamples: CounterexampleReport,
    claim: Claim,
) -> None:
    """Failure classification is advisory routing; it must not fabricate a refutation.

    Only runs when the optional artifact is present. Rules:

    - claim_id must match the locked claim;
    - a ``claim_refuted`` classification requires an admissible all-hypotheses
      counterexample (satisfies_all_claim_hypotheses is True and refutes_claim),
      so a dropped-hypothesis or proof-step failure can never be promoted to a
      claim refutation through routing.

    The report model carries no source/evidence/proof status field, so a
    classification cannot upgrade any status by construction.
    """
    if failure_classification is None:
        return
    if failure_classification.claim_id != claim.id:
        raise KernelValidationError(
            "failure_classification claim_id does not match locked claim"
        )
    claims_refuted = any(
        item.category.value == "claim_refuted"
        for item in failure_classification.classifications
    )
    if claims_refuted and not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "failure_classification marks claim_refuted without an admissible "
            "all-hypotheses counterexample"
        )


def _validate_refuted_grade_admissible(
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
) -> None:
    """A claim may be graded ``refuted`` ONLY when an admissible checked
    counterexample exists: a counterexample that satisfies every claim hypothesis
    AND refutes the claim (``refutes_claim`` already requires a checked ``failed``
    result and forbids dropped-hypothesis / proof-step-only cases at the model
    level). This makes ``refuted`` a kernel-earned status, not something model
    prose, a reviewer objection, memory, or a forged grade can assert -- and it
    retroactively hardens the subgoal-graph ``contradicted`` gate, which trusts
    ``idea_grade.status == refuted``.
    """
    if idea_grade.status != IdeaStatus.refuted:
        return
    if not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "idea_grade.status=refuted requires an admissible checked counterexample "
            "(satisfies every claim hypothesis and refutes the claim); a route attack, "
            "reviewer objection, dropped-hypothesis example, or unchecked counterexample "
            "cannot refute the claim"
        )


def _load_optional_mathematical_judgment(
    run_dir: Path,
    manifest: RunManifest,
) -> MathematicalJudgmentReport | None:
    optional = manifest.optional_artifacts
    if "mathematical_judgment" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["mathematical_judgment"],
        MathematicalJudgmentReport,
    )


def _validate_mathematical_judgment(
    mathematical_judgment: MathematicalJudgmentReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The mathematical judgment layer is advisory; it must never upgrade status.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof status field and StrictBaseModel forbids extras, so a
    YAML that smuggles in ``source_backed`` / checked evidence / a proof status is
    already rejected at load (validate_yaml_model). Here we additionally bind the
    report to this run: claim_id must match the locked claim and run_id must match
    the manifest, so a judgment for another claim/run cannot be attached here.
    """
    if mathematical_judgment is None:
        return
    if mathematical_judgment.claim_id != claim.id:
        raise KernelValidationError(
            "mathematical_judgment claim_id does not match locked claim"
        )
    if mathematical_judgment.run_id != manifest.run_id:
        raise KernelValidationError(
            "mathematical_judgment run_id does not match the run manifest"
        )


def _load_optional_failure_pattern_audit(
    run_dir: Path,
    manifest: RunManifest,
) -> FailurePatternAudit | None:
    optional = manifest.optional_artifacts
    if "failure_pattern_audit" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_pattern_audit"],
        FailurePatternAudit,
    )


def _validate_failure_pattern_audit(
    failure_pattern_audit: FailurePatternAudit | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The failure-pattern audit is advisory; it must not relabel or upgrade.

    Only runs when the optional artifact is present. Rules (fail closed):

    - claim_id / run_id must bind to this run;
    - every matched pattern_id must exist in the canonical SEED bank, and its
      tier / action / family / enforced_by must equal the bank verbatim -- so a
      run (or memory, or a human edit) can NEVER promote a candidate pattern to
      ``hard`` or change what it is allowed to do;
    - a ``hard`` match must point at an EXISTING kernel validator
      (``enforced_by`` in ENFORCING_VALIDATORS); it adds no new blocking.

    The audit model carries no source/evidence/proof status field (and extras are
    forbidden), so a match can never upgrade any status or refute the claim.
    """
    if failure_pattern_audit is None:
        return
    # Local import: failure_pattern_bank imports models+io only (no cycle).
    from .failure_pattern_bank import ENFORCING_VALIDATORS, seed_failure_pattern_bank

    if failure_pattern_audit.claim_id != claim.id:
        raise KernelValidationError(
            "failure_pattern_audit claim_id does not match locked claim"
        )
    if failure_pattern_audit.run_id != manifest.run_id:
        raise KernelValidationError(
            "failure_pattern_audit run_id does not match the run manifest"
        )
    canonical = {p.id: p for p in seed_failure_pattern_bank().patterns}
    for match in failure_pattern_audit.matches:
        pattern = canonical.get(match.pattern_id)
        if pattern is None:
            raise KernelValidationError(
                f"failure_pattern_audit references unknown pattern {match.pattern_id!r}"
            )
        if (
            match.tier != pattern.tier
            or match.action != pattern.action
            or match.family != pattern.family
            or match.enforced_by != pattern.enforced_by
        ):
            raise KernelValidationError(
                f"failure_pattern_audit relabels pattern {match.pattern_id!r} "
                "(tier/action/family/enforced_by must equal the canonical bank)"
            )
        if match.tier.value == "hard" and match.enforced_by not in ENFORCING_VALIDATORS:
            raise KernelValidationError(
                f"hard pattern {match.pattern_id!r} must be enforced by an existing "
                "kernel validator"
            )


def _load_optional_literature_retrieval(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureRetrievalReport | None:
    optional = manifest.optional_artifacts
    if "literature_retrieval" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["literature_retrieval"],
        LiteratureRetrievalReport,
    )


def _load_optional_literature_search_plan(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureSearchPlan | None:
    optional = manifest.optional_artifacts
    if "literature_search_plan" not in optional:
        return None
    # The model has no status field, so loading it is the candidate-only guard:
    # a plan can never carry source_backed / proof status.
    return validate_yaml_model(
        run_dir / optional["literature_search_plan"],
        LiteratureSearchPlan,
    )


def _load_optional_autonomous_literature_retrieval(
    run_dir: Path,
    manifest: RunManifest,
) -> AutonomousLiteratureRetrievalReport | None:
    optional = manifest.optional_artifacts
    if "autonomous_literature_retrieval" not in optional:
        return None
    # AutonomousRetrievalCandidate.status is Literal["candidate"]; validating here
    # fails closed if any candidate were ever written with another status.
    return validate_yaml_model(
        run_dir / optional["autonomous_literature_retrieval"],
        AutonomousLiteratureRetrievalReport,
    )


def _load_optional_literature_candidates(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureCandidates | None:
    optional = manifest.optional_artifacts
    if "literature_candidates" not in optional:
        return None
    # LiteratureCandidate.status is Literal["candidate"]; validating here fails
    # closed if any candidate were ever written (or hand-edited) with another
    # status (e.g. source_backed). The scout artifact has no checked/proof/grade
    # field, so it can never upgrade trusted status.
    return validate_yaml_model(
        run_dir / optional["literature_candidates"],
        LiteratureCandidates,
    )


def _load_optional_retrieval_resolution(
    run_dir: Path,
    manifest: RunManifest,
) -> RetrievalResolutionReport | None:
    optional = manifest.optional_artifacts
    if "retrieval_resolution" not in optional:
        return None
    # RetrievalResolutionReport (StrictBaseModel, extra=forbid) has no
    # source_backed / checked / proof / grade field; validating here fails closed
    # if a retrieval report were ever written/hand-edited with a trusted status.
    return validate_yaml_model(
        run_dir / optional["retrieval_resolution"],
        RetrievalResolutionReport,
    )


def _load_optional_memory_context(
    run_dir: Path,
    manifest: RunManifest,
) -> MemoryContext | None:
    optional = manifest.optional_artifacts
    if "memory_context" not in optional:
        return None
    # MemoryContext / MemoryContextEntry (StrictBaseModel, extra=forbid) have no
    # source/evidence/proof status field; validating here fails closed if a memory
    # context were ever written/hand-edited with a trusted status.
    return validate_yaml_model(
        run_dir / optional["memory_context"],
        MemoryContext,
    )


def _validate_literature_retrieval(
    literature_retrieval: LiteratureRetrievalReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """Retrieval surfaces CANDIDATE sources only; it must not shortcut grounding.

    Only runs when the optional artifact is present. The hit status enum has no
    ``source_backed`` value (and StrictBaseModel forbids extras), so a retrieval
    hit can never be source_backed -- only Source Grounding earns that. Here we
    bind claim_id / run_id to this run (fail closed).
    """
    if literature_retrieval is None:
        return
    if literature_retrieval.claim_id != claim.id:
        raise KernelValidationError(
            "literature_retrieval claim_id does not match locked claim"
        )
    if literature_retrieval.run_id != manifest.run_id:
        raise KernelValidationError(
            "literature_retrieval run_id does not match the run manifest"
        )


def _load_optional_proof_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofVerificationReport | None:
    optional = manifest.optional_artifacts
    if "proof_verification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_verification"],
        ProofVerificationReport,
    )


def _load_optional_external_tool_candidates(
    run_dir: Path,
    manifest: RunManifest,
) -> ExternalToolCandidatesReport | None:
    optional = manifest.optional_artifacts
    if "external_tool_candidates" not in optional:
        return None
    # The model has no source/evidence/proof status field, so loading it is the
    # candidate-only guard: a discovered tool can never earn a trusted status.
    return validate_yaml_model(
        run_dir / optional["external_tool_candidates"],
        ExternalToolCandidatesReport,
    )


def _load_optional_external_tool_runs(
    run_dir: Path,
    manifest: RunManifest,
) -> ExternalToolRunsReport | None:
    optional = manifest.optional_artifacts
    if "external_tool_runs" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["external_tool_runs"],
        ExternalToolRunsReport,
    )


def _validate_external_tool_artifacts(
    candidates: ExternalToolCandidatesReport | None,
    runs: ExternalToolRunsReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """External-tool discovery + run records are RAW evidence, never a status.

    Both report models carry no source/evidence/proof/grade field (StrictBaseModel
    forbids extras), so an external tool cannot write a trusted status by
    construction -- only the kernel-gated proof-verification bridge (a trusted
    deterministic backend with a confirming result) may earn a SCOPED status.
    Here we bind claim_id / run_id and confine every recorded path to the run
    directory (no absolute path, no ``..``); a tool run is always scoped
    (``not_universal_proof`` is pinned True at the model level). Fail closed.
    """

    def _confine(paths: list[str], where: str) -> None:
        for raw in paths:
            p = Path(str(raw))
            if p.is_absolute() or str(raw).startswith("/") or ".." in p.parts:
                raise KernelValidationError(
                    f"external_tool_runs {where} path must be relative to the run "
                    f"directory (no absolute path, no '..'): {raw!r}"
                )

    if candidates is not None:
        if candidates.claim_id != claim.id:
            raise KernelValidationError(
                "external_tool_candidates claim_id does not match locked claim"
            )
        if candidates.run_id != manifest.run_id:
            raise KernelValidationError(
                "external_tool_candidates run_id does not match the run manifest"
            )
    if runs is not None:
        if runs.claim_id != claim.id:
            raise KernelValidationError(
                "external_tool_runs claim_id does not match locked claim"
            )
        if runs.run_id != manifest.run_id:
            raise KernelValidationError(
                "external_tool_runs run_id does not match the run manifest"
            )
        for run in runs.runs:
            _confine([run.working_dir], "working_dir")
            _confine(run.input_files, "input_files")
            _confine(run.output_files, "output_files")


# verifier_type -> the only checked status it may earn (with a confirming result).
_TRUSTED_EARNED = {
    "finite_group_cayley": "instance_checked",
    "finite_identity_table": "instance_checked",
    "finite_ring_identity": "instance_checked",
    "matrix_rank": "instance_checked",
    "exact_computation": "computation_checked",
}


def _validate_proof_verification(
    proof_verification: ProofVerificationReport | None,
    claim: Claim,
    manifest: RunManifest,
    idea_grade: IdeaGrade,
) -> None:
    """Proof verification bridge: only a trusted deterministic backend with a
    confirming result may earn a checked status. Fails closed.

    Rules (the report model already excludes a ``proved`` status by construction):

    - claim_id / run_id bind to this run, and every entry's claim_id must match
      the locked claim (a verifier result for a different claim cannot be
      attached here);
    - a checked status (``instance_checked`` / ``computation_checked``) requires
      ``trusted`` AND ``confirmed`` AND the matching trusted ``verifier_type``;
    - ``lean_checked`` is NOT earnable in V0 (reserved) -> always rejected;
    - ``human_attested`` requires ``verifier_type == human_checked`` and is never
      a machine-checked status;
    - a non-trusted or non-confirming entry, and any ``lean_bridge`` entry, must be
      ``unverified``.

    Natural-language prover/reviewer approval never reaches this report; it cannot
    set any checked status.
    """
    if proof_verification is None:
        return
    if proof_verification.claim_id != claim.id:
        raise KernelValidationError("proof_verification claim_id does not match locked claim")
    if proof_verification.run_id != manifest.run_id:
        raise KernelValidationError("proof_verification run_id does not match the run manifest")
    for entry in proof_verification.entries:
        vt = entry.verifier_type.value
        earned = entry.earned_status.value
        if entry.claim_id != claim.id:
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r} targets a different claim"
            )
        if earned == "lean_checked":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_checked is not earnable in V0"
            )
        if earned in {"instance_checked", "computation_checked"}:
            if not (entry.trusted and entry.confirmed):
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} without a "
                    "trusted + confirming verifier result"
                )
            if _TRUSTED_EARNED.get(vt) != earned:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: verifier_type {vt!r} cannot "
                    f"earn {earned!r}"
                )
        elif earned == "human_attested":
            if vt != "human_checked":
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: human_attested requires "
                    "verifier_type=human_checked"
                )
        # earned == unverified is always acceptable (fail-closed default)
        if vt == "lean_bridge" and earned != "unverified":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_bridge cannot earn {earned!r} in V0"
            )


def _validate_grade_proof_language_backed(
    idea_grade: IdeaGrade,
    computation_verification: ComputationVerificationReport | None,
    proof_verification: ProofVerificationReport | None,
) -> None:
    """A "verified-proof" grade status must be backed by a real trusted result.

    ``idea_grade.proof_language_status`` renders as "A proof has been checked by a
    trusted verifier", so the VERIFIED statuses cannot be asserted from prose or a
    forged grade:

    - ``computation_checked`` requires an actual confirming MACHINE result -- a
      trusted+confirming proof-verification entry (computation/instance checked)
      or a confirmed computation-verification result. A scoped/finite computation
      that genuinely backs the grade therefore stays scoped (it earned its entry);
      a grade that merely *claims* computation_checked with no machine result is
      rejected. This is the "no global proof status from scoped computation" gate.
    - ``lean_checked`` / ``human_confirmed`` are NOT earnable in V0: Espresso has
      no Lean/global-proof lane and no human-proof-attestation artifact (and human
      feedback may never upgrade proof status). Fail closed.
    """
    status = idea_grade.proof_language_status
    if status == ProofLanguageStatus.computation_checked:
        machine_confirmed = bool(
            proof_verification
            and any(
                entry.trusted
                and entry.confirmed
                and entry.earned_status
                in {
                    ProofVerificationStatus.computation_checked,
                    ProofVerificationStatus.instance_checked,
                }
                for entry in proof_verification.entries
            )
        ) or bool(
            computation_verification
            and any(
                result.executed and result.verdict == VerificationVerdict.confirmed
                for result in computation_verification.results
            )
        )
        if not machine_confirmed:
            raise KernelValidationError(
                "idea_grade.proof_language_status=computation_checked requires a confirming "
                "trusted machine result (a proof-verification or computation-verification "
                "entry); a scoped/finite computation cannot upgrade the grade to a global "
                "checked proof without one"
            )
    elif status in {
        ProofLanguageStatus.lean_checked,
        ProofLanguageStatus.human_confirmed,
    }:
        raise KernelValidationError(
            f"idea_grade.proof_language_status={status.value} is not earnable in V0: "
            "Espresso has no Lean/global-proof lane and no human-proof-attestation "
            "artifact (human feedback cannot upgrade proof status)"
        )


def _load_optional_theorem_extraction(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremExtractionReport | None:
    optional = manifest.optional_artifacts
    if "theorem_extraction" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["theorem_extraction"],
        TheoremExtractionReport,
    )


def _load_optional_literature_review_agent(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureReviewAgentReport | None:
    optional = manifest.optional_artifacts
    if "literature_review_agent" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["literature_review_agent"],
        LiteratureReviewAgentReport,
    )


def _load_optional_theorem_applicability(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremApplicabilityReport | None:
    optional = manifest.optional_artifacts
    if "theorem_applicability" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["theorem_applicability"],
        TheoremApplicabilityReport,
    )


def _load_optional_llm_agent_run(
    run_dir: Path,
    manifest: RunManifest,
) -> LLMAgentRunReport | None:
    optional = manifest.optional_artifacts
    if "llm_agent_run" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["llm_agent_run"],
        LLMAgentRunReport,
    )


def _load_optional_prover_loop(
    run_dir: Path,
    manifest: RunManifest,
) -> ProverLoopReport | None:
    optional = manifest.optional_artifacts
    if "prover_loop" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["prover_loop"],
        ProverLoopReport,
    )


def _load_optional_prover_round_index(
    run_dir: Path,
    manifest: RunManifest,
) -> ProverRoundIndex | None:
    optional = manifest.optional_artifacts
    if "prover_round_index" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["prover_round_index"],
        ProverRoundIndex,
    )


def _load_optional_proof_goal_graph(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofGoalGraph | None:
    optional = manifest.optional_artifacts
    if "proof_goal_graph" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_goal_graph"],
        ProofGoalGraph,
    )


def _validate_subgoal_graph(
    proof_goal_graph: ProofGoalGraph | None,
    claim: Claim,
    manifest: RunManifest,
    source_grounding: SourceGroundingReport,
    proof_verification: ProofVerificationReport | None,
    computation_verification: ComputationVerificationReport | None,
    idea_grade: IdeaGrade,
    strategy_brief: StrategyBrief,
) -> None:
    """Subgoal-graph proof-search state (advisory). Fails closed.

    The graph is a SEARCH-MANAGEMENT artifact, never proof evidence. This
    validator enforces that it cannot smuggle trusted status:

    - claim_id / run_id bind to this run;
    - a ``source_supported`` node (or any ``supported_by_source_steps`` ref) must
      point at a source-grounding step whose ``source_backed`` status was earned
      by the existing grounding gate -- prose / reviewer / human / memory can
      never earn it here;
    - a ``scoped_checked`` node (or any ``supported_by_computations`` /
      ``supported_by_scoped_checks`` ref) must point at a deterministic, trusted,
      confirming proof-verification or computation-verification entry;
    - a ``contradicted`` node is allowed only when the kernel's own refutation
      logic already refuted the claim (``idea_grade.status == refuted``), so a
      reviewer objection / dropped-hypothesis counterexample cannot close a
      subgoal as refuted here;
    - the model already forbids proof-complete language and a missing main goal.

    The graph has no status field that feeds back into the run, so its presence
    can never change idea_grade / proof_language / source / evidence / claim
    status (the StrictBaseModel forbids such fields by construction).
    """
    if proof_goal_graph is None:
        return
    if proof_goal_graph.claim_id != claim.id:
        raise KernelValidationError("proof_goal_graph claim_id does not match locked claim")
    if proof_goal_graph.run_id != manifest.run_id:
        raise KernelValidationError("proof_goal_graph run_id does not match the run manifest")

    known_strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    known_strategy_ids.add("none")
    selected = proof_goal_graph.selected_strategy
    if selected is not None and selected not in known_strategy_ids:
        raise KernelValidationError(
            f"proof_goal_graph selected_strategy {selected!r} is not a known strategy"
        )

    earned_source_steps = {
        step.proof_step_id
        for step in source_grounding.steps
        if step.status == SourceStatus.source_backed
    }
    earned_computations: set[str] = set()
    if proof_verification is not None:
        for entry in proof_verification.entries:
            if (
                entry.trusted
                and entry.confirmed
                and entry.earned_status
                in {
                    ProofVerificationStatus.instance_checked,
                    ProofVerificationStatus.computation_checked,
                }
            ):
                earned_computations.add(entry.id)
    confirmed_scoped_checks: set[str] = set()
    if computation_verification is not None:
        for result in computation_verification.results:
            if result.executed and result.verdict.value == "confirmed":
                confirmed_scoped_checks.add(result.check_id)

    claim_refuted = idea_grade.status == IdeaStatus.refuted

    for node in proof_goal_graph.nodes:
        for step_id in node.supported_by_source_steps:
            if step_id not in earned_source_steps:
                raise KernelValidationError(
                    f"{node.id}: supported_by_source_steps references {step_id!r} which is not an "
                    "earned source_backed grounding step"
                )
        for comp_id in node.supported_by_computations:
            if comp_id not in earned_computations:
                raise KernelValidationError(
                    f"{node.id}: supported_by_computations references {comp_id!r} which is not a "
                    "trusted confirming proof-verification entry"
                )
        for check_id in node.supported_by_scoped_checks:
            if check_id not in confirmed_scoped_checks:
                raise KernelValidationError(
                    f"{node.id}: supported_by_scoped_checks references {check_id!r} which is not a "
                    "confirmed deterministic computation-verification result"
                )
        if node.state == SubgoalState.source_supported and not node.supported_by_source_steps:
            raise KernelValidationError(
                f"{node.id}: source_supported requires an earned source-grounding step reference"
            )
        if node.state == SubgoalState.scoped_checked and not (
            node.supported_by_computations or node.supported_by_scoped_checks
        ):
            raise KernelValidationError(
                f"{node.id}: scoped_checked requires a deterministic verification reference"
            )
        if node.state == SubgoalState.contradicted and not claim_refuted:
            raise KernelValidationError(
                f"{node.id}: contradicted requires the claim to be refuted by the kernel "
                "(idea_grade.status=refuted); a reviewer objection or dropped-hypothesis "
                "counterexample cannot close a subgoal as refuted"
            )


def _validate_manifest_paths(run_dir: Path, manifest: RunManifest) -> None:
    try:
        validate_manifest_paths(run_dir, manifest)
    except ManifestPathValidationError as exc:
        raise KernelValidationError(str(exc)) from exc


def _validate_claim_id_consistency(
    claim: Claim,
    human_choice: HumanChoice,
    hypothesis_ledger: HypothesisLedger,
    literature_review: LiteratureReviewReport,
    examples: ExampleSelection,
    computations: ComputationChecks,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    review: Review,
    idea_grade: IdeaGrade,
    loop_control: LoopControl,
    strategy_synthesis: StrategySynthesis,
    strategy_brief: StrategyBrief,
) -> None:
    expected = claim.id
    artifact_ids = {
        "hypothesis_ledger": hypothesis_ledger.claim_id,
        "literature_review": literature_review.claim_id,
        "examples": examples.claim_id,
        "computation_checks": computations.claim_id,
        "counterexamples": counterexamples.claim_id,
        "tests": tests.claim_id,
        "source_grounding": source_grounding.claim_id,
        "review": review.claim_id,
        "idea_grade": idea_grade.claim_id,
        "loop_control": loop_control.claim_id,
        "strategy_synthesis": strategy_synthesis.claim_id,
        "strategy_brief": strategy_brief.claim_id,
        "human_choice": human_choice.claim_id,
    }
    mismatches = {name: claim_id for name, claim_id in artifact_ids.items() if claim_id != expected}
    if mismatches:
        raise KernelValidationError(f"artifact claim_id mismatch: {mismatches}")


def _validate_agent_contracts(agent_contracts: AgentContracts, artifacts: dict[str, str]) -> None:
    output_artifacts = {contract.output_artifact for contract in agent_contracts.contracts}
    required_outputs = {
        "locked_claim.yaml",
        "hypothesis_ledger.yaml",
        "literature_review.yaml",
        "computation_checks.yaml",
        "tests.yaml",
        "source_grounding.yaml",
        "review.yaml",
        "idea_grade.yaml",
        "loop_control.yaml",
        "strategy_brief.yaml",
        "human_choice.yaml",
        "final_report.md",
    }
    # The three bankable artifacts may be a single YAML file OR a per-item bank
    # directory; require whatever path the manifest actually uses (so agent
    # contracts and the manifest stay consistent in either layout).
    for key, default in (
        ("examples", "examples.yaml"),
        ("counterexamples", "counterexamples.yaml"),
        ("strategy_synthesis", "strategy_synthesis.yaml"),
    ):
        required_outputs.add(artifacts.get(key, default))
    missing_outputs = required_outputs.difference(output_artifacts)
    if missing_outputs:
        raise KernelValidationError(f"agent_contracts missing outputs {sorted(missing_outputs)}")
    artifact_values = set(artifacts.values())
    unknown_outputs = {
        output
        for output in output_artifacts.difference(artifact_values)
        if not output.startswith("memory/")
    }
    if unknown_outputs:
        raise KernelValidationError(f"agent_contracts reference non-run artifacts {sorted(unknown_outputs)}")


def _validate_literature_review_separation(literature_review: LiteratureReviewReport) -> None:
    for item in literature_review.candidate_references:
        if item.status == SourceStatus.source_backed:
            raise KernelValidationError(
                f"{item.id}: Literature Review cannot mark a reference source_backed"
            )
        if item.status == SourceStatus.misapplied:
            raise KernelValidationError(
                f"{item.id}: Literature Review cannot decide misapplied; Referencing must do that"
            )


def _validate_source_discipline_bindings(
    run_dir: Path,
    manifest: RunManifest,
    source_grounding: SourceGroundingReport,
    theorem_extraction: TheoremExtractionReport | None,
    theorem_applicability: TheoremApplicabilityReport | None,
) -> None:
    """Goal 41 provenance bindings (optional/additive — both fields default "").

    1. A source ref carrying ``statement_sha256`` must quote EXACTLY the bytes it
       hashed (self-hash), and when ``theorem_extraction.yaml`` is present the
       hash must name a really-extracted theorem — editing the cited statement
       after grounding fails closed.
    2. A theorem_applicability report carrying ``source_grounding_sha256`` must
       match the CURRENT grounding file — editing the grounding after the audit
       (without re-running it) fails closed instead of leaving a stale
       "applicable" verdict standing.
    """

    import hashlib

    extracted_hashes: set[str] | None = (
        {
            hashlib.sha256(theorem.statement.encode("utf-8")).hexdigest()
            for theorem in theorem_extraction.theorems
        }
        if theorem_extraction is not None
        else None
    )
    for ref in source_grounding.source_refs:
        if not ref.statement_sha256:
            continue
        actual = hashlib.sha256(ref.statement_or_summary.encode("utf-8")).hexdigest()
        if actual != ref.statement_sha256:
            raise KernelValidationError(
                f"{ref.id}: cited statement does not match its recorded excerpt "
                "hash (the statement was edited after grounding; re-run grounding)"
            )
        if extracted_hashes is not None and ref.statement_sha256 not in extracted_hashes:
            raise KernelValidationError(
                f"{ref.id}: excerpt hash does not name any extracted theorem in "
                "theorem_extraction.yaml (fabricated or stale citation; re-run "
                "theorem extraction and grounding)"
            )

    if theorem_applicability is not None and theorem_applicability.source_grounding_sha256:
        grounding_rel = manifest.artifacts.get("source_grounding")
        if grounding_rel:
            current = hashlib.sha256((run_dir / grounding_rel).read_bytes()).hexdigest()
            if current != theorem_applicability.source_grounding_sha256:
                raise KernelValidationError(
                    "theorem_applicability.yaml is stale: source_grounding.yaml "
                    "changed after the applicability audit; re-run the audit"
                )


def _validate_source_backing(source_grounding: SourceGroundingReport) -> None:
    refs = {ref.id: ref for ref in source_grounding.source_refs}
    for ref in refs.values():
        if ref.status == SourceStatus.misapplied:
            if ref.hypothesis_match.matches is not False:
                raise KernelValidationError(
                    f"{ref.id}: misapplied source requires a concrete hypothesis mismatch"
                )
            if not ref.hypothesis_match.mismatches:
                raise KernelValidationError(
                    f"{ref.id}: misapplied source must list mismatched hypotheses"
                )
    for step in source_grounding.steps:
        if step.status == SourceStatus.misapplied:
            if step.hypothesis_match.matches is not False:
                raise KernelValidationError(
                    f"{step.proof_step_id}: misapplied step requires a concrete hypothesis mismatch"
                )
            if not step.hypothesis_match.mismatches:
                raise KernelValidationError(
                    f"{step.proof_step_id}: misapplied step must list mismatched hypotheses"
                )
        if step.status == SourceStatus.source_backed:
            if step.hypothesis_match.matches is not True:
                raise KernelValidationError(
                    f"{step.proof_step_id}: source_backed step has non-matching hypotheses"
                )
            if not step.source_refs:
                raise KernelValidationError(f"{step.proof_step_id}: source_backed step has no source refs")
            for ref_id in step.source_refs:
                ref = refs.get(ref_id)
                if ref is None:
                    raise KernelValidationError(f"{step.proof_step_id}: unknown source ref {ref_id}")
                if ref.status != SourceStatus.source_backed:
                    raise KernelValidationError(
                        f"{step.proof_step_id}: source ref {ref_id} is not source_backed"
                    )


def _validate_theorem_applicability(
    manifest: RunManifest,
    claim: Claim,
    source_grounding: SourceGroundingReport,
    theorem_applicability: TheoremApplicabilityReport | None,
) -> None:
    has_source_backed_step = any(
        step.status == SourceStatus.source_backed for step in source_grounding.steps
    )
    if theorem_applicability is None:
        if has_source_backed_step:
            raise KernelValidationError(
                "source_backed theorem steps require theorem_applicability audit"
            )
        return
    if theorem_applicability.claim_id != claim.id:
        raise KernelValidationError("theorem_applicability claim_id does not match locked claim")
    if theorem_applicability.run_id != manifest.run_id:
        raise KernelValidationError("theorem_applicability run_id does not match manifest")
    if theorem_applicability.source_grounding_artifact != manifest.artifacts["source_grounding"]:
        raise KernelValidationError("theorem_applicability must cite the source_grounding artifact")
    if theorem_applicability.literature_review_agent_artifact is not None:
        expected = manifest.optional_artifacts.get("literature_review_agent")
        if theorem_applicability.literature_review_agent_artifact != expected:
            raise KernelValidationError(
                "theorem_applicability literature_review_agent_artifact does not match manifest"
            )
    known_steps = {step.proof_step_id: step for step in source_grounding.steps}
    refs_by_id = {ref.id: ref for ref in source_grounding.source_refs}
    known_refs = set(refs_by_id)
    usages_by_step = {usage.proof_step_id: usage for usage in theorem_applicability.usages}
    for usage in theorem_applicability.usages:
        if usage.proof_step_id not in known_steps:
            raise KernelValidationError(
                f"theorem_applicability references unknown proof step {usage.proof_step_id!r}"
            )
        if usage.source_ref_id not in known_refs and usage.source_ref_id != "missing_source_ref":
            raise KernelValidationError(
                f"theorem_applicability references unknown source ref {usage.source_ref_id!r}"
            )
        step = known_steps[usage.proof_step_id]
        if usage.source_ref_id != "missing_source_ref" and usage.source_ref_id not in step.source_refs:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_ref_id is not used by proof step"
            )
        if usage.source_status != step.status:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_status does not match source_grounding step"
            )
        if usage.source_ref_id in refs_by_id and refs_by_id[usage.source_ref_id].status != usage.source_status:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_status does not match source ref"
            )
    for step in source_grounding.steps:
        if step.status != SourceStatus.source_backed:
            continue
        usage = usages_by_step.get(step.proof_step_id)
        if usage is None:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step lacks applicability audit"
            )
        if usage.applicability_status != "applicable" or usage.blocks_source_backed:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step is blocked by theorem applicability"
            )
        if any(condition.status != "satisfied" for condition in usage.conditions):
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step has unsatisfied theorem conditions"
            )
        if not usage.locator.is_exact:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step requires an exact applicability locator"
            )
        if (
            usage.theorem_scope in {"very_general", "generic"}
            and usage.claim_scope == "universal"
            and not usage.scope_justification.strip()
        ):
            raise KernelValidationError(
                f"{step.proof_step_id}: a '{usage.theorem_scope}' theorem cannot back a universal "
                f"('all') claim without an explicit scope_justification"
            )


def _validate_review_outcome(review: Review, idea_grade: IdeaGrade) -> None:
    failed_reviewers = [
        reviewer.reviewer_id
        for reviewer in review.reviewers
        if reviewer.verdict == ReviewerVerdict.failed
    ]
    if failed_reviewers:
        if review.attempt_status != AttemptStatus.failed:
            raise KernelValidationError(
                f"failed reviewers require attempt_status=failed: {failed_reviewers}"
            )
        if idea_grade.status == IdeaStatus.promising:
            raise KernelValidationError(
                f"failed reviewers cannot produce promising grade: {failed_reviewers}"
            )
        strong_statuses = {
            ProofLanguageStatus.source_checked_argument,
            ProofLanguageStatus.computation_checked,
            ProofLanguageStatus.lean_checked,
            ProofLanguageStatus.human_confirmed,
        }
        if idea_grade.proof_language_status in strong_statuses:
            raise KernelValidationError(
                f"failed reviewers cannot support proof_language_status={idea_grade.proof_language_status.value}"
            )


def _validate_loop_control(loop_control: LoopControl) -> None:
    no_progress = (
        loop_control.no_new_counterexamples_cycles >= 2
        and loop_control.no_new_references_cycles >= 2
        and loop_control.grade_delta_last_two <= 0
    )
    if no_progress and not loop_control.should_stop:
        raise KernelValidationError("two no-progress cycles require Strategy Brief or human stop")
    if loop_control.should_stop and loop_control.next_action == "continue_loop":
        raise KernelValidationError("loop_control cannot stop and continue at the same time")
    if loop_control.next_action == "stop" and not loop_control.should_stop:
        raise KernelValidationError("loop_control next_action=stop requires should_stop=true")
    if loop_control.next_action == "stop" and not loop_control.stop_reasons:
        raise KernelValidationError("loop_control next_action=stop requires stop_reasons")


def _validate_strategy_routes(
    routes: list,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review | None = None,
    computations: ComputationChecks | None = None,
    tests: TestReport | None = None,
) -> None:
    known_tests = {test.id for test in counterexamples.tests}
    for strategy in routes:
        unknown_tests = set(strategy.survived_tests + strategy.failed_tests).difference(known_tests)
        if unknown_tests:
            raise KernelValidationError(
                f"{strategy.id}: strategy references unknown tests {sorted(unknown_tests)}"
            )
    validate_strategy_rules(
        routes,
        idea_grade,
        review=review,
        computations=computations,
        counterexamples=counterexamples,
        tests=tests,
        error_type=KernelValidationError,
    )


def _validate_strategy_synthesis(
    strategy_synthesis: StrategySynthesis,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review,
    computations: ComputationChecks,
    tests: TestReport,
) -> None:
    if not strategy_synthesis.routes:
        raise KernelValidationError("strategy_synthesis requires at least one route")
    required_inputs = {
        "literature_review.yaml",
        "tests.yaml",
        "counterexamples.yaml",
        "source_grounding.yaml",
        "review.yaml",
        "idea_grade.yaml",
        "loop_control.yaml",
    }
    missing_inputs = required_inputs.difference(strategy_synthesis.synthesized_from)
    if missing_inputs:
        raise KernelValidationError(
            f"strategy_synthesis missing required inputs {sorted(missing_inputs)}"
        )
    _validate_strategy_routes(
        strategy_synthesis.routes,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )


def _validate_strategy_brief(
    strategy_brief: StrategyBrief,
    strategy_synthesis: StrategySynthesis,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review,
    computations: ComputationChecks,
    tests: TestReport,
) -> None:
    if not strategy_brief.top_strategies:
        raise KernelValidationError("strategy_brief requires at least one strategy")
    if len(strategy_brief.top_strategies) > 3:
        raise KernelValidationError("strategy_brief may expose at most three top strategies")
    synthesis_ids = {route.id for route in strategy_synthesis.routes}
    synthesis_by_id = {route.id: route for route in strategy_synthesis.routes}
    brief_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    unknown = brief_ids.difference(synthesis_ids)
    if unknown:
        raise KernelValidationError(
            f"strategy_brief contains routes not produced by Strategy Synthesizer: {sorted(unknown)}"
        )
    for strategy in strategy_brief.top_strategies:
        synthesis_route = synthesis_by_id[strategy.id]
        if _strategy_brief_identity(strategy) != _strategy_brief_identity(synthesis_route):
            raise KernelValidationError(
                f"{strategy.id}: strategy_brief route differs from Strategy Synthesis"
            )
    _validate_strategy_routes(
        strategy_brief.top_strategies,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )


def _strategy_brief_identity(strategy) -> dict:
    data = strategy.model_dump(mode="json")
    data.pop("score", None)
    return data


def _validate_human_choice(human_choice: HumanChoice, strategy_brief: StrategyBrief) -> None:
    if human_choice.status == HumanChoiceStatus.selected:
        strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
        if human_choice.selected_strategy not in strategy_ids and human_choice.selected_strategy != "none":
            raise KernelValidationError(
                f"human_choice selected unknown strategy {human_choice.selected_strategy!r}"
            )
        choices = {
            choice.strategy_id: {effort.value for effort in choice.effort_options}
            for choice in strategy_brief.allowed_human_choices
        }
        selected_strategy = human_choice.selected_strategy
        if selected_strategy not in choices:
            raise KernelValidationError(
                f"human_choice selected strategy {selected_strategy!r} has no allowed choice option"
            )
        selected_effort = human_choice.effort.value if human_choice.effort is not None else None
        if selected_effort not in choices[selected_strategy]:
            raise KernelValidationError(
                f"human_choice effort {selected_effort!r} is not allowed for {selected_strategy!r}"
            )
        if (
            selected_strategy == "none"
            and human_choice.continue_decision == ContinueDecision.continue_run
        ):
            raise KernelValidationError("human_choice cannot continue with selected_strategy='none'")


def _validate_human_feedback(
    manifest: RunManifest,
    claim: Claim,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    strategy_brief: StrategyBrief,
    feedback: HumanExampleFeedback | None,
    summary: ExampleFeedbackSummary | None,
) -> None:
    if feedback is None:
        if summary is not None:
            raise KernelValidationError("example_feedback_summary requires human_example_feedback")
        return

    if feedback.project_id != manifest.project_id:
        raise KernelValidationError("human_example_feedback project_id does not match manifest")
    if feedback.run_id != manifest.run_id:
        raise KernelValidationError("human_example_feedback run_id does not match manifest")
    if feedback.claim_id != claim.id:
        raise KernelValidationError("human_example_feedback claim_id does not match locked claim")

    target_sets = _feedback_target_sets(
        examples,
        counterexamples,
        tests,
        source_grounding,
        strategy_brief,
    )
    feedback_by_id = {}
    for item in feedback.feedback:
        feedback_by_id[item.feedback_id] = item
        if not item.human_confirmed:
            raise KernelValidationError(f"{item.feedback_id}: unconfirmed feedback cannot enter a run")
        expected_artifact, allowed_ids = target_sets[item.target_type.value]
        if item.target_artifact != expected_artifact:
            raise KernelValidationError(
                f"{item.feedback_id}: {item.target_type.value} feedback must target {expected_artifact}"
            )
        if item.target_id not in allowed_ids:
            raise KernelValidationError(
                f"{item.feedback_id}: unknown {item.target_type.value} target {item.target_id!r}"
            )

    if summary is None:
        return

    if summary.claim_id != claim.id:
        raise KernelValidationError("example_feedback_summary claim_id does not match locked claim")
    if summary.run_id != manifest.run_id:
        raise KernelValidationError("example_feedback_summary run_id does not match manifest")
    if summary.feedback_artifact_path != manifest.optional_artifacts.get("human_example_feedback"):
        raise KernelValidationError("example_feedback_summary must cite the human feedback artifact path")

    feedback_ids = set(feedback_by_id)
    unknown_summary_ids = set(summary.summarized_from).difference(feedback_ids)
    if unknown_summary_ids:
        raise KernelValidationError(
            f"example_feedback_summary references unknown feedback ids {sorted(unknown_summary_ids)}"
        )

    for adjustment in summary.ranking_adjustments:
        unknown_feedback_ids = set(adjustment.feedback_ids).difference(feedback_ids)
        if unknown_feedback_ids:
            raise KernelValidationError(
                f"ranking adjustment references unknown feedback ids {sorted(unknown_feedback_ids)}"
            )
        expected_artifact, allowed_ids = target_sets[adjustment.target_type.value]
        if adjustment.target_artifact != expected_artifact:
            raise KernelValidationError(
                f"ranking adjustment for {adjustment.target_type.value} must target {expected_artifact}"
            )
        if adjustment.target_id not in allowed_ids:
            raise KernelValidationError(
                f"ranking adjustment references unknown {adjustment.target_type.value} target "
                f"{adjustment.target_id!r}"
            )


def _validate_llm_loop_artifacts(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
    llm_agent_run: LLMAgentRunReport | None,
    prover_loop: ProverLoopReport | None,
    prover_round_index: ProverRoundIndex | None,
    strategy_brief: StrategyBrief,
) -> None:
    if llm_agent_run is None:
        if prover_loop is not None:
            raise KernelValidationError("prover_loop requires llm_agent_run")
        if prover_round_index is not None:
            raise KernelValidationError("prover_round_index requires llm_agent_run")
        return

    if llm_agent_run.claim_id != claim.id:
        raise KernelValidationError("llm_agent_run claim_id does not match locked claim")
    if llm_agent_run.run_id != manifest.run_id:
        raise KernelValidationError("llm_agent_run run_id does not match manifest")
    known_artifacts = set(manifest.artifacts.values()) | set(manifest.optional_artifacts.values())
    unknown_inputs = set(llm_agent_run.input_artifacts).difference(known_artifacts)
    if unknown_inputs:
        raise KernelValidationError(
            f"llm_agent_run references unknown input artifacts {sorted(unknown_inputs)}"
        )

    if prover_loop is None:
        if prover_round_index is not None:
            raise KernelValidationError("prover_round_index requires prover_loop")
        return

    if prover_loop.claim_id != claim.id:
        raise KernelValidationError("prover_loop claim_id does not match locked claim")
    if prover_loop.run_id != manifest.run_id:
        raise KernelValidationError("prover_loop run_id does not match manifest")
    if prover_loop.source_llm_agent_run != manifest.optional_artifacts.get("llm_agent_run"):
        raise KernelValidationError("prover_loop must cite the llm_agent_run optional artifact")
    if prover_round_index is None:
        raise KernelValidationError("prover_loop requires prover_round_index")
    if prover_round_index.claim_id != claim.id:
        raise KernelValidationError("prover_round_index claim_id does not match locked claim")
    if prover_round_index.run_id != manifest.run_id:
        raise KernelValidationError("prover_round_index run_id does not match manifest")
    if prover_round_index.source_prover_loop != manifest.optional_artifacts.get("prover_loop"):
        raise KernelValidationError("prover_round_index must cite the prover_loop optional artifact")
    if prover_loop.human_gate_status != llm_agent_run.human_gate.status:
        raise KernelValidationError("prover_loop human_gate_status does not match llm_agent_run")
    if (
        llm_agent_run.human_gate.status == "pending"
        and prover_loop.loop_decision not in {"ask_human", "stop"}
    ):
        raise KernelValidationError("pending LLM human gate cannot continue prover loop")
    if llm_agent_run.human_gate.status == "pending":
        for attempt in prover_loop.attempts:
            if attempt.time_allocation not in {"ask_human", "stop"} or attempt.status not in {
                "waiting_human",
                "stopped",
            }:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: pending human gate cannot allocate prover work"
                )

    known_strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    known_strategy_ids.add("none")
    if prover_loop.selected_strategy_id is not None and prover_loop.selected_strategy_id not in known_strategy_ids:
        raise KernelValidationError(
            f"prover_loop selected unknown strategy {prover_loop.selected_strategy_id!r}"
        )
    reviews_by_id = {review.verifier_id: review for review in llm_agent_run.verifier_reviews}
    known_reviewer_ids = set(reviews_by_id)
    if len(known_reviewer_ids) < 3:
        raise KernelValidationError("proof-close-style prover loop requires at least three reviewer roles")
    known_attempt_ids = {attempt.attempt_id for attempt in prover_loop.attempts}
    for attempt in prover_loop.attempts:
        if attempt.parent_attempt_id is not None:
            if attempt.parent_attempt_id == attempt.attempt_id:
                raise KernelValidationError(f"{attempt.attempt_id}: parent attempt cannot be self")
            if attempt.parent_attempt_id not in known_attempt_ids:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: parent attempt id does not exist"
                )
        if attempt.strategy_id not in known_strategy_ids:
            raise KernelValidationError(
                f"{attempt.attempt_id}: unknown strategy id {attempt.strategy_id!r}"
            )
        if attempt.round_index == 1:
            unknown_reviewers = set(attempt.reviewer_ids).difference(known_reviewer_ids)
            if unknown_reviewers:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: unknown reviewer ids {sorted(unknown_reviewers)}"
                )
        elif len(set(attempt.reviewer_ids)) < 3:
            raise KernelValidationError(
                f"{attempt.attempt_id}: appended loop round requires at least three reviewers"
            )
        if attempt.prover_attempt_id != llm_agent_run.prover_attempt.agent_id:
            if attempt.round_index == 1:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: prover attempt id does not match llm_agent_run"
                )
            if not attempt.prover_attempt_id.startswith("llm_prover_"):
                raise KernelValidationError(
                    f"{attempt.attempt_id}: appended prover attempt id is malformed"
                )
        for pointer in attempt.reviewer_objections:
            if pointer.verifier_id not in attempt.reviewer_ids:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: reviewer objection is not listed in reviewer_ids"
                )
            if attempt.round_index == 1:
                source_review = reviews_by_id.get(pointer.verifier_id)
                if source_review is None:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection references unknown reviewer "
                        f"{pointer.verifier_id!r}"
                    )
                if pointer.verdict != source_review.verdict:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection verdict does not match "
                        f"{pointer.verifier_id}"
                    )
                if pointer.recommended_human_action != source_review.recommended_human_action:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection action does not match "
                        f"{pointer.verifier_id}"
                    )
                if pointer.objection_count != len(source_review.objections):
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection count does not match "
                        f"{pointer.verifier_id}"
                    )
    _validate_prover_round_index(
        run_dir,
        manifest,
        llm_agent_run,
        prover_loop,
        prover_round_index,
    )


def _validate_prover_round_index(
    run_dir: Path,
    manifest: RunManifest,
    llm_agent_run: LLMAgentRunReport,
    prover_loop: ProverLoopReport,
    prover_round_index: ProverRoundIndex,
) -> None:
    attempts_by_id = {attempt.attempt_id: attempt for attempt in prover_loop.attempts}
    index_by_attempt = {entry.loop_attempt_id: entry for entry in prover_round_index.rounds}
    missing_attempts = set(attempts_by_id).difference(index_by_attempt)
    extra_attempts = set(index_by_attempt).difference(attempts_by_id)
    if missing_attempts:
        raise KernelValidationError(
            f"prover_round_index missing loop attempts {sorted(missing_attempts)}"
        )
    if extra_attempts:
        raise KernelValidationError(
            f"prover_round_index references unknown loop attempts {sorted(extra_attempts)}"
        )

    root = run_dir.resolve()
    for entry in prover_round_index.rounds:
        raw_path = Path(entry.artifact_path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact path must stay inside run dir"
            )
        path = (run_dir / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact path escapes run dir"
            ) from exc
        if not path.exists() or not path.is_file():
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact is missing"
            )
        report = validate_yaml_model(path, ProverRoundReport)
        attempt = attempts_by_id[entry.loop_attempt_id]
        if report.claim_id != llm_agent_run.claim_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round claim_id does not match"
            )
        if report.run_id != manifest.run_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round run_id does not match manifest"
            )
        if entry.round_index != attempt.round_index or report.round_index != attempt.round_index:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round_index does not match loop attempt"
            )
        if entry.strategy_id != attempt.strategy_id or report.strategy_id != attempt.strategy_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round strategy_id does not match loop attempt"
            )
        if report.loop_attempt_id != attempt.attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round loop_attempt_id does not match"
            )
        if report.parent_attempt_id != attempt.parent_attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round parent_attempt_id does not match"
            )
        if entry.status != attempt.status or report.status != attempt.status:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round status does not match loop attempt"
            )
        if entry.time_allocation != attempt.time_allocation or report.time_allocation != attempt.time_allocation:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round time allocation does not match loop attempt"
            )
        if report.prover_attempt.agent_id != attempt.prover_attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round prover attempt id does not match"
            )
        if {review.verifier_id for review in report.verifier_reviews} != set(attempt.reviewer_ids):
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round verifier ids do not match loop attempt"
            )
        known_artifacts = set(manifest.artifacts.values()) | set(manifest.optional_artifacts.values())
        for artifact in report.input_artifacts:
            artifact_path = Path(artifact)
            if artifact_path.is_absolute() or ".." in artifact_path.parts:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round input_artifacts must stay inside manifest"
                )
            if artifact not in known_artifacts:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round references unknown input artifact {artifact!r}"
                )
        _validate_prover_round_language(entry.loop_attempt_id, report)
        pointer_by_id = {pointer.verifier_id: pointer for pointer in attempt.reviewer_objections}
        for review in report.verifier_reviews:
            pointer = pointer_by_id.get(review.verifier_id)
            if pointer is None:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer is missing loop pointer"
                )
            if pointer.verdict != review.verdict:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer verdict does not match loop attempt"
                )
            if pointer.recommended_human_action != review.recommended_human_action:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer action does not match loop attempt"
                )
            if pointer.objection_count != len(review.objections):
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer objection count does not match loop attempt"
                )
        if list(report.reviewer_blockers) != list(attempt.blockers):
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round reviewer_blockers do not match loop attempt"
            )
        if attempt.round_index == 1:
            if report.source != "initial_llm_agent_run":
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: initial prover round source is wrong"
                )
            if report.prover_attempt.model_dump(mode="json") != llm_agent_run.prover_attempt.model_dump(mode="json"):
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: initial round prover does not match llm_agent_run"
                )
            source_reviews = {
                review.verifier_id: review.model_dump(mode="json")
                for review in llm_agent_run.verifier_reviews
            }
            for review in report.verifier_reviews:
                if review.model_dump(mode="json") != source_reviews.get(review.verifier_id):
                    raise KernelValidationError(
                        f"{entry.loop_attempt_id}: initial round verifier body does not match "
                        f"llm_agent_run for {review.verifier_id}"
                    )
        elif report.source != "appended_prover_loop_round":
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: appended prover round source is wrong"
            )


def _validate_prover_round_language(loop_attempt_id: str, report: ProverRoundReport) -> None:
    text_parts = [
        report.prover_attempt.output_summary,
        *report.prover_attempt.proposed_steps,
        *report.prover_attempt.source_requests,
        *report.prover_attempt.risks,
        report.next_action,
        report.safety_note,
    ]
    for review in report.verifier_reviews:
        text_parts.extend(
            [
                *review.objections,
                *review.missing_hypotheses,
                *review.source_requests,
            ]
        )
    # NOTE: review.unsafe_language is deliberately excluded. It is the audit
    # ledger of forbidden phrases the kernel DETECTED in raw agent output (via
    # collect_unsafe_language) and recorded verbatim on purpose; scanning it would
    # make the guard flag its own detection record. The actual content fields
    # above are already redacted (safe_text/safe_list), so proof-language in real
    # content is still caught.
    phrase = _first_forbidden_proof_language(" ".join(text_parts).lower())
    if phrase:
        raise KernelValidationError(
            f"{loop_attempt_id}: prover round artifact uses forbidden proof language {phrase!r}"
        )


def _feedback_target_sets(
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    strategy_brief: StrategyBrief,
) -> dict[str, tuple[str, set[str]]]:
    return {
        "example": ("examples.yaml", {example.id for example in examples.examples}),
        "counterexample_test": (
            "counterexamples.yaml",
            {test.id for test in counterexamples.tests},
        ),
        "test": ("tests.yaml", {test.id for test in tests.tests}),
        "source_ref": ("source_grounding.yaml", {ref.id for ref in source_grounding.source_refs}),
        "source_step": (
            "source_grounding.yaml",
            {step.proof_step_id for step in source_grounding.steps},
        ),
        "strategy": (
            "strategy_brief.yaml",
            {strategy.id for strategy in strategy_brief.top_strategies},
        ),
    }


def _validate_final_report_language(final_report: Path, idea_grade: IdeaGrade) -> None:
    text = read_text(final_report).lower()
    weak_statuses = {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
        ProofLanguageStatus.source_checked_argument,
        ProofLanguageStatus.computation_checked,
    }
    if idea_grade.proof_language_status in weak_statuses:
        phrase = _first_forbidden_proof_language(text)
        if phrase:
            raise KernelValidationError(
                f"final_report.md uses forbidden proof language: {phrase!r}"
            )


def _first_forbidden_proof_language(text: str) -> str | None:
    forbidden_patterns = {
        r"\bverified\b": "verified",
        r"\bproof\s+complete\b": "proof complete",
        r"\bwe\s+proved\b": "we proved",
        r"\bproved\s+the\s+theorem\b": "proved the theorem",
        r"\bproven\b": "proven",
        r"\bproves\s+the\s+claim\b": "proves the claim",
        r"\bproves\s+the\s+theorem\b": "proves the theorem",
        r"\bhas\s+been\s+proven\b": "has been proven",
        r"\bthis\s+proof\b": "this proof",
        r"\bcomplete\s+proof\b": "complete proof",
        r"\bfull\s+proof\b": "full proof",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
        # Goal 41: Chinese global proof-completion markers (no \b — Python's
        # word boundary treats CJK as word chars, so \b would never fire inside
        # Chinese prose). Same curation as the display lexicon.
        r"证毕": "证毕",
        r"证明完毕": "证明完毕",
        r"证明完成": "证明完成",
        r"定理得证": "定理得证",
        r"命题得证": "命题得证",
        r"猜想得证": "猜想得证",
        r"定理已证明": "定理已证明",
        r"命题已证明": "命题已证明",
        r"我们证明了该定理": "我们证明了该定理",
        r"我们证明了这个定理": "我们证明了这个定理",
    }
    for pattern, phrase in forbidden_patterns.items():
        if re.search(pattern, text):
            return phrase
    return None
