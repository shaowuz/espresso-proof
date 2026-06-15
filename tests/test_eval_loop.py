from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from espresso_proof.eval import loop


def _seed_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.yaml").write_text(
        yaml.safe_dump({"artifacts": {"strategy_brief": "strategy_brief.yaml"}}),
        encoding="utf-8",
    )
    (run_dir / "strategy_brief.yaml").write_text(
        yaml.safe_dump(
            {
                "top_strategies": [
                    {
                        "id": "strategy_1",
                        "estimated_effort": "quick_test",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_eval_run_exam_audits_evidence_after_successful_resume(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir = _seed_run(tmp_path)
    calls = {"subprocess": 0, "audited": []}

    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN202 - pytest stub
        calls["subprocess"] += 1
        if calls["subprocess"] == 1:
            return SimpleNamespace(
                returncode=0,
                stdout="Wrote run artifacts to runs/r1\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_audit(path: Path) -> dict:
        calls["audited"].append(path)
        return {"checks": []}

    monkeypatch.setattr(loop, "REPO", tmp_path)
    monkeypatch.setattr(loop.subprocess, "run", fake_run)
    monkeypatch.setattr(loop, "audit_evidence", fake_audit)

    result = loop.run_exam(
        {"id": "p1", "statement": "2+2=4"},
        project="proj",
        cycle_dir=tmp_path / "cycle",
        max_rounds=1,
    )

    assert result["returncode"] == 0
    assert result["run_dir"] == run_dir
    assert calls["audited"] == [run_dir]


def test_eval_run_exam_fails_closed_when_audit_fails(tmp_path: Path, monkeypatch) -> None:
    _seed_run(tmp_path)
    calls = {"subprocess": 0}

    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN202 - pytest stub
        calls["subprocess"] += 1
        if calls["subprocess"] == 1:
            return SimpleNamespace(
                returncode=0,
                stdout="Wrote run artifacts to runs/r1\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="resume stderr")

    def fake_audit(path: Path) -> dict:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(loop, "REPO", tmp_path)
    monkeypatch.setattr(loop.subprocess, "run", fake_run)
    monkeypatch.setattr(loop, "audit_evidence", fake_audit)

    result = loop.run_exam(
        {"id": "p1", "statement": "2+2=4"},
        project="proj",
        cycle_dir=tmp_path / "cycle",
        max_rounds=1,
    )

    assert result["returncode"] == 1
    assert "audit_evidence failed: audit broke" in result["stderr"]
