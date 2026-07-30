# Espresso Proof

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

**A controlled research runtime for long-horizon mathematical reasoning.**

Espresso Proof is built for difficult mathematical problems that require
competing strategies, repeated proof attempts, literature investigation,
examples, counterexamples, adversarial review, targeted repair, and sustained
human judgment.

Complex research rarely fits into one prompt. Over hours or days, the working
claim may shift, hypotheses may disappear from context, a citation may become
stronger in memory than it was in the source, a theorem may be applied outside
its actual scope, and a reviewer objection may be lost between rounds.

Espresso solves this control problem by moving research state out of transient
conversation and into typed, persistent artifacts. Every round begins from the
recorded claim, hypotheses, sources, proof steps, objections, decisions, and
provenance.

![The Espresso Proof cockpit](docs/assets/cockpit.png)

## Long-horizon research control

Espresso gives common forms of research drift an explicit system response.

| Research risk | Espresso response |
| --- | --- |
| The claim changes between rounds | A locked claim and hypothesis ledger define the current mathematical target |
| Definitions or assumptions become implicit | Reviewers record definition gaps, hypothesis gaps, and unresolved assumptions as typed blockers |
| A promising citation is treated as established support | Literature discovery produces candidates; source grounding connects exact sources to exact proof steps |
| A theorem is applied under the wrong hypotheses | Theorem-applicability review compares conditions, conclusion strength, and scope |
| A universal claim is inferred from generic cases | Scope checks distinguish universal, generic, very general, and restricted statements |
| Reviewer criticism disappears from context | Objections persist with severity, scope, target line or subgoal, and proposed next action |
| A proof changes after review | Hash-bound review records require the current proof and evidence artifacts to match |
| Human and automatic decisions become indistinguishable | Strategy and gate decisions record explicit `human` or `auto` provenance |
| A stage writes inconsistent artifacts | Transactional validation restores the last valid run state |

The result is a research record that remains inspectable and resumable across
many agent calls and many research sessions.

## From a claim to an auditable research run

Start with a mathematical claim or proof sketch. Espresso turns it into a
structured research program:

```text
lock the claim and hypotheses
        ↓
generate and grade competing strategies
        ↓
search, read, and ground relevant literature
        ↓
pressure-test the claim with examples and counterexamples
        ↓
construct candidate proof routes
        ↓
run independent strategy, evidence, and line-proof reviewers
        ↓
convert objections into scoped repair targets
        ↓
ask the mathematician where research judgment is required
        ↓
publish an auditable research memo and complete run trace
```

Each stage reads explicit input artifacts and writes schema-validated output
artifacts. Failed routes, unresolved blockers, source gaps, and human decisions
remain available when the investigation resumes.

## Specialized agents with separate responsibilities

Espresso separates mathematical generation from mathematical criticism.

| Component | Responsibility |
| --- | --- |
| Strategy pipeline | Generates competing routes, records their main risks and effort requirements, and identifies the cheapest informative next test |
| Literature agents | Discover candidate sources, inspect available source text, and extract theorem statements, definitions, hypotheses, and locators |
| Example agents | Construct informative special cases, extremal cases, and boundary cases |
| Counterexample agents | Test universal claims, dropped hypotheses, analogies, and vulnerable proof steps |
| Prover agent | Builds candidate arguments from the locked claim and current evidence |
| Strategy reviewer | Tests reductions, analogy risks, theorem drift, and route quality |
| Evidence reviewer | Audits how sources, examples, counterexamples, and computations support the argument |
| Line-proof reviewer | Locates logical gaps, missing definitions, unsupported transitions, and theorem-applicability risks |
| Repair loop | Converts typed reviewer objections into bounded, targeted follow-up rounds |

Reviewer feedback is stored as structured blockers with:

- a target proof step, subgoal, route, or global issue;
- a severity level;
- a failure category;
- the reviewer’s concrete objection;
- a proposed next action.

The next prover round therefore receives a precise repair packet rather than an
unstructured request to try again.

## Literature grounding and theorem applicability

Espresso separates finding a source from establishing how that source supports
an argument.

A literature agent may identify a promising paper, book, or theorem. Source
grounding then connects a specific proof step to:

- an exact source;
- an exact theorem or definition locator;
- the statement used by the argument;
- the source attribution;
- the required hypotheses;
- the available access record;
- the proof step that consumes the result.

The theorem-applicability layer compares the source theorem with the current
proof obligation. It records whether each condition is:

- satisfied;
- missing;
- mismatched;
- unclear;
- awaiting human judgment.

It also compares the theorem’s conclusion and scope with the claim, including
distinctions such as:

- universal versus restricted;
- generic versus every instance;
- very general versus arbitrary;
- stronger versus weaker conclusions;
- compatible versus orthogonal statements.

This creates a traceable evidence chain:

```text
candidate source
    → exact theorem extraction
    → source grounding
    → hypothesis and scope comparison
    → applicability review
    → kernel-validated evidence label
```

A discovered reference therefore becomes useful research evidence through an
explicit sequence of inspectable artifacts.

## What the artifact kernel guarantees

The artifact kernel gives every status label a precise operational meaning. A
label records which checks ran, which evidence they used, and which version of
the research artifacts they reviewed.

The kernel enforces:

- strict schemas for agent inputs and outputs;
- valid references between claims, proof steps, sources, reviews, and evidence;
- exact locator, attribution, and hypothesis requirements for source grounding;
- condition, conclusion, and scope checks for theorem applicability;
- scoped `instance_checked` and `computation_checked` results from machine
  checks;
- three distinct line-citing reviewers for an
  `agent_consensus_mock_checked` proof seal;
- hash binding between a review and the exact proof, claim, sources, and loop
  state it examined;
- automatic expiration of stale seals after relevant artifacts change;
- explicit `human` versus `auto` provenance for strategy and gate decisions;
- transactional stage writes with validation and rollback to the last valid
  run;
- path confinement and display-safe rendering of agent-generated material.

Agents generate mathematical content. The kernel determines which evidence
conditions have been satisfied and records the resulting status.

## Human–AI research control

Espresso supports several levels of human involvement within the same research
run.

| Interaction mode | How it works |
| --- | --- |
| Autonomous scouting | Agents explore strategies, literature, examples, counterexamples, and reviewer pressure before presenting a decision |
| Strategy portfolio | Espresso presents multiple routes with risks, effort estimates, source gaps, and proposed next actions |
| Human route selection | The mathematician chooses from kernel-valid strategies and selects the desired effort level |
| Advisory steering | The mathematician marks routes as `focus`, `repair`, `normal`, or `reduce`; the next round adjusts ordering, compute, and prompt context |
| Asynchronous checkpoint | The run persists its complete state at claim-clarification and strategy gates and resumes from disk later |
| Auditable auto-continue | Espresso selects the highest-ranked allowed strategy and records the decision with `selected_by: auto` |
| Targeted repair | Repairable proof-step and computation blockers receive bounded repair rounds |
| Human research judgment | Ambiguous assumptions, definitions, source choices, and strategic decisions return to an explicit human gate |

Available effort levels include:

- `quick_test`;
- `normal_pass`;
- `deep_exploration`;
- `long_watch`.

Human steering controls where the system spends attention. Kernel evidence rules
continue to determine the meaning of every recorded status.

## Example

`examples/quickstart.sh` runs one claim end to end with the deterministic stub
provider — no model, no API key, no network — and prints the run directory it
produced:

```bash
bash examples/quickstart.sh
```

It ends at a human gate, which is the normal outcome: the run records what it
found and asks which route to spend effort on. `docs/engineering/execution-trunk.md`
walks the same path stage by stage.

## Product path

The supported runtime path is intentionally direct:

```text
React cockpit
    → espresso serve
    → orchestration and human gates
    → configured external agent adapter
    → artifact kernel
    → local auditable run directory
```

The same run can be:

- inspected through the cockpit;
- executed headlessly from the CLI;
- paused at a research decision;
- resumed with its claim, evidence, reviewer objections, and provenance intact;
- continued automatically with explicit automatic-decision provenance.

## Requirements

- Python 3.11 or newer
- `uv` for Python dependencies and the project virtual environment
- Node.js 20 or newer and npm for the React cockpit
- one configured agent backend for real-agent runs

The repository includes adapters for:

- Codex CLI;
- Claude CLI;
- OpenAI;
- espresso-core.

`uv sync` creates the local virtual environment and uses the Python version
declared by the project.

## Install

```bash
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
```

## Deterministic smoke test

The deterministic stub provider runs the complete orchestration path locally
with reproducible agent outputs.

Create a `problem.md` containing a mathematical claim or proof sketch, then run:

```bash
uv run espresso orchestrate \
  --input problem.md \
  --project demo \
  --provider stub \
  --front-half stub
```

The command creates a local run directory containing the claim, strategies,
literature records, examples, counterexamples, reviews, state transitions, and
research memo.

To exercise automatic strategy selection and continuation:

```bash
uv run espresso orchestrate \
  --input problem.md \
  --project demo \
  --provider stub \
  --front-half stub \
  --auto-continue
```

## Start the research cockpit

Configure an external agent command. For the shipped Codex adapter:

```bash
export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"
```

Start the local product server:

```bash
uv run espresso serve \
  --runs-root . \
  --host 127.0.0.1 \
  --port 8765 \
  --provider external
```

Open:

```text
http://127.0.0.1:8765
```

Paste a mathematical claim or proof sketch into the cockpit. Espresso creates a
run, executes the research stages, displays the current artifacts, and presents
human decisions when the workflow reaches a research gate.

Runs and project memory are stored as local artifacts under the configured runs
root.

## Run headlessly

The same workflow can run directly from the CLI:

```bash
uv run espresso orchestrate \
  --input problem.md \
  --project demo \
  --provider external
```

Resume a run after selecting a strategy:

```bash
uv run espresso orchestrate \
  --resume projects/demo/runs/<run_id> \
  --select-strategy <strategy_id> \
  --effort normal_pass
```

Run through the strategy gate with automatic selection:

```bash
uv run espresso orchestrate \
  --input problem.md \
  --project demo \
  --provider external \
  --auto-continue
```

Automatic selection chooses from the same allowed strategy set and records:

```yaml
selected_by: auto
human_confirmed: false
```

## Run tests

Use scoped tests while developing and the complete gate before merging changes
into `main`.

Validate exported schemas:

```bash
uv run espresso export-schemas
uv run espresso export-schemas --check
uv run python -m pytest tests/kernel -q   # schema + kernel contract checks
```

Run the Python suite:

```bash
uv run python -m pytest -q
```

Run the frontend suite:

```bash
npm --prefix web test
npm --prefix web run build
```

The generated `schemas/` directory is a local validation artifact.

## Repository map

| Path | Responsibility |
| --- | --- |
| `espresso_proof/` | Product runtime |
| `espresso_proof/kernel/` | Schemas, validators, status rules, transactions, and trust-bearing artifacts |
| `espresso_proof/agents/` | Prover, reviewer, prompt, adapter, and agent-output processing |
| `espresso_proof/orchestration/` | Canonical run control flow, resume logic, human gates, and prover loops |
| `espresso_proof/literature/` | Literature discovery, retrieval, ingestion, extraction, and review |
| `espresso_proof/verification/` | Scoped computations, source checks, and theorem-applicability services |
| `espresso_proof/strategy/` | Strategy generation, grading, selection, feedback, and time allocation |
| `espresso_proof/server.py` | Local HTTP server behind the cockpit |
| `web/` | React research cockpit |
| `tests/` | Contract, regression, integration, and hard-negative tests |
| `docs/engineering/` | Maintainer-facing execution and integration documentation |
| `docs/reference/` | Frozen comparison and reference material outside the product runtime |

## CLI surface

Most users begin with these commands:

| Command | Purpose |
| --- | --- |
| `espresso serve` | Start the local research cockpit |
| `espresso orchestrate` | Run the canonical workflow headlessly |
| `espresso choose-strategy` | Record a human strategy decision |
| `espresso resolve-agent-gate` | Resolve a prover/reviewer research gate |

Additional command groups support deeper workflows:

| Group | Commands | Purpose |
| --- | --- | --- |
| Agent execution | `run-agent`, `run-agents` | Run focused evidence agents |
| Proof review | `write-proof`, `seal-consensus` | Create and review a structured line-numbered proof |
| Verification | `verify-*` | Run scoped machine and artifact checks |
| Evaluation | `eval`, `benchmark`, `calibration`, `scorecard` | Measure workflow behavior and safety properties |
| Development | `export-schemas`, `new-run`, `inspect`, `memo` | Maintain and inspect Espresso |
| Compatibility | `run-flow` | Support earlier run workflows |

## Branches and reference material

| Location | Role | Stability |
| --- | --- | --- |
| `main` | Current Espresso Proof product runtime | Supported public product |
| `v2` | Future architecture laboratory | Evolving interfaces and artifact formats |

as a concrete control group. It supports focused comparison between:

- prompt-first and runtime-first architectures;
- skill-managed and kernel-managed artifacts;
- agent-directed and orchestrator-directed control flow.

See

## Architecture references

- [Execution Trunk](docs/engineering/execution-trunk.md) — canonical path from problem text to run artifacts
- [Product Philosophy](docs/product-philosophy.md) — research goals and design principles
- [Product Path Boundaries](docs/engineering/product-path-boundaries.md) — supported runtime ownership and boundaries
- [Espresso Core Integration](docs/engineering/espresso-core-integration.md) — external espresso-core adapter contract
- [Contributing](CONTRIBUTING.md) — setup, tests, pull request expectations, and trust-boundary rules
- [Security](SECURITY.md) — vulnerability reporting
- [Code of Conduct](CODE_OF_CONDUCT.md) — contributor conduct

## Acknowledgements

The literature subsystem, including discovery, review-agent work, live search,
and PDF ingestion, was started with @w-ruizhe.

@yangjunjie0320 contributed to the product philosophy and the Claude-centric
comparison route.

## License

Espresso Proof is released under the MIT License. See [LICENSE](LICENSE).
