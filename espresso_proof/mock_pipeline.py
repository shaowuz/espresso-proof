from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .contracts import default_agent_contracts
from .counterexample_probes import propose_deterministic_counterexamples
from .verification_request_probes import (
    _ORDER_P_SQUARED_ABELIAN_EXACT,
    propose_scoped_verification_requests,
)
from .io import read_text, write_text, write_yaml
from .memory import write_run_memory
from .surface_warnings import collect_surface_warnings, render_warning_lines
from .validators import validate_run

_ORDER_6_GROUPS_ABELIAN_EXACT = re.compile(
    r"(?:every|all|each)\s+(?:finite\s+)?groups?\s+of\s+order\s+6\s+"
    r"(?:is|are)\s+abelian\s*\.?",
    re.IGNORECASE,
)

_ORDER_P_CUBED_ABELIAN_EXACT = re.compile(
    r"(?:every|all|each)\s+(?:finite\s+)?groups?\s+of\s+order\s+p(?:\^3|³)\s*"
    r",?\s*(?:where|for|with)\s+p\s+(?:is\s+)?(?:a\s+)?prime\s*,?\s*"
    r"(?:is|are)\s+abelian\s*\.?",
    re.IGNORECASE,
)

_FINITE_P_GROUP_CENTER_EXACT = re.compile(
    r"(?:every|all|each)\s+nontrivial\s+finite\s+p-groups?\s+"
    r"(?:has|have)\s+nontrivial\s+cent(?:er|re)\s*\.?",
    re.IGNORECASE,
)

_CENTER_QUOTIENT_CYCLIC_EXACT = re.compile(
    r"if\s+g\s*/\s*z\s*\(\s*g\s*\)\s+is\s+cyclic\s*,?\s*"
    r"then\s+g\s+is\s+abelian\s*\.?",
    re.IGNORECASE,
)


def run_mock_critique(project_id: str, input_path: Path, output_root: Path) -> Path:
    text = read_text(input_path)
    scenario = _detect_scenario(input_path, text)
    run_id = f"{project_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    run_dir = output_root / "projects" / project_id / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_text(run_dir / "input.md", text)
    artifacts = _artifacts_for_scenario(scenario, text, run_id, project_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]

    # Goal 42E cycle 1: deterministic machine counterexample probes run in the
    # mock path too, so a concrete false claim ("every group of order 6 is
    # abelian") carries a Cayley-table-verified counterexample instead of an
    # empty bank. The probe fails closed (nothing for symbolic/true/modified
    # claims), and because the vague-claim path leaves the hypothesis list
    # undetermined, the record it adds here is DOWNGRADED (satisfies=unclear,
    # refutes_claim=False): an undetermined ledger never earns a refutation.
    probe_tests = propose_deterministic_counterexamples(artifacts["locked_claim.yaml"])
    if probe_tests:
        bank = artifacts.get("counterexamples.yaml", {"claim_id": claim_id, "tests": []})
        existing_ids = {t.get("id") for t in bank.get("tests", [])}
        bank.setdefault("tests", []).extend(
            t for t in probe_tests if t["id"] not in existing_ids
        )
        artifacts["counterexamples.yaml"] = bank

    # Goal 42E cycle 2: deterministic scoped-verification REQUESTS run in the
    # mock path too, so the symbolic p^2-abelian dogfood claim proposes its
    # concrete commutativity instance checks. Requests are proposals only
    # (no status); auto-wiring through the trusted backend is what can earn
    # instance_checked, and the probe fails closed on any other claim.
    verification_requests = propose_scoped_verification_requests(
        artifacts["locked_claim.yaml"]
    )
    if verification_requests:
        artifacts["scoped_verification_requests.yaml"] = {
            "claim_id": claim_id,
            "requests": verification_requests,
        }
    artifacts["final_report.md"] = _with_final_report_disclosures(
        str(artifacts.get("final_report.md") or ""),
        artifacts,
    )
    artifacts["agent_contracts.yaml"] = default_agent_contracts(project_id)
    artifacts.setdefault("human_choice.yaml", _pending_human_choice(claim_id))
    optional_artifacts = {
        "human_example_feedback": "human_example_feedback.yaml",
        "example_feedback_summary": "example_feedback_summary.yaml",
        "scoped_verification_requests": "scoped_verification_requests.yaml",
    }
    optional_manifest = {
        key: filename
        for key, filename in optional_artifacts.items()
        if filename in artifacts
    }
    for filename, data in artifacts.items():
        if filename.endswith(".yaml"):
            write_yaml(run_dir / filename, data)
        else:
            write_text(run_dir / filename, data)

    manifest = {
        "run_id": run_id,
        "project_id": project_id,
        "created_at": datetime.now(UTC).isoformat(),
        "current_state": "WAITING_HUMAN_MAJOR_DECISION",
        "input_file": "input.md",
        "artifacts": {
            "locked_claim": "locked_claim.yaml",
            "agent_contracts": "agent_contracts.yaml",
            "hypothesis_ledger": "hypothesis_ledger.yaml",
            "literature_review": "literature_review.yaml",
            "examples": "examples.yaml",
            "computation_checks": "computation_checks.yaml",
            "counterexamples": "counterexamples.yaml",
            "tests": "tests.yaml",
            "source_grounding": "source_grounding.yaml",
            "review": "review.yaml",
            "idea_grade": "idea_grade.yaml",
            "loop_control": "loop_control.yaml",
            "strategy_synthesis": "strategy_synthesis.yaml",
            "strategy_brief": "strategy_brief.yaml",
            "human_choice": "human_choice.yaml",
            "final_report": "final_report.md",
        },
        "optional_artifacts": optional_manifest,
        "dependencies": {
            "idea_grade": {
                "depends_on": [
                    "locked_claim",
                    "hypothesis_ledger",
                    "literature_review",
                    "examples",
                    "computation_checks",
                    "counterexamples",
                    "tests",
                    "source_grounding",
                    "review",
                ]
            },
            "source_grounding": {
                "depends_on": ["literature_review", "tests"]
            },
            "loop_control": {
                "depends_on": ["tests", "source_grounding", "review", "idea_grade"]
            },
            "strategy_synthesis": {
                "depends_on": ["tests", "source_grounding", "review", "idea_grade", "loop_control"]
            },
            "strategy_brief": {
                "depends_on": ["strategy_synthesis"]
            },
            "human_choice": {
                "depends_on": ["strategy_brief"]
            },
            "final_report": {
                "depends_on": ["idea_grade", "strategy_brief", "human_choice"]
            },
        },
        "retries": {},
    }
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)
    _write_memory_entry(run_dir, output_root, project_id, run_id)
    return run_dir


def _detect_scenario(input_path: Path, text: str) -> str:
    name = input_path.parent.name
    if name in {
        "hodge_all_cubic_fourfolds",
        "dropped_hypothesis_not_refutation",
        "fake_source_backed_rejected",
        "unchecked_example_not_evidence",
        "promising_but_unproved",
        "refuted_by_all_hypotheses_counterexample",
        "likely_false_multiple_pressures",
        "source_misapplication_very_general_to_all",
        "vague_claim_unclear",
        "human_marks_example_interesting",
        "human_marks_example_misleading_not_evidence",
        "human_promotes_dangerous_counterexample",
        "human_feedback_does_not_upgrade_source_status",
    }:
        return name
    lowered = text.lower()
    # Goal 42E cycle 5: a universal irrationality claim over ALL cubic
    # fourfolds is the canonical very-general-vs-all discipline test — the
    # smoke run must exercise the source-misapplication machinery (recorded
    # hypothesis-mismatch refusal), not the generic hodge sketch. Fixture
    # inputs are unaffected (matched by directory name above).
    if "all cubic fourfold" in lowered and "irrational" in lowered:
        return "source_misapplication_very_general_to_all"
    if "all cubic fourfold" in lowered:
        return "hodge_all_cubic_fourfolds"
    if "drop smooth" in lowered or "without projective" in lowered:
        return "dropped_hypothesis_not_refutation"
    if "by voisin" in lowered or "source backed" in lowered:
        return "fake_source_backed_rejected"
    if "hodge number" in lowered and "compute" in lowered:
        return "unchecked_example_not_evidence"
    if "promising" in lowered and "not proved" in lowered:
        return "promising_but_unproved"
    if "n = 3" in lowered and "prime" in lowered and "even" in lowered:
        return "refuted_by_all_hypotheses_counterexample"
    if "multiple structural pressures" in lowered or "specialization" in lowered:
        return "likely_false_multiple_pressures"
    if "very general" in lowered and "locked claim for all" in lowered:
        return "source_misapplication_very_general_to_all"
    # Goal 42E cycle 6: the true classical p^2-abelian claim must engage the
    # source-grounding machinery instead of falling through to the vague
    # scenario (which records no theorem use at all). Anchored fullmatch
    # (Kernel Safety review): the favorable scaffold must never catch
    # negated, weakened, or primality-free variants — those read literally
    # are DIFFERENT (false) claims and stay on the vague path.
    if _ORDER_P_SQUARED_ABELIAN_EXACT.fullmatch(" ".join(lowered.split()).strip()):
        return "true_theorem_classical_source"
    if _finite_group_scenario_key(lowered):
        return "finite_group_claim"
    return "vague_claim_unclear"


def _finite_group_scenario_key(text: str) -> str | None:
    normalized = " ".join(str(text).lower().split()).strip()
    if _ORDER_6_GROUPS_ABELIAN_EXACT.fullmatch(normalized):
        return "order6_false"
    if _ORDER_P_CUBED_ABELIAN_EXACT.fullmatch(normalized):
        return "p3_groups_false"
    if _FINITE_P_GROUP_CENTER_EXACT.fullmatch(normalized):
        return "center_pgroup_true"
    if _CENTER_QUOTIENT_CYCLIC_EXACT.fullmatch(normalized):
        return "cyclic_quotient_center_step"
    return None


def _base_claim(text: str, ambiguous_terms: list[str] | None = None) -> dict:
    return {
        "id": "claim_001",
        "raw_text": text.strip(),
        "normalized_statement": text.strip()[:240] or "Unclear claim.",
        "objects": ["cubic fourfolds"] if "cubic" in text.lower() else ["unspecified objects"],
        "hypotheses": [
            {"name": "smooth", "status": "unclear", "source": "agent"},
            {"name": "projective", "status": "unclear", "source": "agent"},
            {"name": "base field C", "status": "inferred", "source": "agent"},
        ],
        "conclusion": "Claim conclusion requires clarification.",
        "ambiguous_terms": ambiguous_terms or [],
        "locked": True,
        "repaired_from": None,
        "human_confirmed": False,
    }


def _artifacts_for_scenario(scenario: str, text: str, run_id: str, project_id: str) -> dict[str, object]:
    if scenario == "hodge_all_cubic_fourfolds":
        return _hodge_all_cubic_fourfolds(text, run_id)
    if scenario == "dropped_hypothesis_not_refutation":
        return _dropped_hypothesis(text, run_id)
    if scenario == "fake_source_backed_rejected":
        return _fake_source_backed(text, run_id)
    if scenario == "unchecked_example_not_evidence":
        return _unchecked_example(text, run_id)
    if scenario == "promising_but_unproved":
        return _promising_but_unproved(text, run_id)
    if scenario == "refuted_by_all_hypotheses_counterexample":
        return _refuted_by_all_hypotheses(text, run_id)
    if scenario == "likely_false_multiple_pressures":
        return _likely_false_multiple_pressures(text, run_id)
    if scenario == "source_misapplication_very_general_to_all":
        return _source_misapplication(text, run_id)
    if scenario == "true_theorem_classical_source":
        return _true_theorem_classical_source(text, run_id)
    if scenario == "finite_group_claim":
        return _finite_group_claim(text, run_id)
    if scenario in {
        "human_marks_example_interesting",
        "human_marks_example_misleading_not_evidence",
        "human_promotes_dangerous_counterexample",
        "human_feedback_does_not_upgrade_source_status",
    }:
        return _human_feedback_scenario(scenario, text, run_id, project_id)
    return _vague_claim(text, run_id)


def _artifact_dict(artifacts: dict[str, object], filename: str) -> dict:
    value = artifacts.get(filename)
    return value if isinstance(value, dict) else {}


def _with_final_report_disclosures(text: str, artifacts: dict[str, object]) -> str:
    warnings = collect_surface_warnings(
        grade=_artifact_dict(artifacts, "idea_grade.yaml"),
        review=_artifact_dict(artifacts, "review.yaml"),
        examples=_artifact_dict(artifacts, "examples.yaml"),
        counterexamples=_artifact_dict(artifacts, "counterexamples.yaml"),
        tests=_artifact_dict(artifacts, "tests.yaml"),
        source_grounding=_artifact_dict(artifacts, "source_grounding.yaml"),
        theorem_applicability=_artifact_dict(artifacts, "theorem_applicability.yaml"),
        update_inbox=_artifact_dict(artifacts, "update_inbox.yaml"),
    )
    lines = render_warning_lines(warnings)
    if not lines:
        return text
    block = "\n".join(f"- {line}" for line in lines)
    return text.rstrip() + "\n\nOpen blockers / pending checks:\n" + block + "\n"


def _common_source_grounding(claim_id: str, *, source_backed: bool = False) -> dict:
    if source_backed:
        source_refs = [
            {
                "id": "src_exact_001",
                "type": "paper",
                "title": "Pinned exact source",
                "authors": ["Placeholder Author"],
                "locator": {"kind": "theorem", "value": "Theorem 1.1"},
                "statement_or_summary": "Exact theorem statement placeholder for mock test.",
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "source_backed",
            }
        ]
        status = "source_backed"
        refs = ["src_exact_001"]
        match = {"matches": True, "mismatches": []}
    else:
        source_refs = [
            {
                "id": "src_candidate_001",
                "type": "paper",
                "title": "Candidate source needing exact locator",
                "authors": [],
                "locator": {"kind": "unknown", "value": "to_be_filled"},
                "statement_or_summary": "Mock source candidate without exact theorem locator.",
                "hypothesis_match": {"matches": "unclear", "mismatches": []},
                "confidence": "low",
                "status": "candidate",
            }
        ]
        status = "candidate"
        refs = ["src_candidate_001"]
        match = {"matches": "unclear", "mismatches": []}
    return {
        "claim_id": claim_id,
        "source_refs": source_refs,
        "steps": [
            {
                "proof_step_id": "step_001",
                "needed_theorem": "Exact literature statement supporting the Hodge-theoretic step.",
                "status": status,
                "source_refs": refs,
                "hypothesis_match": match,
                "note": "Mock source grounding keeps vague citations below source_backed.",
            }
        ],
    }


def _misapplied_source_grounding(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "source_refs": [
            {
                "id": "src_very_general_only",
                "type": "paper",
                "title": "Very general cubic fourfold theorem",
                "authors": ["Placeholder Author"],
                "locator": {"kind": "theorem", "value": "Theorem VG"},
                "statement_or_summary": (
                    "A candidate theorem whose stated hypotheses apply only to very general "
                    "smooth cubic fourfolds."
                ),
                "hypothesis_match": {
                    "matches": False,
                    "mismatches": [
                        "source theorem applies only to very general cubic fourfolds",
                        "locked claim says all smooth cubic fourfolds",
                    ],
                },
                "confidence": "medium",
                "status": "misapplied",
            }
        ],
        "steps": [
            {
                "proof_step_id": "step_apply_vg_to_all",
                "needed_theorem": "A theorem applying the Hodge obstruction to all smooth cubic fourfolds.",
                "status": "misapplied",
                "source_refs": ["src_very_general_only"],
                "hypothesis_match": {
                    "matches": False,
                    "mismatches": [
                        "very general hypothesis was used as if it applied to all objects"
                    ],
                },
                "note": "Referencing rejects the very-general-to-all application.",
            }
        ],
    }


def _common_literature_review(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "query_summary": "Mock literature pass for Hodge atom / cubic fourfold pressure points.",
        "candidate_references": [
            {
                "id": "lit_candidate_kkpy",
                "title": "Candidate Hodge atom reference",
                "authors": ["Placeholder Author"],
                "locator": {"kind": "unknown", "value": "to_be_filled"},
                "relevance": "possible_theorem_source",
                "status": "candidate",
                "why_relevant": "May contain the theorem needed by the Hodge atom obstruction step.",
                "found_by": "literature_review",
            },
            {
                "id": "lit_special_cubic_examples",
                "title": "Candidate special cubic fourfold examples",
                "authors": [],
                "locator": {"kind": "unknown", "value": "to_be_filled"},
                "relevance": "counterexample_source",
                "status": "candidate",
                "why_relevant": "Special cubic fourfolds are natural stress tests for all versus very general claims.",
                "found_by": "literature_review",
            },
        ],
        "known_examples": ["very general cubic fourfold", "special cubic fourfold"],
        "known_counterexample_families": ["special versus very general drift"],
        "notes": [
            "Literature Review only proposes candidates. Referencing must check exact locator and hypotheses."
        ],
    }


def _loop_control(
    claim_id: str,
    *,
    cycles_analyzed: int = 1,
    no_new_counterexamples_cycles: int = 0,
    no_new_references_cycles: int = 0,
    grade_delta_last_two: int = 0,
    should_stop: bool = False,
    stop_reasons: list[str] | None = None,
    next_action: str = "continue_loop",
) -> dict:
    return {
        "claim_id": claim_id,
        "cycles_analyzed": cycles_analyzed,
        "no_new_counterexamples_cycles": no_new_counterexamples_cycles,
        "no_new_references_cycles": no_new_references_cycles,
        "grade_delta_last_two": grade_delta_last_two,
        "budget_exhausted": False,
        "should_stop": should_stop,
        "stop_reasons": stop_reasons or [],
        "next_action": next_action,
    }


def _pending_human_choice(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "status": "pending",
        "selected_strategy": None,
        "effort": None,
        "taste_judgment": "",
        "continue_decision": "pending",
        "human_confirmed": False,
    }


def _human_feedback_artifacts(
    claim_id: str,
    run_id: str,
    *,
    project_id: str,
    target_type: str,
    target_artifact: str,
    target_id: str,
    labels: list[str],
    requested_actions: list[str],
    reason: str,
    direction: str,
    weight: str,
) -> dict[str, object]:
    feedback_id = "feedback_001"
    return {
        "human_example_feedback.yaml": {
            "schema_version": 1,
            "project_id": project_id,
            "claim_id": claim_id,
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "feedback": [
                {
                    "feedback_id": feedback_id,
                    "target_type": target_type,
                    "target_artifact": target_artifact,
                    "target_id": target_id,
                    "labels": labels,
                    "reason": reason,
                    "scope": "project",
                    "requested_actions": requested_actions,
                    "human_confirmed": True,
                    "supersedes_feedback_id": None,
                }
            ],
        },
        "example_feedback_summary.yaml": {
            "schema_version": 1,
            "claim_id": claim_id,
            "run_id": run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "generated_by": "mock_pipeline",
            "feedback_artifact_path": "human_example_feedback.yaml",
            "summarized_from": [feedback_id],
            "input_artifact_digests": {},
            "ranking_adjustments": [
                {
                    "target_id": target_id,
                    "target_type": target_type,
                    "target_artifact": target_artifact,
                    "feedback_ids": [feedback_id],
                    "direction": direction,
                    "weight": weight,
                    "reason": reason,
                }
            ],
            "project_preferences": [
                f"{target_type}:{target_id} labeled {', '.join(labels)}"
            ],
            "playbook_tuning_candidates": [],
            "memory_entries": [],
        },
    }


def _common_test_report(
    claim_id: str,
    *,
    counterexample_ids: list[str] | None = None,
    result: str = "inconclusive",
    evidence_status: str = "human_needed",
    summary: str = "Mock test keeps the claim below proof status.",
) -> dict:
    return {
        "claim_id": claim_id,
        "target": "user_sketch",
        "summary": summary,
        "tests": [
            {
                "id": "test_report_001",
                "description": "Test the claim against verified or pending example/counterexample material.",
                "uses_examples": ["ex_special_cubic"],
                "uses_counterexamples": counterexample_ids or [],
                "result": result,
                "evidence_status": evidence_status,
                "main_observation": "The available material is not enough to upgrade this to a proof.",
                "next_action": "Verify the hardest example and rerun the test.",
            }
        ],
    }


def _strategy_brief(
    claim_id: str,
    run_id: str,
    *,
    status: str,
    base_score: int,
    source_status: str = "candidate",
) -> dict:
    routes = _strategy_routes(status=status, base_score=base_score, source_status=source_status)
    return {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": routes[:3],
        "also_considered": [
            {
                "title": "Start Lean formalization",
                "reason_not_top_three": "Out of scope for this AG/Hodge critique workflow.",
            }
        ],
        "question_for_mathematician": (
            "Which strategy has the best mathematical taste, and should Espresso spend "
            "quick_test, normal_pass, deep_exploration, or long_watch effort on it?"
        ),
        "allowed_human_choices": [
            {
                "strategy_id": strategy["id"],
                "effort_options": ["quick_test", "normal_pass", "deep_exploration", "long_watch"],
            }
            for strategy in routes[:3]
        ]
        + [{"strategy_id": "none", "effort_options": ["quick_test"]}],
    }


def _strategy_synthesis(
    claim_id: str,
    run_id: str,
    *,
    status: str,
    base_score: int,
    source_status: str = "candidate",
) -> dict:
    return {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": _strategy_routes(
            status=status,
            base_score=base_score,
            source_status=source_status,
        ),
        "discarded_routes": ["Start Lean formalization"],
        "synthesis_note": (
            "Strategy Synthesizer organizes next routes; it does not generate a proof or change the grade."
        ),
    }


def _finite_group_strategy_routes(case: str, *, base_score: int, source_status: str = "candidate") -> list[dict]:
    def score(delta: int) -> int:
        return max(0, min(100, base_score + delta))

    if case == "p2_groups_true":
        return [
            {
                "id": "strategy_center_quotient_p2",
                "title": "Use nontrivial center, then the cyclic quotient criterion",
                "score": score(10),
                "status": "promising",
                "why_this_route": (
                    "A group of order p^2 has nontrivial center; if the center has "
                    "order p^2 we are done, and if it has order p then G/Z(G) is "
                    "cyclic, forcing G abelian."
                ),
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": "The center lemma and quotient-cyclic step still need source or line proof.",
                "source_status": source_status,
                "estimated_effort": "normal_pass",
                "next_loop_plan": "Split into center-size and G/Z(G)-cyclic subgoals.",
            }
        ]
    if case == "order6_false":
        return [
            {
                "id": "strategy_admit_checked_s3_counterexample",
                "title": "Check the S3/D3 counterexample against every hypothesis",
                "score": score(14),
                "status": "likely_false",
                "why_this_route": (
                    "A deterministic Cayley-table check can test the group of order 6 "
                    "and decide whether it is an admissible all-hypotheses counterexample."
                ),
                "survived_tests": [],
                "failed_tests": ["ce_probe_dihedral_group_D3"],
                "main_risk": "The run must not mark refuted unless the locked hypotheses match the table exactly.",
                "source_status": "not_found",
                "estimated_effort": "quick_test",
                "next_loop_plan": "Verify the D3/S3 table and bind the result to the locked order-6 claim.",
            }
        ]
    if case == "p3_groups_false":
        return [
            {
                "id": "strategy_test_nonabelian_order_8",
                "title": "Test nonabelian order-8 groups before any p^2 analogy",
                "score": score(12),
                "status": "likely_false",
                "why_this_route": (
                    "The p^2 theorem does not extend to p^3; D4 or Q8 style groups "
                    "are the cheapest pressure tests."
                ),
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": "The concrete order-8 witness still needs an admissibility check.",
                "source_status": "not_found",
                "estimated_effort": "quick_test",
                "next_loop_plan": "Run a finite-group table check for a nonabelian group of order 8.",
            }
        ]
    if case == "center_pgroup_true":
        return [
            {
                "id": "strategy_class_equation_center",
                "title": "Use the class equation to force a nontrivial center",
                "score": score(10),
                "status": "promising",
                "why_this_route": (
                    "The standard route studies conjugacy classes in a finite p-group; "
                    "class sizes off the center are divisible by p, forcing |Z(G)| > 1."
                ),
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": "The class-equation lemma still needs an exact source or line proof.",
                "source_status": source_status,
                "estimated_effort": "normal_pass",
                "next_loop_plan": "Pin the class equation and create a subgoal for nontriviality of Z(G).",
            }
        ]
    return [
        {
            "id": "strategy_center_quotient_cosets",
            "title": "Write every element as a central factor times one coset generator",
            "score": score(10),
            "status": "promising",
            "why_this_route": (
                "If G/Z(G) is generated by gZ(G), every element is z g^n; central "
                "factors commute, so two such expressions commute."
            ),
            "survived_tests": [],
            "failed_tests": [],
            "main_risk": "The proof must explicitly use Z(G), not an arbitrary subgroup.",
            "source_status": source_status,
            "estimated_effort": "normal_pass",
            "next_loop_plan": "Expand the coset representative calculation and check each commutation step.",
        }
    ]


def _finite_group_strategy_synthesis(
    claim_id: str,
    run_id: str,
    *,
    case: str,
    status: str,
    base_score: int,
    source_status: str = "candidate",
) -> dict:
    routes = _finite_group_strategy_routes(
        case, base_score=base_score, source_status=source_status
    )
    return {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": routes,
        "discarded_routes": ["Start formalization before locking finite-group lemmas"],
        "synthesis_note": (
            "Finite-group strategy synthesis selected domain-specific routes; "
            "these are proof-search plans only and do not upgrade status."
        ),
    }


def _finite_group_strategy_brief(
    claim_id: str,
    run_id: str,
    *,
    case: str,
    status: str,
    base_score: int,
    source_status: str = "candidate",
) -> dict:
    routes = _finite_group_strategy_routes(
        case, base_score=base_score, source_status=source_status
    )
    return {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": routes[:3],
        "also_considered": [
            {
                "title": "Generic claim-lock route",
                "reason_not_top_three": (
                    "The finite-group statement is already precise enough for "
                    "domain-specific proof search."
                ),
            }
        ],
        "question_for_mathematician": (
            "Which finite-group route should Espresso spend time on next?"
        ),
        "allowed_human_choices": [
            {
                "strategy_id": strategy["id"],
                "effort_options": ["quick_test", "normal_pass", "deep_exploration"],
            }
            for strategy in routes[:3]
        ]
        + [{"strategy_id": "none", "effort_options": ["quick_test"]}],
    }


def _strategy_routes(*, status: str, base_score: int, source_status: str = "candidate") -> list[dict]:
    def score(delta: int) -> int:
        return max(0, min(100, base_score + delta))

    routes = [
        {
            "id": "strategy_narrow_genericity",
            "title": "Narrow the statement to the precise genericity class",
            "score": score(10),
            "status": status,
            "why_this_route": "Repair the all-versus-very-general drift before investing in proof structure.",
            "survived_tests": [],
            "failed_tests": [],
            "main_risk": "The repaired statement may become weaker than the intended theorem.",
            "source_status": source_status,
            "estimated_effort": "normal_pass",
            "next_loop_plan": "Rerun Counterexamples on special versus very general cubic fourfolds.",
        },
        {
            "id": "strategy_pin_reference",
            "title": "Pin the exact Hodge-theoretic reference",
            "score": score(4),
            "status": status,
            "why_this_route": "Find the exact theorem and check hypotheses before using it in the sketch.",
            "survived_tests": [],
            "failed_tests": [],
            "main_risk": "The cited theorem may not match the needed hypotheses.",
            "source_status": source_status,
            "estimated_effort": "quick_test",
            "next_loop_plan": "Run Referencing on the key Hodge atom obstruction step.",
        },
        {
            "id": "strategy_test_special_examples",
            "title": "Stress-test with special cubic fourfold examples",
            "score": score(-3),
            "status": status,
            "why_this_route": "Use special examples as a taste check before developing a full argument.",
            "survived_tests": [],
            "failed_tests": [],
            "main_risk": "Example calculations may remain human_needed without exact sources.",
            "source_status": "mixed" if source_status == "candidate" else source_status,
            "estimated_effort": "deep_exploration",
            "next_loop_plan": "Ask Evidence Auditor to check whether the special example satisfies the locked hypotheses.",
        },
    ]
    if status == "unclear":
        routes = [
            {
                "id": "strategy_lock_claim",
                "title": "Lock a precise claim first",
                "score": base_score,
                "status": "unclear",
                "why_this_route": "The current statement is too vague for meaningful example testing.",
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": "Continuing without a precise claim will create false progress.",
                "source_status": "not_found",
                "estimated_effort": "quick_test",
                "next_loop_plan": "Ask the user to specify objects, hypotheses, and conclusion.",
            }
        ]
    return routes


def _parallel_review(
    claim_id: str,
    *,
    attempt_status: str = "failed",
    verdict: str = "repair_first",
    strategy_verdict: str = "fail",
    evidence_verdict: str = "unclear",
    line_verdict: str = "unclear",
    line_applicability: str = "sketch_only",
    strongest_objection: str = "The current attempt has not survived review.",
    theorem_drift: list[str] | None = None,
    missing_hypotheses: list[str] | None = None,
    source_risks: list[str] | None = None,
    example_risks: list[str] | None = None,
    downgrade_reasons: list[str] | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "attempt_status": attempt_status,
        "verdict": verdict,
        "reviewers": [
            {
                "reviewer_id": "strategy_reviewer",
                "scope": "proof_strategy",
                "verdict": strategy_verdict,
                "applicability": "applicable",
                "summary": "High-level strategy review checks whether the proof route is plausible before detailed work.",
                "objections": theorem_drift or [],
                "checked_artifacts": ["locked_claim.yaml", "hypothesis_ledger.yaml", "source_grounding.yaml"],
                "required_reruns": ["Test"] if strategy_verdict == "fail" else [],
                "severity": "high" if strategy_verdict == "fail" else "medium",
            },
            {
                "reviewer_id": "evidence_reviewer",
                "scope": "examples_counterexamples",
                "verdict": evidence_verdict,
                "applicability": "applicable",
                "summary": "Evidence review checks whether verified examples and counterexamples actually support survived/failed claims.",
                "objections": example_risks or [],
                "checked_artifacts": ["examples.yaml", "counterexamples.yaml", "computation_checks.yaml", "tests.yaml"],
                "required_reruns": ["Evidence Auditor", "Test"] if evidence_verdict == "fail" else [],
                "severity": "high" if evidence_verdict == "fail" else "medium",
            },
            {
                "reviewer_id": "line_proof_reviewer",
                "scope": "line_by_line_proof",
                "verdict": line_verdict,
                "applicability": line_applicability,
                "summary": "Line-by-line proof review checks every proof step when a real proof is present.",
                "objections": (
                    ["No line-by-line proof is available in this sketch."]
                    if line_verdict == "fail" or line_applicability == "sketch_only"
                    else []
                ),
                "checked_artifacts": ["locked_claim.yaml", "source_grounding.yaml", "tests.yaml"],
                "required_reruns": ["Referencing"] if line_verdict == "fail" else [],
                "severity": "high" if line_verdict == "fail" else "medium",
            },
        ],
        "strongest_objection": strongest_objection,
        "theorem_drift": theorem_drift or [],
        "missing_hypotheses": missing_hypotheses or [],
        "source_risks": source_risks or [],
        "example_risks": example_risks or [],
        "downgrade_reasons": downgrade_reasons or [],
    }


def _base_hypothesis_ledger(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "hypotheses": [
            {"name": "smooth", "status": "unclear", "source": "agent"},
            {"name": "projective", "status": "unclear", "source": "agent"},
            {"name": "very general versus all", "status": "missing", "source": "agent"},
        ],
        "missing_or_unclear": ["very general versus all", "exact source theorem"],
        "notes": ["P0 mock ledger flags theorem drift instead of repairing the claim."],
        "claim_precision": "imprecise",
    }


def _hodge_examples(claim_id: str, result: str = "inconclusive") -> dict:
    return {
        "claim_id": claim_id,
        "examples": [
            {
                "id": "ex_special_cubic",
                "template_id": "special_cubic_fourfold",
                "name": "Special cubic fourfold",
                "satisfies_all_claim_hypotheses": "unclear",
                "hypothesis_mismatches": ["special may not satisfy very-general assumptions"],
                "what_it_tests": ["all versus very general", "extra Hodge classes"],
                "expected_conclusion": "Should match the stated all-cubic conclusion if the claim is really universal.",
                "observed_or_inferred_behavior": "Extra Hodge classes create pressure on the proof step.",
                "result_for_current_idea": result,
                "source_or_reason": "Selected from machine example bank; exact source still candidate.",
                "nontriviality": "research_level",
            },
            {
                "id": "ex_very_general_cubic",
                "template_id": "very_general_cubic_fourfold",
                "name": "Very general cubic fourfold",
                "satisfies_all_claim_hypotheses": "unclear",
                "hypothesis_mismatches": [],
                "what_it_tests": ["very general target case"],
                "expected_conclusion": "This is the intended target if the claim is narrowed.",
                "observed_or_inferred_behavior": "Needs exact source before evidence use.",
                "result_for_current_idea": "inconclusive",
                "source_or_reason": "Selected from machine example bank; exact source still candidate.",
                "nontriviality": "research_level",
            },
        ],
    }


def _basic_checks(claim_id: str, status: str = "human_needed") -> dict:
    return {
        "claim_id": claim_id,
        "checks": [
            {
                "id": "check_special_cubic_dimension",
                "example_id": "ex_special_cubic",
                "claim_id": claim_id,
                "computed_quantity": "dimension",
                "input_data": ["cubic hypersurface in P5"],
                "method": "exact_arithmetic",
                "tool_or_source": "dimension of hypersurface in P5",
                "result": "4",
                "cross_check": [],
                "status": "checked",
                "notes": "Toy exact dimension check only.",
            },
            {
                "id": "check_special_cubic_hodge",
                "example_id": "ex_special_cubic",
                "claim_id": claim_id,
                "computed_quantity": "extra Hodge classes",
                "input_data": [],
                "method": "manual_review_needed",
                "tool_or_source": "cubic_fourfold_reference_placeholder",
                "result": "Not checked in P0 mock.",
                "cross_check": [],
                "status": status,
                "notes": "Placeholder source cannot support survived/failed evidence.",
            },
        ],
    }


def _hodge_all_cubic_fourfolds(text: str, run_id: str) -> dict[str, object]:
    claim = _base_claim(text, ["all", "very general", "special"])
    claim["conclusion"] = "A Hodge atom obstruction proves irrationality for all cubic fourfolds."
    claim_id = claim["id"]
    return {
        "locked_claim.yaml": claim,
        "hypothesis_ledger.yaml": _base_hypothesis_ledger(claim_id),
        "literature_review.yaml": _common_literature_review(claim_id),
        "examples.yaml": _hodge_examples(claim_id),
        "computation_checks.yaml": _basic_checks(claim_id),
        "counterexamples.yaml": {
            "claim_id": claim_id,
            "tests": [
                {
                    "id": "test_special_vs_very_general",
                    "operator_id": "special_vs_general_test",
                    "example_id": "ex_special_cubic",
                    "satisfies_all_claim_hypotheses": "unclear",
                    "target": "proof_step",
                    "result": "inconclusive",
                    "refutes_claim": False,
                    "refutes_step_only": True,
                    "exposes_missing_hypothesis": True,
                    "explanation": "Special cubic fourfolds pressure the all versus very general jump.",
                    "repaired_claim_suggestion": "State the claim for the precise genericity class actually used.",
                }
            ],
        },
        "tests.yaml": _common_test_report(
            claim_id,
            counterexample_ids=["test_special_vs_very_general"],
            summary="The all-cubic sketch remains fragile under special-versus-general pressure.",
        ),
        "source_grounding.yaml": _common_source_grounding(claim_id),
        "review.yaml": _parallel_review(
            claim_id,
            attempt_status="failed",
            verdict="repair_first",
            strategy_verdict="fail",
            evidence_verdict="unclear",
            line_verdict="unclear",
            line_applicability="sketch_only",
            strongest_objection="The claim drifts from very general behavior to all cubic fourfolds.",
            theorem_drift=["very general -> all"],
            missing_hypotheses=["genericity condition", "exact Hodge atom theorem"],
            source_risks=["candidate source only"],
            example_risks=["special cubic fourfold not resolved"],
            downgrade_reasons=["No source-backed theorem and no checked nontrivial example evidence."],
        ),
        "idea_grade.yaml": {
            "claim_id": claim_id,
            "status": "fragile",
            "score": 38,
            "confidence": "medium",
            "reasons": ["Interesting direction but all-versus-very-general drift is unresolved."],
            "survived_tests": [],
            "failed_tests": [],
            "counterexample_candidates": ["test_special_vs_very_general"],
            "missing_hypotheses": ["genericity"],
            "source_gaps": ["exact theorem for Hodge atom obstruction"],
            "repair_options": ["Narrow all cubic fourfolds to the intended genericity class."],
            "next_cheapest_test": "Pin the exact theorem statement and test special cubic fourfold assumptions.",
            "human_review_required": True,
            "proof_language_status": "idea_sketch",
        },
        "loop_control.yaml": _loop_control(claim_id),
        "strategy_synthesis.yaml": _strategy_synthesis(
            claim_id,
            run_id,
            status="fragile",
            base_score=38,
            source_status="candidate",
        ),
        "strategy_brief.yaml": _strategy_brief(
            claim_id,
            run_id,
            status="fragile",
            base_score=38,
            source_status="candidate",
        ),
        "final_report.md": "Mock critique: the idea is fragile, not proved. Main issue: all versus very general drift.\n",
    }


def _dropped_hypothesis(text: str, run_id: str) -> dict[str, object]:
    claim = _base_claim(text, ["dropped smoothness"])
    claim_id = claim["id"]
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    artifacts["locked_claim.yaml"] = claim
    artifacts["hypothesis_ledger.yaml"] = _base_hypothesis_ledger(claim_id)
    artifacts["counterexamples.yaml"] = {
        "claim_id": claim_id,
        "tests": [
            {
                "id": "test_drop_smoothness",
                "operator_id": "drop_hypothesis_test",
                "example_id": "ex_special_cubic",
                "satisfies_all_claim_hypotheses": False,
                "target": "hypothesis",
                "result": "failed",
                "refutes_claim": False,
                "refutes_step_only": True,
                "exposes_missing_hypothesis": True,
                "explanation": "Failure after dropping smoothness tests necessity, not the original theorem.",
                "repaired_claim_suggestion": "Keep smoothness explicit.",
            }
        ],
    }
    artifacts["idea_grade.yaml"] = {
        "claim_id": claim_id,
        "status": "fragile",
        "score": 42,
        "confidence": "medium",
        "reasons": ["Dropped-hypothesis pressure exposes proof fragility but does not refute the claim."],
        "survived_tests": [],
        "failed_tests": [],
        "counterexample_candidates": ["test_drop_smoothness"],
        "missing_hypotheses": ["smoothness must stay explicit"],
        "source_gaps": ["exact source missing"],
        "repair_options": ["Restore the dropped hypothesis."],
        "next_cheapest_test": "Test an example satisfying all hypotheses.",
        "human_review_required": True,
        "proof_language_status": "idea_sketch",
    }
    artifacts["tests.yaml"] = _common_test_report(
        claim_id,
        counterexample_ids=["test_drop_smoothness"],
        summary="Dropping a hypothesis exposes fragility but does not refute the locked claim.",
    )
    artifacts["strategy_synthesis.yaml"] = _strategy_synthesis(
        claim_id,
        run_id,
        status="fragile",
        base_score=42,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _strategy_brief(
        claim_id,
        run_id,
        status="fragile",
        base_score=42,
        source_status="candidate",
    )
    artifacts["final_report.md"] = "Mock critique: dropping a hypothesis found fragility, not a refutation.\n"
    return artifacts


def _fake_source_backed(text: str, run_id: str) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    artifacts["review.yaml"]["source_risks"] = ["A vague 'by Voisin' citation is only candidate, not source_backed."]
    artifacts["review.yaml"]["reviewers"][0]["objections"].append("The strategy relies on a vague source citation.")
    artifacts["final_report.md"] = "Mock critique: vague citations are downgraded to candidate source status.\n"
    return artifacts


def _unchecked_example(text: str, run_id: str) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    artifacts["examples.yaml"] = _hodge_examples("claim_001", result="inconclusive")
    artifacts["idea_grade.yaml"]["reasons"] = [
        "The relevant example calculation is human_needed, so it cannot count as survived or failed evidence."
    ]
    artifacts["idea_grade.yaml"]["survived_tests"] = []
    artifacts["idea_grade.yaml"]["failed_tests"] = []
    artifacts["tests.yaml"] = _common_test_report(
        "claim_001",
        counterexample_ids=["test_special_vs_very_general"],
        evidence_status="human_needed",
        summary="Unchecked example calculations block survived/failed test evidence.",
    )
    artifacts["strategy_synthesis.yaml"] = _strategy_synthesis(
        "claim_001",
        run_id,
        status="fragile",
        base_score=38,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _strategy_brief(
        "claim_001",
        run_id,
        status="fragile",
        base_score=38,
        source_status="candidate",
    )
    artifacts["final_report.md"] = "Mock critique: unchecked example calculations remain inconclusive.\n"
    return artifacts


def _promising_but_unproved(text: str, run_id: str) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]
    artifacts["hypothesis_ledger.yaml"] = {
        "claim_id": claim_id,
        "hypotheses": artifacts["locked_claim.yaml"]["hypotheses"],
        "missing_or_unclear": [],
        "notes": [
            "The claim is precise enough for the runtime scaffold; proof/source gaps remain separate."
        ],
        "claim_precision": "precise",
    }
    artifacts["computation_checks.yaml"]["checks"].append(
        {
            "id": "check_very_general_hodge_package_source",
            "example_id": "ex_very_general_cubic",
            "claim_id": claim_id,
            "computed_quantity": "very general Hodge atom package support",
            "input_data": ["very general cubic fourfold", "candidate Hodge atom obstruction package"],
            "method": "source_lookup",
            "tool_or_source": "mock exact source lookup for the very general support case",
            "result": (
                "A pinned source-style check supports continuing the very general case, "
                "but it does not prove the all-cubic statement."
            ),
            "cross_check": ["not used as proof completion", "special cubic pressure remains separate"],
            "status": "source_checked",
            "notes": "Non-toy support for a promising direction; still below proof status.",
        }
    )
    artifacts["counterexamples.yaml"]["tests"].append(
        {
            "id": "test_very_general_package_support",
            "operator_id": "source_checked_support_test",
            "example_id": "ex_very_general_cubic",
            "satisfies_all_claim_hypotheses": True,
            "target": "proof_step",
            "result": "survived",
            "refutes_claim": False,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": False,
            "explanation": (
                "The very general support case survives the current source-checked package test, "
                "which makes the route worth continuing without proving it."
            ),
            "repaired_claim_suggestion": None,
        }
    )
    artifacts["tests.yaml"] = {
        "claim_id": claim_id,
        "target": "user_sketch",
        "summary": (
            "A non-toy source-checked very-general support test survives, while special cases "
            "remain pressure tests."
        ),
        "tests": [
            {
                "id": "test_report_very_general_support",
                "description": "Check whether the intended very-general support package survives.",
                "uses_examples": ["ex_very_general_cubic"],
                "uses_counterexamples": ["test_very_general_package_support"],
                "result": "survived",
                "evidence_status": "source_checked",
                "main_observation": (
                    "This supports continuing the route, not claiming a completed proof."
                ),
                "next_action": "Pin the exact source and then retest special cubic pressure.",
            },
            {
                "id": "test_report_special_pressure",
                "description": "Keep special cubic fourfolds as pressure tests.",
                "uses_examples": ["ex_special_cubic"],
                "uses_counterexamples": ["test_special_vs_very_general"],
                "result": "inconclusive",
                "evidence_status": "human_needed",
                "main_observation": "The harder special example remains unresolved.",
                "next_action": "Find a precise source or computation for the special case.",
            },
        ],
    }
    artifacts["review.yaml"] = _parallel_review(
        claim_id,
        attempt_status="unclear",
        verdict="continue",
        strategy_verdict="pass",
        evidence_verdict="pass",
        line_verdict="unclear",
        line_applicability="sketch_only",
        strongest_objection="The idea has promising evidence, but no line-by-line proof is available.",
        source_risks=["candidate source still needs exact locator"],
        example_risks=["harder special examples remain untested"],
        downgrade_reasons=["Sketch-only line proof review blocks proof-like language."],
    )
    artifacts["idea_grade.yaml"] = {
        "claim_id": claim_id,
        "status": "promising",
        "score": 68,
        "confidence": "medium",
        "reasons": [
            "The current direction has a coherent strategy and no checked refutation, but it remains unproved."
        ],
        "survived_tests": ["test_very_general_package_support"],
        "failed_tests": [],
        "counterexample_candidates": ["test_special_vs_very_general"],
        "missing_hypotheses": [],
        "source_gaps": ["exact theorem for Hodge atom obstruction"],
        "repair_options": ["Pin the source and stress-test harder examples before writing proof-like language."],
        "next_cheapest_test": "Pin the exact source and rerun special cubic fourfold pressure tests.",
        "human_review_required": True,
        "proof_language_status": "proof_candidate",
    }
    artifacts["loop_control.yaml"] = _loop_control(claim_id)
    artifacts["strategy_synthesis.yaml"] = _strategy_synthesis(
        claim_id,
        run_id,
        status="promising",
        base_score=68,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _strategy_brief(
        claim_id,
        run_id,
        status="promising",
        base_score=68,
        source_status="candidate",
    )
    artifacts["final_report.md"] = (
        "Mock critique: the idea is promising but remains a proof candidate with explicit risks.\n"
    )
    return artifacts


def _refuted_by_all_hypotheses(text: str, run_id: str) -> dict[str, object]:
    claim = {
        "id": "claim_001",
        "raw_text": text.strip(),
        "normalized_statement": "For every integer n, if n is prime and n > 2, then n is even.",
        "objects": ["integer n"],
        "hypotheses": [
            {"name": "n is an integer", "status": "explicit", "source": "user"},
            {"name": "n is prime", "status": "explicit", "source": "user"},
            {"name": "n > 2", "status": "explicit", "source": "user"},
        ],
        "conclusion": "n is even",
        "ambiguous_terms": [],
        "locked": True,
        "repaired_from": None,
        "human_confirmed": False,
    }
    claim_id = claim["id"]
    return {
        "locked_claim.yaml": claim,
        "hypothesis_ledger.yaml": {
            "claim_id": claim_id,
            "hypotheses": claim["hypotheses"],
            "missing_or_unclear": [],
            "notes": ["The claim is precise enough to test by an all-hypotheses example."],
            "claim_precision": "precise",
        },
        "literature_review.yaml": {
            "claim_id": claim_id,
            "query_summary": "No literature is needed for this arithmetic counterexample fixture.",
            "candidate_references": [],
            "known_examples": ["n = 3"],
            "known_counterexample_families": ["odd primes greater than 2"],
            "notes": ["This fixture tests refutation logic, not source search."],
        },
        "examples.yaml": {
            "claim_id": claim_id,
            "examples": [
                {
                    "id": "ex_prime_three",
                    "template_id": "small_prime",
                    "name": "The integer 3",
                    "satisfies_all_claim_hypotheses": True,
                    "hypothesis_mismatches": [],
                    "what_it_tests": ["all hypotheses", "conclusion parity"],
                    "expected_conclusion": "3 should be even if the claim were true.",
                    "observed_or_inferred_behavior": "3 is odd.",
                    "result_for_current_idea": "failed",
                    "source_or_reason": "Exact arithmetic.",
                    "nontriviality": "routine",
                }
            ],
        },
        "computation_checks.yaml": {
            "claim_id": claim_id,
            "checks": [
                {
                    "id": "check_prime_three_parity",
                    "example_id": "ex_prime_three",
                    "claim_id": claim_id,
                    "computed_quantity": "3 mod 2",
                    "input_data": ["3"],
                    "method": "exact_arithmetic",
                    "tool_or_source": "integer arithmetic",
                    "result": "1, so 3 is odd",
                    "cross_check": ["3 is prime", "3 > 2"],
                    "status": "checked",
                    "notes": "The counterexample satisfies every stated hypothesis.",
                }
            ],
        },
        "counterexamples.yaml": {
            "claim_id": claim_id,
            "tests": [
                {
                    "id": "test_prime_three",
                    "operator_id": "satisfy_all_hypotheses_test",
                    "example_id": "ex_prime_three",
                    "satisfies_all_claim_hypotheses": True,
                    "target": "theorem",
                    "result": "failed",
                    "refutes_claim": True,
                    "refutes_step_only": False,
                    "exposes_missing_hypothesis": False,
                    "explanation": "n = 3 satisfies all hypotheses and violates the even conclusion.",
                    "repaired_claim_suggestion": "Replace 'even' with 'odd' or change the claim.",
                }
            ],
        },
        "tests.yaml": {
            "claim_id": claim_id,
            "target": "claim",
            "summary": "A checked all-hypotheses counterexample refutes the locked claim.",
            "tests": [
                {
                    "id": "test_report_prime_three",
                    "description": "Check n = 3 against the locked claim.",
                    "uses_examples": ["ex_prime_three"],
                    "uses_counterexamples": ["test_prime_three"],
                    "result": "failed",
                    "evidence_status": "checked",
                    "main_observation": "The conclusion fails while every stated hypothesis holds.",
                    "next_action": "Do not pursue this statement without changing it.",
                }
            ],
        },
        "source_grounding.yaml": {"claim_id": claim_id, "source_refs": [], "steps": []},
        "review.yaml": _parallel_review(
            claim_id,
            attempt_status="failed",
            verdict="refuted",
            strategy_verdict="fail",
            evidence_verdict="fail",
            line_verdict="unclear",
            line_applicability="sketch_only",
            strongest_objection="A checked example satisfies all hypotheses and violates the conclusion.",
            example_risks=["test_prime_three refutes the locked claim"],
            downgrade_reasons=["All-hypotheses counterexample found."],
        ),
        "idea_grade.yaml": {
            "claim_id": claim_id,
            "status": "refuted",
            "score": 3,
            "confidence": "high",
            "reasons": ["The checked example n = 3 refutes the locked claim."],
            "survived_tests": [],
            "failed_tests": ["test_prime_three"],
            "counterexample_candidates": ["test_prime_three"],
            "missing_hypotheses": [],
            "source_gaps": [],
            "repair_options": ["Change the conclusion or abandon the statement."],
            "next_cheapest_test": "Ask the mathematician whether to repair or discard the claim.",
            "human_review_required": True,
            "proof_language_status": "idea_sketch",
        },
        "loop_control.yaml": _loop_control(
            claim_id,
            should_stop=True,
            stop_reasons=["Checked all-hypotheses refutation found."],
            next_action="ask_human",
        ),
        "strategy_synthesis.yaml": _strategy_synthesis(
            claim_id,
            run_id,
            status="refuted",
            base_score=3,
            source_status="not_found",
        ),
        "strategy_brief.yaml": _strategy_brief(
            claim_id,
            run_id,
            status="refuted",
            base_score=3,
            source_status="not_found",
        ),
        "final_report.md": "Mock critique: the locked claim is refuted by n = 3.\n",
    }


def _likely_false_multiple_pressures(text: str, run_id: str) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]
    artifacts["counterexamples.yaml"]["tests"].append(
        {
            "id": "test_specialization_invariance_pressure",
            "operator_id": "specialization_test",
            "example_id": "ex_very_general_cubic",
            "satisfies_all_claim_hypotheses": "unclear",
            "target": "proof_step",
            "result": "inconclusive",
            "refutes_claim": False,
            "refutes_step_only": True,
            "exposes_missing_hypothesis": True,
            "explanation": (
                "The sketch assumes an obstruction survives specialization from very general "
                "to special fibers without a pinned theorem."
            ),
            "repaired_claim_suggestion": (
                "State the claim conditionally on a source-backed specialization theorem."
            ),
        }
    )
    artifacts["tests.yaml"] = _common_test_report(
        claim_id,
        counterexample_ids=[
            "test_special_vs_very_general",
            "test_specialization_invariance_pressure",
        ],
        summary=(
            "Multiple independent pressure tests point against the statement, but no checked "
            "all-hypotheses refutation has been produced."
        ),
    )
    artifacts["review.yaml"] = _parallel_review(
        claim_id,
        attempt_status="failed",
        verdict="likely_broken",
        strategy_verdict="fail",
        evidence_verdict="unclear",
        line_verdict="unclear",
        line_applicability="sketch_only",
        strongest_objection=(
            "The idea depends on very-general-to-all and specialization steps that are not source-backed."
        ),
        theorem_drift=["very general -> all", "specialization treated as automatic"],
        missing_hypotheses=["genericity condition", "specialization invariance theorem"],
        source_risks=["candidate source only"],
        example_risks=["special cubic pressure remains unresolved"],
        downgrade_reasons=["Several structural pressures point toward falsity."],
    )
    artifacts["idea_grade.yaml"] = {
        "claim_id": claim_id,
        "status": "likely_false",
        "score": 28,
        "confidence": "medium",
        "reasons": [
            "No checked concrete refutation yet, but two independent structural pressures are serious."
        ],
        "survived_tests": [],
        "failed_tests": [],
        "counterexample_candidates": [
            "test_special_vs_very_general",
            "test_specialization_invariance_pressure",
        ],
        "missing_hypotheses": ["genericity", "specialization invariance"],
        "source_gaps": ["exact source for the transfer from very general to all"],
        "repair_options": [
            "Narrow the claim.",
            "Pin the specialization theorem.",
            "Build a harder special-cubic example suite.",
        ],
        "next_cheapest_test": "Pin the transfer theorem or test the hardest special cubic family.",
        "human_review_required": True,
        "proof_language_status": "idea_sketch",
    }
    artifacts["loop_control.yaml"] = _loop_control(claim_id)
    artifacts["strategy_synthesis.yaml"] = _strategy_synthesis(
        claim_id,
        run_id,
        status="likely_false",
        base_score=28,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _strategy_brief(
        claim_id,
        run_id,
        status="likely_false",
        base_score=28,
        source_status="candidate",
    )
    artifacts["final_report.md"] = (
        "Mock critique: the idea is likely false under current hypotheses, but no checked "
        "all-hypotheses refutation is claimed.\n"
    )
    return artifacts


def _source_misapplication(text: str, run_id: str) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]
    artifacts["source_grounding.yaml"] = _misapplied_source_grounding(claim_id)
    artifacts["review.yaml"] = _parallel_review(
        claim_id,
        attempt_status="failed",
        verdict="repair_first",
        strategy_verdict="fail",
        evidence_verdict="unclear",
        line_verdict="unclear",
        line_applicability="sketch_only",
        strongest_objection="The cited theorem applies only to very general objects, not all objects.",
        theorem_drift=["very general -> all"],
        missing_hypotheses=["genericity condition"],
        source_risks=["source misapplied to a broader locked claim"],
        example_risks=["special cases remain untested"],
        downgrade_reasons=["Referencing marked the key theorem application as misapplied."],
    )
    artifacts["idea_grade.yaml"] = {
        "claim_id": claim_id,
        "status": "fragile",
        "score": 30,
        "confidence": "high",
        "reasons": ["The source application fails because very general was used as all."],
        "survived_tests": [],
        "failed_tests": [],
        "counterexample_candidates": ["test_special_vs_very_general"],
        "missing_hypotheses": ["very general"],
        "source_gaps": ["source theorem does not match locked claim"],
        "repair_options": ["Narrow the claim to very general objects or find a theorem for all objects."],
        "next_cheapest_test": "Pin the exact theorem and rerun the hypothesis ledger.",
        "human_review_required": True,
        "proof_language_status": "idea_sketch",
    }
    artifacts["strategy_synthesis.yaml"] = _strategy_synthesis(
        claim_id,
        run_id,
        status="fragile",
        base_score=30,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _strategy_brief(
        claim_id,
        run_id,
        status="fragile",
        base_score=30,
        source_status="candidate",
    )
    artifacts["final_report.md"] = (
        "Mock critique: Referencing marks the very-general-to-all citation as misapplied.\n"
    )
    return artifacts


def _true_theorem_classical_source(text: str, run_id: str) -> dict[str, object]:
    # Goal 42E cycle 6: a true classical theorem (groups of order p^2 are
    # abelian) must ENGAGE source grounding in smoke mode: one classical
    # citation with an exact locator, attribution, and a recorded
    # all-hypotheses match — held honestly at candidate. Smoke mode runs no
    # real source verification, so nothing here may reach source_backed.
    artifacts = _vague_claim(text, run_id)
    claim = artifacts["locked_claim.yaml"]
    claim["objects"] = ["finite groups of order p^2, p prime"]
    claim["hypotheses"] = [
        {"name": "the group has order p^2", "status": "explicit", "source": "user"},
        {"name": "p is prime", "status": "explicit", "source": "user"},
    ]
    claim["conclusion"] = "The group is abelian."
    claim_id = claim["id"]
    artifacts["hypothesis_ledger.yaml"] = {
        "claim_id": claim_id,
        "hypotheses": claim["hypotheses"],
        "missing_or_unclear": [],
        "notes": ["The claim is precise; both hypotheses are explicit in the statement."],
        "claim_precision": "precise",
    }
    artifacts["literature_review.yaml"] = {
        "claim_id": claim_id,
        "query_summary": "Classical finite group theory: order p^2 forces abelian.",
        "candidate_references": [
            {
                "id": "lit_p2_classical",
                "title": "Classical group theory text (class equation chapter)",
                "authors": ["Placeholder Author"],
                "locator": {
                    "kind": "theorem",
                    "value": "Corollary: groups of order p^2 are abelian",
                },
                "relevance": "possible_theorem_source",
                "status": "candidate",
                "why_relevant": (
                    "Standard corollary: a group of order p^2 has nontrivial "
                    "center, and G/Z(G) cyclic forces G abelian."
                ),
                "found_by": "literature_review",
            }
        ],
        "known_examples": [],
        "known_counterexample_families": [],
        "notes": [
            "Referencing must check exact locator and hypotheses before any "
            "step can claim reference support."
        ],
    }
    artifacts["source_grounding.yaml"] = {
        "claim_id": claim_id,
        "source_refs": [
            {
                "id": "src_p2_classical",
                "type": "book",
                "title": "Classical group theory text (class equation chapter)",
                "authors": ["Placeholder Author"],
                "locator": {
                    "kind": "theorem",
                    "value": "Corollary: groups of order p^2 are abelian",
                },
                "statement_or_summary": (
                    "Every group of order p^2, p prime, is abelian (via "
                    "nontrivial center and the G/Z(G)-cyclic criterion)."
                ),
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "candidate",
            }
        ],
        "steps": [
            {
                "proof_step_id": "step_p2_center",
                "needed_theorem": (
                    "Nontrivial center of a finite p-group plus the "
                    "G/Z(G)-cyclic-implies-abelian criterion."
                ),
                "status": "candidate",
                "source_refs": ["src_p2_classical"],
                "hypothesis_match": {"matches": True, "mismatches": []},
                "note": (
                    "Hypotheses match the locked claim; reference support "
                    "still requires real source verification (never earned "
                    "in smoke mode)."
                ),
            }
        ],
    }
    artifacts["tests.yaml"] = {
        "claim_id": claim_id,
        "target": "user_sketch",
        "summary": (
            "Tests are deferred pending verification of the classical source "
            "statement; the claim itself is precise."
        ),
        "tests": [],
    }
    artifacts["review.yaml"] = _parallel_review(
        claim_id,
        attempt_status="unclear",
        verdict="continue",
        strategy_verdict="pass",
        evidence_verdict="pass",
        line_verdict="unclear",
        line_applicability="sketch_only",
        strongest_objection=(
            "The classical route is plausible and a candidate source matches "
            "all hypotheses, but no line-by-line proof or verified source "
            "excerpt is available."
        ),
        source_risks=["candidate source still needs checked statement excerpt"],
        downgrade_reasons=["Sketch-only line proof review blocks proof-like language."],
    )
    artifacts["idea_grade.yaml"] = {
        "claim_id": claim_id,
        "status": "promising",
        "score": 70,
        "confidence": "medium",
        "reasons": [
            "A classical candidate source matches every stated hypothesis; "
            "the claim remains unproved here pending verification."
        ],
        "survived_tests": [],
        "failed_tests": [],
        "counterexample_candidates": [],
        "missing_hypotheses": [],
        "source_gaps": ["checked statement excerpt for the classical corollary"],
        "repair_options": [
            "Verify the exact source statement, then attempt the center/class-equation route."
        ],
        "next_cheapest_test": "Verify the classical corollary's exact statement and locator.",
        "human_review_required": True,
        "proof_language_status": "idea_sketch",
    }
    artifacts["loop_control.yaml"] = _loop_control(claim_id)
    artifacts["strategy_synthesis.yaml"] = _finite_group_strategy_synthesis(
        claim_id,
        run_id,
        case="p2_groups_true",
        status="promising",
        base_score=70,
        source_status="candidate",
    )
    artifacts["strategy_brief.yaml"] = _finite_group_strategy_brief(
        claim_id,
        run_id,
        case="p2_groups_true",
        status="promising",
        base_score=70,
        source_status="candidate",
    )
    artifacts["final_report.md"] = (
        "Mock critique: the classical-source route is engaged and held at "
        "candidate pending real verification.\n"
    )
    return artifacts


def _finite_group_case_data(text: str) -> dict[str, object]:
    key = _finite_group_scenario_key(text)
    if key == "order6_false":
        return {
            "case": key,
            "normalized": "Every group of order 6 is abelian.",
            "objects": ["finite groups of order 6"],
            "hypotheses": [
                {"name": "G is a group of order 6", "status": "explicit", "source": "user"},
            ],
            "conclusion": "G is abelian.",
            "status": "likely_false",
            "score": 25,
            "confidence": "medium",
            "reasons": [
                "A deterministic finite-group counterexample probe should test D3/S3 before any proof route."
            ],
            "source_gaps": [],
            "next": "Run the checked finite-group counterexample admissibility test.",
            "review": "A nonabelian order-6 table would refute this exact universal claim if admissible.",
        }
    if key == "p3_groups_false":
        return {
            "case": key,
            "normalized": "For every prime p, every group of order p^3 is abelian.",
            "objects": ["finite groups of order p^3", "prime numbers p"],
            "hypotheses": [
                {"name": "p is prime", "status": "explicit", "source": "user"},
                {"name": "G is a group of order p^3", "status": "explicit", "source": "user"},
            ],
            "conclusion": "G is abelian.",
            "status": "likely_false",
            "score": 25,
            "confidence": "medium",
            "reasons": [
                "The p^2 theorem does not automatically extend to p^3; nonabelian order-8 examples are expected pressure."
            ],
            "source_gaps": [],
            "next": "Check a concrete nonabelian order-8 group against the locked hypotheses.",
            "review": "The route should test D4/Q8-style examples instead of importing the p^2 proof.",
        }
    if key == "center_pgroup_true":
        return {
            "case": key,
            "normalized": "Every nontrivial finite p-group has nontrivial center.",
            "objects": ["nontrivial finite p-groups", "their centers"],
            "hypotheses": [
                {"name": "G is a finite p-group", "status": "explicit", "source": "user"},
                {"name": "G is nontrivial", "status": "explicit", "source": "user"},
            ],
            "conclusion": "The center Z(G) is nontrivial.",
            "status": "promising",
            "score": 62,
            "confidence": "medium",
            "reasons": [
                "The class-equation route is the natural finite-group proof strategy, pending source or line proof."
            ],
            "source_gaps": ["exact source or line proof for the class equation step"],
            "next": "Pin the class equation and attack the Z(G) nontriviality subgoal.",
            "review": "A class-equation proof route is plausible but not checked here.",
        }
    return {
        "case": "cyclic_quotient_center_step",
        "normalized": "If G/Z(G) is cyclic, then G is abelian.",
        "objects": ["a group G", "the center Z(G)", "the quotient group G/Z(G)"],
        "hypotheses": [
            {"name": "G is a group", "status": "explicit", "source": "user"},
            {"name": "G/Z(G) is cyclic", "status": "explicit", "source": "user"},
        ],
        "conclusion": "G is abelian.",
        "status": "promising",
        "score": 64,
        "confidence": "medium",
        "reasons": [
            "The standard coset-representative route is available, pending line-by-line proof."
        ],
        "source_gaps": ["exact source or line proof for the cyclic center quotient lemma"],
        "next": "Write the coset representative calculation against a named proof subgoal.",
        "review": "The proof must use the center specifically; an arbitrary cyclic quotient is not enough.",
    }


def _finite_group_claim(text: str, run_id: str) -> dict[str, object]:
    data = _finite_group_case_data(text)
    claim = _base_claim(text, [])
    claim["normalized_statement"] = data["normalized"]
    claim["objects"] = data["objects"]
    claim["hypotheses"] = data["hypotheses"]
    claim["conclusion"] = data["conclusion"]
    claim["ambiguous_terms"] = []
    claim_id = claim["id"]
    status = str(data["status"])
    score = int(data["score"])
    case = str(data["case"])
    artifacts = {
        "locked_claim.yaml": claim,
        "hypothesis_ledger.yaml": {
            "claim_id": claim_id,
            "hypotheses": claim["hypotheses"],
            "missing_or_unclear": [],
            "notes": ["Finite-group claim matched by deterministic scaffold; no status is earned."],
            "claim_precision": "precise",
        },
        "literature_review.yaml": {
            "claim_id": claim_id,
            "query_summary": f"Finite group theory route search for: {data['normalized']}",
            "candidate_references": [],
            "known_examples": [],
            "known_counterexample_families": [],
            "notes": ["No reference support is earned in the mock scaffold."],
        },
        "examples.yaml": {"claim_id": claim_id, "examples": []},
        "computation_checks.yaml": {"claim_id": claim_id, "checks": []},
        "counterexamples.yaml": {"claim_id": claim_id, "tests": []},
        "tests.yaml": {
            "claim_id": claim_id,
            "target": "user_sketch",
            "summary": "Finite-group smoke route selected; tests remain pending.",
            "tests": [],
        },
        "source_grounding.yaml": {"claim_id": claim_id, "source_refs": [], "steps": []},
        "review.yaml": _parallel_review(
            claim_id,
            attempt_status="unclear",
            verdict="continue",
            strategy_verdict="pass",
            evidence_verdict="unclear",
            line_verdict="unclear",
            line_applicability="sketch_only",
            strongest_objection=str(data["review"]),
            source_risks=list(data["source_gaps"]),
            downgrade_reasons=["No checked proof or source-backed step is available yet."],
        ),
        "idea_grade.yaml": {
            "claim_id": claim_id,
            "status": status,
            "score": score,
            "confidence": str(data["confidence"]),
            "reasons": list(data["reasons"]),
            "survived_tests": [],
            "failed_tests": [],
            "counterexample_candidates": [],
            "missing_hypotheses": [],
            "source_gaps": list(data["source_gaps"]),
            "repair_options": [],
            "next_cheapest_test": str(data["next"]),
            "human_review_required": True,
            "proof_language_status": "idea_sketch",
        },
        "loop_control.yaml": _loop_control(claim_id),
        "strategy_synthesis.yaml": _finite_group_strategy_synthesis(
            claim_id,
            run_id,
            case=case,
            status=status,
            base_score=score,
            source_status="candidate",
        ),
        "strategy_brief.yaml": _finite_group_strategy_brief(
            claim_id,
            run_id,
            case=case,
            status=status,
            base_score=score,
            source_status="candidate",
        ),
        "final_report.md": (
            "Mock critique: finite-group claim locked precisely; proof/search "
            "status remains an unchecked idea sketch.\n"
        ),
    }
    return artifacts


def _human_feedback_scenario(
    scenario: str,
    text: str,
    run_id: str,
    project_id: str,
) -> dict[str, object]:
    artifacts = _hodge_all_cubic_fourfolds(text, run_id)
    claim_id = artifacts["locked_claim.yaml"]["id"]
    if scenario == "human_marks_example_interesting":
        artifacts.update(
            _human_feedback_artifacts(
                claim_id,
                run_id,
                project_id=project_id,
                target_type="example",
                target_artifact="examples.yaml",
                target_id="ex_special_cubic",
                labels=["interesting"],
                requested_actions=["promote_ranking"],
                reason="This example may reveal the all-versus-very-general issue.",
                direction="up",
                weight="medium",
            )
        )
    elif scenario == "human_marks_example_misleading_not_evidence":
        artifacts.update(
            _human_feedback_artifacts(
                claim_id,
                run_id,
                project_id=project_id,
                target_type="example",
                target_artifact="examples.yaml",
                target_id="ex_special_cubic",
                labels=["misleading"],
                requested_actions=["demote_ranking"],
                reason="This example is superficially related but does not test the key source gap.",
                direction="down",
                weight="medium",
            )
        )
    elif scenario == "human_promotes_dangerous_counterexample":
        artifacts.update(
            _human_feedback_artifacts(
                claim_id,
                run_id,
                project_id=project_id,
                target_type="counterexample_test",
                target_artifact="counterexamples.yaml",
                target_id="test_special_vs_very_general",
                labels=["dangerous"],
                requested_actions=["prioritize_counterexample_testing"],
                reason="Special cubic pressure is the most dangerous place for theorem drift.",
                direction="up",
                weight="high",
            )
        )
    elif scenario == "human_feedback_does_not_upgrade_source_status":
        artifacts.update(
            _human_feedback_artifacts(
                claim_id,
                run_id,
                project_id=project_id,
                target_type="source_ref",
                target_artifact="source_grounding.yaml",
                target_id="src_candidate_001",
                labels=["needs_source"],
                requested_actions=["run_referencing"],
                reason="The remembered theorem needs an exact locator and hypothesis match.",
                direction="up",
                weight="medium",
            )
        )
    else:
        raise ValueError(f"unknown human feedback scenario {scenario}")
    artifacts["final_report.md"] = (
        "Mock critique: human feedback was recorded as ranking guidance only; "
        "evidence and source status remain unchanged.\n"
    )
    return artifacts


def _vague_claim(text: str, run_id: str) -> dict[str, object]:
    claim = _base_claim(text, [])
    # An arbitrary / not-yet-understood claim has NO determined hypotheses. Do not
    # leak the algebraic-geometry placeholder triple (smooth / projective / base
    # field C) or stale ambiguous-term junk onto a generic claim — the claim card
    # would otherwise show hypotheses that are not in this problem at all. The
    # ledger records that the hypotheses are still missing/unclear.
    claim["objects"] = ["not yet determined"]
    claim["hypotheses"] = []
    claim["ambiguous_terms"] = []
    claim["conclusion"] = "Unclear conclusion."
    claim_id = claim["id"]
    return {
        "locked_claim.yaml": claim,
        "hypothesis_ledger.yaml": {
            "claim_id": claim_id,
            "hypotheses": claim["hypotheses"],
            "missing_or_unclear": ["main objects", "hypotheses", "conclusion"],
            "notes": ["The sketch is too vague to test."],
            "claim_precision": "unclear",
        },
        "literature_review.yaml": {
            "claim_id": claim_id,
            "query_summary": "No useful literature pass can run until the claim is precise.",
            "candidate_references": [],
            "known_examples": [],
            "known_counterexample_families": [],
            "notes": ["Clarify the claim before literature triage."],
        },
        "examples.yaml": {"claim_id": claim_id, "examples": []},
        "computation_checks.yaml": {"claim_id": claim_id, "checks": []},
        "counterexamples.yaml": {"claim_id": claim_id, "tests": []},
        "tests.yaml": {
            "claim_id": claim_id,
            "target": "user_sketch",
            "summary": "The claim is too vague for test selection.",
            "tests": [],
        },
        "source_grounding.yaml": {"claim_id": claim_id, "source_refs": [], "steps": []},
        "review.yaml": _parallel_review(
            claim_id,
            attempt_status="failed",
            verdict="unclear",
            strategy_verdict="fail",
            evidence_verdict="unclear",
            line_verdict="unclear",
            line_applicability="sketch_only",
            strongest_objection="The claim is not precise enough to test.",
            missing_hypotheses=["objects", "hypotheses", "conclusion"],
            downgrade_reasons=["Too vague to test."],
        ),
        "idea_grade.yaml": {
            "claim_id": claim_id,
            "status": "unclear",
            "score": 10,
            "confidence": "high",
            "reasons": ["The claim is too vague to evaluate."],
            "survived_tests": [],
            "failed_tests": [],
            "counterexample_candidates": [],
            "missing_hypotheses": ["objects", "hypotheses", "conclusion"],
            "source_gaps": [],
            "repair_options": ["Restate the claim with explicit objects, hypotheses, and conclusion."],
            "next_cheapest_test": "Ask the user to lock a precise statement.",
            "human_review_required": True,
            "proof_language_status": "idea_sketch",
        },
        "loop_control.yaml": _loop_control(
            claim_id,
            should_stop=True,
            stop_reasons=["Claim is too vague for productive looping."],
            next_action="ask_human",
        ),
        "strategy_synthesis.yaml": _strategy_synthesis(
            claim_id,
            run_id,
            status="unclear",
            base_score=10,
            source_status="not_found",
        ),
        "strategy_brief.yaml": _strategy_brief(
            claim_id,
            run_id,
            status="unclear",
            base_score=10,
            source_status="not_found",
        ),
        "final_report.md": "Mock critique: the claim is unclear and needs restatement before testing.\n",
    }


def _write_memory_entry(run_dir: Path, output_root: Path, project_id: str, run_id: str) -> None:
    # Unified writer: all memory writing/index logic lives in espresso_proof.memory.
    write_run_memory(
        output_root / "projects" / project_id,
        run_dir,
        project_id=project_id,
        run_id=run_id,
    )
