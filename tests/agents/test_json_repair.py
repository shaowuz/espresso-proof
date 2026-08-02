"""A reviewer's feedback must survive the LaTeX it is written in.

Found by driving a live run (harmonic-sum claim, haiku adapter): the strategy
reviewer's whole review was discarded and replaced by the adapter's fallback —
verdict "needs_revision", zero objections — because the reply contained LaTeX
backslashes inside JSON strings:

    claude adapter could not produce usable JSON: Invalid \\escape: line 9 ...

Downstream that empty shell is indistinguishable from a reviewer who found
nothing to say, and it feeds the loop scheduler's objection counts. In a system
whose subject matter is written in LaTeX, this is the normal case.
"""

from __future__ import annotations

import json

import pytest

from espresso_proof.agents.json_repair import loads_forgiving, repair_json_text


def test_latex_backslashes_survive_instead_of_killing_the_call() -> None:
    raw = r'{"verdict": "needs_revision", "objections": ["the sum \frac{1}{k} over k \le n"]}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)  # \l is not a JSON escape: the whole call was lost here
    value, repaired = loads_forgiving(raw)
    assert repaired is True
    assert value["verdict"] == "needs_revision"
    assert value["objections"] == [r"the sum \frac{1}{k} over k \le n"]


def test_latex_that_parses_silently_wrong_is_re_read() -> None:
    r"""The quieter half: \f and \b ARE valid JSON escapes, so a reply using
    \frac or \binom parses with no error at all — into a formfeed and a
    backspace. Nobody writing a proof means a control character."""

    raw = r'{"objections": ["bound \frac{n}{2} by \binom{n}{2}"]}'
    silent = json.loads(raw)  # no error, and the mathematics is gone
    assert "\x0c" in silent["objections"][0] and "\x08" in silent["objections"][0]

    value, repaired = loads_forgiving(raw)
    assert repaired is True
    assert value["objections"] == [r"bound \frac{n}{2} by \binom{n}{2}"]


def test_a_newline_escape_still_means_a_newline() -> None:
    # \n / \r / \t are what a model means when it wraps prose; never rewritten.
    value, repaired = loads_forgiving(r'{"s": "one\ntwo\tthree"}')
    assert repaired is False
    assert value["s"] == "one\ntwo\tthree"


def test_valid_json_is_never_touched() -> None:
    raw = '{"a": "line\\nbreak", "b": "quote\\"inside", "c": "\\u00e9", "d": "slash\\/x"}'
    value, repaired = loads_forgiving(raw)
    assert repaired is False
    assert value == json.loads(raw)


def test_a_backslash_u_that_is_not_a_code_point_is_prose() -> None:
    # "\underline" is not \u + 4 hex digits, so it is text, not an escape.
    value, repaired = loads_forgiving(r'{"note": "\underline{x} and é"}')
    assert repaired is True
    assert value["note"] == r"\underline{x} and " + "é"


def test_raw_newlines_inside_a_string_are_escaped() -> None:
    raw = '{"summary": "first line\nsecond line"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    value, repaired = loads_forgiving(raw)
    assert repaired is True
    assert value["summary"] == "first line\nsecond line"


def test_structure_outside_strings_is_never_rewritten() -> None:
    # A genuinely malformed object stays malformed: repair cannot manufacture
    # a parse, so the caller still falls back and says so.
    for broken in ('{"a": 1,}', '{"a" 1}', "{not json at all"):
        with pytest.raises(json.JSONDecodeError):
            loads_forgiving(broken)


def test_the_original_error_is_what_gets_reported() -> None:
    raw = r'{"a": "\q", "b": }'  # both an invalid escape AND a missing value
    with pytest.raises(json.JSONDecodeError) as excinfo:
        loads_forgiving(raw)
    # The honest diagnostic is the model's own first failure, not the repair's.
    assert "Expecting value" in str(excinfo.value) or "escape" in str(excinfo.value)


def test_repair_is_idempotent_on_already_repaired_text() -> None:
    once = repair_json_text(r'{"x": "\frac{a}{b}"}')
    assert repair_json_text(once) == once


def test_a_repaired_call_still_counts_as_a_real_provider_call() -> None:
    """Provenance asks WHO ran, not how well they escaped their own prose.
    A repair rewrites quoting inside the model's strings; it cannot invent a
    field or change an adapter, so it must not demote the call to unverified —
    while a wholesale fallback still does."""

    from espresso_proof.agents.agent_pass import _agent_call_diagnostic

    class _Client:
        provider = "external"
        model = "claude-haiku-4-5-20251001"
        command_basename = "espresso_proof.agents.claude_adapter"

    def diagnostic(parse_status: str, fallback_used: bool):
        raw = {
            "_espresso_adapter": {
                "adapter": "espresso_claude_agent",
                "model": "claude-haiku-4-5-20251001",
                "parse_status": parse_status,
                "fallback_used": fallback_used,
                "fallback_reason": "",
                "request_id": "req-1",
            }
        }
        return _agent_call_diagnostic(
            call_index=1, role="strategy_reviewer", client=_Client(), raw=raw
        )

    assert diagnostic("ok", False)["verified_real_provider"] is True
    assert diagnostic("repaired", False)["verified_real_provider"] is True
    assert diagnostic("fallback", True)["verified_real_provider"] is False
