"""Phase 6 Slice B: adversarial-consensus runner + seal action.

Runs the three Espresso reviewers ADVERSARIALLY (each hunts for an argument-breaking
flaw) and, if all three return ``no_fatal_flaw_found``, SEALS the run with
``proof_language_status = agent_consensus_mock_checked`` -- an amber/advisory kernel
status (NOT machine-verified) gated by ``validators._validate_agent_consensus_review``.

Trust posture:
- This is the ONLY producer of ``agent_consensus_mock_checked``; nothing else sets it.
- It mutates ``idea_grade`` only this one field, and only when the consensus is
  unanimous; it computes the ``bound_*`` hashes EXACTLY as the kernel re-checks them.
- It is TRANSACTIONAL: it snapshots idea_grade / manifest / the consensus sidecar,
  and if ``validate_run`` rejects the result (e.g. a failed standard reviewer or a
  refuted claim makes the consensus inadmissible) it ROLLS BACK and re-raises -- the
  run is never left in an invalid state, and a status is never half-written.
- An unparseable / missing reviewer verdict fails safe to ``unclear`` (blocks).
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.llm_agents import (
    ESPRESSO_REVIEWER_ROLES,
    adversarial_verifier_system_prompt,
    agent_prompt,
    build_client,
    load_agent_context,
)
from espresso_proof.kernel.io import read_yaml, write_text, write_yaml
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AgentConsensusVerdict,
    IdeaGrade,
    ProofLanguageStatus,
)
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.repair_target import _latest_round_report

AGENT_CONSENSUS_FILENAME = "agent_consensus_review.yaml"
CONSENSUS_DISCLAIMER = (
    "Agent consensus only: three reviewers adversarially found no fatal flaw. This is "
    "NOT machine-verified and NOT a checked/source-backed/proved status -- it is an "
    "advisory mock check and may be wrong; residual gaps below remain open."
)
_VERDICT_VALUES = {v.value for v in AgentConsensusVerdict}

__all__ = [
    "AGENT_CONSENSUS_FILENAME",
    "run_adversarial_reviewers",
    "build_agent_consensus_review",
    "seal_agent_consensus",
]


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically (temp + os.replace), so a restore can never leave a
    file with partial content even on an I/O failure mid-write."""
    tmp = path.with_name(path.name + ".restore.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _normalize_entry(raw: object, reviewer_id: str) -> dict:
    """Normalize one reviewer's raw output into an AgentConsensusEntry dict.
    Fail-safe: an unparseable/missing verdict becomes ``unclear`` (which blocks)."""
    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict", "")).strip()
    if verdict not in _VERDICT_VALUES:
        verdict = AgentConsensusVerdict.unclear.value
    residual = [str(x) for x in (raw.get("residual_objections") or []) if str(x).strip()]
    return {
        "reviewer_id": reviewer_id,
        "verdict": verdict,
        "looked_for": str(raw.get("looked_for", "") or "")[:4000],
        "finding": str(raw.get("finding", "") or "")[:4000],
        "residual_objections": residual,
    }


def _proof_context(run_dir: Path, manifest: dict) -> dict:
    """The standard agent context augmented with the CURRENT prover material, so the
    adversarial reviewers judge the latest proof attempt -- not a stale one.

    Prefers the latest prover-loop ROUND (the deepest/most-recent attempt) over the
    initial single-shot ``llm_agent_run``: when a prover loop has iterated, the real
    proof lives in the latest round, and the consensus is hash-bound to prover_loop,
    so the reviewers must see that round, not the initial attempt.
    """
    context = load_agent_context(run_dir)
    prover_attempt = None
    verifier_reviews = None
    if (run_dir / "prover_rounds").is_dir():
        latest = _latest_round_report(run_dir)
        if isinstance(latest, dict):
            prover_attempt = latest.get("prover_attempt")
            verifier_reviews = latest.get("verifier_reviews")
    if prover_attempt is None:
        agent_run_rel = manifest.get("optional_artifacts", {}).get("llm_agent_run")
        if agent_run_rel:
            agent_run = read_yaml(run_dir / agent_run_rel)
            if isinstance(agent_run, dict):
                prover_attempt = agent_run.get("prover_attempt")
                verifier_reviews = agent_run.get("verifier_reviews")
    if prover_attempt is not None:
        context = {
            **context,
            "prover_attempt": prover_attempt,
            "verifier_reviews": verifier_reviews,
        }
    return context


def run_adversarial_reviewers(run_dir: Path, *, client) -> list[dict]:
    """Call the three Espresso reviewers in adversarial find-fatal-flaw mode."""
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    context = _proof_context(run_dir, manifest)
    entries: list[dict] = []
    for reviewer_id in ESPRESSO_REVIEWER_ROLES:
        raw = client.complete_json(
            system=adversarial_verifier_system_prompt(reviewer_id),
            prompt=agent_prompt(context, role=f"adversarial_{reviewer_id}"),
        )
        entries.append(_normalize_entry(raw, reviewer_id))
    return entries


def _bound_hashes(run_dir: Path, manifest: dict) -> dict:
    artifacts = manifest["artifacts"]
    prover_loop_rel = manifest.get("optional_artifacts", {}).get("prover_loop")
    return {
        "bound_claim_hash": artifact_revision_hash(run_dir, artifacts["locked_claim"]),
        "bound_source_grounding_hash": artifact_revision_hash(run_dir, artifacts["source_grounding"]),
        "bound_review_hash": artifact_revision_hash(run_dir, artifacts["review"]),
        "bound_prover_loop_hash": (
            artifact_revision_hash(run_dir, prover_loop_rel) if prover_loop_rel else ""
        ),
    }


def build_agent_consensus_review(run_dir: Path, entries: list[dict]) -> dict:
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
        **_bound_hashes(run_dir, manifest),
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
    """Run the adversarial reviewers and, if unanimous, seal the run with
    agent_consensus_mock_checked. Transactional: rolls back on any validation failure.
    Returns the consensus record."""
    run_dir = Path(run_dir)
    client = client or build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    entries = run_adversarial_reviewers(run_dir, client=client)

    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    consensus_path = run_dir / AGENT_CONSENSUS_FILENAME
    final_report_rel = manifest["artifacts"].get("final_report")
    final_report_path = run_dir / final_report_rel if final_report_rel else None
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "grade": grade_path.read_bytes(),
        "had_consensus": consensus_path.exists(),
        "consensus": consensus_path.read_bytes() if consensus_path.exists() else None,
        "had_final_report": bool(final_report_path and final_report_path.exists()),
        "final_report": final_report_path.read_bytes() if final_report_path and final_report_path.exists() else None,
    }
    try:
        record = build_agent_consensus_review(run_dir, entries)
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
        # mock check (or its removal) -- the live renderers already reflect it.
        if status_changed and final_report_path is not None:
            from espresso_proof.ui.memo import build_concise_memo

            write_text(final_report_path, build_concise_memo(run_dir))
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
        raise
