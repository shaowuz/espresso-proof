"""Validators — internal topological slice 4/6 (callees precede callers). Imported via espresso_proof.kernel.validators."""


from __future__ import annotations

import re
from pathlib import Path


from espresso_proof.kernel.proof_dependencies import (
    trusted_human_allowed_theorem_refs,
    trusted_source_grounded_theorem_refs,
    unresolved_proof_dependency_lines,
)
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AgentConsensusVerdict,
    AIReviewAssessment,
    Claim,
    ComputationVerificationReport,
    ProofGoalGraph,
    ProofVerificationReport,
    ProofVerificationStatus,
    SubgoalState,
    IdeaGrade,
    IdeaStatus,
    LineNumberedProof,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    ProofLanguageStatus,
    ProverLoopReport,
    ProverRoundIndex,
    Review,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    TheoremApplicabilityReport,
    TheoremApplicabilityReviewArtifact,
    TheoremConditionLedger,
    TheoremExtractionReport,
)
from espresso_proof.kernel.pressure_status import (
    artifact_revision_hash,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.manifest import ManifestPathValidationError
from espresso_proof.validation.manifest import validate_manifest_paths


from espresso_proof.kernel.validators._part1 import validate_yaml_model


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


def _load_optional_theorem_applicability_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> TheoremApplicabilityReviewArtifact | None:
    optional = manifest.optional_artifacts
    if "theorem_applicability_review" not in optional:
        return None
    review = validate_yaml_model(
        run_dir / optional["theorem_applicability_review"],
        TheoremApplicabilityReviewArtifact,
    )
    if review.claim_id != claim.id:
        raise KernelValidationError("theorem_applicability_review claim_id does not match locked claim")
    if review.run_id != manifest.run_id:
        raise KernelValidationError("theorem_applicability_review run_id does not match manifest")
    return review


def _load_optional_theorem_condition_ledger(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremConditionLedger | None:
    optional = manifest.optional_artifacts
    if "theorem_condition_ledger" not in optional:
        return None
    # The ledger model (StrictBaseModel) carries no source/evidence/proof/grade
    # status field; validating here fails closed if one were ever smuggled in.
    return validate_yaml_model(
        run_dir / optional["theorem_condition_ledger"],
        TheoremConditionLedger,
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


def _load_optional_ai_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> AIReviewAssessment | None:
    """Advisory "AI review passed" signal. Fails closed.

    ``AIReviewAssessment`` (StrictBaseModel) carries NO proof/source/evidence/
    grade/checked/trust/status field of any kind, so a malformed assessment — or
    one trying to smuggle such a field — is rejected here at load. The signal is
    advisory only: it never earns, sets, or upgrades any status. Light binding:
    claim_id / run_id must match the run.
    """

    optional = manifest.optional_artifacts
    if "ai_review" not in optional:
        return None
    assessment = validate_yaml_model(
        run_dir / optional["ai_review"],
        AIReviewAssessment,
    )
    if assessment.claim_id != claim.id:
        raise KernelValidationError(
            f"ai_review claim_id {assessment.claim_id!r} does not match the run "
            f"claim {claim.id!r}"
        )
    if assessment.run_id != manifest.run_id:
        raise KernelValidationError(
            f"ai_review run_id {assessment.run_id!r} does not match the run "
            f"{manifest.run_id!r}"
        )
    try:
        from espresso_proof.kernel.ai_review import assess_ai_review

        expected = assess_ai_review(run_dir)
    except Exception as exc:  # noqa: BLE001 - malformed dependencies must fail closed
        raise KernelValidationError(
            f"ai_review cannot be validated against current run artifacts: {exc}"
        ) from exc
    actual = assessment.model_dump(mode="json")
    actual["generated_at"] = ""
    if actual != expected:
        raise KernelValidationError(
            "ai_review does not match current reviewer/refutation/grade artifacts; "
            "rebuild ai_review.yaml before rendering the advisory signal"
        )
    return assessment


def _prover_assertion_clauses(text: str) -> list[str]:
    normalized = " ".join(str(text).lower().split())
    rough = re.split(
        r"(?:[.;!?]\s+|,\s+|\n+|\s+\b(?:but|however|while|although|whereas)\b\s+)",
        normalized,
    )
    return [part.strip() for part in rough if part.strip()]


def _prover_clause_is_status_boundary_disclaimer(clause: str) -> bool:
    return any(
        phrase in clause
        for phrase in (
            "does not upgrade",
            "do not upgrade",
            "cannot upgrade",
            "can never upgrade",
            "never upgrades",
            "never upgrade",
            "does not set",
            "cannot set",
            "cannot write",
            "must not upgrade",
            "not proof status",
            "not evidence",
            "not a proof",
            "is not proof",
            "not applicable",
            "not checked",
            "not refuted",
            "advisory only",
            "advisory-only",
            "scheduling-only",
        )
    )


def _prover_status_smuggling_field(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        for field in (
            "proof_status",
            "proof_language_status",
            "agent_consensus_mock_checked",
            "agent_consensus",
            "mock_checked",
            "source_status",
            "source_backed_status",
            "source_backed",
            "evidence_status",
            "checked_evidence",
            "theorem_applicability",
            "theorem_applicability_status",
            "applicability_status",
            "refutation_status",
            "counterexample_refutes_claim",
            "refutes_claim",
            "grade_status",
        ):
            if re.search(rf"\b{re.escape(field)}\s*[:=]", clause):
                return field
        owner_status_patterns = [
            (r"\bproof[_ -]?language[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "proof_language_status"),
            (r"\bproof\s+status\s+(?:is|are|was|were)\s+[\w -]+", "proof_status"),
            (r"\bsource[_ -]?backed[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "source_backed_status"),
            (r"\bsource\s+status\s+(?:is|are|was|were)\s+[\w -]+", "source_status"),
            (r"\bevidence\s+status\s+(?:is|are|was|were)\s+[\w -]+", "evidence_status"),
            (r"\bchecked[_ -]?evidence\s+(?:is|are|was|were)\s+[\w -]+", "checked_evidence"),
            (r"\btheorem[_ -]?applicability[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\btheorem\s+applicability\s+status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\brefutation\s+status\s+(?:is|are|was|were)\s+[\w -]+", "refutation_status"),
            (r"\bgrade[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
            (r"\bgrade\s+status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
        ]
        for pattern, name in owner_status_patterns:
            if re.search(pattern, clause):
                return name
        if _prover_clause_is_status_boundary_disclaimer(clause):
            continue
        status_patterns = [
            (r"\b(?:is|are|as|becomes|became|counts?\s+as|treats?\s+as)\s+source[_ -]?backed\b", "source_backed"),
            (r"\bsource\s+(?:status\s+)?(?:is|=|:)\s*(?:backed|checked|supported)\b", "source_status"),
            (r"\bchecked[_ -]?evidence\s+(?:supports?|establishes?|proves?|is|are)\b", "checked_evidence"),
            (r"\b(?:is|are|as|counts?\s+as|treats?\s+as)\s+checked[_ -]?evidence\b", "checked_evidence"),
            (r"\bevidence\s+(?:status\s+)?(?:is|=|:)\s*(?:checked|verified)\b", "evidence_status"),
            (r"\btheorem\s+applicability(?:\s+status)?\s+(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\bapplicability[_ ]status\s*(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\ball[_ ]applicable\b", "theorem_applicability"),
            (r"\bexamples?\s+(?:have\s+)?survived\b", "example_status"),
            (r"\bsurvived\s+examples?\b", "example_status"),
            (r"\bno\s+counterexamples?\s+(?:remain|exist|found|pressure)\b", "counterexample_pressure"),
            (r"\bmissing\s+counterexamples?\s+(?:imply|mean|show|prove)\s+no\s+pressure\b", "counterexample_pressure"),
            (r"\bcounterexamples?\s+refutes?\b", "counterexample_refutes_claim"),
            (r"\bcounterexample[_ -]?refutes[_ -]?claim\b", "counterexample_refutes_claim"),
            (r"\b(?:claim|route|statement)\s+is\s+refuted\b", "refutation_status"),
            (r"\brefuted\b", "refutation_status"),
        ]
        for pattern, name in status_patterns:
            if re.search(pattern, clause):
                return name
    return None


def _load_optional_agent_consensus_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> AgentConsensusReview | None:
    """Agent-consensus mock-check record. Fails closed.

    ``AgentConsensusReview`` (StrictBaseModel) carries NO proof/source/evidence/
    grade/checked status FIELD, so a smuggled field name is rejected at load. But
    StrictBaseModel does not police forbidden TOKENS inside the free-text fields, so
    we additionally scan every reviewer-authored text field (which the UI renders)
    for a status smuggle. The model validator already enforces the three distinct
    reviewers + ``consensus`` == unanimous no-fatal-flaw. Light binding: claim_id /
    run_id must match the run; the heavy provenance gate (hash binding, reviewer
    cross-check, refuted/failed incompatibility) is ``_validate_agent_consensus_review``.
    """

    optional = manifest.optional_artifacts
    if "agent_consensus_review" not in optional:
        return None
    consensus = validate_yaml_model(
        run_dir / optional["agent_consensus_review"],
        AgentConsensusReview,
    )
    if consensus.claim_id != claim.id:
        raise KernelValidationError(
            f"agent_consensus_review claim_id {consensus.claim_id!r} does not match "
            f"the run claim {claim.id!r}"
        )
    if consensus.run_id != manifest.run_id:
        raise KernelValidationError(
            f"agent_consensus_review run_id {consensus.run_id!r} does not match the "
            f"run {manifest.run_id!r}"
        )
    # Free-text fields cannot smuggle a status (no reviewer prose may set
    # proof/source/evidence/grade status). The detector exempts honest
    # "not source_backed" disclaimers, so the record's own disclaimer is safe.
    for entry in consensus.entries:
        for text in (entry.looked_for, entry.finding, *entry.residual_objections):
            smuggle = _prover_status_smuggling_field(str(text))
            if smuggle:
                raise KernelValidationError(
                    f"agent_consensus_review entry ({entry.reviewer_id}) text smuggles a "
                    f"status field {smuggle!r}; reviewer prose cannot set proof status"
                )
    for text in (consensus.disclaimer, *consensus.notes):
        smuggle = _prover_status_smuggling_field(str(text))
        if smuggle:
            raise KernelValidationError(
                f"agent_consensus_review text smuggles a status field {smuggle!r}"
            )
    return consensus


def _load_optional_line_numbered_proof(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> LineNumberedProof | None:
    """The structured line-numbered proof. Fails closed. Malformed / route-
    shaped YAML is rejected by ``LineNumberedProof`` (StrictBaseModel + the
    per-line validator). Light binding: claim_id must match the run."""
    optional = manifest.optional_artifacts
    if "line_numbered_proof" not in optional:
        return None
    proof = validate_yaml_model(
        run_dir / optional["line_numbered_proof"], LineNumberedProof
    )
    if proof.claim_id != claim.id:
        raise KernelValidationError(
            f"line_numbered_proof claim_id {proof.claim_id!r} does not match the run "
            f"claim {claim.id!r}"
        )
    return proof


def _validate_agent_consensus_review(
    consensus: AgentConsensusReview | None,
    review: Review,
    idea_grade: IdeaGrade,
    manifest: RunManifest,
    run_dir: Path,
    line_numbered_proof: LineNumberedProof | None = None,
) -> None:
    """Provenance gate for the agent-consensus mock-check (fail-closed).

    The mock check may ONLY seal a structured line-numbered proof. A route /
    proposed_steps list / advisory_proof_sketch is NOT sealable. The gate enforces:

    - a ``line_numbered_proof.yaml`` exists when the status is sealed (missing /
      malformed / route-only -> fail closed);
    - the consensus hash-binds claim + line_numbered_proof (+ source/review/prover_loop),
      so editing the proof after review revokes the seal (fail closed);
    - every PASS (no_fatal_flaw_found) reviewer cites real proof line_id(s) -- a PASS
      that references no line is not a review of the proof;
    - the three distinct proof reviewers reached a unanimous no-fatal-flaw consensus.

    The kernel never computes the agents' judgement (it is an explicitly agent-
    consensus, amber, NOT-machine-verified tier) -- it enforces that a structurally
    valid, current, unanimous review OF THE LINE-NUMBERED PROOF exists, and that no
    stronger/contradicting signal coexists.
    """
    status_uses_it = (
        idea_grade.proof_language_status
        == ProofLanguageStatus.agent_consensus_mock_checked
    )
    if consensus is None:
        if status_uses_it:
            raise KernelValidationError(
                "idea_grade.proof_language_status=agent_consensus_mock_checked requires "
                "an agent_consensus_review artifact"
            )
        return

    # 1. A line-numbered proof MUST exist for a consensus to mean anything.
    #    A route / proposed_steps / advisory sketch is not a proof and is not sealable.
    #    The seal helper also checks this upfront (fail-fast); THIS validator gate is
    #    the authoritative BACKSTOP that blocks a hand-forged consensus bypassing the
    #    seal helper (e.g. a manually written agent_consensus_review.yaml).
    if line_numbered_proof is None:
        raise KernelValidationError(
            "agent_consensus_review requires a line_numbered_proof.yaml; a route / "
            "proposed_steps / advisory_proof_sketch is not a sealable proof"
        )

    # 1b. No line may still carry an unresolved dependency. This rule used to live
    #     only in orchestration/agent_consensus_seal.py, so the sanctioned path
    #     enforced it and a hand-written agent_consensus_review.yaml did not —
    #     reproduced by a hostile review, which sealed a proof whose lines still
    #     carried gaps. A rule that decides a status has to be enforced where the
    #     kernel can see it, not in the component that writes the artifact.
    manifest_dict = manifest.model_dump() if hasattr(manifest, "model_dump") else dict(manifest)
    proof_artifact = (manifest_dict.get("optional_artifacts") or {}).get(
        "line_numbered_proof", "line_numbered_proof.yaml"
    )
    proof_dict = (
        line_numbered_proof.model_dump()
        if hasattr(line_numbered_proof, "model_dump")
        else dict(line_numbered_proof)
    )
    unresolved = unresolved_proof_dependency_lines(
        proof_dict,
        trusted_human_allowed_theorem_refs=trusted_human_allowed_theorem_refs(
            run_dir, manifest_dict, proof_artifact
        ),
        trusted_source_grounded_theorem_refs=trusted_source_grounded_theorem_refs(
            run_dir, manifest_dict
        ),
    )
    if unresolved:
        raise KernelValidationError(
            "agent_consensus_review cannot seal a line-numbered proof with "
            f"unresolved dependencies: {', '.join(unresolved)}"
        )

    # 2. Every PASS must cite real proof line_id(s) (rule 4): a no-fatal-flaw
    #    verdict that references no line is not a review of the line-numbered proof.
    proof_line_ids = {line.line_id for line in line_numbered_proof.lines}
    for entry in consensus.entries:
        if entry.verdict == AgentConsensusVerdict.no_fatal_flaw_found:
            cited = [lid for lid in entry.cited_line_ids if lid]
            unknown = [lid for lid in cited if lid not in proof_line_ids]
            if not cited:
                raise KernelValidationError(
                    f"reviewer {entry.reviewer_id} returned no_fatal_flaw_found but did "
                    "not cite any line_id of the line-numbered proof"
                )
            if unknown:
                raise KernelValidationError(
                    f"reviewer {entry.reviewer_id} cited unknown line_id(s) in the "
                    f"line-numbered proof: {', '.join(sorted(set(unknown)))}"
                )

    # 3. A line_proof reviewer that FAILED the standard kernel review cannot claim
    #    no_fatal_flaw here (the one role that overlaps the front-half review set).
    failed = {
        reviewer.reviewer_id
        for reviewer in review.reviewers
        if reviewer.verdict == ReviewerVerdict.failed
    }
    for entry in consensus.entries:
        if (
            entry.reviewer_id in failed
            and entry.verdict == AgentConsensusVerdict.no_fatal_flaw_found
        ):
            raise KernelValidationError(
                f"reviewer {entry.reviewer_id} failed the review but claims "
                "no_fatal_flaw_found in the agent consensus"
            )

    # 4. Hash binding (revoke on change): the bound_* hashes must equal the CURRENT
    #    artifact hashes -- crucially the line-numbered proof, so editing the proof
    #    after review fails the seal closed.
    artifacts = manifest.artifacts
    optional = manifest.optional_artifacts
    prover_loop_rel = optional.get("prover_loop")
    proof_rel = optional.get("line_numbered_proof")
    expected = {
        "bound_claim_hash": artifact_revision_hash(run_dir, artifacts["locked_claim"]),
        "bound_line_numbered_proof_hash": (
            artifact_revision_hash(run_dir, proof_rel) if proof_rel else ""
        ),
        "bound_source_grounding_hash": artifact_revision_hash(
            run_dir, artifacts["source_grounding"]
        ),
        "bound_review_hash": artifact_revision_hash(run_dir, artifacts["review"]),
        "bound_prover_loop_hash": (
            artifact_revision_hash(run_dir, prover_loop_rel) if prover_loop_rel else ""
        ),
    }
    for field_name, expected_hash in expected.items():
        if getattr(consensus, field_name) != expected_hash:
            raise KernelValidationError(
                f"agent_consensus_review.{field_name} does not match the current run "
                "artifacts; the consensus is stale and must be re-sealed"
            )

    if not status_uses_it:
        return

    # 5. The status path requires a unanimous consensus (belt; model enforces the
    #    consensus<->entries consistency).
    if not consensus.consensus:
        raise KernelValidationError(
            "agent_consensus_mock_checked requires a unanimous no_fatal_flaw_found "
            "consensus from all three proof reviewers"
        )

    # 6. A refuted claim can never carry a mock-checked proof.
    if idea_grade.status == IdeaStatus.refuted:
        raise KernelValidationError(
            "a refuted claim cannot carry "
            "proof_language_status=agent_consensus_mock_checked"
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
    theorem_condition_ledger: TheoremConditionLedger | None = None,
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

    # Cap-aware ledger/graph id consistency. The theorem-condition ledger
    # is the SINGLE WRITER of ``subgoal_tcl_*`` repair nodes (their ids come from a
    # ledger entry's ``repair_subgoal_id``). When BOTH artifacts exist, every such
    # node in the graph MUST correspond to a real ledger entry — this catches a
    # hallucinated / stale ledger repair node smuggled into the graph.
    #
    # CAP-SAFE (does NOT break under _MAX_LEDGER_SUBGOALS): the builder caps how
    # many ledger entries become graph nodes, so the graph only ever holds a SUBSET
    # of the ledger's repair ids — never an extra one. We therefore check
    # "every graph tcl node ⊆ ledger ids", NOT "every ledger entry has a node"
    # (the latter would falsely fail a capped graph). The advisory
    # ``target_subgoal_id`` is a different, unverified field and is untouched here.
    if theorem_condition_ledger is not None:
        ledger_repair_ids = {
            entry.repair_subgoal_id for entry in theorem_condition_ledger.entries
        }
        for node in proof_goal_graph.nodes:
            if node.id.startswith("subgoal_tcl_") and node.id not in ledger_repair_ids:
                raise KernelValidationError(
                    f"{node.id}: a subgoal_tcl_* repair node has no matching "
                    "theorem_condition_ledger entry (hallucinated or stale ledger "
                    "repair node)"
                )


def _validate_manifest_paths(run_dir: Path, manifest: RunManifest) -> None:
    try:
        validate_manifest_paths(run_dir, manifest)
    except ManifestPathValidationError as exc:
        raise KernelValidationError(str(exc)) from exc


__all__ = [
    '_load_optional_agent_consensus_review',
    '_load_optional_ai_review',
    '_load_optional_line_numbered_proof',
    '_load_optional_literature_review_agent',
    '_load_optional_llm_agent_run',
    '_load_optional_proof_goal_graph',
    '_load_optional_prover_loop',
    '_load_optional_prover_round_index',
    '_load_optional_theorem_applicability',
    '_load_optional_theorem_applicability_review',
    '_load_optional_theorem_condition_ledger',
    '_load_optional_theorem_extraction',
    '_prover_assertion_clauses',
    '_prover_clause_is_status_boundary_disclaimer',
    '_prover_status_smuggling_field',
    '_validate_agent_consensus_review',
    '_validate_manifest_paths',
    '_validate_subgoal_graph',
]
