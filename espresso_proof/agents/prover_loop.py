"""Prover-loop round machinery (loop report, rounds, gates) for Espresso.

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every public name defined here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.agent_pass import (
    INPUT_ARTIFACTS,
    collect_open_reviewer_blockers,
    _agent_input_artifacts,
    _append_reviewer_objection_events,
    _apply_missing_context_notes,
    _machine_answer_note,
    _split_machine_answered_blockers,
    _time_allocation_context,
    make_prover_attempt,
    make_verifier_review,
)
from espresso_proof.agents.clients import build_client
from espresso_proof.agents.prompts import (
    _reviewer_roles,
    agent_prompt,
    load_agent_context,
    prover_system_prompt,
    verifier_system_prompt,
)
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import path_exists, read_yaml, write_yaml
from espresso_proof.kernel.llm_client import LLMClient
from espresso_proof.kernel.models import (
    LLMAgentRunReport,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
)
from espresso_proof.kernel.observability import usage_sink_for_run
from espresso_proof.kernel.time_allocation import ensure_prover_time_allocation_plan
from espresso_proof.kernel.update_inbox_service import write_time_allocation_from_update_inbox
from espresso_proof.strategy.human_time_allocation import (
    only_valid_route as _only_valid_route,
    priority_for_strategy,
    recommended_allocation_for_priority,
    time_allocation_prompt_context,
    write_time_allocation_context,
)


# Only the EPHEMERAL prompt lane may cap blockers, and only with an explicit
# disclosed count — the artifact list is always complete (no silent drops).
_PROMPT_BLOCKER_CAP = 40


def _prompt_blockers_with_disclosure(blockers: list) -> list:
    items = list(blockers or [])
    if len(items) <= _PROMPT_BLOCKER_CAP:
        return items
    hidden = len(items) - _PROMPT_BLOCKER_CAP
    return items[:_PROMPT_BLOCKER_CAP] + [
        f"[+{hidden} more open blockers recorded in prover_loop.yaml — not shown in this prompt]"
    ]


def _route_allocation_entry(plan, route_id: str | None) -> dict | None:
    if route_id is None:
        return None
    for entry in plan.entries:
        if entry.route_id == route_id:
            return entry.model_dump(mode="json")
    return None


def build_prover_loop_report(
    *,
    agent_report: dict,
    strategy_brief: dict,
    human_choice: dict | None = None,
    approved_effort: str = "normal_pass",
    run_dir: Path | None = None,
) -> dict:
    allocation_plan = ensure_prover_time_allocation_plan(run_dir) if run_dir is not None else None
    top_strategies = strategy_brief.get("top_strategies", [])[:3]
    if not top_strategies:
        top_strategies = [
            {
                "id": "strategy_missing_brief",
                "title": "No strategy brief route available",
            }
        ]
    verifier_reviews = agent_report.get("verifier_reviews", [])
    reviewer_pointers = [_review_pointer(review) for review in verifier_reviews]
    # Priority-ordered, deduplicated, UNCAPPED (no silent drops — the old
    # alphabetical sorted-set + [:12] silently discarded the objections that
    # sorted last).
    blocker_details = collect_open_reviewer_blockers(verifier_reviews)
    blockers, machine_answered = _split_machine_answered_blockers(
        [detail["text"] for detail in blocker_details],
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    open_texts = set(blockers)
    blocker_details = [detail for detail in blocker_details if detail["text"] in open_texts]
    gate = agent_report["human_gate"]
    selected_strategy = _selected_strategy_from_choice(human_choice, top_strategies)
    approved_effort = _approved_effort_from_choice(human_choice, approved_effort)
    attempts = []
    for index, strategy in enumerate(top_strategies, start=1):
        strategy_id = strategy.get("id", f"strategy_{index}")
        allocation = _allocation_for_strategy(
            strategy_id=strategy_id,
            selected_strategy_id=selected_strategy,
            gate=gate,
            reviewer_reviews=verifier_reviews,
            allocation_entry=(
                _route_allocation_entry(allocation_plan, strategy_id)
                if allocation_plan is not None
                else None
            ),
        )
        status = _attempt_status_for_allocation(allocation, blockers)
        attempts.append(
            {
                "attempt_id": f"attempt_round_001_{strategy_id}",
                "round_index": 1,
                "strategy_id": strategy_id,
                "parent_attempt_id": None,
                "prover_attempt_id": agent_report["prover_attempt"]["agent_id"],
                "source_request_count": len(agent_report["prover_attempt"].get("source_requests", [])),
                "risk_count": len(agent_report["prover_attempt"].get("risks", [])),
                "reviewer_ids": [review["verifier_id"] for review in verifier_reviews],
                "reviewer_objections": reviewer_pointers,
                "time_allocation": allocation,
                "status": status,
                "blockers": blockers,
                "blocker_details": blocker_details,
                "next_action": _next_action_for_strategy(
                    index=index,
                    strategy=strategy,
                    gate=gate,
                    blockers=blockers,
                )
                + (_machine_answer_note(machine_answered) if machine_answered else ""),
            }
        )
    decision = "ask_human" if gate["status"] == "pending" else _decision_from_gate(gate)
    report = {
        "schema_version": 1,
        "claim_id": agent_report["claim_id"],
        "run_id": agent_report["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_llm_agent_run": "llm_agent_run.yaml",
        "selected_strategy_id": selected_strategy,
        "budget": {
            "approved_effort": approved_effort,
            "max_rounds": _max_rounds_for_effort(approved_effort),
            "rounds_used": 1,
            "max_agent_calls": _max_agent_calls_for_effort(approved_effort),
            "agent_calls_used": 1 + len(verifier_reviews),
            "stop_if_human_gate_pending": True,
        },
        "attempts": attempts,
        "human_gate_status": gate["status"],
        "loop_decision": decision,
        "decision_reason": _loop_decision_reason(
            gate, blockers, machine_answered=machine_answered
        ),
        "audit_note": (
            "Prover-loop scaffold: records attempt portfolio, reviewer objections, "
            "time allocation, and human gate without upgrading proof/source/evidence status."
        ),
    }
    ProverLoopReport.model_validate(report)
    return report


def write_initial_prover_round_artifacts(
    *,
    run_dir: Path,
    agent_report: dict,
    loop_report: dict,
) -> dict:
    """Write durable round artifacts for the initial prover/reviewer pass."""
    for attempt in loop_report.get("attempts", []):
        if attempt.get("round_index") != 1:
            continue
        _write_prover_round_artifact(
            run_dir=run_dir,
            agent_report=agent_report,
            loop_report=loop_report,
            attempt=attempt,
            prover_attempt=agent_report["prover_attempt"],
            verifier_reviews=agent_report["verifier_reviews"],
            source="initial_llm_agent_run",
        )
    return refresh_prover_round_index(run_dir=run_dir, loop_report=loop_report)


def refresh_prover_round_index(*, run_dir: Path, loop_report: dict) -> dict:
    rounds_dir = run_dir / "prover_rounds"
    entries = []
    for attempt in loop_report.get("attempts", []):
        artifact_path = _round_artifact_rel_path(attempt)
        path = run_dir / artifact_path
        if not path_exists(path):
            continue
        round_report = ProverRoundReport.model_validate(read_yaml(path))
        entries.append(
            {
                "round_index": round_report.round_index,
                "strategy_id": round_report.strategy_id,
                "loop_attempt_id": round_report.loop_attempt_id,
                "artifact_path": artifact_path,
                "status": round_report.status,
                "time_allocation": round_report.time_allocation,
            }
        )
    index = {
        "schema_version": 1,
        "claim_id": loop_report["claim_id"],
        "run_id": loop_report["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_prover_loop": "prover_loop.yaml",
        "rounds": sorted(entries, key=lambda item: (item["round_index"], item["loop_attempt_id"])),
    }
    ProverRoundIndex.model_validate(index)
    rounds_dir.mkdir(exist_ok=True)
    write_yaml(run_dir / "prover_rounds" / "index.yaml", index)
    return index


def _write_prover_round_artifact(
    *,
    run_dir: Path,
    agent_report: dict,
    loop_report: dict,
    attempt: dict,
    prover_attempt: dict,
    verifier_reviews: list[dict],
    source: str,
) -> dict:
    report = {
        "schema_version": 1,
        "claim_id": loop_report["claim_id"],
        "run_id": loop_report["run_id"],
        "round_index": attempt["round_index"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "strategy_id": attempt["strategy_id"],
        "loop_attempt_id": attempt["attempt_id"],
        "parent_attempt_id": attempt.get("parent_attempt_id"),
        "input_artifacts": agent_report.get("input_artifacts", INPUT_ARTIFACTS),
        "context_summary": (
            "Durable prover/reviewer round artifact. It records the candidate prover output, "
            "the independent reviewer outputs, and the loop allocation for this strategy."
        ),
        "prover_attempt": prover_attempt,
        "verifier_reviews": verifier_reviews,
        "reviewer_blockers": attempt.get("blockers", []),
        "human_gate_status": loop_report["human_gate_status"],
        "time_allocation": attempt["time_allocation"],
        "status": attempt["status"],
        "next_action": attempt["next_action"],
        "safety_note": (
            "This round artifact does not upgrade proof status, source support status, "
            "checked evidence, or human preference into verification."
        ),
    }
    ProverRoundReport.model_validate(report)
    path = run_dir / _round_artifact_rel_path(attempt)
    path.parent.mkdir(exist_ok=True)
    write_yaml(path, report)
    return report


def _round_artifact_rel_path(attempt: dict) -> str:
    attempt_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(attempt.get("attempt_id", "attempt")))
    return f"prover_rounds/round_{int(attempt['round_index']):03d}_{attempt_id}.yaml"


def _human_feedback_advisory(run_dir: Path) -> str:
    """The human's advisory ranking note for this run (human_choice.taste_judgment).

    Pure read, fail-closed to empty. This is advisory prompt context only and
    carries no evidence; the kernel re-validates every round output."""

    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        rel = manifest.get("artifacts", {}).get("human_choice")
        if not rel or not (run_dir / rel).exists():
            return ""
        return str(read_yaml(run_dir / rel).get("taste_judgment") or "")[:2000]
    except Exception:  # noqa: BLE001
        return ""


def _selected_strategy_from_choice(human_choice: dict | None, strategies: list[dict]) -> str | None:
    strategy_ids = [strategy.get("id") for strategy in strategies]
    if human_choice and human_choice.get("selected_strategy") == "none":
        return None
    if (
        human_choice
        and human_choice.get("status") == "selected"
        and human_choice.get("selected_strategy") in strategy_ids
    ):
        return human_choice["selected_strategy"]
    return strategy_ids[0] if strategy_ids else None


def _approved_effort_from_choice(human_choice: dict | None, fallback: str) -> str:
    if human_choice and human_choice.get("status") == "selected" and human_choice.get("effort"):
        return str(human_choice["effort"])
    return fallback


def _allocation_for_strategy(
    *,
    strategy_id: str,
    selected_strategy_id: str | None,
    gate: dict,
    reviewer_reviews: list[dict],
    allocation_entry: dict | None = None,
) -> str:
    if gate.get("status") == "pending":
        return "ask_human"
    if gate.get("decision") == "stop":
        return "stop"
    is_selected = strategy_id == selected_strategy_id
    if allocation_entry is not None:
        action = allocation_entry.get("action")
        if action == "stop":
            return "stop"
        if action == "ask_human":
            return "ask_human"
        if action in {"source_pinning", "repair_attempt"}:
            return "repair" if is_selected else "cheap_check"
        if action == "cheap_check":
            return "cheap_check"
        if action == "bounded_proof_exploration":
            return "expand" if is_selected else "cheap_check"
    if any(review.get("verdict") == "blocked" for review in reviewer_reviews):
        return "pause"
    if any(review.get("verdict") in {"needs_revision", "needs_sources"} for review in reviewer_reviews):
        return "repair" if is_selected else "cheap_check"
    return "expand" if is_selected else "cheap_check"


def _attempt_status_for_allocation(allocation: str, blockers: list[str]) -> str:
    if allocation == "stop":
        return "stopped"
    if allocation in {"pause"}:
        return "blocked"
    if allocation == "ask_human":
        return "waiting_human"
    if blockers:
        return "needs_repair"
    # repair allocation with zero OPEN blockers (e.g. the only recorded
    # request was machine-answered): nothing is repairable — the attempt is
    # an honest candidate; the kernel model rejects needs_repair without
    # blockers, so this is also the fail-closed branch.
    return "candidate"


def _next_action_for_strategy(*, index: int, strategy: dict, gate: dict, blockers: list[str]) -> str:
    title = strategy.get("title") or strategy.get("id") or f"strategy {index}"
    if gate.get("status") == "pending":
        if index == 1:
            return f"Ask mathematician whether to spend the next round on {title}."
        return f"Keep {title} as a low-budget alternative until human preference is resolved."
    if gate.get("decision") == "stop":
        return f"Stop before spending another prover round on {title}."
    if blockers:
        return f"Repair {title} against reviewer blockers before expanding proof text."
    return f"Expand {title} with source matching and checked examples."


def _decision_from_gate(gate: dict) -> str:
    decision = gate.get("decision")
    if decision == "continue":
        return "continue"
    if decision == "stop":
        return "stop"
    return "repair"


def _loop_decision_reason(
    gate: dict, blockers: list[str], *, machine_answered: list[dict] | None = None
) -> str:
    if gate.get("status") == "pending":
        return "Human gate is pending; Espresso must not run deeper prover rounds yet."
    if gate.get("decision") == "stop":
        return "Human gate resolved to stop."
    if blockers:
        return "Reviewer objections or source requests require repair before another proof round."
    if machine_answered:
        return (
            "Human gate resolved; recorded reviewer computation request(s) were "
            "machine-answered by trusted scoped checks (see attempt next_action "
            "and proof_verification.yaml)."
        )
    return "Human gate resolved and no blocking reviewer objection was recorded."


def _max_rounds_for_effort(effort: str) -> int:
    return {
        "quick_test": 1,
        "normal_pass": 2,
        "deep_exploration": 4,
        "long_watch": 8,
    }.get(effort, 2)


def _max_agent_calls_for_effort(effort: str) -> int:
    return {
        "quick_test": 4,
        "normal_pass": 8,
        "deep_exploration": 20,
        "long_watch": 50,
    }.get(effort, 8)


def resolve_agent_gate(
    *,
    run_dir: Path,
    decision: str,
    reason: str,
    resolved_by: str,
) -> dict:
    """Resolve the LLM-agent human gate with honest provenance.

    ``resolved_by`` records WHO resolved the gate: ``"human"`` (a real human)
    or ``"auto"`` (machinery, no human in the loop). It is REQUIRED — there is
    deliberately no default, so a caller can never mint human provenance by
    forgetting the kwarg. ``human_confirmed`` is derived from it — an auto
    resolution can never claim human confirmation (the kernel model rejects
    auto + confirmed).
    """
    if resolved_by not in {"human", "auto"}:
        raise ValueError(f"resolved_by must be 'human' or 'auto', got {resolved_by!r}")
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_path = manifest.get("optional_artifacts", {}).get("llm_agent_run")
    if not agent_path:
        raise RuntimeError("run has no llm_agent_run optional artifact")
    report = read_yaml(run_dir / agent_path)
    gate = report["human_gate"]
    if gate.get("status") == "resolved":
        raise RuntimeError("human gate is already resolved")
    if decision not in gate["allowed_decisions"]:
        raise RuntimeError(f"decision {decision!r} is not allowed for this gate")
    gate.update(
        {
            "status": "resolved",
            "decision": decision,
            "reason": reason,
            # Honest provenance: only a real human resolution may claim human
            # confirmation. resolved_by='auto' records "no human involved".
            "human_confirmed": resolved_by == "human",
            "resolved_by": resolved_by,
            "resolved_at": datetime.now(UTC).isoformat(),
        }
    )
    LLMAgentRunReport.model_validate(report)
    write_yaml(run_dir / agent_path, report)
    return report


def append_prover_loop_round(
    *,
    run_dir: Path,
    provider: str,
    model: str,
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    client: "LLMClient | None" = None,
) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    allocation_plan = ensure_prover_time_allocation_plan(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest.get("optional_artifacts", {})
    agent_path = optional.get("llm_agent_run")
    loop_path = optional.get("prover_loop")
    if not agent_path or not loop_path:
        raise RuntimeError("run requires llm_agent_run.yaml and prover_loop.yaml")
    agent_report = read_yaml(run_dir / agent_path)
    loop_report = read_yaml(run_dir / loop_path)
    if agent_report["human_gate"]["status"] != "resolved":
        raise RuntimeError("cannot run another prover round before resolving the human gate")
    if loop_report.get("loop_decision") in {"ask_human", "stop"}:
        raise RuntimeError(f"cannot append round when loop_decision={loop_report.get('loop_decision')}")

    attempts = loop_report["attempts"]
    if loop_report["budget"]["rounds_used"] >= loop_report["budget"]["max_rounds"]:
        raise RuntimeError("prover loop budget has no remaining rounds")
    selected_strategy_id = loop_report.get("selected_strategy_id")
    parent_attempt = _select_parent_attempt(attempts, selected_strategy_id)
    next_round = max(attempt["round_index"] for attempt in attempts) + 1

    if client is not None and getattr(client, "usage_sink", None) is None and hasattr(client, "usage_sink"):
        client.usage_sink = usage_sink_for_run(run_dir)
    client = client or build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        usage_sink=usage_sink_for_run(run_dir),
    )
    context = load_agent_context(run_dir)
    time_context = _time_allocation_context(run_dir, allocation_plan)
    input_artifacts = _agent_input_artifacts(manifest)
    round_context, target_subgoal = build_next_prover_input_from_reviewer_feedback(
        run_dir=run_dir,
        context=context,
        loop_report=loop_report,
        parent_attempt=parent_attempt,
        selected_strategy_id=selected_strategy_id,
        next_round=next_round,
        allocation_plan=allocation_plan,
        time_context=time_context,
    )
    created_at = datetime.now(UTC).isoformat()
    prover_raw = client.complete_json(
        system=prover_system_prompt(),
        prompt=agent_prompt(round_context, role="prover_loop_round"),
    )
    prover = make_prover_attempt(
        prover_raw,
        client=client,
        created_at=created_at,
        input_artifacts=input_artifacts,
    )
    prover["agent_id"] = f"llm_prover_{next_round:03d}"
    _apply_missing_context_notes(prover, time_context)

    reviewer_roles = _reviewer_roles(reviewer_set, 3)
    verifier_reviews = []
    for index, reviewer_role in enumerate(reviewer_roles, start=1):
        verifier_raw = client.complete_json(
            system=verifier_system_prompt(index, reviewer_role=reviewer_role),
            prompt=agent_prompt(
                {**round_context, "prover_attempt": prover},
                role=f"round_{next_round}_{reviewer_role}",
            ),
        )
        review = make_verifier_review(
            verifier_raw,
            client=client,
            created_at=datetime.now(UTC).isoformat(),
            index=index,
            reviewer_role=reviewer_role,
            target_subgoal_id=target_subgoal.get("id"),  # Bind objections to the attacked subgoal
        )
        review["verifier_id"] = f"round_{next_round:03d}_{review['verifier_id']}"
        verifier_reviews.append(review)

    # Priority-ordered, deduplicated, UNCAPPED — same mechanism as the initial
    # report (no silent drops in the deep-round lane either).
    blocker_details = collect_open_reviewer_blockers(verifier_reviews)
    blockers, machine_answered = _split_machine_answered_blockers(
        [detail["text"] for detail in blocker_details],
        run_dir=run_dir,
        claim_id=loop_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    open_texts = set(blockers)
    blocker_details = [detail for detail in blocker_details if detail["text"] in open_texts]
    reviewer_pointers = [_review_pointer(review) for review in verifier_reviews]
    selected_strategy_id = parent_attempt["strategy_id"]
    # The selected route's human priority deterministically sets the
    # allocation. Reviewer blockers always force 'repair' (objections outrank
    # taste); otherwise focus/normal -> expand, repair -> repair, reduce ->
    # cheap_check unless it is the only valid route. Scheduling only — it never
    # sets or upgrades any proof/source/evidence/grade/claim status. The advisory
    # context sidecar is (re)written so the dashboard + replay can audit it.
    allocation_context = write_time_allocation_context(run_dir)
    allocation = recommended_allocation_for_priority(
        priority_for_strategy(allocation_context, selected_strategy_id),
        has_blockers=bool(blockers),
        only_valid_route=_only_valid_route(allocation_context),
    )
    new_attempt = {
        "attempt_id": f"attempt_round_{next_round:03d}_{selected_strategy_id}",
        "round_index": next_round,
        "strategy_id": selected_strategy_id,
        "parent_attempt_id": parent_attempt["attempt_id"],
        "prover_attempt_id": prover["agent_id"],
        "source_request_count": len(prover.get("source_requests", [])),
        "risk_count": len(prover.get("risks", [])),
        "reviewer_ids": [review["verifier_id"] for review in verifier_reviews],
        "reviewer_objections": reviewer_pointers,
        "time_allocation": allocation,
        "status": "needs_repair" if blockers else "candidate",
        "blockers": blockers,
        "blocker_details": blocker_details,
        "next_action": (
            "Repair reviewer blockers before another proof round."
            if blockers
            else "Expand this candidate with source matching and checked examples."
        )
        + (_machine_answer_note(machine_answered) if machine_answered else ""),
        # Persist the scheduler's pick so the round is durably bound to
        # a named subgoal (prompt context alone is not auditable). None when no
        # subgoal was selectable. Never earns or upgrades any status.
        "target_subgoal_id": target_subgoal.get("id"),
        "target_local_claim": target_subgoal.get("local_claim") or "",
    }
    loop_report["attempts"].append(new_attempt)
    loop_report["generated_at"] = datetime.now(UTC).isoformat()
    loop_report["budget"]["rounds_used"] = len(
        {attempt["round_index"] for attempt in loop_report["attempts"]}
    )
    loop_report["budget"]["agent_calls_used"] += 1 + len(verifier_reviews)
    loop_report["loop_decision"] = "repair" if blockers else "continue"
    loop_report["decision_reason"] = (
        "Round appended from resolved human gate; reviewer blockers were routed into the prover prompt."
        + (
            " Recorded computation request(s) were machine-answered by trusted scoped checks (see attempt next_action)."
            if machine_answered
            else ""
        )
    )
    ProverLoopReport.model_validate(loop_report)
    write_yaml(run_dir / loop_path, loop_report)
    if not (run_dir / "prover_rounds" / "index.yaml").exists():
        write_initial_prover_round_artifacts(
            run_dir=run_dir,
            agent_report=agent_report,
            loop_report=loop_report,
        )
    _write_prover_round_artifact(
        run_dir=run_dir,
        agent_report={**agent_report, "input_artifacts": input_artifacts},
        loop_report=loop_report,
        attempt=new_attempt,
        prover_attempt=prover,
        verifier_reviews=verifier_reviews,
        source="appended_prover_loop_round",
    )
    refresh_prover_round_index(run_dir=run_dir, loop_report=loop_report)
    manifest.setdefault("optional_artifacts", {})["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    if _append_reviewer_objection_events(
        run_dir=run_dir,
        claim_id=loop_report["claim_id"],
        route_id=str(new_attempt["strategy_id"]),
        origin_stage="prover_loop_round",
        origin_artifact=_round_artifact_rel_path(new_attempt),
        event_id_prefix=f"upd_reviewer_objection_r{int(new_attempt['round_index']):03d}",
        verifier_reviews=verifier_reviews,
        open_blocker_texts=set(blockers),
    ):
        write_time_allocation_from_update_inbox(run_dir)
    return loop_report


def build_next_prover_input_from_reviewer_feedback(
    *,
    run_dir: Path,
    context: dict,
    loop_report: dict,
    parent_attempt: dict,
    selected_strategy_id: str | None,
    next_round: int,
    allocation_plan,
    time_context: dict,
) -> tuple[dict, dict]:
    """Build the next prover prompt context from prior reviewer output.

    The key feedback lane is:
    ``prover_loop.yaml.attempts[-1].blockers`` plus scoped repair objections
    -> ``agent_prompt(..., role="prover_loop_round")``.
    """

    # The subgoal graph is the scheduling center. Pick the highest-value
    # OPEN subgoal and NAME it to the prover so the round works on that specific
    # subgoal id + local claim (not the whole claim). This is scheduling only: it
    # never sets/upgrades any status, and a contradicted (refuted) main goal
    # yields no target (stale optimism cannot override a checked refutation).
    from espresso_proof.kernel.subgoal_graph import assemble_subgoal_graph, select_next_subgoal

    target_node = select_next_subgoal(assemble_subgoal_graph(run_dir))
    target_subgoal = (
        {
            "id": target_node.get("id"),
            "local_claim": target_node.get("statement"),
            "state": target_node.get("state"),
            "instruction": (
                "Work on THIS subgoal only; cite its id in your steps. Produce "
                "candidate work for this local claim. Do not claim the whole claim "
                "is proved. Status upgrades require the kernel's hard gates."
            ),
        }
        if target_node is not None
        else {
            "id": None,
            "local_claim": None,
            "state": None,
            "instruction": (
                "No open subgoal is selectable (the claim may be refuted or every "
                "subgoal is already resolved/blocked). Do not invent progress."
            ),
        }
    )

    # Feed the prior round's reviewer objections that NAMED this subgoal
    # back to the prover so a repair attempt can directly address them (instead of
    # only seeing the same free-text blockers). Advisory prompt context only — the
    # round's outputs are re-validated by the kernel, and the pairing is recorded in
    # a snapshot-excluded, never-validated sidecar (can never upgrade any status).
    from espresso_proof.orchestration.repair_target import (
        capture_repair_target,
        collect_repair_target_objections,
    )

    repair_target_objections = collect_repair_target_objections(
        run_dir, target_subgoal.get("id")
    )
    capture_repair_target(
        run_dir,
        round_index=next_round,
        strategy_id=selected_strategy_id,
        target_subgoal_id=target_subgoal.get("id"),
        objections=repair_target_objections,
    )
    round_context = {
        **context,
        "previous_prover_loop_attempt": parent_attempt,
        "reviewer_blockers_to_repair": _prompt_blockers_with_disclosure(
            parent_attempt.get("blockers", [])
        ),
        "repair_target_objections": repair_target_objections,
        "loop_decision": loop_report.get("loop_decision"),
        "time_allocation": parent_attempt.get("time_allocation"),
        "time_allocation_plan": time_context,
        "time_allocation_entry": _route_allocation_entry(
            allocation_plan, parent_attempt.get("strategy_id")
        ),
        "prover_missing_context_to_record": time_context["missing_context"],
        "target_subgoal": target_subgoal,
        # The human's advisory ranking/time-allocation note for this
        # continuation (from human_choice.taste_judgment). It is prompt context
        # ONLY — the round's outputs are re-validated by the kernel, so this can
        # never upgrade proof/source/evidence/grade/claim status.
        "human_feedback_advisory": _human_feedback_advisory(run_dir),
        # Structured human strategy feedback (score/priority/note) +
        # the derived advisory allocation hint, as prompt context. Advisory only.
        "human_time_allocation": time_allocation_prompt_context(run_dir, selected_strategy_id),
    }
    return round_context, target_subgoal


def _select_parent_attempt(attempts: list[dict], selected_strategy_id: str | None) -> dict:
    candidates = [
        attempt
        for attempt in attempts
        if selected_strategy_id is None or attempt.get("strategy_id") == selected_strategy_id
    ]
    if not candidates:
        raise RuntimeError("selected strategy has no prover-loop attempt")
    return sorted(candidates, key=lambda item: item["round_index"])[-1]


def _pointer_scope_from_review(review: dict) -> tuple[str, str | None]:
    """Derive a ProverLoopReviewPointer's (scope, subgoal_id) from the
    review's scoped objections. Only when every subgoal-scoped objection names the
    SAME subgoal does the pointer claim that subgoal; otherwise it stays 'route'
    (honest default — provenance only, never reconciled against the renumbering graph)."""
    sos = review.get("scoped_objections") or []
    subgoal_ids = {
        s.get("subgoal_id") for s in sos
        if isinstance(s, dict) and s.get("scope") == "subgoal" and s.get("subgoal_id")
    }
    if len(subgoal_ids) == 1:
        return "subgoal", next(iter(subgoal_ids))
    return "route", None


def _review_pointer(review: dict) -> dict:
    """Build one ProverLoopReviewPointer dict, carrying the scope/subgoal_id
    derived from the review's scoped objections (advisory provenance)."""
    scope, subgoal_id = _pointer_scope_from_review(review)
    return {
        "verifier_id": review["verifier_id"],
        "verdict": review["verdict"],
        "objection_count": len(review.get("objections", [])),
        "recommended_human_action": review["recommended_human_action"],
        "scope": scope,
        "subgoal_id": subgoal_id,
    }
