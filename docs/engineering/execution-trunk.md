# Execution Trunk — how one math problem becomes one run directory

The canonical runtime path from problem text to output files: the command
(section 2), the entry-point map (3), the state machine (4), error routing (5).
Line numbers are structural hints — they drift; function names do not. The
function-level dataflow is not diagrammed here: it reads directly in the code.

## 1. What one run is

- **In:** a markdown file with a claim or proof sketch (or the same text
  pasted into the browser cockpit). **Out:** one run directory
  `projects/<project>/runs/<run_id>/` with ~30 YAML/Markdown artifacts plus an
  HTML dashboard, each schema-validated after every stage (`validate_run`,
  fail closed).
- By default the run **stops at a human gate** (`WAITING_LLM_AGENT_HUMAN_GATE`);
  a second command resumes it to `COMPLETED_MEMO`. With `--auto-continue` one
  command runs to `COMPLETED_MEMO`: machinery picks the top-scored strategy and
  the artifacts say so (`selected_by/resolved_by='auto'`,
  `human_confirmed=false` — auto can never claim human confirmation). Earlier
  stops (claim clarification, front-half ask_human) are human questions and are
  never auto-answered.
- Nothing on this path can upgrade proof / source / evidence / grade status.
  The kernel validators are the only gate (section 9).

## 2. The one command (two commands around the human gate; one with `--auto-continue`)

`espresso orchestrate` is a thin CLI wrapper over `orchestrate_run()`
(`espresso_proof/orchestration/orchestrator.py`, docstring: "the single
canonical runtime path").

```bash
echo "Claim: every group of prime order is cyclic." > problem.md
uv run espresso orchestrate --input problem.md --project demo --provider stub
# Wrote run artifacts to projects/demo/runs/demo-YYYYMMDD-HHMMSS-xxxxxxxx
# State: WAITING_LLM_AGENT_HUMAN_GATE                <- the designed stop
```

A human then picks a strategy from the run's `strategy_brief.yaml` (ids:
`top_strategies[].id`; efforts: `allowed_human_choices[].effort_options`; anything else is rejected):

```bash
uv run espresso orchestrate --resume projects/demo/runs/<run_id> \
    --select-strategy strategy_investigate_route --effort normal_pass
# bounded prover<->reviewer loop runs, memo prints, State: COMPLETED_MEMO
```

Or let machinery pick — one command, honest provenance
(`--auto-continue` on the fresh run, or in place of `--select-strategy` on
resume; `auto_select_strategy` takes the top-scored strategy, never widening
what a human could have chosen):

```bash
uv run espresso orchestrate --input problem.md --project demo --provider stub --auto-continue
# ... State passes AUTO_CHOICE_CONTINUE_SELECTED, memo prints, State: COMPLETED_MEMO
```

`examples/quickstart.sh` scripts the first half. Providers (`--provider`; the
pre-gate "front half" stages follow it via `--front-half auto`):

| provider | client (`espresso_proof/agents/llm_agents.py`) | needs |
|---|---|---|
| `stub` | `StubLLMClient` — deterministic, offline; smoke tests and this quickstart | nothing |
| `openai` | `OpenAIResponsesClient` (:484) | `OPENAI_API_KEY` |
| `external` | `ExternalCommandClient` (:562) — spawns the command in `$ESPRESSO_EXTERNAL_AGENT_COMMAND`, JSON over stdin/stdout | e.g. `export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"` — the shipped Codex adapter, `agents/codex_adapter.py:main` |

Two more shipped adapters are opt-in `ESPRESSO_EXTERNAL_AGENT_COMMAND` targets
behind the same `external` seam: `espresso-claude-agent` (`agents/claude_adapter.py`,
Claude-CLI-backed) and `espresso-core-agent` (`agents/espresso_core_adapter.py`,
experimental Phase A — prover-family roles go to espresso-core, everything else
to a fallback adapter; contract: `docs/engineering/espresso-core-integration.md`).

## 3. Entry points → entry functions

The per-stage dataflow reads directly in the body of `orchestrate_run()`
(`orchestration/orchestrator.py`), one named stage per call:
`verify_agent_providers_before_run_exists` → `create_or_attach_genesis_run` →
`build_front_half_context_artifacts` → `stop_if_claim_is_not_clear` →
`collect_candidate_literature` → `execute_scoped_machine_checks` (EXECUTES the
front half's scoped verification requests; a confirming result earns trusted
`instance_checked`/`computation_checked` evidence) →
`run_initial_prover_reviewer_round_to_human_gate` →
`publish_agent_process_board` → `write_dashboard_and_return_run_summary`.
Read those bodies for stage detail; a prose copy here would only drift.

| Command / surface | Entry function | File |
|---|---|---|
| `espresso orchestrate` | `orchestrate_run` | `orchestration/orchestrator.py` |
| `espresso orchestrate --resume` | `resume_run` | `orchestration/orchestrator.py` |
| `espresso run-agent` | `run_single_agent` | `orchestration/agent_runner.py` |
| `espresso run-agents` / `POST /api/runs/{id}/agents-batch` | `run_evidence_batch_auto` | `orchestration/evidence_coordinator.py` |
| `espresso serve` → `POST /api/runs` | the same `orchestrate_run` (`fast=True`) | `server.py` → `orchestration/orchestrator.py` |
| `espresso write-proof` | `write_line_numbered_proof` | `orchestration/proof_writer.py` |
| `espresso seal-consensus` | `seal_agent_consensus` | `orchestration/agent_consensus_seal.py` |

## 4. The lifecycle state machine

Eleven ordered states, `ORCHESTRATION_STATES` (orchestrator.py:75). A stage runs
only if the run has not advanced past it (`_reached`, orchestrator.py:123) —
this is what makes `orchestrate_run` idempotent and resumable:

```
SCAFFOLDED -> FRONT_HALF_DONE -> CLAIM_CLEAR -> LITERATURE_DONE -> WAITING_LLM_AGENT_HUMAN_GATE
                                                                        |            |
   (unclear claim parks at WAITING_CLAIM_CLARIFICATION instead)         |   human picks a strategy —
                                                                        |   or machinery does (--auto-continue)
                                                                        v            v
                             HUMAN_CHOICE_{CONTINUE|REVISE|STOP}_SELECTED -> COMPLETED_MEMO
                             AUTO_CHOICE_CONTINUE_SELECTED (auto peer; never spelled HUMAN_*)
```

Both `WAITING_*` states are designed stops, not failures; the run stays valid
and resumable at each. `HUMAN_CHOICE_*` is written by `choose-strategy`
(cli.py:460) as a direct, re-validated manifest write;
`AUTO_CHOICE_CONTINUE_SELECTED` by `orchestrate_run(auto_continue=True)` just
before the same-process resume (a crash mid-loop leaves the truthful state
behind); `COMPLETED_MEMO` by `_finalize_resume_memo` (plain `--resume` skips
`HUMAN_CHOICE_*`). All other state advances happen inside validated
`StageRunner` transactions.

## 5. Error routing

| Failure | Where handled | Result |
|---|---|---|
| Provider unconfigured | `verify_agent_providers_before_run_exists` (orchestrator.py) raises before scaffold | Fail closed before ANY file is written; server turns it into a failed job with product copy (`_friendly_error`, server.py:88) |
| External agent timeout | `ExternalCommandClient.complete_json` kills the process group, raises `ExternalAgentTimeoutError` (llm_agents.py); orchestrator records `agent_status="timed_out"` in agent_events.jsonl | Stage rolls back; timeout distinguishable from other failures; no auto-retry |
| External agent bad output (non-zero exit / non-JSON) | `ExternalCommandClient` raises; the shipped Codex adapter separately converts its own parse failures into valid `needs_revision` JSON (codex_adapter.py, `parse_status="fallback"`) | Adapter soft-fail becomes honest "no usable output" content; client hard-fail rolls the stage back |
| Any stage mutation / validation failure | `StageRunner.run` (kernel/stage_runner.py:133): restore snapshot, append `validation_failed` + `rollback` to events.jsonl, raise `StageError` (:71) | Run restored to last valid state; lifecycle NOT advanced; re-running `orchestrate` skips completed stages |
| On-demand agent failure | `run_single_agent` catches `StageError` (agent_runner.py) | Board shows `failed` + error summary; previous valid artifact intact; retries allowed and counted |
| Prover-loop round failure | `_restore_run` in prover_loop_driver.py; `stop_reason="validation_error"` | Loop exits fail-closed; resume still completes the memo; the round's math survives in advisory_proof_sketch.yaml |
| Claim not clear | `stop_if_claim_is_not_clear` -> `_finish_without_llm_gate` (orchestrator.py); per-stage gate in front_half/runner.py; CLI gate `_require_claim_clear_for_cli` (cli_shared.py:106, re-exported by cli.py) | Run parks at WAITING_CLAIM_CLARIFICATION; no prover spend |
| Two writers on one run | `run_writer_lock` (kernel/run_lock.py:92) -> `RunLockedError`; the server adds a per-run thread lock | Serialize or fail with a friendly message; never interleaved rounds |
| Run stops passing validation while displayed | server `snapshot`/`status` re-validate first (Workbench.snapshot, server.py:798) | 409 instead of stale fragments; disk untouched |
| Process dies mid model call | dangling "started" record in agent_events.jsonl; `latest_unfinished_agent_call` (kernel/observability.py:231); `_run_stalled` (server.py:738) | UI shows the last active stage + elapsed time instead of a dead page |

Errors surface in: the CLI echo, `events.jsonl` (stage transitions/rollbacks),
`agent_events.jsonl` (every model call), and the server job table / cockpit.

## 6. What lands in the run directory

`projects/<project>/runs/<project>-<YYYYMMDD-HHMMSS>-<hex8>/` (naming:
genesis.py:33). Grouped by who writes them:

- **Scaffold (Stage 0), required by `validate_run`:** `run_manifest.yaml`
  (run_id, current_state, artifact registry), `input.md`, and the 16 core
  artifacts (locked_claim, agent_contracts, hypothesis_ledger,
  literature_review, examples, computation_checks, counterexamples, tests,
  source_grounding, review, idea_grade, loop_control, strategy_synthesis,
  strategy_brief, human_choice — all `.yaml` — plus `final_report.md`).
  Front-half stages rewrite these.
- **Front half + gate:** `front_half_run.yaml`, `claim_clear_gate.yaml`.
- **Prover pass:** `llm_agent_run.yaml`, `prover_loop.yaml`,
  `time_allocation.yaml`, `prover_rounds/index.yaml` + `round_NNN_*.yaml`.
- **Snapshot-excluded observability (never rolled back; stage_runner.py:55):**
  `events.jsonl`, `agent_events.jsonl`, `timings.yaml`, `.run_writer_lock`,
  `advisory_proof_sketch.yaml`, `repair_target.yaml`.
- **Process board:** `agent_run_status.yaml`, `pressure_status.yaml`, `stage_status.yaml`.
- **Resume adds:** `update_inbox.yaml`, `mathematical_judgment.yaml`,
  `failure_pattern_audit.yaml`, `memory_context.yaml`, `theorem_condition_ledger.yaml`,
  `proof_goal_graph.yaml`, `human_time_allocation_context.yaml`.
- **Display only (kernel never reads):** `research_dashboard.html`. **Proof
  branch (section 8):** `line_numbered_proof.yaml`, `agent_consensus_review.yaml`.
- **Outside the run dir:** project memory in `projects/<project>/memory/`;
  adapter replay logs in `.espresso_agent_logs/`. All run output is gitignored.

## 7. The other two entrypoints share this trunk

1. **The product server** (`espresso serve` -> server.py `serve`; stdlib
   ThreadingHTTPServer on 127.0.0.1:8765; serves the `web/` cockpit). `POST
   /api/runs` -> background job -> `_do_create_run` -> **the same
   `orchestrate_run`** (`fast=True`); every cockpit button re-enters trunk
   functions (`start_agent` -> `run_single_agent`; Continue a route ->
   `continue_route` -> **the same `resume_run`**). Run ids are path-confined by
   `resolve_run`; the server never runs shell commands from browser input.
2. **`run-flow`** (cli_legacy.py) — a thin compatibility wrapper over the same
   `orchestrate_run`; kept for old diagnostics only.

## 8. Branches (read only when you need them)

- **Bounded machine verifiers:** `cli_verify.py` + `verification/` — earn
  scoped `instance_checked` / `computation_checked`, nothing more.
- **Proof object + consensus seal (CLI only, section 3 table):** three
  adversarial reviewers over the line-numbered proof (never a route);
  unanimous no-fatal-flaw with cited line ids seals the amber, hash-bound
  `agent_consensus_mock_checked` status; route-only runs fail closed.
- **Project memory:** `memory/` — per-project index, advisory only.
- **Evaluation / dev diagnostics:** `cli_eval.py` + `evaluation/`, `cli_dev.py`
  — developer tooling, not the product path.

## 9. Trust boundary in ten lines

1. `kernel/models` (schemas) + `kernel/validators` (`validate_run`) decide every status; nothing else does.
2. `validate_run` re-runs after every mutation on this trunk; failure means rollback, never "keep going".
3. `kernel/stage_runner.py` is the transaction boundary: snapshot -> mutate -> validate -> advance, rollback on failure.
4. LLM output is scrubbed on entry (`make_prover_attempt`): proof-claiming language quarantined, status fields stripped.
5. Machine verifiers earn only scoped `instance_checked` / `computation_checked`; `lean_checked` and `human_confirmed` are unearnable.
6. `agent_consensus_mock_checked` is amber, never machine-trusted, revoked by any edit to the sealed proof (hash check in `kernel/validators`).
7. The human gate directs ranking and effort only; it can never upgrade status.
8. Logs cannot impersonate evidence: status-bearing diagnostic records are dropped by the observability scrubber.
9. The server confines run ids to the runs root and never executes browser input.
10. Before deleting or moving anything on this path, read `docs/engineering/product-path-boundaries.md`.

## 10. Doc freshness rule

The call-level map is the code — start from the entry functions in section 3.
This document keeps only what no single file expresses (state machine, error
routing, artifact map, trust boundary). On conflict the code wins; fix this
file in the same PR that changes the trunk.
