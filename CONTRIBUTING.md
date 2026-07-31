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
need `uv` and Node.js 20+. Real-agent runs additionally need one agent backend
on your `PATH` — the repository ships adapters for the Codex CLI, the Claude
CLI, OpenAI, and espresso-core — but nothing below requires one.

Check that it works without calling a model:

```bash
printf 'Claim: every group of prime order is cyclic.\n' > problem.md
uv run espresso orchestrate --input problem.md --project demo --provider stub --front-half stub
```

The claim text matters here: the run stops at a clarification gate if it cannot
lock a precise claim, which is correct behaviour but looks like a failure the
first time. `examples/quickstart.sh` does the same thing with a claim known to
get past that gate.

## Tests

While developing, run only what you touched:

```bash
uv run python -m pytest tests/<subsystem> -q
```

> **The CI workflows are currently disabled.** They are still in
> `.github/workflows/`, unchanged, but switched off in the repository's Actions
> settings while this is a single-maintainer project. **Nothing runs on a pull
> request and nothing runs on merge — run the full suite yourself before
> merging.** They are re-enabled before the repository goes public; see

When they are on: the full suite runs when a PR targets `main` and again on the
merge commit, and a documentation-only change skips it and gets a fast hygiene
check instead. `.github/workflows/full-ci.yml` is the authority on when the gate
fires; if this paragraph and that file disagree, the file is right.

Run the full suite in parallel:

```bash
uv run python -m pytest -q -n auto --dist loadfile
```

That is about 6 minutes instead of about 17. `--dist loadfile` is not optional:
it keeps every test in a file on one worker, and several suites here share a run
directory or a writer lock within a file.

Do not add those flags to a scoped run. xdist's worker startup costs more than a
small suite takes — one 6-test file goes from 0.36s to 9.1s. That is why they
are not in `addopts`.

Do not weaken a test to make the gate pass.

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

The most natural contribution — a new deterministic verifier so runs in your
area of mathematics can earn checked evidence — has a full walkthrough:
[docs/engineering/adding-a-deterministic-verifier.md](docs/engineering/adding-a-deterministic-verifier.md).

Espresso Proof does not certify mathematical truth. It is not a theorem prover.
Please do not add documentation, UI text, or artifact wording that implies
otherwise.

## Reference material is not runtime

comparison. It is never executed and product code must not import from it. Do
not merge it into `espresso_proof/`. Porting a narrow piece of it is fine as its
own PR, with an owner, a contract, and tests.

## Conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies to everything here.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
