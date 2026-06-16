"""Active memory influence (Goal 09).

Turn prior-run project memory into a deterministic, ADVISORY ``memory_context``
for the current run: which remembered lessons are relevant to this claim and how
to reuse them (avoid a failed route, retest a counterexample, request a known
source gap, prioritize a human preference, ask a human). It is advisory only —
``MemoryContext`` carries no source/evidence/proof status field, nothing here
upgrades a trusted status or overrides current-run artifacts, and it only reads
project memory + writes the run's own memory_context.yaml.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .memory import _load_memory_entry, _rank_entries, _score_entry, _tokens, load_memory_index
from .models import MemoryContext

# type -> advisory recommended use. None of these change a trusted status.
_USE_BY_TYPE = {
    "failed_attempt": "avoid",
    "source_gap": "request_source",
    "theorem_applicability_failure": "request_source",
    "counterexample": "retest",
    "tested_example": "retest",
    "reviewer_objection": "ask_human",
    "user_decision": "prioritize",
    "human_example_feedback": "prioritize",
    "repaired_claim": "note",
}

_MAX_MATCHED = 8


def build_memory_context(run_dir: Path, *, generated_at: str | None = None) -> dict:
    """Deterministically match prior project memory to this run's claim and write
    an advisory memory_context.yaml. Re-validates the run (fail closed)."""
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    claim_id = claim["id"]
    claim_text = " ".join(
        [
            str(claim.get("normalized_statement", "")),
            " ".join(str(o) for o in claim.get("objects", []) or []),
            str(claim.get("conclusion", "")),
        ]
    )
    claim_tokens = _tokens(claim_text)

    project_dir = run_dir.parents[1]
    index = load_memory_index(project_dir)

    matched: list[dict] = []
    if claim_tokens:
        for index_entry in _rank_entries(index.entries, claim_text):
            shared = sorted(claim_tokens.intersection(_tokens(index_entry.summary)))
            score = _score_entry(index_entry, claim_tokens)
            if score <= 0:
                continue  # only genuinely relevant memory is surfaced
            mem = _load_memory_entry(project_dir, index_entry)
            summary = (mem.summary if mem else index_entry.summary)
            provenance = list((mem.provenance if mem else index_entry.provenance) or [])
            source_claim_id = str((mem.claim_id if mem else index_entry.claim_id) or "")
            # _score_entry also scores over tags/provenance, so a relevant entry
            # may share no SUMMARY terms; report that honestly instead of "shares 0".
            reason = (
                f"shares {len(shared)} claim term(s)"
                if shared
                else "matches this claim on tags/metadata"
            )
            same_claim = source_claim_id == claim_id
            recommended_use = _USE_BY_TYPE.get(index_entry.type, "note")
            if not same_claim:
                recommended_use = "note"
                reason = f"{reason}; pending revalidation before reuse"
            matched.append(
                {
                    "id": index_entry.id,
                    "type": index_entry.type,
                    "relevance_reason": reason,
                    "recommended_use": recommended_use,
                    "summary": summary,
                    "memory_claim_id": source_claim_id,
                    "provenance": provenance,
                    "revalidation_required": not same_claim,
                    "revalidated_against_locked_claim": same_claim,
                }
            )
            if len(matched) >= _MAX_MATCHED:
                break

    report = {
        "schema_version": 2,
        "claim_id": claim_id,
        "run_id": manifest["run_id"],
        "advisory_only": True,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "matched_entries": matched,
        "constraints": [
            "advisory only; memory records reuse hints and never assigns kernel-owned conclusions",
            "memory only influences route ranking, time allocation, and what to test "
            "first; kernel validators remain the only gate for trusted conclusions",
            "memory never overrides a current-run artifact",
        ],
    }
    MemoryContext.model_validate(report)

    write_yaml(run_dir / "memory_context.yaml", report)
    manifest.setdefault("optional_artifacts", {})["memory_context"] = "memory_context.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    from .validators import validate_run

    validate_run(run_dir)
    return report
