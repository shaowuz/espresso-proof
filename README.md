# Espresso Proof

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

A local workspace that pressure-tests a mathematical idea and writes down what
it actually established.

You paste in a claim or a proof sketch. LLM agents attack it from several
directions — routes, examples, counterexamples, literature, reviewer objections
— and everything they produce lands in files you can audit, each labelled with
how much evidence is behind it.

![The Espresso Proof cockpit](docs/assets/cockpit.png)

Look at the labels in that screenshot: *Draft only — not checked*, *Needs a
source*, *No step is reference-supported yet*. That is the whole idea. An agent
can write anything; only the kernel decides what it may be **called**.

Espresso is not a theorem prover, not a Lean formalization product, and not a
chatbox. It keeps the important reasoning in files instead of in a conversation
you will never re-read.

The supported product path is intentionally narrow:

```text
React cockpit -> espresso serve -> external Codex agent -> artifact kernel
```

## What it does not guarantee

Espresso Proof does not certify mathematical truth. Nothing it produces is a
proof in the sense a referee would accept.

The value is in the boundary: LLM agents propose freely, and a deterministic
Python kernel decides what any of it is allowed to be *called*. An agent cannot
mark its own work `proved`, cannot promote a source it merely cited into a
checked one, and cannot upgrade a status by asserting it. Statuses are granted
by kernel code against evidence the kernel can check itself, and anything
uncertain fails closed.

So a run tells you what is sourced, what is checked, what is inferred, and what
is still speculation — and whether the idea is worth continuing. It does not
tell you the claim is true. See [docs/product-philosophy.md](docs/product-philosophy.md).

## Requirements

- `uv` for Python dependencies and the project virtual environment
- Node.js 20+ and npm for the React cockpit
- Codex CLI as `codex` for external-agent runs

`uv sync` creates the local `.venv` automatically and uses the Python version declared by the project.

## Install

```bash
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
```

## Try it without a model

This runs end to end with deterministic stubs — no Codex CLI, no API key, no
network. Use it to check the install:

```bash
uv run espresso orchestrate --input problem.md --project demo --provider stub --front-half stub
```

## Start working

Real-agent mode needs the Codex CLI installed as `codex` and reachable on your
`PATH`. The server supports only `--provider external`: it does not call a model
API itself, it shells out to the agent command you configure.

Start the product server with the Codex-backed adapter:

```bash
export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"
uv run espresso serve --runs-root . --host 127.0.0.1 --port 8765 --provider external
```

Open `http://127.0.0.1:8765`, paste a mathematical claim or proof sketch, and let Espresso create a run. Runs and memory are local artifacts under the runs root; they are not committed project files. For example, paste one of these into the cockpit to start a run.

```text
Claim sketch:
For all smooth cubic fourfolds, the Hodge atom obstruction coming from the
Hodge-theoretic mirror symmetry package proves irrationality.

Proof sketch:
The proof treats the very general case as if it applied to every smooth cubic
fourfold. I have not pinned an exact theorem number, and I have not checked a
special cubic fourfold example with extra algebraic classes.

What I want checked:
Do not mark this as proved. Look for the all-versus-very-general jump, missing
source grounding, and special-example tests that could break the argument.
```


## Run tests

Fast-iteration policy: while developing, run only the scoped checks you need
(e.g. `pytest tests/<subsystem> -q`). The full suite is required only when a
PR merges into main — GitHub runs it automatically via
`.github/workflows/full-ci.yml`.

Run these directly; there is no wrapper script.

For a deterministic end-to-end smoke check that does not call a real model
(the server itself supports only `--provider external`):

```bash
uv run espresso orchestrate --input problem.md --project demo --provider stub --front-half stub
```

```bash
uv run espresso export-schemas
uv run espresso export-schemas --check
uv run espresso eval --schemas-only
uv run python -m pytest -q
npm --prefix web test
npm --prefix web run build
```

`schemas/` is a local generated directory and is intentionally ignored by git.

## Repository map

| Path | What it is |
| --- | --- |
| `espresso_proof/` | The product runtime |
| `espresso_proof/kernel/` | Trust boundary: schemas, validators, status rules |
| `espresso_proof/agents/` | LLM proposal and review paths, external adapters |
| `espresso_proof/orchestration/` | Run control flow |
| `espresso_proof/server.py` | Local HTTP server behind the cockpit |
| `web/` | React cockpit (local UI) |
| `tests/` | Contract and regression tests, including hard negatives |
| `docs/` | Engineering and product documentation |
| `docs/reference/` | Reference material — never executed, never imported |

## The CLI, and which parts of it you need

`espresso --help` lists 61 commands. Almost all of them are maintainer tooling.
You need two:

| Command | What it is for |
| --- | --- |
| `espresso serve` | Start the local server and use the cockpit. This is the product. |
| `espresso orchestrate` | Run the same workflow headless, without the UI. |

Everything else falls into one of these groups, and you can ignore all of it
until you have a reason not to:

| Group | Examples | What it is |
| --- | --- | --- |
| Human gates | `choose-strategy`, `resolve-agent-gate` | Answering a run that stopped for a decision |
| Proof pass | `write-proof`, `seal-consensus` | The line-by-line proof and its review |
| Agent plumbing | `run-agent`, `run-agents`, `agent-doctor` | Driving or diagnosing external agents |
| Dev | `export-schemas`, `new-run`, `inspect`, `memo` | Working on Espresso itself |
| Eval | `eval`, `benchmark`, `calibration`, `scorecard` | Measuring behaviour; used by the merge gate |
| Verify | `verify-*` | Scoped machine checks against a run |
| Legacy | `run-flow` | Kept for old runs; not the current path |

## Branches and reference material

| Location | Role | Stability |
| --- | --- | --- |
| `main` | Current product runtime | Supported public target |
| `v2` | Future architecture experiment | Unstable, not release-ready |


## Reference

- [docs/engineering/EXECUTION_TRUNK.md](docs/engineering/EXECUTION_TRUNK.md) - start here for architecture: the end-to-end call tree from problem text to output files.
- [docs/product-philosophy.md](docs/product-philosophy.md) - product principles and trust boundary.
- [docs/engineering/product-path-boundaries.md](docs/engineering/product-path-boundaries.md) - supported product surface.
- [docs/engineering/ESPRESSO_CORE_INTEGRATION.md](docs/engineering/ESPRESSO_CORE_INTEGRATION.md) - espresso-core external-agent contract.
- [CONTRIBUTING.md](CONTRIBUTING.md) - setup, tests, PR expectations, and the boundaries a change may not weaken.
- [AGENTS.md](AGENTS.md) - rules for AI agents working in this repository.
- [SECURITY.md](SECURITY.md) - how to report a vulnerability, and what this project does not claim to protect.

## License

MIT — see [LICENSE](LICENSE).
