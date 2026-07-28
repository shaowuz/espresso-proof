"""Blocker-provenance classification for the prover loop.

A small, self-contained leaf classifier consumed by
``orchestration.prover_loop_driver`` (its old homes — the dogfood loop harness
and the loop replay gate — were deleted; the module moved here from the
now-removed ``loop/`` package). ``provenance_is_human_gated`` remains the
single predicate for "this blocker can never be a bounded loop-autofix
target".
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml


def _read_optional_artifact(run_dir: Path, name: str):
    """Fail-soft read of an artifact registered under the manifest's ``artifacts``
    or ``optional_artifacts`` map. A small self-contained copy of the loop-harness
    helper so this module stays a leaf (no reverse dependency on campaign harnesses)."""
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    except Exception:  # noqa: BLE001
        return None
    rel = manifest.get("artifacts", {}).get(name) or manifest.get(
        "optional_artifacts", {}
    ).get(name)
    if not rel or not (run_dir / rel).exists():
        return None
    try:
        return read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001
        return None


def blocker_provenance(run_dir: Path) -> dict[str, set[str]]:
    """Map each reviewer-emitted text to its recorded provenance kinds
    ({"source_request", "missing_hypothesis", "objection"}), read from the
    run's persisted verifier reviews (llm_agent_run.yaml and every
    prover_rounds/round_*.yaml). Texts the artifacts do not contain map to
    an empty set (legacy keyword classification applies)."""

    provenance: dict[str, set[str]] = {}
    review_sources: list[dict] = []
    agent_run = _read_optional_artifact(run_dir, "llm_agent_run")
    if isinstance(agent_run, dict):
        review_sources.append(agent_run)
    rounds_dir = Path(run_dir) / "prover_rounds"
    if rounds_dir.is_dir():
        for round_path in sorted(rounds_dir.glob("round_*.yaml")):
            try:
                doc = read_yaml(round_path)
            except Exception:  # noqa: BLE001 - unreadable round: skip, fail open to legacy rules
                continue
            if isinstance(doc, dict):
                review_sources.append(doc)
    for doc in review_sources:
        reviews = doc.get("verifier_reviews")
        if not isinstance(reviews, list):
            continue
        for review in reviews:
            if not isinstance(review, dict):
                continue
            for kind, field in (
                ("source_request", "source_requests"),
                ("missing_hypothesis", "missing_hypotheses"),
                ("objection", "objections"),
            ):
                items = review.get(field)
                if not isinstance(items, list):
                    continue
                for item in items:
                    text = str(item).strip()
                    if text:
                        provenance.setdefault(text, set()).add(kind)
    return provenance


def provenance_is_human_gated(kinds: set[str]) -> bool:
    """Single source of truth for "this blocker can never be a bounded
    loop-autofix target": it has recorded provenance and did not originate
    as a reviewer source request. Shared by the classifier and the replay
    probe so the two predicates cannot drift apart."""

    return bool(kinds) and "source_request" not in kinds


def classify_blocker(text: str, *, provenance: set[str] | None = None) -> str:
    """Deterministic blocking_evidence_type for a prover blocker text.

    Provenance decides first. A blocker that originated
    ONLY as a reviewer OBJECTION (a standing risk like "a sketch-only
    argument cannot become proof-complete evidence") or as a MISSING
    HYPOTHESIS (claim scoping / hypothesis work) is human-gated by nature —
    no loop autofix can resolve it, so offering it as a bounded-fix target
    every cycle was dishonest triage input. Reviewer SOURCE REQUESTS (and
    texts with no recorded provenance, e.g. legacy runs) classify by the
    keyword rules."""

    kinds = provenance or set()
    if provenance_is_human_gated(kinds):
        # Objections and missing hypotheses are never bounded loop-autofix
        # targets: code cannot fix an argument's mathematics, and replay
        # could not distinguish repair from suppression. Label precisely
        # (Math Workflow review): a missing hypothesis is claim-scoping
        # judgment; a pure objection keeps the truthful reviewer_objection
        # label. Both are human-gated downstream.
        if "missing_hypothesis" in kinds:
            return "human_judgment_needed"
        return "reviewer_objection"
    lowered = str(text).lower()
    # Theorem/locator REQUESTS are source gaps — they
    # need a verified citation, not a proof step. The theorem keyword
    # is phrase-scoped (review nit): "the theorem's step 3 is invalid"
    # is a proof problem, not a citation request.
    if any(
        k in lowered
        for k in (
            "source", "citation", "reference", "locator",
            "exact theorem", "theorem supporting", "cited theorem",
            "theorem statement",
        )
    ):
        return "source_gap"
    if "definition" in lowered:
        return "missing_definition"
    if "comput" in lowered or "check" in lowered:
        return "missing_computation"
    if "human" in lowered:
        return "human_judgment_needed"
    return "failed_proof_step"


__all__ = ["blocker_provenance", "provenance_is_human_gated", "classify_blocker"]
