"""Runtime skill selection for Espresso external agents.

The external CLI adapters (Codex: ``espresso_proof.agents.codex_adapter``;
Claude Max: ``espresso_proof.agents.claude_adapter``) inject these role-specific
skills into each external agent call. This is deterministic prompt assembly,
not model-side status logic: skills can guide candidate generation, but the
kernel still owns every trusted state transition.
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
    request_type: str = "product_math"
    # repo maintenance/goal files intentionally NOT injected for this request type
    blocked_context: tuple[str, ...] = ()


# Product mathematical runs must never auto-inject repo maintenance context into
# their prompts. Maintenance goal context is explicit-only: callers pass an
# active_goal_file when they want one.
PRODUCT_MATH = "product_math"
_REQUEST_TYPES = frozenset({"product_math", "maintenance_goal", "dogfood_diagnostic", "reviewer"})


def _normalize_request_type(request_type: str | None) -> str:
    """Fail closed: unknown/missing request type => product_math (no goal-file
    auto-injection). The type is caller-DECLARED, never inferred from prompt text."""
    rt = (request_type or "").strip().lower()
    return rt if rt in _REQUEST_TYPES else PRODUCT_MATH


_SKILLS: tuple[AgentSkill, ...] = (
    # Back-half proof roles are listed FIRST on purpose:
    # ``selection.stage`` takes the first hit in this tuple, and these calls also
    # match broad companion triggers further down (e.g. "counterexample" appears
    # in the proof-writer system prompt and in agent_prompt protocol_rules), so
    # the role skill must precede them to own the stage. Their triggers are
    # copied verbatim from the runtime system prompts in
    # ``llm_agents.line_numbered_proof_writer_system_prompt`` /
    # ``proof_consensus_system_prompt`` / ``proof_gap_resolver_system_prompt``
    # and from the ``agent_prompt(role=...)`` JSON payloads, and appear nowhere
    # else in prompt-producing code.
    AgentSkill(
        "proof-writer",
        "proof_writer.md",
        ("line-numbered proof writer", '"role": "line_numbered_proof_writer"'),
    ),
    AgentSkill(
        "proof-gap-resolver",
        "proof_gap_resolver.md",
        ("proof gap resolver", '"role": "proof_gap_resolver"'),
    ),
    AgentSkill(
        "reviewer-theorem-applicability",
        "reviewer_theorem_applicability.md",
        (
            "theorem/lemma applicability reviewer",
            '"role": "proof_review_theorem_lemma_applicability_reviewer"',
        ),
    ),
    AgentSkill(
        "reviewer-adversarial-gap",
        "reviewer_adversarial_gap.md",
        (
            "adversarial gap reviewer",
            '"role": "proof_review_adversarial_gap_reviewer"',
        ),
    ),
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
    request_type: str | None = PRODUCT_MATH,
) -> SkillSelection:
    """Choose role skills and active goal text for a single agent call.

    ``request_type`` is caller-declared. A ``product_math`` run (the
    default, and the fail-closed value for any unknown type) does NOT auto-inject
    the repo's maintenance goal queue; only ``maintenance_goal`` does. An explicit
    ``active_goal_file`` is always honored."""

    request_type = _normalize_request_type(request_type)
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
    blocked: tuple[str, ...] = ()
    skill_text = "\n\n".join([_read_skill(_SAFETY_FILE)] + [_read_skill(skill.file_name) for skill in chosen])
    stage = chosen[0].name
    return SkillSelection(
        stage=stage,
        skills=tuple(skill.name for skill in chosen),
        skill_text=skill_text,
        goal=goal,
        request_type=request_type,
        blocked_context=blocked,
    )


def load_active_goal(
    *,
    cwd: Path | None = None,
    active_goal_file: str | None = None,
    include_default_queue: bool = False,
) -> GoalContext:
    """Load explicit active-goal context for an agent prompt.

    ``include_default_queue`` is accepted for old callers but intentionally
    ignored. The repository no longer has a default maintenance goal queue.
    """
    base = cwd or Path.cwd()
    candidates: list[Path] = []
    if active_goal_file:
        candidates.append(Path(active_goal_file).expanduser())

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
