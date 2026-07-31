"""Proof-verification entries, witnesses, external tools, scoped requests."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal
import re

from espresso_proof.kernel.models.foundations import StrictBaseModel


class VerifierType(str, Enum):
    """A trusted-verifier backend kind.

    - ``finite_group_cayley``: deterministic brute-force check of an explicitly
      given finite group (implemented; trusted).
    - ``finite_identity_table``: exhaustive check of an algebraic identity on an
      explicitly-given finite operation table (implemented; trusted).
    - ``finite_ring_identity``: exhaustive check of a named algebraic identity
      over Z/n for an explicit modulus n (implemented; trusted).
    - ``exact_computation``: the exact_arithmetic / permutation_order backend
      (implemented elsewhere; trusted).
    - ``lean_bridge``: a Lean/mathlib proof check (NOT executed in V0; registry
      only -- it cannot earn a checked status here).
    - ``human_checked``: a human attestation (NOT machine verification; registry
      only in V0 -- no attestation artifact exists, so it cannot earn a status).
    """

    finite_group_cayley = "finite_group_cayley"
    finite_identity_table = "finite_identity_table"
    finite_ring_identity = "finite_ring_identity"
    matrix_rank = "matrix_rank"
    exact_computation = "exact_computation"
    lean_bridge = "lean_bridge"
    human_checked = "human_checked"

class ProofVerificationStatus(str, Enum):
    """What a verifier entry EARNED. There is deliberately NO ``proved`` value:
    a finite/instance check can never earn a universal proof in V0.

    - ``instance_checked``: a specific finite instance was machine-checked.
    - ``computation_checked``: a computation was confirmed by a trusted backend.
    - ``lean_checked``: a Lean proof compiled (reserved; not earnable in V0).
    - ``human_attested``: a human checked it (reserved; NOT earnable in V0 --
      there is no human-attestation artifact for the kernel to check).
    - ``unverified``: nothing earned / the verifier failed (fail closed).
    """

    instance_checked = "instance_checked"
    computation_checked = "computation_checked"
    lean_checked = "lean_checked"
    human_attested = "human_attested"
    unverified = "unverified"

class ProofVerificationEntry(StrictBaseModel):
    """One verifier run. ``trusted`` marks a deterministic machine backend; only a
    trusted backend with a confirming result may earn a checked status (enforced by
    the validator). ``scope_note`` must state exactly what was (and was not) shown.

    The reproducibility fields are provenance only — they record HOW to
    replay a checked computation (adapter, versions, input/output hashes, exact
    replay command). They are optional/additive and never earn or upgrade status.
    """

    id: str
    verifier_type: VerifierType
    claim_id: str
    target: str
    input_summary: str
    result: str
    trusted: bool
    confirmed: bool
    earned_status: ProofVerificationStatus
    scope_note: str
    adapter_id: str = ""
    # What the kernel needs to re-derive this result itself. An entry claiming a
    # checked status must name an adapter_id the kernel can recompute (see
    # kernel/recompute.py) and record whatever input that adapter takes; the
    # validator re-runs it and compares transcript hashes. Self-declared
    # trusted/confirmed booleans are no longer sufficient.
    recompute_input: dict[str, Any] = Field(default_factory=dict)
    code_version: str = ""
    dependency_version: str = ""
    input_hash: str = ""
    output_hash: str = ""
    replay_command: str = ""

class ProofVerificationReport(StrictBaseModel):
    """Proof Verification Bridge V0.

    Records trusted-verifier runs and the SCOPED status each earned. Natural-
    language prover/reviewer approval is not verification and cannot reach this
    report. The model has no ``proved`` status: a finite/instance check earns only
    ``instance_checked`` / ``computation_checked`` for what it actually covered;
    ``lean_checked`` is reserved and not earnable in V0; ``human_attested`` is
    likewise reserved and not earnable in V0 (no human-attestation artifact
    exists for the kernel to check). The validator fails closed.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    entries: list[ProofVerificationEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class FiniteObjectWitnessReport(StrictBaseModel):
    """Scoped finite-object witness sidecar.

    The sidecar is reviewer-facing evidence packaging only. It must stay scoped
    and advisory; the checked status, if any, is owned by proof_verification.yaml.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    witness_id: str
    kind: Literal["gl2_f3_solvability"]
    advisory_only: Literal[True]
    witness: dict[str, Any]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gl2_witness_shape(self) -> FiniteObjectWitnessReport:
        required = {
            "order",
            "derived_series_orders",
            "is_solvable",
            "confirmed",
            "transcript_sha256",
        }
        missing = sorted(required - set(self.witness))
        if missing:
            raise ValueError(f"finite object witness missing required keys: {missing}")
        if self.witness["order"] != 48:
            raise ValueError("GL_2(F_3) witness must record order 48")
        if self.witness["derived_series_orders"] != [48, 24, 8, 2, 1]:
            raise ValueError("GL_2(F_3) witness must record the expected derived series")
        if self.witness["is_solvable"] is not True or self.witness["confirmed"] is not True:
            raise ValueError("GL_2(F_3) witness must be confirmed solvable")
        if not str(self.witness["transcript_sha256"]).strip():
            raise ValueError("finite object witness requires transcript_sha256")
        return self

class ExternalToolCandidate(StrictBaseModel):
    """A discovered external tool proposed for a scoped check (slice 1).

    Discovery is candidate-only: ``status`` is an allowlisting decision, NOT an
    evidence status. The model carries no source/evidence/proof status field (and
    StrictBaseModel forbids extras), so proposing a tool can never earn anything.
    Only an allowlisted deterministic adapter + the existing proof-verification
    bridge can earn scoped evidence; an unknown tool stays ``candidate``.
    """

    name: str
    source_url: str = ""
    version: str = ""
    why_relevant: str = ""
    status: Literal["candidate", "allowlisted", "rejected"] = "candidate"

class ExternalToolCandidatesReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    candidates: list[ExternalToolCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class ExternalToolRun(StrictBaseModel):
    """One audited external-tool execution record (slice 1).

    This is RAW evidence with full provenance, NOT an earned status: the model has
    no proof/source/evidence/grade field, so an external tool can never write a
    trusted status here. Only a kernel-gated bridge (proof_verification, a trusted
    deterministic backend) may later earn a SCOPED instance/computation status
    from a confirming run. ``trusted`` records only whether the producing adapter
    is allowlisted; it does not by itself earn anything. Paths are relative to the
    run directory (absolute / ``..`` are rejected by the validator). No secrets or
    full environment are recorded.
    """

    id: str
    tool_name: str
    source_url: str = ""
    version: str = ""
    install_command: str = ""
    argv: list[str] = Field(default_factory=list)
    working_dir: str = "."
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = None
    timeout_seconds: float | None = None
    timed_out: bool = False
    parsed_result: str = ""
    ai_interpretation: str = ""
    conclusion: str = ""
    statement_checked: str = ""
    scope_note: str
    not_universal_proof: Literal[True] = True
    network_used: bool = False
    trusted: bool = False

    @model_validator(mode="after")
    def validate_no_proof_complete_interpretation(self) -> ExternalToolRun:
        # Stdout/stderr excerpts stay RAW audited evidence (the display
        # layer scrubs them), but the INTERPRETIVE fields are adapter/agent-
        # authored and must never assert global proof completion. Fail closed at
        # parse time so "the tool proved the theorem" can never persist as an
        # interpretation.
        from espresso_proof.kernel.proof_language import contains_proof_language

        for field in ("ai_interpretation", "conclusion", "statement_checked", "scope_note"):
            phrase = contains_proof_language(getattr(self, field))
            if phrase:
                raise ValueError(
                    f"external tool run field {field!r} contains proof-complete "
                    f"language ({phrase!r}); tool evidence is scoped and never a "
                    "universal proof"
                )
        return self

class ExternalToolRunsReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    runs: list[ExternalToolRun] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class ScopedVerificationRequest(StrictBaseModel):
    """A CONCRETE, machine-runnable scoped-verification request proposed by the
    strategy/prover stage so a finite/computational check can be auto-wired into a
    run instead of only run by hand.

    It is ONLY a proposal: a trusted deterministic backend runs it and only a
    confirming result earns ``instance_checked`` (via the existing proof-
    verification gate). It carries NO status / earned field, so a request can never
    self-certify, and natural-language prose is never read here. Auto-wiring fails
    closed on a missing / ambiguous / too-expensive request (it earns nothing).
    """

    id: str
    kind: Literal[
        "finite_identity_table",
        "finite_ring_identity",
        "matrix_rank",
        "named_finite_witness",
        "finite_group_from_generators",
    ]
    # named_finite_witness carries no data of its own: `witness` names a
    # builder in NAMED_FINITE_WITNESSES that regenerates the finite object,
    # so a hand-edited request cannot supply fabricated tables.
    witness: str = ""
    # finite_group_from_generators: the group is DEFINED by the request's own
    # data (permutations, one-line images, 0-indexed); the verifier enumerates
    # the closure itself, so no named-object matching is involved anywhere.
    generators: list[list[int]] | None = None
    expected_order: int | None = None
    expected_solvable: bool | None = None
    identity: str = ""
    table: list[list[int]] | None = None  # operation table (finite_identity_table) or matrix (matrix_rank)
    modulus: int | None = None  # required for finite_ring_identity
    expected_rank: int | None = None  # matrix_rank
    expected_determinant: int | None = None  # matrix_rank (square only)
    target: str = ""

class ScopedVerificationRequests(StrictBaseModel):
    """The strategy/prover stage's structured scoped-verification proposals for a
    run. The auto-wiring reader executes only these explicit, concrete requests;
    prose in other artifacts is never parsed into a request."""

    claim_id: str
    requests: list[ScopedVerificationRequest] = Field(default_factory=list)


__all__ = [
    "ExternalToolCandidate",
    "ExternalToolCandidatesReport",
    "ExternalToolRun",
    "ExternalToolRunsReport",
    "FiniteObjectWitnessReport",
    "ProofVerificationEntry",
    "ProofVerificationReport",
    "ProofVerificationStatus",
    "ScopedVerificationRequest",
    "ScopedVerificationRequests",
    "VerifierType",
]
