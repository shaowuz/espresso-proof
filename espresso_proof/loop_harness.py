"""Goal 42A: the dogfood loop runner.

``run_loop_cycle`` runs Espresso on a small set of dogfood cases through the
EXISTING runtime (``orchestrate_run`` + ``resume_run`` — never a second
orchestrator), then derives one durable, auditable cycle folder plus
campaign-level memory files, exactly in the Goal 42 layout.

The runner is strictly observational:

- it never changes product code, never opens or merges PRs;
- it writes only under the caller's ``--out`` directory (plus the Espresso run
  directories it creates under ``<out>/runs/``);
- nothing it writes is a kernel artifact: loop records carry no
  proof/source/evidence/grade/refutation/claim status and can never upgrade
  one (Goal 42 Hard Rules);
- evaluator-only ground truth (``known_truth_status``) never reaches an
  Espresso agent prompt or run input — the run input is the claim text alone.

If an agent/stage was not run, the corresponding record is absent or marked
``not_run``; the runner never fabricates empty successful artifacts.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_text, write_yaml
from .loop_models import (
    AgentEvent,
    BlockerRecord,
    BlockerReport,
    BottleneckEntry,
    BottleneckReport,
    CampaignCycleEntry,
    CampaignIndex,
    CaseTimings,
    CycleCases,
    CycleTimings,
    DogfoodCase,
    FailureReport,
    GapLedger,
    GapLedgerEntry,
    GateState,
    LoopMemory,
    LoopMemoryItem,
    LoopRunManifest,
    LoopRunRecord,
    RefutationRecord,
    RefutationReport,
    StopGate,
    StrategyRouteTrace,
    StrategyTrace,
    StrategyTraceReport,
    SubgoalProgress,
    SubgoalProgressReport,
    SubgoalSnapshot,
    TechnicalFailure,
    TheoremUseEntry,
    TheoremUseLedger,
)

# ---------------------------------------------------------------------------
# Default dogfood cases: one per required Goal 42 case family.
# known_truth_status is EVALUATOR-ONLY and never enters a run input.
# ---------------------------------------------------------------------------

DEFAULT_CASES: list[dict] = [
    {
        "case_id": "p2_groups_true",
        "claim": "Every group of order p^2, where p is prime, is abelian.",
        "case_family": "true_theorem",
        "expected_difficulty": "smoke",
        "known_truth_status": "evaluator_only_true",
        "what_this_case_tests": ["proof-search progress", "source grounding"],
        "must_not_claim": ["Espresso proved the theorem"],
    },
    {
        "case_id": "order6_abelian_false",
        "claim": "Every group of order 6 is abelian.",
        "case_family": "false_theorem",
        "expected_difficulty": "smoke",
        "known_truth_status": "evaluator_only_false",
        "what_this_case_tests": ["counterexample reasoning", "refutation gate"],
        "must_not_claim": ["Espresso proved the theorem", "refuted without a checked counterexample"],
    },
    {
        "case_id": "continuous_max_missing_hypothesis",
        "claim": "Every continuous function f: R -> R attains a maximum value.",
        "case_family": "missing_hypothesis",
        "expected_difficulty": "normal",
        "known_truth_status": "evaluator_only_false",
        "what_this_case_tests": ["missing-hypothesis detection", "dropped-hypothesis non-refutation"],
        "must_not_claim": ["a dropped-hypothesis counterexample refutes the claim"],
    },
    {
        "case_id": "source_sensitive_very_general",
        "claim": (
            "Every cubic fourfold has a nontrivial Hodge atom obstruction, so all "
            "cubic fourfolds are irrational."
        ),
        "case_family": "source_sensitive",
        "expected_difficulty": "normal",
        # The universal conclusion is classically FALSE (Pfaffian cubic
        # fourfolds are rational); the case tests very-general-vs-all source
        # discipline, and the evaluator must be able to score a miss.
        "known_truth_status": "evaluator_only_false",
        "what_this_case_tests": ["source applicability", "very-general vs all discipline"],
        "must_not_claim": ["a very-general source supports an all-objects claim"],
    },
    {
        "case_id": "matrix_rank_scoped",
        "claim": "The 2x2 matrix [[1, 2], [2, 4]] has rank 1.",
        "case_family": "computation_scoped",
        "expected_difficulty": "smoke",
        "known_truth_status": "evaluator_only_true",
        "what_this_case_tests": ["scoped computation evidence", "scope display discipline"],
        "must_not_claim": ["a finite computation proves a universal claim"],
    },
    {
        "case_id": "hodge_hard_sketch",
        "claim": (
            "A Hodge-theoretic atom decomposition argument shows very general cubic "
            "fourfolds are irrational."
        ),
        "case_family": "hard_research_sketch",
        "expected_difficulty": "hard",
        "known_truth_status": "evaluator_only_unknown",
        "what_this_case_tests": ["proof-sketch critique", "honest human_needed outcome"],
        "must_not_claim": ["the sketch is a verified proof"],
    },
]

# Stages the loop reports as "agents" (front-half + loop). Kept in sync with
# the event names StageRunner/orchestrator emit on the run's events.jsonl.
_FIRST_FEEDBACK_STAGE = "scaffold"
_FIRST_ROUTE_STAGES = ("front_half", "llm_prover_reviewers")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(p.strip().lower() for p in parts).encode()).hexdigest()[:16]


def load_cases(cases_path: Path | None, *, limit: int | None = None) -> list[DogfoodCase]:
    """Load dogfood cases from a YAML file, or the built-in default set."""

    if cases_path is None:
        raw = DEFAULT_CASES
    else:
        data = read_yaml(Path(cases_path))
        raw = data["cases"] if isinstance(data, dict) else data
    cases = [DogfoodCase.model_validate(item) for item in raw]
    if limit is not None:
        cases = cases[:limit]
    return cases


def _next_cycle_id(out_root: Path) -> str:
    existing = sorted(p.name for p in out_root.glob("cycle_*") if p.is_dir())
    return f"cycle_{len(existing) + 1:03d}"


# ---------------------------------------------------------------------------
# Per-case execution + collection
# ---------------------------------------------------------------------------


def _run_case(
    case: DogfoodCase,
    *,
    runs_root: Path,
    mode: str,
    provider: str,
    front_half: str,
) -> dict:
    """Run one case through the real runtime. Returns a record dict with the
    run dir, timing, validation outcome, and any technical failure."""

    from .orchestrator import orchestrate_run, resume_run
    from .validators import validate_run

    case_root = runs_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    # The run input is the CLAIM TEXT ONLY: evaluator-only fields (notably
    # known_truth_status) must never reach an Espresso prompt.
    input_path = case_root / "input.md"
    write_text(input_path, case.claim + "\n")

    started_at = _now_iso()
    t0 = time.monotonic()
    record: dict = {
        "case": case,
        "run_dir": None,
        "selected_route": None,
        "started_at": started_at,
        "failure": None,
        "validation_result": "not_run",
        "validation_error": "",
        "final_state": "",
    }
    try:
        result = orchestrate_run(
            project=case.case_id,
            input_path=input_path,
            output_root=case_root,
            provider=provider,
            front_half=front_half,
        )
        run_dir = Path(result["run_dir"])
        record["run_dir"] = run_dir
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
        selected = brief["top_strategies"][0]["id"] if brief.get("top_strategies") else "none"
        record["selected_route"] = selected
        resume_run(
            run_dir=run_dir,
            selected_strategy=selected,
            effort="normal_pass",
            prover_provider=provider,
        )
    except Exception as exc:  # noqa: BLE001 - one case failing must not kill the cycle
        record["failure"] = f"{type(exc).__name__}: {exc}"
    record["duration_s"] = round(time.monotonic() - t0, 3)
    record["finished_at"] = _now_iso()

    run_dir = record["run_dir"]
    if run_dir is not None:
        try:
            validate_run(run_dir)
            record["validation_result"] = "valid"
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            record["validation_result"] = "invalid"
            record["validation_error"] = str(exc)[:400]
        try:
            record["final_state"] = read_yaml(run_dir / "run_manifest.yaml").get(
                "current_state", ""
            )
        except Exception:  # noqa: BLE001
            record["final_state"] = "unreadable"
    return record


def _read_optional(run_dir: Path, name: str):
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    except Exception:  # noqa: BLE001
        return None
    rel = manifest.get("artifacts", {}).get(name) or manifest.get(
        "optional_artifacts", {}
    ).get(name)
    if not rel or not (run_dir / rel).exists():
        return None
    try:
        return read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001
        return None


def _collect_agent_events(record: dict, cycle_id: str) -> list[AgentEvent]:
    """Translate the run's diagnostic events.jsonl into Goal-42 agent events,
    pairing stage_started/stage_ok|validation_failed into durations."""

    run_dir = record["run_dir"]
    case_id = record["case"].case_id
    if run_dir is None:
        return [
            AgentEvent(
                timestamp=record["finished_at"],
                cycle_id=cycle_id,
                case_id=case_id,
                run_id="not_run",
                agent_name="orchestrator",
                stage="orchestrate",
                event="failed",
                error_summary=(record["failure"] or "run did not start")[:300],
            )
        ]
    from .run_events import read_events

    run_id = run_dir.name
    events = read_events(run_dir)
    out: list[AgentEvent] = []
    started: dict[str, str] = {}
    for entry in events:
        stage = str(entry.get("stage", ""))
        kind = entry.get("kind")
        ts = str(entry.get("ts", ""))
        if kind == "stage_started":
            started[stage] = ts
            out.append(
                AgentEvent(
                    timestamp=ts, cycle_id=cycle_id, case_id=case_id, run_id=run_id,
                    agent_name=stage, stage=stage, event="started",
                )
            )
        elif kind in {"stage_ok", "validation_failed"}:
            duration_ms = None
            begin = started.get(stage)
            if begin:
                try:
                    duration_ms = int(
                        (
                            datetime.fromisoformat(ts) - datetime.fromisoformat(begin)
                        ).total_seconds()
                        * 1000
                    )
                except ValueError:
                    duration_ms = None
            out.append(
                AgentEvent(
                    timestamp=ts, cycle_id=cycle_id, case_id=case_id, run_id=run_id,
                    agent_name=stage, stage=stage,
                    event="completed" if kind == "stage_ok" else "failed",
                    duration_ms=duration_ms,
                    error_summary=(
                        str(entry.get("detail", ""))[:300]
                        if kind == "validation_failed"
                        else None
                    ),
                )
            )
    if record["failure"]:
        out.append(
            AgentEvent(
                timestamp=record["finished_at"], cycle_id=cycle_id, case_id=case_id,
                run_id=run_id, agent_name="loop_harness", stage="case_run",
                event="failed", error_summary=str(record["failure"])[:300],
            )
        )
    return out


def _collect_agent_outputs(record: dict, outputs_dir: Path) -> None:
    """Bounded JSON summaries of each agent's output, referencing the original
    artifacts rather than duplicating them. Absent agents are marked not_run."""

    outputs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = record["run_dir"]

    def dump(name: str, payload: dict) -> None:
        (outputs_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str)[:20000],
            encoding="utf-8",
        )

    if run_dir is None:
        dump("claim_understanding", {"status": "not_run", "reason": record["failure"]})
        return

    claim = _read_optional(run_dir, "locked_claim")
    dump(
        "claim_understanding",
        {
            "status": "ran" if claim else "not_run",
            "artifact": str(run_dir / "locked_claim.yaml"),
            "normalized_statement": (claim or {}).get("normalized_statement", ""),
            "hypotheses": [h.get("name") for h in (claim or {}).get("hypotheses", [])],
            "conclusion": (claim or {}).get("conclusion", ""),
        },
    )
    literature = _read_optional(run_dir, "literature_review")
    dump(
        "literature_review",
        {
            "status": "ran" if literature else "not_run",
            "candidate_count": len((literature or {}).get("candidate_references", [])),
            "artifact": str(run_dir / "literature_review.yaml"),
        },
    )
    examples = _read_optional(run_dir, "examples")
    dump(
        "examples",
        {
            "status": "ran" if examples else "not_run",
            "example_count": len((examples or {}).get("examples", [])),
            "artifact": str(run_dir / "examples.yaml"),
        },
    )
    counterexamples = _read_optional(run_dir, "counterexamples")
    dump(
        "counterexamples",
        {
            "status": "ran" if counterexamples else "not_run",
            "test_count": len((counterexamples or {}).get("tests", [])),
            "artifact": str(run_dir / "counterexamples.yaml"),
        },
    )
    brief = _read_optional(run_dir, "strategy_brief")
    dump(
        "strategy_generator",
        {
            "status": "ran" if brief else "not_run",
            "routes": [
                {"id": s.get("id"), "score": s.get("score")}
                for s in (brief or {}).get("top_strategies", [])
            ],
            "artifact": str(run_dir / "strategy_brief.yaml"),
        },
    )
    loop = _read_optional(run_dir, "prover_loop")
    attempts = (loop or {}).get("attempts", [])
    rounds = sorted({a.get("round_index") for a in attempts if a.get("round_index", 0) >= 2})
    for index in rounds:
        round_attempts = [a for a in attempts if a.get("round_index") == index]
        dump(
            f"prover_round_{index:03d}",
            {
                "status": "ran",
                "attempts": [
                    {
                        "attempt_id": a.get("attempt_id"),
                        "status": a.get("status"),
                        "blockers": a.get("blockers", []),
                        "target_subgoal_id": a.get("target_subgoal_id"),
                    }
                    for a in round_attempts
                ],
                "artifact": str(run_dir / "prover_loop.yaml"),
            },
        )
    if not rounds:
        dump("prover_round_001", {"status": "not_run", "reason": "no appended prover round"})
    agent_run = _read_optional(run_dir, "llm_agent_run")
    reviews = (agent_run or {}).get("verifier_reviews", [])
    if reviews:
        dump(
            "reviewer_math",
            {
                "status": "ran",
                "reviews": [
                    {"verifier_id": r.get("verifier_id"), "verdict": r.get("verdict")}
                    for r in reviews
                ],
                "artifact": str(run_dir / "llm_agent_run.yaml"),
            },
        )
    else:
        dump("reviewer_math", {"status": "not_run", "reason": "no llm_agent_run reviews"})
    grounding = _read_optional(run_dir, "source_grounding")
    dump(
        "reviewer_source",
        {
            "status": "ran" if grounding else "not_run",
            "source_backed_steps": sum(
                1 for s in (grounding or {}).get("steps", []) if s.get("status") == "source_backed"
            ),
            "artifact": str(run_dir / "source_grounding.yaml"),
        },
    )


def _case_timings(record: dict, events: list[AgentEvent]) -> CaseTimings:
    agent_ms: dict[str, int] = {}
    first_feedback = None
    first_route = None
    for event in events:
        if event.event == "completed" and event.duration_ms is not None:
            agent_ms[event.agent_name] = agent_ms.get(event.agent_name, 0) + event.duration_ms
            if event.stage == _FIRST_FEEDBACK_STAGE and first_feedback is None:
                first_feedback = event.duration_ms
            if event.stage in _FIRST_ROUTE_STAGES and first_route is None:
                first_route = sum(agent_ms.values())
    slowest = max(agent_ms, key=agent_ms.get) if agent_ms else None
    return CaseTimings(
        case_id=record["case"].case_id,
        time_to_first_feedback_ms=first_feedback,
        time_to_first_route_ms=first_route,
        total_case_duration_s=record["duration_s"],
        agent_timings_ms=agent_ms,
        slowest_agent=slowest,
        first_user_wait_bottleneck=("first_route" if first_route else None),
    )


def _collect_refutations(record: dict, cycle_id: str) -> list[RefutationRecord]:
    """Map kernel CounterexampleTest records (the REAL schema: operator_id /
    example_id / target / result / refutes_claim / refutes_step_only /
    exposes_missing_hypothesis / explanation) into loop refutation records.

    Conservative by construction, mirroring the kernel validator: a test is
    "checked" exactly when ``result == "failed"`` (the kernel precondition for
    refutes_claim); claim_refuted additionally requires satisfies_all is True,
    refutes_claim, AND a kernel-valid run. Dropped-hypothesis tests map to
    theorem_condition_gap; proof-step attacks are visibly separate."""

    run_dir = record["run_dir"]
    if run_dir is None:
        return []
    counterexamples = _read_optional(run_dir, "counterexamples") or {}
    claim = _read_optional(run_dir, "locked_claim") or {}
    grade = _read_optional(run_dir, "idea_grade") or {}
    run_is_valid = record["validation_result"] == "valid"
    records: list[RefutationRecord] = []
    for test in counterexamples.get("tests", []):
        refutes_claim = bool(test.get("refutes_claim"))
        satisfies = test.get("satisfies_all_claim_hypotheses")
        result = str(test.get("result", "not_tested"))
        checked = result == "failed"
        step_attack_only = bool(test.get("refutes_step_only")) and not refutes_claim
        if refutes_claim and satisfies is True and checked and run_is_valid:
            failure_class = "claim_refuted"
        elif step_attack_only:
            failure_class = "strategy_failed_by_example"
        elif bool(test.get("exposes_missing_hypothesis")) or satisfies is False:
            failure_class = "theorem_condition_gap"
        elif not checked:
            failure_class = "computation_unchecked"
        else:
            failure_class = "no_progress"
        records.append(
            RefutationRecord(
                case_id=record["case"].case_id,
                run_id=run_dir.name,
                claimed_by=str(test.get("operator_id", "counterexample_agent")),
                candidate_counterexample=(
                    f"{test.get('example_id', '')}: {test.get('explanation', '')}"
                )[:300],
                satisfies_all_locked_hypotheses=(
                    satisfies if isinstance(satisfies, bool) else None
                ),
                conclusion_fails=(True if checked and refutes_claim else None),
                counterexample_checked=checked,
                espresso_allowed_refuted=grade.get("status") == "refuted",
                locked_claim_id=str(claim.get("id", "")),
                hypothesis_ledger_path=str(run_dir / "hypothesis_ledger.yaml"),
                failure_class=failure_class,
                attacked_step_or_tactic=str(test.get("target", ""))[:200],
                failed_lemma=str(test.get("missing_hypothesis") or "")[:200],
                check_method=result,
                recurrence_fingerprint=_fingerprint(
                    record["case"].case_id, str(test.get("id", "")), failure_class
                ),
                repair_suggestion=(
                    "Machine-check the candidate against every locked hypothesis "
                    "before any refutation routing."
                ),
                next_action="needs check" if not checked else "review scope",
                success_criterion=(
                    "candidate satisfies all hypotheses, the claim fails on it "
                    "(result=failed), and the run stays kernel-valid"
                ),
                is_proof_step_attack_only=step_attack_only,
            )
        )
    return records


def _collect_blockers(record: dict, prior_fingerprints: set[str]) -> list[BlockerRecord]:
    run_dir = record["run_dir"]
    if run_dir is None:
        return []
    claim = _read_optional(run_dir, "locked_claim") or {}
    loop = _read_optional(run_dir, "prover_loop") or {}
    graph = _read_optional(run_dir, "proof_goal_graph") or {}
    nodes = {n.get("id"): n for n in graph.get("nodes", [])}
    blockers: list[BlockerRecord] = []
    seen: set[str] = set()
    for attempt in loop.get("attempts", []):
        for text in attempt.get("blockers", []):
            fingerprint = _fingerprint(record["case"].case_id, text)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            lowered = text.lower()
            if "source" in lowered or "citation" in lowered or "reference" in lowered:
                evidence_type = "source_gap"
            elif "definition" in lowered:
                evidence_type = "missing_definition"
            elif "comput" in lowered or "check" in lowered:
                evidence_type = "missing_computation"
            elif "human" in lowered:
                evidence_type = "human_judgment_needed"
            else:
                evidence_type = "failed_proof_step"
            blockers.append(
                BlockerRecord(
                    case_id=record["case"].case_id,
                    run_id=run_dir.name,
                    subgoal_id=attempt.get("target_subgoal_id"),
                    local_claim=str(
                        nodes.get(attempt.get("target_subgoal_id"), {}).get("statement", "")
                    )[:300],
                    blocker_text=str(text)[:400],
                    blocking_evidence_type=evidence_type,
                    recurrence="recurring" if fingerprint in prior_fingerprints else "new",
                    locked_claim_id=str(claim.get("id", "")),
                    hypothesis_ledger_path=str(run_dir / "hypothesis_ledger.yaml"),
                    affected_route_id=attempt.get("strategy_id"),
                    attempted_tactic=str(attempt.get("time_allocation", "")),
                    missing_item=str(text)[:200],
                    repair_suggestion="See triage; one bounded fix per cycle.",
                    next_action="triage",
                    success_criterion="blocker absent on replay of the same case",
                    recurrence_fingerprint=fingerprint,
                )
            )
    return blockers


def _collect_strategy_trace(record: dict, cycle_id: str) -> StrategyTrace | None:
    run_dir = record["run_dir"]
    if run_dir is None:
        return None
    brief = _read_optional(run_dir, "strategy_brief") or {}
    loop = _read_optional(run_dir, "prover_loop") or {}
    selected = record["selected_route"]
    attempts_by_route: dict[str, list[dict]] = {}
    for attempt in loop.get("attempts", []):
        attempts_by_route.setdefault(attempt.get("strategy_id", ""), []).append(attempt)
    routes = []
    for strategy in brief.get("top_strategies", []):
        route_id = strategy.get("id", "")
        attempts = attempts_by_route.get(route_id, [])
        outcome = (
            f"{len(attempts)} attempt(s); last status "
            f"{attempts[-1].get('status') if attempts else 'not attempted'}"
        )
        routes.append(
            StrategyRouteTrace(
                route_id=route_id,
                core_idea=str(strategy.get("summary", strategy.get("title", "")))[:200],
                score=strategy.get("score"),
                selected=route_id == selected,
                attempted_tactics=sorted(
                    {str(a.get("time_allocation", "")) for a in attempts if a}
                ),
                outcome=outcome,
            )
        )
    return StrategyTrace(
        cycle_id=cycle_id,
        case_id=record["case"].case_id,
        run_id=run_dir.name,
        routes=routes,
        selected_route_id=selected,
        ranking_evidence=[str(run_dir / "strategy_brief.yaml")],
    )


def _collect_subgoal_progress(record: dict) -> SubgoalProgress:
    run_dir = record["run_dir"]
    if run_dir is None:
        return SubgoalProgress(case_id=record["case"].case_id, run_id="not_run", graph_present=False)
    graph = _read_optional(run_dir, "proof_goal_graph")
    if not graph:
        return SubgoalProgress(
            case_id=record["case"].case_id, run_id=run_dir.name, graph_present=False
        )
    from .subgoal_graph import select_next_subgoal

    nodes = graph.get("nodes", [])
    next_node = select_next_subgoal(graph)
    return SubgoalProgress(
        case_id=record["case"].case_id,
        run_id=run_dir.name,
        graph_present=True,
        subgoals=[
            SubgoalSnapshot(
                subgoal_id=str(n.get("id", "")),
                local_claim=str(n.get("statement", ""))[:240],
                state=str(n.get("state", "")),
                parent=(n.get("depends_on") or [None])[0],
                blockers=[str(b)[:160] for b in n.get("reviewer_objections", [])][:4],
                scoped_checks=[str(c) for c in n.get("supported_by_scoped_checks", [])][:4],
            )
            for n in nodes[:24]
        ],
        next_target_subgoal_id=(next_node or {}).get("id"),
    )


def _collect_theorem_use(record: dict) -> list[TheoremUseEntry]:
    run_dir = record["run_dir"]
    if run_dir is None:
        return []
    grounding = _read_optional(run_dir, "source_grounding") or {}
    applicability = _read_optional(run_dir, "theorem_applicability") or {}
    usage_by_step = {u.get("proof_step_id"): u for u in applicability.get("usages", [])}
    refs = {r.get("id"): r for r in grounding.get("source_refs", [])}
    entries: list[TheoremUseEntry] = []
    for step in grounding.get("steps", []):
        ref = refs.get((step.get("source_refs") or [None])[0]) or {}
        usage = usage_by_step.get(step.get("proof_step_id")) or {}
        conditions = usage.get("conditions", [])
        status = step.get("status")
        entries.append(
            TheoremUseEntry(
                case_id=record["case"].case_id,
                run_id=run_dir.name,
                cited_source=str(ref.get("title", "")) or str(step.get("needed_theorem", ""))[:200],
                exact_locator=str((ref.get("locator") or {}).get("value", "")),
                statement_excerpt_hash=str(ref.get("statement_sha256", "")),
                required_hypotheses=[str(c.get("text", ""))[:160] for c in conditions][:8],
                matched_hypotheses=[
                    str(c.get("text", ""))[:160]
                    for c in conditions
                    if c.get("status") == "satisfied"
                ][:8],
                missing_or_unclear_hypotheses=[
                    str(c.get("text", ""))[:160]
                    for c in conditions
                    if c.get("status") in {"missing", "unclear", "mismatched", "not_checked"}
                ][:8],
                step_status=(
                    "reference_supported" if status == "source_backed" else
                    "candidate" if status == "candidate" else "needs_check"
                ),
            )
        )
    return entries


def _derive_bottlenecks(
    cycle_id: str,
    records: list[dict],
    timings: list[CaseTimings],
    failures: list[TechnicalFailure],
    blockers: list[BlockerRecord],
    theorem_entries: list[TheoremUseEntry],
) -> list[BottleneckEntry]:
    entries: list[BottleneckEntry] = []
    index = 1

    def add(case_id, run_id, stage, category, metric, impact, diagnostic):
        nonlocal index
        entries.append(
            BottleneckEntry(
                bottleneck_id=f"{cycle_id}_bn_{index:03d}",
                case_id=case_id,
                run_id=run_id,
                stage_or_agent=stage,
                category=category,
                metric_or_evidence_path=metric,
                user_visible_impact=impact,
                proposed_next_diagnostic=diagnostic,
            )
        )
        index += 1

    for failure in failures:
        add(
            failure.case_id, failure.run_id, failure.stage,
            "artifact_invalidity" if failure.kind in {"invalid_artifact", "validate_run_failure"} else "harness_gap",
            failure.evidence_path or failure.error_summary[:160],
            "the case run failed before producing a usable research state",
            "re-run the case alone and inspect the failing stage artifact",
        )
    for record in records:
        run_dir = record["run_dir"]
        if run_dir is None:
            continue
        run_id = run_dir.name
        case_id = record["case"].case_id
        loop = _read_optional(run_dir, "prover_loop") or {}
        attempts = loop.get("attempts", [])
        if attempts and all(a.get("status") in {"needs_repair", "blocked"} for a in attempts):
            add(
                case_id, run_id, "prover_loop", "proof_search_stall",
                str(run_dir / "prover_loop.yaml"),
                "every prover attempt ended blocked/needs_repair; no route progressed",
                "inspect recurring blockers; consider a capability fix for the top one",
            )
        case_theorems = [e for e in theorem_entries if e.case_id == case_id]
        if case_theorems and all(e.step_status != "reference_supported" for e in case_theorems):
            add(
                case_id, run_id, "source_grounding", "source_mismatch",
                str(run_dir / "source_grounding.yaml"),
                "no proof step earned reference support for this case",
                "compare missing hypotheses in the theorem-use ledger against the claim",
            )
        if any(e.missing_or_unclear_hypotheses for e in case_theorems):
            add(
                case_id, run_id, "theorem_applicability", "theorem_applicability_gap",
                str(run_dir / "theorem_applicability.yaml"),
                "cited theorem hypotheses did not match the claim hypotheses",
                "extend the typed hypothesis comparison for this mismatch family",
            )
    for timing in timings:
        if timing.slowest_agent and (timing.total_case_duration_s or 0) > 60:
            add(
                timing.case_id, "n/a", timing.slowest_agent, "latency",
                f"timings.yaml: {timing.slowest_agent}={timing.agent_timings_ms.get(timing.slowest_agent)}ms",
                "the user waits on this agent before seeing progress",
                "profile the slowest agent stage",
            )
    return entries


# ---------------------------------------------------------------------------
# Campaign-level updates
# ---------------------------------------------------------------------------


def _load_campaign_file(path: Path, model, default):
    if path.exists():
        return model.model_validate(read_yaml(path))
    return model.model_validate(default)


def _update_campaign_files(
    out_root: Path,
    cycle_id: str,
    records: list[dict],
    blockers: list[BlockerRecord],
    bottlenecks: list[BottleneckEntry],
    failures: list[TechnicalFailure],
    cycle_wall_hours: float = 0.0,
) -> None:
    family_by_case = {r["case"].case_id: r["case"].case_family for r in records}
    campaign_id = out_root.name or "campaign"
    index_path = out_root / "campaign_index.yaml"
    index = _load_campaign_file(
        index_path, CampaignIndex, {"campaign_id": campaign_id, "cycles": []}
    )
    index.cycles.append(
        CampaignCycleEntry(
            cycle_id=cycle_id,
            case_ids=[r["case"].case_id for r in records],
            run_ids=[r["run_dir"].name if r["run_dir"] else "not_run" for r in records],
            final_status="recorded",
        )
    )
    write_yaml(index_path, index.model_dump(mode="json"))

    ledger_path = out_root / "gap_ledger.yaml"
    ledger = _load_campaign_file(ledger_path, GapLedger, {"campaign_id": campaign_id, "gaps": []})
    by_fingerprint = {g.gap_id: g for g in ledger.gaps}
    for blocker in blockers:
        gap_id = f"gap_{blocker.recurrence_fingerprint}"
        family = family_by_case.get(blocker.case_id, blocker.case_id)
        if gap_id in by_fingerprint:
            entry = by_fingerprint[gap_id]
            entry.recurrence_count += 1
            if family not in entry.affected_case_families:
                entry.affected_case_families.append(family)
        else:
            entry = GapLedgerEntry(
                gap_id=gap_id,
                first_seen_cycle=cycle_id,
                linked_blocker_ids=[blocker.recurrence_fingerprint],
                affected_case_families=[family],
                bottleneck_category="proof_search_stall",
                before_metric=blocker.blocker_text[:160],
                selection_note="auto-derived from a recurring prover blocker",
            )
            ledger.gaps.append(entry)
            by_fingerprint[gap_id] = entry
    for bottleneck in bottlenecks:
        if bottleneck.category in {"source_mismatch", "theorem_applicability_gap"}:
            gap_id = f"gap_{_fingerprint(bottleneck.case_id, bottleneck.category)}"
            if gap_id in by_fingerprint:
                by_fingerprint[gap_id].recurrence_count += 1
            else:
                entry = GapLedgerEntry(
                    gap_id=gap_id,
                    first_seen_cycle=cycle_id,
                    linked_blocker_ids=[bottleneck.bottleneck_id],
                    affected_case_families=[family_by_case.get(bottleneck.case_id, bottleneck.case_id)],
                    bottleneck_category=bottleneck.category,
                    before_metric=bottleneck.metric_or_evidence_path[:160],
                    selection_note="auto-derived from a bottleneck entry",
                )
                ledger.gaps.append(entry)
                by_fingerprint[gap_id] = entry
    write_yaml(ledger_path, ledger.model_dump(mode="json"))

    memory_path = out_root / "loop_memory.yaml"
    memory = _load_campaign_file(
        memory_path, LoopMemory, {"campaign_id": campaign_id, "items": []}
    )
    for failure in failures[:4]:
        memory.items.append(
            LoopMemoryItem(
                cycle_id=cycle_id,
                what_failed=failure.error_summary[:200],
                where_it_failed=f"{failure.case_id}/{failure.stage}",
                repair_attempted="none yet (recorded for triage)",
                try_or_avoid_next="reproduce the failing stage in isolation before fixing",
                scope_limits="this case family only until rechecked",
                forbidden_generalizations=["do not assume other case families share this failure"],
            )
        )
    for blocker in [b for b in blockers if b.recurrence == "recurring"][:4]:
        memory.items.append(
            LoopMemoryItem(
                cycle_id=cycle_id,
                what_failed=f"recurring blocker: {blocker.blocker_text[:160]}",
                where_it_failed=f"{blocker.case_id}/prover_loop",
                repair_attempted="none yet",
                try_or_avoid_next="prioritize this blocker in triage; it has recurred",
                scope_limits="advisory; must be revalidated on the next run",
                forbidden_generalizations=["recurrence is not evidence the claim is false"],
            )
        )
    write_yaml(memory_path, memory.model_dump(mode="json"))

    gate_path = out_root / "stop_gate.yaml"
    if gate_path.exists():
        gate = StopGate.model_validate(read_yaml(gate_path))
    else:
        def pending() -> GateState:
            return GateState(passed=False, note="not yet evaluated")

        gate = StopGate(
            status="RUNNING",
            safety_gate=pending(), capability_gate=pending(), ux_gate=pending(),
            dogfood_coverage_gate=pending(), replay_gate=pending(), holdout_gate=pending(),
            scorecard_gate=pending(), calibration_gate=pending(),
        )
    gate.cycles_completed = len(index.cycles)
    gate.wall_time_hours_used = round(gate.wall_time_hours_used + cycle_wall_hours, 4)
    gate.decision_evidence.append(f"{out_root.name}/{cycle_id}/cycle_summary.md")
    write_yaml(gate_path, gate.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_loop_cycle(
    *,
    out_root: Path,
    cases_path: Path | None = None,
    mode: str = "smoke",
    cycle_id: str | None = None,
    max_cases: int | None = 5,
) -> Path:
    """Run one observational dogfood cycle. Returns the cycle directory.

    ``mode='smoke'`` runs the deterministic stub provider with the mock front
    half (no network, no model). ``mode='deep'`` uses the external provider
    configured via ``ESPRESSO_EXTERNAL_AGENT_COMMAND``. The runner only reads
    product behavior; it never modifies code and never opens PRs.
    """

    if mode not in {"smoke", "deep"}:
        raise ValueError("mode must be 'smoke' or 'deep'")
    provider = "stub" if mode == "smoke" else "external"
    front_half = "mock" if mode == "smoke" else "external"

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    cycle_id = cycle_id or _next_cycle_id(out_root)
    cycle_dir = out_root / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(cases_path, limit=max_cases)
    if not 1 <= len(cases) <= 6:
        raise ValueError("a cycle needs between 1 and 6 dogfood cases")
    write_yaml(
        cycle_dir / "cases.yaml",
        CycleCases(cycle_id=cycle_id, cases=cases).model_dump(mode="json"),
    )

    cycle_t0 = time.monotonic()
    prior_fingerprints: set[str] = set()
    ledger_path = out_root / "gap_ledger.yaml"
    if ledger_path.exists():
        prior = GapLedger.model_validate(read_yaml(ledger_path))
        for gap in prior.gaps:
            prior_fingerprints.update(gap.linked_blocker_ids)

    records: list[dict] = []
    for case in cases:
        records.append(
            _run_case(
                case,
                runs_root=cycle_dir / "runs",
                mode=mode,
                provider=provider,
                front_half=front_half,
            )
        )

    # --- run_manifest.yaml -------------------------------------------------
    write_yaml(
        cycle_dir / "run_manifest.yaml",
        LoopRunManifest(
            cycle_id=cycle_id,
            runs=[
                LoopRunRecord(
                    case_id=r["case"].case_id,
                    command=(
                        f"orchestrate(front_half={front_half}, provider={provider}) "
                        "+ resume(effort=normal_pass)"
                    ),
                    run_dir=str(r["run_dir"]) if r["run_dir"] else "not_run",
                    provider=provider,
                    mode=mode,
                    selected_route=r["selected_route"],
                    started_at=r["started_at"],
                    finished_at=r["finished_at"],
                    validation_result=r["validation_result"],
                    validation_error=r["validation_error"],
                    final_visible_state=r["final_state"],
                )
                for r in records
            ],
        ).model_dump(mode="json"),
    )

    # --- agent_events.jsonl + agent_outputs/ + timings ---------------------
    all_events: list[AgentEvent] = []
    timings: list[CaseTimings] = []
    for record in records:
        events = _collect_agent_events(record, cycle_id)
        all_events.extend(events)
        case_dir = cycle_dir
        _collect_agent_outputs(record, case_dir / "agent_outputs_root" / record["case"].case_id)
        timings.append(_case_timings(record, events))
    # The goal layout puts agent_outputs/ at the cycle level; keep a flat copy
    # for the FIRST case (primary) and per-case folders under agent_outputs_root.
    primary_outputs = cycle_dir / "agent_outputs_root" / records[0]["case"].case_id
    flat = cycle_dir / "agent_outputs"
    flat.mkdir(exist_ok=True)
    for item in primary_outputs.glob("*.json"):
        (flat / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
    with (cycle_dir / "agent_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in all_events:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
    write_yaml(
        cycle_dir / "timings.yaml",
        CycleTimings(
            cycle_id=cycle_id,
            total_cycle_duration_s=round(time.monotonic() - cycle_t0, 3),
            cases=timings,
        ).model_dump(mode="json"),
    )

    # --- failures / refutations / blockers ---------------------------------
    failures: list[TechnicalFailure] = []
    for i, record in enumerate(records, start=1):
        if record["failure"]:
            failures.append(
                TechnicalFailure(
                    failure_id=f"{cycle_id}_fail_{i:03d}",
                    case_id=record["case"].case_id,
                    run_id=record["run_dir"].name if record["run_dir"] else "not_run",
                    stage="case_run",
                    kind="provider_failure" if "provider" in str(record["failure"]).lower() else "harness_error",
                    error_summary=str(record["failure"])[:400],
                )
            )
        if record["validation_result"] == "invalid":
            failures.append(
                TechnicalFailure(
                    failure_id=f"{cycle_id}_fail_v{i:03d}",
                    case_id=record["case"].case_id,
                    run_id=record["run_dir"].name,
                    stage="validate_run",
                    kind="validate_run_failure",
                    error_summary=record["validation_error"],
                    evidence_path=str(record["run_dir"]),
                )
            )
    write_yaml(
        cycle_dir / "failures.yaml",
        FailureReport(cycle_id=cycle_id, failures=failures).model_dump(mode="json"),
    )

    refutations: list[RefutationRecord] = []
    for record in records:
        refutations.extend(_collect_refutations(record, cycle_id))
    write_yaml(
        cycle_dir / "refutations.yaml",
        RefutationReport(cycle_id=cycle_id, refutations=refutations).model_dump(mode="json"),
    )

    blockers: list[BlockerRecord] = []
    for record in records:
        blockers.extend(_collect_blockers(record, prior_fingerprints))
    write_yaml(
        cycle_dir / "blockers.yaml",
        BlockerReport(cycle_id=cycle_id, blockers=blockers).model_dump(mode="json"),
    )

    # --- strategy / subgoals / theorem use ---------------------------------
    traces = [t for t in (_collect_strategy_trace(r, cycle_id) for r in records) if t]
    write_yaml(
        cycle_dir / "strategy_trace.yaml",
        StrategyTraceReport(cycle_id=cycle_id, traces=traces).model_dump(mode="json"),
    )
    write_yaml(
        cycle_dir / "subgoal_progress.yaml",
        SubgoalProgressReport(
            cycle_id=cycle_id, cases=[_collect_subgoal_progress(r) for r in records]
        ).model_dump(mode="json"),
    )
    theorem_entries: list[TheoremUseEntry] = []
    for record in records:
        theorem_entries.extend(_collect_theorem_use(record))
    write_yaml(
        cycle_dir / "theorem_use_ledger.yaml",
        TheoremUseLedger(cycle_id=cycle_id, entries=theorem_entries).model_dump(mode="json"),
    )

    # --- bottlenecks + summary + campaign updates ---------------------------
    bottlenecks = _derive_bottlenecks(
        cycle_id, records, timings, failures, blockers, theorem_entries
    )
    write_yaml(
        cycle_dir / "bottleneck_report.yaml",
        BottleneckReport(cycle_id=cycle_id, bottlenecks=bottlenecks).model_dump(mode="json"),
    )

    summary_lines = [
        f"# Cycle summary — {cycle_id}",
        "",
        f"Mode: {mode} (provider={provider}, front_half={front_half})",
        f"Cases run: {', '.join(r['case'].case_id for r in records)}",
        f"Technical failures: {len(failures)}",
        f"Refutation records: {len(refutations)} "
        f"(proof-step-attack-only: {sum(1 for r in refutations if r.is_proof_step_attack_only)})",
        f"Blockers: {len(blockers)} (recurring: {sum(1 for b in blockers if b.recurrence == 'recurring')})",
        f"Bottlenecks: {len(bottlenecks)}",
        "",
        "No loop record upgrades any proof/source/evidence/grade/refutation/claim",
        "status. Next: six-reviewer pass, then triage of exactly one gap.",
        "",
    ]
    write_text(cycle_dir / "cycle_summary.md", "\n".join(summary_lines))

    _update_campaign_files(
        out_root, cycle_id, records, blockers, bottlenecks, failures,
        cycle_wall_hours=(time.monotonic() - cycle_t0) / 3600.0,
    )
    return cycle_dir


__all__ = ["run_loop_cycle", "load_cases", "DEFAULT_CASES"]
