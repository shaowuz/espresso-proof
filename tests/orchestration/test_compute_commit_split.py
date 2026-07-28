"""Compute/commit split acceptance.

Proves the two load-bearing invariants of the split:

1. ``compute_agent`` is observationally PURE w.r.t. ``run_dir`` — it creates,
   deletes, or modifies NO file (content + mtime). This is what lets the fan-out
   run the four lanes' ``compute()`` in parallel OFF the writer lock.
2. ``compute_agent`` + ``run_single_agent(candidate=...)`` (the split path)
   reproduces the inline ``run_single_agent`` evidence artifacts BYTE-FOR-BYTE.
   So the split is a refactor, not a behavior change.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from espresso_proof.kernel.agent_run_status import agent_status, evidence_ready
from espresso_proof.orchestration.agent_runner import (
    AgentCandidate,
    ON_DEMAND_AGENTS,
    compute_agent,
    run_single_agent,
)
from espresso_proof.orchestration.evidence_coordinator import order_for_commit
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."

# Evidence artifacts the split actually produces — these must be byte-identical
# between the inline path and the compute/commit path. The board / pressure /
# manifest carry timestamps and are written by shared code in BOTH paths, so they
# are checked structurally (status + evidence_ready) rather than byte-wise.
_EVIDENCE_FILES = [
    "examples.yaml",
    "computation_checks.yaml",
    "counterexamples.yaml",
    "tests.yaml",
    "literature_review.yaml",
]

# failure_pattern_audit.yaml stamps a wall-clock ``generated_at`` (the compute
# runs a few ms before the commit), so compare it with that one field normalized;
# every math field must still match exactly.
_TIMESTAMPED_FILES = ["failure_pattern_audit.yaml"]


def _fast_run(tmp_path: Path, name: str = "p2") -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project=name,
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return Path(result["run_dir"])


def _fingerprint(run_dir: Path) -> dict[str, tuple[int, int, str]]:
    """relpath -> (size, mtime_ns, sha256) for every file under run_dir."""
    fp: dict[str, tuple[int, int, str]] = {}
    for p in sorted(run_dir.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            st = p.stat()
            fp[str(p.relative_to(run_dir))] = (
                st.st_size,
                st.st_mtime_ns,
                hashlib.sha256(data).hexdigest(),
            )
    return fp


def test_compute_leaves_run_dir_untouched(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    for agent in ON_DEMAND_AGENTS:
        before = _fingerprint(run_dir)
        cand = compute_agent(run_dir, agent, provider="stub")

        # compute() is a pure read: no file created, deleted, or modified (the
        # mtime_ns catch is what proves an atomic temp+rename did NOT happen).
        after = _fingerprint(run_dir)
        assert after == before, f"compute_agent({agent!r}) mutated run_dir"
        # no stray temp file left behind
        assert not list(run_dir.rglob("*.tmp")), f"{agent}: leftover .tmp file"

        # it really did the work, in memory
        assert isinstance(cand, AgentCandidate)
        assert cand.agent == agent
        assert cand.artifacts, f"{agent}: empty candidate"


def test_compute_commit_equals_inline(tmp_path: Path) -> None:
    base = _fast_run(tmp_path)
    # The kernel pins the run-dir basename to the manifest run_id, so copy the
    # whole project tree (preserving .../<project>/runs/<run_id>) into two roots
    # rather than renaming the leaf. Both copies share the run_id, so their
    # evidence artifacts are directly comparable.
    proj = base.parents[1]  # <tmp>/<project>
    shutil.copytree(proj, tmp_path / "A" / proj.name)
    shutil.copytree(proj, tmp_path / "B" / proj.name)
    inline_dir = tmp_path / "A" / proj.name / "runs" / base.name
    split_dir = tmp_path / "B" / proj.name / "runs" / base.name

    order = order_for_commit(set(ON_DEMAND_AGENTS))

    # A) inline path: compute+write in one locked step
    for agent in order:
        out = run_single_agent(inline_dir, agent, provider="stub")
        assert out["status"] == "done", f"inline {agent}: {out}"

    # B) split path: pure compute OFF lock, then commit the candidate under lock
    for agent in order:
        cand = compute_agent(split_dir, agent, provider="stub")
        out = run_single_agent(split_dir, agent, provider="stub", candidate=cand)
        assert out["status"] == "done", f"split {agent}: {out}"

    # evidence artifacts must be byte-identical
    for name in _EVIDENCE_FILES:
        a = inline_dir / name
        b = split_dir / name
        assert a.exists() == b.exists(), f"{name}: presence differs"
        if a.exists():
            assert a.read_bytes() == b.read_bytes(), f"{name}: bytes differ between inline and split"

    # timestamped artifacts: every math field identical, only generated_at differs
    from espresso_proof.kernel.io import read_yaml

    for name in _TIMESTAMPED_FILES:
        a = inline_dir / name
        b = split_dir / name
        assert a.exists() == b.exists(), f"{name}: presence differs"
        if a.exists():
            da = read_yaml(a)
            db = read_yaml(b)
            da.pop("generated_at", None)
            db.pop("generated_at", None)
            assert da == db, f"{name}: math content differs between inline and split"

    # both runs are kernel-valid and converge to the same evidence state
    validate_run(inline_dir)
    validate_run(split_dir)
    for agent in ON_DEMAND_AGENTS:
        assert agent_status(inline_dir, agent) == "done"
        assert agent_status(split_dir, agent) == "done"
    for agent in ("examples", "counterexamples"):
        assert evidence_ready(inline_dir, agent) == evidence_ready(split_dir, agent)

    # the failure_patterns commit must register its optional artifact in BOTH
    # paths (the candidate path relies on persist_failure_pattern_audit doing it)
    from espresso_proof.kernel.io import read_yaml as _read_yaml

    for d in (inline_dir, split_dir):
        manifest = _read_yaml(d / "run_manifest.yaml")
        assert (
            manifest.get("optional_artifacts", {}).get("failure_pattern_audit")
            == "failure_pattern_audit.yaml"
        )


def test_candidate_agent_mismatch_rejected(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    cand = compute_agent(run_dir, "literature", provider="stub")
    # committing a literature candidate under a different agent name must fail fast
    import pytest

    with pytest.raises(ValueError):
        run_single_agent(run_dir, "failure_patterns", provider="stub", candidate=cand)


def test_stale_candidate_fails_closed_and_rolls_back(tmp_path: Path) -> None:
    # A candidate computed against a now-invalid run must not silently commit:
    # the in-transaction validate_run is still the gate. We simulate staleness by
    # corrupting the candidate's artifact so the committed run fails validation.
    run_dir = _fast_run(tmp_path)
    cand = compute_agent(run_dir, "literature", provider="stub")
    broken = AgentCandidate(
        agent="literature",
        artifacts={"literature_review": {"not": "a valid literature report"}},
    )
    out = run_single_agent(run_dir, "literature", provider="stub", candidate=broken)
    assert out["status"] == "failed"
    # the run is still valid (rolled back), and literature did not become evidence
    validate_run(run_dir)
    assert agent_status(run_dir, "literature") == "failed"
