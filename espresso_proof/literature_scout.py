"""LLM Literature Scout (Goal 02).

LLM = scout, kernel = judge. The scout proposes CANDIDATE sources / locator hints
tied to specific proof steps and writes them to ``literature_candidates.yaml``. It
can never grant source-backed status: the artifact is candidate-only by
construction (``LiteratureCandidate.status`` is a fixed Literal, and this
normalizer additionally forces ``candidate`` and never invents a title/author).
Reference support is earned only later by retrieval + extraction + grounding +
validation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .claim_clear import ensure_claim_clear_for_downstream
from .io import read_yaml, write_yaml
from .llm_agents import LLMClient, safe_list, safe_text
from .models import LiteratureCandidates

_ALLOWED_SOURCE_TYPES = {"paper", "book", "notes", "web", "local", "unknown"}

_SCOUT_SYSTEM = (
    "You are the Espresso Proof LLM Literature Scout. For each proof step you are "
    "given, propose CANDIDATE references that might support it. You are a scout, "
    "not a judge: you NEVER mark a source as source-backed, verified, or checked, "
    "and you NEVER invent an author, title, or exact locator you do not actually "
    "know. Prefer exact title/author/identifier when known; otherwise leave the "
    "field empty and list it in missing_metadata. Tie every candidate to the "
    "proof_step_id it might support. Respond with ONLY a JSON object."
)

_SCOUT_OUTPUT_SCHEMA = {
    "proof_step_candidates": [
        {
            "proof_step_id": "string (an existing proof step id)",
            "candidates": [
                {
                    "title": "string (exact title if known, else empty)",
                    "authors": ["string"],
                    "source_type": "paper|book|notes|web|local|unknown",
                    "locator_hint": "string (a hint, NOT an exact verified locator)",
                    "statement_hint": "string (what the source likely states)",
                    "url_or_identifier": "string (arXiv id / DOI / URL if known)",
                    "why_relevant": "string (which step this might support and why)",
                }
            ],
        }
    ],
    "notes": ["string"],
}


def _normalize_source_type(value: object) -> str:
    return value if value in _ALLOWED_SOURCE_TYPES else "unknown"


def _missing_metadata(
    *, authors: list[str], locator_hint: str, url_or_identifier: str, statement_hint: str
) -> list[str]:
    missing: list[str] = []
    if not authors:
        missing.append("authors")
    if not locator_hint.strip():
        missing.append("locator_hint")
    # SAFETY: the scout never supplies an exact verified locator; grounding does.
    # Always record that an exact locator is still needed before any support.
    missing.append("exact_locator")
    if not url_or_identifier.strip():
        missing.append("url_or_identifier")
    if not statement_hint.strip():
        missing.append("statement_hint")
    return missing


def _normalize_candidates(raw_list: object, *, step_idx: int) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw_list, list):
        return out
    for ci, raw in enumerate(raw_list, start=1):
        if not isinstance(raw, dict):
            continue
        title = safe_text(raw.get("title", "")).strip()
        # SAFETY: never invent a title; drop a candidate with no usable title.
        if not title or title == "No usable agent text returned.":
            continue
        authors = [a for a in safe_list(raw.get("authors")) if a.strip()]
        locator_hint = safe_text(raw.get("locator_hint", ""))
        url = safe_text(raw.get("url_or_identifier", ""))
        statement_hint = safe_text(raw.get("statement_hint", ""))
        out.append(
            {
                "id": f"litcand_{step_idx:02d}_{ci:02d}",
                "title": title,
                "authors": authors,
                "source_type": _normalize_source_type(raw.get("source_type")),
                "locator_hint": locator_hint,
                "statement_hint": statement_hint,
                "url_or_identifier": url,
                "why_relevant": safe_text(raw.get("why_relevant", ""))
                or "candidate source proposed by the literature scout",
                "missing_metadata": _missing_metadata(
                    authors=authors,
                    locator_hint=locator_hint,
                    url_or_identifier=url,
                    statement_hint=statement_hint,
                ),
                # SAFETY: forced candidate regardless of model input (downgrade).
                "status": "candidate",
            }
        )
    return out


def _reviewer_source_requests(review: dict) -> list[str]:
    """Source-gap signals the reviewers raised, used only as advisory prompt
    context for the scout (never as support). Reads the canonical Review artifact
    fields (source_risks / missing_hypotheses / per-reviewer objections) and also
    tolerates the LLM-agent verifier shape (verifier_reviews/source_requests)."""
    requests: list[str] = []
    # Canonical Review model.
    requests.extend(safe_list(review.get("source_risks")))
    requests.extend(safe_list(review.get("missing_hypotheses")))
    for reviewer in review.get("reviewers", []) or []:
        if isinstance(reviewer, dict):
            requests.extend(safe_list(reviewer.get("objections")))
    # LLM-agent verifier shape (if a run used it instead).
    requests.extend(safe_list(review.get("source_requests")))
    for key in ("verifier_reviews", "reviews"):
        for item in review.get(key, []) or []:
            if isinstance(item, dict):
                requests.extend(safe_list(item.get("source_requests")))
    # de-dup, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for r in requests:
        if r and r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


def run_literature_scout(
    *, run_dir: Path, client: LLMClient, generated_at: str | None = None
) -> dict:
    """Run the Literature Scout over a run and write ``literature_candidates.yaml``.

    Reads the locked claim, hypothesis ledger, source-grounding proof steps, the
    strategy brief, and reviewer source requests; asks the LLM for candidate
    references per step; writes a candidate-only artifact; registers it in the
    manifest; and re-validates the run (fail closed).
    """
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    ledger = read_yaml(run_dir / artifacts["hypothesis_ledger"]) or {}
    grounding = read_yaml(run_dir / artifacts["source_grounding"]) or {}
    review = read_yaml(run_dir / artifacts["review"]) or {}
    brief = read_yaml(run_dir / artifacts["strategy_brief"]) or {}

    steps = grounding.get("steps", []) or []
    # Ordered known proof steps with a short summary; only these may receive
    # candidates (a candidate for an unknown step id is dropped, never reassigned).
    step_summaries: dict[str, str] = {}
    for s in steps:
        sid = s.get("proof_step_id")
        if sid and sid not in step_summaries:
            step_summaries[sid] = safe_text(s.get("needed_theorem", ""))[:300]

    payload = {
        "task": "scout_candidate_sources_per_proof_step",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
            "conclusion": claim.get("conclusion", ""),
        },
        "hypotheses": claim.get("hypotheses", []),
        "claim_precision": ledger.get("claim_precision", "unclear"),
        "proof_steps": [
            {"proof_step_id": sid, "summary": summary}
            for sid, summary in step_summaries.items()
        ],
        "strategy_titles": [s.get("title", "") for s in brief.get("top_strategies", [])],
        "reviewer_source_requests": _reviewer_source_requests(review),
        "required_json_schema": _SCOUT_OUTPUT_SCHEMA,
        "rules": [
            "candidates only; never source_backed / verified / checked",
            "never invent an author, title, or exact locator you do not know",
            "tie each candidate to an existing proof_step_id",
            "list every missing piece of metadata",
        ],
    }
    raw = client.complete_json(system=_SCOUT_SYSTEM, prompt=json.dumps(payload, indent=2)) or {}

    # Bucket model candidates by KNOWN proof_step_id only.
    by_step: dict[str, list] = {}
    for item in raw.get("proof_step_candidates") or []:
        if not isinstance(item, dict):
            continue
        sid = safe_text(item.get("proof_step_id", "")).strip()
        if sid not in step_summaries:
            continue  # SAFETY: a candidate for an unknown step is dropped, not reassigned
        by_step.setdefault(sid, []).extend(item.get("candidates") or [])

    proof_step_candidates = []
    for idx, (sid, summary) in enumerate(step_summaries.items(), start=1):
        proof_step_candidates.append(
            {
                "proof_step_id": sid,
                "proof_step_summary": summary,
                "candidates": _normalize_candidates(by_step.get(sid, []), step_idx=idx),
            }
        )

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "proof_step_candidates": proof_step_candidates,
        "notes": safe_list(raw.get("notes"))
        or [
            "Literature Scout proposes candidates only. Source support is earned "
            "later by retrieval + extraction + grounding + validation, never here."
        ],
    }
    LiteratureCandidates.model_validate(report)

    write_yaml(run_dir / "literature_candidates.yaml", report)
    manifest.setdefault("optional_artifacts", {})["literature_candidates"] = "literature_candidates.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    # Fail closed: the kernel re-validates the whole run after the scout writes.
    from .surface_warnings import refresh_final_report_disclosures
    from .validators import validate_run

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    return report
