"""Source-grounding stage (Phase 05).

One reusable, auditable pre-prover stage for HUMAN-PROVIDED source grounding,
shared by the resume CLI (and available to other callers) instead of inline CLI
plumbing. It runs inside the transactional ``StageRunner`` so a bad source /
incomplete metadata / failed validation fails closed and leaves the run at its
prior valid state — it never enters prover continuation and never upgrades
source/proof/evidence status.

Status rules are unchanged and owned by the existing kernel: a step earns
``source_backed`` only via the deterministic grounding rules (exact locator +
attribution + hypothesis match + theorem-applicability audit). Literature,
memory, web search, LLM prose, and human route preference can only PROPOSE
sources here; they cannot certify them.
"""

from __future__ import annotations

from pathlib import Path

from .claim_clear import ensure_claim_clear_for_downstream
from .io import read_yaml, write_yaml
from .models import TheoremExtractionReport
from .pdf_ingestion import ingest_source_for_theorems
from .source_verification import ground_run
from .stage_runner import StageRunner
from .validators import validate_run

THEOREM_EXTRACTION_FILENAME = "theorem_extraction.yaml"


def prepare_source_grounding(
    *,
    run_dir: Path,
    ground_source: str | Path,
    source_id: str = "source_001",
    title: str = "Ingested source",
    authors: list[str] | None = None,
    source_type: str = "paper",
) -> None:
    """Ground a run's proof steps against a human-provided source, transactionally.

    Ingests the source for candidate theorems, registers/writes
    ``theorem_extraction.yaml``, runs the existing deterministic grounding
    pipeline (which writes ``source_grounding.yaml`` / optional
    ``theorem_applicability.yaml`` and earns ``source_backed`` only when the
    grounding rules are satisfied), then re-validates. On any failure the run is
    rolled back to its prior valid state (fail closed).
    """

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])

    def _mutate() -> None:
        extraction = ingest_source_for_theorems(
            claim_id=claim["id"],
            source=str(ground_source),
            source_id=source_id,
            title=title,
            authors=authors or [],
            source_type=source_type,
        )
        TheoremExtractionReport.model_validate(extraction)
        stage_manifest = read_yaml(run_dir / "run_manifest.yaml")
        stage_manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = (
            THEOREM_EXTRACTION_FILENAME
        )
        write_yaml(run_dir / THEOREM_EXTRACTION_FILENAME, extraction)
        write_yaml(run_dir / "run_manifest.yaml", stage_manifest)
        # The deterministic grounding pipeline owns source_backed status.
        ground_run(
            run_dir=run_dir,
            theorem_extraction_path=run_dir / THEOREM_EXTRACTION_FILENAME,
        )

    # Whole-run snapshot: grounding creates theorem_extraction.yaml and may write
    # source_grounding / theorem_applicability; all must be dropped on rollback.
    StageRunner(run_dir).run(
        name="source_grounding",
        mutate=_mutate,
        validate=lambda: validate_run(run_dir),
    )


__all__ = ["prepare_source_grounding", "THEOREM_EXTRACTION_FILENAME"]
