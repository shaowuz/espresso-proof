"""Goal 65D: product prompt context routing.

The order-3 postmortem found the external adapter injected
``docs/goals/CURRENT_CLAUDE_GOAL_QUEUE.md`` (repo maintenance state) into a
product mathematical prompt. A product math run must never auto-inject the
maintenance goal queue; the request type is caller-declared and fails closed.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.agents.agent_skill_catalog import select_agent_skills

_MATH_SYSTEM = "You are the literature-review agent."
_MATH_PROMPT = "prove that a group of order 3 is cyclic"


def _cwd_with_goal_queue(tmp_path: Path) -> Path:
    goals = tmp_path / "docs" / "goals"
    goals.mkdir(parents=True)
    (goals / "CURRENT_CLAUDE_GOAL_QUEUE.md").write_text(
        "# Current Claude Goal Queue\nGoal 64/65 ... maintenance state ...\n",
        encoding="utf-8",
    )
    return tmp_path


def test_product_math_does_not_inject_goal_queue(tmp_path: Path):
    cwd = _cwd_with_goal_queue(tmp_path)
    sel = select_agent_skills(
        system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd, request_type="product_math"
    )
    assert sel.request_type == "product_math"
    assert sel.goal.path is None
    assert sel.goal.text == "none"
    assert "maintenance state" not in sel.goal.text
    # the blocked queue path is recorded for routing visibility
    assert any("CURRENT_CLAUDE_GOAL_QUEUE.md" in b for b in sel.blocked_context)


def test_default_request_type_is_product_math(tmp_path: Path):
    cwd = _cwd_with_goal_queue(tmp_path)
    sel = select_agent_skills(system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd)
    assert sel.request_type == "product_math"
    assert sel.goal.path is None  # not injected by default


def test_unknown_request_type_fails_closed_to_product_math(tmp_path: Path):
    cwd = _cwd_with_goal_queue(tmp_path)
    sel = select_agent_skills(
        system=_MATH_SYSTEM, prompt=_MATH_PROMPT, cwd=cwd, request_type="something_weird"
    )
    assert sel.request_type == "product_math"
    assert sel.goal.path is None  # unknown => restrictive, no injection


def test_maintenance_goal_may_inject_the_queue(tmp_path: Path):
    cwd = _cwd_with_goal_queue(tmp_path)
    sel = select_agent_skills(
        system="maintenance goal-running agent",
        prompt="continue the goal queue",
        cwd=cwd,
        request_type="maintenance_goal",
    )
    assert sel.request_type == "maintenance_goal"
    assert sel.goal.path is not None
    assert "maintenance state" in sel.goal.text
    assert sel.blocked_context == ()


def test_explicit_active_goal_file_is_honored_even_for_product_math(tmp_path: Path):
    cwd = _cwd_with_goal_queue(tmp_path)
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
