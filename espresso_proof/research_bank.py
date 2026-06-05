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

import re
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
        p for p in bank_dir.glob("*.yaml") if p.name != META_FILENAME and p.is_file()
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


def _safe_id_for_filename(item_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", item_id).strip("_")
    return cleaned or "item"


def append_bank_item(bank_dir: Path, item: dict, *, id_field: str = "id") -> Path:
    """Append one validated item to a bank as a NEW file, never rewriting others.

    Validates the item against the bank's item model BEFORE writing, refuses a
    duplicate id, writes exactly one ``NN-<id>.yaml`` (next ordinal), and
    re-aggregates to confirm the bank still validates (rolling back the new file
    on failure). Returns the written path.
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

    next_index = len(existing)
    new_path = bank_dir / f"{next_index:02d}-{_safe_id_for_filename(item_id)}.yaml"
    if new_path.exists():  # extremely unlikely; never clobber an existing file
        raise KernelValidationError(f"{new_path}: refusing to overwrite an existing bank item file")
    write_yaml(new_path, validated)
    try:
        load_bank(bank_dir)  # fail closed: the bank must still aggregate validly
    except KernelValidationError:
        new_path.unlink(missing_ok=True)
        raise
    return new_path
