#!/usr/bin/env python3
"""Aggregate frozen review annotations; do not infer mathematical judgments."""

import argparse
import hashlib
import json
from pathlib import Path


DIRECTIONS = {
    "notation_coverage": "higher",
    "gap_ratio": "lower",
    "terminology_consistency": "higher",
    "citation_misuse": "lower",
    "casual_chatting": "lower",
    "circular_dependency": "lower",
    "statement_clarity": "higher",
}
STATUSES = {"pass", "fail", "unresolved"}


def read_json(path):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def validate_registry(registry):
    scope = registry.get("scope", {})
    digest = scope.get("source_sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("scope.source_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(scope.get("inventory_complete"), bool):
        raise ValueError("scope.inventory_complete must be a boolean")
    if not isinstance(scope.get("reader"), str) or not scope["reader"].strip():
        raise ValueError("scope.reader is required")
    units = registry.get("units")
    if not isinstance(units, list):
        raise ValueError("units must be a list")
    indexed = {}
    for unit in units:
        unit_id = unit.get("id")
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise ValueError("Every unit needs a nonempty id")
        if unit_id in indexed:
            raise ValueError(f"Duplicate unit: {unit_id}")
        if unit.get("metric") not in DIRECTIONS:
            raise ValueError(f"Unknown metric for {unit_id}")
        if any(not isinstance(unit.get(key), str) or not unit[key].strip()
               for key in ("location", "description")):
            raise ValueError(f"Unit {unit_id} needs a location and description")
        indexed[unit_id] = unit
    return indexed


def score(registry, registry_digest, assessment):
    units = validate_registry(registry)
    if assessment.get("registry_sha256") != registry_digest:
        raise ValueError("Assessment does not match the frozen registry hash")
    judgments = assessment.get("judgments")
    if not isinstance(judgments, list):
        raise ValueError("judgments must be a list")
    annotations = {}
    for judgment in judgments:
        unit_id = judgment.get("unit_id")
        if unit_id not in units:
            raise ValueError(f"Unknown assessed unit: {unit_id}")
        if unit_id in annotations:
            raise ValueError(f"Duplicate assessment: {unit_id}")
        status = judgment.get("status")
        if status not in STATUSES:
            raise ValueError(f"Invalid status for {unit_id}: {status}")
        reason = judgment.get("reason")
        if status != "unresolved" and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f"Assessed unit {unit_id} needs a reason")
        annotations[unit_id] = judgment

    results = {}
    for metric, direction in DIRECTIONS.items():
        registered = {uid for uid, u in units.items() if u["metric"] == metric}
        passed = sorted(uid for uid in registered if annotations.get(uid, {}).get("status") == "pass")
        failed = sorted(uid for uid in registered if annotations.get(uid, {}).get("status") == "fail")
        unresolved = sorted(registered - set(passed) - set(failed))
        assessed = len(passed) + len(failed)
        numerator = len(passed) if direction == "higher" else len(failed)
        results[metric] = {
            "direction": direction,
            "numerator": numerator,
            "assessed": assessed,
            "registered": len(registered),
            "unresolved": unresolved,
            "rate": numerator / assessed if assessed else None,
            "coverage": assessed / len(registered) if registered else None,
            "assessed_ids": sorted(passed + failed),
        }
    return {
        "registry_sha256": registry_digest,
        "scope": registry["scope"],
        "coverage_scope": "declared-inventory" if registry["scope"]["inventory_complete"] else "registered-units-only",
        "notice": "Aggregates supplied annotations; performs no semantic or proof checking.",
        "metrics": results,
    }


def compare(current, baseline):
    comparison = {}
    for metric in DIRECTIONS:
        now, before = current["metrics"][metric], baseline["metrics"][metric]
        comparable = bool(now["assessed_ids"]) and now["assessed_ids"] == before["assessed_ids"]
        improvement = None
        if comparable:
            change = now["rate"] - before["rate"]
            improvement = change if now["direction"] == "higher" else -change
        comparison[metric] = {
            "same_assessed_units": comparable,
            "improvement": improvement,
            "baseline_rate": before["rate"],
            "current_rate": now["rate"],
        }
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("assessment", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--source", type=Path, help="Verify the source bytes against the frozen source hash")
    args = parser.parse_args()
    try:
        registry, digest = read_json(args.registry)
        assessment, _ = read_json(args.assessment)
        result = score(registry, digest, assessment)
        if args.source:
            actual = hashlib.sha256(args.source.read_bytes()).hexdigest()
            if actual != registry["scope"]["source_sha256"]:
                raise ValueError("Source bytes do not match the frozen source hash")
        result["source_bytes_verified"] = args.source is not None
        if args.baseline:
            baseline_data, _ = read_json(args.baseline)
            baseline = score(registry, digest, baseline_data)
            result["comparison"] = compare(result, baseline)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.exit(2, f"score_review: {exc}\n")


if __name__ == "__main__":
    main()
