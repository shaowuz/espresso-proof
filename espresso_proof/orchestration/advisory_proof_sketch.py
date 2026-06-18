"""Goal 67B — non-discarding capture of a prover attempt's reasoning.

The Prover stage runs under a whole-run snapshot transaction
(``stage_runner.StageRunner`` for the initial pass). If the round artifact trips
``validate_run`` -- for example a model step smuggles a status field like
``proof_status: ...`` or ``source_backed: true`` past ``safe_text`` (which only
redacts the *value*, not the ``field:`` key, so the smuggle still reaches
``validators._prover_status_smuggling_field``) -- the stage rolls the whole run
directory back and the entire attempt is dropped, *including any genuinely good
mathematical reasoning that preceded the smuggle*. ``agent_events.jsonl`` keeps
only call metadata (provider/model/timestamps), never the reasoning text, so the
math is lost with no trace.

This module captures that reasoning into ``advisory_proof_sketch.yaml`` -- a
snapshot-EXCLUDED sidecar (see ``stage_runner._SNAPSHOT_EXCLUDED_NAMES``) so it
survives the rollback, exactly like ``agent_events.jsonl`` does.

KERNEL SAFETY -- this is a pure DISPLAY/DIAGNOSTIC SINK:
- It is NOT a kernel artifact: it is not in ``models.MODEL_REGISTRY``, is not a
  ``run_manifest`` artifact, and ``validate_run`` does not consume it (mirrors
  ``agent_events.jsonl``). It therefore never upgrades proof / source / evidence
  / grade / refuted / checked status, and the kernel ignores it entirely.
- Its text is scrubbed with the SAME detectors the kernel uses, so the sink can
  never itself carry forbidden proof language or a status smuggle: every step is
  passed through the proof-language / source-support / draft-status scrubbers and
  then any sentence that still trips ``_first_forbidden_proof_language`` /
  ``_prover_status_smuggling_field`` / ``_prover_unchecked_support_misuse`` is
  dropped. The kernel detectors are the safety belt -- correctness does not
  depend on the local field list being exhaustive.
- The name is ``advisory_proof_sketch``, deliberately NOT ``proof_candidate``
  (which is a ``ProofLanguageStatus`` enum member -- using it would be a
  status-upgrade backdoor). Every record is stamped ``trusted: false`` /
  ``advisory_only: true``.
- It is fail-closed: any error during capture returns ``None`` and never breaks
  the run -- a diagnostic must never take down the pipeline it documents.
"""

from __future__ import annotations

import re
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.proof_language import (
    scrub_draft_status_claims,
    scrub_proof_language,
    scrub_source_support_language,
)
from espresso_proof.kernel.stage_runner import ADVISORY_PROOF_SKETCH_FILENAME
from espresso_proof.kernel.validators import (
    _first_forbidden_proof_language,
    _prover_status_smuggling_field,
    _prover_unchecked_support_misuse,
)

__all__ = [
    "ADVISORY_PROOF_SKETCH_FILENAME",
    "capture_advisory_proof_sketch",
    "capture_advisory_proof_sketch_from_latest_round",
    "read_advisory_proof_sketch",
]

ADVISORY_PROOF_SKETCH_NOTE = (
    "Advisory only. Captured from a prover attempt the kernel did not accept. "
    "Nothing here is checked, source-backed, refuted, or proved; it is display "
    "only and never upgrades any status."
)

# Status-field keys a prover round may not assert (mirrors the field list in
# validators._prover_status_smuggling_field). This local copy only widens how
# much surrounding text we can KEEP -- the per-sentence safety belt below, which
# calls the validator's own detector, is what GUARANTEES nothing slips through,
# so drift here cannot weaken the sink.
_SMUGGLING_FIELD_KEYS = (
    "proof_status", "proof_language_status", "source_status", "source_backed_status",
    "source_backed", "evidence_status", "checked_evidence", "theorem_applicability",
    "theorem_applicability_status", "applicability_status", "refutation_status",
    "counterexample_refutes_claim", "refutes_claim", "grade_status",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.;\n])\s+")


def _scrub_sketch_text(text: object) -> str:
    """Return ``text`` with all proof/status language neutralised.

    Layered: (1) existing display scrubbers redact proof-complete / source-backed
    / draft-status verdict phrasing; (2) any ``field:``/``field=`` status smuggle
    is blanked; (3) the SAFETY BELT drops any remaining sentence that still trips
    a kernel language detector. Pure / never raises."""
    if not text:
        return ""
    scrubbed = scrub_draft_status_claims(
        scrub_source_support_language(scrub_proof_language(str(text)))
    )
    for field in _SMUGGLING_FIELD_KEYS:
        scrubbed = re.sub(
            rf"\b{re.escape(field)}\s*[:=]\s*\S+",
            "[withheld status assignment]",
            scrubbed,
            flags=re.IGNORECASE,
        )
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(scrubbed):
        s = sentence.strip()
        if not s:
            continue
        if _first_forbidden_proof_language(s.lower()):
            continue
        if _prover_status_smuggling_field(s):
            continue
        if _prover_unchecked_support_misuse(s):
            continue
        kept.append(s)
    return " ".join(kept).strip()


def capture_advisory_proof_sketch(
    run_dir: Path, agent_report: dict, *, source: str = "prover_attempt"
) -> Path | None:
    """Capture a prover attempt's scrubbed reasoning into the rollback-surviving
    advisory sidecar. ``source`` records provenance only (e.g. the initial
    Prover+Reviewers pass vs a deep continuation round). Returns the path written,
    or ``None`` if there was nothing to capture or anything went wrong
    (fail-closed -- never raises)."""
    try:
        run_dir = Path(run_dir)
        attempt = (agent_report or {}).get("prover_attempt") or {}
        summary = _scrub_sketch_text(attempt.get("output_summary", ""))
        steps = [
            cleaned
            for raw in (attempt.get("proposed_steps") or [])
            if (cleaned := _scrub_sketch_text(raw))
        ]
        if not summary and not steps:
            return None
        sketch = {
            "kind": "advisory_proof_sketch",
            "trusted": False,
            "advisory_only": True,
            "scrubbed": True,
            "source": source,
            "note": ADVISORY_PROOF_SKETCH_NOTE,
            "summary": summary,
            "steps": steps,
        }
        path = run_dir / ADVISORY_PROOF_SKETCH_FILENAME
        write_yaml(path, sketch)
        return path
    except Exception:  # noqa: BLE001 - diagnostic capture must never break a run
        return None


def capture_advisory_proof_sketch_from_latest_round(run_dir: Path) -> Path | None:
    """Goal 67B (deep-round path): capture the most recent prover-loop ROUND's
    reasoning into the advisory sidecar.

    The deep continuation loop (``prover_loop_driver.continue_prover_loop``) writes
    each round to ``prover_rounds/round_*.yaml`` and rolls a rejected round back via
    ``_restore_run`` (which unlinks the round artifact). Reading the latest round
    here, BEFORE that rollback, and writing the (root) sidecar preserves the math:
    ``_restore_run`` never blanket-deletes root files, so the sidecar survives.

    Pure sink: reuses ``capture_advisory_proof_sketch`` (scrub with the kernel's
    own detectors, ``trusted: false``, no status upgrade). Fail-closed."""
    try:
        run_dir = Path(run_dir)
        rounds_dir = run_dir / "prover_rounds"
        if not rounds_dir.is_dir():
            return None
        latest: dict | None = None
        latest_idx = -1
        for f in rounds_dir.glob("round_*.yaml"):
            data = read_yaml(f)
            if not isinstance(data, dict):
                continue
            idx = data.get("round_index")
            if isinstance(idx, int) and idx > latest_idx:
                latest_idx, latest = idx, data
        if latest is None:
            return None
        return capture_advisory_proof_sketch(
            run_dir,
            {"prover_attempt": latest.get("prover_attempt")},
            source="prover_loop_round",
        )
    except Exception:  # noqa: BLE001 - diagnostic capture must never break a run
        return None


def read_advisory_proof_sketch(run_dir: Path) -> dict | None:
    """Read the advisory sketch sidecar if present, else ``None`` (fail-closed)."""
    try:
        path = Path(run_dir) / ADVISORY_PROOF_SKETCH_FILENAME
        if not path.exists():
            return None
        data = read_yaml(path)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
