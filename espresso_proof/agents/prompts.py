"""System prompts and prompt/context assembly for Espresso's LLM agents.

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every name defined here.
"""

from __future__ import annotations

import json
from pathlib import Path

from espresso_proof.agents.output_schemas import AGENT_OUTPUT_SCHEMA
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.research_bank import load_bankable_dict


ESPRESSO_REVIEWER_ROLES = [
    "strategy_reviewer",
    "evidence_reviewer",
    "line_proof_reviewer",
]


def load_agent_context(run_dir: Path) -> dict:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    return {
        "manifest": manifest,
        "claim": read_yaml(run_dir / artifacts["locked_claim"]),
        "hypothesis_ledger": read_yaml(run_dir / artifacts["hypothesis_ledger"]),
        "literature_review": read_yaml(run_dir / artifacts["literature_review"]),
        # Bankable artifacts may be a single YAML file or a per-item bank dir;
        # load them through the manifest + bank-aware loader.
        "examples": load_bankable_dict(run_dir, "examples", manifest),
        "computation_checks": read_yaml(run_dir / artifacts["computation_checks"]),
        "counterexamples": load_bankable_dict(run_dir, "counterexamples", manifest),
        "tests": read_yaml(run_dir / artifacts["tests"]),
        "source_grounding": read_yaml(run_dir / artifacts["source_grounding"]),
        "review": read_yaml(run_dir / artifacts["review"]),
        "idea_grade": read_yaml(run_dir / artifacts["idea_grade"]),
        "strategy_brief": read_yaml(run_dir / artifacts["strategy_brief"]),
    }


def prover_system_prompt() -> str:
    return (
        "You are Espresso's prover agent. Propose candidate proof routes only. "
        "Do not claim the theorem is proved, verified, complete, or true. "
        "Every route must list source requests and risks. "
        "Unchecked examples and counterexamples are pressure-lane context only: "
        "they may suggest a route or expose a boundary case, but they may NEVER be "
        "cited as proof support, theorem support, survived evidence, or proof dependencies. "
        "When discussing unchecked examples/counterexamples, use the words pressure, "
        "suggests, or boundary case; do not write that unchecked context supports a "
        "claim, route, theorem, or proof step. "
        "Only definitions, prior proof lines, explicit theorem references, verified "
        "computations, or human-confirmed assumptions can support a proof step. "
        "Return JSON only."
    )


def verifier_system_prompt(index: int, *, reviewer_role: str = "verifier") -> str:
    role_guidance = {
        "strategy_reviewer": (
            "You are Espresso strategy reviewer agent. Assess theorem drift, strategic soundness, "
            "missing reductions, and analogy risks."
        ),
        "evidence_reviewer": (
            "You are Espresso evidence reviewer agent. Assess example coverage, counterexample pressure, "
            "and whether computation/source evidence is being used honestly."
        ),
        "line_proof_reviewer": (
            "You are Espresso line proof reviewer agent. Parse proof-sketch steps, locate source gaps, "
            "and flag external theorem applicability risks."
        ),
        "verifier": f"You are Espresso verifier agent {index}. Critique the prover route.",
    }.get(reviewer_role, f"You are Espresso verifier agent {index}. Critique the prover route.")
    return (
        f"{role_guidance} "
        "Flag missing hypotheses, source gaps, unsafe proof-like language, and counterexample pressure. "
        "When you are reviewing work on a NAMED target subgoal or proof step, return a "
        "'scoped_objections' list: each item with text, scope (subgoal|proof_step|route|global), "
        "the matching subgoal_id or proof_step_id, kind (missing_source|hypothesis_gap|definition_gap|"
        "logical_gap|computation_needed|counterexample_pressure|unclear_claim|reviewer_disagreement|other), "
        "severity (P0 fatal flaw | P1 blocking step flaw | P2 blocking gap or source/computation request — the default | P3 non-blocking context note ONLY, never a step flaw), and a suggested_next_action. An objection only blocks or prioritizes "
        "repair; it never proves, refutes, or closes anything. "
        "Do not upgrade proof, evidence, source, or grade status. Return JSON only."
    )


def line_numbered_proof_writer_system_prompt() -> str:
    """Write a STRUCTURED, line-numbered proof (not a route / plan). Each
    line has a statement AND a justification AND its line dependencies. This is a
    CANDIDATE proof: it never claims to be machine-verified, source-backed, or proved;
    the kernel re-validates and the only seal it can earn is the explicitly-amber
    agent-consensus mock check."""
    return (
        "LINE-NUMBERED PROOF WRITER. Write a real, structured, line-numbered proof of "
        "the claim -- NOT a route, plan, or list of 'steps to try'. Carry out the "
        "argument: each line must actually assert a mathematical statement and give a "
        "concrete justification for WHY it follows (a rule of inference, an explicit "
        "algebraic manipulation, a case, or a cited lemma whose hypotheses you state). "
        "Number the lines and record a structured 'dependencies' graph for each line. "
        "Dependency types are prior_line, definition, arithmetic, theorem_ref, "
        "human_assumption, and unchecked_gap. Unchecked examples/counterexamples are "
        "pressure-lane context only; never cite them as proof dependencies. If a line "
        "can cite an existing source_grounding source_ref whose status is "
        "source_backed and whose statement directly covers the needed theorem or "
        "claim conclusion, prefer that citation over rebuilding the same theorem "
        "from unsupported internal lemmas. Such a dependency must use the exact "
        "source_ref id, authority 'source_grounded_theorem_ref', source_grounded=true, "
        "machine_verified=false, and the exact locator text in source_locator. "
        "Do not mark an unchecked or candidate-only source this way. If no "
        "source_backed ref covers the line, keep it missing/candidate as appropriate. "
        "If a line "
        "genuinely rests on an uncited external theorem, still give the justification "
        "and record a theorem_ref with status 'missing' plus an unchecked_gap. "
        "A theorem allowed by the user must be marked human_assumption or theorem_ref "
        "with authority 'human_allowed_theorem_ref', source_grounded=false, and "
        "machine_verified=false. Do not use human_assumption for the locked claim's "
        "own hypotheses; the first line may instantiate the arbitrary locked "
        "hypothesis with no dependency, or with definition/arithmetic dependencies "
        "only. Prior-line dependencies should be status 'resolved'; the referenced "
        "line carries any unresolved theorem/gap status itself. This is a CANDIDATE "
        "proof only: do NOT claim it is proved, verified, machine-checked, or "
        "source-certified -- the kernel decides status. "
        "Return JSON only: {\"proof_kind\": short label (e.g. parity_case_split | "
        "consecutive_integers | induction), \"lines\": [{\"line_id\": e.g. L1, "
        "\"statement\": the asserted math, \"justification\": why it follows, "
        "\"dependencies\": [{\"type\": prior_line|definition|arithmetic|theorem_ref|"
        "human_assumption|unchecked_gap, \"ref\": id, \"status\": resolved|missing|"
        "candidate|rejected|accepted_by_human, \"role\": short role, "
        "\"needed_statement\": optional theorem statement, \"source_locator\": optional, "
        "\"authority\": optional source_grounded_theorem_ref|human_allowed_theorem_ref|"
        "agent_claimed_theorem_ref. Use empty authority for prior_line, definition, "
        "arithmetic, and unchecked_gap; use agent_claimed_theorem_ref only for "
        "candidate theorem_ref dependencies, \"source_grounded\": bool, "
        "\"machine_verified\": bool}], "
        "\"depends_on\": [earlier line_ids], \"source_refs\": [optional candidate refs], "
        "\"gaps\": [optional acknowledged holes]}]}."
    )


def proof_consensus_system_prompt(reviewer_id: str) -> str:
    """The three reviewers review the LINE-NUMBERED PROOF (not route text)
    adversarially, and MUST cite the proof line_id(s) they examined. Each hunts for an
    argument-BREAKING flaw and returns {no_fatal_flaw_found, fatal_flaw_found,
    unclear}. A residual gap (a fixable missing citation, a definition to pin) is NOT
    fatal -> residual_objections. If a reviewer cannot CONFIRM soundness it returns
    'unclear' (blocks). Advisory mock check, NOT machine verification."""
    role_focus = {
        "line_proof_reviewer": (
            "LINE-PROOF REVIEWER: check that EACH line actually follows from its stated "
            "justification and its depends_on lines. A FATAL flaw is a line that does "
            "not follow, a non-sequitur, or a broken dependency chain."
        ),
        "theorem_lemma_applicability_reviewer": (
            "THEOREM/LEMMA APPLICABILITY REVIEWER: check that every cited theorem/lemma "
            "ACTUALLY applies -- its hypotheses are met by the line that invokes it. A "
            "FATAL flaw is a misapplied theorem (used outside its hypotheses) or a false "
            "lemma. A merely uncited-but-true standard fact is residual, not fatal."
        ),
        "adversarial_gap_reviewer": (
            "ADVERSARIAL GAP REVIEWER: actively attack the proof for a HOLE that breaks "
            "it -- a missing case, an unjustified leap, a hidden assumption, or a "
            "counterexample to the claim. A FATAL flaw is a gap that makes the argument "
            "invalid."
        ),
    }.get(reviewer_id, "review the proof for any argument-breaking flaw.")
    return (
        "PROOF-CONSENSUS REVIEW (advisory mock check, NOT machine verification). You "
        f"review the LINE-NUMBERED PROOF provided. {role_focus} "
        "You MUST cite the specific line_id(s) you examined in cited_line_ids; a verdict "
        "that cites no line is not a review. A residual (non-fatal, fixable) gap goes in "
        "residual_objections and does NOT block. If you cannot CONFIRM the proof is free "
        "of a fatal flaw, return 'unclear' -- do not guess. "
        "Return JSON only: {\"verdict\": one of no_fatal_flaw_found|fatal_flaw_found|"
        "unclear, \"looked_for\": the fatal-flaw classes you checked, \"finding\": your "
        "concrete finding referencing line_ids, \"cited_line_ids\": [the line_ids you "
        "examined], \"residual_objections\": [non-fatal gaps]}. You may NEVER set or "
        "upgrade proof/source/evidence/grade status; this verdict is advisory only."
    )


def proof_gap_resolver_system_prompt() -> str:
    return (
        "PROOF GAP RESOLVER. You inspect unresolved dependencies in a "
        "line_numbered_proof and propose candidate repairs only. Do not build a "
        "theorem library. Do not claim a theorem is source-backed, checked, proved, "
        "or machine-verified. This resolver has no trusted retrieval transcript in "
        "context, so any locator you name is only a candidate locator claim, not "
        "evidence that user material, project corpus, or the web was actually "
        "searched. Prefer precise candidate theorem/source queries; if you cannot "
        "name a plausible precise locator, keep the gap. A separate retrieval and "
        "applicability review must validate it before it can clear a dependency. "
        "Return JSON only: {\"candidates\": [{\"line_id\": proof line id, "
        "\"dependency_ref\": unresolved dependency ref, \"dependency_type\": "
        "theorem_ref|human_assumption|unchecked_gap, \"action\": "
        "attach_candidate_theorem_ref|keep_gap, \"proposed_ref\": stable candidate "
        "id, \"needed_statement\": statement needed by the proof line, "
        "\"proposed_statement\": theorem statement from source if found, "
        "\"source_locator\": exact theorem/section/page locator if found, "
        "\"source_url\": optional URL, \"source_kind\": user_material|project_corpus|"
        "web|paper|book|unknown, \"applicability_review\": not_reviewed|candidate_match|"
        "mismatch|unclear, \"reviewer_notes\": [short notes]}]}."
    )


def agent_prompt(context: dict, *, role: str) -> str:
    return json.dumps(
        {
            "role": role,
            "protocol_rules": [
                "candidate routes only",
                "no proof/source/evidence upgrades",
                "human gate before deeper work",
                "source support requires exact locator and matching hypotheses",
                "unchecked examples/counterexamples are pressure only, never proof support",
                "time_allocation.yaml is scheduling-only and advisory-only",
                "record missing source/theorem/example/counterexample context explicitly",
            ],
            "context": context,
            "required_json_schema": AGENT_OUTPUT_SCHEMA,
        },
        indent=2,
    )


def _reviewer_roles(reviewer_set: str, verifier_count: int) -> list[str]:
    if reviewer_set == "espresso":
        return ESPRESSO_REVIEWER_ROLES
    return ["verifier" for _ in range(max(3, verifier_count))]
