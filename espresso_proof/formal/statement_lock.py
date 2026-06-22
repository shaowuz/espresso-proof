"""Statement-lock helpers for optional formal-verifier bridges.

A formal verifier result is only meaningful for the statement it checked. This
module records a conservative lock between Espresso's locked informal claim and
a proposed formal statement. It is advisory until a kernel validator chooses to
consume it, but it gives the Lean bridge a fail-closed equivalence gate today.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.pressure_status import artifact_revision_hash

STATEMENT_LOCK_FILENAME = "statement_lock.yaml"


def normalize_statement_text(text: object) -> str:
    """Stable whitespace/case normalization for exact-lock comparisons."""

    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def statement_sha256(text: object) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def build_statement_lock(
    run_dir: Path,
    *,
    formal_statement: str,
    equivalence_evidence: str = "",
    explicit_equivalence_confirmed: bool = False,
    lock_id: str = "statement_lock",
) -> dict[str, Any]:
    """Build a conservative lock between the current claim and a formal statement.

    ``locked_equivalent`` is deliberately hard to obtain: a caller must provide an
    explicit confirmation flag AND the normalized formal statement must equal the
    locked claim's normalized statement. Anything else is not accepted as a
    formal-status bridge.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_rel = manifest["artifacts"]["locked_claim"]
    claim = read_yaml(run_dir / claim_rel)
    informal = str(claim.get("normalized_statement") or "")
    norm_informal = normalize_statement_text(informal)
    norm_formal = normalize_statement_text(formal_statement)
    if explicit_equivalence_confirmed and norm_formal == norm_informal:
        status = "locked_equivalent"
    elif explicit_equivalence_confirmed:
        status = "statement_drift"
    else:
        status = "needs_human_equivalence_review"
    return {
        "schema_version": 1,
        "lock_id": lock_id,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "informal_statement": informal,
        "formal_statement": formal_statement,
        "normalized_informal": norm_informal,
        "normalized_formal": norm_formal,
        "bound_claim_hash": artifact_revision_hash(run_dir, claim_rel),
        "formal_statement_sha256": statement_sha256(formal_statement),
        "equivalence_status": status,
        "explicit_equivalence_confirmed": bool(explicit_equivalence_confirmed),
        "equivalence_evidence": equivalence_evidence,
        "notes": [
            "Statement lock is an equivalence gate for optional formal checks.",
            "It is not proof evidence and does not earn formal status by itself.",
        ],
    }


def validate_statement_lock(run_dir: Path, statement_lock: dict[str, Any]) -> tuple[bool, str]:
    """Validate that a statement lock is fresh and accepted for this run."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_rel = manifest["artifacts"]["locked_claim"]
    claim = read_yaml(run_dir / claim_rel)
    if statement_lock.get("claim_id") != claim.get("id"):
        return False, "statement lock claim_id does not match locked claim"
    if statement_lock.get("run_id") != manifest.get("run_id"):
        return False, "statement lock run_id does not match run manifest"
    if statement_lock.get("bound_claim_hash") != artifact_revision_hash(run_dir, claim_rel):
        return False, "statement lock is stale: locked claim changed"
    formal_statement = str(statement_lock.get("formal_statement") or "")
    if statement_lock.get("formal_statement_sha256") != statement_sha256(formal_statement):
        return False, "statement lock formal statement hash is stale"
    if statement_lock.get("normalized_informal") != normalize_statement_text(
        claim.get("normalized_statement")
    ):
        return False, "statement lock normalized informal statement is stale"
    if statement_lock.get("normalized_formal") != normalize_statement_text(formal_statement):
        return False, "statement lock normalized formal statement is stale"
    if statement_lock.get("equivalence_status") != "locked_equivalent":
        return False, "statement equivalence is not locked"
    if statement_lock.get("explicit_equivalence_confirmed") is not True:
        return False, "statement equivalence was not explicitly confirmed"
    return True, "statement lock accepted"


def formal_status_allowed(run_dir: Path, statement_lock: dict[str, Any]) -> bool:
    ok, _ = validate_statement_lock(run_dir, statement_lock)
    return ok


def write_statement_lock(run_dir: Path, statement_lock: dict[str, Any]) -> Path:
    """Write the lock as an optional advisory artifact."""

    run_dir = Path(run_dir)
    path = run_dir / STATEMENT_LOCK_FILENAME
    write_yaml(path, statement_lock)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["statement_lock"] = (
        STATEMENT_LOCK_FILENAME
    )
    write_yaml(manifest_path, manifest)
    return path
