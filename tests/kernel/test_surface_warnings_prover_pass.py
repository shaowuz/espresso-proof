"""PR2: prover-pass aware surface warnings.

Once real prover-pass reviewers ran, (a) the front-half placeholder review may
not resurrect as a live warning, and (b) the memo must disclose the open
prover-pass blockers. prover_loop=None keeps today's behavior byte-identical.
"""

from __future__ import annotations

from espresso_proof.kernel.surface_warnings import (
    FRONT_HALF_REVIEW_PENDING_NOTE,
    PROVER_REVIEWER_BLOCKERS,
    collect_surface_warnings,
)


def _base_kwargs() -> dict:
    return {
        "grade": {},
        "examples": {},
        "counterexamples": {},
        "tests": {},
        "source_grounding": {},
    }


def _sentinel_review() -> dict:
    return {
        "reviewers": [{"reviewer_id": "front_half_reviewer", "verdict": "unclear"}],
        "downgrade_reasons": [FRONT_HALF_REVIEW_PENDING_NOTE],
        "strongest_objection": FRONT_HALF_REVIEW_PENDING_NOTE,
    }


def _loop(blockers: list[str]) -> dict:
    return {
        "attempts": [
            {
                "round_index": 1,
                "reviewer_ids": ["r1", "r2", "r3"],
                "blockers": blockers,
            }
        ]
    }


def test_prover_pass_supersedes_front_half_placeholder_and_discloses_blockers() -> None:
    warnings = collect_surface_warnings(
        review=_sentinel_review(),
        prover_loop=_loop(["S6 induction gap", "Lagrange locator needed"]),
        **_base_kwargs(),
    )
    texts = [w["text"] for w in warnings]
    assert not any(FRONT_HALF_REVIEW_PENDING_NOTE in t for t in texts)
    prover = [w for w in warnings if w["category"] == PROVER_REVIEWER_BLOCKERS]
    assert len(prover) == 1
    assert "2 open blocker(s) from round 1 (3 reviewer(s))" in prover[0]["text"]
    assert prover[0]["ids"] == ["S6 induction gap", "Lagrange locator needed"]


def test_no_prover_loop_keeps_todays_behavior_byte_identical() -> None:
    with_none = collect_surface_warnings(review=_sentinel_review(), **_base_kwargs())
    default = collect_surface_warnings(
        review=_sentinel_review(), prover_loop=None, **_base_kwargs()
    )
    assert with_none == default
    assert all(w["category"] != PROVER_REVIEWER_BLOCKERS for w in with_none)
    # the front-half line stays live when no prover pass exists
    assert any("Reviewer blockers still open" in w["text"] for w in with_none)


def test_prover_pass_with_no_open_blockers_emits_no_over_warning() -> None:
    # [hard-negative] a clean latest round must NOT emit the prover-pass
    # warning, while the front-half placeholder is still superseded.
    warnings = collect_surface_warnings(
        review=_sentinel_review(), prover_loop=_loop([]), **_base_kwargs()
    )
    assert all(w["category"] != PROVER_REVIEWER_BLOCKERS for w in warnings)
    assert not any(FRONT_HALF_REVIEW_PENDING_NOTE in w["text"] for w in warnings)


def test_a_front_half_reviewer_that_objected_is_still_reported() -> None:
    # A row carrying a real verdict survives the scaffold filter: it reviewed
    # something. No "placeholder" suffix is needed any more, because rows that
    # reviewed nothing no longer reach the line at all.
    review = {
        "reviewers": [{"reviewer_id": "front_half_reviewer", "verdict": "fail"}],
        "downgrade_reasons": ["a real front-half downgrade"],
        "strongest_objection": "a real objection",
    }
    warnings = collect_surface_warnings(
        review=review, prover_loop=_loop(["one blocker"]), **_base_kwargs()
    )
    front = [w for w in warnings if w["category"] == "reviewer_blockers"]
    assert front and "front_half_reviewer: blocking objection" in front[0]["text"]
    assert "front-half placeholder" not in front[0]["text"]


def test_unrun_scaffold_reviewers_are_not_open_blockers() -> None:
    """Observed on a live run: the report listed all three scaffold reviewers as
    "needs more review" and then admitted in the same sentence that prover-pass
    reviewers had superseded them. A row with verdict "unclear" and zero
    objections is an unrun reviewer, not a blocker."""

    review = {
        "reviewers": [
            {"reviewer_id": name, "verdict": "unclear", "objections": []}
            for name in ("strategy_reviewer", "evidence_reviewer", "line_proof_reviewer")
        ],
    }
    warnings = collect_surface_warnings(
        review=review, prover_loop=_loop(["one blocker"]), **_base_kwargs()
    )
    assert not [w for w in warnings if w["category"] == "reviewer_blockers"]
    # ...and the real prover-pass blockers are still reported.
    assert [w for w in warnings if w["category"] == PROVER_REVIEWER_BLOCKERS]


def test_the_report_says_what_the_run_produced_not_only_what_is_missing(tmp_path) -> None:
    """A live run selected a correct route, ran two rounds and collected 28
    reviewer objections, and its whole report was eight lines of what was
    missing. The reader could not tell whether anything had been produced."""

    from espresso_proof.kernel.io import write_yaml
    from espresso_proof.kernel.surface_warnings import run_output_block

    write_yaml(
        tmp_path / "run_manifest.yaml",
        {
            "artifacts": {"strategy_brief": "strategy_brief.yaml"},
            "optional_artifacts": {"prover_loop": "prover_loop.yaml"},
        },
    )
    write_yaml(
        tmp_path / "strategy_brief.yaml",
        {
            "top_strategies": [
                {"id": "r1", "title": "Discrete telescoping with a sign change"},
                {"id": "r2", "title": "Integral comparison"},
            ]
        },
    )
    write_yaml(
        tmp_path / "prover_loop.yaml",
        {
            "selected_strategy_id": "r1",
            "attempts": [
                {
                    "round_index": 1,
                    "reviewer_objections": [{"verifier_id": "a"}, {"verifier_id": "b"}],
                },
                {"round_index": 2, "reviewer_objections": [{"verifier_id": "a"}]},
            ],
        },
    )

    block = run_output_block(tmp_path)
    assert "Discrete telescoping with a sign change" in block
    assert "Routes proposed: 2" in block
    # Rounds and attempts are different facts and are not conflated.
    assert "Prover rounds run: 2 (2 route attempt(s))" in block
    assert "reviewer passes recorded: 3" in block
    # And it claims nothing: no proof/source/checked status anywhere.
    for forbidden in ("proved", "source-backed", "source_backed", "checked", "proof-close"):
        assert forbidden not in block


def test_a_run_with_nothing_to_report_adds_no_section(tmp_path) -> None:
    from espresso_proof.kernel.io import write_yaml
    from espresso_proof.kernel.surface_warnings import run_output_block

    write_yaml(tmp_path / "run_manifest.yaml", {"artifacts": {}, "optional_artifacts": {}})
    assert run_output_block(tmp_path) == ""


def test_counterexample_pressure_is_stated_in_words_a_reader_recognizes(tmp_path) -> None:
    """A live run on a FALSE claim (every proper subgroup abelian => G abelian)
    proposed S_3 and Q_8 — both correct — and never told the reader their claim
    looked false. The only mention was "Counterexample checks pending ... no
    counterexample pressure is not established", which describes missing support
    FOR the claim and reads backwards."""

    from espresso_proof.kernel.io import write_yaml
    from espresso_proof.kernel.surface_warnings import run_output_block

    write_yaml(
        tmp_path / "run_manifest.yaml",
        {"artifacts": {"counterexamples": "counterexamples.yaml"}, "optional_artifacts": {}},
    )
    write_yaml(
        tmp_path / "counterexamples.yaml",
        {"tests": [{"id": "cex_001"}, {"id": "cex_002"}]},
    )
    block = run_output_block(tmp_path)
    assert "Counterexample pressure: 2 candidate(s)" in block
    assert "cex_001, cex_002" in block
    # Conditional, because admissibility is the kernel's decision, not this
    # sentence's: it never says the claim IS false.
    assert "Each one is a proposed refutation of the claim" in block
    assert "undecided here" in block
    # Never a verdict, and never a sentence that becomes one when the
    # conditional in front of it is dropped by a quote.
    assert "is refuted" not in block
    assert "claim is false" not in block
    # And the memory boundary must accept it: an earlier wording was refused
    # there and rolled an entire front half back.
    from espresso_proof.kernel.models.run_lifecycle import _memory_forbidden_text_reason

    for line in block.splitlines():
        assert _memory_forbidden_text_reason(line) is None, line


def test_no_counterexamples_means_no_pressure_line(tmp_path) -> None:
    from espresso_proof.kernel.io import write_yaml
    from espresso_proof.kernel.surface_warnings import run_output_block

    write_yaml(
        tmp_path / "run_manifest.yaml",
        {"artifacts": {"counterexamples": "counterexamples.yaml"}, "optional_artifacts": {}},
    )
    write_yaml(tmp_path / "counterexamples.yaml", {"tests": []})
    assert "Counterexample pressure" not in run_output_block(tmp_path)


def test_the_residual_says_what_would_close_what_is_open() -> None:
    """Every audited run ended with tens of open blockers and no way to tell a
    run that got far from one that got nowhere. Grouping the same blockers by
    the ACTION that would close them is the difference between a list and a
    next step. Display only: every count comes from a warning already listed."""

    from espresso_proof.kernel.surface_warnings import (
        EXAMPLES_PENDING,
        PROVER_REVIEWER_BLOCKERS,
        SOURCE_GAPS,
        residual_block,
    )

    block = residual_block(
        [
            {"category": SOURCE_GAPS, "text": "x", "ids": ["g1", "g2"]},
            {"category": EXAMPLES_PENDING, "text": "y", "ids": ["ex_001"]},
            {"category": PROVER_REVIEWER_BLOCKERS, "text": "z", "ids": ["b1", "b2", "b3"]},
        ]
    )
    assert "1 item(s) waiting on a machine check the kernel can run." in block
    assert "2 item(s) waiting on an exact source locator" in block
    assert "3 item(s) waiting on a human decision." in block
    # Cheapest first: compute before reading before a person's time.
    assert "Cheapest next step: supply a machine check" in block
    assert residual_block([]) == ""


def test_one_gap_cannot_redact_the_others() -> None:
    """Observed live: the whole "Source gaps still open" line rendered as
    "[withheld advisory status phrase]". One gap said "candidate source needed"
    and a different one said "no step is reference-supported"; joined into a
    single line they matched the advisory-projection guard together, and four
    honest gap descriptions were erased by a pair of words that never appeared
    in the same sentence."""

    from espresso_proof.kernel.surface_warnings import render_warning_lines

    joined = (
        "Source gaps still open: No step is reference-supported yet, Cyclic "
        "structure of (Z/p^2Z)*: exact statement (candidate source needed)."
    )
    [line] = render_warning_lines([{"category": "source_gaps", "text": joined}])
    assert "withheld advisory status phrase" not in line
    assert "candidate source needed" in line
    assert "No step is reference-supported yet" in line
