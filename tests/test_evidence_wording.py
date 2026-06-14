"""Human-readable mathematical evidence wording for user-facing surfaces.

These pin that the memo / inspect / report renderers translate internal status
enums (``SourceStatus``, ``ComputationStatus``, ``ProofVerificationStatus``,
``RetrievedSourceStatus``, ``FailurePatternTier`` ...) into plain
mathematical-evidence phrases, and never print the raw enum tokens to the user.
See ``docs/engineering/EVIDENCE_DISPLAY_WORDING.md``.

This is a presentation-layer test only: it asserts wording, never status
semantics. Internal enums stay in schemas/validators (asserted elsewhere).
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.descriptions import (
    evidence_phrase,
    judgment_tier_phrase,
    objection_phrase,
)
from espresso_proof.failure_pattern_bank import audit_failure_patterns
from espresso_proof.mathematical_judgment import judge_run
from espresso_proof.memo import (
    PROOF_ROUTE_STEP_HEADINGS,
    build_concise_memo,
    render_proof_route_steps,
)
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.proof_verification import verify_finite_groups

FIXTURES = Path("tests/fixtures")


def _grade(run_dir: Path) -> dict:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts["idea_grade"])


def _artifact_path(run_dir: Path, key: str) -> Path:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return run_dir / artifacts[key]

# Internal enum tokens that must never appear verbatim in renderer-controlled
# output (status fields the renderer formats into labels). The English word
# "candidate"/"checked" inside human phrases such as "a candidate argument" or
# "computation-checked result" is allowed.
RAW_ENUM_TOKENS = (
    "source_backed",
    "computation_checked",
    "instance_checked",
    "human_needed",
    "not_found",
    "unverified",
    "[candidate]",
    "[audited]",
    "[hard]",
)

# Internal status enum values surfaced as bracketed/labelled tiers. The renderer
# must translate every one of these; they must never reach the user as a label.
RAW_TIER_LABELS = ("[candidate]", "[audited]", "[hard]", "[info]", "[warn]", "[block_candidate]")


def _assert_no_raw_tokens(text: str) -> None:
    for token in RAW_ENUM_TOKENS:
        assert token not in text, f"user-facing output exposed raw enum token {token!r}"


def _assert_no_tier_labels(text: str) -> None:
    for token in RAW_TIER_LABELS:
        assert token not in text, f"user-facing output exposed raw tier label {token!r}"


# --------------------------------------------------------------------------- #
# Pure helper tables: exact required wording + no raw token echoed back.
# --------------------------------------------------------------------------- #


def test_evidence_phrase_required_wording() -> None:
    assert (
        evidence_phrase("source_backed")
        == "Reference supports this stated claim; see locator and applicability reason"
    )
    assert (
        evidence_phrase("computation_checked")
        == "Checked by computation for the stated finite/computational scope"
    )
    assert evidence_phrase("instance_checked") == "Checked in this finite case only"
    assert evidence_phrase("candidate") == "Proposed, not checked"
    assert evidence_phrase("human_needed") == "Needs human judgment"
    assert evidence_phrase("not_found") == "Needs a source"
    assert evidence_phrase("unverified") == "Needs a check"
    # An unknown/missing status fails closed to "needs a check", never to support.
    assert evidence_phrase("totally_unknown") == "Needs a check"
    assert evidence_phrase(None) == "Needs a check"
    # The phrase never echoes the raw token back.
    for token in ("source_backed", "computation_checked", "instance_checked",
                  "human_needed", "not_found", "unverified"):
        assert token not in evidence_phrase(token)


def test_objection_phrase_required_wording() -> None:
    assert objection_phrase("hard") == "Objection: matches a known failure pattern"
    assert objection_phrase("audited") == "Objection: matches a reviewed warning pattern"
    assert objection_phrase("candidate") == "Objection to review: possible failure pattern"
    # Unknown tier falls back to the most conservative (to-review) phrasing.
    assert objection_phrase("weird") == "Objection to review: possible failure pattern"
    for token in ("hard", "audited", "candidate"):
        assert token not in objection_phrase(token)


def test_judgment_tier_phrase_is_readable_severity() -> None:
    assert judgment_tier_phrase("info") == "Note"
    assert judgment_tier_phrase("warn") == "Caution"
    assert judgment_tier_phrase("block_candidate") == "Serious concern, needs human review"
    # "block_candidate" must not leak (it contains the substring "candidate").
    assert "candidate" not in judgment_tier_phrase("block_candidate")


# --------------------------------------------------------------------------- #
# Memo proof-route step shape: Statement / Evidence phrase / Evidence detail /
# Scope of check or source.
# --------------------------------------------------------------------------- #


def test_render_proof_route_steps_uses_evidence_shape() -> None:
    source_grounding = {
        "source_refs": [
            {
                "id": "ref_1",
                "authors": ["A. Mathematician"],
                "title": "On Finite p-Groups",
                "locator": {"kind": "theorem", "value": "1.1"},
            }
        ],
        "steps": [
            {
                "proof_step_id": "s1",
                "needed_theorem": "Every finite p-group has nontrivial center.",
                "status": "source_backed",
                "source_refs": ["ref_1"],
            },
            {
                "proof_step_id": "s2",
                "needed_theorem": "unbacked step",
                "status": "candidate",
                "source_refs": [],
            },
        ],
    }
    computations = {
        "checks": [
            {
                "id": "c1",
                "computed_quantity": "|G/Z(G)| for |G|=4, |Z(G)|=4",
                "status": "checked",
                "machine_check": {
                    "kind": "exact_arithmetic",
                    "expression": "4 / 4",
                    "expected": "1",
                },
            },
            {
                "id": "c2",
                "computed_quantity": "unchecked",
                "status": "human_needed",
                "machine_check": None,
            },
        ],
    }
    # A theorem-applicability audit recording the satisfied "why it applies".
    theorem_applicability = {
        "usages": [
            {
                "proof_step_id": "s1",
                "conditions": [
                    {
                        "status": "satisfied",
                        "text": "The group is a finite p-group.",
                    }
                ],
            }
        ]
    }
    lines = render_proof_route_steps(source_grounding, computations, theorem_applicability)
    block = "\n".join(lines)

    # The required seven-field shape is present for each rendered step.
    for heading in PROOF_ROUTE_STEP_HEADINGS:
        assert block.count(heading) == 2, heading  # one source step + one computation

    # The earned evidence phrases appear; the raw status tokens do not.
    assert "Reference supports this stated claim" in block
    assert "Checked by computation for the stated finite/computational scope" in block
    assert "Every finite p-group has nontrivial center." in block
    assert "A. Mathematician, On Finite p-Groups [theorem:1.1]" in block
    # The recorded applicability reason is surfaced (not invented).
    assert "Why it applies: The group is a finite p-group." in block
    # Conservative defaults for fields with no per-step artifact data.
    assert "Reviewer objections: none recorded" in block
    assert "Remaining risk: this evidence does not prove the full theorem" in block
    _assert_no_raw_tokens(block)

    # Only earned steps render: the candidate step and the human_needed
    # computation are not echoed as proof-route evidence.
    assert "unbacked step" not in block
    assert "unchecked" not in block


def test_source_backed_step_does_not_invent_applicability_reason() -> None:
    """With no recorded applicability reason, "Why it applies" stays conservative."""

    source_grounding = {
        "source_refs": [
            {
                "id": "ref_1",
                "authors": ["A. Mathematician"],
                "title": "On Finite p-Groups",
                "locator": {"kind": "theorem", "value": "1.1"},
            }
        ],
        "steps": [
            {
                "proof_step_id": "s1",
                "needed_theorem": "Every finite p-group has nontrivial center.",
                "status": "source_backed",
                "source_refs": ["ref_1"],
            }
        ],
    }
    # No theorem_applicability passed at all.
    lines = render_proof_route_steps(source_grounding, {"checks": []})
    block = "\n".join(lines)
    assert "Why it applies: Needs a check" in block

    # An applicability audit that exists but records no satisfied reason for the
    # step also must not be turned into an invented reason.
    empty_reason = {
        "usages": [
            {
                "proof_step_id": "s1",
                "conditions": [
                    {"status": "mismatched", "text": "some unmet condition"},
                    {"status": "satisfied", "text": ""},
                ],
            }
        ]
    }
    block2 = "\n".join(render_proof_route_steps(source_grounding, {"checks": []}, empty_reason))
    assert "Why it applies: Needs a check" in block2
    # An unsatisfied condition's text is never promoted into the applies reason.
    assert "some unmet condition" not in block2


# --------------------------------------------------------------------------- #
# End-to-end: memo + inspect over a run with the optional advisory artifacts.
# --------------------------------------------------------------------------- #


def _run_with_optional_artifacts(tmp_path: Path) -> Path:
    # This fixture triggers candidate + audited + hard failure-pattern matches and
    # a misapplied source status -- every advisory tier the renderer must translate.
    run = run_mock_critique(
        "wd", FIXTURES / "source_misapplication_very_general_to_all" / "input.md", tmp_path
    )
    judge_run(run)
    audit_failure_patterns(run)
    verify_finite_groups(run, primes=[2, 3])
    return run


# NOTE on scope: these renderers translate every *status field* into evidence
# wording. They still echo producer-authored free-text artifact fields verbatim
# (e.g. a strategy route's ``main_risk`` prose, or a judgment finding's
# ``explanation``), which may quote an internal token inside a sentence. Rewording
# that upstream prose belongs to a separate goal (it touches artifact producers,
# not the presentation layer). These tests therefore pin the renderer-controlled
# label surfaces: no status enum is shown as a bracketed/labelled value, and the
# required evidence phrases are used.


def test_memo_label_surfaces_use_evidence_wording(tmp_path: Path) -> None:
    run = _run_with_optional_artifacts(tmp_path)
    memo = build_concise_memo(run)
    # No status enum reaches the user as a bracketed/labelled tier.
    _assert_no_tier_labels(memo)
    # Advisory failure-pattern objections render in the required human wording.
    assert "Objection: matches a known failure pattern" in memo
    assert "Objection: matches a reviewed warning pattern" in memo
    assert "Objection to review: possible failure pattern" in memo
    # A misapplied source status renders as plain provenance wording.
    assert "A reviewer found a gap in how the source applies" in memo
    # Scoped finite checks render in evidence wording, never as a proof.
    assert "Checked in this finite case only" in memo


def test_inspect_label_surfaces_use_evidence_wording(tmp_path: Path) -> None:
    run = _run_with_optional_artifacts(tmp_path)
    result = CliRunner().invoke(app, ["inspect", "--run", str(run)])
    assert result.exit_code == 0, result.output
    out = result.output
    _assert_no_tier_labels(out)
    assert "Objection" in out
    assert "Checked in this finite case only" in out
    # The "evidence so far" line shows translated provenance, never a status enum.
    evidence_line = next(
        (line for line in out.splitlines() if "evidence so far:" in line), None
    )
    assert evidence_line is not None
    _assert_no_raw_tokens(evidence_line)


def test_memo_cli_command_matches_builder(tmp_path: Path) -> None:
    run = _run_with_optional_artifacts(tmp_path)
    result = CliRunner().invoke(app, ["memo", "--run", str(run)])
    assert result.exit_code == 0, result.output
    _assert_no_tier_labels(result.output)
    assert build_concise_memo(run) in result.output


def _grade_label_lines(memo: str) -> str:
    """The structured grade-label lines of a memo (Summary / Verdict / Proof state)."""

    wanted = ("Summary:", "Verdict:", "Proof state:")
    return "\n".join(
        line for line in memo.splitlines() if line.startswith(wanted)
    )


def test_likely_false_not_shown_as_raw_status_label(tmp_path: Path) -> None:
    run = run_mock_critique(
        "lf", FIXTURES / "likely_false_multiple_pressures" / "input.md", tmp_path
    )
    grade = _grade(run)
    assert grade["status"] == "likely_false"  # the fixture exercises this status
    labels = _grade_label_lines(build_concise_memo(run))
    # The structured grade labels never show the raw enum token...
    assert "likely_false" not in labels
    # ...they show the concrete-English description instead.
    assert "no checked counterexample has been verified" in labels


def test_fragile_not_shown_as_raw_status_label(tmp_path: Path) -> None:
    run = run_mock_critique(
        "fr", FIXTURES / "source_misapplication_very_general_to_all" / "input.md", tmp_path
    )
    grade = _grade(run)
    assert grade["status"] == "fragile"  # the fixture exercises this status
    labels = _grade_label_lines(build_concise_memo(run))
    assert "fragile" not in labels
    assert "missing hypotheses, source gaps, or counterexample pressure" in labels


def test_next_cheapest_test_with_refutation_language_is_scoped(tmp_path: Path) -> None:
    run = run_mock_critique(
        "future-scope",
        FIXTURES / "likely_false_multiple_pressures" / "input.md",
        tmp_path,
    )
    grade_path = _artifact_path(run, "idea_grade")
    grade = read_yaml(grade_path)
    grade["next_cheapest_test"] = (
        "Run a future computation refuting the universal claim if the obstruction persists."
    )
    write_yaml(grade_path, grade)

    memo = build_concise_memo(run)
    assert "Next cheapest test: Run a future computation refuting the universal claim" in memo
    assert (
        "Scope: proposed next check only; not current evidence, source support, "
        "proof, or refutation."
    ) in memo


def test_rendering_does_not_mutate_persisted_artifacts(tmp_path: Path) -> None:
    """Rendering is read-only: memo + inspect never rewrite any run artifact."""

    run = _run_with_optional_artifacts(tmp_path)
    before = {p: p.read_bytes() for p in sorted(run.glob("*.yaml"))}
    assert before  # sanity: there are artifacts to protect

    # Render every user-facing surface that this PR touches.
    build_concise_memo(run)
    assert CliRunner().invoke(app, ["memo", "--run", str(run)]).exit_code == 0
    assert CliRunner().invoke(app, ["inspect", "--run", str(run)]).exit_code == 0

    after = {p: p.read_bytes() for p in sorted(run.glob("*.yaml"))}
    assert after.keys() == before.keys(), "rendering added/removed an artifact file"
    for path, original in before.items():
        assert after[path] == original, f"rendering mutated persisted artifact {path.name}"


# --------------------------------------------------------------------------- #
# Goal 22: a reference-supported step is step-scoped, never a global proof.
# --------------------------------------------------------------------------- #
def _source_backed_step_grounding() -> dict:
    return {
        "source_refs": [
            {
                "id": "ref_1",
                "authors": ["A. Mathematician"],
                "title": "On Finite p-Groups",
                "locator": {"kind": "theorem", "value": "1.1"},
            }
        ],
        "steps": [
            {
                "proof_step_id": "s1",
                "needed_theorem": "Every finite p-group has nontrivial center.",
                "status": "source_backed",
                "source_refs": ["ref_1"],
            }
        ],
    }


def test_source_backed_step_is_step_scoped_not_global_proof() -> None:
    # reference_substep_not_global_theorem: a source-backed step renders as
    # support for THIS step only, with citation + locator, and never as a global
    # proof of the claim/route. No proof-complete language appears.
    from espresso_proof.proof_language import contains_proof_language

    block = "\n".join(render_proof_route_steps(_source_backed_step_grounding(), {"checks": []}))
    assert "this step only" in block
    assert "not a proof of the whole claim or route" in block
    # citation + exact locator are shown for the step
    assert "A. Mathematician, On Finite p-Groups [theorem:1.1]" in block
    # the step statement is shown; no global proof-complete language leaks
    assert "Every finite p-group has nontrivial center." in block
    assert contains_proof_language(block) is None
    # the user-facing evidence phrase reads as reference SUPPORT, not "proved"
    assert "proved" not in evidence_phrase("source_backed").lower()
    assert "proof-close" not in evidence_phrase("source_backed").lower()


def test_source_backed_evidence_phrase_is_reference_support_only() -> None:
    from espresso_proof.proof_language import contains_proof_language

    phrase = evidence_phrase("source_backed")
    assert "Reference supports" in phrase
    assert contains_proof_language(phrase) is None


def test_source_backed_step_statement_with_proof_language_is_redacted() -> None:
    # A real guarantee (not fixture-dependent): even if a step's echoed free text
    # smuggles global proof-complete language, the rendered step redacts it.
    from espresso_proof.proof_language import contains_proof_language

    grounding = _source_backed_step_grounding()
    grounding["steps"][0]["needed_theorem"] = (
        "This completes the proof. proof-close. We proved the theorem."
    )
    block = "\n".join(render_proof_route_steps(grounding, {"checks": []}))
    assert contains_proof_language(block) is None
    assert "[withheld proof-like phrase]" in block
