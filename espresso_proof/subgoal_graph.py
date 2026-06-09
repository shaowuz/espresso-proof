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

from .io import read_yaml, write_yaml
from .models import ProofGoalGraph

GRAPH_FILENAME = "proof_goal_graph.yaml"

# How many candidate subgoals (route + blocker derived) to surface. Evidence
# subgoals (earned source / scoped checks) are added on top of this cap because
# they reflect real earned support, not candidate search work.
_MAX_CANDIDATE_SUBGOALS = 4

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


def _scrub(text: Any) -> str:
    """Return ``text`` with any proof-completion language neutralised, so embedding
    an artifact's free text can never make the graph read as a finished proof."""

    value = str(text or "").strip()
    if not value:
        return value
    for token, replacement in _SCRUB_REPLACEMENTS:
        value = re.sub(re.escape(token), replacement, value, flags=re.IGNORECASE)
    return value


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


def _classify_blocker(blocker: str, has_reviewer_objection: bool) -> tuple[str, str]:
    """Return ``(role, state)`` for a blocker-derived subgoal (conservative)."""

    low = blocker.lower()
    role = "hypothesis_check" if any(k in low for k in _HYPOTHESIS_KEYWORDS) else "lemma"
    if any(k in low for k in _SOURCE_KEYWORDS):
        return role, "blocked_missing_source"
    if has_reviewer_objection:
        return role, "blocked_reviewer_objection"
    return role, "open"


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
            role, state = _classify_blocker(str(blocker), has_reviewer_objection)
            if state == "blocked_missing_source":
                contract = "Find and attribute the missing source, or replace it with a local derivation."
            elif state == "blocked_reviewer_objection":
                contract = "Resolve the reviewer objection with a grounded step or a finite/computational check."
            else:
                contract = "Provide a grounded step or a finite/computational check for this subgoal."
            add_child(
                {
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
            )
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
            1
            for node in nodes
            if node["state"] in {"blocked_missing_source", "blocked_reviewer_objection"}
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
    "contradicted": "Contradicted under the current hypotheses",
}

# Order in which to look for the next subgoal to attack (most actionable first).
NEXT_SUBGOAL_PRIORITY = (
    "open",
    "blocked_missing_source",
    "blocked_reviewer_objection",
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


def group_subgoals(graph: dict | None) -> dict[str, Any]:
    """Split a graph dict into the render groups shared by the dashboard + memo.

    Returns ``{"main", "open", "sketched", "blocked", "reference", "scoped",
    "next"}``. ``main`` / ``next`` are single nodes (or ``None``); the rest are
    lists. Pure and read-only; never mutates the graph.
    """
    nodes = (graph or {}).get("nodes") or []
    main = next((n for n in nodes if n.get("role") == "main_goal"), None)
    subgoals = [n for n in nodes if n.get("role") != "main_goal"]

    def by_state(*states: str) -> list[dict[str, Any]]:
        return [n for n in subgoals if n.get("state") in states]

    next_node = None
    for wanted in NEXT_SUBGOAL_PRIORITY:
        match = [n for n in subgoals if n.get("state") == wanted]
        if match:
            next_node = match[0]
            break

    return {
        "main": main,
        "open": by_state("open"),
        "sketched": by_state("sketched"),
        "blocked": by_state("blocked_missing_source", "blocked_reviewer_objection"),
        "reference": by_state("source_supported"),
        "scoped": by_state("scoped_checked"),
        "next": next_node,
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

    lines = ["Proof-search state (a map of the search, not a proof):"]
    main = groups["main"]
    if main is not None:
        lines.append(
            f"  Main goal [{state_words(main.get('state', ''))}]: {short(main.get('statement'))}"
        )

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

    nxt = groups["next"]
    if nxt is not None:
        lines.append(f"  Next subgoal to attack: {short(nxt.get('statement'))}")
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
    "group_subgoals",
    "render_proof_search_lines",
    "state_words",
    "remove_subgoal_graph",
]
