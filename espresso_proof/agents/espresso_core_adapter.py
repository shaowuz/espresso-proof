#!/usr/bin/env python3
"""Espresso external-agent adapter that uses espresso-core as the STRONG prover
backend for prover-family roles, and delegates every other role to the normal
external adapter.

Wire it up (console script, or the absolute module path):

    export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-core-agent"
    # or: ESPRESSO_EXTERNAL_AGENT_COMMAND="/ABS/PATH/espresso_proof/agents/espresso_core_adapter.py"
    export ESPRESSO_CORE_MODE=mock                 # off | mock | live (off = pure passthrough)
    export ESPRESSO_CORE_FIXTURE_RUN=/abs/core-run # mock mode: a captured espresso-core run dir
    export ESPRESSO_CORE_FALLBACK_AGENT_COMMAND="uv run espresso-codex-agent (or claude adapter)"
    espresso run-prover-loop-round --run <run_dir> --provider external

Contract (espresso_proof.agents.llm_agents.ExternalCommandClient):
- reads ONE JSON request on stdin: {system, prompt, provider, model, required_json_schema}
- prints ONE JSON object on stdout.

Role routing (see docs/engineering/espresso-core-integration.md):
- prover role            -> espresso-core run  -> AGENT_OUTPUT_SCHEMA translation
- line-numbered writer   -> espresso-core run  -> LINE_NUMBERED_PROOF_OUTPUT_SCHEMA translation
- EVERYTHING ELSE (front-half agents, the three loop reviewers, the consensus
  reviewers, gap resolver, diagnostics) -> ESPRESSO_CORE_FALLBACK_AGENT_COMMAND,
  stdin/stdout passthrough, so reviewer independence from the prover backend is
  preserved and the consensus seal never reviews its own author.

Trust posture: espresso-core's internal "proved" status is an LLM verifier
quorum, NOT machine verification. This adapter therefore emits CANDIDATE
language only: core "proved" maps to verdict "plausible_direction" plus
quorum-accepted-candidate wording, never anything stronger. The espresso_proof
kernel re-validates everything (make_prover_attempt pins status needs_human;
LineNumberedProof.status is pinned advisory; the consensus seal is the only
status-upgrade path). On any error the adapter prints a conservative fallback
object instead of crashing the round.

No kernel edits; provider stays "external".
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # PyYAML is a hard dependency of espresso_proof itself.
    import yaml
except Exception:  # noqa: BLE001 - the adapter must still emit fallback JSON
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Environment contract (all opt-in; default is pure passthrough)
# ---------------------------------------------------------------------------
MODE_ENV = "ESPRESSO_CORE_MODE"  # off | mock | live
FIXTURE_RUN_ENV = "ESPRESSO_CORE_FIXTURE_RUN"  # mock: captured core run dir
CORE_ROOT_ENV = "ESPRESSO_CORE_ROOT"  # live: espresso-core source checkout
CORE_CLI_ENV = "ESPRESSO_CORE_CLI"  # live: espresso-core executable
CORE_CONFIG_ENV = "ESPRESSO_CORE_CONFIG"  # live: host-authored config yaml
CORE_RUNS_DIR_ENV = "ESPRESSO_CORE_RUNS_DIR"  # live: parent dir for run dirs
CORE_TIMEOUT_ENV = "ESPRESSO_CORE_TIMEOUT_SECONDS"  # live: solve timeout
FALLBACK_COMMAND_ENV = "ESPRESSO_CORE_FALLBACK_AGENT_COMMAND"
FALLBACK_TIMEOUT_ENV = "ESPRESSO_CORE_FALLBACK_TIMEOUT_SECONDS"

DEFAULT_CORE_TIMEOUT_SECONDS = 3600
DEFAULT_FALLBACK_TIMEOUT_SECONDS = 300

_DIAGNOSTIC_META_KEY = "_espresso_core_adapter"

_FALLBACK = {
    "summary": "No usable espresso-core backend output was produced.",
    "steps": [],
    "source_requests": [],
    "missing_hypotheses": [],
    "risks": [],
    "verdict": "needs_revision",
    "recommended_human_action": "revise",
}

# Core status vocabulary -> candidate-only vocabulary. NEVER emit the raw core
# words: "proved"/"verified" are on the kernel's FORBIDDEN_LANGUAGE list and
# core "proved" only means an LLM quorum agreed.
_CANDIDATE_WORDING = {
    "proved": "quorum-accepted candidate (LLM reviewer quorum only, unchecked here)",
    "partial": "partially developed candidate",
    "failed": "failed candidate attempt",
    "needs_human": "stopped awaiting a human decision",
}
_VERDICT_BY_CORE_STATUS = {
    "proved": "plausible_direction",  # NEVER stronger than this
    "partial": "needs_revision",
    "failed": "needs_revision",
    "needs_human": "blocked",
}
_ACTION_BY_CORE_STATUS = {
    "proved": "continue",
    "partial": "revise",
    "failed": "revise",
    "needs_human": "stop",
}

_STANDING_RISK = (
    "espresso-core acceptance is an LLM reviewer quorum plus optional review "
    "panel, not machine checking; every imported step remains an unchecked "
    "candidate until the espresso_proof kernel and its human gates decide."
)

# Local last-resort neutralizer, used only if the kernel's own scrubbers are not
# importable. Word-boundary replacement of completion vocabulary.
_LOCAL_FORBIDDEN = (
    "proved", "proven", "proves", "verified", "proof-close", "complete proof",
    "proof complete", "source_backed", "source-backed", "proof_status",
    "mock_checked", "mock checked",
)


def _local_neutralize(text: str) -> str:
    out = re.sub(r"\s+", " ", str(text)).strip()
    for phrase in sorted(_LOCAL_FORBIDDEN, key=len, reverse=True):
        pattern = re.escape(phrase).replace(r"\ ", r"\s+")
        out = re.sub(
            rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])",
            "[withheld phrase]",
            out,
            flags=re.IGNORECASE,
        )
    return out[:1200]


def _safe(text: object) -> str:
    """Neutralize proof-complete / status-smuggle language using the kernel's own
    scrubber when importable (same convention as the claude adapter)."""
    raw = str(text)
    try:
        from espresso_proof.agents.llm_agents import safe_text

        return safe_text(raw)
    except Exception:  # noqa: BLE001 - never block on the kernel being importable
        return _local_neutralize(raw)


def _safe_statement(text: object) -> str:
    """Gentler scrub for proof-line statements: keep the math, drop status prose."""
    try:
        from espresso_proof.orchestration.advisory_proof_sketch import scrub_sketch_text

        scrubbed = scrub_sketch_text(str(text))
    except Exception:  # noqa: BLE001
        scrubbed = _local_neutralize(str(text))
    scrubbed = scrubbed.strip()[:600]
    return scrubbed or "[withheld candidate statement]"


# ---------------------------------------------------------------------------
# Role detection (modeled on codex_adapter._uses_generic_agent_output markers,
# but anchored on the top-level "role" field agent_prompt() always embeds).
# ---------------------------------------------------------------------------
def _prompt_role(prompt: str) -> str:
    """The authoritative role string: agent_prompt() serializes
    {"role": <role>, "protocol_rules": ..., "context": ...}. Reviewer prompts
    CONTAIN a nested prover attempt ('"role": "prover"' inside context), so we
    must read the top-level field, not substring-match the whole prompt."""
    try:
        payload = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("role", ""))
    return ""


def detect_role(*, system: str, prompt: str) -> str:
    """Return 'prover' | 'proof_writer' | 'other'."""
    role = _prompt_role(prompt)
    if role in {"prover", "prover_loop_round"}:
        return "prover"
    if role == "line_numbered_proof_writer":
        return "proof_writer"
    if role:
        return "other"
    lowered = f"{system}\n{prompt}".lower()
    if "line-numbered proof writer" in lowered:
        return "proof_writer"
    # Reviewer / consensus / gap-resolver systems must NEVER route to core
    # (marker list mirrors codex_adapter._uses_generic_agent_output).
    for marker in (
        "reviewer agent",
        "verifier agent",
        "strategy reviewer",
        "evidence reviewer",
        "line proof reviewer",
        "proof-consensus review",
        "proof gap resolver",
        "external agent diagnostic",
    ):
        if marker in lowered:
            return "other"
    if "prover agent" in lowered or "prover_loop_round" in lowered or '"role": "prover"' in lowered:
        return "prover"
    return "other"


# ---------------------------------------------------------------------------
# espresso-core input bundle (problem markdown) from the espresso_proof prompt
# payload. Section headings are the ones ClaimLockEngine parses (claim_lock.py):
# "Claim" (required), "Human strategy", "Known results", "Constraints",
# "Forbidden shortcuts", "Sources or references".
# ---------------------------------------------------------------------------
def build_problem_markdown(prompt: str) -> str:
    payload = json.loads(prompt)
    context = payload.get("context", {}) if isinstance(payload, dict) else {}
    claim = context.get("claim") or {}
    claim_text = str(
        claim.get("normalized_statement") or claim.get("raw_text") or ""
    ).strip()
    if not claim_text:
        raise ValueError("prompt payload has no locked claim text (context.claim)")

    sections = [f"## Claim\n\n{claim_text}"]

    strategy_text = _selected_strategy_text(context)
    if strategy_text:
        sections.append(f"## Human strategy\n\n{strategy_text}")
    # NOTE: with no human strategy section, espresso-core extracts a
    # system_generated strategy and STOPS at needs_human (exit 2) until
    # `approve-strategy --approver` is recorded. That default-deny stall is
    # intentional and maps to verdict=blocked here.

    known = _source_backed_lines(context)
    if known:
        sections.append("## Known results\n\n" + "\n".join(known))

    hypotheses = [
        str(h.get("name", "")).strip()
        for h in (claim.get("hypotheses") or [])
        if isinstance(h, dict) and str(h.get("name", "")).strip()
    ]
    if hypotheses:
        sections.append(
            "## Constraints\n\n" + "\n".join(f"- {h}" for h in hypotheses[:12])
        )

    if known:
        sections.append("## Sources or references\n\n" + "\n".join(known))
    return "\n\n".join(sections) + "\n"


def _selected_strategy_text(context: dict) -> str:
    parent = context.get("previous_prover_loop_attempt") or {}
    strategy_id = str(parent.get("strategy_id", "")).strip()
    brief = context.get("strategy_brief") or {}
    for item in brief.get("top_strategies") or []:
        if not isinstance(item, dict):
            continue
        if strategy_id and str(item.get("id", "")) != strategy_id:
            continue
        parts = [
            str(item.get("title", "")).strip(),
            str(item.get("why_this_route", "")).strip(),
            str(item.get("next_loop_plan", "")).strip(),
        ]
        text = " ".join(p for p in parts if p)
        if text:
            return text
    advisory = context.get("human_feedback_advisory")
    return str(advisory).strip() if isinstance(advisory, str) else ""


def _source_backed_lines(context: dict) -> list[str]:
    lines: list[str] = []
    grounding = context.get("source_grounding") or {}
    for ref in grounding.get("source_refs") or []:
        if not isinstance(ref, dict) or ref.get("status") != "source_backed":
            continue
        statement = str(ref.get("statement_or_summary", "")).strip()
        title = str(ref.get("title", "")).strip()
        locator = ref.get("locator") or {}
        locator_text = str(locator.get("value", "")).strip()
        if statement:
            suffix = ", ".join(p for p in (title, locator_text) if p)
            lines.append(f"- {statement}" + (f" ({suffix})" if suffix else ""))
    return lines[:12]


# ---------------------------------------------------------------------------
# Exact espresso-core CLI invocations (verified against the espresso-core
# source checkout, src/strategy_prover/cli.py: solve_parser, verify_parser).
# --schemas-dir defaults to cwd-relative "schemas", so it MUST be absolute.
# ---------------------------------------------------------------------------
def core_cli_command() -> list[str]:
    cli = os.environ.get(CORE_CLI_ENV, "").strip()
    if cli:
        return shlex.split(cli)
    core_root = os.environ.get(CORE_ROOT_ENV, "").strip()
    if core_root:
        return [str(Path(core_root) / ".venv" / "bin" / "espresso-core")]
    return ["espresso-core"]


def core_schemas_dir() -> str:
    core_root = os.environ.get(CORE_ROOT_ENV, "").strip()
    return str(Path(core_root) / "schemas") if core_root else "schemas"


def build_solve_command(problem_path: Path, run_dir: Path) -> list[str]:
    command = [
        *core_cli_command(),
        "solve",
        str(problem_path),
        str(run_dir),
        "--mode",
        "live",
        "--schemas-dir",
        core_schemas_dir(),
        "--force",
    ]
    config = os.environ.get(CORE_CONFIG_ENV, "").strip()
    if config:
        command[-1:-1] = ["--config", config]
    return command


def build_verify_command(run_dir: Path) -> list[str]:
    return [
        *core_cli_command(),
        "verify-run",
        str(run_dir),
        "--schemas-dir",
        core_schemas_dir(),
    ]


# ---------------------------------------------------------------------------
# Core run-dir readers
# ---------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_core_run(run_dir: Path) -> dict:
    """Read the espresso-core run-directory contract (best-effort per artifact;
    run_summary.yaml is required)."""
    run_dir = Path(run_dir)
    summary = _read_yaml(run_dir / "run_summary.yaml")
    if not summary:
        raise ValueError(f"no run_summary.yaml in {run_dir}")

    def _optional_yaml(rel: str) -> dict:
        try:
            return _read_yaml(run_dir / rel)
        except Exception:  # noqa: BLE001 - optional artifacts stay best-effort
            return {}

    def _optional_text(rel: str) -> str:
        try:
            return (run_dir / rel).read_text(encoding="utf-8")
        except OSError:
            return ""

    verifications = []
    verification_dir = run_dir / "verification"
    if verification_dir.is_dir():
        for report_path in sorted(verification_dir.glob("*.yaml")):
            try:
                verifications.append(_read_yaml(report_path))
            except Exception:  # noqa: BLE001
                continue

    best_verified: dict[str, str] = {}
    best_dir = run_dir / "best_verified"
    if best_dir.is_dir():
        for md_path in sorted(best_dir.glob("*.md")):
            best_verified[md_path.stem] = md_path.read_text(encoding="utf-8")

    return {
        "run_dir": str(run_dir),
        "run_summary": summary,
        "global_verification": _optional_yaml("global_verification.yaml"),
        "verifications": verifications,
        "claim_lock": _optional_yaml("claim_lock/claim_lock.yaml"),
        "proof_route": _optional_yaml("plans/proof_route.yaml"),
        "proposition_plan": _optional_yaml("plans/proposition_plan.yaml"),
        "selected_strategy": _optional_yaml("strategies/selected_strategy.yaml"),
        "promotion": _optional_yaml("research_materials/best_materials/promotion.yaml"),
        "final_proof_text": _optional_text("final_proof.md"),
        "best_verified": best_verified,
    }


# ---------------------------------------------------------------------------
# Mapping table 1: core outcome -> AGENT_OUTPUT_SCHEMA (prover round)
# ---------------------------------------------------------------------------
def translate_prover_round(core: dict) -> dict:
    summary = core["run_summary"]
    proof_status = str(summary.get("proof_status", "")).strip()
    soundness = str(summary.get("soundness_level", "unknown")).strip()
    wording = _CANDIDATE_WORDING.get(proof_status, "unrecognized outcome")
    global_verdict = str(core["global_verification"].get("verdict", "absent"))

    steps: list[str] = [
        _safe(
            f"Candidate route imported from espresso-core run "
            f"{summary.get('run_id', 'unknown')} "
            f"(route: {core['proof_route'].get('route', 'unknown')}, "
            f"strategy origin: {core['selected_strategy'].get('strategy_origin', 'unknown')})."
        )
    ]
    for node_id, node in sorted((summary.get("nodes") or {}).items()):
        node_status = str(node.get("status", "unknown"))
        node_wording = _CANDIDATE_WORDING.get(node_status, node_status)
        steps.append(
            _safe(
                f"Core obligation {node_id}: {node_wording} after "
                f"{node.get('attempts', 0)} attempt(s)."
            )
        )
    for prop in core["proposition_plan"].get("propositions") or []:
        if isinstance(prop, dict) and prop.get("statement"):
            steps.append(
                _safe(f"Core obligation {prop.get('id', '?')} targets: {prop['statement']}")
            )
    final_text = " ".join(core["final_proof_text"].split())
    if final_text:
        steps.append(_safe(f"Candidate final argument (excerpt): {final_text[:400]}"))

    risks = [_STANDING_RISK]
    if global_verdict in {"fail", "uncertain"}:
        risks.append(
            _safe(f"Core global verifier quorum returned verdict {global_verdict}.")
        )
    for report in [core["global_verification"], *core["verifications"]]:
        for issue in report.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            if issue.get("severity") in {"major", "critical"}:
                risks.append(
                    _safe(
                        f"Core verifier issue ({issue.get('severity')} "
                        f"{issue.get('type', 'unknown')}): {issue.get('message', '')}"
                    )
                )

    source_requests = [
        _safe(f"Core research left an unresolved gap: {gap}")
        for gap in core["promotion"].get("unresolved_gaps") or []
    ]
    source_requests += [
        _safe(f"Core strategy recorded a material gap: {gap}")
        for gap in core["selected_strategy"].get("material_gaps") or []
    ]

    missing_hypotheses = [
        _safe(f"Core claim-lock ambiguity ({amb.get('severity', '?')}): {amb.get('issue', '')}")
        for amb in core["claim_lock"].get("ambiguities") or []
        if isinstance(amb, dict) and amb.get("resolution_status") != "resolved"
    ]

    return {
        "summary": _safe(
            f"espresso-core backend round finished as a {wording}; "
            f"soundness level reported by core: {soundness}; global reviewer-quorum "
            f"verdict: {global_verdict}. All content is candidate-only; the "
            f"espresso_proof kernel decides every status."
        ),
        "steps": steps[:12],
        "source_requests": source_requests[:12],
        "missing_hypotheses": missing_hypotheses[:12],
        "risks": risks[:12],
        "verdict": _VERDICT_BY_CORE_STATUS.get(proof_status, "needs_revision"),
        "recommended_human_action": _ACTION_BY_CORE_STATUS.get(proof_status, "revise"),
    }


# ---------------------------------------------------------------------------
# Mapping table 2: core final_proof + lemma structure ->
# LINE_NUMBERED_PROOF_OUTPUT_SCHEMA (proof-writer role)
# ---------------------------------------------------------------------------
def _dependency(dep_type: str, ref: str, *, status: str, role: str) -> dict:
    return {
        "type": dep_type,
        "ref": ref,
        "status": status,
        "role": role,
        "needed_statement": "",
        "source_locator": "",
        "reviewer_verdict": "",
        "authority": "",
        "source_grounded": False,
        "machine_verified": False,
    }


def _proof_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = " ".join(
            line.strip() for line in block.splitlines() if not line.strip().startswith("#")
        ).strip()
        cleaned = cleaned.strip("\\[]").strip()
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def translate_line_numbered_proof(core: dict) -> dict:
    claim_lock = core["claim_lock"]
    route = str(core["proof_route"].get("route", "")).strip() or "import"
    hypotheses = "; ".join(str(h) for h in claim_lock.get("hypotheses") or [])
    locked = str(
        claim_lock.get("locked_statement")
        or core["run_summary"].get("problem_id", "the locked claim")
    )

    lines: list[dict] = [
        {
            "line_id": "L1",
            "statement": _safe_statement(
                f"Assume the locked hypotheses of the claim: {hypotheses or locked}"
            ),
            "justification": (
                "Instantiate the locked claim hypotheses recorded in the "
                "espresso-core claim_lock.yaml (imported candidate, unchecked here)."
            ),
            "dependencies": [],
            "depends_on": [],
            "source_refs": [],
            "gaps": [],
        }
    ]
    previous_id = "L1"
    counter = 1

    for prop_id, lemma_text in sorted(core["best_verified"].items()):
        counter += 1
        line_id = f"L{counter}"
        first_paragraph = next(iter(_proof_paragraphs(lemma_text)), "")
        statement = _prop_statement(core, prop_id) or first_paragraph
        lines.append(
            {
                "line_id": line_id,
                "statement": _safe_statement(statement),
                "justification": (
                    f"Imported candidate lemma argument from espresso-core "
                    f"best_verified/{prop_id}.md; accepted there only by an LLM "
                    f"reviewer quorum, not machine checking."
                ),
                "dependencies": [
                    _dependency("prior_line", previous_id, status="resolved", role="context"),
                    _dependency(
                        "unchecked_gap",
                        f"core_quorum_{prop_id}",
                        status="missing",
                        role="lemma accepted only by the espresso-core LLM reviewer quorum",
                    ),
                ],
                "depends_on": [previous_id],
                "source_refs": [],
                "gaps": [],
            }
        )
        previous_id = line_id

    for paragraph in _proof_paragraphs(core["final_proof_text"]):
        counter += 1
        line_id = f"L{counter}"
        lines.append(
            {
                "line_id": line_id,
                "statement": _safe_statement(paragraph),
                "justification": (
                    "Imported candidate step from espresso-core final_proof.md; "
                    "the argument for this step was reviewed there only by an LLM "
                    "quorum and remains unchecked here."
                ),
                "dependencies": [
                    _dependency("prior_line", previous_id, status="resolved", role="previous step"),
                ],
                "depends_on": [previous_id],
                "source_refs": [],
                "gaps": [],
            }
        )
        previous_id = line_id

    if len(lines) == 1:
        raise ValueError("core run has no final_proof.md paragraphs or best_verified lemmas")

    lines[-1]["gaps"] = [
        "espresso-core acceptance was an LLM reviewer quorum only; no machine "
        "checking was performed and the espresso_proof consensus seal must "
        "re-review every line."
    ]
    return {"proof_kind": f"espresso_core_{route}", "lines": lines}


def _prop_statement(core: dict, prop_id: str) -> str:
    for prop in core["proposition_plan"].get("propositions") or []:
        if isinstance(prop, dict) and str(prop.get("id")) == prop_id:
            return str(prop.get("statement", ""))
    return ""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _run_core_backend(role: str, prompt: str, meta: dict) -> dict:
    mode = os.environ.get(MODE_ENV, "off").strip().lower()
    meta["mode"] = mode
    if mode == "mock":
        fixture = os.environ.get(FIXTURE_RUN_ENV, "").strip()
        if not fixture:
            raise ValueError(f"{MODE_ENV}=mock requires {FIXTURE_RUN_ENV}")
        core = load_core_run(Path(fixture))
    elif mode == "live":
        core = _run_core_live(prompt, meta)
    else:
        raise ValueError(f"unsupported {MODE_ENV}: {mode!r}")
    meta["core_run_dir"] = core["run_dir"]
    if role == "proof_writer":
        return translate_line_numbered_proof(core)
    return translate_prover_round(core)


def _run_core_live(prompt: str, meta: dict) -> dict:
    """Phase A live-mode stub: builds the input bundle and the EXACT pinned
    subprocess commands. Execution only happens when ESPRESSO_CORE_MODE=live is
    explicitly set (never in tests). Long runs: raise
    ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS above ESPRESSO_CORE_TIMEOUT_SECONDS."""
    import tempfile

    runs_parent = os.environ.get(CORE_RUNS_DIR_ENV, "").strip() or tempfile.gettempdir()
    request_tag = uuid.uuid4().hex[:12]
    bundle_dir = Path(runs_parent) / f"espresso-core-{request_tag}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    problem_path = bundle_dir / "problem.md"
    problem_path.write_text(build_problem_markdown(prompt), encoding="utf-8")
    run_dir = bundle_dir / "run"

    solve_command = build_solve_command(problem_path, run_dir)
    meta["solve_command"] = solve_command
    timeout = int(os.environ.get(CORE_TIMEOUT_ENV, DEFAULT_CORE_TIMEOUT_SECONDS))
    solve = subprocess.run(  # guarded: only reachable with ESPRESSO_CORE_MODE=live
        solve_command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    meta["solve_exit_code"] = solve.returncode
    if solve.returncode == 1:
        detail = (solve.stderr or solve.stdout or "").strip()[:300]
        raise RuntimeError(f"espresso-core solve failed (exit 1): {detail}")
    # Exit 0 = accepted terminal run; exit 2 = intentional mathematical stop
    # (claim-lock needs_human / unapproved system_generated strategy). Both leave
    # a readable run dir; translation maps needs_human to verdict=blocked.
    verify_command = build_verify_command(run_dir)
    meta["verify_command"] = verify_command
    verify = subprocess.run(verify_command, capture_output=True, text=True, timeout=timeout)
    meta["verify_exit_code"] = verify.returncode
    return load_core_run(run_dir)


def _delegate_to_fallback(raw_request: str, meta: dict) -> str | None:
    """Subprocess passthrough of the untouched request to the normal adapter.
    Returns the fallback's stdout VERBATIM (validated to parse as one JSON
    object), or None when no fallback is configured."""
    raw_command = os.environ.get(FALLBACK_COMMAND_ENV, "").strip()
    if not raw_command:
        return None
    command = shlex.split(raw_command)
    if len(command) == 1 and command[0].endswith(".py") and Path(command[0]).exists():
        command = [sys.executable, command[0]]
    timeout = int(os.environ.get(FALLBACK_TIMEOUT_ENV, DEFAULT_FALLBACK_TIMEOUT_SECONDS))
    proc = subprocess.run(
        command,
        input=raw_request,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"fallback agent command failed: {detail}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise ValueError("fallback agent did not return a JSON object")
    meta["delegated"] = True
    return proc.stdout


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    raw_request = sys.stdin.read() or "{}"
    meta: dict = {
        "adapter": "espresso_core_adapter",
        "request_id": str(uuid.uuid4()),
        "backend": None,
        "delegated": False,
        "parse_status": "not_run",
        "fallback_used": False,
        "fallback_reason": None,
    }
    try:
        request = json.loads(raw_request)
        if not isinstance(request, dict):
            request = {}
    except json.JSONDecodeError:
        request = {}
    system = str(request.get("system", ""))
    prompt = str(request.get("prompt", ""))
    role = detect_role(system=system, prompt=prompt)
    meta["role"] = role
    mode = os.environ.get(MODE_ENV, "off").strip().lower()

    result: dict | None = None
    try:
        if role in {"prover", "proof_writer"} and mode in {"mock", "live"}:
            meta["backend"] = "espresso-core"
            result = _run_core_backend(role, prompt, meta)
            meta["parse_status"] = "ok"
        else:
            delegated = _delegate_to_fallback(raw_request, meta)
            if delegated is not None:
                # Pass the normal adapter's stdout through VERBATIM.
                sys.stdout.write(delegated)
                return 0
            raise ValueError(
                f"no backend for role {role!r} (mode={mode!r}, "
                f"{FALLBACK_COMMAND_ENV} unset)"
            )
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash the round
        meta["parse_status"] = "fallback"
        meta["fallback_used"] = True
        meta["fallback_reason"] = str(exc)[:500]
        result = dict(_FALLBACK)
        result["summary"] = (
            f"espresso-core adapter could not produce usable JSON: {meta['fallback_reason']}"
        )[:300]

    result[_DIAGNOSTIC_META_KEY] = meta
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
