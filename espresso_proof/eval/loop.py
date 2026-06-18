"""能力飞轮:跑一圈(固定预算考试 → 打分 → 诊断 → 写记录)+ replay 验收。

一圈 = 对命题库每题跑一次"冻结固定预算"的 espresso 考试(R 次重复),读 run_dir,
算 RunMetrics + 诊断错误,聚合成 scorecard.json,逐题写 diagnoses.jsonl。
"改机器"那一步是人/外部插入的(写 change.json),loop 只负责考试+打分+诊断+裁决。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from .runview import RunView, analyze, read_run, score_run
from .scorecard import (
    PropOutcome, attach_noise_band, build_scorecard, write_scorecard,
)
from espresso_proof.verification.verifier import audit_evidence

REPO = Path(__file__).resolve().parents[2]
ESPRESSO = [str(REPO / ".venv" / "bin" / "espresso")]
EXTERNAL_AGENT = str(REPO / "scripts" / "espresso_claude_agent.py")

# S0:每个策略的允许 effort 集 = [它自己的 estimated_effort](front_half_agents.py:1029,
# 经 human_gate 进 validators._validate_human_choice 的 choices[strategy]).因此必须把所选策略
# 的 estimated_effort 原样传给 --effort,绝不能重映射(remap 到别的合法值会落在该策略允许集之外而崩)。
_VALID_EFFORT = {"quick_test", "normal_pass", "deep_exploration", "long_watch"}


def _env() -> dict:
    return {**os.environ, "ESPRESSO_EXTERNAL_AGENT_COMMAND": EXTERNAL_AGENT}


def load_bank(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 一题一次固定预算考试
# --------------------------------------------------------------------------- #
def run_exam(prop: dict, project: str, cycle_dir: Path, max_rounds: int,
             provider: str = "external", front_half: str = "external") -> dict:
    """跑 fresh orchestrate(停在 human gate)→ resume 固定 rounds。
    返回 {returncode, stderr, run_dir|None}。provider=stub 用于确定性烟雾测试。"""
    claim = cycle_dir / "claims" / f"{prop['id']}.md"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(prop["statement"] + "\n", encoding="utf-8")

    fresh = subprocess.run(
        ESPRESSO + ["orchestrate", "--input", str(claim), "--project", project,
                    "--provider", provider, "--front-half", front_half],
        cwd=REPO, capture_output=True, text=True, env=_env(),
    )
    if fresh.returncode != 0:
        return {"returncode": fresh.returncode, "stderr": fresh.stderr, "run_dir": None}
    m = re.search(r"Wrote run artifacts to (\S+)", fresh.stdout)
    if not m:
        return {"returncode": 1, "stderr": "no run_dir in stdout\n" + fresh.stdout, "run_dir": None}
    run_dir = REPO / m.group(1)

    # 选 top strategy + 映射 effort(S0 修正),固定 rounds 跑有界 prover loop
    manifest = yaml.safe_load((run_dir / "run_manifest.yaml").read_text(encoding="utf-8"))
    brief = yaml.safe_load((run_dir / manifest["artifacts"]["strategy_brief"]).read_text(encoding="utf-8"))
    tops = brief.get("top_strategies", [])
    strat = tops[0]["id"] if tops else "none"
    # 用所选策略自己的 estimated_effort(= 它的唯一允许 effort);不在合法枚举内才退到 normal_pass
    raw_effort = tops[0].get("estimated_effort", "normal_pass") if tops else "normal_pass"
    effort = raw_effort if raw_effort in _VALID_EFFORT else "normal_pass"

    resume = subprocess.run(
        ESPRESSO + ["orchestrate", "--resume", str(run_dir),
                    "--select-strategy", strat, "--effort", effort,
                    "--rounds", str(max_rounds), "--provider", provider],
        cwd=REPO, capture_output=True, text=True, env=_env(),
    )
    if resume.returncode == 0:
        try:
            audit_evidence(run_dir)
        except Exception as exc:  # noqa: BLE001 - eval run fails closed on audit breakage
            return {
                "returncode": 1,
                "stderr": resume.stderr + f"\naudit_evidence failed: {exc}",
                "run_dir": run_dir,
            }
    return {"returncode": resume.returncode, "stderr": resume.stderr, "run_dir": run_dir}


def _final_report_text(run_dir: Path) -> str:
    for name in ("final_report.md", "memo.md"):
        p = run_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
    return ""


def _outcome_for(prop: dict, exam: dict, baseline_drifted: bool) -> tuple[PropOutcome | None, dict]:
    """把一次考试变成 (PropOutcome|None, diagnosis dict)。"""
    run_dir = exam["run_dir"]
    view: RunView | None = read_run(prop["id"], run_dir) if run_dir else None
    diag = analyze(prop["id"], view, exam["returncode"], exam["stderr"], baseline_drifted)
    if view is None:
        return None, diag.__dict__
    outcome = PropOutcome(
        prop_id=prop["id"],
        known_truth=prop.get("known_truth", "open"),
        has_oracle=prop.get("oracle", "none") != "none",
        metrics=score_run(view),
        final_report_text=_final_report_text(run_dir),
    )
    return outcome, diag.__dict__


# --------------------------------------------------------------------------- #
# 跑一圈
# --------------------------------------------------------------------------- #
def run_cycle(*, campaign_id: str, cycle: int, bank: list[dict], split: str,
              cycle_dir: Path, repeats: int = 5, max_rounds: int = 4,
              baseline_band: dict | None = None) -> dict:
    """跑 R 次重复 × 全题,写 diagnoses.jsonl + scorecard.json,返回 scorecard。"""
    cycle_dir.mkdir(parents=True, exist_ok=True)
    props = [p for p in bank if p.get("split", split) == split]

    all_repeats: list[list[PropOutcome]] = []
    diag_path = cycle_dir / "diagnoses.jsonl"
    diag_lines: list[str] = []

    for r in range(repeats):
        outcomes: list[PropOutcome] = []
        for prop in props:
            exam = run_exam(prop, project=f"{campaign_id}_c{cycle}", cycle_dir=cycle_dir,
                            max_rounds=max_rounds)
            outcome, diag = _outcome_for(prop, exam, baseline_drifted=False)
            diag["repeat"] = r
            diag_lines.append(json.dumps(diag, ensure_ascii=False))
            if outcome is not None:
                outcomes.append(outcome)
        all_repeats.append(outcomes)

    diag_path.write_text("\n".join(diag_lines) + "\n", encoding="utf-8")

    model = {"prover": os.environ.get("ESPRESSO_MODEL_FINGERPRINT", "external:unpinned"),
             "judge": None}
    scorecard = build_scorecard(campaign_id=campaign_id, cycle=cycle, split=split,
                                model=model, repeats=all_repeats)
    if cycle == 0 and baseline_band is None:
        scorecard = attach_noise_band(scorecard)
    elif baseline_band is not None:
        scorecard["noise_band"] = baseline_band
    write_scorecard(cycle_dir / "scorecard.json", scorecard)
    return scorecard


# --------------------------------------------------------------------------- #
# REPLAY 验收谓词(计划文档的代码化)
# --------------------------------------------------------------------------- #
# 方向:True=越大越好,False=越小越好。raw falsification_rate 是混合量,不参与裁决。
# 证伪按真值拆:false_positive_rate(证伪真命题,守卫,越低越好)/ true_positive_rate(证伪假命题,越高越好)
_DIRECTION = {
    "self_generated_confirm_rate": True,
    "grounding_coverage": True, "oracle_accuracy": True, "true_positive_rate": True,
    "false_positive_rate": False, "calibration_brier": False, "calibration_ece": False,
    "irrelevant_confirmed": False,
}
_PRIMARY = (
    "self_generated_confirm_rate",
    "grounding_coverage",
    "oracle_accuracy",
    "true_positive_rate",
)


def replay_decision(baseline: dict, candidate: dict, min_abs_delta: float = 0.02) -> dict:
    """接受 ⇔ 某非LLM主导指标超带改善 ∧ 无指标超带回退 ∧ 诚实违规=0。
    仅看非LLM指标;judge 指标不参与(judge-only delta 不可能让这里通过)。"""
    band = baseline.get("noise_band", {})
    reasons: list[str] = []

    # 硬门:诚实
    if candidate["metrics"].get("honesty_violations", 0) != 0:
        return {"accept": False, "reasons": ["honesty_violations != 0 (hard gate)"]}
    false_positive = candidate["metrics"].get("false_positive_rate", {}).get("mean")
    if false_positive not in (None, 0, 0.0):
        return {"accept": False, "reasons": ["false_positive_rate != 0 (hard gate)"]}

    improved, regressed = [], []
    for key, higher_better in _DIRECTION.items():
        b = baseline["metrics"].get(key, {}).get("mean")
        c = candidate["metrics"].get(key, {}).get("mean")
        if b is None or c is None:
            continue
        delta = c - b
        good = delta if higher_better else -delta            # >0 表示朝好方向
        threshold = max(band.get(key) or 0.0, min_abs_delta)
        if good > threshold and key in _PRIMARY:
            improved.append(f"{key}: {b:.3f}->{c:.3f} (>band {threshold:.3f})")
        elif good < -threshold:
            regressed.append(f"{key}: {b:.3f}->{c:.3f} (regressed >band {threshold:.3f})")

    if regressed:
        reasons.append("regression beyond band: " + "; ".join(regressed))
        return {"accept": False, "reasons": reasons, "improved": improved}
    if not improved:
        reasons.append("no primary metric improved beyond noise band")
        return {"accept": False, "reasons": reasons}

    return {"accept": True, "reasons": ["accepted"], "improved": improved}


def write_replay_decision(cycle_dir: Path, decision: dict) -> None:
    (cycle_dir / "replay_decision.json").write_text(
        json.dumps(decision, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 断路器(计划文档 RE8 的最小子集)
# --------------------------------------------------------------------------- #
def circuit_breakers(scorecard: dict, baseline: dict) -> list[str]:
    trips = []
    if scorecard["metrics"].get("honesty_violations", 0) != 0:
        trips.append("honesty_violations>0")
    fpr = scorecard["metrics"].get("false_positive_rate", {}).get("mean")
    if fpr not in (None, 0, 0.0):
        trips.append("false_positive_rate>0")
    joa = scorecard["metrics"].get("judge_oracle_agreement")
    floor = float(os.environ.get("ESPRESSO_JOA_FLOOR", "0.7"))
    if joa is not None and joa < floor:
        trips.append(f"judge_oracle_agreement<{floor}")
    return trips


if __name__ == "__main__":
    import sys
    bank_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "benchmarks" / "capability_v0.yaml"
    campaign = sys.argv[2] if len(sys.argv) > 2 else "cap_v0"
    out = REPO / "eval_runs" / campaign
    sc = run_cycle(campaign_id=campaign, cycle=0, bank=load_bank(bank_path), split="dev",
                   cycle_dir=out / "cycle_000_baseline",
                   repeats=int(os.environ.get("ESPRESSO_REPEATS", "5")))
    print(json.dumps(sc, indent=2, ensure_ascii=False))
