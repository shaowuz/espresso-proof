"""Proof-gap repair sidecar.

This module does not prove mathematics and does not maintain a theorem library.
It turns unresolved dependencies in ``line_numbered_proof.yaml`` into explicit
repair artifacts, optionally attaching candidate theorem refs or human-allowed
theorem refs back to the proof graph.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.llm_agents import (
    agent_prompt,
    build_client,
    load_agent_context,
    proof_gap_resolver_system_prompt,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import LineNumberedProof, ProofGapResolutionReport
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.validators import validate_run

PROOF_GAP_RESOLUTION_FILENAME = "proof_gap_resolution.yaml"
GAP_REPAIR_ROUNDS_INDEX = "gap_repair_rounds/index.yaml"

__all__ = ["PROOF_GAP_RESOLUTION_FILENAME", "resolve_proof_gaps"]


def _load_proof(run_dir: Path, manifest: dict) -> dict:
    proof_rel = manifest.get("optional_artifacts", {}).get("line_numbered_proof")
    if not proof_rel:
        raise RuntimeError("cannot resolve proof gaps: no line_numbered_proof.yaml")
    return LineNumberedProof.model_validate(read_yaml(run_dir / proof_rel)).model_dump(mode="json")


def _unresolved_dependencies(proof: dict) -> list[dict]:
    unresolved: list[dict] = []
    for line in proof.get("lines") or []:
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("line_id") or "")
        for dep in line.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            dep_type = dep.get("type")
            status = dep.get("status")
            resolved = False
            if dep_type == "unchecked_gap":
                resolved = False
            elif dep_type == "theorem_ref":
                resolved = False
            elif dep_type == "human_assumption":
                resolved = False
            else:
                resolved = status == "resolved"
            if not resolved:
                unresolved.append({"line_id": line_id, "dependency": dep, "line": line})
    return unresolved


def _normalize_agent_candidates(raw: object, unresolved: list[dict]) -> list[dict]:
    raw = raw if isinstance(raw, dict) else {}
    by_key = {
        (item["line_id"], str(item["dependency"].get("ref") or "")): item
        for item in unresolved
    }
    candidates: list[dict] = []
    for item in raw.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("line_id") or "").strip()
        dependency_ref = str(item.get("dependency_ref") or "").strip()
        if (line_id, dependency_ref) not in by_key:
            continue
        action = str(item.get("action") or "keep_gap")
        if action not in {"attach_candidate_theorem_ref", "keep_gap"}:
            action = "keep_gap"
        dependency_type = str(item.get("dependency_type") or "unchecked_gap")
        if dependency_type not in {"theorem_ref", "human_assumption", "unchecked_gap"}:
            dependency_type = "unchecked_gap"
        source_kind = "unknown"
        applicability_review = str(item.get("applicability_review") or "not_reviewed")
        if applicability_review not in {
            "not_reviewed",
            "candidate_match",
            "mismatch",
            "unclear",
            "human_allowed",
        }:
            applicability_review = "not_reviewed"
        candidates.append(
            {
                "line_id": line_id,
                "dependency_ref": dependency_ref,
                "dependency_type": dependency_type,
                "action": action,
                "proposed_ref": str(item.get("proposed_ref") or "")[:200],
                "needed_statement": str(item.get("needed_statement") or "")[:2000],
                "proposed_statement": str(item.get("proposed_statement") or "")[:4000],
                "source_locator": str(item.get("source_locator") or "")[:1000],
                "source_url": "",
                "source_kind": source_kind,
                "applicability_review": applicability_review,
                "reviewer_notes": [str(x)[:1000] for x in (item.get("reviewer_notes") or [])],
                "status": "candidate",
                "source_grounded": False,
                "machine_verified": False,
                "human_confirmation_required": True,
            }
        )
    covered = {(item["line_id"], item["dependency_ref"]) for item in candidates}
    for item in unresolved:
        dep = item["dependency"]
        key = (item["line_id"], str(dep.get("ref") or ""))
        if key in covered:
            continue
        dependency_type = str(dep.get("type") or "unchecked_gap")
        if dependency_type not in {"theorem_ref", "human_assumption", "unchecked_gap"}:
            dependency_type = "unchecked_gap"
        candidates.append(
            {
                "line_id": item["line_id"],
                "dependency_ref": key[1],
                "dependency_type": dependency_type,
                "action": "keep_gap",
                "needed_statement": str(dep.get("needed_statement") or dep.get("role") or ""),
                "reviewer_notes": ["No candidate repair was produced for this dependency."],
            }
        )
    return candidates


def _parse_human_allow(specs: list[str] | None, unresolved: list[dict]) -> list[dict]:
    if not specs:
        return []
    unresolved_by_line: dict[str, list[dict]] = {}
    for item in unresolved:
        unresolved_by_line.setdefault(item["line_id"], []).append(item)
    candidates: list[dict] = []
    for spec in specs:
        parts = [part.strip() for part in str(spec).split("|", 2)]
        if len(parts) != 3:
            raise ValueError("--human-allow must be LINE_ID|REF|THEOREM_STATEMENT")
        line_selector, ref, statement = parts
        if "@" in line_selector:
            line_id, requested_dependency_ref = [
                item.strip() for item in line_selector.split("@", 1)
            ]
        else:
            line_id, requested_dependency_ref = line_selector, ""
        line_unresolved = unresolved_by_line.get(line_id) or []
        if not line_unresolved:
            raise ValueError(f"--human-allow line {line_id!r} has no unresolved dependency")
        if requested_dependency_ref:
            matches = [
                item
                for item in line_unresolved
                if str(item["dependency"].get("ref") or "") == requested_dependency_ref
            ]
            if not matches:
                raise ValueError(
                    f"--human-allow dependency {requested_dependency_ref!r} is not unresolved on line {line_id!r}"
                )
            selected = matches[0]
        elif len(line_unresolved) == 1:
            selected = line_unresolved[0]
        else:
            refs = ", ".join(
                str(item["dependency"].get("ref") or "<missing-ref>")
                for item in line_unresolved
            )
            raise ValueError(
                f"--human-allow line {line_id!r} is ambiguous; use LINE_ID@DEPENDENCY_REF "
                f"(unresolved: {refs})"
            )
        dependency_ref = str(selected["dependency"].get("ref") or f"{line_id}.gap.1")
        candidates.append(
            {
                "line_id": line_id,
                "dependency_ref": dependency_ref,
                "dependency_type": "theorem_ref",
                "action": "attach_human_allowed_theorem_ref",
                "proposed_ref": ref,
                "needed_statement": statement,
                "proposed_statement": statement,
                "source_locator": "",
                "source_url": "",
                "source_kind": "unknown",
                "applicability_review": "human_allowed",
                "reviewer_notes": [
                    "User explicitly allowed this theorem for advisory proof work."
                ],
                "status": "accepted_by_human",
                "source_grounded": False,
                "machine_verified": False,
                "human_confirmation_required": False,
            }
        )
    return candidates


def _append_unique_dependency(line: dict, dep: dict) -> None:
    deps = list(line.get("dependencies") or [])
    key = (dep.get("type"), dep.get("ref"))
    if not any((item.get("type"), item.get("ref")) == key for item in deps if isinstance(item, dict)):
        deps.append(dep)
    line["dependencies"] = deps


def _apply_candidates_to_proof(proof: dict, candidates: list[dict]) -> dict:
    lines_by_id = {
        str(line.get("line_id")): line
        for line in proof.get("lines") or []
        if isinstance(line, dict)
    }
    for candidate in candidates:
        line = lines_by_id.get(str(candidate.get("line_id") or ""))
        if not line:
            continue
        action = candidate.get("action")
        proposed_ref = str(candidate.get("proposed_ref") or "").strip()
        if not proposed_ref:
            continue
        if action == "attach_candidate_theorem_ref":
            _append_unique_dependency(
                line,
                {
                    "type": "theorem_ref",
                    "ref": proposed_ref,
                    "status": "candidate",
                    "role": "candidate repair for unresolved proof dependency",
                    "needed_statement": str(candidate.get("needed_statement") or ""),
                    "source_locator": str(candidate.get("source_locator") or ""),
                    "authority": "agent_claimed_theorem_ref",
                    "source_grounded": False,
                    "machine_verified": False,
                },
            )
        elif action == "attach_human_allowed_theorem_ref":
            dependency_ref = str(candidate.get("dependency_ref") or "")
            removed_gap_index: int | None = None
            if dependency_ref.startswith(f"{line.get('line_id')}.gap."):
                try:
                    removed_gap_index = int(dependency_ref.rsplit(".", 1)[1]) - 1
                except ValueError:
                    removed_gap_index = None
            line["dependencies"] = [
                dep
                for dep in (line.get("dependencies") or [])
                if not (
                    isinstance(dep, dict)
                    and dep.get("type") in {"unchecked_gap", "theorem_ref", "human_assumption"}
                    and dep.get("ref") == dependency_ref
                )
            ]
            line["source_refs"] = [
                ref
                for ref in (line.get("source_refs") or [])
                if str(ref) != dependency_ref
            ]
            gaps = list(line.get("gaps") or [])
            if removed_gap_index is not None and 0 <= removed_gap_index < len(gaps):
                gaps.pop(removed_gap_index)
            line["gaps"] = gaps
            _append_unique_dependency(
                line,
                {
                    "type": "theorem_ref",
                    "ref": proposed_ref,
                    "status": "accepted_by_human",
                    "role": "human-allowed theorem for advisory seal",
                    "needed_statement": str(candidate.get("needed_statement") or ""),
                    "authority": "human_allowed_theorem_ref",
                    "source_grounded": False,
                    "machine_verified": False,
                },
            )
    return LineNumberedProof.model_validate(proof).model_dump(mode="json")


def _resolver_context(run_dir: Path, proof: dict, unresolved: list[dict]) -> dict:
    return {
        **load_agent_context(run_dir),
        "line_numbered_proof": proof,
        "unresolved_dependencies": [
            {
                "line_id": item["line_id"],
                "statement": item["line"].get("statement"),
                "dependency": item["dependency"],
            }
            for item in unresolved
        ],
        "source_policy": "dynamic_sources_only",
    }


def _repair_plan(candidates: list[dict]) -> list[dict]:
    plan: list[dict] = []
    for candidate in candidates:
        line_id = str(candidate.get("line_id") or "")
        dep_ref = str(candidate.get("dependency_ref") or "")
        needed = " ".join(
            str(candidate.get(key) or "")
            for key in ("needed_statement", "proposed_statement", "source_locator")
        ).lower()
        base = {
            "line_id": line_id,
            "dependency_ref": dep_ref,
            "dependency_type": candidate.get("dependency_type", "unchecked_gap"),
            "candidate_ref": candidate.get("proposed_ref", ""),
            "status": "planned",
            "advisory_only": True,
        }
        if "gl_2(f_3)" in needed or "gl2(f3)" in needed or "gl_2" in needed and "solv" in needed:
            plan.append(
                {
                    **base,
                    "action": "finite_object_witness",
                    "channel": "scoped_witness_then_review",
                    "description": (
                        "Generate explicit GL_2(F_3) derived-series witness; "
                        "reviewer must decide whether it supports only this finite object."
                    ),
                    "target_artifact": "finite_object_witness.yaml",
                }
            )
        if candidate.get("dependency_type") == "theorem_ref" or "theorem" in needed or "source" in needed:
            plan.append(
                {
                    **base,
                    "action": "source_retrieval",
                    "channel": "source_locator_grounding",
                    "description": (
                        "Retrieve the proposed dynamic source, extract theorem locator, "
                        "then run theorem applicability review."
                    ),
                    "target_artifact": "theorem_extraction.yaml",
                }
            )
            plan.append(
                {
                    **base,
                    "action": "theorem_applicability_review",
                    "channel": "source_match_and_hypothesis_review",
                    "description": (
                        "Run source-match and hypothesis reviewers; keep blocked unless "
                        "the theorem statement and conditions match."
                    ),
                    "target_artifact": "theorem_applicability.yaml",
                }
            )
        if candidate.get("dependency_type") == "unchecked_gap":
            plan.append(
                {
                    **base,
                    "action": "proof_dependency_rewrite",
                    "channel": "proof_graph_repair",
                    "description": (
                        "Rewrite the proof dependency edge only after a source-backed, "
                        "human-allowed, or scoped witness review is available."
                    ),
                    "target_artifact": "line_numbered_proof.yaml",
                }
            )
    if candidates:
        plan.append(
            {
                "line_id": "*",
                "dependency_ref": "*",
                "dependency_type": "seal_retry",
                "candidate_ref": "",
                "status": "planned",
                "advisory_only": True,
                "action": "rerun_seal_consensus",
                "channel": "seal_after_repairs_only",
                "description": "Rerun seal-consensus after accepted repairs; unresolved candidate repairs must still fail closed.",
                "target_artifact": "agent_consensus_review.yaml",
            }
        )
    return plan


def _write_repair_loop_artifacts(
    run_dir: Path,
    *,
    manifest: dict,
    claim: dict,
    report: dict,
    repair_plan: list[dict],
) -> None:
    blockers = [
        {
            "line_id": item.get("line_id"),
            "dependency_ref": item.get("dependency_ref"),
            "dependency_type": item.get("dependency_type"),
            "needed_statement": item.get("needed_statement", ""),
        }
        for item in report.get("candidates", [])
    ]
    repair_target = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "kind": "repair_target",
        "source": "proof_gap_resolver",
        "advisory_only": True,
        "selected_target": {
            "line_ids": sorted({str(item.get("line_id")) for item in blockers if item.get("line_id")}),
            "reason": "seal-consensus found unresolved proof dependencies",
        },
        "blockers": blockers,
        "repair_plan": repair_plan,
        "notes": [
            "Repair target is scheduling metadata only.",
            "It cannot upgrade proof/source/evidence status.",
        ],
    }
    write_yaml(run_dir / "repair_target.yaml", repair_target)

    rounds_dir = run_dir / "gap_repair_rounds"
    rounds_dir.mkdir(exist_ok=True)
    index_path = run_dir / GAP_REPAIR_ROUNDS_INDEX
    index = read_yaml(index_path) if index_path.exists() else {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "rounds": [],
        "notes": [
            "Gap repair rounds are advisory process records.",
            "They never clear a proof dependency without source/applicability review or human allowance.",
        ],
    }
    round_id = f"gap_repair_{len(index.get('rounds', [])) + 1:03d}"
    round_record = {
        "round_id": round_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "trigger": "seal_consensus_unresolved_dependencies",
        "proof_gap_resolution": PROOF_GAP_RESOLUTION_FILENAME,
        "repair_target": "repair_target.yaml",
        "status": "planned",
        "repair_plan_count": len(repair_plan),
        "actions": repair_plan,
    }
    write_yaml(rounds_dir / f"{round_id}.yaml", round_record)
    index.setdefault("rounds", []).append(
        {
            "round_id": round_id,
            "artifact": f"gap_repair_rounds/{round_id}.yaml",
            "status": "planned",
            "repair_plan_count": len(repair_plan),
        }
    )
    write_yaml(index_path, index)


def _snapshot_optional_file(path: Path) -> dict:
    return {
        "exists": path.exists(),
        "bytes": path.read_bytes() if path.exists() else None,
    }


def _restore_optional_file(path: Path, snapshot: dict) -> None:
    if snapshot["exists"]:
        path.write_bytes(snapshot["bytes"])
    elif path.exists():
        path.unlink()


def _snapshot_directory(path: Path) -> dict:
    files: dict[str, bytes] = {}
    existed = path.exists()
    if existed:
        for item in path.rglob("*"):
            if item.is_file():
                files[item.relative_to(path).as_posix()] = item.read_bytes()
    return {"exists": existed, "files": files}


def _restore_directory(path: Path, snapshot: dict) -> None:
    if path.exists():
        for item in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if item.is_file():
                rel = item.relative_to(path).as_posix()
                if rel not in snapshot["files"]:
                    item.unlink()
        for item in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass
    if snapshot["exists"]:
        path.mkdir(exist_ok=True)
        for rel, data in snapshot["files"].items():
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    elif path.exists():
        try:
            path.rmdir()
        except OSError:
            pass


def resolve_proof_gaps(
    run_dir: Path,
    *,
    provider: str = "stub",
    model: str = "stub-espresso-agent",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    human_allow: list[str] | None = None,
    client=None,
) -> dict:
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
    proof = _load_proof(run_dir, manifest)
    unresolved = _unresolved_dependencies(proof)
    human_candidates = _parse_human_allow(human_allow, unresolved)
    raw = {}
    if unresolved and not human_candidates:
        raw = client.complete_json(
            system=proof_gap_resolver_system_prompt(),
            prompt=agent_prompt(_resolver_context(run_dir, proof, unresolved), role="proof_gap_resolver"),
        )
    agent_candidates = [] if human_candidates else _normalize_agent_candidates(raw, unresolved)
    candidates = [*agent_candidates, *human_candidates]
    repair_plan = _repair_plan(candidates)
    patched_proof = _apply_candidates_to_proof(proof, candidates)
    proof_artifact = manifest.get("optional_artifacts", {}).get(
        "line_numbered_proof", "line_numbered_proof.yaml"
    )

    proof_path = run_dir / proof_artifact
    report_path = run_dir / PROOF_GAP_RESOLUTION_FILENAME
    repair_target_path = run_dir / "repair_target.yaml"
    rounds_dir = run_dir / "gap_repair_rounds"
    snapshot = {
        "manifest": manifest_path.read_bytes(),
        "proof": proof_path.read_bytes(),
        "had_report": report_path.exists(),
        "report": report_path.read_bytes() if report_path.exists() else None,
        "repair_target": _snapshot_optional_file(repair_target_path),
        "rounds_dir": _snapshot_directory(rounds_dir),
    }
    try:
        write_yaml(proof_path, patched_proof)
        proof_hash = artifact_revision_hash(run_dir, proof_artifact)
        report = {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": datetime.now(UTC).isoformat(),
            "source_policy": "dynamic_sources_only",
            "proof_artifact": proof_artifact,
            "line_numbered_proof_sha256": proof_hash,
            "candidates": candidates,
            "repair_plan": repair_plan,
            "notes": [
                "Candidate repairs only; source/applicability review or explicit human allowance is required before a gap can clear.",
                "No theorem library or mathematical rule checker is used by this resolver.",
                "LLM-suggested locators are locator claims only until backed by retrieval/reviewer evidence.",
            ],
        }
        report = ProofGapResolutionReport.model_validate(report).model_dump(mode="json")
        write_yaml(report_path, report)
        _write_repair_loop_artifacts(
            run_dir,
            manifest=manifest,
            claim=claim,
            report=report,
            repair_plan=repair_plan,
        )
        manifest.setdefault("optional_artifacts", {})[
            "proof_gap_resolution"
        ] = PROOF_GAP_RESOLUTION_FILENAME
        manifest.setdefault("optional_artifacts", {})["repair_target"] = "repair_target.yaml"
        manifest.setdefault("optional_artifacts", {})[
            "gap_repair_rounds_index"
        ] = GAP_REPAIR_ROUNDS_INDEX
        write_yaml(manifest_path, manifest)
        validate_run(run_dir)
        return report
    except Exception:
        tmp = manifest_path.with_name(manifest_path.name + ".restore.tmp")
        tmp.write_bytes(snapshot["manifest"])
        os.replace(tmp, manifest_path)
        proof_path.write_bytes(snapshot["proof"])
        if snapshot["had_report"]:
            report_path.write_bytes(snapshot["report"])
        elif report_path.exists():
            report_path.unlink()
        _restore_optional_file(repair_target_path, snapshot["repair_target"])
        _restore_directory(rounds_dir, snapshot["rounds_dir"])
        raise
