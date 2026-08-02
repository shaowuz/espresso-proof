"""Three guards, one rule for what a denial is.

"Does this text assert a mathematical status?" was answered by separate pattern
lists in the redactor, the memory boundary, and the surface-warning renderer.
They disagreed, and the disagreement was not theoretical:

- round 4 relaxed the redactor so "If proved, ... would need to be located and
  verified" stayed readable; round 5's live run then died because the memory
  boundary read the same words as a proof claim and rolled back a paid front
  half;
- the surface-warning guard erased honest disclaimers ("a literature candidate
  is not evidence for this step") because it could not tell a denial from an
  assertion either.

The corpus below is the contract: every guard must agree on it, so the next
relaxation cannot desynchronise them again.
"""

from __future__ import annotations

import pytest

from espresso_proof.kernel.models.run_lifecycle import _memory_forbidden_text_reason
from espresso_proof.kernel.proof_language import safe_text
from espresso_proof.kernel.surface_warnings import _display_text

# Sentences that DENY or CONDITION a status. Every guard must let them through.
DENIALS = [
    "If proved, a foundational source would need to be located and verified.",
    "No step is verified.",
    "This has never been proven for n >= 3.",
    "The bound is yet to be proved.",
    "It cannot be verified without a locator.",
    "whether the bound is proved remains open",
]

# Sentences that ASSERT one. Every guard must react (redact, refuse, or wipe).
ASSERTIONS = [
    "We proved the theorem.",
    "The route is verified.",
    "The counterexample refuted the claim.",
]


@pytest.mark.parametrize("text", DENIALS)
def test_every_guard_lets_a_denial_through(text: str) -> None:
    assert safe_text(text) == text
    assert _memory_forbidden_text_reason(text) is None
    assert "withheld" not in _display_text(text)


@pytest.mark.parametrize("text", ASSERTIONS)
def test_every_guard_still_catches_an_assertion(text: str) -> None:
    caught_by_redactor = "[withheld" in safe_text(text)
    caught_by_memory = _memory_forbidden_text_reason(text) is not None
    assert caught_by_redactor or caught_by_memory
    assert caught_by_memory, text


ADVISORY_DENIALS = [
    "A literature candidate is not evidence for this step.",
    "The prover output does not establish the claim.",
    "No candidate source supports any step yet.",
]

ADVISORY_ASSERTIONS = [
    "Candidate sources support this step.",
    "The reviewer verdict confirms the route.",
]


@pytest.mark.parametrize("text", ADVISORY_DENIALS)
def test_the_advisory_guard_keeps_a_disclaimer(text: str) -> None:
    assert "withheld advisory status phrase" not in _display_text(text)


@pytest.mark.parametrize("text", ADVISORY_ASSERTIONS)
def test_the_advisory_guard_still_wipes_a_projection(text: str) -> None:
    assert "withheld advisory status phrase" in _display_text(text)


def test_the_denial_rule_has_exactly_one_implementation() -> None:
    """A second copy is how they drifted apart the first time."""

    import inspect

    from espresso_proof.kernel import surface_warnings
    from espresso_proof.kernel.models import run_lifecycle
    from espresso_proof.kernel.proof_language import is_denied_occurrence

    assert inspect.getsource(is_denied_occurrence)
    # Both other guards reach the rule by importing it, never by re-deriving it.
    assert "is_denied_occurrence" in inspect.getsource(surface_warnings)
    assert "neutralize_denied_verbs" in inspect.getsource(run_lifecycle)
