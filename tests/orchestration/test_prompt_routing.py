"""Product prompt context routing.

Product math runs must never auto-inject maintenance context. Goal context is
loaded only from an explicit active_goal_file.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.agents.agent_skill_catalog import select_agent_skills

_MATH_SYSTEM = "You are the literature-review agent."
_MATH_PROMPT = "prove that a group of order 3 is cyclic"


def _cwd_with_legacy_goal_file(tmp_path: Path) -> Path:
    goals = tmp_path / "docs" / "goals"
    goals.mkdir(parents=True)
    (goals / "legacy-goal-queue.md").write_text(
        "# legacy goal queue\nmaintenance state\n",
        encoding="utf-8",
    )
    return tmp_path


def test_product_math_does_not_inject_goal_context(tmp_path: Path):
    cwd = _cwd_with_legacy_goal_file(tmp_path)
    sel = select_agent_skills(
        system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd, request_type="product_math"
    )
    assert sel.request_type == "product_math"
    assert sel.goal.path is None
    assert sel.goal.text == "none"
    assert "maintenance state" not in sel.goal.text
    assert sel.blocked_context == ()


def test_default_request_type_is_product_math(tmp_path: Path):
    cwd = _cwd_with_legacy_goal_file(tmp_path)
    sel = select_agent_skills(system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd)
    assert sel.request_type == "product_math"
    assert sel.goal.path is None  # not injected by default


def test_unknown_request_type_fails_closed_to_product_math(tmp_path: Path):
    cwd = _cwd_with_legacy_goal_file(tmp_path)
    sel = select_agent_skills(
        system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd, request_type="something_weird"
    )
    assert sel.request_type == "product_math"
    assert sel.goal.path is None  # unknown => restrictive, no injection


def test_maintenance_goal_does_not_load_implicit_queue(tmp_path: Path):
    cwd = _cwd_with_legacy_goal_file(tmp_path)
    sel = select_agent_skills(
        system="maintenance goal-running agent",
        prompt="continue the goal queue",
        cwd=cwd,
        request_type="maintenance_goal",
    )
    assert sel.request_type == "maintenance_goal"
    assert sel.goal.path is None
    assert sel.goal.text == "none"
    assert sel.blocked_context == ()


def test_explicit_active_goal_file_is_honored_even_for_product_math(tmp_path: Path):
    cwd = _cwd_with_legacy_goal_file(tmp_path)
    explicit = tmp_path / "my_goal.md"
    explicit.write_text("Active goal: study this specific claim.\n", encoding="utf-8")
    sel = select_agent_skills(
        system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd,
        active_goal_file=str(explicit), request_type="product_math",
    )
    # explicit file is loaded; the default maintenance queue is NOT
    assert sel.goal.path == str(explicit)
    assert "study this specific claim" in sel.goal.text
    assert "maintenance state" not in sel.goal.text
