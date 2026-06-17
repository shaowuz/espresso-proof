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

from .io import write_yaml
from .proof_language import (
    scrub_draft_status_claims,
    scrub_proof_language,
    scrub_source_support_language,
)
from .stage_runner import ADVISORY_PROOF_SKETCH_FILENAME
from .validators import (
    _first_forbidden_proof_language,
    _prover_status_smuggling_field,
    _prover_unchecked_support_misuse,
)

__all__ = ["ADVISORY_PROOF_SKETCH_FILENAME", "capture_advisory_proof_sketch", "read_advisory_proof_sketch"]

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


def capture_advisory_proof_sketch(run_dir: Path, agent_report: dict) -> Path | None:
    """Capture a prover attempt's scrubbed reasoning into the rollback-surviving
    advisory sidecar. Returns the path written, or ``None`` if there was nothing
    to capture or anything went wrong (fail-closed -- never raises)."""
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
            "source": "prover_attempt",
            "note": ADVISORY_PROOF_SKETCH_NOTE,
            "summary": summary,
            "steps": steps,
        }
        path = run_dir / ADVISORY_PROOF_SKETCH_FILENAME
        write_yaml(path, sketch)
        return path
    except Exception:  # noqa: BLE001 - diagnostic capture must never break a run
        return None


def read_advisory_proof_sketch(run_dir: Path) -> dict | None:
    """Read the advisory sketch sidecar if present, else ``None`` (fail-closed)."""
    try:
        from .io import read_yaml

        path = Path(run_dir) / ADVISORY_PROOF_SKETCH_FILENAME
        if not path.exists():
            return None
        data = read_yaml(path)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
