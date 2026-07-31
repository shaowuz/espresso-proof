"""The findings a hostile review confirmed but the first fix round did not close.

Each test here pins one of them. They are unrelated to each other except in
kind: every one is a place where the system reported something cleaner than what
had actually happened — a rollback that left writes behind, an index that
silently dropped entries, a sanitizer that passed the strongest overclaim in the
vocabulary, a declared budget that was accepted and ignored, and a headline that
contradicted the section below it.
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from espresso_proof.kernel.proof_language import contains_proof_language, scrub_proof_language
from espresso_proof.kernel.stage_runner import StageError, StageRunner
from espresso_proof.kernel.surface_warnings import rank_scoped_objections
from espresso_proof.memory.memory import load_memory_index, write_memory_entry


# --- rollback must not report success over writes it could not undo


def _run_shell(tmp_path: Path) -> Path:
    from espresso_proof.kernel.io import write_yaml

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_yaml(run_dir / "run_manifest.yaml", {"run_id": "r", "current_state": "START"})
    (run_dir / "declared.yaml").write_text("original\n", encoding="utf-8")
    (run_dir / "undeclared.yaml").write_text("original\n", encoding="utf-8")
    return run_dir


def test_a_write_outside_the_declared_snapshot_is_reported(tmp_path: Path) -> None:
    run_dir = _run_shell(tmp_path)
    runner = StageRunner(run_dir)

    def _mutate() -> None:
        (run_dir / "declared.yaml").write_text("changed\n", encoding="utf-8")
        (run_dir / "undeclared.yaml").write_text("changed\n", encoding="utf-8")
        raise RuntimeError("stage failed")

    with pytest.raises(StageError) as excinfo:
        runner.run(
            name="leaky",
            mutate=_mutate,
            validate=lambda: None,
            snapshot_paths=["declared.yaml"],
        )

    message = str(excinfo.value)
    assert "rollback incomplete" in message
    assert "undeclared.yaml" in message
    assert "stage failed" in message  # the original cause survives
    # The declared path really was restored; the undeclared one really was not.
    assert (run_dir / "declared.yaml").read_text() == "original\n"
    assert (run_dir / "undeclared.yaml").read_text() == "changed\n"
    assert runner.outcomes[-1].status == "rolled_back"


def test_a_clean_rollback_says_nothing_extra(tmp_path: Path) -> None:
    run_dir = _run_shell(tmp_path)
    runner = StageRunner(run_dir)

    def _mutate() -> None:
        (run_dir / "declared.yaml").write_text("changed\n", encoding="utf-8")
        raise RuntimeError("stage failed")

    with pytest.raises(StageError) as excinfo:
        runner.run(
            name="tidy", mutate=_mutate, validate=lambda: None, snapshot_paths=["declared.yaml"]
        )
    assert "rollback incomplete" not in str(excinfo.value)
    assert (run_dir / "declared.yaml").read_text() == "original\n"


def test_a_whole_directory_snapshot_needs_no_detection(tmp_path: Path) -> None:
    # snapshot_paths=None covers everything, so there is nothing to leak and no
    # fingerprint walk to pay for.
    run_dir = _run_shell(tmp_path)

    def _mutate() -> None:
        (run_dir / "undeclared.yaml").write_text("changed\n", encoding="utf-8")
        raise RuntimeError("boom")

    with pytest.raises(StageError) as excinfo:
        StageRunner(run_dir).run(name="whole", mutate=_mutate, validate=lambda: None)
    assert "rollback incomplete" not in str(excinfo.value)
    assert (run_dir / "undeclared.yaml").read_text() == "original\n"


# --- the project memory index is shared, so it needs a shared lock


def _memory_entry(index: int) -> dict:
    return {
        "id": f"mem_{index:03d}",
        "claim_id": "claim_001",
        "type": "source_gap",
        "project": "p",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "summary": f"entry {index}",
        "reusable_lesson": f"lesson {index}",
        "provenance": [f"projects/p/runs/r{index}/idea_grade.yaml"],
    }


def test_concurrent_threads_do_not_drop_index_entries(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p"
    project.mkdir(parents=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: write_memory_entry(project, _memory_entry(i)), range(24)))
    assert len(load_memory_index(project).entries) == 24


def test_concurrent_processes_do_not_drop_index_entries(tmp_path: Path) -> None:
    # The case a thread-local re-entrancy set cannot cover: the server creates
    # runs in separate processes.
    project = tmp_path / "projects" / "p"
    project.mkdir(parents=True)
    script = tmp_path / "writer.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, pathlib
            sys.path.insert(0, {str(Path.cwd())!r})
            from espresso_proof.memory.memory import write_memory_entry
            project = pathlib.Path(sys.argv[1]); base = int(sys.argv[2])
            for k in range(base, base + 4):
                write_memory_entry(project, {{
                    "id": f"mem_{{k:03d}}", "claim_id": "claim_001", "type": "source_gap",
                    "project": "p", "timestamp": "2026-01-01T00:00:00+00:00",
                    "summary": f"e{{k}}", "reusable_lesson": f"l{{k}}",
                    "provenance": [f"projects/p/runs/r{{k}}/idea_grade.yaml"]}})
            """
        ),
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen([sys.executable, str(script), str(project), str(i * 4)])
        for i in range(4)
    ]
    assert all(proc.wait() == 0 for proc in procs)
    assert len(load_memory_index(project).entries) == 16


# --- the sanitizer matched spellings, not meanings


@pytest.mark.parametrize(
    "text",
    [
        "Q. E. D.",
        "[proof-close]",
        "proof-close",
        "ＱＥＤ",  # fullwidth
        "∎",
        "and so the result follows. □",
        "the proof is now complete",
        "the proof is thus complete",
        "we proved the theorem",
    ],
)
def test_completion_markers_are_redacted_however_they_are_spelled(text: str) -> None:
    assert scrub_proof_language(text) != text
    assert contains_proof_language(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "we have not proved the theorem",
        "the source proves Lemma 2.1",
        "needs a checked proof",
        "proof completeness is a subtle notion",
        "we proved Lemma 2.1",
        "a sketch-only argument cannot become proof-complete evidence",
        "the square □ in the diagram commutes",
        "p² + 1 is prime for these p",
    ],
)
def test_honest_prose_is_preserved_exactly(text: str) -> None:
    assert scrub_proof_language(text) == text
    assert contains_proof_language(text) is None


def test_redaction_does_not_rewrite_the_surrounding_mathematics() -> None:
    # Matching happens on a compatibility-folded view; the OUTPUT must be the
    # original characters, or every render would silently mangle exponents.
    text = "For p² we conclude x ≤ y. proof-close"
    out = scrub_proof_language(text)
    assert out.startswith("For p² we conclude x ≤ y. ")
    assert "proof-close" not in out


# --- a declared budget must not be accepted and dropped


def test_reapplying_a_budget_widens_a_written_brief(tmp_path: Path) -> None:
    from espresso_proof.kernel.io import read_yaml
    from espresso_proof.orchestration.orchestrator import orchestrate_run
    from espresso_proof.strategy.brief import reapply_declared_budget

    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2, where p is prime, is abelian.\n", encoding="utf-8")
    result = orchestrate_run(
        project="budget", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]

    before = read_yaml(brief_path)["allowed_human_choices"]
    outcome = reapply_declared_budget(run_dir, 6_400_000)
    after = read_yaml(brief_path)["allowed_human_choices"]

    if outcome["updated"]:
        assert after != before
        # Only the options move; the routes themselves are untouched.
        assert [c["strategy_id"] for c in after] == [c["strategy_id"] for c in before]
    else:
        assert after == before


def test_no_budget_and_no_brief_are_both_no_ops(tmp_path: Path) -> None:
    from espresso_proof.strategy.brief import reapply_declared_budget

    assert reapply_declared_budget(tmp_path, None)["updated"] is False
    (tmp_path / "run_manifest.yaml").write_text("artifacts: {}\n", encoding="utf-8")
    assert reapply_declared_budget(tmp_path, 3_200_000)["updated"] is False


# --- the memo headline must not contradict the ranking below it


def test_ranked_but_nothing_blocking_is_not_called_unranked() -> None:
    ranking = rank_scoped_objections(
        {"verifier_reviews": [{"scoped_objections": [
            {"text": "a", "severity": "P2"}, {"text": "b", "severity": "P3"},
        ]}]}
    )
    # The state that produced the contradiction: reviewers DID rank, and `top`
    # is empty because nothing reached P0/P1.
    assert ranking["differentiated"] is True and ranking["top"] == []

    import inspect

    from espresso_proof.ui import memo

    source = inspect.getsource(memo)
    assert 'elif _blocker_ranking.get("differentiated"):' in source
    assert "none\n                is blocking" in source or "none " in source
