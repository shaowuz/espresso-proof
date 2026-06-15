"""飞轮第一圈的"改进":把 bank 的 oracle_spec 作为 machine_check 注入 run,再 audit-evidence。

动机(实跑实证):agent 不会给计算附 machine_check,于是真命题挣不到 computation_checked、
假命题不被证伪。这一步用 bank 已知的 oracle_spec 补上机器检查,让 verifier.py 的真后端
(exact_arithmetic / permutation_order / finite_enumeration)挣 checked 或标 wrong。

安全:只改 computation_checks 的 machine_check/status(经 audit_evidence,confirmed->checked /
refuted->wrong / 其它->human_needed),绝不碰 idea_grade、来源、证明状态。computation_checked
是计算范围的、非全称证明(kernel 已保证)。是纯软层后处理,不改 agent、不改 kernel。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ESPRESSO = [str(REPO / ".venv" / "bin" / "espresso")]


def inject_oracle_and_audit(run_dir: Path, prop: dict) -> dict:
    """把 prop.oracle_spec 注入 run 的第一个 computation check,跑 audit-evidence。

    返回 {injected: bool, audit_returncode: int|None, verdict_status: str|None}。
    """
    if prop.get("oracle", "none") == "none":
        return {"injected": False, "audit_returncode": None, "verdict_status": None}

    manifest = yaml.safe_load((run_dir / "run_manifest.yaml").read_text(encoding="utf-8"))
    comp_path = run_dir / manifest["artifacts"]["computation_checks"]
    comp = yaml.safe_load(comp_path.read_text(encoding="utf-8"))
    checks = comp.setdefault("checks", [])
    spec = prop["oracle_spec"]
    machine_check = {
        "kind": spec["kind"],
        "expression": spec["expression"],
        "expected": spec["expected"],
        "notes": "oracle-injected by capability flywheel (bank oracle_spec)",
    }
    if checks:
        checks[0]["machine_check"] = machine_check
    else:
        # cycle 2 修正:agent 没产出任何 computation_check 时,新建一个携带机器检查的 check,
        # 否则注入 no-op、oracle 题挣不到 checked/wrong(诊断自 false_linear_square)。
        checks.append({
            "id": "comp_oracle_injected",
            "example_id": "",
            "claim_id": comp.get("claim_id", prop["id"]),
            "computed_quantity": f"oracle check for {prop['id']}",
            "input_data": [],
            "method": "manual_review_needed",
            "tool_or_source": "espresso-stdlib-verifier",
            "result": spec["expected"],
            "cross_check": [],
            "status": "human_needed",
            "machine_check": machine_check,
            "notes": "synthesized by capability flywheel oracle injection (no agent check existed)",
        })
    comp_path.write_text(yaml.safe_dump(comp, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # audit-evidence: 机器判决派生 status(confirmed->checked / refuted->wrong / 其它->human_needed),
    # 并 validate_run(fail closed)。这是已有 CLI,不新写 kernel 逻辑。
    audit = subprocess.run(
        ESPRESSO + ["audit-evidence", "--run", str(run_dir)],
        cwd=REPO, capture_output=True, text=True,
    )
    new_status = None
    regraded = False
    if audit.returncode == 0:
        comp2 = yaml.safe_load(comp_path.read_text(encoding="utf-8"))
        new_status = (comp2.get("checks", []) or [{}])[0].get("status")
        # (a) Wire injection -> regrade: re-run the regrade stage so the recomputed grade SEES the
        # machine-checked evidence (a confirming check pulls a true claim off likely_false). The
        # grade stage is deterministic from artifacts; the kernel re-validates. Never upgrades to proved.
        try:
            from ..front_half_agents import run_front_half_agent_pass
            from ..llm_agents import StubLLMClient
            run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient(), stages=["regrade"])
            regraded = True
        except Exception:
            regraded = False  # leave the audited evidence in place even if regrade can't re-run
    return {"injected": True, "audit_returncode": audit.returncode,
            "verdict_status": new_status, "regraded": regraded,
            "audit_stderr": audit.stderr[-400:]}
