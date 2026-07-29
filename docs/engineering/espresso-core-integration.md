# espresso-core Integration: strong prover backend behind the external-agent seam

Status: Phase A (contract + adapter skeleton + mock tests). Adapter:
`espresso_proof/agents/espresso_core_adapter.py` (console script
`espresso-core-agent`). Tests: `tests/orchestration/test_espresso_core_adapter.py`.
Everything here was verified against the espresso-core source checkout
(`src/strategy_prover/cli.py`, `engine/solve.py`, `schemas/*.yaml`, example configs)
and against this repo's `espresso_proof/agents/` runtime (clients.py / agent_pass.py; llm_agents.py is the compatibility facade).

## 1. Why this pairing

espresso-core declares, as a design principle, that it is NOT autonomous proof
search from a bare theorem: **its central input is a human-chosen strategy** for a
locked claim, plus context. That is *exactly* what espresso_proof's front half
produces:

| espresso_proof front half output               | espresso-core declared input                          |
| ---------------------------------------------- | ----------------------------------------------------- |
| locked claim (`locked_claim.yaml`: `raw_text`, `normalized_statement`, `hypotheses`, `conclusion`) | `## Claim` section (required; `claim_lock.py` recognized headings) |
| HUMAN strategy choice at the gate (`human_choice`, `strategy_brief.top_strategies[].{id,title,why_this_route,next_loop_plan}`) | `## Human strategy` section -> `strategy_origin: human` (no approval stall) |
| literature / source grounding (`source_grounding.source_refs[]` with `status: source_backed`, `statement_or_summary`, `locator`) | `## Known results` / `## Sources or references` sections |
| examples / counterexamples / hypothesis ledger | `## Constraints` / `## Forbidden shortcuts` sections   |

So espresso_proof runs the front half (claim locking, literature, examples,
counterexamples, strategy generation, **human strategy choice at the gate**), and
espresso-core acts as the strong prover backend for prover-loop rounds. "Strong"
means stronger *orchestration and audit* (strategy -> proposition DAG -> lemma
tasks -> prover/verifier/review-panel loop -> digest-chained `verify-run`), NOT
stronger certification: espresso-core has no Lean/Coq backend, and its
`proof_status: proved` means an LLM verifier quorum (default 2 calls) agreed.

Consequence, non-negotiable: **core results come back as candidate-only JSON, the
espresso_proof kernel revalidates everything, and the consensus seal remains the
only status-upgrade path.** Core "proved" maps to `"plausible_direction"` plus
quorum-accepted-candidate wording — never to any espresso_proof status.

## 2. The boundary contract (exact field names, both sides)

### 2.1 espresso_proof side: the ExternalCommandClient seam

The ONLY injection point is the `LLMClient` protocol; out-of-process it is
`ExternalCommandClient` (`espresso_proof/agents/clients.py:253`), selected with
`--provider external` + `ESPRESSO_EXTERNAL_AGENT_COMMAND=/abs/path/adapter`.

Request, one JSON object on stdin:

```json
{"system": "...", "prompt": "...", "provider": "external",
 "model": "...", "required_json_schema": { ... }}
```

Reply: ONE JSON object on stdout (or `{"output": {...}}`). Non-zero exit /
non-JSON / non-dict -> `RuntimeError` -> the round **fails closed and rolls back**.
Timeout: `ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS` (default 120s; the whole
process group is killed on expiry).

`required_json_schema` is dispatched per role by `_required_json_schema_for_system`
(`agents/output_schemas.py:165`), keyed on the system prompt:

- prover / reviewers (default): `AGENT_OUTPUT_SCHEMA` = required
  `{summary, steps, source_requests, missing_hypotheses, risks, verdict, recommended_human_action}`
- "line-numbered proof writer": `LINE_NUMBERED_PROOF_OUTPUT_SCHEMA` = required
  `{proof_kind, lines[]}` with each line requiring
  `{line_id, statement, justification, dependencies[], depends_on, source_refs, gaps}`
  and each dependency requiring `{type, ref, status, role, needed_statement,
  source_locator, reviewer_verdict, authority, source_grounded, machine_verified}`
- "proof-consensus review": `PROOF_CONSENSUS_OUTPUT_SCHEMA` = required
  `{verdict, looked_for, finding, cited_line_ids, residual_objections}`
- "proof gap resolver": `PROOF_GAP_RESOLVER_OUTPUT_SCHEMA` = required
  `{candidates[]}` (per-candidate line_id / dependency_ref / action / locator fields)

The `prompt` is `agent_prompt()` JSON: `{"role": <role>, "protocol_rules": [...],
"context": {...}, "required_json_schema": {...}}`. Role values seen by the adapter:
`prover` (round 1), `prover_loop_round` (loop rounds), `round_N_<reviewer_role>`,
`line_numbered_proof_writer`, `proof_review_<reviewer_id>` (consensus seal),
`proof_gap_resolver`. `context` carries the front-half artifacts
(`claim`, `hypothesis_ledger`, `literature_review`, `examples`, `counterexamples`,
`source_grounding`, `strategy_brief`, `idea_grade`, ...) plus
`previous_prover_loop_attempt` (whose `strategy_id` is the human-selected route),
`reviewer_blockers_to_repair`, `target_subgoal`, `human_feedback_advisory`.

Caution (verified): reviewer prompts EMBED the prover attempt, so the substring
`"role": "prover"` appears inside reviewer prompts. Role detection must read the
top-level `role` field, not substring-match the payload
(`espresso_core_adapter.detect_role`).

### 2.2 espresso-core side: input format, CLI, output artifacts

Input A (the seam we use): one **raw markdown problem file**. Recognized section
headings (`engine/claim_lock.py:42`): `## Claim` (required), `## Human strategy`,
`## Known results`, `## Constraints`, `## Forbidden shortcuts`,
`## Sources or references`. With no `## Human strategy`, extraction yields
`strategy_origin: system_generated` and solve STOPS at `needs_human` (exit 2)
until `approve-strategy --approver X` — a default-deny gate that matches our
human-in-loop stance; the adapter maps it to `verdict: blocked`.

Input B (structured capsule, `schemas/`): `input/problem.yaml` (required
`problem_id`, `goal`), `input/strategy.yaml` (required `strategy_id`,
`human_strategy`; optional `constraints`, `allowed_background`,
`forbidden_shortcuts`, `strategy_origin`), `input/context.yaml` (required
`definitions[]`, `known_results[]`, `allowed_notation`). Phase A uses input A;
input B is generated internally by core's plan integration.

Exact CLI invocation (verified against `cli.py` argparse, lines 125-135 / 47-49;
`--schemas-dir` defaults to cwd-relative `"schemas"` so it MUST be absolute):

```
<core>/.venv/bin/espresso-core solve <problem.md> <run_dir> \
    --mode live --config <host_config.yaml> \
    --schemas-dir <core_checkout>/schemas --force
<core>/.venv/bin/espresso-core verify-run <run_dir> \
    --schemas-dir <core_checkout>/schemas
```

Also available: `--resume-from {claim_locked,research_ready,strategy_extracted,
planned,run_complete}` for crash recovery; incremental subcommands `claim-lock`,
`research-materials`, `extract-strategy`, `approve-strategy --approver`, `plan`,
`run --mode`; preflight `provider-doctor --config` / `provider-smoke --config
--role all`. Exit codes: `0` verifier-accepted terminal run, `2` intentional
mathematical stop (needs_human), `1` schema/invariant/runtime failure.

Output: a durable run directory (verified empirically against a fake-live run):
`input/ plans/ tasks/ proofs/ objects/ verification/ reviews/ staged_outputs/
best_verified/ failed_outputs/ repairs/ logs/ claim_lock/ research_materials/
strategies/` plus `state.yaml`, `run_summary.yaml`, `final_proof.md`,
`global_verification.yaml`, `constructed_objects.yaml`, `events.jsonl`,
`token_usage.jsonl`. Fields the adapter consumes:

- `run_summary.yaml`: `run_id`, `problem_id`, `proof_status`
  (`proved|partial|failed|needs_human`), `soundness_level`
  (`artifact_valid|llm_reviewed|computably_refuted`), `nodes.{id}.{status,attempts}`,
  `raw_stage`, `totals.{total_tokens,cost_usd}`.
- `global_verification.yaml` + `verification/*.yaml`: `verdict`
  (`pass|fail|uncertain`), `issues[].{severity,type,message}` (severity
  `info|minor|major|critical`), `checks` (includes quorum counts).
- `claim_lock/claim_lock.yaml`: `locked_statement`, `hypotheses`,
  `ambiguities[].{severity,issue,resolution_status}`, `strategy_origin`.
- `plans/proof_route.yaml` (`route: direct_proof|planner`),
  `plans/proposition_plan.yaml` (`propositions[].{id,statement,depends_on}`).
- `strategies/selected_strategy.yaml` (`strategy_id`, `human_strategy`,
  `material_gaps`), `research_materials/best_materials/promotion.yaml`
  (`unresolved_gaps`, `material_status: candidate`, `trusted_for_proof: false`).
- `final_proof.md` (assembled candidate proof text) and `best_verified/*.md`
  (per-proposition accepted lemma texts).

## 3. Mapping tables

### 3.1 Core outcome -> `AGENT_OUTPUT_SCHEMA` (prover round)

Implemented in `translate_prover_round`. Language rule: NEVER emit core's own
status vocabulary — "proved"/"verified" are on the kernel's `FORBIDDEN_LANGUAGE`
list and core "proved" is only an LLM quorum. All free text additionally passes
through the kernel's own `safe_text` scrubber.

| core artifact / field | AGENT_OUTPUT field | mapping |
| --- | --- | --- |
| `run_summary.proof_status` | `verdict` | `proved -> "plausible_direction"` (**NEVER stronger**); `partial -> "needs_revision"`; `failed -> "needs_revision"`; `needs_human -> "blocked"` |
| `run_summary.proof_status` | `recommended_human_action` | `proved -> "continue"`, `partial/failed -> "revise"`, `needs_human -> "stop"` |
| `proof_status` + `soundness_level` + global verdict | `summary` | candidate wording: proved -> "quorum-accepted candidate (LLM reviewer quorum only, unchecked here)", etc. |
| `run_summary.nodes` (proof obligations), `proposition_plan.propositions[].statement`, `final_proof.md` excerpt, `proof_route.route` | `steps` | one candidate step per obligation/lemma result + route provenance + final-argument excerpt |
| `global_verification`/`verification/*.yaml` `issues[]` with severity `major|critical`; verdict `fail|uncertain` | `risks` | one risk per failed verification, PLUS an always-present standing risk that core acceptance is an LLM quorum, not machine checking |
| `promotion.unresolved_gaps[]`, `selected_strategy.material_gaps[]` | `source_requests` | core's missing citations / research gaps become source requests |
| `claim_lock.ambiguities[]` with `resolution_status != resolved` | `missing_hypotheses` | unresolved intake ambiguities |

The kernel then re-contains the reply regardless: `make_prover_attempt`
unconditionally pins `"status": "needs_human"`, re-scrubs text, and
`validate_run` re-validates the whole run (verified end-to-end in this repo).

### 3.2 Core `final_proof.md` + lemma structure -> `LINE_NUMBERED_PROOF_OUTPUT_SCHEMA`

Implemented in `translate_line_numbered_proof` (proof-writer role):

| core artifact | proof line(s) |
| --- | --- |
| `claim_lock.hypotheses` / `locked_statement` | `L1`: instantiate the locked hypotheses (no dependencies) |
| `best_verified/PROPn.md` + `proposition_plan.propositions[PROPn].statement` | one line per accepted lemma: statement from the proposition plan, justification = "imported candidate lemma ... accepted only by an LLM reviewer quorum", dependencies = `prior_line` + an explicit `unchecked_gap` edge `core_quorum_PROPn` with `status: missing` |
| `final_proof.md` paragraphs | one line per paragraph, chained `depends_on` to the previous line, justification marked imported/unchecked |
| `proof_route.route` | `proof_kind = "espresso_core_<route>"` |
| — | final line carries a `gaps` entry stating no machine checking happened and the consensus seal must re-review every line |

Every dependency edge sets `source_grounded: false`, `machine_verified: false`,
`authority: ""` — an imported core proof can never smuggle grounded/verified
provenance. The output validates against the kernel's own `LineNumberedProof`
model (status pinned `advisory`, well-founded `depends_on`), which is what
`write_line_numbered_proof` enforces transactionally before commit.

### 3.3 Role routing

| detected role (top-level `role` in prompt, system-prompt backstop) | backend |
| --- | --- |
| `prover`, `prover_loop_round` | espresso-core (mock/live), table 3.1 |
| `line_numbered_proof_writer` | espresso-core (mock/live), table 3.2 |
| everything else — front-half agents, the three loop reviewers, consensus reviewers, gap resolver, diagnostics | passthrough to `ESPRESSO_CORE_FALLBACK_AGENT_COMMAND`, stdin/stdout unchanged |

Reviewers and the consensus seal are deliberately NOT core-backed: the seal must
stay independent of the prover backend it is auditing. `ESPRESSO_CORE_MODE=off`
(default) delegates even prover roles — the integration is strictly opt-in.

## 4. Trust rules (kernel stays the gate)

Verbatim from the codebase (these are the backstops the adapter relies on; the
adapter's candidate-language scrubbing is defense-in-depth on top, not instead):

- `prover_loop_driver.py`: "The kernel stays the trusted gate: every round is
  re-validated, nothing upgrades proof / source / evidence status (the
  loop/round artifacts are StrictBaseModels with no status fields, and
  `make_prover_attempt` pins `needs_human`), and the loop fails closed."
- `validators.py`: "`lean_checked` / `human_confirmed` are NOT earnable in V0:
  Espresso has no Lean/global-proof lane and no human-proof-attestation artifact
  (and human feedback may never upgrade proof status). Fail closed."
- `validators.py`: "idea_grade.proof_language_status=agent_consensus_mock_checked
  requires a unanimous agent_consensus_review (no_fatal_flaw_found from all three
  reviewers); reviewer prose cannot upgrade proof language".
- `validators.py`: "agent_consensus_review requires a line_numbered_proof.yaml;
  a route / proposed_steps / advisory_proof_sketch is not a sealable proof".
- `validators.py`: a PASS "must cite a real line_id"; consensus hashes are
  review-time bound — "the consensus is stale and must be re-sealed" on any edit.
- `source_checked_argument` requires an actually-earned `source_backed`
  source-grounding step; `computation_checked` requires trusted+confirmed
  machine results — an external prover cannot self-grant either.

Mechanics that make the above hold for THIS adapter (all verified):

- prover output has NO status channel: `make_prover_attempt` (`agents/agent_pass.py:616`)
  pins `"status": "needs_human"`; the raw JSON has no status field to fill.
- `LineNumberedProof.status` is pinned `Literal["advisory"]`; `proof_writer.py`
  writes transactionally and rolls back on `validate_run` failure.
- prose smuggling is blocked twice: adapter-side (`safe_text` /
  `_scrub_sketch_text`, `collect_unsafe_language` covers `FORBIDDEN_LANGUAGE` +
  `RESERVED_STATUS_LANGUAGE` incl. `source_backed`, `proof_status`,
  `agent_consensus_mock_checked`) and kernel-side
  (`_validate_prover_round_language`, `_prover_status_smuggling_field`).
- provider stays `"external"`: `build_client` and the
  `LLMAgentAttempt.provider` / `LLMAgentRunReport.provider`
  `Literal["openai","stub","external"]` are untouched. Adding a named
  "espresso_core" provider would be a kernel model edit = trust boundary = full
  gate (see §6).

### Honest red flags in espresso-core (do not design around them silently)

- **"proved" = LLM quorum.** No formal backend; `soundness_level` caps at
  `llm_reviewed`; the only deterministic math check is a narrow
  elementary-integer/rational counterexample checker (`computably_refuted`).
- **Squashed history**: one commit, v0.1.0 — no evolution to audit; treat as a
  black box behind the CLI (`run_verifier.py` 4656 lines, `supervisor.py` 3952).
- **Source-checkout-bound runtime**: `prompts/` and `schemas/` are not packaged
  (prompt root via `__file__.parents[3]`); a plain wheel install breaks live
  mode. `ESPRESSO_CORE_ROOT` must point at an editable/source checkout.
- **Docker image is mock-only** and omits `prompts/`; no service mode exists.
- **Checked-in "claude"/"gemini" providers are the FAKE provider**
  (`config.live.example.yaml` aliases all of openai/codex/claude/gemini to
  `fake_live_provider`). Only the codex CLI and OpenAI Responses wrappers are
  real; a Claude wrapper for core's `CommandProvider` must be authored host-side
  in the host config (`providers.claude: {type: command, command: [...],
  prompt_mode: stdin, response_format: text}`).
- **Token/cost ledger is soft** for text-format providers (zeros/estimates).
- **Strategy Scorer is vaporware**: `execution.strategy_score` is a hand-set
  constant (7.0) that just selects proof-search depth.

## 5. Phased roadmap

**Phase A — this PR.** Contract doc + adapter skeleton + mock tests. Opt-in via
`ESPRESSO_CORE_MODE` (default `off` = passthrough). `mock` translates a fixture
core run dir (`ESPRESSO_CORE_FIXTURE_RUN`) through the §3 tables; `live` code
path exists but constructs-and-guards the exact §2.2 commands and is never
exercised by tests. No network, no core execution, no new deps, no kernel /
schemas / server / orchestration edits. **Gate tier: normal**
(quick + focused + hard-negative + replay), per the three-tier policy — the
change is doc + one new adapter module (`espresso_proof/agents/espresso_core_adapter.py`
plus its `espresso-core-agent` console-script entry in pyproject) + one new
test file on the external side of the existing external-executor trust boundary.

**Phase B — live subprocess runs.** Enable `ESPRESSO_CORE_MODE=live`: adapter
writes the problem bundle, runs `solve` + `verify-run` in core's own venv
(dependency isolation; core needs only jsonschema+pyyaml, Python >=3.11), then
translates the run dir. Cost/timeout policy: core runs are LONG (per-call
provider timeouts ~900-960s, quorum >= 2 calls, minutes-to-hours per solve), so
one core solve == one prover ROUND, budgeted by the existing effort ladder
(`_max_rounds_for_effort`: quick_test=1, normal_pass=2, deep_exploration=4,
long_watch=8; agent-call budgets 4/8/20/50). Required wiring:
`ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS` > `ESPRESSO_CORE_TIMEOUT_SECONDS`;
tail `events.jsonl` for progress; parse `token_usage.jsonl` totals into a risk
note (cost may be zeros — red flag above); `--resume-from` for crash recovery;
surface exit-2 needs_human stops to the human gate (`approve-strategy` decisions
belong to the human, never the adapter). Gate tier: normal while the change
stays in the adapter module + tests; if any server/orchestration wiring is
added (server endpoints are a trust boundary), the full gate applies — run on
GitHub when the PR merges into main, not locally.

**Phase C — run-dir import as advisory sidecar + richer proof-writer mapping.**
Import the core run directory (or a digest-pinned subset: `run_summary.yaml`,
`final_proof.md`, `global_verification.yaml`, `state.yaml` provenance chain) as
a snapshot-excluded advisory sidecar artifact (the `advisory_proof_sketch` /
`repair_target` precedent: never validated as trusted state, can never upgrade
status), so humans can browse the full core evidence from the workbench. Deepen
table 3.2: map core's proposition DAG (`proposition_plan.depends_on`) into
structured `depends_on` graphs instead of a linear chain, and map core
`verification` issues onto per-line `gaps`. Gate tier: **full gate** (on
GitHub at merge time, not locally) — this phase touches
manifest/optional_artifacts handling and possibly `kernel/validators.py`
sidecar rules (trust boundary), and any UI surface.

**Explicitly out of scope at every phase**: mapping any core status to
`proof_language_status`, `source_backed`, `computation_checked`, or
`agent_consensus_mock_checked`. The consensus seal (3 independent reviewers,
PASS-must-cite-line_id, hash-bound) remains the only upgrade path, and core
"proved" arrives there as nothing more than a well-organized candidate.

## 6. Operational summary

```
# opt-in, mock (CI-safe):
export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-core-agent"
#   (or the absolute path to espresso_proof/agents/espresso_core_adapter.py)
export ESPRESSO_CORE_MODE=mock
export ESPRESSO_CORE_FIXTURE_RUN=/abs/fixtures/core-run
export ESPRESSO_CORE_FALLBACK_AGENT_COMMAND="/abs/path/to/normal/adapter"

# live (Phase B):
export ESPRESSO_CORE_MODE=live
export ESPRESSO_CORE_ROOT=/abs/espresso-core            # source checkout (schemas/prompts)
export ESPRESSO_CORE_CLI=$ESPRESSO_CORE_ROOT/.venv/bin/espresso-core
export ESPRESSO_CORE_CONFIG=/abs/host_config.yaml       # host-authored providers
export ESPRESSO_CORE_RUNS_DIR=/abs/runs
export ESPRESSO_CORE_TIMEOUT_SECONDS=3600
export ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS=4000     # must exceed core timeout
```

Gate-tier rule of thumb (three-tier policy): staying on `provider="external"`
with a new adapter script = normal tier. Touching `kernel/models.py` provider
`Literal`s, status enums, validators, seal logic, server security, or CI = trust
boundary = full gate. The full gate runs only on GitHub, on code-touching PRs
into main and on the merge to main (`.github/workflows/full-ci.yml` is the
authority on exactly when); local iteration stays on
quick/scoped checks and never blocks on the full suite.
