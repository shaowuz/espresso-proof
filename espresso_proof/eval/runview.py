"""能力飞轮 I/O 层:读 espresso run_dir → 算指标 → 分析错误。

这是 loop engineering 缺失的那层(契约见 LAYOUT.md)。所有字段访问对齐 cli.py
inspect 里看到的真实工件结构,全部经 run_manifest 的 artifacts 映射解析,绝不硬编码文件名。

纯读 + 纯计算,不写任何 run、不升任何状态。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from espresso_proof.verification.machine_check_guards import is_non_vacuous_machine_check, source_step_ids


# --------------------------------------------------------------------------- #
# 读:run_dir → RunView(契约 C)
# --------------------------------------------------------------------------- #
@dataclass
class RunView:
    prop_id: str
    run_dir: Path
    manifest: dict
    artifacts: dict = field(default_factory=dict)   # 已解析的工件内容,key=manifest key

    def art(self, key: str) -> dict:
        return self.artifacts.get(key, {})


def _resolve(run_dir: Path, manifest: dict, key: str) -> dict | None:
    """按 manifest 映射(artifacts 优先,再 optional_artifacts)读一个工件。缺失返回 None。"""
    for table in ("artifacts", "optional_artifacts"):
        rel = manifest.get(table, {}).get(key)
        if rel and (run_dir / rel).exists():
            return yaml.safe_load((run_dir / rel).read_text(encoding="utf-8"))
    return None


# 循环关心的工件 key(LAYOUT 契约 C 那张表)
_WANT = (
    "locked_claim", "idea_grade", "review", "source_grounding",
    "computation_checks", "failure_classification", "prover_loop",
)


def read_run(prop_id: str, run_dir: Path) -> RunView:
    manifest = yaml.safe_load((run_dir / "run_manifest.yaml").read_text(encoding="utf-8"))
    arts = {}
    for key in _WANT:
        got = _resolve(run_dir, manifest, key)
        if got is not None:
            arts[key] = got
    return RunView(prop_id=prop_id, run_dir=run_dir, manifest=manifest, artifacts=arts)


# --------------------------------------------------------------------------- #
# 算:RunView → 单题指标(契约 D 的逐题分量)
# --------------------------------------------------------------------------- #
@dataclass
class RunMetrics:
    n_steps: int
    n_refuted: int                  # claim-level wrong checks (not generated scoped refutes)
    n_grounded: int                 # source_backed step + bound, non-vacuous checked computation
    falsification_rate: float
    grounding_coverage: float
    grade_status: str | None        # 用于校准的预测概率映射
    grade_score: int | None
    human_confirmed: bool           # 诚实:必须 False
    proof_language_status: str | None
    irrelevant_confirmed: int = 0   # checked computations excluded by binding/non-vacuity
    self_generated_verdict: str | None = None  # checked|wrong when comp_gen_* earns a scoped verdict


def score_run(view: RunView) -> RunMetrics:
    grounding = view.art("source_grounding")
    comps = view.art("computation_checks")
    grade = view.art("idea_grade")
    claim = view.art("locked_claim")

    steps = grounding.get("steps", [])
    checks = comps.get("checks", [])

    n_steps = max(len(steps), 1)
    step_ids = source_step_ids(grounding)
    n_refuted = sum(1 for c in checks if _claim_level_wrong(c))
    checked_machine_grounded = 0
    irrelevant_confirmed = 0
    generated_checked = False
    generated_wrong = False
    for check in checks:
        status = check.get("status")
        is_generated = _is_generated_check(check)
        bound_non_vacuous = _bound_non_vacuous_machine_check(check, step_ids)
        if status == "wrong" and is_generated:
            if bound_non_vacuous:
                generated_wrong = True
            continue
        if status != "checked":
            continue
        if bound_non_vacuous:
            checked_machine_grounded += 1
            if is_generated:
                generated_checked = True
        else:
            irrelevant_confirmed += 1

    n_grounded = sum(1 for s in steps if s.get("status") == "source_backed") + checked_machine_grounded
    self_generated_verdict = "wrong" if generated_wrong else "checked" if generated_checked else None
    return RunMetrics(
        n_steps=len(steps),
        n_refuted=n_refuted,
        n_grounded=n_grounded,
        falsification_rate=n_refuted / n_steps,
        grounding_coverage=min(n_grounded / n_steps, 1.0),
        grade_status=grade.get("status"),
        grade_score=grade.get("score"),
        human_confirmed=bool(claim.get("human_confirmed")),
        proof_language_status=grade.get("proof_language_status"),
        irrelevant_confirmed=irrelevant_confirmed,
        self_generated_verdict=self_generated_verdict,
    )


def _is_generated_check(check: dict) -> bool:
    return str(check.get("id", "")).startswith("comp_gen_")


def _claim_level_wrong(check: dict) -> bool:
    return check.get("status") == "wrong" and not _is_generated_check(check)


def _bound_non_vacuous_machine_check(check: dict, step_ids: set[str]) -> bool:
    spec = check.get("machine_check") if isinstance(check, dict) else None
    target = spec.get("target_step_id") if isinstance(spec, dict) else None
    bound = isinstance(target, str) and target in step_ids
    non_vacuous = isinstance(spec, dict) and is_non_vacuous_machine_check(spec)
    return bound and non_vacuous


# --------------------------------------------------------------------------- #
# 分析错误:RunView + CLI 结果 → 诊断(契约 E,错误分析的落点)
# --------------------------------------------------------------------------- #
_CATEGORY_TO_DIM = {
    "claim_ambiguous": "claim_precision",
    "proof_gap": "strategy_generation",
    "source_gap": "locator_extraction",
    "theorem_condition_gap": "applicability",
    "computation_unchecked": "verifier_backend",
    "no_progress": "route_death",
}


@dataclass
class Diagnosis:
    prop_id: str
    outcome: str                    # ok|crash|refuted|objection|no_progress|inconclusive
    error_source: str               # cli_stderr|validate_run|computation_checks|review|prover_loop|none
    detail: str
    refuted_steps: list[str]
    failure_categories: list[str]
    gap_dimension: str | None
    run_dir: str


def analyze(
    prop_id: str,
    view: RunView | None,
    cli_returncode: int,
    cli_stderr: str,
    baseline_drifted: bool = False,
) -> Diagnosis:
    """按 LAYOUT 契约 E 的短路顺序解析错误来源。"""
    rd = str(view.run_dir) if view else ""

    # 6. co-run baseline 漂出带 → 整圈作废
    if baseline_drifted:
        return Diagnosis(prop_id, "inconclusive", "none", "co-run baseline drifted out of band",
                         [], [], None, rd)

    # 1. 进程崩溃 → 解析 stderr 关键字
    if cli_returncode != 0:
        if "_validate_human_choice" in cli_stderr or "effort" in cli_stderr.lower():
            detail = "effort_enum_reject: " + _tail(cli_stderr)
        elif "KernelValidationError" in cli_stderr:
            detail = "validate_run: " + _tail(cli_stderr)
        else:
            detail = _tail(cli_stderr)
        src = "validate_run" if "KernelValidationError" in cli_stderr else "cli_stderr"
        return Diagnosis(prop_id, "crash", src, detail, [], [], None, rd)

    if view is None:
        return Diagnosis(prop_id, "crash", "cli_stderr", "no run_dir produced", [], [], None, rd)

    cats = [c.get("category") for c in view.art("failure_classification").get("classifications", [])]
    cats = [c for c in cats if c]
    dim = next((_CATEGORY_TO_DIM[c] for c in cats if c in _CATEGORY_TO_DIM), None)

    # 2. 机器证伪(硬信号优先)
    refuted = [
        c.get("id")
        for c in view.art("computation_checks").get("checks", [])
        if _claim_level_wrong(c)
    ]
    if refuted:
        return Diagnosis(prop_id, "refuted", "computation_checks",
                         f"{len(refuted)} step(s) machine-refuted", refuted, cats,
                         dim or "verifier_backend", rd)

    # 3. reviewer 反对
    review = view.art("review")
    if any(r.get("verdict") == "fail" for r in review.get("reviewers", [])):
        return Diagnosis(prop_id, "objection", "review",
                         _tail(str(review.get("strongest_objection") or "")), [], cats, dim, rd)

    # 4. no_progress / 死路
    loop = view.art("prover_loop")
    if loop.get("loop_decision") in {"stop", "no_progress"} or _no_progress(loop):
        blockers = _latest_blockers(loop)
        return Diagnosis(prop_id, "no_progress", "prover_loop",
                         ", ".join(blockers[:3]), [], cats, dim or "route_death", rd)

    # 5. 干净
    return Diagnosis(prop_id, "ok", "none", "", [], cats, dim, rd)


def _no_progress(loop: dict) -> bool:
    b = loop.get("budget", {})
    return bool(b) and b.get("rounds_used", 0) >= b.get("max_rounds", 1) and not _latest_blockers(loop) == []


def _latest_blockers(loop: dict) -> list[str]:
    attempts = loop.get("attempts", [])
    return attempts[-1].get("blockers", []) if attempts else []


def _tail(s: str, n: int = 300) -> str:
    s = s.strip().replace("\n", " ")
    return s[-n:]


# --------------------------------------------------------------------------- #
# 跑一题考试(契约:固定预算,见计划文档"内层=冻结考试夹具")
# --------------------------------------------------------------------------- #
def run_exam(espresso: list[str], claim_path: Path, project: str, repo: Path,
             env: dict, max_rounds: int) -> tuple[int, str, str]:
    """跑 espresso orchestrate(固定预算)。返回 (returncode, stdout, stderr)。
    解析 run_dir 由调用方从 stdout 'Wrote run artifacts to <dir>' 取。"""
    proc = subprocess.run(
        espresso + ["orchestrate", "--input", str(claim_path), "--project", project,
                    "--provider", "external", "--front-half", "external"],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr
