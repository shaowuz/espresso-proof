"""Goal 69: agent-consensus runner + seal action over a LINE-NUMBERED PROOF.

The three proof reviewers (line_proof / theorem_lemma_applicability / adversarial_gap)
review the structured ``line_numbered_proof.yaml`` ADVERSARIALLY -- NOT a route /
proposed_steps list. If all three return ``no_fatal_flaw_found`` (each citing the
proof line_id(s) it examined), the run is SEALED with ``proof_language_status =
agent_consensus_mock_checked`` -- an amber/advisory kernel status (NOT machine-
verified) gated by ``validators._validate_agent_consensus_review``.

Trust posture:
- The mock check may ONLY seal a line_numbered_proof; if it is missing / route-only,
  the seal FAILS CLOSED (a route is not a proof).
- This is the ONLY producer of ``agent_consensus_mock_checked``; nothing else sets it.
- It mutates ``idea_grade`` only this one field, only on unanimous consensus; it
  computes the ``bound_*`` hashes (incl. the line-numbered proof) EXACTLY as the
  kernel re-checks them, so editing the proof after review revokes the seal.
- It is TRANSACTIONAL: snapshots idea_grade / manifest / consensus sidecar / memo and
  ROLLS BACK + re-raises if ``validate_run`` rejects the result.
- An unparseable / missing reviewer verdict fails safe to ``unclear`` (blocks).
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.llm_agents import (
    agent_prompt,
    build_client,
    load_agent_context,
    proof_consensus_system_prompt,
)
from espresso_proof.kernel.io import read_yaml, write_text, write_yaml
from espresso_proof.kernel.models import (
    PROOF_CONSENSUS_REVIEWERS,
    AgentConsensusReview,
    AgentConsensusVerdict,
    IdeaGrade,
    LineNumberedProof,
    ProofLanguageStatus,
)
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.validators import validate_run

AGENT_CONSENSUS_FILENAME = "agent_consensus_review.yaml"
CONSENSUS_DISCLAIMER = (
    "Agent consensus only: three reviewers adversarially found no fatal flaw. This is "
    "NOT machine-verified and NOT a checked/source-backed/proved status -- it is an "
    "advisory mock check and may be wrong; residual gaps below remain open."
)
_VERDICT_VALUES = {v.value for v in AgentConsensusVerdict}

__all__ = [
    "AGENT_CONSENSUS_FILENAME",
    "run_proof_reviewers",
    "build_agent_consensus_review",
    "seal_agent_consensus",
]


class NoLineNumberedProofError(RuntimeError):
    """Raised when a seal is attempted with no line_numbered_proof (route-only)."""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically (temp + os.replace), so a restore can never leave a
    file with partial content even on an I/O failure mid-write."""
    tmp = path.with_name(path.name + ".restore.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


_MOCK_CHECK_DISCLOSURE_HEAD = (
    "Agent-consensus mock check: three agents ran an adversarial pass and surfaced "
    "no argument-breaking flaw."
)


def _patch_sealed_memo(existing: str, *, status: str, sealed: bool, residual_count: int) -> str:
    """Minimally patch an existing (already scan-clean) memo to reflect the seal:
    swap the Proof state line and (re)write the mock-check disclosure. Idempotent --
    a prior disclosure block is stripped first. Never full-rebuilds (which could pull
    in forbidden proof language from other artifacts)."""
    from espresso_proof.ui.descriptions import describe_proof_language

    skip_prefixes = (
        _MOCK_CHECK_DISCLOSURE_HEAD,
        "Agent consensus only, not a machine check",
    )
    out: list[str] = []
    for line in existing.splitlines():
        if line.startswith("Proof state:"):
            out.append(f"Proof state: {describe_proof_language(status)}")
            continue
        stripped = line.strip()
        if stripped.startswith(skip_prefixes) or "residual (non-fatal) gap(s) remain" in stripped:
            continue  # strip a prior mock-check block (idempotency)
        out.append(line)
    text = "\n".join(out).rstrip()
    if sealed:
        text += (
            f"\n\n{_MOCK_CHECK_DISCLOSURE_HEAD}"
            "\n  Agent consensus only, not a machine check -- advisory; it is not a "
            "checked or source-backed status."
            f"\n  {residual_count} residual (non-fatal) gap(s) remain; "
            "see agent_consensus_review.yaml."
        )
    return text + "\n"


def _normalize_entry(raw: object, reviewer_id: str) -> dict:
    """Normalize one reviewer's raw output into an AgentConsensusEntry dict.
    Fail-safe: an unparseable/missing verdict becomes ``unclear`` (which blocks)."""
    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict", "")).strip()
    if verdict not in _VERDICT_VALUES:
        verdict = AgentConsensusVerdict.unclear.value
    residual = [str(x) for x in (raw.get("residual_objections") or []) if str(x).strip()]
    cited = [str(x).strip() for x in (raw.get("cited_line_ids") or []) if str(x).strip()]
    return {
        "reviewer_id": reviewer_id,
        "verdict": verdict,
        "looked_for": str(raw.get("looked_for", "") or "")[:4000],
        "finding": str(raw.get("finding", "") or "")[:4000],
        "cited_line_ids": cited,
        "residual_objections": residual,
    }


def _require_line_numbered_proof(run_dir: Path, manifest: dict) -> dict:
    """Load the line_numbered_proof the reviewers must review. Fail closed (route-only
    / missing -> NoLineNumberedProofError)."""
    proof_rel = manifest.get("optional_artifacts", {}).get("line_numbered_proof")
    if not proof_rel or not (run_dir / proof_rel).exists():
        raise NoLineNumberedProofError(
            "cannot seal: no line_numbered_proof.yaml (a route / proposed_steps is not "
            "a sealable proof -- run the proof writer first)"
        )
    proof = read_yaml(run_dir / proof_rel)
    if not isinstance(proof, dict) or not proof.get("lines"):
        raise NoLineNumberedProofError("cannot seal: line_numbered_proof is empty/route-only")
    try:
        return LineNumberedProof.model_validate(proof).model_dump(mode="json")
    except Exception as exc:
        raise NoLineNumberedProofError(
            f"cannot seal: line_numbered_proof.yaml is malformed ({exc})"
        ) from exc


def _proof_review_context(run_dir: Path, proof: dict) -> dict:
    """The agent context + the LINE-NUMBERED PROOF the reviewers must review (Goal 69:
    reviewers review the proof, not route text)."""
    return {**load_agent_context(run_dir), "line_numbered_proof": proof}


def run_proof_reviewers(run_dir: Path, *, client, proof: dict | None = None) -> list[dict]:
    """Call the three proof reviewers adversarially over the LINE-NUMBERED PROOF."""
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    if proof is None:
        proof = _require_line_numbered_proof(run_dir, manifest)
    context = _proof_review_context(run_dir, proof)
    entries: list[dict] = []
    for reviewer_id in PROOF_CONSENSUS_REVIEWERS:
        raw = client.complete_json(
            system=proof_consensus_system_prompt(reviewer_id),
            prompt=agent_prompt(context, role=f"proof_review_{reviewer_id}"),
        )
        entries.append(_normalize_entry(raw, reviewer_id))
    return entries


def _bound_hashes(run_dir: Path, manifest: dict) -> dict:
    artifacts = manifest["artifacts"]
    optional = manifest.get("optional_artifacts", {})
    prover_loop_rel = optional.get("prover_loop")
    proof_rel = optional.get("line_numbered_proof")
    return {
        "bound_claim_hash": artifact_revision_hash(run_dir, artifacts["locked_claim"]),
        "bound_line_numbered_proof_hash": (
            artifact_revision_hash(run_dir, proof_rel) if proof_rel else ""
        ),
        "bound_source_grounding_hash": artifact_revision_hash(run_dir, artifacts["source_grounding"]),
        "bound_review_hash": artifact_revision_hash(run_dir, artifacts["review"]),
        "bound_prover_loop_hash": (
            artifact_revision_hash(run_dir, prover_loop_rel) if prover_loop_rel else ""
        ),
    }


def build_agent_consensus_review(
    run_dir: Path, entries: list[dict], bound_hashes: dict | None = None
) -> dict:
    """Build + validate + write agent_consensus_review.yaml and register it (mirrors
    build_ai_review). ``consensus`` is DERIVED here from the entries (unanimous
    no-fatal-flaw); the model validator re-checks the consistency."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    consensus = bool(entries) and all(
        e["verdict"] == AgentConsensusVerdict.no_fatal_flaw_found.value for e in entries
    )
    record = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        # Bind the hashes the reviewers actually reviewed against (passed in by the
        # seal at review time) so a mid-seal proof swap is caught by the gate's
        # recompute; fall back to the current files for direct callers.
        **(bound_hashes or _bound_hashes(run_dir, manifest)),
        "entries": entries,
        "consensus": consensus,
        "disclaimer": CONSENSUS_DISCLAIMER,
        "notes": [],
    }
    path = run_dir / AGENT_CONSENSUS_FILENAME
    if path.exists():  # idempotency: keep generated_at if nothing else changed
        prior = read_yaml(path)
        if isinstance(prior, dict):
            a, b = dict(record), dict(prior)
            a.pop("generated_at", None)
            b.pop("generated_at", None)
            if a == b:
                record["generated_at"] = prior.get("generated_at", record["generated_at"])
    AgentConsensusReview.model_validate(record)  # fail-closed BEFORE writing
    write_yaml(path, record)
    manifest.setdefault("optional_artifacts", {})["agent_consensus_review"] = AGENT_CONSENSUS_FILENAME
    write_yaml(manifest_path, manifest)
    return record


def seal_agent_consensus(
    run_dir: Path,
    *,
    provider: str = "stub",
    model: str = "stub-espresso-agent",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Run the three proof reviewers over the line_numbered_proof and, if unanimous,
    seal the run with agent_consensus_mock_checked. Fails CLOSED if there is no
    line-numbered proof (route-only). Transactional: rolls back on any validation
    failure. Returns the consensus record."""
    run_dir = Path(run_dir)
    client = client or build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    # Goal 69: the mock check may ONLY seal a line-numbered proof (fail closed).
    proof = _require_line_numbered_proof(run_dir, manifest)
    unresolved_gap_lines = [
        str(line.get("line_id") or f"line_{index}")
        for index, line in enumerate(proof.get("lines") or [], start=1)
        if isinstance(line, dict) and line.get("gaps")
    ]
    if unresolved_gap_lines:
        raise RuntimeError(
            "agent consensus cannot seal line-numbered proof with unresolved gaps: "
            + ", ".join(unresolved_gap_lines)
        )
    # Bind the hashes AS THE REVIEWERS REVIEW THEM (captured before the reviewers run,
    # off the on-disk proof they are about to see). The gate re-reads the current
    # files, so a mid-seal proof swap (current != reviewed) is caught -> rollback.
    review_time_bound = _bound_hashes(run_dir, manifest)
    entries = run_proof_reviewers(run_dir, client=client, proof=proof)

    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    consensus_path = run_dir / AGENT_CONSENSUS_FILENAME
    proof_rel = manifest.get("optional_artifacts", {}).get("line_numbered_proof")
    proof_path = run_dir / proof_rel if proof_rel else None
    final_report_rel = manifest["artifacts"].get("final_report")
    final_report_path = run_dir / final_report_rel if final_report_rel else None
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "grade": grade_path.read_bytes(),
        "had_consensus": consensus_path.exists(),
        "consensus": consensus_path.read_bytes() if consensus_path.exists() else None,
        "had_proof": bool(proof_path and proof_path.exists()),
        "proof": proof_path.read_bytes() if proof_path and proof_path.exists() else None,
        "had_final_report": bool(final_report_path and final_report_path.exists()),
        "final_report": final_report_path.read_bytes() if final_report_path and final_report_path.exists() else None,
    }
    try:
        record = build_agent_consensus_review(run_dir, entries, review_time_bound)
        grade = read_yaml(grade_path)
        current_status = grade.get("proof_language_status")
        status_changed = False
        if record["consensus"]:
            grade["proof_language_status"] = ProofLanguageStatus.agent_consensus_mock_checked.value
            IdeaGrade.model_validate(grade)
            write_yaml(grade_path, grade)
            status_changed = True
        elif current_status == ProofLanguageStatus.agent_consensus_mock_checked.value:
            # Honest downgrade: a re-seal that no longer reaches consensus must not
            # leave a stale amber status. Drop to proof_candidate (a weaker status
            # that makes no consensus/machine claim).
            grade["proof_language_status"] = ProofLanguageStatus.proof_candidate.value
            IdeaGrade.model_validate(grade)
            write_yaml(grade_path, grade)
            status_changed = True
        # Surface the change in the static memo so final_report.md discloses the
        # mock check (or its removal) -- the live renderers already reflect it. We
        # MINIMALLY PATCH the existing (already-validated, scan-clean) memo rather
        # than full-rebuild it: a full rebuild would pull in arbitrary artifact prose
        # (e.g. a likely_false assessment line containing "verified") that the
        # weak-status final-report-language gate would reject. The patch only swaps
        # the Proof state line + the (scan-clean) mock-check block.
        if status_changed and final_report_path is not None and final_report_path.exists():
            new_status = (
                ProofLanguageStatus.agent_consensus_mock_checked.value
                if record["consensus"]
                else ProofLanguageStatus.proof_candidate.value
            )
            residual_count = sum(
                len(e.get("residual_objections") or []) for e in record["entries"]
            )
            write_text(
                final_report_path,
                _patch_sealed_memo(
                    final_report_path.read_text(encoding="utf-8"),
                    status=new_status,
                    sealed=record["consensus"],
                    residual_count=residual_count,
                ),
            )
        validate_run(run_dir)
        return record
    except Exception:
        # Best-effort ATOMIC restore: attempt every file even if one fails, then
        # re-raise the ORIGINAL error. Never leave a half-written status behind.
        for path, data in ((manifest_path, snapshot["manifest"]), (grade_path, snapshot["grade"])):
            try:
                _atomic_write_bytes(path, data)
            except Exception:  # noqa: BLE001 - keep restoring the rest
                pass
        try:
            if snapshot["had_consensus"]:
                _atomic_write_bytes(consensus_path, snapshot["consensus"])
            elif consensus_path.exists():
                consensus_path.unlink()
        except Exception:  # noqa: BLE001
            pass
        try:
            if snapshot["had_final_report"] and final_report_path is not None:
                _atomic_write_bytes(final_report_path, snapshot["final_report"])
        except Exception:  # noqa: BLE001
            pass
        try:
            if snapshot["had_proof"] and proof_path is not None:
                _atomic_write_bytes(proof_path, snapshot["proof"])
        except Exception:  # noqa: BLE001
            pass
        raise
