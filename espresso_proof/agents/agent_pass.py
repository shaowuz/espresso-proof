"""Initial Prover -> Reviewers pass orchestration for Espresso's LLM agents.

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every public name defined here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.agents.clients import CODEX_ADAPTER_DIAGNOSTIC_KEY, build_client
from espresso_proof.agents.prompts import (
    _reviewer_roles,
    agent_prompt,
    load_agent_context,
    prover_system_prompt,
    verifier_system_prompt,
)
from espresso_proof.agents.sanitize import (
    collect_unsafe_language,
    normalize_action,
    normalize_verdict,
    safe_prover_list,
    safe_prover_text,
)
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.llm_client import LLMClient
from espresso_proof.kernel.models import LLMAgentRunReport
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.proof_language import NO_AGENT_TEXT, safe_list
from espresso_proof.kernel.time_allocation import (
    TimeAllocationPlan,
    ensure_prover_time_allocation_plan,
    time_allocation_prompt_payload,
)
from espresso_proof.kernel.update_inbox_service import (
    append_scheduling_update,
    write_time_allocation_from_update_inbox,
)


INPUT_ARTIFACTS = [
    "locked_claim.yaml",
    "hypothesis_ledger.yaml",
    "literature_review.yaml",
    "examples.yaml",
    "computation_checks.yaml",
    "counterexamples.yaml",
    "tests.yaml",
    "source_grounding.yaml",
    "review.yaml",
    "idea_grade.yaml",
    "strategy_brief.yaml",
]


MISSING_CONTEXT_NOTES = {
    "source_support": {
        "source_request": (
            "Missing source context: exact source support is not checked for this route."
        ),
        "risk": (
            "Unchecked citations cannot support a proof candidate; route source use must "
            "stay pending until source grounding validates exact locator and hypotheses."
        ),
    },
    "theorem_applicability": {
        "source_request": (
            "Missing theorem context: theorem applicability is not checked for this route."
        ),
        "risk": (
            "Pending theorem applicability cannot support a proof candidate; route theorem "
            "use must stay conditional until applicability validation passes."
        ),
    },
    "examples_pressure": {
        "source_request": (
            "Missing example context: example pressure is pending or partial."
        ),
        "risk": (
            "Unchecked examples cannot count as survived evidence for a proof candidate."
        ),
    },
    "counterexamples_pressure": {
        "source_request": (
            "Missing counterexample context: counterexample pressure is pending or partial."
        ),
        "risk": (
            "No counterexample found in unchecked pressure is not evidence against refutation."
        ),
    },
}


def _complete_agent_json(
    *,
    client: LLMClient,
    role: str,
    call_diagnostics: list[dict],
    system: str,
    prompt: str,
) -> dict:
    raw = client.complete_json(system=system, prompt=prompt)
    diagnostic = _agent_call_diagnostic(
        raw,
        client=client,
        role=role,
        call_index=len(call_diagnostics) + 1,
    )
    if diagnostic is not None:
        call_diagnostics.append(diagnostic)
    return raw


def _agent_call_diagnostic(
    raw: dict,
    *,
    client: LLMClient,
    role: str,
    call_index: int,
) -> dict | None:
    provider = getattr(client, "provider", "")
    model = getattr(client, "model", "")
    if provider == "stub":
        return None
    if provider == "openai":
        return {
            "call_index": call_index,
            "role": role,
            "provider": "openai",
            "model": model,
            "adapter": "openai_responses",
            "parse_status": "ok",
            "fallback_used": False,
            "fallback_reason": "",
            "request_id": "",
            "verified_real_provider": True,
        }
    if provider != "external":
        return None

    meta = raw.get(CODEX_ADAPTER_DIAGNOSTIC_KEY) if isinstance(raw, dict) else None
    if isinstance(meta, dict):
        adapter = str(meta.get("adapter") or "")
        parse_status = str(meta.get("parse_status") or "")
        fallback_used = meta.get("fallback_used") is True
        fallback_reason = str(meta.get("fallback_reason") or "")
        request_id = str(meta.get("request_id") or "")
        verified = (
            adapter == "espresso_codex_agent"
            and parse_status == "ok"
            and not fallback_used
        )
        return {
            "call_index": call_index,
            "role": role,
            "provider": "external",
            "model": model,
            "adapter": adapter,
            "parse_status": parse_status,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "request_id": request_id,
            "verified_real_provider": verified,
        }
    return {
        "call_index": call_index,
        "role": role,
        "provider": "external",
        "model": model,
        "adapter": "external_json_agent",
        "parse_status": "unverified",
        "fallback_used": False,
        "fallback_reason": "",
        "request_id": "",
        "verified_real_provider": False,
    }


def _agent_input_artifacts(manifest: dict) -> list[str]:
    inputs = list(INPUT_ARTIFACTS)
    rel = (manifest.get("optional_artifacts") or {}).get("time_allocation")
    if rel and rel not in inputs:
        inputs.append(rel)
    return inputs


def _time_allocation_context(run_dir: Path, plan) -> dict:
    payload = time_allocation_prompt_payload(plan)
    theorem_missing = True
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    theorem_rel = (manifest.get("optional_artifacts") or {}).get("theorem_applicability")
    if theorem_rel and (run_dir / theorem_rel).exists():
        try:
            theorem = read_yaml(run_dir / theorem_rel)
            theorem_missing = theorem.get("overall_status") != "applicable"
        except Exception:  # noqa: BLE001 - fail closed for prompt context
            theorem_missing = True
    if theorem_missing and "theorem_applicability" not in payload["missing_context"]:
        payload["missing_context"].append("theorem_applicability")
    return payload


def _append_unique(values: list[str], new_values: list[str]) -> list[str]:
    seen = {value.strip() for value in values}
    out = list(values)
    for value in new_values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _apply_missing_context_notes(prover: dict, time_context: dict) -> None:
    missing = [
        item
        for item in time_context.get("missing_context", [])
        if item in MISSING_CONTEXT_NOTES
    ]
    if not missing:
        return
    source_requests = [
        MISSING_CONTEXT_NOTES[item]["source_request"]
        for item in missing
    ]
    risks = [
        MISSING_CONTEXT_NOTES[item]["risk"]
        for item in missing
    ]
    prover["source_requests"] = _append_unique(prover.get("source_requests", []), source_requests)
    prover["risks"] = _append_unique(prover.get("risks", []), risks)


def run_llm_agent_pass(
    *,
    run_dir: Path,
    provider: str,
    model: str,
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    verifier_count: int = 1,
    reviewer_set: str = "generic",
    provider_diagnostic: dict | None = None,
) -> dict:
    """Run the initial Prover -> Reviewers pass and return its artifact payload.

    This function does not write files. The orchestrator persists the returned
    data into ``llm_agent_run.yaml`` and derives ``prover_loop.yaml`` plus
    ``prover_rounds/`` from it.
    """

    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    # Order matters: the time-allocation plan is written (and registered in the
    # manifest) BEFORE the client is built, so a provider misconfiguration
    # surfaces the graded-strategy requirement first and non-snapshot callers
    # (dev CLI) still persist the plan when build_client fails.
    allocation_plan = ensure_prover_time_allocation_plan(run_dir)
    client = build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    context, input_artifacts = load_context_artifacts_for_initial_agent_pass(
        run_dir, allocation_plan
    )
    agent_call_diagnostics: list[dict] = []
    now = datetime.now(UTC).isoformat()
    prover = ask_prover_for_candidate_route(
        client=client,
        context=context,
        input_artifacts=input_artifacts,
        created_at=now,
        agent_call_diagnostics=agent_call_diagnostics,
    )
    verifier_reviews = ask_reviewers_to_attack_candidate_route(
        client=client,
        context=context,
        prover_attempt=prover,
        reviewer_set=reviewer_set,
        verifier_count=verifier_count,
        agent_call_diagnostics=agent_call_diagnostics,
    )
    return assemble_llm_agent_run_report(
        context=context,
        client=client,
        generated_at=now,
        input_artifacts=input_artifacts,
        prover_attempt=prover,
        verifier_reviews=verifier_reviews,
        reviewer_set=reviewer_set,
        provider_diagnostic=provider_diagnostic,
        agent_call_diagnostics=agent_call_diagnostics,
    )


def load_context_artifacts_for_initial_agent_pass(
    run_dir: Path, allocation_plan: TimeAllocationPlan
) -> tuple[dict, list[str]]:
    context = load_agent_context(run_dir)
    time_context = _time_allocation_context(run_dir, allocation_plan)
    context["time_allocation_plan"] = time_context
    context["prover_missing_context_to_record"] = time_context["missing_context"]
    input_artifacts = _agent_input_artifacts(context["manifest"])
    return context, input_artifacts


def ask_prover_for_candidate_route(
    *,
    client: LLMClient,
    context: dict,
    input_artifacts: list[str],
    created_at: str,
    agent_call_diagnostics: list[dict],
) -> dict:
    """Feed context artifacts to the prover and normalize its candidate route."""

    time_context = context["time_allocation_plan"]
    prover_raw = _complete_agent_json(
        client=client,
        role="prover",
        call_diagnostics=agent_call_diagnostics,
        system=prover_system_prompt(),
        prompt=agent_prompt(context, role="prover"),
    )
    prover = make_prover_attempt(
        prover_raw,
        client=client,
        created_at=created_at,
        input_artifacts=input_artifacts,
    )
    _apply_missing_context_notes(prover, time_context)
    return prover


def ask_reviewers_to_attack_candidate_route(
    *,
    client: LLMClient,
    context: dict,
    prover_attempt: dict,
    reviewer_set: str,
    verifier_count: int,
    agent_call_diagnostics: list[dict],
) -> list[dict]:
    """Feed the prover attempt back into independent reviewers."""

    verifier_reviews = []
    reviewer_roles = _reviewer_roles(reviewer_set, verifier_count)
    for index, reviewer_role in enumerate(reviewer_roles, start=1):
        verifier_raw = _complete_agent_json(
            client=client,
            role=reviewer_role,
            call_diagnostics=agent_call_diagnostics,
            system=verifier_system_prompt(index, reviewer_role=reviewer_role),
            prompt=agent_prompt(
                {**context, "prover_attempt": prover_attempt},
                role=reviewer_role,
            ),
        )
        verifier_reviews.append(
            make_verifier_review(
                verifier_raw,
                client=client,
                created_at=datetime.now(UTC).isoformat(),
                index=index,
                reviewer_role=reviewer_role,
            )
        )
    return verifier_reviews


def assemble_llm_agent_run_report(
    *,
    context: dict,
    client: LLMClient,
    generated_at: str,
    input_artifacts: list[str],
    prover_attempt: dict,
    verifier_reviews: list[dict],
    reviewer_set: str,
    provider_diagnostic: dict | None,
    agent_call_diagnostics: list[dict],
) -> dict:
    reviewer_roles = _reviewer_roles(reviewer_set, len(verifier_reviews))
    gate = make_human_gate(verifier_reviews)
    report = {
        "schema_version": 1,
        "claim_id": context["claim"]["id"],
        "run_id": context["manifest"]["run_id"],
        "generated_at": generated_at,
        "provider": client.provider,
        "model": client.model,
        "input_artifacts": input_artifacts,
        "prover_attempt": prover_attempt,
        "verifier_reviews": verifier_reviews,
        "human_gate": gate,
        "provider_diagnostic": provider_diagnostic,
        "agent_call_diagnostics": agent_call_diagnostics,
        "notes": [
            "Initial LLM agent runtime; outputs are candidate planning artifacts only.",
            f"Reviewer set: {reviewer_set}; roles: {', '.join(reviewer_roles)}.",
            "This artifact must not upgrade Idea grade, source support, checked evidence, or proof status.",
            "Human gate must be resolved through a validated CLI/API path before deeper automated work.",
        ],
    }
    LLMAgentRunReport.model_validate(report)
    return report


# The ONLY machine-answerable request shape: the generic disjunctive
# "(source or) exact computation for the most dangerous example/case" —
# an ANCHORED FULLMATCH (Kernel Safety + Math Workflow review): a request
# naming any specific object ("exact computation of the Hodge numbers ...")
# never matches and stays open, because claim-scoped evidence does not bind
# to specifics the entries' targets do not cover. Fail-closed false
# negatives are the intended direction.
_ANSWERABLE_COMPUTATION_REQUEST = re.compile(
    r"(?:source\s+or\s+)?exact\s+computation\s+for\s+the\s+most\s+dangerous\s+"
    r"(?:example|case)\s*\.?",
    re.IGNORECASE,
)


def _split_machine_answered_blockers(
    blockers: list[str],
    *,
    run_dir: Path | None,
    claim_id: str,
    source_request_texts: set[str] | None = None,
) -> tuple[list[str], list[dict]]:
    """Split reviewer blockers into (open, machine_answered).

    Goal 42E cycle 3: a blocker is machine-answered ONLY when ALL hold:
    (a) it originated as a reviewer SOURCE REQUEST (provenance by text-set
        membership against the round's ``source_requests``; objections and
        missing hypotheses are never answerable regardless of phrasing);
    (b) its WHOLE text fullmatches the generic disjunctive
        "(source or) exact computation for the most dangerous example" form
        — requests naming specific objects stay open;
    (c) this run carries trusted, confirmed machine-check entries for THIS
        locked claim (instance_checked / computation_checked in
        proof_verification.yaml).
    Each answer records WHICH entries discharged WHICH request text.
    Resolution is bookkeeping over reviewer requests; it never touches any
    proof/source/evidence/grade status."""

    if run_dir is None or not blockers:
        return blockers, []
    pv_path = Path(run_dir) / "proof_verification.yaml"
    if not pv_path.exists():
        return blockers, []
    try:
        entries = read_yaml(pv_path).get("entries", [])
    except Exception:  # noqa: BLE001 - fail closed: unreadable evidence answers nothing
        return blockers, []
    confirmed = [
        e for e in entries
        if e.get("trusted") and e.get("confirmed")
        and e.get("earned_status") in {"instance_checked", "computation_checked"}
        and e.get("claim_id") == claim_id
    ]
    if not confirmed:
        return blockers, []
    requests = {str(t).strip() for t in (source_request_texts or set())}
    answered: list[dict] = []
    open_blockers: list[str] = []
    for blocker in blockers:
        text = str(blocker).strip()
        if text in requests and _ANSWERABLE_COMPUTATION_REQUEST.fullmatch(
            " ".join(text.split())
        ):
            ids = [str(e.get("id", "")) for e in confirmed]
            if len(ids) > 8:
                ids = ids[:8] + [f"+{len(ids) - 8} more"]
            answered.append({"request": text, "entry_ids": ids})
        else:
            open_blockers.append(blocker)
    if not answered:
        return blockers, []
    return open_blockers, answered


def _machine_answer_note(answered: list[dict]) -> str:
    parts = "; ".join(
        f"{item['request']!r} answered by trusted machine check(s) "
        f"[{', '.join(item['entry_ids'])}]"
        for item in answered
    )
    note = (
        f" Machine-answered reviewer request(s): {parts} "
        "(see proof_verification.yaml; the evidence is scoped, never a "
        "universal proof)."
    )
    return note if len(note) <= 600 else note[:597] + "..."


def _reviewer_blocker_slots(
    review: dict, open_blocker_texts: set[str] | None
) -> list[tuple[str, int]]:
    slots: list[tuple[str, int]] = []
    for field in ("objections", "missing_hypotheses", "source_requests"):
        for index, item in enumerate(review.get(field, []) or [], start=1):
            text = str(item).strip()
            if not text:
                continue
            if open_blocker_texts is not None and text not in open_blocker_texts:
                continue
            slots.append((field, index))
    return slots


def _append_reviewer_objection_events(
    *,
    run_dir: Path,
    claim_id: str,
    route_id: str,
    origin_stage: str,
    origin_artifact: str,
    event_id_prefix: str,
    verifier_reviews: list[dict],
    open_blocker_texts: set[str] | None = None,
) -> bool:
    """Route reviewer blockers into Update Inbox as scheduling-only pointers.

    The event records provenance to the validated origin artifact, not blocker
    prose. Reviewer text remains owned by the prover-round artifact and its
    language/status-smuggling validators.
    """

    artifact_hash = artifact_revision_hash(run_dir, origin_artifact)
    created_any = False
    event_index = 0
    for review in verifier_reviews:
        slots = _reviewer_blocker_slots(review, open_blocker_texts)
        if not slots:
            continue
        verifier_id = str(review.get("verifier_id", "reviewer"))
        safe_verifier = re.sub(r"[^a-zA-Z0-9_.-]+", "_", verifier_id)
        for field, blocker_index in slots:
            event_index += 1
            append_scheduling_update(
                run_dir,
                {
                    "id": f"{event_id_prefix}_{event_index:03d}",
                    "type": "reviewer_objection",
                    "origin_stage": origin_stage,
                    "origin_artifact": origin_artifact,
                    "origin_artifact_id_or_path": (
                        f"{safe_verifier}:{field}:{blocker_index:03d}"
                    ),
                    "locked_claim_id": claim_id,
                    "route_id_or_step_id": route_id,
                    "artifact_revision_or_hash": artifact_hash,
                    "validator_ok": True,
                    "created_at": datetime.now(UTC).isoformat(),
                    "dedupe_key": "canonicalized-by-writer",
                    "applied_at": None,
                    "effect": "scheduling_only",
                    "advisory_only": True,
                },
            )
            created_any = True
    return created_any


def route_initial_reviewer_blockers_to_update_inbox(
    *, run_dir: Path, agent_report: dict, selected_strategy_id: str | None
) -> bool:
    """Persist initial reviewer blocker pointers before building resolved loop state."""

    if not selected_strategy_id or selected_strategy_id == "none":
        return False
    verifier_reviews = agent_report.get("verifier_reviews", [])
    blockers = sorted(
        {
            item
            for review in verifier_reviews
            for item in (
                review.get("objections", [])
                + review.get("missing_hypotheses", [])
                + review.get("source_requests", [])
            )
            if str(item).strip()
        }
    )
    blockers, _ = _split_machine_answered_blockers(
        blockers,
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    created = _append_reviewer_objection_events(
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        route_id=str(selected_strategy_id),
        origin_stage="llm_prover_reviewers",
        origin_artifact="llm_agent_run.yaml",
        event_id_prefix="upd_reviewer_objection_initial",
        verifier_reviews=verifier_reviews,
        open_blocker_texts=set(blockers[:12]),
    )
    if created:
        write_time_allocation_from_update_inbox(run_dir)
    return created


def make_prover_attempt(
    raw: dict,
    *,
    client: LLMClient,
    created_at: str,
    input_artifacts: list[str] | None = None,
) -> dict:
    output_summary, summary_neutralized = safe_prover_text(raw.get("summary", ""))
    # The prover attempt's summary is the ONE sanctioned home for the
    # no-output placeholder (safe_text itself no longer manufactures it, so
    # optional fields elsewhere honestly stay empty/None).
    output_summary = output_summary or NO_AGENT_TEXT
    proposed_steps, steps_neutralized = safe_prover_list(raw.get("steps", []))
    risks, risks_neutralized = safe_prover_list(raw.get("risks", []))
    if summary_neutralized or steps_neutralized or risks_neutralized:
        risks = _append_unique(
            risks,
            [
                (
                    "Adapter neutralized unchecked pressure phrasing before artifact "
                    "commit; treat the affected text as pressure-lane context only."
                )
            ],
        )
    return {
        "agent_id": "llm_prover_001",
        "role": "prover",
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "input_artifacts": list(input_artifacts or INPUT_ARTIFACTS),
        "output_summary": output_summary,
        "proposed_steps": proposed_steps,
        "source_requests": safe_list(raw.get("source_requests", [])),
        "risks": risks,
        "status": "needs_human",
    }


_OBJECTION_KINDS = {
    "missing_source", "hypothesis_gap", "definition_gap", "logical_gap",
    "computation_needed", "counterexample_pressure", "unclear_claim",
    "reviewer_disagreement", "other",
}
_OBJECTION_SEVERITIES = {"P0", "P1", "P2", "P3"}


def _normalize_scoped_objections(
    raw: dict, *, verifier_id: str, target_subgoal_id: str | None
) -> list[dict]:
    """Goal 54B: structured scoped objections for one reviewer review (advisory only).

    1. Explicit well-formed reviewer ``scoped_objections`` (a subgoal/proof_step scope
       must name its target or it is downgraded to ``route`` — never falsely specific).
    2. Otherwise legacy free-text ``risks`` -> objections; bound to this round's target
       as ``inferred_target`` (honestly marked + reasoned) when a subgoal was attacked,
       else a plain ``route`` ``legacy_fallback``.
    Never raises; malformed entries are dropped.
    """

    def _coerce_explicit(entry: dict, idx: int) -> dict | None:
        if not isinstance(entry, dict):
            return None
        text = str(entry.get("text", "")).strip()
        if not text:
            return None
        scope = entry.get("scope")
        if scope not in {"subgoal", "proof_step", "route", "global"}:
            scope = "route"
        subgoal_id = entry.get("subgoal_id")
        proof_step_id = entry.get("proof_step_id")
        if scope == "subgoal" and not subgoal_id:
            scope = "route"
        if scope == "proof_step" and not proof_step_id:
            scope = "route"
        if scope != "subgoal":
            subgoal_id = None
        if scope != "proof_step":
            proof_step_id = None
        return {
            "id": f"{verifier_id}_obj_{idx:03d}",
            "text": text[:600],
            "scope": scope,
            "scope_source": "explicit",
            "scope_inferred": False,
            "inference_reason": "",
            "subgoal_id": subgoal_id,
            "proof_step_id": proof_step_id,
            "kind": entry.get("kind") if entry.get("kind") in _OBJECTION_KINDS else "other",
            "severity": entry.get("severity") if entry.get("severity") in _OBJECTION_SEVERITIES else "P2",
            "suggested_next_action": str(entry.get("suggested_next_action", ""))[:300],
        }

    explicit = raw.get("scoped_objections")
    if isinstance(explicit, list) and explicit:
        return [c for i, e in enumerate(explicit) if (c := _coerce_explicit(e, i)) is not None]

    out: list[dict] = []
    for i, text in enumerate(safe_list(raw.get("risks", []))):
        text = str(text).strip()
        if not text:
            continue
        if target_subgoal_id:
            out.append({
                "id": f"{verifier_id}_obj_{i:03d}", "text": text[:600], "scope": "subgoal",
                "scope_source": "inferred_target", "scope_inferred": True,
                "inference_reason": (
                    "reviewer objection on the round's attacked subgoal; no explicit scope "
                    "was given, bound to the prover round's target_subgoal_id"
                ),
                "subgoal_id": target_subgoal_id, "proof_step_id": None,
                "kind": "other", "severity": "P2", "suggested_next_action": "",
            })
        else:
            out.append({
                "id": f"{verifier_id}_obj_{i:03d}", "text": text[:600], "scope": "route",
                "scope_source": "legacy_fallback", "scope_inferred": False, "inference_reason": "",
                "subgoal_id": None, "proof_step_id": None,
                "kind": "other", "severity": "P2", "suggested_next_action": "",
            })
    return out


def make_verifier_review(
    raw: dict,
    *,
    client: LLMClient,
    created_at: str,
    index: int,
    reviewer_role: str,
    target_subgoal_id: str | None = None,
) -> dict:
    unsafe_language = collect_unsafe_language(raw)
    verdict = normalize_verdict(raw.get("verdict", "needs_revision"))
    action = normalize_action(raw.get("recommended_human_action", "revise"))
    verifier_id = f"{reviewer_role}_{index:03d}"
    return {
        "verifier_id": verifier_id,
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "verdict": verdict,
        "objections": safe_list(raw.get("risks", [])),
        "missing_hypotheses": safe_list(raw.get("missing_hypotheses", [])),
        "source_requests": safe_list(raw.get("source_requests", [])),
        "unsafe_language": unsafe_language,
        "scoped_objections": _normalize_scoped_objections(
            raw, verifier_id=verifier_id, target_subgoal_id=target_subgoal_id
        ),
        "recommended_human_action": action,
        "status": "needs_human" if verdict != "blocked" else "blocked",
    }


def make_human_gate(verifier_reviews: list[dict]) -> dict:
    recommended = "revise"
    if any(review["recommended_human_action"] == "stop" for review in verifier_reviews):
        recommended = "stop"
    elif all(review["recommended_human_action"] == "continue" for review in verifier_reviews):
        recommended = "continue"
    return {
        "gate_id": "human_gate_001",
        "status": "pending",
        "question": (
            "Review the prover route and verifier objections. Choose whether to continue, "
            "revise the claim/route, or stop before any deeper automated work."
        ),
        "allowed_decisions": ["continue", "revise", "stop"],
        "recommended_decision": recommended,
        "decision": None,
        "reason": None,
        "human_confirmed": False,
        "created_at": datetime.now(UTC).isoformat(),
        "resolved_at": None,
    }
