"""What each model call is given, and what it costs.

A fourth hostile review looked specifically at whether agent invocation is well
designed. It was not, in five ways that share one shape: the call site knew
exactly which role it was invoking, and then threw that knowledge away, leaving
downstream code to re-infer it from prompt text — or to skip the question
entirely.

- Skill selection scanned system+prompt for trigger substrings. The prompt
  embeds the whole run context, so vocabulary present in EVERY run
  ("strategy_synthesis.yaml", "proof_strategy", "counterexample") matched almost
  everything: one live prover call shipped six skills, including both reviewer
  rubrics it was supposed to be judged by.
- Deep prover-loop rounds called the client directly, so they produced no
  per-call diagnostics (a fallback round was indistinguishable from a real one
  in the durable artifact) and declared no role at all.
- All three reviewers received the byte-identical payload, so the only thing
  separating them was one sentence of system prompt.
- Applicability review called the model twice per usage in an uncapped loop
  whose length was itself model-controlled.
- Every call got the same 1200-token output budget, including roles whose
  prompts ask for a whole line-numbered proof.
"""

from __future__ import annotations

import pytest

from espresso_proof.agents.agent_skill_catalog import select_agent_skills, skills_for_role
from espresso_proof.agents.clients import _max_output_tokens_for, _retry_backoff_seconds
from espresso_proof.agents.prompts import (
    line_numbered_proof_writer_system_prompt,
    prover_system_prompt,
    reviewer_context,
    verifier_system_prompt,
)

# The shape of a real payload: run context, whose vocabulary trips many triggers.
NOISY_PROMPT = (
    '{"role": "prover_loop_round", "context": {"manifest": {"artifacts": '
    '{"strategy_synthesis": "strategy_synthesis.yaml"}}, "review": '
    '{"scope": "proof_strategy"}, "counterexamples": {"tests": []}, '
    '"line_by_line_proof": null, "source gap": "none"}}'
)


# --- selection must follow the declared role, not the prompt's vocabulary


def test_a_declared_role_selects_exactly_its_own_skill() -> None:
    scanned = select_agent_skills(system=prover_system_prompt(), prompt=NOISY_PROMPT)
    declared = select_agent_skills(
        system=prover_system_prompt(), prompt=NOISY_PROMPT, role="prover"
    )
    assert declared.skills == ("prover",)
    # The defect, pinned: the scan pulls in skills this call has no use for.
    assert len(scanned.skills) > len(declared.skills)
    assert len(declared.skill_text) < len(scanned.skill_text)


def test_a_prover_is_not_handed_the_reviewers_rubrics() -> None:
    declared = select_agent_skills(
        system=prover_system_prompt(), prompt=NOISY_PROMPT, role="prover"
    )
    for reviewer_skill in ("proof-outline-reviewer", "line-proof-reviewer", "strategy-grader"):
        assert reviewer_skill not in declared.skills


@pytest.mark.parametrize(
    "role,expected",
    [
        ("understanding_claim", ("claim-understanding",)),
        ("literature_review", ("literature-review",)),
        ("prover", ("prover",)),
        ("strategy_reviewer", ("proof-outline-reviewer",)),
        ("evidence_reviewer", ("literature-use-reviewer",)),
        ("line_proof_reviewer", ("line-proof-reviewer",)),
        ("line_numbered_proof_writer", ("proof-writer",)),
        # Round-lane reviewers carry a round prefix; the trailing role decides.
        ("round_003_strategy_reviewer", ("proof-outline-reviewer",)),
    ],
)
def test_every_shipped_role_maps_to_its_skill(role: str, expected: tuple) -> None:
    assert skills_for_role(role) == expected


def test_an_unknown_role_falls_back_to_the_scan() -> None:
    # Third-party callers and any future role must not silently get no skill.
    assert skills_for_role("something_new") == ()
    selection = select_agent_skills(
        system=prover_system_prompt(), prompt=NOISY_PROMPT, role="something_new"
    )
    assert selection.skills  # the scan still answers


# --- each reviewer must be given the evidence its rubric is about


def _full_context() -> dict:
    return {
        key: {"marker": key}
        for key in (
            "claim", "hypothesis_ledger", "manifest", "literature_review", "examples",
            "computation_checks", "counterexamples", "tests", "source_grounding",
            "review", "idea_grade", "strategy_brief", "prover_attempt",
        )
    }


def test_the_three_reviewers_no_longer_get_identical_payloads() -> None:
    context = _full_context()
    payloads = {
        role: set(reviewer_context(context, reviewer_role=role))
        for role in ("strategy_reviewer", "evidence_reviewer", "line_proof_reviewer")
    }
    assert payloads["strategy_reviewer"] != payloads["evidence_reviewer"]
    assert payloads["evidence_reviewer"] != payloads["line_proof_reviewer"]
    assert payloads["strategy_reviewer"] != payloads["line_proof_reviewer"]


def test_each_reviewer_keeps_the_evidence_its_rubric_names() -> None:
    context = _full_context()
    evidence = reviewer_context(context, reviewer_role="evidence_reviewer")
    for key in ("examples", "counterexamples", "computation_checks", "source_grounding"):
        assert key in evidence, key
    strategy = reviewer_context(context, reviewer_role="strategy_reviewer")
    assert "strategy_brief" in strategy and "idea_grade" in strategy


def test_every_reviewer_keeps_the_claim_and_the_work_under_review() -> None:
    context = _full_context()
    for role in ("strategy_reviewer", "evidence_reviewer", "line_proof_reviewer"):
        scoped = reviewer_context(context, reviewer_role=role)
        assert "claim" in scoped and "prover_attempt" in scoped
        # And the reviewer is told its payload was scoped, so a missing artifact
        # is not read as an absent artifact.
        assert "scoped to the" in scoped["context_scope_note"]


def test_an_unknown_reviewer_role_sees_everything() -> None:
    context = _full_context()
    assert reviewer_context(context, reviewer_role="verifier") == context


# --- cost knobs must match what the prompts ask for


def test_output_budget_follows_the_role() -> None:
    prover = _max_output_tokens_for(prover_system_prompt())
    reviewer = _max_output_tokens_for(verifier_system_prompt(1, reviewer_role="strategy_reviewer"))
    writer = _max_output_tokens_for(line_numbered_proof_writer_system_prompt())
    assert writer > prover > reviewer > _max_output_tokens_for("an unrecognized role")


def test_retries_back_off_instead_of_hammering() -> None:
    delays = [_retry_backoff_seconds(i) for i in range(4)]
    assert delays == sorted(delays) and delays[0] > 0
    assert all(d <= 20.0 for d in delays)  # bounded
    # Deterministic: a run must stay reproducible.
    assert delays == [_retry_backoff_seconds(i) for i in range(4)]


# --- the uncapped fan-out


def test_applicability_review_fan_out_is_capped() -> None:
    from espresso_proof.agents import theorem_applicability_review as review

    assert review._MAX_REVIEWED_USAGES > 0
    # Two calls per usage; the whole invocation is bounded by that product.
    assert review._MAX_REVIEWED_USAGES * 2 <= 20


# --- deep rounds must record what the initial pass records


def test_appended_rounds_carry_per_call_diagnostics() -> None:
    import inspect

    from espresso_proof.agents import prover_loop
    from espresso_proof.kernel.models import ProverRoundReport

    assert "agent_call_diagnostics" in ProverRoundReport.model_fields
    source = inspect.getsource(prover_loop.append_prover_loop_round)
    # The round's model calls go through the shared wrapper (which stamps the
    # role and collects the adapter envelope), not straight to the client.
    assert "_complete_agent_json" in source
    assert "client.complete_json(" not in source
