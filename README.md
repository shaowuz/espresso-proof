# Espresso Proof

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

**A local AI workspace for mathematical research. Agents generate the mathematics; a deterministic kernel owns the truth about its status.**

Espresso Proof helps a mathematician turn a claim or proof sketch into auditable project artifacts: claim state, source gaps, examples, counterexamples, strategy notes, reviewer objections, line-numbered proof attempts, and a visible run trace. It is built around one practical question:

> *Is this mathematical idea worth continuing, and what is the cheapest next action?*

The supported product path is intentionally narrow:

```text
React cockpit -> espresso serve -> external agent (Codex or Claude CLI) -> artifact kernel
```

Espresso is an agent-backed research workflow that keeps important reasoning in files instead of only in conversation.

## Why Espresso

Large language models are strong mathematical *generators* and unreliable *graders* of their own output. Most AI-for-math tools resolve that tension in one of two ways: trust the model's self-assessment, or demand full formalization before anything counts. Espresso takes a third position:

**Let agents produce every candidate — strategies, examples, counterexamples, literature matches, line-numbered proofs, reviews. Let a small deterministic Python kernel be the only component that can grant status.**

Concretely:

- Every run is a directory of ~30 schema-validated YAML artifacts (69 registered artifact models). Nothing important lives only in a chat transcript.
- Every mutation happens inside a transaction: snapshot → mutate → `validate_run` → advance, with rollback on failure. A run on disk is always in a valid state.
- Agent output is scrubbed on entry: proof-claiming language is quarantined (English *and* Chinese completion phrases), and any status field an agent tries to attach is stripped. Log records that carry status-bearing keys are rejected outright — logs cannot impersonate evidence.
- The phrases `fail closed` and `default-deny` appear **more than 200 times** in the source. That is the design temperament.

The payoff: you can let agents run hard — fan out strategies, attack the claim with counterexamples, iterate prover/reviewer rounds overnight — and everything the run asserts afterwards is exactly as strong as the evidence trail behind it, no stronger.

## The status ladder

Proof-language status is a strictly ordered vocabulary defined in the kernel. Every value states who can earn it; wording alone earns nothing.

| Status | Who can grant it |
|---|---|
| `idea_sketch`, `proof_candidate` | any agent — proposing is free |
| `agent_consensus_mock_checked` | only the consensus seal: three adversarial reviewers, unanimous, line-citations required, hash-bound. **Amber by definition — explicitly not verification** |
| `source_checked_argument` | exact source grounding of the load-bearing steps (default-deny theorem-applicability audit) |
| `instance_checked`, `computation_checked` | only bounded machine verifiers that actually executed the check |
| `lean_checked` | no code path can grant it today — reserved until a real formal backend exists |
| `human_confirmed` | only an actual human decision, recorded as such. Automation can never write it |

The same discipline runs through every enum: retrieved sources have no `source_backed` state (only `candidate` / `unverified` / `not_found`), the proof-verification enum deliberately has no `proved`, and subgoal states include `blocked_missing_source` but nothing that spells success by assertion.

## How a run works

```text
problem.md, or a claim pasted into the cockpit
        |
  front half - claim clearing (default-deny: every unresolved entry blocks),
        |       hypothesis ledger; an unclear claim parks the run with a human
        |       question and spends nothing on provers
  literature - candidate sources, never auto-trusted
        |
  scoped machine checks - the only way to earn instance_checked / computation_checked
        |
  strategy fan-out + grading  ->  HUMAN GATE (the designed stop)
        |
  human picks strategy + effort   (or --auto-continue: machinery picks the
        |                          top-scored strategy and the artifacts say so —
        |                          selected_by='auto', never 'human')
  bounded prover <-> reviewer loop  ->  research memo (COMPLETED_MEMO)
```

The memo answers the core question: the clarified claim, the strongest routes, what is sourced / checked / inferred / speculative, the main obstruction, and the next decision for the mathematician. Failed attempts are preserved as advisory memory instead of vanishing.

On-demand **evidence agents** (examples, counterexamples, literature, failure patterns) can be fanned out in parallel at any time — computation runs concurrently, commits are serialized in deterministic order, and results converge byte-identical with the serial path. One lane failing is captured, never silently dropped.

### The proof branch: line-numbered proofs and the consensus seal

A route is not a proof, and Espresso enforces the difference structurally.

`espresso write-proof` produces `line_numbered_proof.yaml`: every line carries a `statement`, a `justification`, and typed dependency edges (`prior_line`, `theorem_ref`, `human_assumption`, `unchecked_gap`, …). A line without a justification is rejected as a route step. The artifact's status field is pinned to `advisory` — the schema forbids attaching anything stronger.

`espresso seal-consensus` then runs **three adversarial reviewers with disjoint jobs** — a line-by-line proof reviewer, a theorem/lemma-applicability reviewer, and a dedicated gap-hunter — over that proof object:

- The seal is granted only on a unanimous *no fatal flaw found* verdict.
- A passing reviewer **must cite the specific line ids** it checked; an uncited pass is a validation error.
- The seal is **hash-bound** to the claim, the proof, and its source grounding. Editing a single line after review revokes it — the run fails validation until re-sealed.
- A run with only a route and no line-numbered proof **fails closed**. So does an `unclear` verdict.
- The offline stub client *refuses* the proof-writer and consensus-reviewer roles entirely: a test double can never earn the amber status.

What this buys, stated exactly: `agent_consensus_mock_checked` means *three independent agent reviewers each failed to find a fatal flaw in a fully written-out proof and signed specific lines*. That is a meaningfully higher bar than "the model says proof-close" — and the kernel still displays it as unverified, because it is.

## The cockpit

`espresso serve` hosts a local React workbench (React 18 + TypeScript + Vite, KaTeX math rendering): a claim card, strategy/route table, a prover-round ledger (selected strategy, effort, stop reason, open reviewer blockers), an evidence rail for examples / counterexamples / computations, a literature rail, streaming draft and chat panels (proof-language-scrubbed server-side), and a collapsible live trace of every agent call. The server is stdlib-only Python, loopback-bound, CSRF-guarded, and path-confines every run id — it never executes browser input.

## Quickstart

### Requirements

- `uv` for Python dependencies and the project virtual environment
- Node.js 20+ and npm for the React cockpit
- An agent CLI for external-agent runs: Codex CLI as `codex`, or Claude Code as `claude`

`uv sync` creates the local `.venv` automatically and uses the Python version declared by the project.

### Install

```bash
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
```

### Start working

Start the product server with the adapter for whichever agent CLI you use:

```bash
export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"    # Codex CLI
# or: export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-claude-agent"  # Claude Code CLI
uv run espresso serve --runs-root . --host 127.0.0.1 --port 8765 --provider external
```

Open `http://127.0.0.1:8765`, paste a mathematical claim or proof sketch, and let Espresso create a run. Runs and memory are local artifacts under the runs root; they are not committed project files. For example, paste this into the cockpit to start a run:

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

### Headless, without any model

The full pipeline also runs from the CLI, and a deterministic offline stub makes the trunk testable end-to-end (`--provider` is required everywhere — there is no silent test mode):

```bash
echo "Claim: every group of prime order is cyclic." > problem.md
uv run espresso orchestrate --input problem.md --project demo --provider stub --front-half stub
# ...stops at the human gate. Pick a strategy from strategy_brief.yaml:
uv run espresso orchestrate --resume projects/demo/runs/<run_id> --provider stub \
    --select-strategy <strategy_id> --effort normal_pass
```

## Bring your own agent

Any agent that speaks the adapter contract can drive Espresso: one JSON request on stdin (`system`, `prompt`, `required_json_schema`, timeout), one JSON object on stdout, non-zero exit instead of malformed output for trust-relevant roles. Three adapters ship in-tree (a direct `openai` API provider is also built in):

| Adapter | Backs onto | Notes |
|---|---|---|
| `espresso-codex-agent` | Codex CLI | reports per-call usage from the exec event stream |
| `espresso-claude-agent` | Claude Code CLI | self-reports model identity, usage, and cost per call |
| `espresso-core-agent` | espresso-core (experimental) | routes prover-family roles to a dedicated prover backend; consensus reviewers go elsewhere, so the seal never reviews its own author |

Provenance is honest by construction: executor identity is recorded from the agent's own envelope, never from a CLI flag; unknown identity stays `null` rather than defaulting to a plausible model name; and every model call — including failures and timeouts — lands in an append-only `token_usage.jsonl` cost sidecar that survives rollbacks and that validation never reads.

## By the numbers

| | |
|---|---|
| Kernel + orchestration | 174 Python modules, ~58k lines, across 13 subpackages |
| Runtime dependencies | **3** (`pydantic`, `PyYAML`, `typer`) — the trust surface stays auditable |
| Artifact schemas | 69 registered models, exported and drift-checked in CI |
| CLI surface | 60+ subcommands, incl. the `verify-*` family of bounded machine verifiers |
| Agent roles | 17 prompt-defined roles (prover, strategy attacker, adversarial gap reviewer, …) |
| Tests | 1,876 Python test functions in 142 files, plus 80+ web tests |
| Honesty fixtures | 13 deterministic evals such as `fake_source_backed_rejected` and `human_feedback_does_not_upgrade_source_status` |

Fast-iteration policy: while developing, run only the scoped checks you need (e.g. `pytest tests/<subsystem> -q`). The full suite — pytest, mock evals, benchmark, calibration, scorecard, web build — is required only when a PR merges into main; GitHub runs it automatically via [`.github/workflows/full-ci.yml`](.github/workflows/full-ci.yml).

```bash
uv run espresso export-schemas --check
uv run espresso eval --schemas-only
uv run python -m pytest -q
npm --prefix web test
```

`schemas/` is a local generated directory and is intentionally ignored by git.

## Scope

- The strongest status a purely agentic path can reach is the amber consensus seal. The green statuses (`lean_checked`, `human_confirmed`) require a formal backend or a human.
- Espresso makes uncorrected overclaiming structurally hard: every upgrade path runs through the kernel's validators, and every shortcut fails closed.
- It is a local, single-mathematician research tool.

The bet is that this is the useful half of the problem: a mathematician with strong agent horsepower does not need a system that agrees with the agents. They need one that keeps score honestly.

## Reference

- [docs/engineering/execution-trunk.md](docs/engineering/execution-trunk.md) — the end-to-end call tree from problem text to output files; start here for architecture.
- [docs/product-philosophy.md](docs/product-philosophy.md) — product principles and the trust boundary.
- [docs/engineering/product-path-boundaries.md](docs/engineering/product-path-boundaries.md) — supported product surface.
- [docs/engineering/espresso-core-integration.md](docs/engineering/espresso-core-integration.md) — espresso-core external-agent contract.
- [docs/engineering/adding-a-deterministic-verifier.md](docs/engineering/adding-a-deterministic-verifier.md) — how to contribute a new machine check.
- [CONTRIBUTING.md](CONTRIBUTING.md) — setup, tests, and trust-boundary rules for contributors. [SECURITY.md](SECURITY.md) — vulnerability reporting.

## Team

Espresso Proof is developed by [@shaowuz](https://github.com/shaowuz), @yangjunjie0320, and @w-ruizhe ([@w-ruizhe](https://github.com/w-ruizhe)). The literature subsystem — discovery, review agents, live search, PDF ingestion — was started with @w-ruizhe. @yangjunjie0320 contributed to the product philosophy and the Claude-centric comparison route.

**License:** MIT — see [LICENSE](LICENSE).
