import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_review.py"
SPEC = importlib.util.spec_from_file_location("skill_score_review", SCRIPT)
SCORER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORER)


def registry():
    units = [
        {"id": uid, "metric": metric, "location": f"anchor:{uid}", "description": uid}
        for metric, ids in {
            "notation_coverage": ["N1", "N2", "N3", "N4"],
            "citation_misuse": ["C1", "C2", "C3"],
        }.items()
        for uid in ids
    ]
    return {"scope": {"source_sha256": hashlib.sha256(b"source").hexdigest(),
                      "reader": "research mathematician", "inventory_complete": False},
            "units": units}


def assessment(statuses, digest="a" * 64):
    return {"registry_sha256": digest, "judgments": [
        {"unit_id": uid, "status": state, "reason": "Located anchor judgment"}
        for uid, state in statuses.items()
    ]}


class ScoringTests(unittest.TestCase):
    def test_known_anchor_counts_and_polarities(self):
        data = assessment({"N1": "pass", "N2": "pass", "N3": "pass", "N4": "fail",
                           "C1": "pass", "C2": "fail", "C3": "unresolved"})
        metrics = SCORER.score(registry(), "a" * 64, data)["metrics"]
        self.assertEqual(metrics["notation_coverage"]["rate"], 3 / 4)
        self.assertEqual(metrics["citation_misuse"]["rate"], 1 / 2)
        self.assertEqual(metrics["citation_misuse"]["coverage"], 2 / 3)
        self.assertEqual(metrics["citation_misuse"]["unresolved"], ["C3"])
        self.assertIsNone(metrics["gap_ratio"]["rate"])

    def test_no_assessed_units_never_become_zero_error(self):
        result = SCORER.score(registry(), "a" * 64, assessment({}))
        metric = result["metrics"]["citation_misuse"]
        self.assertIsNone(metric["rate"])
        self.assertEqual(metric["coverage"], 0)
        self.assertEqual(metric["registered"], 3)
        self.assertEqual(len(metric["unresolved"]), 3)
        self.assertEqual(result["coverage_scope"], "registered-units-only")

    def test_unknown_and_duplicate_assessments_are_rejected(self):
        with self.assertRaises(ValueError):
            SCORER.score(registry(), "a" * 64, assessment({"NEW": "pass"}))
        data = assessment({"N1": "pass"})
        data["judgments"].append(data["judgments"][0].copy())
        with self.assertRaises(ValueError):
            SCORER.score(registry(), "a" * 64, data)

    def test_duplicate_registry_ids_and_hash_changes_are_rejected(self):
        data = registry()
        data["units"].append(data["units"][0].copy())
        with self.assertRaises(ValueError):
            SCORER.score(data, "a" * 64, assessment({}))
        with self.assertRaises(ValueError):
            SCORER.score(registry(), "b" * 64, assessment({}))

    def test_registry_locators_and_descriptions_are_text(self):
        for field in ("location", "description"):
            for invalid in (None, 1, ""):
                with self.subTest(field=field, invalid=invalid):
                    data = registry()
                    data["units"][0][field] = invalid
                    with self.assertRaises(ValueError):
                        SCORER.score(data, "a" * 64, assessment({}))

    def test_equal_counts_with_different_assessed_units_are_not_comparable(self):
        before = SCORER.score(registry(), "a" * 64, assessment({"C1": "pass"}))
        after = SCORER.score(registry(), "a" * 64, assessment({"C2": "fail"}))
        result = SCORER.compare(after, before)["citation_misuse"]
        self.assertFalse(result["same_assessed_units"])
        self.assertIsNone(result["improvement"])

    def test_improvement_sign_matches_metric_direction(self):
        before = SCORER.score(registry(), "a" * 64, assessment({"N1": "fail", "C1": "fail"}))
        after = SCORER.score(registry(), "a" * 64, assessment({"N1": "pass", "C1": "pass"}))
        changes = SCORER.compare(after, before)
        self.assertEqual(changes["notation_coverage"]["improvement"], 1)
        self.assertEqual(changes["citation_misuse"]["improvement"], 1)

    def test_assessed_unit_requires_a_reason(self):
        for reason in ("", None, 0, False):
            with self.subTest(reason=reason):
                data = assessment({"N1": "pass"})
                data["judgments"][0]["reason"] = reason
                with self.assertRaises(ValueError):
                    SCORER.score(registry(), "a" * 64, data)

    def test_cli_checks_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_bytes = (json.dumps(registry()) + "\n").encode()
            (root / "registry.json").write_bytes(registry_bytes)
            digest = hashlib.sha256(registry_bytes).hexdigest()
            (root / "assessment.json").write_text(json.dumps(assessment({"C1": "pass"}, digest)))
            (root / "source.txt").write_bytes(b"source")
            args = [sys.executable, str(SCRIPT), str(root / "registry.json"),
                    str(root / "assessment.json"), "--source", str(root / "source.txt")]
            passed = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertTrue(json.loads(passed.stdout)["source_bytes_verified"])
            (root / "source.txt").write_bytes(b"changed")
            rejected = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("Source bytes do not match", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
