"""Phase 04 — front-half package split guards.

The implementation moved to the espresso_proof.front_half package; the old
module is a compatibility shim. These tests pin that public imports still work
from both paths, every FRONT_HALF_STAGES entry is registered in order, and the
documented submodules expose their surfaces.
"""

from __future__ import annotations

import importlib


def test_shim_public_imports_still_work() -> None:
    shim = importlib.import_module("espresso_proof.front_half_agents")
    for name in (
        "FRONT_HALF_STAGES",
        "FRONT_HALF_RUN_FILENAME",
        "register_stage",
        "registered_stages",
        "run_front_half_agent_pass",
        "build_front_half_run_report",
        "write_front_half_run",
    ):
        assert hasattr(shim, name), f"shim lost public export {name}"


def test_package_and_shim_agree() -> None:
    from espresso_proof import front_half
    from espresso_proof import front_half_agents as shim

    assert front_half.FRONT_HALF_STAGES == shim.FRONT_HALF_STAGES
    assert front_half.run_front_half_agent_pass is shim.run_front_half_agent_pass
    assert front_half.write_front_half_run is shim.write_front_half_run


def test_every_stage_is_registered_in_order() -> None:
    from espresso_proof.front_half import FRONT_HALF_STAGES, registered_stages

    assert registered_stages() == FRONT_HALF_STAGES
    assert len(FRONT_HALF_STAGES) == len(set(FRONT_HALF_STAGES))


def test_documented_submodules_expose_surfaces() -> None:
    registry = importlib.import_module("espresso_proof.front_half.registry")
    safety = importlib.import_module("espresso_proof.front_half.safety")
    runner = importlib.import_module("espresso_proof.front_half.runner")

    assert hasattr(registry, "register_stage")
    assert hasattr(registry, "FRONT_HALF_STAGES")
    # safety surface is imported, not copied
    assert hasattr(safety, "_safe_lock_route")
    assert hasattr(safety, "_neutralize_review_if_failed")
    assert hasattr(runner, "run_front_half_agent_pass")
    assert hasattr(runner, "write_front_half_run")
