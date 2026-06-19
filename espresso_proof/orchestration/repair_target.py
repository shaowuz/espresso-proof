"""Phase 5 — strategy-scoped proof-attempt repair targets (advisory).

The capability gap Phase 5 closes: when a continuation round (N+1) is appended,
the prover is told WHICH subgoal to attack but NOT the specific reviewer objections
from round N that named that subgoal. So a "repair" round could not actually use
the prior reviewer's objection to improve. This module extracts those scoped
objections and feeds them back as advisory prompt context, and records the pairing
in a sidecar for audit.

KERNEL SAFETY — pure ADVISORY display/diagnostic sink (mirrors advisory_proof_sketch):
- ``repair_target.yaml`` is NOT in ``models.MODEL_REGISTRY``, is NOT a run_manifest
  artifact, and ``validate_run`` never consumes it. It therefore cannot upgrade or
  carry any proof / source / evidence / grade / refuted / checked status.
- It is snapshot-EXCLUDED (``stage_runner._SNAPSHOT_EXCLUDED_NAMES``) so it survives
  a rolled-back round, exactly like ``advisory_proof_sketch.yaml``.
- Every objection text is scrubbed with the SAME kernel detectors as the advisory
  sketch (so the sink can never carry forbidden proof language or a status smuggle).
- It is stamped ``trusted: false`` / ``advisory_only: true`` and is fail-closed:
  any error returns ``None`` / ``[]`` and never breaks the run.

The proof-attempt itself stays advisory by the existing contract: ``make_prover_
attempt`` pins ``status="needs_human"`` and the round is re-validated by the kernel.
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.stage_runner import REPAIR_TARGET_FILENAME
from espresso_proof.orchestration.advisory_proof_sketch import _scrub_sketch_text

__all__ = [
    "REPAIR_TARGET_FILENAME",
    "collect_repair_target_objections",
    "capture_repair_target",
    "read_repair_target",
]

REPAIR_TARGET_NOTE = (
    "Advisory only. These are the prior reviewer objections scoped to the subgoal "
    "this round was asked to repair. Nothing here is checked, source-backed, "
    "refuted, or proved; it is prompt context and never upgrades any status."
)


def _latest_round_report(run_dir: Path) -> dict | None:
    """Read the most recent prover-loop round report (the parent of the round being
    appended). Fail-closed -> None."""
    rounds_dir = Path(run_dir) / "prover_rounds"
    if not rounds_dir.is_dir():
        return None
    latest: dict | None = None
    latest_idx = -1
    for f in rounds_dir.glob("round_*.yaml"):
        data = read_yaml(f)
        if not isinstance(data, dict):
            continue
        idx = data.get("round_index")
        if isinstance(idx, int) and idx > latest_idx:
            latest_idx, latest = idx, data
    return latest


def collect_repair_target_objections(
    run_dir: Path, target_subgoal_id: str | None
) -> list[dict]:
    """Return the latest round's scoped objections that named ``target_subgoal_id``,
    as advisory (scrubbed) repair targets. Empty list if there is no target, no
    prior round, or nothing matched. Pure / fail-closed (never raises)."""
    if not target_subgoal_id:
        return []
    try:
        latest = _latest_round_report(run_dir)
        if not isinstance(latest, dict):
            return []
        targets: list[dict] = []
        for review in latest.get("verifier_reviews", []) or []:
            if not isinstance(review, dict):
                continue
            reviewer_id = review.get("verifier_id")
            for obj in review.get("scoped_objections", []) or []:
                if not isinstance(obj, dict):
                    continue
                if obj.get("subgoal_id") != target_subgoal_id:
                    continue
                text = _scrub_sketch_text(obj.get("text", ""))
                action = _scrub_sketch_text(obj.get("suggested_next_action", ""))
                if not text and not action:
                    continue
                # The round artifact is validated on write (ScopedObjection enums),
                # but this reads raw YAML, so apply the SAME scrub safety belt to
                # EVERY field that flows into the prover prompt -- not just the free
                # text -- so a tampered/legacy round can never inject proof-language
                # via objection_id / reviewer_id / kind / severity. subgoal_id comes
                # from the trusted target argument, never the (untrusted) objection.
                targets.append(
                    {
                        "objection_id": _scrub_sketch_text(obj.get("id", "")),
                        "reviewer_id": _scrub_sketch_text(reviewer_id or ""),
                        "subgoal_id": target_subgoal_id,
                        "kind": _scrub_sketch_text(obj.get("kind", "")),
                        "severity": _scrub_sketch_text(obj.get("severity", "")),
                        "text": text,
                        "suggested_next_action": action,
                    }
                )
        return targets
    except Exception:  # noqa: BLE001 - advisory extraction must never break a run
        return []


def capture_repair_target(
    run_dir: Path,
    *,
    round_index: int,
    strategy_id: str | None,
    target_subgoal_id: str | None,
    objections: list[dict],
) -> Path | None:
    """Record the advisory repair-target pairing for a continuation round into the
    snapshot-excluded sidecar. Returns the path written, or None if there is
    nothing to record / on any error (fail-closed -- never raises)."""
    try:
        if not objections:
            return None
        record = {
            "kind": "repair_target",
            "trusted": False,
            "advisory_only": True,
            "scrubbed": True,
            "schema_version": 1,
            "note": REPAIR_TARGET_NOTE,
            "round_index": round_index,
            "strategy_id": strategy_id,
            "target_subgoal_id": target_subgoal_id,
            "objections": objections,
        }
        path = Path(run_dir) / REPAIR_TARGET_FILENAME
        write_yaml(path, record)
        return path
    except Exception:  # noqa: BLE001 - diagnostic capture must never break a run
        return None


def read_repair_target(run_dir: Path) -> dict | None:
    """Read the repair-target sidecar if present, else None (fail-closed)."""
    try:
        path = Path(run_dir) / REPAIR_TARGET_FILENAME
        if not path.exists():
            return None
        data = read_yaml(path)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
