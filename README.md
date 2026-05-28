# Espresso Proof

Espresso Proof is a customizable AI workflow platform for mathematical research. The first high-value workflow is proof-sketch critique plus idea grading, but that workflow is not the product boundary. The product boundary is a reusable runtime for project-specific mathematical workflows: `WorkflowSpec`, agent contracts, artifact graph, run manifest, memory, eval fixtures, human gates, source status, and Grade.

It is not a Lean-heavy prover, not a chatbox, and not an agent zoo. The platform value is artifact-based, auditable, reusable workflow: every important claim, source status, example check, strategy decision, and human choice should live in project files, not only in conversation.

Lean-light is intended adapter behavior after a Lean/Mathlib connector exists: use existing Mathlib results when they help; if they do not exist, continue with critique and counterexample checks. The current kernel does not include Mathlib integration.

This repository now contains the first local engineering kernel: Pydantic schemas, a Typer CLI, mock critique pipeline, `agent_contracts.yaml`, `literature_review.yaml`, `tests.yaml`, `loop_control.yaml`, `strategy_synthesis.yaml`, `strategy_brief.yaml`, and `human_choice.yaml` artifacts, indexed memory writing/loading, cross-artifact validators, JSON Schema exports, fixture contracts under `tests/fixtures/`, a deterministic `generate-agent --mode v0` admission flow, and two seeded domains: `hodge_atoms` and `coherent_base_change`. It still does not contain real LLM agents, real Literature Discovery, real Evidence Auditor adapters, VS Code integration, PDF ingestion, Mathlib integration, or long-running workers.

## Core Position

The first version should not try to be a general theorem prover. It should be a local, auditable workflow runtime plus one serious workflow: proof-sketch critique and idea grading. It works beside LaTeX, Markdown, and Lean files when present, and it always separates:

- informal mathematical suggestions,
- source-backed candidates from papers, notes, or Mathlib,
- background Lean-light checks of existing declarations or tiny snippets when available,
- human-confirmed interpretation of the mathematical claim.

The guiding rule is:

> Slow is acceptable. Wrong and confident is not acceptable.

## Documents

- [RESEARCH.md](RESEARCH.md): current landscape of AI theorem proving, Lean agents, benchmarks, theorem search, and research assistants.
- [PRODUCT_STRATEGY.md](PRODUCT_STRATEGY.md): algebraic geometry opportunity, positioning, product-form comparison, and MVP recommendation.
- [ARCHITECTURE.md](ARCHITECTURE.md): proposed system architecture, module responsibilities, critique loop, retrieval layer, and multi-agent workflow.
- [FLOW_DESIGN.md](FLOW_DESIGN.md): detailed user-facing flow for Generate AI Agent, Critique Sketch, and Improve Workflow.
- [ESPRESSO_FLOW.md](ESPRESSO_FLOW.md): final no-Lean-formalization agentic flow centered on examples, counterexamples, taste, and idea grading.
- [IDEA_GRADING.md](IDEA_GRADING.md): grading pipeline for deciding whether a proof idea is promising, fragile, likely false, refuted, or unclear.
- [IMPLEMENTATION_GOALS.md](IMPLEMENTATION_GOALS.md): concrete build goals, acceptance tests, failure modes, and release gates.
- [ENGINEERING_KERNEL.md](ENGINEERING_KERNEL.md): smallest buildable local kernel: CLI, artifacts, schemas, rejection gates, mock evals, and non-goals.
- [SCHEMAS.md](SCHEMAS.md): structured YAML/JSON schemas for claims, attempts, examples, counterexample tests, source grounding, idea grades, and memory.
- [ORCHESTRATOR.md](ORCHESTRATOR.md): state machine, parallel/serial agent rules, major human gates, long-run policy, and checkpoint rules.
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

The product loop remains:

1. Ask the user about the research direction, important papers, goal, style, and cost preference.
2. Run a first literature review summary. In the current kernel this is a mock `literature_review.yaml`, not live Literature Discovery.
3. Draft a specialized, runnable workflow.
4. Let the user tune it with natural language: `more examples`, `more proof`, `stricter review`, `cheaper`, `slower but more careful`.
5. Run one trial round, then have the AI suggest adjustments.
6. Save the confirmed workflow as `projects/<name>/playbook.md` and `workflow_spec.yaml`.
7. Use the workflow to critique proof sketches with hypothesis ledger, locality/descent checks, examples, counterexample tests, reference needs, a Review verdict, and a Grade.
8. After the Grade, run Strategy Synthesizer to organize the best next routes, then present a Strategy Brief and ask the human whether to stop, run a quick extra test, invest deeper effort, or save the direction for later exploration. Real 6h/24h long runs are post-V0.

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
- Literature Review only finds candidate papers/examples/counterexamples; Referencing alone decides whether a proof step is actually source-backed.
- Stop after repeated no-progress loops and return a Strategy Brief instead of letting agents self-loop.
- Evidence Auditor evidence must come from exact arithmetic, CAS, tiny Lean snippets, or source lookup; manual review needed cannot count as checked evidence.
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
