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
from espresso_proof.kernel.models import LineNumberedProof
from espresso_proof.kernel.validators import validate_run

LINE_NUMBERED_PROOF_FILENAME = "line_numbered_proof.yaml"

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

    raw = client.complete_json(
        system=line_numbered_proof_writer_system_prompt(),
        prompt=agent_prompt(_writer_context(run_dir, manifest), role="line_numbered_proof_writer"),
    )
    raw = raw if isinstance(raw, dict) else {}
    record = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "strategy_id": _selected_strategy_id(run_dir, manifest),
        "proof_kind": (str(raw.get("proof_kind", "") or "unspecified").strip() or "unspecified")[:200],
        "status": "advisory",
        "lines": _normalize_lines(raw.get("lines")),
        "generated_at": datetime.now(UTC).isoformat(),
        "notes": [],
    }
    # Fail closed before writing, and persist the normalized proof graph rather
    # than the raw model output (legacy depends_on/source_refs/gaps are expanded
    # into structured dependencies by the model).
    record = LineNumberedProof.model_validate(record).model_dump(mode="json")

    proof_path = run_dir / LINE_NUMBERED_PROOF_FILENAME
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "had_proof": proof_path.exists(),
        "proof": proof_path.read_bytes() if proof_path.exists() else None,
    }
    try:
        write_yaml(proof_path, record)
        manifest.setdefault("optional_artifacts", {})["line_numbered_proof"] = LINE_NUMBERED_PROOF_FILENAME
        write_yaml(manifest_path, manifest)
        validate_run(run_dir)
        return record
    except Exception:
        tmp = manifest_path.with_name(manifest_path.name + ".restore.tmp")
        tmp.write_bytes(snapshot["manifest"])
        os.replace(tmp, manifest_path)
        if snapshot["had_proof"]:
            proof_path.write_bytes(snapshot["proof"])
        elif proof_path.exists():
            proof_path.unlink()
        raise
