# Espresso Proof

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

**A local AI workspace for mathematical research. Agents generate the mathematics; a deterministic kernel owns the truth about its status.**

Espresso turns a claim or proof sketch into a run: a directory of schema-validated
YAML artifacts — claim state, examples, counterexamples, strategy notes, reviewer
objections, line-numbered proof attempts, and a full run trace. Agents propose
everything; only the kernel's validators grant status; every shortcut fails closed.

A run proceeds: claim clearing (default-deny; an unclear claim parks with a human
question) → candidate literature → scoped machine checks → strategy fan-out →
**human gate** → bounded prover/reviewer loop → research memo. Details:
[docs/engineering/execution-trunk.md](docs/engineering/execution-trunk.md).

## Status ladder

Wording earns nothing; every status names who can grant it.

| Status | Granted by |
|---|---|
| `idea_sketch`, `proof_candidate` | any agent |
| `agent_consensus_mock_checked` | the consensus seal — amber, displayed as unverified |
| `source_checked_argument` | exact source grounding of the load-bearing steps |
| `instance_checked`, `computation_checked` | bounded machine verifiers that ran |
| `lean_checked` | nothing today — reserved for a formal backend |
| `human_confirmed` | a recorded human decision, never automation |

A route is not a proof. `espresso write-proof` produces a line-numbered proof
(each line: statement, justification, typed dependency edges; status pinned to
`advisory`). `espresso seal-consensus` runs three adversarial reviewers with
disjoint jobs; the seal requires a unanimous no-fatal-flaw verdict with cited
line ids and is hash-bound to claim, proof, and sources — editing one line
revokes it.

## Quickstart

Requirements: [`uv`](https://docs.astral.sh/uv/), Node 20+, and an agent CLI
(`codex` or `claude`) for real runs.

```bash
uv sync --extra dev
npm --prefix web ci && npm --prefix web run build   # serve refuses to start without the build

export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"   # or espresso-claude-agent
uv run espresso serve --runs-root . --host 127.0.0.1 --port 8765 --provider external
```

Open `http://127.0.0.1:8765` and paste a claim or proof sketch.

Offline, no model — the deterministic stub runs the same pipeline end to end:

```bash
./examples/quickstart.sh
```

Any agent that speaks the adapter contract can drive Espresso: one JSON request
on stdin, one JSON object on stdout, non-zero exit instead of malformed output.
Provenance is recorded from the agent's own envelope; every call lands in an
append-only `token_usage.jsonl`.

## Repository map

| Path | Role |
|---|---|
| `espresso_proof/kernel/` | schemas, validators, status rules — the trust boundary |
| `espresso_proof/orchestration/` | run control flow |
| `espresso_proof/agents/` | LLM proposal/review paths and the shipped adapters |
| `espresso_proof/verification/` | bounded deterministic verifiers (`verify-*` CLI family) |
| `web/` | local cockpit UI (React 19 + TypeScript + Vite), served by `espresso serve` |
| `tests/` | contract and regression tests, incl. 13 deterministic honesty fixtures |
| `examples/quickstart.sh` | offline stub demo — the only checked-in example |
| `docs/reference/` | frozen reference material, not runtime |

`main` is the product line. `v2` is an experimental future-architecture branch
and is not release-ready. Runtime dependencies: `pydantic`, `PyYAML`, `typer`.

## Checks

```bash
uv run espresso export-schemas --check
uv run espresso eval --schemas-only
uv run python -m pytest -q -n auto --dist loadfile
npm --prefix web test
```

The merge gate is defined in
[`.github/workflows/full-ci.yml`](.github/workflows/full-ci.yml); the workflows
are currently disabled in Actions settings, so run the suite locally.

## Scope

- A local, single-mathematician research tool.
- The strongest purely agentic status is the amber consensus seal; the green
  statuses (`lean_checked`, `human_confirmed`) require a formal backend or a human.
- Not a theorem prover; it does not certify mathematical truth.

[docs/product-philosophy.md](docs/product-philosophy.md) —
[CONTRIBUTING.md](CONTRIBUTING.md) — [SECURITY.md](SECURITY.md)

## Team

Espresso Proof is developed by [@shaowuz](https://github.com/shaowuz),
@yangjunjie0320, and @w-ruizhe ([@w-ruizhe](https://github.com/w-ruizhe)).
The literature subsystem was started with @w-ruizhe. @yangjunjie0320 contributed
to the product philosophy and the Claude-centric comparison route.

**License:** MIT — see [LICENSE](LICENSE).
