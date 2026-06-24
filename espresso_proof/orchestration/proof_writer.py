"""Goal 69: Line-Numbered Proof Writer V1.

Produces ``line_numbered_proof.yaml`` -- a real STRUCTURED proof object (each line has
a statement AND a justification AND its dependencies), NOT a route / proposed_steps
list. This is the only artifact the agent-consensus mock check may seal.

Trust posture: the proof is CANDIDATE / ADVISORY. ``LineNumberedProof.status`` is
pinned ``advisory`` and the model carries no proof/source/checked status field, so
writing a proof never earns trusted status; only the kernel's hard gates (or the
explicitly-amber agent-consensus mock check) move proof language, and the mock check
stays not-machine-verified. Writing is TRANSACTIONAL + fail-closed: a malformed proof
is rejected by ``LineNumberedProof`` before write, and a write that fails ``validate_run``
is rolled back.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.llm_agents import (
    agent_prompt,
    build_client,
    line_numbered_proof_writer_system_prompt,
    load_agent_context,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import LineNumberedProof, ProofLanguageStatus
from espresso_proof.kernel.validators import validate_run

LINE_NUMBERED_PROOF_FILENAME = "line_numbered_proof.yaml"
_PROOF_BOUND_SIDECARS = ("proof_gap_resolution", "agent_consensus_review")

__all__ = ["LINE_NUMBERED_PROOF_FILENAME", "write_line_numbered_proof"]


def _normalize_lines(raw_lines: object) -> list[dict]:
    out: list[dict] = []
    for index, line in enumerate(raw_lines or [], start=1):
        if not isinstance(line, dict):
            continue
        out.append(
            {
                "line_id": (str(line.get("line_id") or "").strip() or f"L{index}"),
                "statement": str(line.get("statement", "") or "").strip(),
                "justification": str(line.get("justification", "") or "").strip(),
                "dependencies": list(line.get("dependencies") or []),
                "depends_on": [str(d).strip() for d in (line.get("depends_on") or []) if str(d).strip()],
                "source_refs": [str(s).strip() for s in (line.get("source_refs") or []) if str(s).strip()],
                "gaps": [str(g).strip() for g in (line.get("gaps") or []) if str(g).strip()],
            }
        )
    return out


def _writer_context(run_dir: Path, manifest: dict) -> dict:
    """Claim + the prover's candidate route(s), so the writer turns a route into a
    real line-numbered proof."""
    context = load_agent_context(run_dir)
    agent_run_rel = manifest.get("optional_artifacts", {}).get("llm_agent_run")
    if agent_run_rel:
        agent_run = read_yaml(run_dir / agent_run_rel)
        if isinstance(agent_run, dict):
            context = {**context, "prover_attempt": agent_run.get("prover_attempt")}
    return context


def _selected_strategy_id(run_dir: Path, manifest: dict) -> str:
    loop_rel = manifest.get("optional_artifacts", {}).get("prover_loop")
    if loop_rel:
        loop = read_yaml(run_dir / loop_rel)
        if isinstance(loop, dict) and loop.get("selected_strategy_id"):
            return str(loop["selected_strategy_id"])
    return "strategy_route_001"


def write_line_numbered_proof(
    run_dir: Path,
    *,
    provider: str = "stub",
    model: str = "stub-espresso-agent",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Write line_numbered_proof.yaml from a proof-writer agent. Transactional +
    fail-closed: rolls back on any validation failure. Returns the proof record."""
    run_dir = Path(run_dir)
    client = client or build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    generated_at = datetime.now(UTC).isoformat()
    strategy_id = _selected_strategy_id(run_dir, manifest)

    direct_source_proof = _direct_source_backed_theorem_proof(
        run_dir,
        manifest,
        claim,
        strategy_id=strategy_id,
        generated_at=generated_at,
    )
    if direct_source_proof is not None:
        record = direct_source_proof
    else:
        raw = client.complete_json(
            system=line_numbered_proof_writer_system_prompt(),
            prompt=agent_prompt(_writer_context(run_dir, manifest), role="line_numbered_proof_writer"),
        )
        raw = raw if isinstance(raw, dict) else {}
        record = {
            "schema_version": 1,
            "claim_id": claim["id"],
            "strategy_id": strategy_id,
            "proof_kind": (str(raw.get("proof_kind", "") or "unspecified").strip() or "unspecified")[:200],
            "status": "advisory",
            "lines": _normalize_lines(raw.get("lines")),
            "generated_at": generated_at,
            "notes": [],
        }
    # Fail closed before writing, and persist the normalized proof graph rather
    # than the raw model output (legacy depends_on/source_refs/gaps are expanded
    # into structured dependencies by the model).
    record = LineNumberedProof.model_validate(record).model_dump(mode="json")

    proof_path = run_dir / LINE_NUMBERED_PROOF_FILENAME
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "grade": grade_path.read_bytes() if grade_path.exists() else None,
        "had_proof": proof_path.exists(),
        "proof": proof_path.read_bytes() if proof_path.exists() else None,
    }
    try:
        optional = manifest.setdefault("optional_artifacts", {})
        revoked_sidecars = [key for key in _PROOF_BOUND_SIDECARS if key in optional]
        for key in revoked_sidecars:
            optional.pop(key, None)
        if "agent_consensus_review" in revoked_sidecars:
            grade = read_yaml(grade_path)
            if (
                isinstance(grade, dict)
                and grade.get("proof_language_status")
                == ProofLanguageStatus.agent_consensus_mock_checked.value
            ):
                grade["proof_language_status"] = ProofLanguageStatus.proof_candidate.value
                grade["human_review_required"] = True
                reason = (
                    "Line-numbered proof was regenerated; stale agent consensus "
                    "seal was revoked."
                )
                reasons = list(grade.get("reasons") or [])
                if reason not in reasons:
                    grade["reasons"] = [reason, *reasons]
                write_yaml(grade_path, grade)
        write_yaml(proof_path, record)
        optional["line_numbered_proof"] = LINE_NUMBERED_PROOF_FILENAME
        write_yaml(manifest_path, manifest)
        validate_run(run_dir)
        return record
    except Exception:
        tmp = manifest_path.with_name(manifest_path.name + ".restore.tmp")
        tmp.write_bytes(snapshot["manifest"])
        os.replace(tmp, manifest_path)
        if snapshot["grade"] is not None:
            tmp = grade_path.with_name(grade_path.name + ".restore.tmp")
            tmp.write_bytes(snapshot["grade"])
            os.replace(tmp, grade_path)
        if snapshot["had_proof"]:
            proof_path.write_bytes(snapshot["proof"])
        elif proof_path.exists():
            proof_path.unlink()
        raise


def _direct_source_backed_theorem_proof(
    run_dir: Path,
    manifest: dict,
    claim: dict,
    *,
    strategy_id: str,
    generated_at: str,
) -> dict | None:
    """Use an already grounded theorem/corollary when it directly states the claim.

    This is a proof-writer convenience, not a status upgrade. The later consensus
    seal cross-checks the theorem_ref against ``source_grounding.yaml`` again.
    """

    source_rel = manifest.get("artifacts", {}).get("source_grounding")
    if not source_rel:
        return None
    source_path = run_dir / source_rel
    if not source_path.exists():
        return None
    try:
        source_grounding = read_yaml(source_path)
    except Exception:  # noqa: BLE001 - malformed source artifact just disables shortcut
        return None

    for ref in source_grounding.get("source_refs") or []:
        if not _source_ref_directly_covers_claim(ref, claim):
            continue
        locator = ref.get("locator") or {}
        locator_text = f"{ref.get('title', '').strip()}, {locator.get('value', '').strip()}".strip(", ")
        statement = str(ref.get("statement_or_summary") or "").strip()
        return {
            "schema_version": 1,
            "claim_id": claim["id"],
            "strategy_id": strategy_id,
            "proof_kind": "direct_source_grounded_theorem",
            "status": "advisory",
            "lines": [
                {
                    "line_id": "L1",
                    "statement": claim.get("normalized_statement") or claim.get("raw_text") or "",
                    "justification": (
                        "Instantiate the locked claim with the hypotheses recorded "
                        "in locked_claim.yaml."
                    ),
                    "dependencies": [],
                    "depends_on": [],
                    "source_refs": [],
                    "gaps": [],
                },
                {
                    "line_id": "L2",
                    "statement": claim.get("conclusion") or claim.get("normalized_statement") or "",
                    "justification": (
                        f"The cited source statement {ref['id']} applies to the "
                        "locked hypotheses and states the claimed conclusion."
                    ),
                    "dependencies": [
                        {
                            "type": "prior_line",
                            "ref": "L1",
                            "status": "resolved",
                            "role": "locked hypotheses",
                            "needed_statement": "",
                            "source_locator": "",
                            "reviewer_verdict": "",
                            "authority": "",
                            "source_grounded": False,
                            "machine_verified": False,
                        },
                        {
                            "type": "theorem_ref",
                            "ref": ref["id"],
                            "status": "resolved",
                            "role": "direct source-grounded theorem covering the claim",
                            "needed_statement": statement,
                            "source_locator": locator_text,
                            "reviewer_verdict": "",
                            "authority": "source_grounded_theorem_ref",
                            "source_grounded": True,
                            "machine_verified": False,
                        },
                    ],
                    "depends_on": ["L1"],
                    "source_refs": [ref["id"]],
                    "gaps": [],
                },
            ],
            "generated_at": generated_at,
            "notes": [
                "Proof writer used an already source-grounded theorem/corollary that directly covers the locked claim."
            ],
        }
    return None


def _source_ref_directly_covers_claim(ref: dict, claim: dict) -> bool:
    if not isinstance(ref, dict):
        return False
    if ref.get("status") != "source_backed":
        return False
    if (ref.get("hypothesis_match") or {}).get("matches") is not True:
        return False
    locator = ref.get("locator") or {}
    if locator.get("kind") in {"", "unknown", None} or not str(locator.get("value") or "").strip():
        return False
    statement = str(ref.get("statement_or_summary") or "")
    conclusion = str(claim.get("conclusion") or claim.get("normalized_statement") or "")
    if not statement.strip() or not conclusion.strip():
        return False
    try:
        from espresso_proof.verification.source_verification import conclusion_compatible

        return conclusion_compatible(conclusion, statement)
    except Exception:  # noqa: BLE001 - direct shortcut must fail closed
        return False
