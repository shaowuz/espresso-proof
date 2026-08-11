"""Language System: one product vocabulary across entry page, run page,
memo, and errors — no implementation words on the main user path."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.ui.descriptions import (
    PRODUCT_COPY,
    effort_phrase,
    loop_stop_phrase,
    product_copy,
)
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench, _friendly_error
from espresso_proof.kernel.validators import validate_run

from tests.support_jobs import wait_for_job

CLAIM = "Every group of order p^2, where p is prime, is abelian."

# Implementation TOKENS that must never appear anywhere on the main user path
# (these are never legitimate English in math prose).
_FORBIDDEN_TOKENS = (
    "Traceback", ".yaml", "source_backed", "computation_checked",
    "human_needed", "idea_sketch", "likely_false", "not_run", "quick_test",
)
# Implementation WORDS forbidden in the product's own chrome. Echoed artifact
# free text may legitimately use these as plain English (e.g. "every external
# theorem step"), so they are only checked on pages whose visible text is all
# product copy (entry page; fresh run page).
_FORBIDDEN_CHROME = ("stub", "external", "provider", "artifact", "validator")
_FORBIDDEN_VISIBLE = _FORBIDDEN_TOKENS + _FORBIDDEN_CHROME


def _run(tmp_path: Path, name: str = "lang") -> Path:
    claim = tmp_path / f"{name}.md"
    claim.write_text(CLAIM, encoding="utf-8")
    return orchestrate_run(
        project=name, input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]


def _visible_text(html: str) -> str:
    """The user-visible main path: drop the script island and debug drawers."""

    import re

    body = html.split("<script>")[0]
    body = re.sub(r"<style>.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<details class=\"debug\".*?</details>", " ", body, flags=re.S)
    # drop tags + attribute values (data-*, title, href) — only rendered text matters
    body = re.sub(r"<[^>]+>", " ", body)
    return body


# --------------------------------------------------------------------------- #
# language map
# --------------------------------------------------------------------------- #


def test_copy_inventory_covers_required_states() -> None:
    required = {
        "waiting", "running", "partial_result", "failed_job", "retrying",
        "skipped_agent", "missing_source", "needs_locator",
        "needs_hypothesis_match", "route_attack", "dropped_hypothesis",
        "admissible_refutation", "scoped_computation", "smoke_checked",
        "reference_supported",
    }
    assert required.issubset(PRODUCT_COPY.keys())
    for state, sentence in PRODUCT_COPY.items():
        assert sentence and sentence[0].isupper(), state
        assert "_" not in sentence, state  # never a raw token
    assert product_copy("no_such_state") == "Needs a check."


def test_loop_stop_and_effort_phrases_are_human() -> None:
    assert loop_stop_phrase("budget_rounds") == "the round budget was used up"
    assert "_" not in loop_stop_phrase("loop_decision_ask_human")
    assert loop_stop_phrase("unknown_reason") == "the loop stopped"
    assert effort_phrase("quick_test") == "quick test"
    assert effort_phrase("deep_exploration") == "deep exploration"


# --------------------------------------------------------------------------- #
# main-path lexicon: entry / run / memo
# --------------------------------------------------------------------------- #


def test_run_page_main_path_lexicon(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    text = _visible_text(render_run_dashboard(run_dir))
    for bad in _FORBIDDEN_VISIBLE:
        assert bad not in text, bad


def test_memo_main_path_lexicon(tmp_path: Path) -> None:
    """The memo speaks product language: no implementation words."""
    run_dir = _run(tmp_path, "memolex")
    memo = build_concise_memo(run_dir)
    for bad in ("artifact", "stub", "provider", ".yaml from", "Traceback",
                "source_backed", "quick_test", "gaps="):
        assert bad not in memo, bad


def test_memo_has_no_raw_effort_or_stop_enums(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    brief = __import__("espresso_proof.kernel.io", fromlist=["read_yaml"]).read_yaml(
        run_dir / "strategy_brief.yaml"
    )
    sid = (brief.get("top_strategies") or [{}])[0].get("id") or "none"
    effort = next(
        str(option)
        for choice in brief.get("allowed_human_choices") or []
        if choice.get("strategy_id") == sid
        for option in choice.get("effort_options") or []
    )
    memo = resume_run(run_dir=run_dir, selected_strategy=sid, effort=effort)
    validate_run(run_dir)
    assert "quick_test" not in memo
    assert "budget_rounds" not in memo
    # judgment / failure-pattern / memory sections render words, not tokens
    assert "claim_precision_risk" not in memo
    assert "request_human_review" not in memo
    assert "source_backed" not in memo
    # the POST-CONTINUATION run page (timeline includes the continued route)
    # also stays token-free on the main path (tokens only: this page echoes
    # model free text, where plain-English words like "external" are legitimate)
    text = _visible_text(render_run_dashboard(run_dir))
    for bad in _FORBIDDEN_TOKENS:
        assert bad not in text, bad
    # either the explicit stop-reason sentence or the 0-round budget sentence
    assert (
        "stopped because" in memo
        or "allowed no further rounds" in memo
        or "Prover loop" not in memo
    )
    # effort renders as plain words in the route lines
    assert "effort:" not in memo or "effort: " in memo


# --------------------------------------------------------------------------- #
# error copy: what happened / math safe / next step
# --------------------------------------------------------------------------- #


def test_friendly_error_for_missing_codex_command() -> None:
    msg = _friendly_error("ESPRESSO_EXTERNAL_AGENT_COMMAND is not set")
    assert "Codex deep run is not set up" in msg
    assert "math state is unchanged" in msg
    assert "Set up the Codex agent" in msg
    assert "ESPRESSO_EXTERNAL_AGENT_COMMAND" not in msg  # no env var as headline


def test_friendly_error_for_validation_rollback() -> None:
    msg = _friendly_error("stage 'agent:examples' failed and was rolled back: boom")
    assert "rolled back" in msg
    assert "previous valid run is preserved" in msg
    assert "Retry" in msg


def test_friendly_error_default_is_safe_and_actionable() -> None:
    msg = _friendly_error("KeyError: 'x'")
    assert "stopped safely" in msg
    assert "Existing results are untouched" in msg
    assert "Retry" in msg
    assert "KeyError" not in msg


def test_friendly_error_bare_validation_claims_no_rollback() -> None:
    """A validation failure WITHOUT an actual rollback must not claim one
    (mathematician-review blocker pin): 'rolled back' copy is reserved for the
    StageRunner's own rolled-back message."""
    msg = _friendly_error("run_manifest.yaml: schema validation failed")
    assert "rolled back" not in msg
    assert "did not pass" in msg and "stopped without going further" in msg


def test_failed_job_keeps_raw_cause_only_in_detail(tmp_path: Path, monkeypatch) -> None:
    """A failed background job: headline error is product copy; the raw cause is
    only in error_detail (advanced path)."""
    import time

    monkeypatch.delenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", raising=False)
    wb = Workbench(tmp_path / "root", provider="stub")
    out = wb.start_create_run(CLAIM, mode="codex")
    job = wait_for_job(lambda: wb.get_job(out["job_id"]), expect="failed")
    assert "Codex deep run is not set up" in job["error"]
    assert "ESPRESSO_EXTERNAL_AGENT_COMMAND" in job["error_detail"]


# --------------------------------------------------------------------------- #
# tooltips + strict guards unchanged
# --------------------------------------------------------------------------- #


def test_now_chips_have_explanatory_tooltips(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir)
    assert 'title="A deterministic machine check confirmed' in html
    assert 'title="A cited result supports one proof step' in html
    assert "A candidate supports nothing until grounding earns it." in html


def test_proof_language_guard_still_strict(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    memo = build_concise_memo(run_dir).lower()
    html = render_run_dashboard(run_dir).lower()
    for phrase in ("q" "ed", "proof complete", "the theorem is proved"):
        assert phrase not in memo
        assert phrase not in html


# ---------------------------------------------------------------------------
# A denial is not a claim (found by driving a false claim through a live run)
# ---------------------------------------------------------------------------


def test_a_conditional_or_denied_verb_survives_redaction() -> None:
    """The run recorded this honest gap:

        "If proved, a foundational source ... would need to be located and
         verified."

    and published it as "If [withheld proof-like phrase], ... located and
    [withheld proof-like phrase]" — destroying the only sentence that said what
    was missing, while withholding nothing: "if proved" and "would need to be
    verified" assert the opposite of a finished proof.
    """

    from espresso_proof.kernel.proof_language import safe_text

    for honest in (
        "If proved, a foundational source would need to be located and verified.",
        "No step is verified.",
        "It cannot be verified without a locator.",
        "whether the bound is proved remains open",
        "This has never been proven for n >= 3.",
        "The lemma is yet to be proved.",
    ):
        assert safe_text(honest) == honest


def test_an_assertion_is_still_redacted() -> None:
    from espresso_proof.kernel.proof_language import safe_text

    for assertion in (
        "We proved the theorem.",
        "Espresso has proven the claim.",
        "This step is proved and the result follows.",
        "The lemma is proven.",
        "The argument is verified and the route is closed.",
    ):
        assert "[withheld" in safe_text(assertion), assertion


def test_a_distant_denial_does_not_launder_an_assertion() -> None:
    # The exemption reaches back a few words, not across a whole sentence: a
    # denial early in a sentence must not license a claim at the end of it.
    from espresso_proof.kernel.proof_language import safe_text

    laundered = "There is no reason to doubt it, and the theorem is proved."
    assert "[withheld" in safe_text(laundered)


def test_multi_word_proof_claims_are_never_exempt() -> None:
    from espresso_proof.kernel.proof_language import safe_text

    assert "[withheld" in safe_text("If we have proved the theorem, we are done.")
    assert "[withheld" in safe_text("It is not the case that the proof is complete.")


def test_the_redactor_and_the_memory_boundary_agree(tmp_path) -> None:
    """Round 4 relaxed the redactor; round 5's live run died because the memory
    boundary had not heard about it. The honest gap "If proved, a foundational
    source ... would need to be located and verified" survived redaction, was
    read at the boundary as a proof claim, and rolled back an entire front half
    that had already been paid for. One rule, two users."""

    from espresso_proof.kernel.models.run_lifecycle import _memory_forbidden_text_reason
    from espresso_proof.kernel.proof_language import safe_text

    honest = [
        "If proved, a foundational source would need to be located and verified.",
        "No step is verified.",
        "This has never been proven for n >= 3.",
        "The bound is yet to be proved.",
    ]
    for text in honest:
        assert safe_text(text) == text
        assert _memory_forbidden_text_reason(text) is None, text

    claims = [
        "We proved the theorem.",
        "The route is verified.",
        "The counterexample refuted the claim.",
    ]
    for text in claims:
        assert "[withheld" in safe_text(text) or _memory_forbidden_text_reason(text)
        assert _memory_forbidden_text_reason(text) is not None, text
