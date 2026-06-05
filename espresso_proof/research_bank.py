"""Per-item research bank V0 (storage/layout only).

Three research artifacts may optionally be stored as a DIRECTORY of one file per
item instead of a single YAML file, so multiple agents can append findings
concurrently without editing the same file (single writer *per item file*)::

    examples/            counterexamples/        strategies/
      _meta.yaml           _meta.yaml              _meta.yaml
      00-<id>.yaml         00-<id>.yaml            00-<id>.yaml
      01-<id>.yaml         ...                     ...

``_meta.yaml`` holds the bank's non-list fields (always ``claim_id``, plus a
``kind`` discriminator, plus the scalar/list metadata for strategies). Each
``NN-<id>.yaml`` holds exactly one item. The ``NN`` ordinal prefix preserves list
order, so the aggregated model is equivalent to the old single-file artifact.

SAFETY BOUNDARY: this is pure storage/layout. It only reads/writes YAML and
validates against the EXISTING item / aggregate models. It changes no
proof/source/evidence/grade/theorem-applicability/counterexample semantics: an
item is trusted exactly as much as the existing model + downstream validators
already allow, never because an agent wrote a file. All cross-artifact
validation still runs downstream on the aggregated model in ``validators.py``.

The concept (per-item bank dirs + ``_meta.yaml`` + ``NN-<id>.yaml`` + an
add-bank-item tool) is borrowed from @yangjunjie0320's PR #7; this is a small, focused
re-implementation on current ``main`` (no dashboard, no prompt changes, no
fixture migration), not a merge of that branch.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .io import read_yaml, write_yaml
from .models import (
    CounterexampleReport,
    CounterexampleTest,
    ExampleSelection,
    StrategyBriefItem,
    StrategySynthesis,
    TestedExample,
)
from .validation.errors import KernelValidationError

META_FILENAME = "_meta.yaml"


@dataclass(frozen=True)
class BankSpec:
    """How one bank maps between its aggregated model and its item files."""

    kind: str
    model_cls: type[BaseModel]
    item_cls: type[BaseModel]
    list_field: str
    meta_fields: tuple[str, ...]
    id_field: str = "id"


# Keyed by the ``kind`` discriminator stored in ``_meta.yaml`` (also the natural
# directory name). Directory name itself is free — the manifest points at it.
BANK_SPECS: dict[str, BankSpec] = {
    "examples": BankSpec(
        kind="examples",
        model_cls=ExampleSelection,
        item_cls=TestedExample,
        list_field="examples",
        meta_fields=("claim_id",),
    ),
    "counterexamples": BankSpec(
        kind="counterexamples",
        model_cls=CounterexampleReport,
        item_cls=CounterexampleTest,
        list_field="tests",
        meta_fields=("claim_id",),
    ),
    "strategies": BankSpec(
        kind="strategies",
        model_cls=StrategySynthesis,
        item_cls=StrategyBriefItem,
        list_field="routes",
        meta_fields=("claim_id", "synthesized_from", "discarded_routes", "synthesis_note"),
    ),
}


def is_bank_dir(path: Path) -> bool:
    """A path is a research bank iff it is a directory containing ``_meta.yaml``."""

    path = Path(path)
    return path.is_dir() and (path / META_FILENAME).is_file()


def _spec_for_meta(meta: dict, *, bank_dir: Path) -> BankSpec:
    kind = meta.get("kind")
    spec = BANK_SPECS.get(str(kind))
    if spec is None:
        raise KernelValidationError(
            f"{bank_dir / META_FILENAME}: unknown or missing bank kind {kind!r} "
            f"(expected one of {sorted(BANK_SPECS)})"
        )
    return spec


def _item_files(bank_dir: Path) -> list[Path]:
    """Item YAML files in filename order (the ``NN`` prefix preserves list order)."""

    return sorted(
        p
        for p in bank_dir.glob("*.yaml")
        # Skip _meta.yaml and any in-flight temp file (".tmp-bankitem-*.yaml") so a
        # concurrent reader never tries to validate a half-written item.
        if p.name != META_FILENAME and not p.name.startswith(".") and p.is_file()
    )


def _validate_item(path: Path, spec: BankSpec) -> dict:
    data = read_yaml(path)
    try:
        return spec.item_cls.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        raise KernelValidationError(
            f"{path}: invalid {spec.kind} item ({spec.item_cls.__name__})\n{exc}"
        ) from exc


def load_bank(bank_dir: Path) -> BaseModel:
    """Aggregate a bank directory into its existing validated model.

    Items are read in filename order; each is validated against the existing item
    model; duplicate item ids fail; the aggregated dict is validated against the
    existing aggregate model. Returns the validated aggregate model instance.
    """

    bank_dir = Path(bank_dir)
    meta_path = bank_dir / META_FILENAME
    if not meta_path.is_file():
        raise KernelValidationError(f"{bank_dir}: bank directory is missing {META_FILENAME}")
    meta = read_yaml(meta_path) or {}
    spec = _spec_for_meta(meta, bank_dir=bank_dir)

    items: list[dict] = []
    seen_ids: set[str] = set()
    for path in _item_files(bank_dir):
        item = _validate_item(path, spec)
        item_id = str(item.get(spec.id_field, ""))
        if item_id in seen_ids:
            raise KernelValidationError(
                f"{path}: duplicate {spec.kind} item id {item_id!r} in bank {bank_dir}"
            )
        seen_ids.add(item_id)
        items.append(item)

    missing_meta = [field for field in spec.meta_fields if field not in meta]
    if missing_meta:
        raise KernelValidationError(
            f"{meta_path}: bank _meta.yaml is missing required field(s) {missing_meta}"
        )
    aggregated = {field: meta[field] for field in spec.meta_fields}
    aggregated[spec.list_field] = items
    try:
        return spec.model_cls.model_validate(aggregated)
    except ValidationError as exc:
        raise KernelValidationError(
            f"{bank_dir}: aggregated {spec.kind} bank failed {spec.model_cls.__name__} validation\n{exc}"
        ) from exc


def load_artifact(path: Path, *, expected_kind: str) -> BaseModel:
    """Load either a single-file YAML artifact or a per-item bank directory.

    Backward compatible: a plain ``*.yaml`` file validates exactly as before; a
    directory is aggregated via :func:`load_bank`. ``expected_kind`` cross-checks
    the bank's declared kind so a manifest cannot point an artifact slot at the
    wrong bank.
    """

    path = Path(path)
    spec = BANK_SPECS[expected_kind]
    if path.is_dir():
        meta = read_yaml(path / META_FILENAME) if (path / META_FILENAME).is_file() else {}
        if str(meta.get("kind")) != expected_kind:
            raise KernelValidationError(
                f"{path}: bank kind {meta.get('kind')!r} does not match expected {expected_kind!r}"
            )
        return load_bank(path)
    try:
        return spec.model_cls.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc


def load_examples_artifact(path: Path) -> ExampleSelection:
    return load_artifact(path, expected_kind="examples")  # type: ignore[return-value]


def load_counterexamples_artifact(path: Path) -> CounterexampleReport:
    return load_artifact(path, expected_kind="counterexamples")  # type: ignore[return-value]


def load_strategy_synthesis_artifact(path: Path) -> StrategySynthesis:
    return load_artifact(path, expected_kind="strategies")  # type: ignore[return-value]


# Manifest artifact key -> bank kind, for the three bankable artifacts. Used by
# manifest-aware consumers so a run works whether the artifact is a single YAML
# file or a per-item bank directory (after `materialize-banks`).
_BANKABLE_KINDS: dict[str, str] = {
    "examples": "examples",
    "counterexamples": "counterexamples",
    "strategy_synthesis": "strategies",
}


def bankable_artifact_path(run_dir: Path, key: str, manifest: dict | None = None) -> Path:
    """Resolve a bankable artifact's on-disk path (single file OR bank dir) via
    the run manifest. Falls back to the conventional ``<key>.yaml`` name when the
    manifest does not list the key (back-compat / partial manifests)."""

    run_dir = Path(run_dir)
    if manifest is None:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = (manifest.get("artifacts", {}) or {}).get(key, f"{key}.yaml")
    return run_dir / rel


def load_bankable_artifact(run_dir: Path, key: str, manifest: dict | None = None) -> BaseModel:
    """Load a bankable artifact through the manifest + the bank-aware loader.

    Works for both layouts. Fails closed exactly like a hard-coded read would
    (a missing path raises while reading), and never fabricates an empty model.
    """

    if key not in _BANKABLE_KINDS:
        raise KernelValidationError(f"load_bankable_artifact: {key!r} is not a bankable artifact")
    return load_artifact(
        bankable_artifact_path(run_dir, key, manifest), expected_kind=_BANKABLE_KINDS[key]
    )


def load_bankable_dict(run_dir: Path, key: str, manifest: dict | None = None) -> dict:
    """Same as :func:`load_bankable_artifact` but returns the aggregated dict --
    identical in shape to ``read_yaml`` of the old single-file artifact, so it is
    a drop-in replacement for hard-coded single-file reads."""

    return load_bankable_artifact(run_dir, key, manifest).model_dump(mode="json")


def aggregate_bank_dict(bank_dir: Path) -> dict:
    """Lenient aggregation for READ-ONLY DISPLAY (snapshot / detail previews).

    Reads ``_meta.yaml`` + item files into the same aggregated dict shape as the
    old single-file artifact, WITHOUT model validation -- mirroring the leniency
    of ``read_yaml`` on a single file (the strict path is :func:`load_bank` /
    ``validate_run``). Item order follows the ``NN`` filename prefix. An unknown
    bank kind degrades to exposing the raw items under ``items``."""

    bank_dir = Path(bank_dir)
    meta = read_yaml(bank_dir / META_FILENAME) or {}
    items = [read_yaml(p) for p in _item_files(bank_dir)]
    spec = BANK_SPECS.get(str(meta.get("kind")))
    if spec is None:
        return {**{k: v for k, v in meta.items() if k != "kind"}, "items": items}
    out: dict = {field: meta.get(field) for field in spec.meta_fields}
    out[spec.list_field] = items
    return out


# Manifest artifact key -> (bank kind, conventional bank directory name). Used by
# the opt-in bank-output mode; the single-file layout remains the default.
BANK_OUTPUT_LAYOUT: dict[str, tuple[str, str]] = {
    "examples": ("examples", "examples"),
    "counterexamples": ("counterexamples", "counterexamples"),
    "strategy_synthesis": ("strategies", "strategies"),
}


def write_bank(bank_dir: Path, aggregate, *, kind: str) -> Path:
    """Write an aggregated model (or dict) out as a per-item bank directory.

    Validates the aggregate against the existing model first, then writes
    ``_meta.yaml`` (the non-list fields + ``kind``) and one ``NN-<id>.yaml`` per
    item, and finally re-loads the bank to confirm it round-trips to the same
    model (fail closed). Refuses to write into a directory that already holds a
    bank or item files. Adds no status and changes no item content.
    """

    spec = BANK_SPECS.get(kind)
    if spec is None:
        raise KernelValidationError(f"write_bank: unknown bank kind {kind!r}")
    bank_dir = Path(bank_dir)
    data = aggregate if isinstance(aggregate, dict) else aggregate.model_dump(mode="json")
    try:
        validated = spec.model_cls.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        raise KernelValidationError(
            f"write_bank: aggregate is not a valid {spec.model_cls.__name__}\n{exc}"
        ) from exc

    bank_dir.mkdir(parents=True, exist_ok=True)
    if (bank_dir / META_FILENAME).exists() or _item_files(bank_dir):
        raise KernelValidationError(f"{bank_dir}: refusing to write into a non-empty bank directory")

    meta = {"kind": kind}
    for field in spec.meta_fields:
        meta[field] = validated[field]
    write_yaml(bank_dir / META_FILENAME, meta)

    seen_ids: set[str] = set()
    for index, item in enumerate(validated[spec.list_field]):
        item_id = str(item.get(spec.id_field, ""))
        if item_id in seen_ids:
            raise KernelValidationError(
                f"{bank_dir}: duplicate {kind} item id {item_id!r} while writing bank"
            )
        seen_ids.add(item_id)
        write_yaml(bank_dir / f"{index:02d}-{_safe_id_for_filename(item_id)}.yaml", item)

    # Fail closed: the written bank must aggregate back to the same model.
    if load_bank(bank_dir).model_dump(mode="json") != validated:
        raise KernelValidationError(f"{bank_dir}: bank round-trip does not match the source model")
    return bank_dir


def materialize_run_banks(run_dir: Path, *, keys: list[str] | None = None) -> list[str]:
    """Opt-in: convert a run's single-file research artifacts into bank dirs.

    For each requested artifact key (default: all three bankable ones), reads the
    existing single YAML via the bank-aware loader, writes a bank directory,
    removes the old single file, and repoints BOTH the manifest artifact and the
    matching ``agent_contracts`` output to the directory (so contract path checks
    still pass). Idempotent: an artifact already pointing at a bank dir is skipped.
    Does not change item content or any status. The caller runs ``validate_run``.

    Returns the list of artifact keys that were converted.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    contracts = read_yaml(run_dir / manifest["artifacts"]["agent_contracts"])
    target_keys = keys or list(BANK_OUTPUT_LAYOUT)

    # Plan the conversions and load each aggregate up front.
    plan: list[tuple[str, str, str, str, object]] = []
    for key in target_keys:
        if key not in BANK_OUTPUT_LAYOUT:
            raise KernelValidationError(f"materialize_run_banks: {key!r} is not a bankable artifact")
        kind, dir_name = BANK_OUTPUT_LAYOUT[key]
        old_value = manifest["artifacts"][key]
        if (run_dir / old_value).is_dir():
            continue  # already a bank directory (idempotent)
        aggregate = load_artifact(run_dir / old_value, expected_kind=kind)
        plan.append((key, old_value, dir_name, kind, aggregate))

    # Phase 1 (non-destructive): write every bank directory first. If any write
    # fails, the original single files + manifest are untouched, so the run stays
    # valid (only an unreferenced stray dir may remain).
    for _key, _old, dir_name, kind, aggregate in plan:
        write_bank(run_dir / dir_name, aggregate, kind=kind)

    # Phase 2 (commit): remove old single files and repoint the manifest +
    # agent_contracts. Only unlinks/writes, which do not fail under normal use.
    for key, old_value, dir_name, _kind, _agg in plan:
        (run_dir / old_value).unlink()
        manifest["artifacts"][key] = dir_name
        for contract in contracts.get("contracts", []):
            if contract.get("output_artifact") == old_value:
                contract["output_artifact"] = dir_name

    if plan:
        write_yaml(run_dir / manifest["artifacts"]["agent_contracts"], contracts)
        write_yaml(run_dir / "run_manifest.yaml", manifest)
    return [key for key, _o, _d, _k, _a in plan]


def _safe_id_for_filename(item_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", item_id).strip("_")
    return cleaned or "item"


# Bounded retry when two appenders race for the same ordinal slot. Far above any
# realistic per-bank item count; a runaway loop fails closed rather than spinning.
_MAX_APPEND_ATTEMPTS = 10_000


def _atomic_link(src: Path, dst: Path) -> bool:
    """Atomically, EXCLUSIVELY place ``src`` content at ``dst`` via a hardlink.

    Returns ``True`` on success, ``False`` if ``dst`` already exists. ``os.link``
    is used instead of ``os.rename`` precisely because ``rename`` would silently
    OVERWRITE an existing item file under a race, and a bank must never rewrite an
    existing item file. The link is atomic on a single filesystem (``src`` and
    ``dst`` share the bank directory).
    """

    try:
        os.link(src, dst)
        return True
    except FileExistsError:
        return False


def append_bank_item(bank_dir: Path, item: dict, *, id_field: str = "id") -> Path:
    """Append one validated item to a bank as a NEW file, concurrency-safe.

    Validates the item against the bank's item model BEFORE writing any final
    file, refuses a duplicate id, writes the content to a TEMP file, then
    atomically and exclusively places it at the next free ``NN-<id>.yaml`` ordinal
    (retrying a later ordinal if two appenders raced for the same slot — never
    overwriting an existing item file). Re-aggregates to confirm the bank still
    validates (rolling back the new file on failure). The temp file is always
    cleaned up. Returns the written path.

    Note: the ``NN`` ordinal prefix is best-effort under true concurrency. Two
    appenders with different ids racing for the same slot produce two distinct
    filenames at the same ordinal (no overwrite, no lost item); loading stays
    deterministic via the full-filename sort. The exclusive placement guarantees
    only that an existing item file is never rewritten.
    """

    bank_dir = Path(bank_dir)
    meta_path = bank_dir / META_FILENAME
    if not meta_path.is_file():
        raise KernelValidationError(f"{bank_dir}: bank directory is missing {META_FILENAME}")
    meta = read_yaml(meta_path) or {}
    spec = _spec_for_meta(meta, bank_dir=bank_dir)

    if not isinstance(item, dict):
        raise KernelValidationError(f"{bank_dir}: bank item must be a mapping, got {type(item).__name__}")
    try:
        validated = spec.item_cls.model_validate(item).model_dump(mode="json")
    except ValidationError as exc:
        raise KernelValidationError(
            f"{bank_dir}: cannot append invalid {spec.kind} item ({spec.item_cls.__name__})\n{exc}"
        ) from exc

    item_id = str(validated.get(id_field, ""))
    if not item_id:
        raise KernelValidationError(f"{bank_dir}: appended item is missing id field {id_field!r}")

    existing = _item_files(bank_dir)
    for path in existing:
        if str(read_yaml(path).get(id_field, "")) == item_id:
            raise KernelValidationError(
                f"{bank_dir}: duplicate {spec.kind} item id {item_id!r} (already in {path.name})"
            )

    # Write the content to a temp file in the SAME directory, then atomically and
    # exclusively place it at a free ordinal. Only a fully-written file is ever
    # linked into place, so a concurrent reader never sees a partial item.
    safe_id = _safe_id_for_filename(item_id)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-bankitem-", suffix=".yaml", dir=bank_dir)
    os.close(fd)
    tmp_path = Path(tmp_name)
    new_path: Path | None = None
    try:
        write_yaml(tmp_path, validated)
        index = len(existing)
        for _ in range(_MAX_APPEND_ATTEMPTS):
            candidate = bank_dir / f"{index:02d}-{safe_id}.yaml"
            if _atomic_link(tmp_path, candidate):
                new_path = candidate
                break
            index += 1
        if new_path is None:
            raise KernelValidationError(
                f"{bank_dir}: could not place bank item after {_MAX_APPEND_ATTEMPTS} attempts"
            )
    finally:
        tmp_path.unlink(missing_ok=True)  # always remove the temp file

    try:
        load_bank(bank_dir)  # fail closed: the bank must still aggregate validly
    except KernelValidationError:
        new_path.unlink(missing_ok=True)
        raise
    return new_path
