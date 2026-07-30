"""Validators — internal topological slice 3/6 (callees precede callers). Imported via espresso_proof.kernel.validators."""


from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AutonomousLiteratureRetrievalReport,
    Claim,
    ComputationVerificationReport,
    ExternalToolCandidatesReport,
    ExternalToolRunsReport,
    FiniteObjectWitnessReport,
    LiteratureCandidates,
    MemoryContext,
    RetrievalResolutionReport,
    LiteratureRetrievalReport,
    LiteratureSearchPlan,
    ProofVerificationReport,
    ProofVerificationStatus,
    ProofGapResolutionReport,
    IdeaGrade,
    LineNumberedProof,
    ProofLanguageStatus,
    VerificationVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    TheoremExtractionReport,
)
from espresso_proof.kernel.pressure_status import (
    artifact_revision_hash,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.manifest import ManifestPathValidationError
from espresso_proof.validation.manifest import validate_relative_artifact_path


from espresso_proof.kernel.recompute import is_recomputable, recompute_result
from espresso_proof.kernel.validators._part1 import (
    _TRUSTED_EARNED,
    _adapt_legacy_memory_context,
    _memory_context_schema_version,
    validate_yaml_model,
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


def _validate_autonomous_literature_retrieval(
    report: AutonomousLiteratureRetrievalReport | None,
    source_grounding: SourceGroundingReport,
    theorem_extraction: TheoremExtractionReport | None,
) -> None:
    """Bind autonomous retrieval's earned flag to source-grounding truth.

    The retrieval sidecar is provenance/scheduling context. If it records that
    grounding earned source support, the current validated source-grounding
    artifact must actually contain earned source-backed refs/steps for the
    candidate/source this retrieval handed to grounding.
    """

    if report is None or not report.earned_source_backed:
        return
    if not report.grounded:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires grounded=true"
        )
    if not report.grounded_candidate_id or not report.grounded_retrieval_source_id:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires grounded candidate identity"
        )
    candidates_by_id = {candidate.id: candidate for candidate in report.candidates}
    grounded_candidate = candidates_by_id.get(report.grounded_candidate_id)
    if grounded_candidate is None:
        raise KernelValidationError(
            "autonomous_literature_retrieval grounded_candidate_id is not one of its candidates"
        )
    if grounded_candidate.provider != "local_corpus" or not grounded_candidate.local_path:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires a grounded local candidate"
        )
    expected_source_id = f"ret_{Path(grounded_candidate.local_path).stem}"
    if report.grounded_retrieval_source_id != expected_source_id:
        raise KernelValidationError(
            "autonomous_literature_retrieval grounded source does not match grounded candidate"
        )
    if theorem_extraction is None:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires theorem_extraction binding"
        )
    allowed_ref_ids = {
        f"verified_{theorem.id}"
        for theorem in theorem_extraction.theorems
        if theorem.source_ref_id == report.grounded_retrieval_source_id
    }
    reported_ref_ids = set(report.earned_source_ref_ids)
    if not reported_ref_ids or not reported_ref_ids <= allowed_ref_ids:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned source refs do not match grounded retrieval source"
        )
    refs_by_id = {ref.id: ref for ref in source_grounding.source_refs}
    if any(
        refs_by_id.get(ref_id) is None
        or refs_by_id[ref_id].status != SourceStatus.source_backed
        for ref_id in reported_ref_ids
    ):
        raise KernelValidationError(
            "autonomous_literature_retrieval earned source refs are not source-grounding backed"
        )
    reported_step_ids = set(report.earned_proof_step_ids)
    if not reported_step_ids:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires an "
            "earned source-grounding step"
        )
    steps_by_id = {step.proof_step_id: step for step in source_grounding.steps}
    for step_id in reported_step_ids:
        step = steps_by_id.get(step_id)
        if step is None or step.status != SourceStatus.source_backed:
            raise KernelValidationError(
                "autonomous_literature_retrieval earned proof step is not source-backed"
            )
        if not set(step.source_refs) & reported_ref_ids:
            raise KernelValidationError(
                "autonomous_literature_retrieval earned proof step is not bound to earned source ref"
            )
    if not any(
        step.status == SourceStatus.source_backed
        and set(step.source_refs) & reported_ref_ids
        for step in source_grounding.steps
    ):
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires an "
            "earned source-grounding step bound to the grounded retrieval source"
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


def _load_optional_proof_gap_resolution(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofGapResolutionReport | None:
    optional = manifest.optional_artifacts
    if "proof_gap_resolution" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_gap_resolution"],
        ProofGapResolutionReport,
    )


def _validate_proof_gap_resolution(
    report: ProofGapResolutionReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    if report is None:
        return
    if report.claim_id != claim.id:
        raise KernelValidationError("proof_gap_resolution claim_id does not match locked claim")
    if report.run_id != manifest.run_id:
        raise KernelValidationError("proof_gap_resolution run_id does not match manifest")
    proof_artifact = manifest.optional_artifacts.get("line_numbered_proof")
    if not proof_artifact:
        raise KernelValidationError("proof_gap_resolution requires a line_numbered_proof artifact")
    if report.proof_artifact != proof_artifact:
        raise KernelValidationError("proof_gap_resolution proof_artifact does not match line_numbered_proof")
    if report.line_numbered_proof_sha256 != artifact_revision_hash(run_dir, proof_artifact):
        raise KernelValidationError("proof_gap_resolution is stale for the current line_numbered_proof")
    proof = validate_yaml_model(run_dir / proof_artifact, LineNumberedProof)
    proof_dependencies = {
        (line.line_id, dep.ref): dep
        for line in proof.lines
        for dep in line.dependencies
    }
    seen: set[tuple[str, str]] = set()
    for candidate in report.candidates:
        key = (candidate.line_id, candidate.dependency_ref)
        if key in seen:
            raise KernelValidationError("duplicate proof gap resolution candidate")
        seen.add(key)
        if candidate.action == "attach_human_allowed_theorem_ref":
            if candidate.status != "accepted_by_human":
                raise KernelValidationError("human-allowed proof gap candidate must be accepted_by_human")
            if candidate.source_grounded or candidate.machine_verified:
                raise KernelValidationError("human-allowed theorem refs are advisory, not source-grounded or machine-verified")
            dep = proof_dependencies.get((candidate.line_id, candidate.proposed_ref))
            if dep is None:
                raise KernelValidationError("human-allowed proof gap candidate is not attached to the proof graph")
            if dep.authority != "human_allowed_theorem_ref" or dep.status != "accepted_by_human":
                raise KernelValidationError("human-allowed proof dependency lacks trusted advisory provenance")
            if dep.source_grounded or dep.machine_verified:
                raise KernelValidationError("human-allowed proof dependency must remain advisory")
        if candidate.action == "attach_candidate_theorem_ref":
            if candidate.status != "candidate":
                raise KernelValidationError("agent source-locator theorem refs must remain candidate until reviewed")
            dep = proof_dependencies.get((candidate.line_id, candidate.proposed_ref))
            if dep is None:
                raise KernelValidationError("agent proof gap candidate is not attached to the proof graph")


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
    artifact_path = optional["memory_context"]
    path = run_dir / artifact_path
    raw = read_yaml(path)
    if isinstance(raw, dict) and _memory_context_schema_version(raw) < 2:
        raw = _adapt_legacy_memory_context(raw, run_dir, artifact_path)
    try:
        return MemoryContext.model_validate(raw)
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc


def _validate_memory_context(
    memory_context: MemoryContext,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    if memory_context.claim_id != claim.id:
        raise KernelValidationError(
            f"memory_context claim_id mismatch: {memory_context.claim_id!r} != {claim.id!r}"
        )
    if memory_context.run_id != manifest.run_id:
        raise KernelValidationError(
            f"memory_context run_id mismatch: {memory_context.run_id!r} != {manifest.run_id!r}"
        )
    if memory_context.schema_version < 2 and memory_context.matched_entries:
        raise KernelValidationError(
            "memory_context with matched_entries requires schema_version >= 2 replay metadata"
        )
    for entry in memory_context.matched_entries:
        if not entry.memory_claim_id.strip():
            raise KernelValidationError("memory_context entry requires memory_claim_id")
        if not entry.provenance:
            raise KernelValidationError("memory_context entry requires provenance")
        cross_claim = entry.memory_claim_id != claim.id
        if cross_claim and not entry.revalidation_required:
            raise KernelValidationError(
                "memory_context replay under a different locked claim requires revalidation"
            )
        if cross_claim and entry.revalidation_required and entry.recommended_use != "note":
            raise KernelValidationError(
                "pending cross-claim memory replay must be non-actionable"
            )
        if cross_claim and entry.revalidated_against_locked_claim:
            raise KernelValidationError(
                "memory_context cannot claim revalidation for a different locked claim"
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


def _load_optional_finite_object_witness(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> FiniteObjectWitnessReport | None:
    optional = manifest.optional_artifacts
    if "finite_object_witness" not in optional:
        return None
    witness = validate_yaml_model(
        run_dir / optional["finite_object_witness"],
        FiniteObjectWitnessReport,
    )
    if witness.claim_id != claim.id:
        raise KernelValidationError("finite_object_witness claim_id does not match locked claim")
    if witness.run_id != manifest.run_id:
        raise KernelValidationError("finite_object_witness run_id does not match manifest")
    return witness


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
            try:
                validate_relative_artifact_path(
                    where,
                    raw,
                    path_name="external_tool_runs path",
                )
            except ManifestPathValidationError as exc:
                raise KernelValidationError(
                    f"external_tool_runs {str(exc)}"
                ) from exc

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
    - ``human_attested`` is NOT earnable in V0 either -> always rejected: there
      is no human-attestation artifact for the kernel to re-derive it from, so
      an entry claiming it is unverifiable say-so (fail closed);
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
            # The booleans above are written by whoever wrote the file, so they
            # are a declaration, not evidence. The kernel re-derives the result
            # itself and compares its own transcript hash. A forged entry cannot
            # pass: no adapter reproduces a hash it invented.
            if not is_recomputable(entry.adapter_id):
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} but "
                    f"adapter_id {entry.adapter_id!r} is not a kernel-recomputable "
                    "deterministic verifier; a checked status cannot be earned from "
                    "a result the kernel cannot re-derive"
                )
            recomputed = recompute_result(entry.adapter_id, entry.recompute_input)
            if recomputed is None:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} but the "
                    f"kernel could not re-derive it with {entry.adapter_id!r} "
                    "(missing or malformed recompute_input)"
                )
            if not recomputed["confirmed"]:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} but the "
                    "kernel's own recomputation does not confirm it"
                )
            # Hash comparison only when both sides produced one: several shipped
            # verifiers do not emit a transcript hash, and the re-derived
            # confirming boolean above is the part a forged entry cannot fake.
            digest = recomputed["transcript_sha256"]
            if digest and entry.output_hash and digest != entry.output_hash:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} but the "
                    "kernel's recomputation does not reproduce the recorded "
                    f"output_hash (recorded {entry.output_hash[:16]}..., recomputed "
                    f"{digest[:16]}...)"
                )
            if _TRUSTED_EARNED.get(vt) != earned:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: verifier_type {vt!r} cannot "
                    f"earn {earned!r}"
                )
        elif earned == "human_attested":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: human_attested is not earnable "
                "in V0: there is no human-attestation artifact for the kernel to check, "
                "so a recorded attestation would be unverifiable say-so (fail closed)"
            )
        # earned == unverified is always acceptable (fail-closed default)
        if vt == "lean_bridge" and earned != "unverified":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_bridge cannot earn {earned!r} in V0"
            )


def _validate_grade_proof_language_backed(
    idea_grade: IdeaGrade,
    source_grounding: SourceGroundingReport,
    computation_verification: ComputationVerificationReport | None,
    proof_verification: ProofVerificationReport | None,
    agent_consensus_review: AgentConsensusReview | None = None,
) -> None:
    """A "verified-proof" grade status must be backed by a real trusted result.

    ``idea_grade.proof_language_status`` renders as "A proof has been checked by a
    trusted verifier", so the VERIFIED statuses cannot be asserted from prose or a
    forged grade:

    - ``source_checked_argument`` requires at least one source-grounding step
      that actually earned ``source_backed``. This remains step-local reference
      support, not a checked proof.
    - ``computation_checked`` requires an actual confirming MACHINE result -- a
      trusted+confirming proof-verification entry (computation/instance checked)
      or a confirmed computation-verification result. A scoped/finite computation
      that genuinely backs the grade therefore stays scoped (it earned its entry);
      a grade that merely *claims* computation_checked with no machine result is
      rejected. This is the "no global proof status from scoped computation" gate.
    - ``lean_checked`` / ``human_confirmed`` are NOT earnable in V0: Espresso has
      no Lean/global-proof lane and no human-proof-attestation artifact (and human
      feedback may never upgrade proof status). Fail closed.
    - Any OTHER status (i.e. a future enum member) is rejected outright: a new
      proof-language status must add an explicit backing rule here before a run
      carrying it can validate. Only the two weak statuses (``idea_sketch``,
      ``proof_candidate``) validate without backing.
    """
    status = idea_grade.proof_language_status
    if status == ProofLanguageStatus.agent_consensus_mock_checked:
        # Agent-consensus mock-check: amber/advisory, NOT machine-verified. Requires
        # a unanimous adversarial consensus artifact; reviewer prose alone cannot
        # upgrade the proof language. The hash binding + cross-checks live in
        # _validate_agent_consensus_review.
        if agent_consensus_review is None or not agent_consensus_review.consensus:
            raise KernelValidationError(
                "idea_grade.proof_language_status=agent_consensus_mock_checked requires "
                "a unanimous agent_consensus_review (no_fatal_flaw_found from all three "
                "reviewers); reviewer prose cannot upgrade proof language"
            )
    elif status == ProofLanguageStatus.source_checked_argument:
        if not any(
            step.status == SourceStatus.source_backed
            for step in source_grounding.steps
        ):
            raise KernelValidationError(
                "idea_grade.proof_language_status=source_checked_argument requires "
                "at least one earned source-grounding step; reviewer/source prose "
                "cannot upgrade proof language"
            )
    elif status == ProofLanguageStatus.computation_checked:
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
    elif status not in {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
    }:
        # Default-deny backstop: a proof-language status with no explicit backing
        # rule above must never validate. Without this, adding a new enum member
        # would silently fall through as if it needed no backing at all.
        raise KernelValidationError(
            f"idea_grade.proof_language_status={getattr(status, 'value', status)!r} has "
            "no backing rule in the kernel validator; a new status must add an explicit "
            "fail-closed branch here before a run carrying it can validate"
        )


def _validate_grade_10_requires_global_proof_status(idea_grade: IdeaGrade) -> None:
    """A perfect score is a global proof-strength claim, not local support.

    Source-backed theorem applicability is intentionally step-local. It can
    support an individual proof step, but it cannot by itself make the whole
    route proved or justify a Grade-10/perfect grade while the proof-language
    status is still weak or scoped.
    """
    weak_or_scoped_statuses = {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
        # Agent consensus is amber/advisory, never a global machine-checked proof,
        # so it can never imply a perfect score.
        ProofLanguageStatus.agent_consensus_mock_checked,
        ProofLanguageStatus.source_checked_argument,
        ProofLanguageStatus.computation_checked,
    }
    if idea_grade.score == 100 and idea_grade.proof_language_status in weak_or_scoped_statuses:
        raise KernelValidationError(
            "idea_grade.score=100 requires a global checked proof status; local "
            "source-backed/applicable steps, proof candidates, and scoped checks cannot "
            "upgrade the grade to Grade 10"
        )


__all__ = [
    '_load_optional_autonomous_literature_retrieval',
    '_load_optional_external_tool_candidates',
    '_load_optional_external_tool_runs',
    '_load_optional_finite_object_witness',
    '_load_optional_literature_candidates',
    '_load_optional_literature_search_plan',
    '_load_optional_memory_context',
    '_load_optional_proof_gap_resolution',
    '_load_optional_proof_verification',
    '_load_optional_retrieval_resolution',
    '_validate_autonomous_literature_retrieval',
    '_validate_external_tool_artifacts',
    '_validate_grade_10_requires_global_proof_status',
    '_validate_grade_proof_language_backed',
    '_validate_literature_retrieval',
    '_validate_memory_context',
    '_validate_proof_gap_resolution',
    '_validate_proof_verification',
]
