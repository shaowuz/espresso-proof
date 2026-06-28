from __future__ import annotations

import argparse
import json
from pathlib import Path

from espresso_proof.kernel.models import MODEL_REGISTRY


def schema_payloads() -> dict[str, str]:
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2) + "\n"
        for name, model in MODEL_REGISTRY.items()
    }


def write_schemas(out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    for filename, payload in schema_payloads().items():
        (out_dir / filename).write_text(payload, encoding="utf-8")


def check_schemas(out_dir: Path) -> int:
    expected = schema_payloads()
    actual_files = {path.name for path in out_dir.glob("*.schema.json")} if out_dir.exists() else set()
    expected_files = set(expected)
    failures: list[str] = []

    for filename in sorted(expected_files - actual_files):
        failures.append(f"missing {out_dir / filename}")
    for filename in sorted(actual_files - expected_files):
        failures.append(f"unexpected {out_dir / filename}")
    for filename in sorted(expected_files & actual_files):
        actual = (out_dir / filename).read_text(encoding="utf-8")
        if actual != expected[filename]:
            failures.append(f"stale {out_dir / filename}")

    if failures:
        print("Schema drift detected:")
        for failure in failures:
            print(f"  - {failure}")
        print("Run: uv run espresso export-schemas")
        return 1

    print(f"Schema check passed: {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export local Pydantic model JSON schema snapshots."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the local schema output does not match the current model registry",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("schemas"),
        help="schema output directory",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_schemas(args.out_dir)
    write_schemas(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
