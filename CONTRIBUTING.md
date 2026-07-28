# Contributing

Espresso Proof is a research workflow that produces auditable artifacts. The
thing that makes it worth using is that a status in an artifact means something.
Most of the rules below exist to protect that.

## Setup

```bash
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
```

`uv sync` creates `.venv` and uses the Python version the project declares. You
need `uv`, Node.js 20+, and — for real-agent runs — a Codex CLI on your `PATH`.

Check that it works without calling a model:

```bash
uv run espresso orchestrate --input problem.md --project demo --provider stub --front-half stub
```

## Tests

While developing, run only what you touched:

```bash
uv run python -m pytest tests/<subsystem> -q
```

The full suite takes roughly 14 minutes locally. You do not need to run it —
GitHub runs it automatically via `.github/workflows/full-ci.yml` when a PR
targets `main` and again on the merge commit. Do not weaken a test to make that
gate pass.

Before pushing, these are usually enough:

```bash
uv run espresso export-schemas --check
uv run python -m pytest tests/kernel -q
npm --prefix web test
```

## Pull requests

- One coherent change per PR. Keep unrelated cleanups out of it.
- Say what you verified and what you did not. "Tests pass" is not a summary.
- If you changed a schema, run `uv run espresso export-schemas --check`.
- If you changed behaviour, a test should fail without your change.
- Match the surrounding code. This repository has strong local conventions.

If your change touches the kernel — schemas, validators, status rules, seal
logic, or server endpoints — say so explicitly in the PR description. That is
the trust boundary and it gets read more carefully.

## The trust boundary

`docs/product-philosophy.md` is the authoritative statement. The short version:

**LLM output cannot upgrade a status.** Agents and external skills generate
candidate mathematics freely. They do not own trusted status transitions. Only
deterministic Python kernel code grants a status, and only against evidence it
can check itself.

Concretely, a change is not acceptable if it:

- lets an agent's own claim about its work set or raise a status;
- treats a candidate source, an unchecked example, a reviewer's approval, or a
  memory entry as trusted evidence because of how it is worded;
- relaxes a validator, a schema, or the status ladder to make a run go through;
- removes a hard-negative test — the tests that prove forged or invalid input is
  actually rejected;
- writes outside the run root, or skips rollback after a failed write.

If a feature you want conflicts with these, the boundary wins. Propose the
boundary change on its own, with its own tests, rather than folding it into a
feature PR.

Espresso Proof does not certify mathematical truth. It is not a theorem prover.
Please do not add documentation, UI text, or artifact wording that implies
otherwise.

## Reference material is not runtime

comparison. It is never executed and product code must not import from it. Do
not merge it into `espresso_proof/`. Porting a narrow piece of it is fine as its
own PR, with an owner, a contract, and tests.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
