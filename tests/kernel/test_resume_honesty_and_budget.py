"""Three defects found by running a real open problem, not by unit tests.

A hard claim admits no one-round route, so the loop has to iterate. Four
resumes of such a run produced four apparently-successful commands, and every
one of the three problems below was invisible from the command output.
"""

from __future__ import annotations

import pytest

from espresso_proof.strategy.brief import (
    EFFORT_TIERS,
    _declared_effort_ceiling,
    _effort_options_for,
)


# --- 1. a declared budget raises the effort tiers a route may be approved for


def test_no_declared_budget_keeps_the_old_behaviour() -> None:
    # The route offers exactly the grader's estimate, as before.
    assert _declared_effort_ceiling({}) is None
    for tier in EFFORT_TIERS:
        assert _effort_options_for(tier, None) == [tier]


def test_budget_opens_headroom_above_the_estimate() -> None:
    ceiling = _declared_effort_ceiling({"token_budget": 3_200_000})
    assert ceiling == "deep_exploration"
    # This is the hard-claim case with no one-round route: a route the grader
    # estimated at quick_test was capped at one prover round, which cannot
    # iterate on an open problem.
    assert _effort_options_for("quick_test", ceiling) == [
        "quick_test",
        "normal_pass",
        "deep_exploration",
    ]


def test_budget_never_lowers_the_graders_estimate() -> None:
    # A small budget does not veto the route's own judgement of what it needs;
    # it simply funds nothing beyond it.
    small = _declared_effort_ceiling({"token_budget": 800_000})
    assert small == "quick_test"
    assert _effort_options_for("normal_pass", small) == ["normal_pass"]
    assert _effort_options_for("long_watch", small) == ["long_watch"]


def test_budget_is_read_from_the_manifest_too() -> None:
    assert _declared_effort_ceiling({"manifest": {"token_budget": 6_400_000}}) == "long_watch"


@pytest.mark.parametrize("bad", [0, -1, "many", None, "", 3.7])
def test_unusable_budget_values_yield_no_ceiling(bad: object) -> None:
    if bad == 3.7:  # a float is usable; int() accepts it
        assert _declared_effort_ceiling({"token_budget": bad}) is not None
        return
    assert _declared_effort_ceiling({"token_budget": bad}) is None


def test_manifest_rejects_a_nonpositive_budget() -> None:
    from espresso_proof.kernel.models import RunManifest

    base = {
        "run_id": "r1",
        "project_id": "p",
        "created_at": "2026-01-01T00:00:00+00:00",
        "current_state": "SCAFFOLDED",
        "input_file": "input.md",
        "artifacts": {},
    }
    with pytest.raises(ValueError):
        RunManifest.model_validate({**base, "token_budget": 0})


# --- 2. resuming must not silently switch a real-agent run onto stubs


def test_recorded_provider_is_read_from_the_run(tmp_path) -> None:
    from espresso_proof.cli import _recorded_run_provider
    from espresso_proof.kernel.io import write_yaml

    assert _recorded_run_provider(tmp_path) is None  # nothing recorded, no opinion

    write_yaml(tmp_path / "front_half_run.yaml", {"provider": "external"})
    assert _recorded_run_provider(tmp_path) == "external"

    # llm_agent_run wins: it is the later, prover-pass record.
    write_yaml(tmp_path / "llm_agent_run.yaml", {"provider": "openai"})
    assert _recorded_run_provider(tmp_path) == "openai"


def test_unreadable_or_bogus_provider_is_ignored(tmp_path) -> None:
    from espresso_proof.cli import _recorded_run_provider
    from espresso_proof.kernel.io import write_yaml

    write_yaml(tmp_path / "front_half_run.yaml", {"provider": "not-a-provider"})
    assert _recorded_run_provider(tmp_path) is None

    (tmp_path / "llm_agent_run.yaml").write_text(": not yaml :\n", encoding="utf-8")
    assert _recorded_run_provider(tmp_path) is None
