"""Runtime skill selection for Espresso external agents.

The Codex CLI adapter injects these role-specific skills into each external
agent call. This is deterministic prompt assembly, not model-side status logic:
skills can guide candidate generation, but the kernel still owns every trusted
state transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class AgentSkill:
    name: str
    file_name: str
    triggers: tuple[str, ...]


@dataclass(frozen=True)
class GoalContext:
    path: str | None
    text: str


@dataclass(frozen=True)
class SkillSelection:
    stage: str
    skills: tuple[str, ...]
    skill_text: str
    goal: GoalContext


_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        "claim-understanding",
        "claim_understanding.md",
        ("understanding-claim", "understanding claim", "claim precision"),
    ),
    AgentSkill(
        "literature-review",
        "literature_review.md",
        ("literature-review", "literature review", "literature-search", "literature scout"),
    ),
    AgentSkill(
        "examples",
        "examples.md",
        ("examples agent", "propose candidate examples", "canonical examples"),
    ),
    AgentSkill(
        "counterexamples",
        "counterexamples.md",
        ("counterexamples agent", "counterexample", "pressure tests"),
    ),
    AgentSkill(
        "strategy-generator",
        "strategy_generator.md",
        ("strategy agent", "propose candidate proof routes", "strategy_synthesis"),
    ),
    AgentSkill(
        "strategy-attacker",
        "strategy_attacker.md",
        ("example/counterexample attacker", "adversarially pressure", "attacker"),
    ),
    AgentSkill(
        "strategy-grader",
        "strategy_grader.md",
        ("strategy grader", "grade agent", "candidate confidence", "mathematical judgment"),
    ),
    AgentSkill(
        "reference-matcher",
        "reference_matcher.md",
        ("source-matching", "reference matcher", "source grounding", "hypothesis match"),
    ),
    AgentSkill(
        "prover",
        "prover.md",
        ("prover agent", "prover_loop_round", '"role": "prover"'),
    ),
    AgentSkill(
        "proof-outline-reviewer",
        "reviewer_proof_outline.md",
        ("strategy reviewer", "proof outline", "proof_strategy"),
    ),
    AgentSkill(
        "literature-use-reviewer",
        "reviewer_literature_use.md",
        ("evidence reviewer", "literature use", "source gap"),
    ),
    AgentSkill(
        "line-proof-reviewer",
        "reviewer_line_proof.md",
        ("line proof reviewer", "line-proof", "line_by_line_proof"),
    ),
)

_SAFETY_FILE = "mathematical_safety.md"


def select_agent_skills(
    *,
    system: str,
    prompt: str,
    cwd: Path | None = None,
    active_goal_file: str | None = None,
) -> SkillSelection:
    """Choose role skills and active goal text for a single agent call."""

    text = f"{system}\n{prompt}".lower()
    chosen: list[AgentSkill] = []
    for skill in _SKILLS:
        if any(trigger in text for trigger in skill.triggers):
            chosen.append(skill)

    # The strategy stage owns both generation and grading in current runtime.
    names = {skill.name for skill in chosen}
    if "counterexamples agent" in text and "strategy-attacker" not in names:
        chosen = [skill for skill in chosen if skill.name != "examples"]
        names = {skill.name for skill in chosen}
    if "strategy-attacker" in names:
        chosen = [skill for skill in chosen if skill.name not in {"examples", "counterexamples"}]
        names = {skill.name for skill in chosen}
    if "strategy-generator" in names and "strategy-grader" not in names:
        chosen.append(_skill_by_name("strategy-grader"))
    if "counterexamples" in names and "examples" in names and "strategy-attacker" not in names:
        if "attacker" in text or "adversarially pressure" in text:
            chosen = [skill for skill in chosen if skill.name not in {"examples", "counterexamples"}]
            chosen.append(_skill_by_name("strategy-attacker"))

    if not chosen:
        chosen = [_skill_by_name("claim-understanding")]

    goal = load_active_goal(cwd=cwd, active_goal_file=active_goal_file)
    skill_text = "\n\n".join([_read_skill(_SAFETY_FILE)] + [_read_skill(skill.file_name) for skill in chosen])
    stage = chosen[0].name
    return SkillSelection(
        stage=stage,
        skills=tuple(skill.name for skill in chosen),
        skill_text=skill_text,
        goal=goal,
    )


def load_active_goal(*, cwd: Path | None = None, active_goal_file: str | None = None) -> GoalContext:
    base = cwd or Path.cwd()
    candidates: list[Path] = []
    if active_goal_file:
        candidates.append(Path(active_goal_file).expanduser())
    candidates.append(base / "docs" / "goals" / "CURRENT_CLAUDE_GOAL_QUEUE.md")

    for path in candidates:
        if not path.is_absolute():
            path = base / path
        try:
            if path.exists() and path.is_file():
                return GoalContext(path=str(path), text=path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return GoalContext(path=None, text="none")


def _skill_by_name(name: str) -> AgentSkill:
    for skill in _SKILLS:
        if skill.name == name:
            return skill
    raise KeyError(name)


def _read_skill(file_name: str) -> str:
    resource = files("espresso_proof").joinpath("agent_skills", file_name)
    return resource.read_text(encoding="utf-8")
