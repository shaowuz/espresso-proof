#!/usr/bin/env bash
# Quickstart: one problem in, one validated run directory out (offline stub provider).
# Full path explanation: docs/engineering/execution-trunk.md
set -euo pipefail
cd "$(dirname "$0")/.."

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/espresso-quickstart.XXXXXX")
echo "Claim: every group of prime order is cyclic." > "$WORKDIR/problem.md"
uv run espresso orchestrate --input "$WORKDIR/problem.md" --project demo --provider stub

RUN_DIR=$(ls -dt projects/demo/runs/* | head -1)
echo
echo "Outputs: $RUN_DIR (dashboard: $RUN_DIR/research_dashboard.html)"
echo "Pick a strategy id from $RUN_DIR/strategy_brief.yaml (top_strategies[].id), then resume:"
echo "  uv run espresso orchestrate --resume $RUN_DIR --select-strategy <id> --effort <mode>"
