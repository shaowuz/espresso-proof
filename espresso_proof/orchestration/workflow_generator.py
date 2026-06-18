from __future__ import annotations

import re
from copy import deepcopy
from typing import Literal

from espresso_proof.kernel.models import WorkflowSpec

DomainKey = Literal["hodge_atoms", "coherent_base_change", "stacks_descent", "generic_ag"]


DEFAULT_AGENT_FLOW = [
    "Claim",
    "Hypotheses",
    "Literature Review",
    "Examples",
    "Counterexamples",
    "Evidence Auditor",
    "Test",
    "Referencing",
    "Review",
    "Grade",
    "Loop Control",
    "Strategy Synthesizer",
    "Strategy Brief",
    "Human Choice",
    "Memory",
]

DOMAIN_KEYWORDS: dict[DomainKey, tuple[str, ...]] = {
    "hodge_atoms": (
        "hodge atom",
        "hodge",
        "kkpy",
        "kkp",
        "mirror symmetry",
        "cubic fourfold",
        "f bundle",
        "f-bundle",
        "variation of hodge",
        "mixed hodge",
        "d-module",
        "d module",
        "perverse sheaf",
        "quantum cohomology",
        "gromov witten",
        "guere",
    ),
    "coherent_base_change": (
        "coherent sheaf",
        "coherent",
        "base change",
        "cohomology and base change",
        "flat base change",
        "higher direct image",
        "direct image",
        "semicontinuity",
        "proper morphism",
        "flatness",
        "tor",
        "derived pullback",
        "cech",
        "hartshorne iii",
        "ega iii",
    ),
    "stacks_descent": (
        "stack",
        "stacks",
        "algebraic stack",
        "descent",
        "representability",
        "quotient stack",
        "fibered category",
        "fppf",
        "fpqc",
        "etale",
        "site",
        "topology",
        "atlas",
        "diagonal",
        "artin",
        "deligne mumford",
        "2 category",
        "2-categorical",
        "effective descent",
        "moduli stack",
    ),
    "generic_ag": (
        "algebraic geometry",
        "scheme",
        "variety",
        "morphism",
        "divisor",
        "sheaf",
        "cohomology",
        "projective",
        "smooth",
        "proper",
        "flat",
        "noetherian",
        "generic",
    ),
}


DOMAIN_TEMPLATES: dict[DomainKey, dict] = {
    "hodge_atoms": {
        "name": "hodge_atoms_mirror_symmetry",
        "domain": "Hodge theoretic mirror symmetry and Hodge atoms",
        "core_corpus": [
            {
                "id": "KKP_0806_0107",
                "title": "Hodge theoretic aspects of mirror symmetry",
                "url": "https://arxiv.org/abs/0806.0107",
                "role": "core",
            },
            {
                "id": "KKPY_2508_05105",
                "title": "Birational Invariants from Hodge Structures and Quantum Multiplication",
                "url": "https://arxiv.org/abs/2508.05105",
                "role": "core",
            },
            {
                "id": "GUERE_2603_04518",
                "title": "On the irrationality of cubic fourfolds",
                "url": "https://arxiv.org/html/2603.04518v1",
                "role": "core",
            },
        ],
        "background_theories": [
            "classical Hodge theory",
            "noncommutative Hodge structures",
            "quantum cohomology",
            "Gromov-Witten theory",
            "D-modules",
            "perverse sheaves",
            "mixed Hodge modules",
            "birational geometry",
        ],
        "required_examples": [
            "point",
            "P1",
            "elliptic curve",
            "higher genus curve",
            "K3 surface",
            "abelian surface",
            "Calabi-Yau threefold",
            "cubic threefold",
            "special cubic fourfold",
            "very general cubic fourfold",
        ],
        "canonical_example_templates": [
            "p1",
            "elliptic_curve",
            "k3_surface",
            "abelian_surface",
            "very_general_cubic_fourfold",
            "special_cubic_fourfold",
        ],
        "pathology_templates": [
            "special_vs_very_general_cubic_fourfold",
            "extra_hodge_classes",
            "non_semisimple_quantum_cohomology",
            "bad_blowup_center",
            "singular_degeneration",
            "ordinary_hodge_substructure_confused_with_hodge_atom",
        ],
        "test_operators": [
            "satisfy_all_hypotheses_test",
            "special_vs_general_test",
            "drop_hypothesis_test",
            "coefficient_change_test",
            "degeneration_test",
            "hodge_class_control_test",
        ],
        "conditional_agents": [
            {
                "name": "Concepts",
                "trigger": "Definitions mix Hodge atoms, F-bundles, VHS, D-modules, perverse sheaves, or mixed Hodge modules.",
            },
            {
                "name": "Locality",
                "trigger": "A proof step uses family, degeneration, or local-to-global behavior.",
            },
        ],
        "non_goals": [
            "Lean formalization is explicitly rejected by default for Hodge atom research statements.",
            "automatic proof generation",
            "claiming irrationality from an unsourced obstruction",
            "treating very general and special cubic fourfolds interchangeably",
            "inventing Hodge numbers, ranks, or invariants",
            "broad web search without provenance",
        ],
        "admission_focus": [
            "Confirm KKP, KKPY, and cubic-fourfold sources are the intended core corpus.",
            "Run a trial fixture involving special versus very general cubic fourfolds.",
            "Human review must confirm Hodge-atom terminology before memory reuse.",
        ],
    },
    "coherent_base_change": {
        "name": "coherent_base_change",
        "domain": "Coherent sheaf cohomology and base change in algebraic geometry",
        "core_corpus": [
            {
                "id": "stacks_cohomology_of_schemes",
                "title": "Stacks Project, More on Cohomology of Schemes",
                "url": "https://stacks.math.columbia.edu",
                "role": "core",
            },
            {
                "id": "hartshorne_ag_iii_12",
                "title": "Hartshorne, Algebraic Geometry, Chapter III, Section 12",
                "url": "isbn:9780387902449",
                "role": "core",
            },
            {
                "id": "ega_iii_cohomology_base_change",
                "title": "EGA III, Cohomologie des faisceaux coherents",
                "url": "https://stacks.math.columbia.edu/bibliography#EGA3",
                "role": "background",
            },
        ],
        "background_theories": [
            "schemes",
            "coherent sheaves",
            "proper morphisms",
            "flat morphisms",
            "derived functors",
            "higher direct images",
            "Tor and derived pullback",
            "semicontinuity",
            "formal functions",
            "generic freeness",
        ],
        "required_examples": [
            "affine morphism with vanishing higher cohomology",
            "projective space over a field",
            "projective bundle over an affine base",
            "family of smooth curves",
            "family of plane conics degenerating to a reducible fiber",
            "non-flat family over a discrete valuation ring",
            "coherent sheaf not flat over the base",
            "proper morphism with jumping h0",
            "nonproper open family",
        ],
        "canonical_example_templates": [
            "projective_space_relative_twists",
            "family_of_curves_line_bundle",
            "flat_projective_family_constant_hilbert_polynomial",
            "nonflat_dvr_family",
            "skyscraper_sheaf_moving_support",
            "open_nonproper_family",
        ],
        "pathology_templates": [
            "non_flat_base_change",
            "jumping_cohomology",
            "missing_properness",
            "missing_flatness",
            "local_global_mismatch",
            "tor_terms_ignored",
            "upper_semicontinuity_treated_as_constancy",
            "pointwise_base_change_treated_as_neighborhood_statement",
        ],
        "test_operators": [
            "satisfy_all_hypotheses_test",
            "flatness_test",
            "properness_test",
            "finite_presentation_test",
            "derived_base_change_test",
            "tor_independence_test",
            "fiber_cohomology_dimension_test",
            "local_global_compatibility_test",
            "drop_hypothesis_test",
        ],
        "conditional_agents": [
            {
                "name": "Locality",
                "trigger": "A proof uses affine covers, stalks, localization, descent, or local-to-global arguments.",
            },
            {
                "name": "Concepts",
                "trigger": "A proof mixes ordinary base change, derived base change, semicontinuity, flatness, or formal functions.",
            },
        ],
        "non_goals": [
            "Lean formalization is explicitly rejected by default for coherent base change statements.",
            "fully automatic proof generation",
            "claiming base change without checking properness, flatness, and finiteness hypotheses",
            "treating semicontinuity as constancy",
            "replacing source-grounded theorem lookup",
            "broad web search without provenance",
        ],
        "admission_focus": [
            "Confirm which base-change theorem family is in scope.",
            "Run a trial fixture involving non-flat base change or jumping cohomology.",
            "Human review must confirm properness, flatness, and finiteness conventions.",
        ],
    },
    "stacks_descent": {
        "name": "stacks_descent",
        "domain": "Algebraic stacks, descent, representability, and locality checks",
        "core_corpus": [
            {
                "id": "stacks_project_stacks",
                "title": "Stacks Project, Algebraic Stacks",
                "url": "https://stacks.math.columbia.edu",
                "role": "core",
            },
            {
                "id": "olsson_algebraic_spaces_and_stacks",
                "title": "Algebraic Spaces and Stacks",
                "url": "isbn:9780821843814",
                "role": "background",
            },
            {
                "id": "laumon_moret_bailly",
                "title": "Champs algebriques",
                "url": "isbn:9783540657613",
                "role": "background",
            },
        ],
        "background_theories": [
            "sites and topologies",
            "fibered categories",
            "algebraic stacks",
            "algebraic spaces",
            "representable morphisms",
            "diagonal criteria",
            "smooth and etale atlases",
            "fppf and fpqc descent",
            "2-categorical coherence",
        ],
        "required_examples": [
            "affine scheme as a stack",
            "algebraic space with etale cover",
            "classifying stack BG",
            "quotient stack [X/G]",
            "doubled origin",
            "non-separated algebraic space",
            "descent datum on an fpqc cover",
            "stack with non-representable diagonal",
            "moduli stack with automorphisms",
        ],
        "canonical_example_templates": [
            "scheme_as_stack",
            "classifying_stack_bg",
            "quotient_stack_x_mod_g",
            "algebraic_space_etale_cover",
            "moduli_stack_with_automorphisms",
            "descent_datum_on_fpqc_cover",
        ],
        "pathology_templates": [
            "ineffective_descent_datum",
            "wrong_topology_for_descent",
            "representability_not_stable_under_claimed_base_change",
            "non_representable_diagonal",
            "automorphisms_ignored",
            "atlas_property_not_local_in_claimed_topology",
            "two_categorical_coherence_gap",
        ],
        "test_operators": [
            "satisfy_all_hypotheses_test",
            "site_topology_consistency_test",
            "effective_descent_test",
            "representability_test",
            "diagonal_representability_test",
            "atlas_locality_test",
            "base_change_stability_test",
            "two_categorical_coherence_test",
            "drop_hypothesis_test",
        ],
        "conditional_agents": [
            {
                "name": "Locality",
                "trigger": "A proof claims a property is Zariski, etale, smooth, fppf, or fpqc local.",
            },
            {
                "name": "Representability",
                "trigger": "A proof step requires a morphism, diagonal, fiber product, or functor to be representable.",
            },
            {
                "name": "Descent",
                "trigger": "A proof uses effective descent or gluing data.",
            },
        ],
        "non_goals": [
            "Lean formalization is explicitly rejected by default for stacks and descent research statements.",
            "fully automatic proof generation",
            "treating every locality statement as fpqc-local without proof",
            "ignoring automorphisms or 2-categorical compatibility",
            "replacing source-grounded representability checks",
            "broad web search without provenance",
        ],
        "admission_focus": [
            "Confirm the intended topology: Zariski, etale, smooth, fppf, or fpqc.",
            "Run a trial fixture involving quotient stacks or ineffective descent.",
            "Human review must confirm representability and diagonal conventions.",
        ],
    },
    "generic_ag": {
        "name": "generic_algebraic_geometry_critic",
        "domain": "General algebraic geometry proof-sketch critique",
        "core_corpus": [
            {
                "id": "user_prompt",
                "title": "User research prompt",
                "url": "local:user_prompt",
                "role": "core",
            },
            {
                "id": "stacks_project_background",
                "title": "Stacks Project background reference",
                "url": "https://stacks.math.columbia.edu",
                "role": "background",
            },
        ],
        "background_theories": [
            "schemes",
            "varieties",
            "morphisms",
            "sheaves",
            "cohomology",
            "smoothness",
            "properness",
            "flatness",
            "genericity",
        ],
        "required_examples": [
            "affine line",
            "projective line",
            "smooth projective curve",
            "nodal curve",
            "projective space",
            "blowup of a point",
            "nonreduced scheme",
            "nonproper open variety",
            "family over a discrete valuation ring",
        ],
        "canonical_example_templates": [
            "affine_line",
            "projective_line",
            "smooth_projective_curve",
            "nodal_curve",
            "projective_space",
            "blowup_of_point",
            "dvr_family",
        ],
        "pathology_templates": [
            "missing_smoothness",
            "missing_properness",
            "missing_flatness",
            "special_vs_general_fiber",
            "nonreduced_structure_ignored",
            "local_global_mismatch",
            "field_extension_mismatch",
        ],
        "test_operators": [
            "satisfy_all_hypotheses_test",
            "drop_hypothesis_test",
            "special_vs_general_test",
            "base_field_change_test",
            "local_global_compatibility_test",
            "degeneration_test",
        ],
        "conditional_agents": [
            {
                "name": "Locality",
                "trigger": "A proof uses local-to-global, base change, descent, or genericity.",
            },
            {
                "name": "Concepts",
                "trigger": "Definitions or hypotheses are ambiguous.",
            },
        ],
        "non_goals": [
            "Lean formalization is explicitly rejected by default for research-level algebraic geometry statements.",
            "fully automatic proof generation",
            "unsourced theorem usage",
            "treating generic and all-points statements interchangeably",
            "replacing expert mathematical review",
            "broad web search without provenance",
        ],
        "admission_focus": [
            "Confirm the domain is not better served by a more specialized workflow.",
            "Run a trial fixture with a missing hypothesis or special-versus-general failure.",
            "Human review must confirm the source policy and non-goals.",
        ],
    },
}


def classify_domain(prompt_text: str) -> DomainKey:
    """Classify a research prompt into the v0 deterministic domain set."""
    normalized = _normalize_prompt(prompt_text)
    if not normalized:
        return "generic_ag"

    scores = {
        domain: sum(_keyword_score(normalized, keyword) for keyword in keywords)
        for domain, keywords in DOMAIN_KEYWORDS.items()
    }
    scores["generic_ag"] = max(scores["generic_ag"], 1)
    best_domain = max(
        ("hodge_atoms", "coherent_base_change", "stacks_descent", "generic_ag"),
        key=lambda domain: (scores[domain], _tie_break_priority(domain)),
    )
    return best_domain


def generate_workflow_spec(prompt_text: str, project_name: str | None = None) -> dict:
    """Generate a schema-valid draft WorkflowSpec dict for a research prompt."""
    domain_key = classify_domain(prompt_text)
    template = deepcopy(DOMAIN_TEMPLATES[domain_key])
    name = _slug(project_name) if project_name else template.pop("name")

    spec = {
        "name": name,
        "mode": "idea_test_critique",
        "status": "draft",
        "domain": template["domain"],
        "domain_key": domain_key,
        "source": {
            "kind": "deterministic_v0_generator",
            "uses_network": False,
            "uses_live_llm": False,
            "prompt_fingerprint": _prompt_fingerprint(prompt_text),
        },
        "research_intake": {
            "raw_prompt": prompt_text,
            "domain": template["domain"],
            "domain_key": domain_key,
            "subdomains": template["background_theories"],
            "core_sources": [item["id"] for item in template["core_corpus"]],
            "reference_policy": "local-first candidate sources; Referencing decides source_backed status",
            "primary_tasks": [
                "proof sketch critique",
                "hypothesis audit",
                "examples and counterexample pressure",
                "source grounding",
                "idea grading",
            ],
            "risk_profile": template["pathology_templates"],
        },
        "core_corpus": template["core_corpus"],
        "include_references": True,
        "background_theories": template["background_theories"],
        "agent_flow": list(DEFAULT_AGENT_FLOW),
        "default_critique_flow": list(DEFAULT_AGENT_FLOW),
        "conditional_agents": template["conditional_agents"],
        "primary_outputs": [
            "agent_contracts",
            "literature_review",
            "reference_index",
            "example_bank",
            "hypothesis_ledger",
            "counterexample_report",
            "test_report",
            "source_grounding_report",
            "review",
            "idea_grade",
            "loop_control",
            "strategy_synthesis",
            "strategy_brief",
            "human_choice",
            "inspectable_agent_outputs",
        ],
        "required_examples": template["required_examples"],
        "canonical_example_templates": template["canonical_example_templates"],
        "pathology_templates": template["pathology_templates"],
        "test_operators": template["test_operators"],
        "verification_boundaries": {
            "source_backed": "References and exact theorem statements must cite a source; Referencing decides applicability.",
            "informal_critique": "Critique can be heuristic, but must be labeled as inferred or speculative unless sourced.",
            "computation_checked": "Only small, inspectable computations may count as checked evidence.",
            "lean_checked": "Lean-light may use existing declarations if available; no new research formalization by default.",
            "human_confirmed": "Required before treating this workflow or statement as reusable project memory.",
        },
        "lean_policy": {
            "mode": "lean_light",
            "user_facing_option": False,
            "use_existing_mathlib_results": True,
            "allow_new_lean_formalization": False,
            "allow_lean_proof_repair": False,
            "skip_if_no_result_exists": True,
            "never_block_critique_on_lean": True,
            "fallback_agents_if_no_result": ["Review", "Counterexamples"],
        },
        "user_preferences": {
            "style_profile": "examples_first",
            "default_reasoning_intensity": "high",
            "default_exploration_mode": "normal_pass",
            "preferred_outputs": [
                "examples",
                "counterexamples",
                "pathology_tests",
                "source_grounding_report",
                "idea_grade",
                "strategy_brief",
            ],
            "avoid_outputs": [
                "full_proof_claims",
                "unsourced_theorem_usage",
                "lean_formalization_attempts",
            ],
        },
        "agent_settings": _agent_settings(domain_key),
        "admission_metadata": {
            "status": "draft_requires_human_confirmation",
            "next_checks": [
                "validate workflow spec",
                "attach agent contracts",
                "create one trial fixture",
                "run trial fixture through artifact protocol",
                "collect human confirmation",
            ],
            "human_review_gate": [
                "core corpus",
                "required examples",
                "pathology templates",
                "non-goals",
                "cost policy",
            ],
            "domain_specific_focus": template["admission_focus"],
        },
        "non_goals": template["non_goals"],
        "cost_policy": {
            "default_model_tier": "medium",
            "escalation_rule": "Use stronger review only after examples, counterexample pressure, and source gaps are visible.",
            "stop_rule": "Stop when a concrete refutation appears, a missing source blocks progress, or two no-progress cycles occur.",
            "human_budget_gate": "Ask before deep_exploration or long_watch.",
        },
        "design_principles": [
            "multi_attempt_not_single_route",
            "generator_and_review_are_separate",
            "every_proof_step_needs_source_grounding",
            "failed_attempts_enter_memory",
            "specialist_outputs_are_inspectable",
            "human_major_decisions_are_gated",
        ],
    }

    WorkflowSpec.model_validate(spec)
    return spec


def generate_agent_spec(prompt_text: str, project_name: str | None = None) -> dict:
    """Compatibility alias for generate-agent integration."""
    return generate_workflow_spec(prompt_text, project_name=project_name)


def canonical_example_templates_for(domain: DomainKey) -> list[str]:
    return list(DOMAIN_TEMPLATES[domain]["canonical_example_templates"])


def pathology_templates_for(domain: DomainKey) -> list[str]:
    return list(DOMAIN_TEMPLATES[domain]["pathology_templates"])


def _normalize_prompt(prompt_text: str) -> str:
    text = prompt_text.lower()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9+\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return f" {text.strip()} "


def _keyword_score(normalized: str, keyword: str) -> int:
    phrase = _normalize_prompt(keyword).strip()
    if not phrase:
        return 0
    if " " in phrase or "-" in phrase:
        return 3 if f" {phrase} " in normalized else 0
    return 1 if f" {phrase} " in normalized else 0


def _tie_break_priority(domain: DomainKey) -> int:
    priorities: dict[DomainKey, int] = {
        "hodge_atoms": 4,
        "coherent_base_change": 3,
        "stacks_descent": 2,
        "generic_ag": 1,
    }
    return priorities[domain]


def _slug(value: str) -> str:
    normalized = _normalize_prompt(value).strip()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug or "generated_workflow"


def _prompt_fingerprint(prompt_text: str) -> str:
    normalized = _normalize_prompt(prompt_text).strip()
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
    return f"det-v0-{checksum % 1_000_000:06d}"


def _agent_settings(domain_key: DomainKey) -> dict[str, dict[str, str]]:
    settings = {
        "Literature Review": {
            "reasoning_intensity": "medium",
            "model_tier": "medium",
            "output_bias": "retrieval",
            "budget": "normal",
            "verbosity": "normal",
            "exploration_mode": "normal_pass",
        },
        "Examples": {
            "reasoning_intensity": "high",
            "model_tier": "medium",
            "output_bias": "examples",
            "budget": "extended",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        },
        "Counterexamples": {
            "reasoning_intensity": "very_high",
            "model_tier": "strong",
            "output_bias": "examples",
            "budget": "extended",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        },
        "Evidence Auditor": {
            "reasoning_intensity": "high",
            "model_tier": "medium",
            "output_bias": "computation",
            "budget": "normal",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        },
        "Review": {
            "reasoning_intensity": "very_high",
            "model_tier": "strong",
            "output_bias": "critique",
            "budget": "extended",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        },
        "Grade": {
            "reasoning_intensity": "high",
            "model_tier": "strong",
            "output_bias": "critique",
            "budget": "normal",
            "verbosity": "normal",
            "exploration_mode": "normal_pass",
        },
        "Strategy Synthesizer": {
            "reasoning_intensity": "high",
            "model_tier": "strong",
            "output_bias": "synthesis",
            "budget": "normal",
            "verbosity": "normal",
            "exploration_mode": "normal_pass",
        },
        "Memory": {
            "reasoning_intensity": "medium",
            "model_tier": "cheap",
            "output_bias": "retrieval",
            "budget": "bounded",
            "verbosity": "compact",
            "exploration_mode": "normal_pass",
        },
    }
    if domain_key in {"coherent_base_change", "stacks_descent", "generic_ag"}:
        settings["Locality"] = {
            "reasoning_intensity": "high",
            "model_tier": "medium",
            "output_bias": "critique",
            "budget": "normal",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        }
    if domain_key == "stacks_descent":
        settings["Representability"] = {
            "reasoning_intensity": "very_high",
            "model_tier": "strong",
            "output_bias": "critique",
            "budget": "normal",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        }
        settings["Descent"] = {
            "reasoning_intensity": "very_high",
            "model_tier": "strong",
            "output_bias": "critique",
            "budget": "normal",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        }
    if domain_key == "hodge_atoms":
        settings["Concepts"] = {
            "reasoning_intensity": "high",
            "model_tier": "medium",
            "output_bias": "critique",
            "budget": "normal",
            "verbosity": "detailed",
            "exploration_mode": "normal_pass",
        }
    return settings
