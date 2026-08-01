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

# Caller-DECLARED role -> the one skill that role needs. Same principle the
# catalog already applies to request_type ("caller-declared, never inferred from
# prompt text"), extended to the role itself.
#
# Why: selection used to be a substring scan over system+prompt, and the prompt
# embeds the whole run context — the manifest filename "strategy_synthesis.yaml",
# review scopes like "proof_strategy", the word "counterexample" in the protocol
# rules. That vocabulary appears in EVERY run, so nearly every skill matched on
# nearly every call: a single live prover call shipped six skills
# (counterexamples, strategy-generator, prover, proof-outline-reviewer,
# line-proof-reviewer, strategy-grader). That is not only ~16k tokens of waste
# per call — it hands the prover the reviewers' rubrics, which is exactly the
# role confusion the reviewers are supposed to provide independently.
_ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    # front-half stages (registry names)
    "understanding_claim": ("claim-understanding",),
    "literature_review": ("literature-review",),
    "examples": ("examples",),
    "counterexamples": ("counterexamples",),
    "source_theorem": ("reference-matcher",),
    "check_generator": ("reference-matcher",),
    "regrade": ("strategy-grader",),
    "strategy": ("strategy-generator", "strategy-grader"),
    "attacker": ("strategy-attacker",),
    "grade_memory_memo": ("strategy-grader",),
    # prover / reviewer lane
    "prover": ("prover",),
    "prover_loop_round": ("prover",),
    "strategy_reviewer": ("proof-outline-reviewer",),
    "evidence_reviewer": ("literature-use-reviewer",),
    "line_proof_reviewer": ("line-proof-reviewer",),
    # back-half authoritative roles
    "line_numbered_proof_writer": ("proof-writer",),
    "proof_gap_resolver": ("proof-gap-resolver",),
    "proof_review_theorem_lemma_applicability_reviewer": ("reviewer-theorem-applicability",),
    "proof_review_adversarial_gap_reviewer": ("reviewer-adversarial-gap",),
}


def skills_for_role(role: str | None) -> tuple[str, ...]:
    """Skill names a declared role needs, or () when the role is unknown."""

    if not role:
        return ()
    key = str(role).strip().lower()
    if key in _ROLE_SKILLS:
        return _ROLE_SKILLS[key]
    # Round-lane reviewers arrive as "round_003_strategy_reviewer"; the trailing
    # role name is the identity that matters.
    for name, skills in _ROLE_SKILLS.items():
        if key.endswith(name):
            return skills
    return ()


def select_agent_skills(
    *,
    system: str,
    prompt: str,
    cwd: Path | None = None,
    active_goal_file: str | None = None,
    request_type: str | None = PRODUCT_MATH,
    role: str | None = None,
) -> SkillSelection:
    """Choose role skills and active goal text for a single agent call.

    ``request_type`` is caller-declared. A ``product_math`` run (the
    default, and the fail-closed value for any unknown type) does NOT auto-inject
    the repo's maintenance goal queue; only ``maintenance_goal`` does. An explicit
    ``active_goal_file`` is always honored.

    ``role`` is likewise caller-declared and, when recognized, selects the skill
    directly. The trigger scan below remains only as the fallback for callers
    that declare no role — it reads the whole prompt, which embeds run context,
    so it over-matches (see ``_ROLE_SKILLS``)."""

    request_type = _normalize_request_type(request_type)
    declared = skills_for_role(role)
    if declared:
        chosen = [_skill_by_name(name) for name in declared]
        goal = load_active_goal(cwd=cwd, active_goal_file=active_goal_file)
        skill_text = "\n\n".join(
            [_read_skill(_SAFETY_FILE)] + [_read_skill(skill.file_name) for skill in chosen]
        )
        return SkillSelection(
            stage=chosen[0].name,
            skills=tuple(skill.name for skill in chosen),
            skill_text=skill_text,
            goal=goal,
            request_type=request_type,
            blocked_context=(),
        )
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
