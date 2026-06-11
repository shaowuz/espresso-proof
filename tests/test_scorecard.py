"""Goal 40B: Autoprover Behavior Scorecard.

The scorecard reports safety AND capability metrics deterministically, fails
on planted regressions (including reviewer gold-flaw misses and dishonest
dogfood replays), and never earns or upgrades any mathematical status.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.scorecard import (
    SCORECARD_SCHEMA_VERSION,
    _probe_reviewer_quality,
    render_scorecard_markdown,
    run_scorecard,
)

FIXTURES = Path("tests/fixtures")


def test_scorecard_is_clean_and_complete(tmp_path: Path) -> None:
    report = run_scorecard(tmp_path)
    assert report["schema_version"] == SCORECARD_SCHEMA_VERSION
    assert report["clean"] is True
    assert report["safety"]["false_verified_count"] == 0
    assert report["safety"]["unauthorized_source_backed_count"] == 0
    cap = report["capability"]
    assert cap["exact_source_grounding_rate"] == 1.0
    assert cap["theorem_applicability_block_recall"] == 1.0
    assert cap["counterexample_scope_accuracy"] == 1.0
    assert cap["scoped_verifier_wiring_success"] == 1.0
    assert cap["reviewer_objection_recall"] == 1.0
    assert cap["repair_round_non_regression"] is True
    assert cap["dogfood_replay_honest"] is True


def test_reviewer_quality_probe_seeds_and_recalls_gold_flaws(tmp_path: Path) -> None:
    probe = _probe_reviewer_quality(tmp_path)
    assert probe["seeded"] == 2
    assert probe["recalled"] == 2
    assert probe["merge_blocked"] is True  # unsafe pass-through would fail
    assert probe["passed"] is True


def test_scorecard_fails_on_missed_gold_flaw(tmp_path: Path, monkeypatch) -> None:
    # A reviewer that stops flagging proof-complete language must fail the
    # scorecard (reviewer-quality gate bites through the real aggregation).
    import espresso_proof.reviewer_checklist as checklist_mod
    import espresso_proof.scorecard as scorecard_mod

    monkeypatch.setattr(checklist_mod, "detect_proof_complete_language", lambda text: [])
    # scorecard imports review_checklist lazily from the module, so the patch
    # above is seen; sanity-check via the probe first.
    probe = scorecard_mod._probe_reviewer_quality(tmp_path / "probe")
    assert probe["passed"] is False
    report = scorecard_mod.run_scorecard(tmp_path / "full")
    assert report["clean"] is False
    assert report["capability"]["reviewer_objection_recall"] < 1.0


def test_scorecard_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    json_path = tmp_path / "out" / "scorecard.json"
    md_path = tmp_path / "out" / "scorecard.md"
    res = CliRunner().invoke(
        app,
        [
            "scorecard",
            "--out-root", str(tmp_path / "runs"),
            "--json-out", str(json_path),
            "--markdown-out", str(md_path),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCORECARD_SCHEMA_VERSION
    assert payload["clean"] is True
    markdown = md_path.read_text(encoding="utf-8")
    assert "# Autoprover Behavior Scorecard" in markdown
    assert "Overall: CLEAN" in markdown
    assert "never earn or upgrade any" in markdown
    assert "proved" not in markdown.replace("'proved'", "")  # no overclaim wording


def test_scorecard_markdown_is_stable_for_trends(tmp_path: Path) -> None:
    # No timestamps / machine-local paths in the SUMMARY surfaces, so two runs
    # are byte-identical and diffable over time.
    r1 = run_scorecard(tmp_path / "a")
    r2 = run_scorecard(tmp_path / "b")
    assert render_scorecard_markdown(r1) == render_scorecard_markdown(r2)
    assert r1["safety"] == r2["safety"]
    assert r1["capability"] == r2["capability"]


def test_dogfood_replay_fails_on_proof_complete_memo(tmp_path: Path, monkeypatch) -> None:
    # Spec negative test: the replay cannot pass with proof-complete language.
    import espresso_proof.memo as memo_mod
    import espresso_proof.scorecard as scorecard_mod

    monkeypatch.setattr(
        memo_mod, "build_concise_memo", lambda run_dir: "We proved the theorem. proof-close."
    )
    probe = scorecard_mod._probe_dogfood_replay(tmp_path)
    assert probe["memo_honest"] is False
    assert probe["passed"] is False


def test_dogfood_replay_fails_on_unauthorized_source_backed(tmp_path: Path) -> None:
    # Spec negative test: the replay cannot pass with unauthorized source
    # support. Plant a source_backed step into the replayed run's grounding by
    # intercepting the read — the probe must count it and fail.
    import espresso_proof.scorecard as scorecard_mod
    from espresso_proof.io import read_yaml as real_read_yaml

    def replay_with_planted_step(root: Path) -> dict:
        import espresso_proof.scorecard as sc

        original = sc.read_yaml

        def tampered_read(path):
            data = original(path)
            if str(path).endswith("source_grounding.yaml") and isinstance(data, dict):
                steps = [dict(s) for s in data.get("steps", [])]
                if steps:
                    steps[0]["status"] = "source_backed"
                data = {**data, "steps": steps}
            return data

        sc.read_yaml = tampered_read
        try:
            return sc._probe_dogfood_replay(root)
        finally:
            sc.read_yaml = original

    probe = replay_with_planted_step(tmp_path)
    assert probe["unauthorized_source_backed"] > 0
    assert probe["passed"] is False
    assert real_read_yaml is not None  # silence unused-import lint
