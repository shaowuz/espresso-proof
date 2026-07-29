"""Normalize an ALREADY-PERSISTED claim + hypothesis ledger before reuse.

Brings old / externally-created / denormalized persisted runs up to the same
MEANING-PRESERVING hypothesis shape a freshly-generated run gets, WITHOUT weakening
or silently strengthening the claim-clear gate.

Scope: apply ONLY
``normalize_ambient_object_hypotheses`` — the idempotent, meaning-preserving
group-axiom consolidation (e.g. four separate axiom hypotheses, or a single
"G is a group (closure, associativity, identity, inverses)", collapse to
"G is a group"). It deliberately does NOT run ``normalize_simple_claim`` on
persisted runs: that helper can flip ``claim_precision`` imprecise->precise and so
UNBLOCK the claim-clear gate, which on resume would silently re-open a claim an
agent had marked unclear. This helper is therefore provably claim-clear NEUTRAL: it
never touches ``locked`` / ``claim_precision`` / ``missing_or_unclear``.

Hard contract:
- Never writes proof / source / evidence / grade / checked / refuted status; never
  sets ``human_confirmed``.
- Preserves ``raw_text``, ``ambiguous_terms``, ``repaired_from``, hypothesis
  ``source``, ``missing_or_unclear``, qualifiers, and negative cases.
- Idempotent: a second call changes nothing and returns ``False``.
- Writes are wrapped in a ``StageRunner`` transaction (``state_after=None``) so a
  validation failure rolls back atomically and never advances the lifecycle state.

The caller is expected to hold the run writer lock (e.g. ``resume_run``); standalone
callers / tests run single-threaded.
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.claim.claim_clear import (
    CLAIM_CLEAR_GATE_FILENAME,
    evaluate_claim_clear,
    write_claim_clear_gate,
)
from espresso_proof.claim.hypothesis_normalization import (
    normalize_ambient_object_hypotheses,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.stage_runner import StageRunner
from espresso_proof.kernel.validators import validate_run

_LEDGER_NOTE = (
    "hypotheses consolidated by persisted-claim normalization "
    "(meaning-preserving; claim-clear-neutral; no proof/source/evidence status implied)"
)


def normalize_persisted_claim_ledger(run_dir: Path) -> bool:
    """Normalize the persisted ``locked_claim`` + ``hypothesis_ledger`` in place.

    Returns ``True`` if anything changed, ``False`` if it was already normalized
    (a true no-op: no writes, no events).
    """
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    claim_rel = artifacts["locked_claim"]
    ledger_rel = artifacts["hypothesis_ledger"]
    claim = read_yaml(run_dir / claim_rel)
    ledger = read_yaml(run_dir / ledger_rel)

    new_claim_hyps = normalize_ambient_object_hypotheses(claim.get("hypotheses"))
    new_ledger_hyps = normalize_ambient_object_hypotheses(ledger.get("hypotheses"))

    claim_changed = new_claim_hyps != claim.get("hypotheses")
    ledger_changed = new_ledger_hyps != ledger.get("hypotheses")
    if not claim_changed and not ledger_changed:
        return False  # idempotent no-op

    gate_existed = (run_dir / CLAIM_CLEAR_GATE_FILENAME).exists()

    def _mutate() -> None:
        if claim_changed:
            claim["hypotheses"] = new_claim_hyps
            write_yaml(run_dir / claim_rel, claim)
        if ledger_changed:
            ledger["hypotheses"] = new_ledger_hyps
            notes = list(ledger.get("notes") or [])
            if _LEDGER_NOTE not in notes:
                notes.append(_LEDGER_NOTE)
            ledger["notes"] = notes
            write_yaml(run_dir / ledger_rel, ledger)
        # The consolidation is claim-clear NEUTRAL (the verdict is unchanged), but
        # the artifact hashes changed; if a gate was recorded, refresh it through
        # the gate path so a later ``require_recorded_clear_gate`` resume does not
        # false-trip on the stale hash. Never set claim-clear directly.
        if gate_existed:
            write_claim_clear_gate(run_dir, evaluate_claim_clear(run_dir))

    snapshot_paths = [claim_rel, ledger_rel]
    if gate_existed:
        snapshot_paths.append(CLAIM_CLEAR_GATE_FILENAME)

    StageRunner(run_dir).run(
        name="persisted_claim_normalization",
        mutate=_mutate,
        validate=lambda: validate_run(run_dir),
        state_after=None,
        snapshot_paths=snapshot_paths,
    )
    return True


__all__ = ["normalize_persisted_claim_ledger"]
