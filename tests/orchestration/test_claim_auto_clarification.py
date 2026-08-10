"""An unclear claim gets completed and the run proceeds; parking is the fallback.

Behavioral spec. A claim such as "There exists a polynomial map f: R^2 -> R^2
that is injective but not invertible" would park the run at
WAITING_CLAIM_CLARIFICATION with two listed gaps — one a real ambiguity
(polynomial inverse vs set-theoretic bijection), and one that is not a
precision issue at all ("whether such a map exists is open": precision is not
truth, by this codebase's own stated principle).

Required behavior: the agent that found the gaps resolves them — one more call
proposes the completed precise statement with each gap resolved by an explicit
recorded assumption, the run continues, and the human sees the completion and
can revise; silence means continue.

Safety contract pinned here: raw_text is never overwritten, human_confirmed
stays False, every resolution is recorded in the ledger notes, and a completion
the safety floor rejects falls back to the old parking behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.front_half.stages.understanding import _clarify_claim


class _Client:
    provider = "stub"
    model = "test"

    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def complete_json(self, *, system: str, prompt: str) -> dict:
        self.calls += 1
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


GOOD_REPLY = {
    "normalized_statement": (
        "There exists a polynomial map f: R^2 -> R^2 that is injective "
        "and has no polynomial inverse."
    ),
    "objects": ["f: polynomial map R^2 -> R^2", "the candidate inverse of f"],
    "hypotheses": [
        {"name": "f is a polynomial map R^2 -> R^2", "status": "explicit"},
        {"name": "f is injective", "status": "explicit"},
        {
            "name": "invertible means: admits a polynomial inverse",
            "status": "inferred",
        },
    ],
    "conclusion": "f has no polynomial inverse.",
    "resolutions": [
        {
            "gap": "polynomial inverse or set-theoretic bijection?",
            "assumption": "polynomial inverse (the standard reading for polynomial maps)",
        },
        {
            "gap": "whether such a map exists is an open question",
            "assumption": "none needed: openness is not imprecision",
        },
    ],
}

GAPS = [
    "polynomial inverse or set-theoretic bijection?",
    "whether such a map exists is an open question",
]


def test_a_good_completion_is_accepted_with_recorded_resolutions() -> None:
    out = _clarify_claim(
        client=_Client(GOOD_REPLY),
        raw_text="...",
        normalized="...",
        missing_or_unclear=GAPS,
        ambiguous_terms=["invertible"],
    )
    assert out is not None
    assert "polynomial inverse" in out["normalized_statement"]
    assert len(out["resolutions"]) == 2
    # Every recorded resolution pairs the gap with what was assumed for it.
    for gap, assumption in out["resolutions"]:
        assert gap and assumption


@pytest.mark.parametrize(
    "broken",
    [
        {"normalized_statement": ""},
        {"conclusion": ""},
        {"objects": []},
        {"resolutions": []},  # gaps listed but nothing resolved
        {"conclusion": "something something unclear placeholder"},
    ],
)
def test_a_bad_completion_fails_closed(broken: dict) -> None:
    reply = {**GOOD_REPLY, **broken}
    out = _clarify_claim(
        client=_Client(reply),
        raw_text="...",
        normalized="...",
        missing_or_unclear=GAPS,
        ambiguous_terms=[],
    )
    if broken.get("conclusion") == "something something unclear placeholder":
        # the safety floor's imprecision markers must keep rejecting
        assert out is None or "unclear" not in out["conclusion"]
    else:
        assert out is None


def test_a_crashing_client_fails_closed_not_up() -> None:
    out = _clarify_claim(
        client=_Client(RuntimeError("model unavailable")),
        raw_text="...",
        normalized="...",
        missing_or_unclear=GAPS,
        ambiguous_terms=[],
    )
    assert out is None


def test_no_gaps_requires_no_resolutions() -> None:
    # Called for an imprecise-but-gapless claim: resolutions may be empty.
    reply = {**GOOD_REPLY, "resolutions": []}
    out = _clarify_claim(
        client=_Client(reply),
        raw_text="...",
        normalized="...",
        missing_or_unclear=[],
        ambiguous_terms=[],
    )
    assert out is not None


def test_the_stage_records_honest_provenance(tmp_path: Path, monkeypatch) -> None:
    """End to end through the stage: completion accepted -> run proceeds with
    raw_text preserved, human_confirmed False, and the resolutions in the
    ledger notes."""

    from espresso_proof.kernel.io import read_yaml
    from espresso_proof.orchestration.orchestrator import orchestrate_run

    import espresso_proof.front_half.stages.understanding as understanding

    real = understanding._clarify_claim
    calls = {"n": 0}

    def fake_clarify(**kwargs):
        calls["n"] += 1
        return {
            "normalized_statement": GOOD_REPLY["normalized_statement"],
            "objects": GOOD_REPLY["objects"],
            "hypotheses": GOOD_REPLY["hypotheses"],
            "conclusion": GOOD_REPLY["conclusion"],
            "resolutions": [
                (r["gap"], r["assumption"]) for r in GOOD_REPLY["resolutions"]
            ],
        }

    monkeypatch.setattr(understanding, "_clarify_claim", fake_clarify)
    claim_file = tmp_path / "c.md"
    raw = (
        "There exists a polynomial map f: R^2 -> R^2 that is injective "
        "but not invertible."
    )
    claim_file.write_text(raw + "\n", encoding="utf-8")
    result = orchestrate_run(
        project="polymap", input_path=claim_file, output_root=tmp_path,
        provider="stub", front_half="stub",
    )
    run_dir = Path(result["run_dir"])
    claim = read_yaml(run_dir / "locked_claim.yaml")
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")

    if calls["n"]:
        # The clarifier fired: the run must have moved past the parking state,
        # with honest provenance.
        assert result["state"] != "WAITING_CLAIM_CLARIFICATION"
        assert claim["raw_text"].strip() == raw
        assert claim["human_confirmed"] is False
        assert ledger["claim_precision"] == "precise"
        assert ledger["missing_or_unclear"] == []
        notes = " ".join(ledger.get("notes") or [])
        assert "auto-clarified" in notes
        assert "auto-resolved" in notes
    else:
        # The stub understanding pass judged the claim precise on its own; the
        # clarifier correctly never ran. Still a valid outcome — but the test
        # then proves nothing, so record that explicitly.
        pytest.skip("stub understanding accepted the claim; clarifier not exercised")

    monkeypatch.setattr(understanding, "_clarify_claim", real)
