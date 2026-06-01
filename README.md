# Espresso Proof

Espresso Proof is a customizable AI workflow platform for mathematical research. The first high-value workflow is proof-sketch critique plus idea grading, but that workflow is not the product boundary. The product boundary is a reusable runtime for project-specific mathematical workflows: `WorkflowSpec`, agent contracts, artifact graph, run manifest, memory, eval fixtures, human gates, source status, and Grade.

It is not a Lean-heavy prover, not a chatbox, and not an agent zoo. The platform value is artifact-based, auditable, reusable workflow: every important claim, source status, example check, strategy decision, and human choice should live in project files, not only in conversation.

Lean-light is intended adapter behavior after a Lean/Mathlib connector exists: use existing Mathlib results when they help; if they do not exist, continue with critique and counterexample checks. The current kernel does not include Mathlib integration.

This repository now contains the first local engineering kernel: Pydantic schemas, a Typer CLI, mock critique pipeline, `agent_contracts.yaml`, `literature_review.yaml`, `tests.yaml`, `loop_control.yaml`, `strategy_synthesis.yaml`, `strategy_brief.yaml`, `human_choice.yaml`, optional `human_example_feedback.yaml`, optional `example_feedback_summary.yaml`, and optional `llm_agent_run.yaml` artifacts, indexed memory writing/loading, cross-artifact validators, JSON Schema exports, fixture contracts under `tests/fixtures/`, a deterministic `generate-agent --mode v0` admission flow, conservative Literature Discovery commands, PDF/text theorem ingestion, conservative source verification, a local VS Code side-panel prototype, a runnable `espresso run-flow` multiagent command, an initial human-gated LLM prover plus Espresso reviewer-set runtime, and two seeded domains: `hodge_atoms` and `coherent_base_change`. It still does not contain LLM tool execution, durable long-running workers, real Evidence Auditor adapters, Mathlib integration, or proof automation.

## Core Position

The first version should not try to be a general theorem prover. It should be a local, auditable workflow runtime plus one serious workflow: proof-sketch critique and idea grading. It works beside LaTeX, Markdown, and Lean files when present, and it always separates:

- informal mathematical suggestions,
- source-backed candidates from papers, notes, or Mathlib,
- background Lean-light checks of existing declarations or tiny snippets when available,
- human-confirmed interpretation of the mathematical claim.

The guiding rule is:

> Slow is acceptable. Wrong and confident is not acceptable.

## Documents

Core design documents:

- [PRODUCT_PHILOSOPHY.md](PRODUCT_PHILOSOPHY.md): product philosophy, core mathematical judgment principles, and what Espresso should/should not become.
- [AGENT_FLOW.md](AGENT_FLOW.md): current multi-agent workflow, parallel/serial rules, review stages, human gates, and memory loop.
- [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md): system architecture, module responsibilities, local runtime, retrieval, UI, memory, and evaluation boundaries.
- [FILE_OVERVIEW.md](FILE_OVERVIEW.md): explanation of what each repository file and folder is for.

Supporting documents:

- [ESPRESSO_AGENT_PROTOCOL.md](ESPRESSO_AGENT_PROTOCOL.md): source of truth for collaborator/Codex sessions, UI V1, artifact rules, and agent execution boundaries.
- [RESEARCH.md](RESEARCH.md): current landscape of AI theorem proving, Lean agents, benchmarks, theorem search, and research assistants.
- [PRODUCT_STRATEGY.md](PRODUCT_STRATEGY.md): algebraic geometry opportunity, positioning, product-form comparison, and MVP recommendation.
- [FLOW_DESIGN.md](FLOW_DESIGN.md): detailed user-facing flow for Generate AI Agent, Critique Sketch, and Improve Workflow.
- [IDEA_GRADING.md](IDEA_GRADING.md): grading pipeline for deciding whether a proof idea is promising, fragile, likely false, refuted, or unclear.
- [IMPLEMENTATION_GOALS.md](IMPLEMENTATION_GOALS.md): concrete build goals, acceptance tests, failure modes, and release gates.
- [ENGINEERING_KERNEL.md](ENGINEERING_KERNEL.md): smallest buildable local kernel: CLI, artifacts, schemas, rejection gates, mock evals, and non-goals.
- [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md): product and engineering design for human mathematical judgment, feedback labels, memory, and non-upgrade rules.
- [HUMAN_IN_THE_LOOP_AUDIT.md](HUMAN_IN_THE_LOOP_AUDIT.md): implementation audit of Human Choice, Strategy Brief, memory, and Human Example Feedback artifacts.
- [docs/AGENT_INFRA_REFERENCES.md](docs/AGENT_INFRA_REFERENCES.md): infrastructure references for LLM provider calls, human-gated agent execution, durable orchestration, and VS Code webviews.
- [docs/REAL_AGENT_RUNBOOK.md](docs/REAL_AGENT_RUNBOOK.md): supported path for saving an OpenAI API key, checking the real provider, running OpenAI or external-command multiagent flow, and resolving the human gate.
- [skill/espresso-proof/SKILL.md](skill/espresso-proof/SKILL.md): Claude-style skill prototype for running the current Espresso agent flow in chat.
- [SCHEMAS.md](SCHEMAS.md): structured YAML/JSON schemas for claims, attempts, examples, counterexample tests, source grounding, idea grades, and memory.
- [EVALS.md](EVALS.md): fixture format, rubrics, automated checks, and release gates.
- [PROJECT_SKELETON_REVIEW.md](PROJECT_SKELETON_REVIEW.md): engineering/math review of feasibility, gaps, and priority improvements before coding.
- [ROADMAP.md](ROADMAP.md): four-week MVP plan with acceptance criteria and explicit cuts.
- [WORKFLOW_SYNTHESIZER.md](WORKFLOW_SYNTHESIZER.md): meta-workflow for turning a research prompt into a specialized project playbook.
- [UPDATED_PLAN.md](UPDATED_PLAN.md): platform-oriented V0 plan: kernel, first workflow, workflow generator, and later platform surface.

## Project Playbooks

- [projects/hodge_atoms/playbook.md](projects/hodge_atoms/playbook.md): first saved specialized workflow for Hodge atoms, Hodge-theoretic mirror symmetry, KKPY, and cubic fourfold proof-sketch critique.
- [projects/hodge_atoms/example_bank.yaml](projects/hodge_atoms/example_bank.yaml): first machine-verifiable seed example bank for Hodge atoms critique.
- [projects/coherent_base_change/playbook.md](projects/coherent_base_change/playbook.md): second seeded workflow for coherent sheaf cohomology, base change, flatness/properness hypotheses, and local-to-global pathologies.
- [projects/coherent_base_change/workflow_spec.yaml](projects/coherent_base_change/workflow_spec.yaml): draft `WorkflowSpec` for the second domain.

## Collaboration Workflow

This project should be developed through small branches and reviewed pull requests. The main branch should stay runnable and should pass the local release gate before important merges.

Recommended local setup:

```text
git clone git@github.com:shaowuz/espresso-proof.git
cd espresso-proof
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Before opening or merging a pull request, run:

```text
.venv/bin/python -m pytest -q
.venv/bin/espresso eval --mode mock
.venv/bin/espresso eval --schemas-only
```

Use this branch pattern:

```text
feature/<short-topic>
fix/<short-topic>
docs/<short-topic>
```

Do not commit directly to `main` once two people are coding. Make a branch, push it, open a pull request, and let the other person review it. Keep pull requests focused: one behavior change, one validator rule, one fixture family, or one documentation update.

### Suggested Division of Work

the maintainer leads the mathematical product layer:

- decide whether examples are meaningful, dangerous, too easy, misleading, or worth saving;
- write and review fixture `rubric.md` files;
- curate domain playbooks and example banks under `projects/`;
- review Strategy Brief outputs and decide whether the suggested routes have real mathematical taste;
- define the human feedback labels that should influence future example ranking without pretending to be proof evidence.

The engineering collaborator leads the runtime layer:

- keep the CLI, schemas, validators, and tests reliable;
- add focused negative tests for unsafe proof claims, source misuse, unchecked examples, and invalid strategy scoring;
- maintain the fixture runner and release-gate commands;
- implement small, inspectable modules before changing hot files;
- keep generated artifacts deterministic and schema-valid.

Shared work should happen at the boundary:

- every new product rule needs a fixture;
- every fixture needs a mathematical rubric;
- every validator rule needs at least one negative test;
- every agent or workflow change must leave auditable artifacts, not only chat output.

### Working Agreement

Start each coding session by pulling the latest `main`. If both people need to touch the same hot file, agree on the owner for that file before editing. Hot files include:

```text
espresso_proof/mock_pipeline.py
espresso_proof/validators.py
espresso_proof/cli.py
espresso_proof/models.py
tests/test_kernel.py
```

Use issues or a short shared TODO list for task ownership. A good issue should name:

- the product behavior being protected;
- the artifact or schema affected;
- the fixture that proves the behavior;
- the release-gate commands that must pass.

Pull request checklist:

- What changed?
- Which product rule does this protect?
- Which files are affected?
- Which tests or evals were run?
- Are any proof-like claims, source-backed claims, or strategy scores newly allowed?
- Does this preserve the positioning: not a Lean-heavy prover, not a chatbox, not an agent zoo?

### Immediate Coding Priorities

The highest-value next features are:

- Rubric discipline: make every fixture explain the mathematical reason it exists.
- Strategy Brief Review: let human route choices feed memory and future strategy ranking.
- GitHub hygiene: add CI, issue templates, pull request template, and branch protection after the first collaborator has access.

## Recommended V0

Build the **Espresso Proof local workflow kernel first**, then the first proof-sketch critique workflow, then the AI workflow generator.

The first engineering target is implemented as a mock/local kernel:

```text
espresso init
espresso critique-sketch
espresso eval
espresso generate-agent
espresso improve-workflow
```

Current command scope is deliberately narrow. `critique-sketch` and `eval` run deterministic mock artifacts. `generate-agent --mode v0` classifies the prompt into a small deterministic domain set, writes a schema-valid draft `workflow_spec.yaml`, `agent_contracts.yaml`, `admission_check.yaml`, `playbook.md`, and `trial_fixture/`, then leaves `admitted: false` until human confirmation. It does not create a live LLM agent.

The current product loop is:

1. Understand and lock the user's mathematical problem.
2. Run Literature Review, Examples, and Counterexamples in parallel.
3. Generate several proof strategies.
4. Attack strategies with the examples/counterexamples bank and grade them with LLM mathematical judgment.
5. Check whether references actually match each strategy.
6. Present a Strategy Brief and collect Human Strategy Preference.
7. Let the Prover try the selected or high-score routes.
8. Let reviewers check proof outline, literature use, and line-level proof detail.
9. Send reviewer objections to the Time Allocation Agent, which decides whether to repair, retry, switch, stop, or spend more time.
10. Grade the current proof attempt and save useful routes, failures, examples, source gaps, and human preferences to Memory.

After a Lean adapter exists, V0 should default to background **Lean-light**: use existing Mathlib/Lean results if they are available, but skip Lean if they are not. Do not ask the user to choose a Lean mode, and do not formalize the user's research theorem by default.

Hard product decision:

> Espresso Proof does not do Lean formalization of user research statements. Lean-light is only background theorem lookup, similar to source retrieval.

## Design Principles

- Use multiple attempts; never bet on one proof route.
- Keep Generator and Review separate.
- Every important proof step needs citation or source grounding.
- Failed attempts, counterexamples, and rejected analogies enter memory.
- Work local-first in project files; editor-specific UI is post-V0.
- Orchestrator dispatches specialists; sub-agent outputs must be inspectable.
- User can approve, stop, resume, and tune workflows.
- Human approval is needed only for major decisions; V0 only shapes the checkpoint/resume skeleton for future long runs.
- Custom agents are editable Markdown/YAML-like playbooks.
- Platform value comes from workflows being composable, auditable, reusable, and evaluable; not from having many agents.
- All advanced capabilities must leave artifacts, not only chat history.
- Literature Review only finds candidate papers/examples/counterexamples; `Check If References Match Strategies` decides whether a proof step is actually source-backed.
- Stop after repeated no-progress loops and return a Strategy Brief instead of letting agents self-loop.
- Examples/Counterexamples Attacker can lower or block a strategy score; LLM Strategy Grader cannot overrule concrete example failure, unchecked computation, or theorem-condition gaps.
- Human Strategy Preference affects future time allocation, not proof status, source-backed status, or checked evidence.
- Do not become a broad research workbench.
- Do not let LaTeX, slides, Lean, computation, or tool count replace mathematical judgment.

The three product actions are:

- **Generate AI Agent**
- **Critique Sketch**
- **Improve Workflow**

## Why Not Start With Full Automation

Modern systems can solve some formal benchmark problems, but the failure mode that matters here is research-level algebraic geometry: missing hypotheses, wrong definitions, hallucinated lemmas, and proof sketches that sound plausible while changing the theorem. Lean is valuable when the statement is already formalized faithfully, but that is usually not the cheap first step for hard AG research. The hard product problem is preserving mathematical meaning across informal text, literature, local notes, examples, and counterexamples.

## Primary References Used

- [Lean 4 documentation](https://lean-lang.org/doc/reference/latest/)
- [Mathlib documentation](https://leanprover-community.github.io/mathlib4_docs/Mathlib)
- [Lean 4 VS Code extension](https://github.com/leanprover/vscode-lean4)
- [LeanDojo](https://leandojo.org/index.html)
- [miniF2F](https://github.com/openai/miniF2F)
- [proof-close](https://arxiv.org/abs/2604.24021)
- [DeepSeek-Prover](https://arxiv.org/abs/2405.14333)
- [DeepSeek-Prover-V2](https://github.com/deepseek-ai/DeepSeek-Prover-V2)
- [Numina-Lean-Agent](https://arxiv.org/abs/2601.14027)
- [ArXivLean](https://matharena.ai/arxivlean/)
- [LeanSearch](https://leansearch.net/)
- [Moogle](https://www.moogle.ai/)
- [Loogle](https://loogle.lean-lang.org/)
- [Google DeepMind AlphaProof](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/)
- [AI co-mathematician](https://arxiv.org/abs/2605.06651)
