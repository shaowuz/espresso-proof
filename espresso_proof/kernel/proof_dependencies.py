"""Which lines of a line-numbered proof still have unresolved dependencies.

This lived in ``orchestration/agent_consensus_seal.py``, and only there. The
seal helper called it before writing a consensus, so the sanctioned path was
safe — but ``_validate_agent_consensus_review`` calls itself "the authoritative
BACKSTOP that blocks a hand-forged consensus bypassing the seal helper", and it
only checked that a proof artifact existed. A hostile review reproduced the
consequence: a hand-written ``agent_consensus_review.yaml`` citing real line ids,
with the five ``bound_*`` hashes recomputed from the public unkeyed digest,
earned ``agent_consensus_mock_checked`` over a proof whose lines still carried
``gaps``.

A rule that decides a status belongs where the kernel can enforce it, not in the
component that happens to write the artifact. Anything that bypasses
orchestration bypasses orchestration's checks — that is the general shape of the
defect, and moving the rule here is the general fix.

Read-only and deterministic: it reads validated artifacts and returns line ids.
It grants nothing.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.models import ProofGapResolutionReport
from espresso_proof.kernel.pressure_status import artifact_revision_hash

def trusted_human_allowed_theorem_refs(
    run_dir: Path,
    manifest: dict,
    proof_artifact: str,
) -> set[tuple[str, str]]:
    report_rel = (manifest.get("optional_artifacts") or {}).get("proof_gap_resolution")
    if not report_rel:
        return set()
    try:
        report = ProofGapResolutionReport.model_validate(read_yaml(run_dir / report_rel))
    except Exception:  # noqa: BLE001 - malformed provenance must fail closed
        return set()
    if report.run_id != manifest.get("run_id"):
        return set()
    if report.proof_artifact != proof_artifact:
        return set()
    if report.line_numbered_proof_sha256 != artifact_revision_hash(run_dir, proof_artifact):
        return set()
    return {
        (candidate.line_id, candidate.proposed_ref)
        for candidate in report.candidates
        if candidate.action == "attach_human_allowed_theorem_ref"
        and candidate.status == "accepted_by_human"
        and not candidate.source_grounded
        and not candidate.machine_verified
        and candidate.proposed_ref
    }


def trusted_source_grounded_theorem_refs(run_dir: Path, manifest: dict) -> set[str]:
    artifacts = manifest.get("artifacts") or {}
    source_rel = artifacts.get("source_grounding")
    if not source_rel:
        return set()
    try:
        source_grounding = read_yaml(run_dir / source_rel)
    except Exception:  # noqa: BLE001 - malformed/unreadable support fails closed
        return set()
    refs = source_grounding.get("source_refs") or []
    return {
        str(ref.get("id") or "")
        for ref in refs
        if isinstance(ref, dict)
        and ref.get("status") == "source_backed"
        and ref.get("locator", {}).get("kind") not in {"", "unknown", None}
        and str(ref.get("title") or "").strip()
        and any(str(author).strip() for author in (ref.get("authors") or []))
    }


def is_resolved_dependency(
    dep: dict,
    *,
    line_id: str,
    trusted_human_allowed_theorem_refs: set[tuple[str, str]],
    trusted_source_grounded_theorem_refs: set[str],
) -> bool:
    dep_type = dep.get("type")
    status = dep.get("status")
    if dep_type == "unchecked_gap":
        return False
    if dep_type == "theorem_ref":
        ref = str(dep.get("ref") or "")
        if ref in trusted_source_grounded_theorem_refs:
            return (
                dep.get("source_grounded") is True
                and dep.get("authority") == "source_grounded_theorem_ref"
                and dep.get("machine_verified") is False
                and bool(str(dep.get("source_locator") or "").strip())
            )
        if (line_id, str(dep.get("ref") or "")) in trusted_human_allowed_theorem_refs:
            return (
                status == "accepted_by_human"
                and dep.get("authority") == "human_allowed_theorem_ref"
                and dep.get("machine_verified") is False
                and dep.get("source_grounded") is False
            )
        return False
    if dep_type == "human_assumption":
        return False
    return status == "resolved"


def unresolved_proof_dependency_lines(
    proof: dict,
    *,
    trusted_human_allowed_theorem_refs: set[tuple[str, str]] | None = None,
    trusted_source_grounded_theorem_refs: set[str] | None = None,
) -> list[str]:
    trusted_human_allowed_theorem_refs = trusted_human_allowed_theorem_refs or set()
    trusted_source_grounded_theorem_refs = trusted_source_grounded_theorem_refs or set()
    unresolved: list[str] = []
    for index, line in enumerate(proof.get("lines") or [], start=1):
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("line_id") or f"line_{index}")
        if line.get("gaps"):
            unresolved.append(line_id)
            continue
        for dep in line.get("dependencies") or []:
            if isinstance(dep, dict) and not is_resolved_dependency(
                dep,
                line_id=line_id,
                trusted_human_allowed_theorem_refs=trusted_human_allowed_theorem_refs,
                trusted_source_grounded_theorem_refs=trusted_source_grounded_theorem_refs,
            ):
                unresolved.append(line_id)
                break
    return unresolved
