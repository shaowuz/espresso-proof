"""Research Bank Dashboard V0 (read-only static HTML).

Renders examples / counterexamples / strategies through the bank-aware loaders
(bank directories OR single YAML), never mutates artifacts, invents no statuses,
and emits no proof-complete language.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from espresso_proof.bank_dashboard import (
    _counterexample_card,
    _example_card,
    _strategy_card,
    render_bank_dashboard,
)
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.research_bank import materialize_run_banks
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")
_FORBIDDEN = ("proof-close", "proof complete", "we proved", "proved the claim", "proof is complete")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )


def _digest(run_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(run_dir.rglob("*.yaml")):
        out[str(p.relative_to(run_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _assert_dashboard_shape(html_text: str) -> None:
    assert "<h1>Research Bank Dashboard</h1>" in html_text
    assert "Examples (" in html_text
    assert "Counterexamples (" in html_text
    assert "Strategies (" in html_text
    assert "assessment is not a proof" in html_text


# ---------------------------------------------------------------------------
# 1-2) Renders from single YAML and from bank directories.
# ---------------------------------------------------------------------------


def test_dashboard_renders_from_single_yaml(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    html_text = render_bank_dashboard(run_dir)
    _assert_dashboard_shape(html_text)


def test_dashboard_renders_from_bank_directories(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    html_text = render_bank_dashboard(run_dir)
    _assert_dashboard_shape(html_text)


def test_single_and_bank_render_same_item_ids(tmp_path: Path) -> None:
    single = _run(tmp_path / "single")
    bank = _run(tmp_path / "bank")
    materialize_run_banks(bank)
    h_single = render_bank_dashboard(single)
    h_bank = render_bank_dashboard(bank)
    # the same run content renders the same section counts in both layouts.
    for marker in ("Examples (", "Counterexamples (", "Strategies ("):
        i_s = h_single.index(marker)
        i_b = h_bank.index(marker)
        assert h_single[i_s:i_s + 14] == h_bank[i_b:i_b + 14]


# ---------------------------------------------------------------------------
# 3) No raw proof-complete language is introduced.
# ---------------------------------------------------------------------------


def test_dashboard_has_no_proof_complete_language(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    lower = render_bank_dashboard(run_dir).lower()
    for phrase in _FORBIDDEN:
        assert phrase not in lower
    materialize_run_banks(run_dir)
    lower_bank = render_bank_dashboard(run_dir).lower()
    for phrase in _FORBIDDEN:
        assert phrase not in lower_bank


# ---------------------------------------------------------------------------
# 4) Rendering does not mutate artifacts.
# ---------------------------------------------------------------------------


def test_rendering_does_not_mutate_artifacts(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    before = _digest(run_dir)
    render_bank_dashboard(run_dir)
    assert _digest(run_dir) == before
    materialize_run_banks(run_dir)
    before_bank = _digest(run_dir)
    render_bank_dashboard(run_dir)
    assert _digest(run_dir) == before_bank


# ---------------------------------------------------------------------------
# 5) Missing optional fields render conservatively (em dash; no fabricated
#    status; no proof-complete language).
# ---------------------------------------------------------------------------


def test_missing_optional_fields_render_conservatively() -> None:
    em = "—"
    # Minimal item dicts with all optional fields absent.
    ex_html = _example_card({"id": "ex_min"})
    ce_html = _counterexample_card({"id": "ce_min"})
    st_html = _strategy_card({"id": "st_min"})
    for card in (ex_html, ce_html, st_html):
        assert em in card  # conservative em dash for the missing fields
        for phrase in _FORBIDDEN:
            assert phrase not in card.lower()
    # status/result of a missing/unknown value must NOT be a raw enum dump and
    # must not be a fabricated proof status.
    assert "Reported on examples (needs a check)" in ex_html  # unknown ExampleStatus -> conservative
    assert "ex_min" in ex_html and "ce_min" in ce_html and "st_min" in st_html
