"""Subgoal Graph Builder V0 — a durable, advisory proof-SEARCH state layer.

This reads a run's already-validated artifacts and assembles a conservative
graph of mathematical subgoals (``proof_goal_graph.yaml``): what must be shown,
what is open / only sketched, what is reference-supported, what is checked only
in a finite/computational scope, and what is blocked.

The graph is search-management state, NOT proof evidence. It can never set or
upgrade ``idea_grade`` / ``proof_language_status`` / source / evidence / claim
status, never claim a theorem is proved, and never turn reviewer agreement,
human preference, or memory into trusted status. A node may carry the
``source_supported`` / ``scoped_checked`` state ONLY when it references support
that the existing grounding / deterministic-verification gates actually earned
(re-checked by ``validators._validate_subgoal_graph``). The builder fails closed
and is idempotent.

See docs/goals/product_completion/14_SUBGOAL_GRAPH_PROOF_SEARCH_GOAL.md.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .external_tool_runner import run_completed_cleanly
from .io import read_yaml, write_yaml
from .models import ProofGoalGraph

GRAPH_FILENAME = "proof_goal_graph.yaml"

# How many candidate subgoals (route + blocker derived) to surface. Evidence
# subgoals (earned source / scoped checks) are added on top of this cap because
# they reflect real earned support, not candidate search work.
_MAX_CANDIDATE_SUBGOALS = 4
# How many blocked external-tool obligations to surface (Goal 57B).
_MAX_EXTERNAL_TOOL_SUBGOALS = 4
# How many theorem-condition ledger repair subgoals to surface (Goal 57A).
_MAX_LEDGER_SUBGOALS = 4

# Free text copied from other artifacts is scrubbed so the graph can never read
# as a proof-completion claim. Replacements are case-insensitive and applied in
# order; the longer multi-word forms run before the bare words.
_SCRUB_REPLACEMENTS = [
    ("proof-complete", "fully established"),
    ("proof complete", "fully established"),
    ("proof_complete", "fully established"),
    ("proved the theorem", "argued the route for the theorem"),
    ("proves the theorem", "argues the route for the theorem"),
    ("proves the claim", "argues the route for the claim"),
    ("we proved", "we argued"),
    ("proved", "argued"),
    ("proven", "argued"),
    ("verified", "checked-as-stated"),
    ("proof-close", "[end of sketch]"),
]

_SOURCE_KEYWORDS = ("source", "locator", "theorem", "reference", "cite", "citation", "literature")
_HYPOTHESIS_KEYWORDS = ("hypothes", "condition", "genericity", "assumption", "smooth", "scope")
# Goal 57B: typed route-death classification. Phrases are deliberately specific
# so a math word (e.g. "singular variety") does not match a tool blocker.
_EXTERNAL_TOOL_KEYWORDS = (
    "external tool",
    "computer algebra",
    "tool unavailable",
    "tool is unavailable",
    "tool was refused",
    "tool needed",
    "needs an external",
    "macaulay2",
    "sage tool",
    "magma tool",
    "singular package",
    "groebner tool",
)
_FALSE_LEMMA_KEYWORDS = (
    "key lemma",
    "lemma is false",
    "false lemma",
    "is false",
    "does not hold",
    "fails in general",
    "not true in general",
)
_HARD_RESEARCH_KEYWORDS = (
    "open problem",
    "open question",
    "research-level",
    "research level",
    "no known proof",
    "major obstruction",
)
# Set of subgoal-state values counted as "blocked" and grouped together. Mirrors
# models._SUBGOAL_BLOCKED_STATES (kept as plain strings since this module works
# on dicts before model validation).
_BLOCKED_STATE_VALUES = (
    "blocked_missing_source",
    "blocked_reviewer_objection",
    "blocked_external_tool",
    "conditional",
)
# Plain-language next action per block_reason (Goal 57B; never proof language).
_NEXT_ACTION_BY_REASON = {
    "false_key_lemma": (
        "Reject the key lemma; add a hypothesis that excludes the obstruction or "
        "restate the target."
    ),
    "theorem_condition_gap": (
        "List the cited theorem's hypotheses and check each against the claim "
        "before reusing it."
    ),
    "hard_research_blocker": (
        "Queue a decisive experiment or a focused literature search; do not treat "
        "the route as alive yet."
    ),
    "external_tool_blocked": (
        "Make the external tool available and re-run it, or replace it with a "
        "local derivation."
    ),
}


def _scrub(text: Any) -> str:
    """Return ``text`` with any proof-completion language neutralised, so embedding
    an artifact's free text can never make the graph read as a finished proof."""

    value = str(text or "").strip()
    if not value:
        return value
    for token, replacement in _SCRUB_REPLACEMENTS:
        value = re.sub(re.escape(token), replacement, value, flags=re.IGNORECASE)
    return value


def _objection_tag(so: dict) -> str:
    """Goal 54B: render one scoped objection as a single advisory free-text tag for
    a subgoal node. Surfaces the reviewer's repairable instruction
    (``suggested_next_action``) and honestly marks an attribution that was INFERRED
    from the round's attacked subgoal (not explicitly scoped). All text is scrubbed
    so an embedded objection can never read as a finished proof. Never a status."""

    tag = f"[{so.get('severity', 'P2')}] {so.get('kind', 'other')}: {so.get('text', '')}"
    next_action = str(so.get("suggested_next_action", "")).strip()
    if next_action:
        tag += f" → next: {next_action}"
    if so.get("scope_source") == "inferred_target":
        tag += " (inferred from round target)"
    return _scrub(tag)


def _claim_statement(claim: dict) -> str:
    return str(
        claim.get("normalized_statement")
        or claim.get("statement")
        or claim.get("raw_text")
        or "the claim"
    )


def _selected_strategy(human_choice: dict) -> str | None:
    if human_choice.get("status") != "selected":
        return None
    selected = human_choice.get("selected_strategy")
    if not selected or selected == "none":
        return None
    return str(selected)


def _latest_attempt(prover_loop: dict | None) -> dict:
    if not prover_loop:
        return {}
    attempts = prover_loop.get("attempts") or []
    if not attempts:
        return {}
    return max(attempts, key=lambda attempt: attempt.get("round_index", 0))


def _classify_blocker(
    blocker: str, has_reviewer_objection: bool
) -> tuple[str, str, str | None]:
    """Return ``(role, state, block_reason)`` for a blocker-derived subgoal.

    Conservative and advisory: a route-death blocker is typed into a search
    state plus an optional ``block_reason``. None of this earns or refutes any
    status -- it only makes the dead route visible and actionable (Goal 57B).
    """

    low = blocker.lower()
    role = "hypothesis_check" if any(k in low for k in _HYPOTHESIS_KEYWORDS) else "lemma"
    if any(k in low for k in _EXTERNAL_TOOL_KEYWORDS):
        return role, "blocked_external_tool", "external_tool_blocked"
    if any(k in low for k in _FALSE_LEMMA_KEYWORDS):
        return role, "blocked_reviewer_objection", "false_key_lemma"
    if any(k in low for k in _HARD_RESEARCH_KEYWORDS):
        return role, "conditional", "hard_research_blocker"
    if any(k in low for k in _SOURCE_KEYWORDS):
        reason = "theorem_condition_gap" if any(k in low for k in _HYPOTHESIS_KEYWORDS) else None
        return role, "blocked_missing_source", reason
    if has_reviewer_objection:
        return role, "blocked_reviewer_objection", None
    return role, "open", None


def _review_objections(review: dict) -> list[str]:
    objections: list[str] = []
    strongest = (review.get("strongest_objection") or "").strip()
    if strongest and strongest.lower() != "none recorded":
        objections.append(_scrub(strongest))
    for reviewer in review.get("reviewers") or []:
        for objection in reviewer.get("objections") or []:
            scrubbed = _scrub(objection)
            if scrubbed and scrubbed not in objections:
                objections.append(scrubbed)
    return objections[:5]


def _scoped_objection_reviews(run_dir: Path, llm_agent_run: dict | None) -> list[dict]:
    """Every verifier review that can carry a subgoal-scoped objection: the
    gate-time ``llm_agent_run`` plus every continuation round file. Round files are
    where the inferred-target subgoal objections live (gate-time reviews are
    route-scoped before any subgoal exists), so the Goal-54B attach must read both
    or a round's subgoal objection never reaches its node."""
    reviews: list[dict] = list((llm_agent_run or {}).get("verifier_reviews") or [])
    rounds = Path(run_dir) / "prover_rounds"
    if rounds.is_dir():
        for round_path in sorted(rounds.glob("round_*.yaml")):
            try:
                data = read_yaml(round_path)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                reviews.extend(data.get("verifier_reviews") or [])
    return reviews


def assemble_subgoal_graph(run_dir: Path) -> dict:
    """Read a run's validated artifacts and return a graph dict (no I/O).

    Only reads; never mutates artifacts and never sets any trusted status. The
    returned dict carries a ``generated_at`` placeholder that ``build_subgoal_graph``
    finalises for idempotency.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    def optional_artifact(key: str) -> dict | None:
        path = optional.get(key)
        if path and (run_dir / path).exists():
            return read_yaml(run_dir / path)
        return None

    claim = artifact("locked_claim")
    strategy_brief = artifact("strategy_brief")
    human_choice = artifact("human_choice")
    source_grounding = artifact("source_grounding")
    review = artifact("review")
    idea_grade = artifact("idea_grade")
    prover_loop = optional_artifact("prover_loop")
    proof_verification = optional_artifact("proof_verification")
    computation_verification = optional_artifact("computation_verification")
    llm_agent_run = optional_artifact("llm_agent_run")  # Goal 54B: scoped reviewer objections
    external_tool_runs = optional_artifact("external_tool_runs")  # Goal 57B: blocked tool obligations
    theorem_condition_ledger = optional_artifact("theorem_condition_ledger")  # Goal 57A: named repair subgoals

    strategies = {
        str(strategy.get("id")): strategy
        for strategy in strategy_brief.get("top_strategies") or []
    }
    selected = _selected_strategy(human_choice)
    claim_refuted = idea_grade.get("status") == "refuted"

    nodes: list[dict] = []
    notes: list[str] = []
    child_ids: list[str] = []

    def add_child(node: dict) -> None:
        nodes.append(node)
        child_ids.append(node["id"])

    # --- main goal -----------------------------------------------------------
    main = {
        "id": "subgoal_main",
        "statement": _scrub("Show: " + _claim_statement(claim)),
        "role": "main_goal",
        "state": "contradicted" if claim_refuted else "open",
        "closure_contract": (
            "A fully source-grounded or finite/computational argument for the whole "
            "claim, confirmed by a human at the major gate."
        ),
        "depends_on": [],
        "reviewer_objections": _review_objections(review),
        "counterexample_pressure": [
            _scrub(item) for item in (idea_grade.get("counterexample_candidates") or [])[:5]
        ],
        "remaining_risk": "",
    }
    nodes.append(main)

    if claim_refuted:
        main["remaining_risk"] = (
            "The kernel's refutation logic rejected the claim under the current hypotheses."
        )
        # Goal 26: explain why no "next subgoal" is offered on a refuted claim, so a
        # reader is not left with leftover subgoal lists and no next action.
        notes.append(
            "The claim is refuted by an admissible checked counterexample, so there is "
            "no next subgoal to work on; any subgoals below are historical context."
        )
    elif selected is None:
        main["remaining_risk"] = "Choose a route first before deeper proof search."
        notes.append(
            "No strategy route is selected yet; this graph has one open main goal. "
            "Choose a route first."
        )
    else:
        main["remaining_risk"] = (
            "The whole claim is not established; it depends on the open subgoals below."
        )

    # --- candidate subgoals from the selected route + prover blockers --------
    candidate_budget = _MAX_CANDIDATE_SUBGOALS
    if selected is not None:
        route = strategies.get(selected) or {}
        route_text = route.get("next_loop_plan") or route.get("title") or "Carry out the selected route."
        add_child(
            {
                "id": "subgoal_route",
                "statement": _scrub(route_text),
                "role": "reduction",
                "state": "sketched",
                "closure_contract": (
                    "Turn this route into grounded steps or a finite/computational check, "
                    "then a human review."
                ),
                "depends_on": [],
                "reviewer_objections": [],
                "counterexample_pressure": [],
                "remaining_risk": _scrub(route.get("main_risk"))
                or "A route is only a sketch until its steps are grounded or checked.",
            }
        )
        candidate_budget -= 1

        attempt = _latest_attempt(prover_loop)
        has_reviewer_objection = bool(attempt.get("reviewer_objections"))
        for index, blocker in enumerate(attempt.get("blockers") or [], start=1):
            if candidate_budget <= 0:
                notes.append("Some prover blockers are not shown (subgoal display is capped).")
                break
            role, state, block_reason = _classify_blocker(str(blocker), has_reviewer_objection)
            if state == "blocked_missing_source":
                contract = "Find and attribute the missing source, or replace it with a local derivation."
            elif state == "blocked_reviewer_objection":
                contract = "Resolve the reviewer objection with a grounded step or a finite/computational check."
            elif state == "blocked_external_tool":
                contract = "Make the external tool available and re-run it, or replace it with a local derivation."
            elif state == "conditional":
                contract = "Resolve the conditional gap with a decisive experiment or a grounded step before relying on this route."
            else:
                contract = "Provide a grounded step or a finite/computational check for this subgoal."
            block_node = {
                "id": f"subgoal_block_{index:03d}",
                "statement": _scrub("Resolve: " + str(blocker)),
                "role": role,
                "state": state,
                "closure_contract": contract,
                "depends_on": [],
                "reviewer_objections": [],
                "counterexample_pressure": [],
                "remaining_risk": _scrub(blocker),
            }
            if block_reason is not None:
                block_node["block_reason"] = block_reason
                block_node["suggested_next_action"] = _NEXT_ACTION_BY_REASON.get(block_reason, "")
            add_child(block_node)
            candidate_budget -= 1

    # --- evidence subgoals (only where the kernel already earned support) -----
    for step in source_grounding.get("steps") or []:
        if step.get("status") != "source_backed":
            continue
        step_id = str(step.get("proof_step_id"))
        add_child(
            {
                "id": f"subgoal_src_{step_id}",
                "statement": _scrub("Reference support: " + str(step.get("needed_theorem", step_id))),
                "role": "reference_check",
                "state": "source_supported",
                "closure_contract": "A cited result for this step; still not a proof of the whole claim.",
                "depends_on": [],
                "supported_by_source_steps": [step_id],
                "reviewer_objections": [],
                "counterexample_pressure": [],
                "remaining_risk": "Supports only this step as a reference, not the whole claim.",
            }
        )

    if proof_verification is not None:
        for entry in proof_verification.get("entries") or []:
            if not (entry.get("trusted") and entry.get("confirmed")):
                continue
            if entry.get("earned_status") not in {"instance_checked", "computation_checked"}:
                continue
            entry_id = str(entry.get("id"))
            add_child(
                {
                    "id": f"subgoal_pv_{entry_id}",
                    "statement": _scrub("Finite/computational check: " + str(entry.get("target", entry_id))),
                    "role": "computation_check",
                    "state": "scoped_checked",
                    "closure_contract": "Confirmed only in this finite/computational scope.",
                    "depends_on": [],
                    "supported_by_computations": [entry_id],
                    "reviewer_objections": [],
                    "counterexample_pressure": [],
                    "remaining_risk": "Checked only in this finite or computational scope, not the universal claim.",
                }
            )

    if computation_verification is not None:
        for result in computation_verification.get("results") or []:
            if not (result.get("executed") and result.get("verdict") == "confirmed"):
                continue
            check_id = str(result.get("check_id"))
            add_child(
                {
                    "id": f"subgoal_cv_{check_id}",
                    "statement": _scrub("Finite/computational check: " + str(result.get("kind", check_id))),
                    "role": "computation_check",
                    "state": "scoped_checked",
                    "closure_contract": "Confirmed only in this finite/computational scope.",
                    "depends_on": [],
                    "supported_by_scoped_checks": [check_id],
                    "reviewer_objections": [],
                    "counterexample_pressure": [],
                    "remaining_risk": "Checked only in this finite or computational scope, not the universal claim.",
                }
            )

    # --- Goal 57B: blocked external-tool obligations -------------------------
    # A tool run that did not complete cleanly (unavailable / refused / errored /
    # timed out) becomes a durable blocked node so the dead route is visible on a
    # re-inspect. A tool run is raw evidence: a non-clean run NEVER earns status,
    # it only surfaces an actionable blocker.
    if external_tool_runs is not None:
        tool_budget = _MAX_EXTERNAL_TOOL_SUBGOALS
        for run in external_tool_runs.get("runs") or []:
            if tool_budget <= 0:
                notes.append("Some blocked external-tool runs are not shown (display is capped).")
                break
            if run_completed_cleanly(bool(run.get("timed_out")), run.get("exit_code")):
                continue
            tool = str(run.get("tool_name") or "external tool")
            run_key = re.sub(r"[^A-Za-z0-9_]+", "_", str(run.get("id") or tool))
            add_child(
                {
                    "id": f"subgoal_tool_{run_key}",
                    "statement": _scrub(f"Blocked external tool: {tool}"),
                    "role": "computation_check",
                    "state": "blocked_external_tool",
                    "closure_contract": _NEXT_ACTION_BY_REASON["external_tool_blocked"],
                    "depends_on": [],
                    "reviewer_objections": [],
                    "counterexample_pressure": [],
                    "remaining_risk": _scrub(
                        run.get("scope_note")
                        or f"{tool} did not complete cleanly; no status earned."
                    ),
                    "block_reason": "external_tool_blocked",
                    "suggested_next_action": _NEXT_ACTION_BY_REASON["external_tool_blocked"],
                }
            )
            tool_budget -= 1

    # --- Goal 57A: theorem-condition ledger repair subgoals ------------------
    # The ledger is the SINGLE WRITER of theorem-condition repair nodes: their ids
    # come from the ledger's ``repair_subgoal_id`` (``subgoal_tcl_*``), disjoint
    # from 57B's ``subgoal_block_*`` blocker nodes, so the two paths never create a
    # duplicate node for the same obligation. A ledger node is advisory SEARCH
    # state only: it never carries ``supported_by_*`` (so it can never earn
    # source_backed/checked), never sets/refutes any status. ``match_status`` is
    # consumed verbatim from the ledger (which never infers it from claim text).
    if theorem_condition_ledger is not None:
        ledger_budget = _MAX_LEDGER_SUBGOALS
        for entry in theorem_condition_ledger.get("entries") or []:
            if ledger_budget <= 0:
                notes.append(
                    "Some cited-theorem repair subgoals are not shown (display is capped)."
                )
                break
            conditions = entry.get("conditions") or []
            unmatched = sum(1 for c in conditions if c.get("match_status") == "unmatched")
            unknown = sum(1 for c in conditions if c.get("match_status") == "unknown")
            if unmatched:
                risk = (
                    f"{unmatched} of {len(conditions)} cited-theorem condition(s) do not "
                    "match the claim; the rest still need checking."
                )
            else:
                risk = (
                    f"{unknown} of {len(conditions)} cited-theorem condition(s) are still "
                    "unchecked against the claim."
                )
            ledger_node = {
                "id": str(entry["repair_subgoal_id"]),
                "statement": _scrub("Check the cited theorem: " + str(entry["requested_theorem"])),
                "role": "hypothesis_check",
                # Any unmatched condition => a real theorem-condition gap to repair;
                # otherwise the hypotheses simply still need checking (open target).
                "state": "blocked_missing_source" if unmatched else "open",
                "closure_contract": (
                    "List this theorem's hypotheses and match each against the claim; "
                    "find an exact locator before reusing it."
                ),
                "depends_on": [],
                "reviewer_objections": [],
                "counterexample_pressure": [],
                "remaining_risk": risk,
            }
            if unmatched:
                ledger_node["block_reason"] = "theorem_condition_gap"
                ledger_node["suggested_next_action"] = _NEXT_ACTION_BY_REASON[
                    "theorem_condition_gap"
                ]
            add_child(ledger_node)
            ledger_budget -= 1

    # --- Goal 54B: attach subgoal-scoped reviewer objections to matching nodes ---
    # Attach-if-matches: an objection naming a node that exists gets placed on it;
    # one naming a stale/unknown subgoal_id is simply not placed (never a fabricated
    # node, never a state change). Dedup keeps rebuilds idempotent. An objection can
    # only block/prioritize repair — it never closes or upgrades a subgoal.
    # ``proof_step``-scoped objections are provenance-only for now (no proof_step
    # node exists to attach to); only ``subgoal`` scope is rendered downstream.
    # Reviews come from the gate-time ``llm_agent_run`` (absent on refuted/early
    # runs) AND every continuation round file, since a round's inferred-target
    # subgoal objection lives there, not in the gate-time route-scoped review.
    objections_by_subgoal: dict[str, list[str]] = {}
    for review in _scoped_objection_reviews(run_dir, llm_agent_run):
        if not isinstance(review, dict):
            continue
        for so in review.get("scoped_objections") or []:
            if isinstance(so, dict) and so.get("scope") == "subgoal" and so.get("subgoal_id"):
                objections_by_subgoal.setdefault(str(so["subgoal_id"]), []).append(_objection_tag(so))
    if objections_by_subgoal:
        node_by_id = {node["id"]: node for node in nodes}
        for sid, texts in objections_by_subgoal.items():
            node = node_by_id.get(sid)
            if node is None:
                continue
            existing = node.setdefault("reviewer_objections", [])
            for tag in texts:
                if tag not in existing:
                    existing.append(tag)

    # --- wire dependencies: every subgoal is needed for the main goal --------
    main["depends_on"] = list(child_ids)
    edges = [{"from": child_id, "to": "subgoal_main", "relation": "needed_for"} for child_id in child_ids]

    summary = {
        "open": sum(1 for node in nodes if node["state"] == "open"),
        "sketched": sum(1 for node in nodes if node["state"] == "sketched"),
        "source_supported": sum(1 for node in nodes if node["state"] == "source_supported"),
        "finite_or_computation_checked": sum(
            1 for node in nodes if node["state"] == "scoped_checked"
        ),
        "blocked": sum(
            1 for node in nodes if node["state"] in _BLOCKED_STATE_VALUES
        ),
    }

    return {
        "schema_version": 1,
        "claim_id": str(claim.get("id")),
        "run_id": str(manifest.get("run_id")),
        "generated_at": "",  # finalised by build_subgoal_graph
        "selected_strategy": selected,
        "generated_from": {
            "strategy_brief": artifacts.get("strategy_brief"),
            "prover_loop": optional.get("prover_loop"),
            "source_grounding": artifacts.get("source_grounding"),
        },
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
        "notes": notes,
    }


def build_subgoal_graph(run_dir: Path) -> dict:
    """Assemble, validate, and write ``proof_goal_graph.yaml``; register it as an
    optional artifact and return the graph dict.

    Idempotent: if an equivalent graph already exists (everything but
    ``generated_at``), the previous timestamp is preserved so a re-build leaves
    the artifact byte-identical.
    """

    run_dir = Path(run_dir)
    graph = assemble_subgoal_graph(run_dir)

    graph_path = run_dir / GRAPH_FILENAME
    generated_at = datetime.now(UTC).isoformat()
    if graph_path.exists():
        try:
            existing = read_yaml(graph_path)
        except Exception:  # noqa: BLE001 - a corrupt prior file is simply replaced
            existing = None
        if isinstance(existing, dict):
            previous_stamp = existing.get("generated_at")
            if {**existing, "generated_at": ""} == {**graph, "generated_at": ""} and previous_stamp:
                generated_at = str(previous_stamp)
    graph["generated_at"] = generated_at

    # Fail closed on a malformed graph before touching the run.
    ProofGoalGraph.model_validate(graph)

    write_yaml(graph_path, graph)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["proof_goal_graph"] = GRAPH_FILENAME
    write_yaml(manifest_path, manifest)
    return graph


# ---------------------------------------------------------------------------
# Human-readable rendering (single source of truth for the dashboard + memo).
#
# These map internal subgoal-state enums to plain mathematician-facing wording.
# Raw enum names must never reach a user-facing surface. The renderer is
# read-only and advisory: it describes a proof SEARCH, never claims a proof.
# ---------------------------------------------------------------------------

# Human wording for each internal subgoal state.
STATE_WORDING: dict[str, str] = {
    "open": "Open",
    "sketched": "Only sketched",
    "source_supported": "Supported by a cited reference",
    "scoped_checked": "Checked only in a finite/computational scope",
    "blocked_missing_source": "Blocked by a missing source",
    "blocked_reviewer_objection": "Blocked by a reviewer objection",
    "blocked_external_tool": "Blocked: needs an external tool",
    "conditional": "Conditional on an unresolved gap",
    "contradicted": "Contradicted under the current hypotheses",
}

# Plain-language wording for each block_reason (Goal 57B). Raw enum values must
# never reach a user-facing surface; renderers translate through this map.
BLOCK_REASON_WORDING: dict[str, str] = {
    "false_key_lemma": "A key step looks false",
    "theorem_condition_gap": "A cited theorem's hypotheses are not met",
    "hard_research_blocker": "A research-level obstruction",
    "external_tool_blocked": "An external tool is unavailable",
}

# Order in which to look for the next subgoal to attack (most actionable first).
NEXT_SUBGOAL_PRIORITY = (
    "open",
    "blocked_missing_source",
    "blocked_reviewer_objection",
    "blocked_external_tool",
    "conditional",
    "sketched",
)

# The advisory disclaimer shown on every proof-search rendering.
PROOF_SEARCH_NOTE = (
    "This is a proof-search map, not a proof. It does not claim the theorem is "
    "checked, and references support only their own step."
)


def state_words(state: str) -> str:
    """Plain wording for an internal subgoal state (fail-safe default)."""
    return STATE_WORDING.get(state, "In progress")


def block_reason_words(reason: str | None) -> str:
    """Plain wording for a block_reason (Goal 57B; fail-safe default).

    Raw enum values must never reach a user-facing surface; renderers translate
    every block_reason through this map.
    """
    return BLOCK_REASON_WORDING.get(reason or "", "Blocked")


def graph_notes(graph: dict | None) -> list[str]:
    """Return the graph's display notes (non-empty, stripped strings).

    Notes are advisory display text that tell the reader how to interpret the
    shown subgoals — e.g. that a blocked list was capped, or that no route is
    selected yet. They are NEVER proof/status; they only prevent a shortened
    view from being misread as the complete proof-search state.
    """
    notes = (graph or {}).get("notes") or []
    return [str(n).strip() for n in notes if str(n).strip()]


def select_next_subgoal(graph: dict | None) -> dict[str, Any] | None:
    """Deterministically pick the single highest-value OPEN subgoal to work on next
    (Goal 26: the graph is the scheduling center). Pure, read-only scheduling: it
    NEVER sets or upgrades any status -- it only chooses where to spend effort.

    Priority is the fixed ``NEXT_SUBGOAL_PRIORITY`` order (open > blocked-missing-
    source > blocked-reviewer-objection > sketched); within a state, the first
    node in graph order wins, so selection is stable/deterministic.

    Refutation dominates: if the main goal is already ``contradicted`` (the claim
    was refuted by an admissible checked counterexample, the Goal-21 hard gate),
    there is nothing to "prove", so no subgoal is selected -- stale route optimism
    cannot override a claim-level checked refutation.
    """
    nodes = (graph or {}).get("nodes") or []
    main = next((n for n in nodes if n.get("role") == "main_goal"), None)
    if main is not None and main.get("state") == "contradicted":
        return None
    subgoals = [n for n in nodes if n.get("role") != "main_goal"]
    for wanted in NEXT_SUBGOAL_PRIORITY:
        for node in subgoals:
            if node.get("state") == wanted:
                return node
    return None


def group_subgoals(graph: dict | None) -> dict[str, Any]:
    """Split a graph dict into the render groups shared by the dashboard + memo.

    Returns ``{"main", "open", "sketched", "blocked", "reference", "scoped",
    "next"}``. ``main`` / ``next`` are single nodes (or ``None``); the rest are
    lists. ``next`` is ``select_next_subgoal`` (single source of truth). Pure and
    read-only; never mutates the graph.
    """
    nodes = (graph or {}).get("nodes") or []
    main = next((n for n in nodes if n.get("role") == "main_goal"), None)
    subgoals = [n for n in nodes if n.get("role") != "main_goal"]

    def by_state(*states: str) -> list[dict[str, Any]]:
        return [n for n in subgoals if n.get("state") in states]

    return {
        "main": main,
        "open": by_state("open"),
        "sketched": by_state("sketched"),
        "blocked": by_state(*_BLOCKED_STATE_VALUES),
        "reference": by_state("source_supported"),
        "scoped": by_state("scoped_checked"),
        "next": select_next_subgoal(graph),
    }


def render_proof_search_lines(graph: dict | None, *, statement_limit: int = 200) -> list[str]:
    """Render the proof-search state as plain memo text lines.

    Returns ``[]`` when the graph has no nodes, so callers can simply skip an
    empty section. Read-only and advisory: it only describes the search map and
    never emits proof-complete language.
    """
    if not (graph or {}).get("nodes"):
        return []
    groups = group_subgoals(graph)

    def short(text: Any) -> str:
        text = " ".join(str(text or "").split())
        if len(text) > statement_limit:
            return text[: statement_limit - 1].rstrip() + "…"
        return text

    # Goal 54B: a node may carry scoped reviewer objections attached at build time.
    # Echo them (advisory free text only) under the node they name, so the memo
    # shows *which* subgoal a reviewer objected to — never a status change.
    def objection_lines(node: dict, indent: str) -> list[str]:
        out: list[str] = []
        for objection in (node.get("reviewer_objections") or [])[:3]:
            text = short(objection)
            if text:
                out.append(f"{indent}· reviewer objection: {text}")
        return out

    # Goal 57B: a blocked/conditional node may carry a typed reason and a plain
    # suggested next step. Both are translated to human wording (never the raw
    # enum) so the memo reads as a next-action row, not a status change.
    def block_lines(node: dict, indent: str) -> list[str]:
        out: list[str] = []
        reason = node.get("block_reason")
        if reason:
            out.append(f"{indent}· why blocked: {block_reason_words(reason)}")
        action = short(node.get("suggested_next_action"))
        if action:
            out.append(f"{indent}· suggested next step: {action}")
        return out

    lines = ["Proof-search state (a map of the search, not a proof):"]
    main = groups["main"]
    if main is not None:
        lines.append(
            f"  Main goal [{state_words(main.get('state', ''))}]: {short(main.get('statement'))}"
        )
        lines.extend(objection_lines(main, "      "))

    for label, key in (
        ("Open subgoals", "open"),
        ("Only sketched", "sketched"),
        ("Blocked subgoals", "blocked"),
        ("Supported by reference (not a proof of the claim)", "reference"),
        ("Checked only in a finite/computational scope", "scoped"),
    ):
        rows = groups[key]
        if not rows:
            continue
        lines.append(f"  {label}:")
        for node in rows:
            lines.append(
                f"    - {short(node.get('statement'))} [{state_words(node.get('state', ''))}]"
            )
            if key == "blocked":
                lines.extend(block_lines(node, "        "))
            lines.extend(objection_lines(node, "        "))

    nxt = groups["next"]
    if nxt is not None:
        lines.append(
            f"  Next subgoal to work on [{nxt.get('id', 'subgoal')}]: "
            f"{short(nxt.get('statement'))} ({state_words(nxt.get('state', ''))})"
        )

    # Display notes (how to read the list above) — shown only when present, and
    # never truncated so a "list was capped" caveat is fully visible.
    notes = graph_notes(graph)
    if notes:
        lines.append("  About this proof-search view:")
        for note in notes:
            lines.append(f"    - {note}")

    lines.append(f"  Note: {PROOF_SEARCH_NOTE}")
    return lines


def remove_subgoal_graph(run_dir: Path) -> None:
    """Drop the graph artifact + its manifest registration (used to fail closed
    if a freshly built graph fails the full run validation)."""

    run_dir = Path(run_dir)
    graph_path = run_dir / GRAPH_FILENAME
    if graph_path.exists():
        graph_path.unlink()
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    optional = manifest.get("optional_artifacts", {})
    if "proof_goal_graph" in optional:
        del optional["proof_goal_graph"]
        write_yaml(manifest_path, manifest)


__all__ = [
    "GRAPH_FILENAME",
    "STATE_WORDING",
    "NEXT_SUBGOAL_PRIORITY",
    "PROOF_SEARCH_NOTE",
    "assemble_subgoal_graph",
    "build_subgoal_graph",
    "graph_notes",
    "group_subgoals",
    "render_proof_search_lines",
    "state_words",
    "remove_subgoal_graph",
]
