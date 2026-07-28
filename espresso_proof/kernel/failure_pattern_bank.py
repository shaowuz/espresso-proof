"""Math Failure Pattern Bank V1 (Six-Hour goal, Phase 2).

A structured catalogue of mathematical failure patterns plus a per-run audit that
records which patterns the run's signals match.

Safety model (three tiers, none of which earns or clears status):

- ``candidate``: WARN only. New / AI- / reviewer-suggested patterns enter here.
- ``audited``: may inform RANKING / TIME ALLOCATION only (advisory weight).
- ``hard``: maps to an EXISTING kernel validator (``enforced_by``). The pattern
  does not add new blocking logic; it documents that the validator already fails
  closed on the unsafe use. The very-general-to-all gate is the first hard pattern.

A pattern match never proves falsehood, never refutes a claim, and never upgrades
proof/source/evidence status (the audit model carries no status field and the
validator re-checks that each match's tier/action/enforced_by equals the canonical
seed bank, so a candidate can never be promoted to hard at run time, by memory, or
by human input).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import FailurePatternAudit, FailurePatternBank

# Names of EXISTING kernel validators a hard pattern may point at. A hard pattern
# adds no new blocking; it must reference one of these already-enforced gates.
ENFORCING_VALIDATORS = {
    "_validate_theorem_applicability",
    "_validate_source_backing",
    "SourceRef.validate_source_backed",
    "_validate_example_evidence",
    "_validate_test_report_evidence",
    "_validate_computation_verification",
    "CounterexampleTest.validate_refutation_scope",
}

_UNCHECKED_COMPUTATION = {"inconclusive", "human_needed"}
_CHECKED_COMPUTATION = {"checked", "source_checked"}

# --- Seed bank (>=15 patterns across the required families) -------------------
# tier: candidate=warn, audited=rank_downgrade, hard=block_via_validator.
_SEED: list[dict] = [
    # hard patterns -> existing structural validators (no new blocking added)
    {"id": "very_general_to_all", "family": "generality",
     "summary": "A 'generic' / 'very general' result is applied to 'all' objects.",
     "tier": "hard", "action": "block_via_validator",
     "enforced_by": "_validate_theorem_applicability"},
    {"id": "dropped_hypothesis_not_refutation", "family": "counterexample_scope",
     "summary": "A counterexample that drops a claim hypothesis does not refute the claim.",
     "tier": "hard", "action": "block_via_validator",
     "enforced_by": "CounterexampleTest.validate_refutation_scope"},
    {"id": "proof_step_attack_not_refutation", "family": "counterexample_scope",
     "summary": "Attacking a proof step is not a refutation of the claim.",
     "tier": "hard", "action": "block_via_validator",
     "enforced_by": "CounterexampleTest.validate_refutation_scope"},
    {"id": "unchecked_example_not_evidence", "family": "evidence",
     "summary": "An unchecked example/computation cannot support survived/failed evidence.",
     "tier": "hard", "action": "block_via_validator",
     "enforced_by": "_validate_example_evidence"},
    {"id": "fabricated_source_backed", "family": "source",
     "summary": "A source cannot be source_backed without exact locator + hypothesis match + attribution.",
     "tier": "hard", "action": "block_via_validator",
     "enforced_by": "SourceRef.validate_source_backed"},
    # audited patterns -> ranking / time allocation only
    {"id": "finite_check_not_universal", "family": "evidence",
     "summary": "Checking finitely many cases is not a proof of a universal claim.",
     "tier": "audited", "action": "rank_downgrade", "enforced_by": ""},
    {"id": "source_supports_different_claim", "family": "source",
     "summary": "The cited theorem's conclusion supports a different claim than intended.",
     "tier": "audited", "action": "rank_downgrade", "enforced_by": ""},
    # candidate patterns -> warn only
    {"id": "smooth_not_singular", "family": "geometry",
     "summary": "A result for smooth objects need not hold for singular ones.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "alg_closed_not_arbitrary_field", "family": "field",
     "summary": "A result over an algebraically closed field need not hold over an arbitrary field.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "char_zero_not_positive_char", "family": "characteristic",
     "summary": "A characteristic-zero result need not hold in positive characteristic.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "finite_dim_not_infinite_dim", "family": "dimension",
     "summary": "A finite-dimensional result need not extend to infinite dimensions.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "example_not_universal_proof", "family": "evidence",
     "summary": "Example evidence does not prove a universal claim.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "iso_noncanonical_not_equality", "family": "canonicity",
     "summary": "Isomorphism up to a noncanonical choice is not equality.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "local_not_global", "family": "locality",
     "summary": "A local property does not automatically imply the global property.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "existence_not_uniqueness", "family": "logic",
     "summary": "Existence of an object does not imply uniqueness.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "necessary_not_sufficient", "family": "logic",
     "summary": "A necessary condition is not automatically sufficient.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "converse_not_equivalent", "family": "logic",
     "summary": "A theorem does not give its converse; do not assume equivalence.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "finite_not_infinite", "family": "cardinality",
     "summary": "A result for finite objects need not hold for infinite ones.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "compact_proper_complete_assumption", "family": "topology",
     "summary": "A result assuming compactness / properness / completeness need not hold without it.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    # Additional named seed families: scope, geometry, coefficient, local/global.
    {"id": "generic_to_special", "family": "generality",
     "summary": "A property of the generic object need not hold for a special / specific one.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "projective_to_proper", "family": "geometry",
     "summary": "Projective and proper are not interchangeable: a proper object need not be projective.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "rational_to_integral_coefficients", "family": "coefficients",
     "summary": "A result with rational coefficients need not hold with integral coefficients.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
    {"id": "dense_open_not_whole_space", "family": "local_global",
     "summary": "A property on a dense open subset need not hold on the whole space.",
     "tier": "candidate", "action": "warn", "enforced_by": ""},
]

# Keyword triggers for the candidate (warn-only) textual patterns.
_CANDIDATE_TRIGGERS: dict[str, list[str]] = {
    "smooth_not_singular": ["smooth", "singular"],
    "alg_closed_not_arbitrary_field": ["algebraically closed"],
    "char_zero_not_positive_char": ["characteristic zero", "characteristic 0", "positive characteristic"],
    "finite_dim_not_infinite_dim": ["finite-dimensional", "finite dimensional", "infinite-dimensional"],
    "iso_noncanonical_not_equality": ["isomorphism", "isomorphic", "canonical"],
    "local_not_global": ["local", "global"],
    # General finite-vs-infinite swap (distinct from finite_dim_not_infinite_dim);
    # specific phrases to avoid firing on every legitimate "finite group" claim.
    "finite_not_infinite": [
        "infinite group", "infinite order", "infinitely many", "infinite set",
        "infinite cardinality", "infinite-dimensional",
    ],
    # Dropping a compactness / properness / completeness assumption.
    "compact_proper_complete_assumption": [
        "compact", "compactness", "properness", "proper morphism", "proper map",
        "complete variety", "completeness",
    ],
    "generic_to_special": ["generic", "special fiber", "special member", "specialization"],
    "projective_to_proper": ["projective", "proper but not projective", "proper scheme", "proper variety"],
    "rational_to_integral_coefficients": [
        "rational coefficients", "integral coefficients", "rational cohomology",
        "integral cohomology", "with q-coefficients", "with z-coefficients",
    ],
    "dense_open_not_whole_space": ["dense open", "zariski dense", "whole space", "dense subset"],
}


def seed_failure_pattern_bank() -> FailurePatternBank:
    """Return the validated canonical seed bank."""
    return FailurePatternBank.model_validate({"schema_version": 1, "patterns": _SEED})


def _bank_index() -> dict[str, dict]:
    return {p["id"]: p for p in _SEED}


def build_failure_pattern_audit(run_dir: Path) -> dict:
    """Read a run's validated artifacts and match them against the seed bank.

    Deterministic and read-only. Emits matches whose tier/action/enforced_by are
    copied verbatim from the canonical bank (so a run cannot relabel a pattern).
    """
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    claim = artifact("locked_claim")
    counterexamples = artifact("counterexamples")
    source_grounding = artifact("source_grounding")
    computations = artifact("computation_checks")

    bank = _bank_index()
    matches: list[dict] = []

    def add(pattern_id: str, subject: str, signal: str, explanation: str) -> None:
        pat = bank[pattern_id]
        matches.append(
            {
                "pattern_id": pattern_id,
                "family": pat["family"],
                "tier": pat["tier"],
                "action": pat["action"],
                "subject": subject,
                "signal": signal,
                "explanation": explanation,
                "enforced_by": pat["enforced_by"],
            }
        )

    # --- hard patterns: observe the structural condition the validator guards ---
    tests = counterexamples.get("tests", [])
    if any(t.get("satisfies_all_claim_hypotheses") is False for t in tests):
        add(
            "dropped_hypothesis_not_refutation",
            "counterexamples",
            "a counterexample drops a claim hypothesis",
            "A dropped-hypothesis counterexample cannot refute the claim; the kernel "
            "validator already prevents refutes_claim here.",
        )
    if any(t.get("refutes_step_only") for t in tests):
        add(
            "proof_step_attack_not_refutation",
            "counterexamples",
            "a counterexample attacks a proof step only",
            "A proof-step attack is not a claim refutation; enforced structurally.",
        )
    if any(c.get("status") in _UNCHECKED_COMPUTATION for c in computations.get("checks", [])):
        add(
            "unchecked_example_not_evidence",
            "computation_checks",
            "an example computation is unchecked (inconclusive/human_needed)",
            "Unchecked computations cannot support survived/failed evidence; enforced structurally.",
        )
    theorem_rel = optional.get("theorem_applicability")
    if theorem_rel:
        applicability = read_yaml(run_dir / theorem_rel)
        for usage in applicability.get("usages", []):
            scope = (usage.get("theorem_scope") or "").lower()
            claim_scope = (usage.get("claim_scope") or "").lower()
            if ("general" in scope or "generic" in scope) and claim_scope in {"all", "universal"}:
                add(
                    "very_general_to_all",
                    usage.get("proof_step_id", usage.get("source_ref_id", "usage")),
                    f"theorem_scope={scope!r} applied to claim_scope={claim_scope!r}",
                    "A 'very general' theorem applied to 'all' objects needs an explicit "
                    "scope justification; the theorem-applicability validator gates this.",
                )

    # --- audited patterns: ranking / time-allocation hints only -----------------
    any_checked = any(c.get("status") in _CHECKED_COMPUTATION for c in computations.get("checks", []))
    normalized = (claim.get("normalized_statement") or "").lower()
    universal = any(w in normalized for w in ("for all", "every", "all "))
    if any_checked and universal:
        add(
            "finite_check_not_universal",
            "computation_checks",
            "checked computation(s) present for a universal claim",
            "A finite machine check does not establish the universal claim; "
            "use only as ranking/time-allocation weight, not as proof.",
        )
    for step in source_grounding.get("steps", []):
        if step.get("status") == "source_backed":
            continue
        mm = (step.get("hypothesis_match") or {})
        if mm.get("matches") in (False, "unclear") and (mm.get("mismatches") or step.get("status") == "candidate"):
            add(
                "source_supports_different_claim",
                step.get("proof_step_id", "step"),
                "cited theorem hypotheses do not confirm the claim",
                "The cited source may support a different statement; treat as a "
                "ranking/time hint, not reference support.",
            )
            break  # one audited source signal is enough

    # --- candidate patterns: keyword warns over claim + source statements --------
    haystack_parts = [normalized]
    for ref in source_grounding.get("source_refs", []):
        haystack_parts.append((ref.get("statement_or_summary") or "").lower())
    haystack = " ".join(haystack_parts)
    for pattern_id, triggers in _CANDIDATE_TRIGGERS.items():
        if any(t in haystack for t in triggers):
            add(
                pattern_id,
                "claim/source text",
                f"keyword(s) present: {', '.join(t for t in triggers if t in haystack)}",
                bank[pattern_id]["summary"] + " Warning only; verify the scope explicitly.",
            )

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "matches": matches,
        "notes": [
            "Advisory pattern audit: a match never proves falsehood, never refutes "
            "the claim, and never upgrades proof/source/evidence status.",
            "candidate=warn only; audited=ranking/time only; hard=existing validator gate.",
            "Patterns cannot be promoted to hard at run time, by memory, or by human input.",
        ],
    }
    FailurePatternAudit.model_validate(report)
    return report


def failure_pattern_allocation_hints(audit: dict) -> dict:
    """Derive ADVISORY ranking / time-allocation hints from a failure-pattern
    audit. This is the audited-tier effect: an audited (``rank_downgrade``) match
    recommends downgrading the ranking of the route/step it touches and reallocating
    effort (repair / switch / ask-human). It is advisory weight only: it returns
    recommendations and NEVER mutates a score, grade, or proof/source/evidence
    status. Pure and read-only over the audit dict.
    """
    matches = audit.get("matches", [])
    rank_downgrade = [
        {
            "pattern_id": m.get("pattern_id"),
            "subject": m.get("subject"),
            "reason": m.get("explanation"),
        }
        for m in matches
        if m.get("action") == "rank_downgrade"
    ]
    time_allocation: list[str] = []
    if rank_downgrade:
        time_allocation.append(
            "Audited failure-pattern pressure: spend the next effort on repair "
            "(pin sources / re-scope the claim) before deeper proof search."
        )
    if len(rank_downgrade) >= 2:
        time_allocation.append(
            "Multiple audited patterns matched: consider switching route or asking "
            "a human before allocating more time."
        )
    return {
        "rank_downgrade": rank_downgrade,
        "time_allocation": time_allocation,
        "note": "Advisory only: these affect ranking/time-allocation guidance, "
        "never a score, grade, or proof/source/evidence status.",
    }


def persist_failure_pattern_audit(run_dir: Path, report: dict) -> dict:
    """Write a precomputed failure-pattern audit + register it as an optional
    artifact (compute/commit split: the write half of
    ``audit_failure_patterns``). ``report`` comes from ``build_failure_pattern_audit``.
    """
    run_dir = Path(run_dir)
    write_yaml(run_dir / "failure_pattern_audit.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["failure_pattern_audit"] = (
        "failure_pattern_audit.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report


def audit_failure_patterns(run_dir: Path) -> dict:
    """Compute + write failure_pattern_audit.yaml and register it as an optional
    artifact (the pure compute is ``build_failure_pattern_audit``)."""
    run_dir = Path(run_dir)
    report = build_failure_pattern_audit(run_dir)
    return persist_failure_pattern_audit(run_dir, report)
