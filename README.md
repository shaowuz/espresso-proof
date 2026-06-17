# Espresso Proof

## Canonical Product Path

The official product path is now:

```text
React cockpit
-> local loopback server
-> external Codex agent
-> artifact kernel
-> visible trace / UI
```

Start product work from the React cockpit served by `espresso serve`. The CLI,
VS Code extension, static dashboards, mock/stub providers, dogfood reports, and
archived goals are development, test, or historical surfaces unless the maintainer
explicitly says otherwise. See
`docs/engineering/PRODUCT_PATH_BOUNDARIES.md` before cleanup.

Espresso Proof is a customizable AI workflow platform for mathematical research. The first high-value workflow is proof-sketch critique plus idea grading, but that workflow is not the product boundary. The product boundary is a reusable runtime for project-specific mathematical workflows: `WorkflowSpec`, agent contracts, artifact graph, run manifest, memory, eval fixtures, human gates, source status, and Grade.

It is not a Lean-heavy prover, not a chatbox, and not an agent zoo. The platform value is artifact-based, auditable, reusable workflow: every important claim, source status, example check, strategy decision, and human choice should live in project files, not only in conversation.

Lean-light is intended adapter behavior after a Lean/Mathlib connector exists: use existing Mathlib results when they help; if they do not exist, continue with critique and counterexample checks. The current kernel does not include Mathlib integration.

This repository contains the local product kernel: React cockpit, loopback
server, Pydantic schemas, artifact validators, JSON Schema exports, external
Codex adapter, run manifest, memory, eval fixtures, source status, human gates,
strategy/prover/reviewer artifacts, and two seeded domains: `hodge_atoms` and
`coherent_base_change`. It also contains older CLI, VS Code, dashboard, mock,
and dogfood surfaces. Those are not the canonical product path.

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

Supporting documents:

- [CURRENT_STATE_FOR_NEW_CONTEXT.md](CURRENT_STATE_FOR_NEW_CONTEXT.md): short
  current handoff for a fresh agent context.
- [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md): shortest current map
  of active entry points, runtime folders, tests, mathematical data, and archive
  boundaries.
- [docs/engineering/CODEBASE_SPINE.md](docs/engineering/CODEBASE_SPINE.md):
  short code map for user entrypoints, orchestration, trust boundaries,
  artifact ownership, UI surfaces, and testing policy.
- [docs/FILE_OVERVIEW.md](docs/FILE_OVERVIEW.md): explanation of what each repository file and folder is for.
- [docs/goals/](docs/goals/): active goal queue only; historical goals live in
  `docs/archive/goals/`.
- [docs/product/](docs/product/): product strategy, human-in-the-loop design, idea grading, workflow synthesis, and prompt-building rules.
- [docs/engineering/](docs/engineering/): local kernel, schemas, evals, implementation goals, and release gates.
- [docs/engineering/SYSTEM_ARCHITECTURE.md](docs/engineering/SYSTEM_ARCHITECTURE.md): system architecture, module responsibilities, local runtime, retrieval, UI, memory, and evaluation boundaries.
- [docs/engineering/ESPRESSO_AGENT_PROTOCOL.md](docs/engineering/ESPRESSO_AGENT_PROTOCOL.md): source of truth for Codex/collaborator sessions, current capability boundaries, safe language, and validation discipline.
- [docs/engineering/CLEANUP_GUIDE.md](docs/engineering/CLEANUP_GUIDE.md): cleanup-only collaboration contract, layer boundaries, hot files, and future refactor plan.
- [docs/engineering/PRODUCT_PATH_BOUNDARIES.md](docs/engineering/PRODUCT_PATH_BOUNDARIES.md): canonical/dev-only/test-only/legacy/unsafe-to-delete boundaries.
- [docs/research/](docs/research/): research survey and source material about AI theorem proving and math agents.
- [docs/archive/](docs/archive/): older planning notes, status reports,
  dogfood reports, porting notes, and collaborator handoffs preserved for
  history, not current product direction.
- [docs/AGENT_INFRA_REFERENCES.md](docs/AGENT_INFRA_REFERENCES.md): infrastructure references for LLM provider calls, human-gated agent execution, durable orchestration, and VS Code webviews.
- [docs/REAL_AGENT_RUNBOOK.md](docs/REAL_AGENT_RUNBOOK.md): supported path for saving an OpenAI API key, checking the real provider, running OpenAI or external-command multiagent flow, and resolving the human gate.
- [docs/LITERATURE_DISCOVERY.md](docs/LITERATURE_DISCOVERY.md): conservative literature discovery, source ingestion, and source verification behavior.
- [docs/UI_PLAN.md](docs/UI_PLAN.md): legacy/dev-only VS Code side-panel scope and artifact-backed UI plan.
- [.claude/skills/espresso-proof/SKILL.md](.claude/skills/espresso-proof/SKILL.md): project-discoverable Claude Code skill for running the current Espresso agent flow in chat.

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

Before opening or merging a pull request, use local-first CI. For most cleanup
work, start with the focused lane:

```text
scripts/local_ci.sh quick
```

Use `scripts/local_ci.sh full` for trust-boundary/runtime merges. GitHub Actions
is manual/lightweight only.

Use this branch pattern:

```text
feature/<short-topic>
fix/<short-topic>
docs/<short-topic>
```

Do not commit directly to `main` once two people are coding. Make a branch, push it, open a pull request, and let the other person review it. Keep pull requests focused: one behavior change, one validator rule, one fixture family, or one documentation update.

### Suggested Work Tracks

Use work tracks instead of fixed personal ownership:

- Math product track: define meaningful examples, dangerous examples, rubrics, domain playbooks, Strategy Brief taste, and misleading-language rules.
- Runtime track: maintain CLI, schemas, validators, tests, memory, fixtures, and release gates.
- UI track: expose artifact-backed runs, Strategy Brief cards, human choices, example feedback, and source gaps without inventing mathematical status.
- Research/source track: improve literature discovery, theorem ingestion, source matching, and reference discipline.

Shared work happens at the boundary:

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

The old CLI surface remains available for development and tests:

```text
espresso init
espresso critique-sketch
espresso eval
espresso generate-agent
espresso improve-workflow
espresso run-llm-agents
espresso resolve-agent-gate
espresso run-prover-loop-round
espresso audit-theorem-applicability
```

These commands are not the canonical user product path. `critique-sketch` and
`eval` run deterministic mock artifacts. `generate-agent --mode v0` writes a
schema-valid draft workflow and leaves `admitted: false` until human
confirmation. `run-prover-loop-round` appends one resolved-gate
prover/reviewer round; it is not a durable long-running worker.

The current product loop is:

1. Understand and lock the user's mathematical problem.
2. If the claim is unclear, stop before Literature, Examples, Counterexamples, Strategy, Prover, and Reviewers.
3. If the claim is clear, run Literature Review and start Examples / Counterexamples as async pressure lanes.
4. Run the current runtime `source_theorem` candidate-only source/theorem pass before Strategy; it cannot create source-backed status or prove a route/theorem.
5. Conservatively regrade from checked artifacts and known blockers without treating pending pressure as survived/no-pressure evidence.
6. Generate several proof strategies; examples and counterexamples may still be pending.
7. Attack strategies with the examples/counterexamples bank and grade them with LLM mathematical judgment.
8. Start bounded Time Allocation from Strategy Grader output; Strategy Generator alone does not start Time Allocation.
9. Present a Strategy Brief and optionally collect Human Strategy Preference as a steering signal.
10. Let the Prover try allocated routes.
11. Let reviewers check proof outline, literature use, and line-level proof detail.
12. Send reviewer objections through Update Inbox as scheduling-only advice to Time Allocation, which decides whether to repair, retry, switch, stop, or spend more time.
13. Grade the current proof attempt and save useful routes, failures, examples, source gaps, and human preferences to Memory as advisory context only.

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
- Literature Review only finds candidate papers/examples/counterexamples; candidate discovery never decides whether a proof step is actually source-backed.
- A source candidate is not source-backed.
- Current runtime runs the candidate-only `source_theorem` pass before Strategy; supported/applicable proof steps remain step-local and do not prove a route or theorem.
- Pending examples are not `survived_tests`, and pending counterexamples are not `failed_tests` or no-pressure evidence.
- Stop after repeated no-progress loops and return a Strategy Brief instead of letting agents self-loop.
- Examples/Counterexamples Attacker can lower or block a strategy score; LLM Strategy Grader cannot overrule concrete example failure, unchecked computation, or theorem-condition gaps.
- Human Strategy Preference affects future time allocation, not proof status, source-backed status, or checked evidence. It is optional.
- Reviewer approval does not prove a theorem, and Memory is advisory, not evidence.
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
