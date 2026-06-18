"""Claim-clear scheduling gate for Espresso Workflow V2.

This module owns only a runtime scheduling predicate. It does not certify proof,
source support, checked evidence, theorem applicability, examples,
counterexamples, grade, final-report, or memory status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from espresso_proof.claim.claim_clarity import is_blocking_missing_item
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.observability import now_iso

WAITING_CLAIM_CLARIFICATION = "WAITING_CLAIM_CLARIFICATION"
CLAIM_CLEAR = "CLAIM_CLEAR"
CLAIM_CLEAR_GATE_FILENAME = "claim_clear_gate.yaml"

CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES = frozenset(
    {
        "literature_review",
        "examples",
        "counterexamples",
        "source_theorem",
        "strategy",
        "attacker",
        "grade_memory_memo",
    }
)

CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS = frozenset(
    {"literature", "examples", "counterexamples"}
)


class ClaimClearBlockedError(RuntimeError):
    """Downstream scheduling was blocked by the V2 claim-clear gate."""


@dataclass(frozen=True)
class ClaimClearResult:
    clear: bool
    claim_id: str | None
    locked_claim_sha256: str | None
    hypothesis_ledger_sha256: str | None
    reasons: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        return CLAIM_CLEAR if self.clear else WAITING_CLAIM_CLARIFICATION


def _artifact_path(run_dir: Path, manifest: dict[str, Any], artifact_key: str) -> Path:
    rel = manifest.get("artifacts", {}).get(artifact_key)
    if not rel:
        raise ValueError(f"missing required artifact path for {artifact_key}")
    return run_dir / rel


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _blocking_items(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return ["hypothesis_ledger.missing_or_unclear is not a list"]
    # Goal 67A: default-deny — every non-empty entry blocks UNLESS it is a
    # conservative advisory clarification (and never if it names a core gap).
    return [str(item).strip() for item in raw if is_blocking_missing_item(str(item))]


def _existing_gate(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    rel = manifest.get("optional_artifacts", {}).get("claim_clear_gate")
    if not rel:
        return None
    path = run_dir / rel
    if not path.exists():
        return None
    data = read_yaml(path)
    return data if isinstance(data, dict) else None


def evaluate_claim_clear(
    run_dir: Path,
    *,
    require_recorded_clear_gate: bool = False,
) -> ClaimClearResult:
    """Evaluate whether the current run is allowed to schedule downstream work.

    ``require_recorded_clear_gate`` is used when resuming a run that already
    passed downstream stages. If the claim/ledger hashes have changed since the
    recorded clear gate, downstream reuse is blocked until the claim-clear gate
    is re-established.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = _artifact_path(run_dir, manifest, "locked_claim")
    ledger_path = _artifact_path(run_dir, manifest, "hypothesis_ledger")
    claim = read_yaml(claim_path)
    ledger = read_yaml(ledger_path)
    claim_hash = _sha256(claim_path)
    ledger_hash = _sha256(ledger_path)

    reasons: list[str] = []
    claim_id = claim.get("id") if isinstance(claim, dict) else None
    ledger_claim_id = ledger.get("claim_id") if isinstance(ledger, dict) else None

    if claim_id != ledger_claim_id:
        reasons.append("locked_claim.id does not match hypothesis_ledger.claim_id")
    if claim.get("locked") is not True:
        reasons.append("locked_claim.locked is not true")
    if ledger.get("claim_precision") != "precise":
        reasons.append("hypothesis_ledger.claim_precision is not precise")
    blockers = _blocking_items(ledger.get("missing_or_unclear", []))
    if blockers:
        reasons.append("hypothesis_ledger.missing_or_unclear has unresolved items")

    if require_recorded_clear_gate:
        gate = _existing_gate(run_dir, manifest)
        if gate:
            if gate.get("state") != CLAIM_CLEAR:
                reasons.append("recorded claim-clear gate is not clear")
            else:
                if gate.get("locked_claim_sha256") != claim_hash:
                    reasons.append("locked_claim changed after claim-clear gate")
                if gate.get("hypothesis_ledger_sha256") != ledger_hash:
                    reasons.append("hypothesis_ledger changed after claim-clear gate")

    return ClaimClearResult(
        clear=not reasons,
        claim_id=str(claim_id) if claim_id is not None else None,
        locked_claim_sha256=claim_hash,
        hypothesis_ledger_sha256=ledger_hash,
        reasons=reasons,
    )


def write_claim_clear_gate(run_dir: Path, result: ClaimClearResult | None = None) -> dict[str, Any]:
    """Persist the scheduling gate audit sidecar and register it as optional.

    The sidecar is additive and status-free: it records only the current claim
    inputs and whether downstream scheduling is allowed.
    """

    run_dir = Path(run_dir)
    result = result or evaluate_claim_clear(run_dir)
    report = {
        "schema_version": 1,
        "claim_id": result.claim_id,
        "generated_at": now_iso(),
        "state": result.state,
        "clear": result.clear,
        "blocking_reasons": list(result.reasons),
        "locked_claim_sha256": result.locked_claim_sha256,
        "hypothesis_ledger_sha256": result.hypothesis_ledger_sha256,
        "effect": "scheduling_gate_only",
        "mathematical_status_authority": "none",
        "notes": [
            "Claim-clear is a scheduling gate only.",
            "It never upgrades proof, source, evidence, theorem, grade, or memory status.",
        ],
    }
    write_yaml(run_dir / CLAIM_CLEAR_GATE_FILENAME, report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["claim_clear_gate"] = CLAIM_CLEAR_GATE_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


def record_claim_clear_block(
    run_dir: Path,
    result: ClaimClearResult | None = None,
) -> dict[str, Any]:
    """Persist a blocked gate and make downstream process state non-evidence.

    This is still only a scheduling/status-board update. It does not edit the
    mathematical artifacts that own proof, source, evidence, theorem, grade,
    final-report, or memory status.
    """

    run_dir = Path(run_dir)
    result = result or evaluate_claim_clear(run_dir)
    report = write_claim_clear_gate(run_dir, result)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["current_state"] = WAITING_CLAIM_CLARIFICATION
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    from espresso_proof.kernel.agent_run_status import init_agent_run_status
    from espresso_proof.kernel.pressure_status import build_pressure_status
    from espresso_proof.kernel.validators import validate_run

    init_agent_run_status(
        run_dir,
        done={"claim_understanding"},
        skipped=set(CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS),
        notes=[
            "Claim clarity blocked downstream scheduling.",
            "Skipped evidence agents contribute no evidence or status.",
        ],
    )
    build_pressure_status(run_dir, late_after_strategy=False)
    validate_run(run_dir)
    return report


def ensure_claim_clear_for_downstream(
    run_dir: Path,
    *,
    require_recorded_clear_gate: bool = False,
) -> ClaimClearResult:
    """Require claim clarity before starting or reusing downstream work.

    If blocked, this records the sidecar, updates the effective lifecycle state,
    marks downstream evidence agents skipped/non-evidence, and raises before any
    caller can start a model/tool call or write downstream artifacts.
    """

    result = evaluate_claim_clear(
        run_dir,
        require_recorded_clear_gate=require_recorded_clear_gate,
    )
    if not result.clear:
        record_claim_clear_block(run_dir, result)
        reason = "; ".join(result.reasons) or "claim is not clear"
        raise ClaimClearBlockedError(f"blocked by claim-clear gate: {reason}")
    manifest = read_yaml(Path(run_dir) / "run_manifest.yaml")
    gate = _existing_gate(Path(run_dir), manifest)
    gate_matches = (
        gate
        and gate.get("state") == CLAIM_CLEAR
        and gate.get("locked_claim_sha256") == result.locked_claim_sha256
        and gate.get("hypothesis_ledger_sha256") == result.hypothesis_ledger_sha256
    )
    if not gate_matches:
        write_claim_clear_gate(run_dir, result)
    return result


def claim_clear_for_downstream(run_dir: Path, *, stage: str) -> ClaimClearResult:
    if stage not in CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES:
        return ClaimClearResult(True, None, None, None, [])
    return evaluate_claim_clear(run_dir)


__all__ = [
    "CLAIM_CLEAR",
    "CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES",
    "CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS",
    "CLAIM_CLEAR_GATE_FILENAME",
    "WAITING_CLAIM_CLARIFICATION",
    "ClaimClearBlockedError",
    "ClaimClearResult",
    "claim_clear_for_downstream",
    "ensure_claim_clear_for_downstream",
    "evaluate_claim_clear",
    "record_claim_clear_block",
    "write_claim_clear_gate",
]
