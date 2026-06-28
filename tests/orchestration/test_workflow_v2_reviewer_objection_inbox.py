from __future__ import annotations

import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.orchestrator import _resolve_and_build_loop, orchestrate_run
from espresso_proof.orchestration.prover_loop_driver import continue_prover_loop
from espresso_proof.kernel.update_inbox import load_update_inbox
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run
from tests.kernel.test_evidence_aware_grade import FakeClaude

CLAIM = "Every group of order p^2, where p is a prime, is abelian."


class _ReviewerObjectionClient:
    provider = "stub"
    model = "stub-reviewer-objection"

    def __init__(
        self,
        *,
        objections: list[str],
        source_requests: list[str] | None = None,
        missing_hypotheses: list[str] | None = None,
        verdict: str = "needs_revision",
        reviewer_summary: str = "review",
        prover_summary: str = "candidate route step",
    ) -> None:
        self._objections = objections
        self._source_requests = objections if source_requests is None else source_requests
        self._missing_hypotheses = [] if missing_hypotheses is None else missing_hypotheses
        self._verdict = verdict
        self._reviewer_summary = reviewer_summary
        self._prover_summary = prover_summary

    def complete_json(self, *, system: str, prompt: str) -> dict:
        if "reviewer" in system.lower() or "verifier" in system.lower():
            return {
                "summary": self._reviewer_summary,
                "steps": ["check the route"],
                "source_requests": list(self._source_requests),
                "missing_hypotheses": list(self._missing_hypotheses),
                "risks": list(self._objections),
                "verdict": self._verdict,
                "recommended_human_action": "revise",
            }
        return {
            "summary": self._prover_summary,
            "steps": ["state the route as a conditional argument"],
            "source_requests": ["exact locator for the key lemma"],
            "missing_hypotheses": [],
            "risks": ["candidate route"],
            "verdict": "candidate",
            "recommended_human_action": "revise",
        }


def _write_claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _run_to_resolved_loop(tmp_path: Path) -> tuple[Path, str]:
    result = orchestrate_run(
        project="task06b",
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    selected = brief["top_strategies"][0]["id"]
    human_choice = {
        "claim_id": claim["id"],
        "status": "selected",
        "selected_strategy": selected,
        "effort": "normal_pass",
        "taste_judgment": "",
        "continue_decision": "continue",
        "human_confirmed": True,
    }
    write_yaml(run_dir / artifacts["human_choice"], human_choice)
    _resolve_and_build_loop(
        run_dir,
        decision="continue",
        reason="task06b test",
        human_choice=human_choice,
    )
    validate_run(run_dir)
    return run_dir, selected


def _run_to_gate_with_human_choice(tmp_path: Path) -> tuple[Path, str]:
    result = orchestrate_run(
        project="task06b_cli",
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    selected = brief["top_strategies"][0]["id"]
    write_yaml(
        run_dir / artifacts["human_choice"],
        {
            "claim_id": claim["id"],
            "status": "selected",
            "selected_strategy": selected,
            "effort": "normal_pass",
            "taste_judgment": "",
            "continue_decision": "continue",
            "human_confirmed": True,
        },
    )
    validate_run(run_dir)
    return run_dir, selected


def _bump_budget(run_dir: Path, *, max_rounds: int = 6, max_agent_calls: int = 60) -> None:
    loop_path = run_dir / "prover_loop.yaml"
    loop = read_yaml(loop_path)
    loop["budget"]["max_rounds"] = max_rounds
    loop["budget"]["max_agent_calls"] = max_agent_calls
    write_yaml(loop_path, loop)
    validate_run(run_dir)


def _grade(run_dir: Path) -> dict:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return read_yaml(run_dir / manifest["artifacts"]["idea_grade"])


def _round_keys(run_dir: Path) -> list[tuple[int, str]]:
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    return [(entry["round_index"], entry["loop_attempt_id"]) for entry in index["rounds"]]


def _latest_round_report(run_dir: Path) -> dict:
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    return read_yaml(run_dir / index["rounds"][-1]["artifact_path"])


def test_reviewer_objection_routes_through_update_inbox_and_time_allocation(
    tmp_path: Path,
) -> None:
    run_dir, selected = _run_to_resolved_loop(tmp_path)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(objections=["exact source locator is missing"]),
    )

    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert inbox.entries
    assert {entry.type for entry in inbox.entries} == {"reviewer_objection"}
    assert {entry.route_id_or_step_id for entry in inbox.entries} == {selected}
    assert all(entry.effect == "scheduling_only" for entry in inbox.entries)
    assert all(entry.advisory_only is True for entry in inbox.entries)
    assert all(
        "exact source locator" not in entry.origin_artifact_id_or_path
        for entry in inbox.entries
    )

    allocation = read_yaml(run_dir / "time_allocation.yaml")
    selected_entry = next(entry for entry in allocation["entries"] if entry["route_id"] == selected)
    assert selected_entry["action"] == "repair_attempt"
    assert selected_entry["effect"] == "scheduling_only"
    dependencies = read_yaml(run_dir / "run_manifest.yaml")["dependencies"]
    assert "update_inbox" in dependencies["time_allocation"]["depends_on"]
    assert result["rounds_run"] >= 1


def test_initial_reviewer_blockers_route_to_update_inbox_before_loop(
    tmp_path: Path,
) -> None:
    run_dir, selected = _run_to_resolved_loop(tmp_path)

    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert any(entry.origin_artifact == "llm_agent_run.yaml" for entry in inbox.entries)
    assert {entry.route_id_or_step_id for entry in inbox.entries} == {selected}
    allocation = read_yaml(run_dir / "time_allocation.yaml")
    selected_entry = next(entry for entry in allocation["entries"] if entry["route_id"] == selected)
    assert selected_entry["action"] == "repair_attempt"


def test_cli_resolve_agent_gate_routes_initial_blockers_to_update_inbox(
    tmp_path: Path,
) -> None:
    run_dir, selected = _run_to_gate_with_human_choice(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "continue",
            "--reason",
            "task06b cli parity",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert any(entry.origin_artifact == "llm_agent_run.yaml" for entry in inbox.entries)
    assert {entry.route_id_or_step_id for entry in inbox.entries} == {selected}
    allocation = read_yaml(run_dir / "time_allocation.yaml")
    selected_entry = next(entry for entry in allocation["entries"] if entry["route_id"] == selected)
    assert selected_entry["action"] == "repair_attempt"


@pytest.mark.parametrize(
    ("client_kwargs", "field_name"),
    [
        (
            {
                "objections": [],
                "source_requests": ["source request only blocker"],
                "verdict": "needs_sources",
            },
            "source_requests",
        ),
        (
            {
                "objections": [],
                "source_requests": [],
                "missing_hypotheses": ["missing hypothesis only blocker"],
                "verdict": "needs_revision",
            },
            "missing_hypotheses",
        ),
    ],
)
def test_reviewer_source_and_hypothesis_blockers_route_through_update_inbox(
    tmp_path: Path, client_kwargs: dict, field_name: str
) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)

    continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(**client_kwargs),
    )

    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert any(
        entry.origin_artifact.startswith("prover_rounds/round_002")
        and f":{field_name}:" in entry.origin_artifact_id_or_path
        for entry in inbox.entries
    )


def test_reviewer_objection_does_not_create_unbounded_repair_loop(tmp_path: Path) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)
    _bump_budget(run_dir)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(objections=["same unresolved local gap"]),
    )

    validate_run(run_dir)
    assert result["stop_reason"] in {"no_progress", "time_allocation_ask_human", "budget_rounds"}
    assert result["rounds_run"] < 6
    keys = _round_keys(run_dir)
    assert len(keys) == len(set(keys))


def test_reviewer_pass_or_no_objection_cannot_mark_route_proved(tmp_path: Path) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)
    _bump_budget(run_dir)
    before = _grade(run_dir)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(
            objections=[], verdict="pass", reviewer_summary="no objection"
        ),
    )

    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert all(entry.origin_artifact == "llm_agent_run.yaml" for entry in inbox.entries)
    after = _grade(run_dir)
    assert after["proof_language_status"] == before["proof_language_status"] == "idea_sketch"
    assert after["status"] == before["status"]
    assert result["stop_reason"] == "clean"


def test_reviewer_verified_text_cannot_upgrade_proof_status(tmp_path: Path) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)

    continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(
            objections=["Verified."],
            reviewer_summary="Verified.",
        ),
    )

    validate_run(run_dir)
    grade = _grade(run_dir)
    assert grade["proof_language_status"] == "idea_sketch"
    assert grade["status"] != "promising"
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    raw_inbox = (run_dir / "update_inbox.yaml").read_text(encoding="utf-8").lower()
    assert "verified" not in raw_inbox


@pytest.mark.parametrize(
    ("text", "unsafe_terms"),
    [
        ("source_status: source_backed", ("source_status", "source_backed")),
        ("Checked evidence supports this route.", ("checked evidence",)),
    ],
)
def test_reviewer_reserved_status_language_is_contained_as_unsafe_ledger(
    tmp_path: Path, text: str, unsafe_terms: tuple[str, ...]
) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(objections=[text]),
    )

    assert result["stop_reason"] != "validation_error"
    validate_run(run_dir)
    latest = _latest_round_report(run_dir)
    reviews = latest["verifier_reviews"]
    content_text = " ".join(
        [
            item
            for review in reviews
            for item in (
                review["objections"]
                + review["source_requests"]
                + [obj["text"] for obj in review.get("scoped_objections", [])]
            )
        ]
    ).lower()
    unsafe_text = " ".join(
        phrase for review in reviews for phrase in review["unsafe_language"]
    ).lower()

    assert "[withheld status phrase]" in content_text
    for term in unsafe_terms:
        assert term not in content_text
        assert term in unsafe_text

    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    round_002_entries = [
        entry for entry in inbox.entries if "prover_rounds/round_002" in entry.origin_artifact
    ]
    assert round_002_entries
    assert all(entry.effect == "scheduling_only" for entry in round_002_entries)
    assert all(entry.advisory_only is True for entry in round_002_entries)
    raw_inbox = (run_dir / "update_inbox.yaml").read_text(encoding="utf-8").lower()
    for term in unsafe_terms:
        assert term not in raw_inbox


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("The theorem applicability status is applicable.", "theorem_applicability"),
        ("A counterexample refutes the claim.", "counterexample_refutes_claim"),
    ],
)
def test_reviewer_uncontained_status_language_still_fails_closed(
    tmp_path: Path, text: str, match: str
) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(objections=[text]),
    )

    assert result["stop_reason"] == "validation_error"
    assert any(match in note for note in result["notes"])
    validate_run(run_dir)
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert all(
        not entry.origin_artifact.startswith("prover_rounds/round_002")
        for entry in inbox.entries
    )


def test_repeated_no_progress_stops_or_asks_human(tmp_path: Path) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)
    _bump_budget(run_dir)

    result = continue_prover_loop(
        run_dir=run_dir,
        client=_ReviewerObjectionClient(objections=["same reviewer objection"]),
    )

    validate_run(run_dir)
    assert result["stop_reason"] in {"no_progress", "time_allocation_ask_human"}
    assert result["rounds_run"] >= 1


def test_concurrent_reviewer_objection_continuation_cannot_duplicate_round_id(
    tmp_path: Path,
) -> None:
    run_dir, _ = _run_to_resolved_loop(tmp_path)
    _bump_budget(run_dir)
    errors: list[Exception] = []

    def run_one() -> None:
        try:
            continue_prover_loop(
                run_dir=run_dir,
                client=_ReviewerObjectionClient(objections=["concurrent reviewer objection"]),
            )
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors
    validate_run(run_dir)
    keys = _round_keys(run_dir)
    assert len(keys) == len(set(keys)), keys
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert len(keys) == len(loop["attempts"])
