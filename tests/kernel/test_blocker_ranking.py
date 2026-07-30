"""Blocker ranking must be honest about whether the reviewers ranked anything.

A real run produced 19 scoped objections, every one at severity P2 with an empty
suggested_next_action, while product-philosophy.md promises the mathematician
"the cheapest next action". Presenting the schema default as a ranking would be
the display layer inventing a judgement no reviewer made.
"""

from __future__ import annotations

from espresso_proof.kernel.surface_warnings import (
    latest_prover_round_reviews,
    rank_scoped_objections,
    render_blocker_ranking,
)


def _run(*objections: dict) -> dict:
    return {"verifier_reviews": [{"scoped_objections": list(objections)}]}


def _obj(text: str, severity: str = "P2", action: str = "") -> dict:
    return {"text": text, "severity": severity, "suggested_next_action": action}


def test_uniform_severity_is_reported_as_unranked() -> None:
    # The observed real-run state: everything P2.
    ranking = rank_scoped_objections(_run(*[_obj(f"objection {i}") for i in range(19)]))
    assert ranking["total"] == 19
    assert ranking["differentiated"] is False
    assert ranking["top"] == []

    lines = render_blocker_ranking(ranking)
    text = " ".join(lines)
    assert "none" in text.lower()
    assert "arbitrary" in text.lower()
    assert "19" in text


def test_real_ranking_surfaces_blocking_objections_first() -> None:
    ranking = rank_scoped_objections(
        _run(
            _obj("hygiene", "P3"),
            _obj("route is refuted", "P0", action="abandon route_001"),
            _obj("cosmetic", "P2"),
            _obj("step unsupported", "P1", action="pin Shafarevich with a locator"),
        )
    )
    assert ranking["differentiated"] is True
    assert [o["severity"] for o in ranking["top"]] == ["P0", "P1"]

    lines = render_blocker_ranking(ranking)
    text = "\n".join(lines)
    assert "[P0] route is refuted" in text
    assert "next: abandon route_001" in text
    # P2/P3 must not be promoted into the ranked head
    assert "cosmetic" not in text and "hygiene" not in text


def test_differentiated_but_nothing_blocking_says_so() -> None:
    ranking = rank_scoped_objections(_run(_obj("a", "P2"), _obj("b", "P3")))
    assert ranking["differentiated"] is True
    assert ranking["top"] == []
    assert "nothing is blocking" in " ".join(render_blocker_ranking(ranking)).lower()


def test_actionable_counts_only_actions_a_reviewer_wrote() -> None:
    ranking = rank_scoped_objections(
        _run(_obj("a", "P0", action="pin X"), _obj("b", "P1", action="   "), _obj("c", "P1"))
    )
    assert ranking["actionable"] == 1


def test_empty_and_malformed_input_render_nothing() -> None:
    for run in ({}, {"verifier_reviews": []}, {"verifier_reviews": [{}]}, None):
        ranking = rank_scoped_objections(run)
        assert ranking["total"] == 0
        assert render_blocker_ranking(ranking) == []


def test_ranking_never_assigns_a_severity_itself() -> None:
    # An objection with no severity must be counted at the schema default and
    # must not be silently promoted into the blocking head.
    ranking = rank_scoped_objections(_run({"text": "no severity field"}))
    assert ranking["levels"] == {"P2": 1}
    assert ranking["differentiated"] is False


# --- ranking must read the LATEST round, not the initial pass


def _write(p, doc):
    import yaml

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_latest_round_wins_over_initial_pass(tmp_path) -> None:
    _write(tmp_path / "llm_agent_run.yaml",
           {"verifier_reviews": [{"scoped_objections": [_obj("stale round-1 objection")]}]})
    _write(tmp_path / "prover_rounds" / "round_001_x.yaml",
           {"verifier_reviews": [{"scoped_objections": [_obj("round 1")]}]})
    _write(tmp_path / "prover_rounds" / "round_003_x.yaml",
           {"verifier_reviews": [{"scoped_objections": [_obj("round 3 latest", "P1", "do X")]}]})
    doc = latest_prover_round_reviews(tmp_path)
    texts = [s["text"] for rv in doc["verifier_reviews"] for s in rv["scoped_objections"]]
    assert texts == ["round 3 latest"]


def test_no_rounds_falls_back_to_initial(tmp_path) -> None:
    _write(tmp_path / "llm_agent_run.yaml",
           {"verifier_reviews": [{"scoped_objections": [_obj("initial")]}]})
    doc = latest_prover_round_reviews(tmp_path)
    assert doc["verifier_reviews"]


def test_empty_run_dir_yields_empty(tmp_path) -> None:
    assert latest_prover_round_reviews(tmp_path) == {}
    (tmp_path / "llm_agent_run.yaml").write_text(": bad yaml :", encoding="utf-8")
    assert latest_prover_round_reviews(tmp_path) == {}


def test_round_without_reviews_is_skipped_for_an_earlier_one(tmp_path) -> None:
    # index.yaml and review-less round files must not shadow a real record.
    _write(tmp_path / "prover_rounds" / "round_001_x.yaml",
           {"verifier_reviews": [{"scoped_objections": [_obj("real")]}]})
    _write(tmp_path / "prover_rounds" / "round_002_x.yaml", {"verifier_reviews": []})
    doc = latest_prover_round_reviews(tmp_path)
    assert doc["verifier_reviews"][0]["scoped_objections"][0]["text"] == "real"
