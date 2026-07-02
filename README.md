# Espresso Proof

Start here for architecture: [docs/engineering/EXECUTION_TRUNK.md](docs/engineering/EXECUTION_TRUNK.md) — the end-to-end call tree from problem text to output files.

Espresso Proof is a local AI workspace for mathematical research. It helps a mathematician turn a claim or proof sketch into auditable project artifacts: claim state, source gaps, examples, counterexamples, strategy notes, reviewer objections, and a visible run trace.

The supported product path is intentionally narrow:

```text
React cockpit -> espresso serve -> external Codex agent -> artifact kernel
```

Espresso is not a theorem prover, not a Lean formalization product, and not a chatbox. It is a Codex-backed research workflow that keeps important reasoning in files instead of only in conversation.

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

## Start working

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

For a deterministic server smoke check that does not call Codex:

```bash
uv run espresso serve --provider stub
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

## Reference

- [docs/engineering/EXECUTION_TRUNK.md](docs/engineering/EXECUTION_TRUNK.md) - end-to-end execution map and trust boundary summary.
- [docs/product-philosophy.md](docs/product-philosophy.md) - product principles and trust boundary.
- [docs/engineering/product-path-boundaries.md](docs/engineering/product-path-boundaries.md) - supported product surface.
- [docs/engineering/ESPRESSO_CORE_INTEGRATION.md](docs/engineering/ESPRESSO_CORE_INTEGRATION.md) - espresso-core external-agent contract.
