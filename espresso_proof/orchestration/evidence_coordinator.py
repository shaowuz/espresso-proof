"""Run a batch of on-demand evidence agents (Claude-reconstructs-Espresso-Proof
subgoals 03/05).

This is the EXPLICIT, on-demand batch path — it is NOT called by orchestrate_run.
The fast-core orchestrate pass still defers the evidence agents (they stay
``not_run`` at the human gate; the memo/dashboard say PRELIMINARY). A caller (CLI /
server / a future orchestrator step) invokes this to actually run a set of lanes.

Subgoal 03 (this version): SERIAL — each requested lane is run via the proven
``run_single_agent`` (each writes disjoint artifacts, commits under the
``run_writer_lock``, re-validates the whole run, and converges regardless of order).
Subgoal 05 parallelizes the COMPUTE while keeping the COMMIT serial on the
coordinator thread. Nothing here touches ``current_state``; lane progress lives on
the ``agent_run_status`` board.
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS, run_single_agent

# Deterministic commit order. ``examples`` reconciles ``tests.yaml`` from the
# ``counterexamples`` report and both own ``tests.yaml``, so counterexamples MUST
# commit before examples; literature and failure_patterns own disjoint files and may
# go in any order. This order is what subgoal 05's parallel-compute / serial-commit
# coordinator must also honor for byte-identical convergence.
_COMMIT_ORDER = ["counterexamples", "examples", "literature", "failure_patterns"]


def order_for_commit(agents: set[str]) -> list[str]:
    """Return the requested agents in the deterministic commit order."""
    ordered = [a for a in _COMMIT_ORDER if a in agents]
    ordered += [a for a in sorted(agents) if a not in _COMMIT_ORDER]
    return ordered


def run_evidence_batch(
    run_dir: Path,
    agents: list[str] | None = None,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Run a set of on-demand evidence agents serially; return their statuses.

    ``agents`` defaults to all ON_DEMAND_AGENTS. Each lane fails closed inside
    ``run_single_agent`` (rollback + ``failed`` status) without aborting the batch,
    so one lane's failure never corrupts another's committed artifact.
    """
    run_dir = Path(run_dir)
    requested = list(ON_DEMAND_AGENTS) if agents is None else list(agents)
    unknown = [a for a in requested if a not in ON_DEMAND_AGENTS]
    if unknown:
        raise ValueError(
            f"unknown evidence agents {unknown}; valid on-demand agents: {ON_DEMAND_AGENTS}"
        )

    results: dict[str, dict] = {}
    for agent in order_for_commit(set(requested)):
        results[agent] = run_single_agent(
            run_dir,
            agent,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            client=client,
        )
    return {"agents": results}


__all__ = ["run_evidence_batch", "order_for_commit"]
