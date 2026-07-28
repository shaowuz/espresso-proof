"""Theorem Condition Ledger Builder (slice 1).

Reads a run's already-validated artifacts and assembles a conservative, advisory
ledger (``theorem_condition_ledger.yaml``) of the theorems a reviewer asked for
(the verbatim ``source_requests``) together with the conditions/hypotheses that
still have to be checked against the current claim before any such theorem could
ever be reused as support.

The ledger is search-management bookkeeping, NOT proof evidence. It can never set
or upgrade source / evidence / grade / claim status, and it carries no field that
could. Every requested-theorem string and every condition string is copied
VERBATIM; ``match_status`` starts ``unknown`` and is NEVER inferred from the
claim text (this builder does no matching at all — that is a later slice). The
builder fails closed (validates the model before writing) and is idempotent.

See the theorem-condition ledger design notes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import TheoremConditionLedger

LEDGER_FILENAME = "theorem_condition_ledger.yaml"

# Placeholder condition emitted when a reviewer requested a theorem but listed no
# missing_hypotheses: an entry must always carry >=1 condition so the "what must
# be checked" obligation is never silently empty.
_PLACEHOLDER_CONDITION = "Hypotheses to check against the claim"


def _collect_request_strings(llm_agent_run: dict | None) -> tuple[list[str], list[str]]:
    """Aggregate the verbatim reviewer ``source_requests`` and ``missing_hypotheses``.

    Reads every ``verifier_reviews[]`` entry and also tolerates the same two lists
    appearing at the top level of the artifact. Returns ``(source_requests,
    missing_hypotheses)`` with order preserved and exact duplicates dropped, all
    strings copied verbatim (no scrubbing, no inference)."""

    source_requests: list[str] = []
    missing_hypotheses: list[str] = []

    def extend(target: list[str], values: Any) -> None:
        for value in values or []:
            text = str(value)
            if text not in target:
                target.append(text)

    data = llm_agent_run or {}
    # Tolerate top-level lists in addition to per-review lists.
    extend(source_requests, data.get("source_requests"))
    extend(missing_hypotheses, data.get("missing_hypotheses"))
    for review in data.get("verifier_reviews") or []:
        if not isinstance(review, dict):
            continue
        extend(source_requests, review.get("source_requests"))
        extend(missing_hypotheses, review.get("missing_hypotheses"))

    return source_requests, missing_hypotheses


def assemble_theorem_condition_ledger(run_dir: Path) -> dict:
    """Read a run's artifacts and return a ledger dict (no I/O side effects).

    Only reads; never mutates artifacts and never sets any trusted status. The
    returned dict carries a ``generated_at`` placeholder that
    ``build_theorem_condition_ledger`` finalises for idempotency.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    claim = read_yaml(run_dir / artifacts["locked_claim"])

    llm_agent_run: dict | None = None
    agent_path = optional.get("llm_agent_run")
    if agent_path and (run_dir / agent_path).exists():
        loaded = read_yaml(run_dir / agent_path)
        if isinstance(loaded, dict):
            llm_agent_run = loaded

    source_requests, missing_hypotheses = _collect_request_strings(llm_agent_run)

    # One condition per missing_hypotheses string (verbatim, match_status=unknown);
    # if none were listed, a single placeholder keeps the "must be checked"
    # obligation visible. match_status is NEVER inferred from claim text.
    if missing_hypotheses:
        conditions = [
            {"text": hypothesis, "claim_value": "", "match_status": "unknown"}
            for hypothesis in missing_hypotheses
        ]
    else:
        conditions = [
            {"text": _PLACEHOLDER_CONDITION, "claim_value": "", "match_status": "unknown"}
        ]

    entries: list[dict] = []
    for index, request in enumerate(source_requests, start=1):
        entries.append(
            {
                "id": f"tcl_{index}",
                "requested_theorem": request,
                "locator_status": "needs_exact_locator",
                "locator": None,
                # A fresh copy per entry so entries never share a mutable list.
                "conditions": [dict(condition) for condition in conditions],
                "drift_kind": "",
                "repair_subgoal_id": f"subgoal_tcl_{index}",
                "source_request_origin": "reviewer",
            }
        )

    return {
        "schema_version": 1,
        "claim_id": str(claim.get("id")),
        "run_id": str(manifest.get("run_id")),
        "generated_at": "",  # finalised by build_theorem_condition_ledger
        "entries": entries,
        "notes": [],
    }


def build_theorem_condition_ledger(run_dir: Path) -> dict:
    """Assemble, validate, and write ``theorem_condition_ledger.yaml``; register it
    as an optional artifact and return the ledger dict.

    Choice on the empty case: when ``llm_agent_run`` is absent or has no
    ``source_requests``, we still WRITE a valid ledger with ``entries: []`` (rather
    than skip). This keeps ``validate_run`` happy either way (the artifact's
    manifest path always exists, and an empty ledger is a valid model) and makes
    the layer's presence deterministic for later slices.

    Idempotent: if an equivalent ledger already exists (everything but
    ``generated_at``), the previous timestamp is preserved so a re-build leaves the
    artifact byte-identical.
    """

    run_dir = Path(run_dir)
    ledger = assemble_theorem_condition_ledger(run_dir)

    ledger_path = run_dir / LEDGER_FILENAME
    generated_at = datetime.now(UTC).isoformat()
    if ledger_path.exists():
        try:
            existing = read_yaml(ledger_path)
        except Exception:  # noqa: BLE001 - a corrupt prior file is simply replaced
            existing = None
        if isinstance(existing, dict):
            previous_stamp = existing.get("generated_at")
            if {**existing, "generated_at": ""} == {**ledger, "generated_at": ""} and previous_stamp:
                generated_at = str(previous_stamp)
    ledger["generated_at"] = generated_at

    # Fail closed on a malformed ledger before touching the run.
    TheoremConditionLedger.model_validate(ledger)

    write_yaml(ledger_path, ledger)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["theorem_condition_ledger"] = LEDGER_FILENAME
    write_yaml(manifest_path, manifest)
    return ledger


__all__ = [
    "LEDGER_FILENAME",
    "assemble_theorem_condition_ledger",
    "build_theorem_condition_ledger",
]
