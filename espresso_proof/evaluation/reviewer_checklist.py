"""Reviewer quality calibration V1.

Deterministic, artifact-grounded helpers that make reviewer (Engineering /
Kernel Safety / Math) outputs more consistent. None of this earns or changes any
status: it only checks that a review is artifact-grounded and that the run does
not over-claim, and it surfaces the existing kernel guarantees to the reviewer.

Calibration rules encoded here:

- a finding must cite an artifact path/id;
- a finding must declare severity blocker vs non-blocker;
- proof-complete language must be flagged wherever it appears in user-facing
  output;
- a ``source_backed`` ref/step must have an exact locator + attribution + a
  hypothesis match (otherwise it is flagged as an invalid claim);
- non-blocking suggestions do not block a merge.
"""

from __future__ import annotations

import re
from pathlib import Path

from espresso_proof.evaluation.benchmark import FORBIDDEN_PHRASES
from espresso_proof.kernel.io import read_text, read_yaml

# Re-export the canonical proof-complete marker list so reviewers and the
# benchmark stay in sync (specific markers, NOT bare "proved"/"verified").
PROOF_COMPLETE_MARKERS = FORBIDDEN_PHRASES

BLOCKER = "blocker"
NON_BLOCKER = "non_blocker"

# A finding should reference an artifact filename / id. Conservative recogniser:
# a *.yaml / *.md artifact name, or an id-like token (e.g. thm_001, ps_center,
# strategy_route_001, claim_001).
_ARTIFACT_PATTERN = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:yaml|yml|md)\b|\b[a-z][a-z0-9]*_[a-z0-9_]+\b"
)


def _locator_is_exact(locator: dict) -> bool:
    """Reuse the canonical Locator.is_exact (single source of truth) so the
    reviewer check matches the kernel gate, incl. tbd/to_be_filled sentinels.
    Falls back to a conservative literal check if the dict does not validate."""

    from pydantic import ValidationError

    from espresso_proof.kernel.models import Locator

    try:
        return Locator.model_validate(locator).is_exact
    except ValidationError:
        return False


def detect_proof_complete_language(text: str) -> list[str]:
    """Return the proof-complete markers present in ``text`` (case-insensitive).

    Uses the specific marker list, so legitimate conservative phrasings such as
    "unproved" / "not verified" / "no counterexample has been verified" are not
    flagged."""

    lowered = (text or "").lower()
    return [marker for marker in PROOF_COMPLETE_MARKERS if marker in lowered]


def find_invalid_source_backed(source_grounding: dict) -> list[dict]:
    """Flag any ``source_backed`` ref/step lacking exact locator / attribution /
    hypothesis match. (The kernel validator already rejects these at parse time;
    this is the reviewer-facing surfacing so a review can cite the exact ref.)"""

    refs = {r.get("id"): r for r in source_grounding.get("source_refs", [])}
    problems: list[dict] = []
    for ref_id, ref in refs.items():
        if ref.get("status") != "source_backed":
            continue
        reasons: list[str] = []
        if not _locator_is_exact(ref.get("locator") or {}):
            reasons.append("missing exact locator")
        if not [a for a in (ref.get("authors") or []) if str(a).strip()]:
            reasons.append("missing attribution (author)")
        if not str(ref.get("title", "")).strip():
            reasons.append("missing attribution (title)")
        if (ref.get("hypothesis_match") or {}).get("matches") is not True:
            reasons.append("hypothesis match not confirmed")
        if reasons:
            problems.append({"ref_id": ref_id, "reasons": reasons})
    for step in source_grounding.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        if (step.get("hypothesis_match") or {}).get("matches") is not True:
            problems.append(
                {"proof_step_id": step.get("proof_step_id"), "reasons": ["hypothesis match not confirmed"]}
            )
    return problems


def lint_reviewer_finding(finding: dict) -> dict:
    """Calibrate one reviewer finding. Returns
    ``{severity, cites_artifact, well_formed, problems}``.

    A well-formed finding cites an artifact path/id and declares a recognised
    severity (blocker / non-blocker). Severity defaults to ``non_blocker`` when a
    finding does not explicitly claim ``blocker`` — so an unmarked suggestion
    never blocks a merge by accident."""

    text = str(finding.get("text", "")).strip()
    raw_severity = str(finding.get("severity", "")).strip().lower()
    severity = BLOCKER if raw_severity in {"blocker", "block", "required"} else NON_BLOCKER
    cites_artifact = bool(finding.get("artifact")) or bool(_ARTIFACT_PATTERN.search(text))
    problems: list[str] = []
    if not cites_artifact:
        problems.append("finding does not cite an artifact path/id")
    if raw_severity and raw_severity not in {"blocker", "block", "required", "non_blocker", "non-blocker", "nit", "suggestion"}:
        problems.append(f"unrecognised severity {raw_severity!r}")
    return {
        "severity": severity,
        "cites_artifact": cites_artifact,
        "well_formed": not problems,
        "problems": problems,
    }


def merge_allowed(findings: list[dict]) -> bool:
    """A merge is allowed iff no finding is a blocker. Non-blocking suggestions
    (nits / recommendations) never block a merge."""

    return not any(lint_reviewer_finding(f)["severity"] == BLOCKER for f in findings)


def review_checklist(run_dir: Path) -> dict:
    """Run the artifact-grounded reviewer checklist on a run (read-only).

    Surfaces: proof-complete language in the final report (with the artifact
    cited), and invalid source_backed refs/steps. Returns a structured checklist
    that a reviewer can attach; it earns no status and mutates nothing."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    findings: list[dict] = []

    final_report_rel = artifacts.get("final_report", "final_report.md")
    final_report_path = run_dir / final_report_rel
    if final_report_path.exists():
        markers = detect_proof_complete_language(read_text(final_report_path))
        if markers:
            findings.append(
                {
                    "severity": BLOCKER,
                    "artifact": final_report_rel,
                    "text": f"{final_report_rel}: proof-complete language present: {markers}",
                }
            )

    sg_rel = artifacts.get("source_grounding")
    if sg_rel and (run_dir / sg_rel).exists():
        invalid = find_invalid_source_backed(read_yaml(run_dir / sg_rel))
        for item in invalid:
            cite = item.get("ref_id") or item.get("proof_step_id")
            findings.append(
                {
                    "severity": BLOCKER,
                    "artifact": sg_rel,
                    "text": f"{sg_rel}: invalid source_backed {cite}: {item['reasons']}",
                }
            )

    return {
        "run_dir": str(run_dir),
        "findings": findings,
        "merge_allowed": merge_allowed(findings),
        "notes": [
            "Reviewer checklist is artifact-grounded and read-only; it earns no "
            "status and never asserts a claim is proved.",
            "Every finding cites an artifact and declares blocker vs non-blocker.",
        ],
    }
