"""The CLI command surface is intentional and minimally exercised.

Every registered command must at least render its --help (catches broken
signatures, missing imports, and registration regressions after the CLI
split), and the surface itself is pinned so a command cannot appear or
disappear silently — deleting or adding one is a deliberate, reviewed act.
"""

from __future__ import annotations

import pytest
import typer.main
from typer.testing import CliRunner

from espresso_proof.cli import app

EXPECTED_COMMANDS = {
    "add-bank-item", "agent-doctor", "audit-patterns",
    "audit-theorem-applicability", "auto-wire-verifications", "benchmark",
    "build-subgoal-graph", "calibration", "choose-strategy",
    "classify-failures", "discover-literature", "eval", "export-schemas",
    "extract-locators", "feedback-example", "generate-agent",
    "improve-workflow", "ingest-pdf", "init", "inspect", "judge",
    "literature-scout", "materialize-banks", "memo", "memory-context",
    "new-run", "orchestrate", "real-agent-smoke", "real-math-capability",
    "resolve-agent-gate", "resolve-proof-gaps", "retrieve-and-ground",
    "retrieve-autonomous", "retrieve-sources", "review-checklist",
    "review-literature-agent", "review-theorem-applicability", "run-agent",
    "run-agents", "run-dashboard", "run-external-tool", "run-flow",
    "run-llm-agents", "run-prover-loop-round", "scorecard", "seal-consensus",
    "serve", "show-pattern-bank", "summarize-feedback", "verify-computations",
    "verify-exact-modexp", "verify-finite-groups",
    "verify-gl2-f3-solvable-in-run", "verify-identity",
    "verify-identity-in-run", "verify-matrix-rank-in-run",
    "verify-ring-identity-in-run", "verify-sources",
    "verify-tensor-left-exact-counterexample-in-run", "write-proof",
    "bank-dashboard",
}


def _registered() -> set[str]:
    return set(typer.main.get_command(app).commands.keys())


def test_command_surface_is_pinned() -> None:
    assert _registered() == EXPECTED_COMMANDS


@pytest.mark.parametrize("name", sorted(EXPECTED_COMMANDS))
def test_every_command_renders_help(name: str) -> None:
    outcome = CliRunner().invoke(app, [name, "--help"])
    assert outcome.exit_code == 0, outcome.output
