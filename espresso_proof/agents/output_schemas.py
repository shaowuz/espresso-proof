"""JSON output schemas for Espresso's LLM agent runtime.

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every name defined here.
"""

from __future__ import annotations


AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "source_requests": {"type": "array", "items": {"type": "string"}},
        "missing_hypotheses": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
        "recommended_human_action": {"type": "string"},
        # Optional, reviewer roles only. The system prompt has asked reviewers
        # for ranked scoped objections since the severity rubric landed, but this
        # schema said additionalProperties=false without listing the key — the
        # machine-readable contract forbade what the prose requested, models
        # correctly obeyed the schema, and every objection fell back to the
        # kernel default P2 with no suggested action. Across five real rounds:
        # 51 objections, 51 fallbacks, zero explicit.
        "scoped_objections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "scope": {"enum": ["subgoal", "proof_step", "route", "global"]},
                    "subgoal_id": {"type": ["string", "null"]},
                    "proof_step_id": {"type": ["string", "null"]},
                    "kind": {
                        "enum": [
                            "missing_source", "hypothesis_gap", "definition_gap",
                            "logical_gap", "computation_needed",
                            "counterexample_pressure", "unclear_claim",
                            "reviewer_disagreement", "other",
                        ]
                    },
                    "severity": {"enum": ["P0", "P1", "P2", "P3"]},
                    "suggested_next_action": {"type": "string"},
                },
                "required": ["text", "scope", "severity"],
            },
        },
    },
    "required": [
        "summary",
        "steps",
        "source_requests",
        "missing_hypotheses",
        "risks",
        "verdict",
        "recommended_human_action",
    ],
}
LINE_NUMBERED_PROOF_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "proof_kind": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "line_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "justification": {"type": "string"},
                    "dependencies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {"type": "string"},
                                "ref": {"type": "string"},
                                "status": {"type": "string"},
                                "role": {"type": "string"},
                                "needed_statement": {"type": "string"},
                                "source_locator": {"type": "string"},
                                "reviewer_verdict": {"type": "string"},
                                "authority": {
                                    "type": "string",
                                    "enum": [
                                        "",
                                        "source_grounded_theorem_ref",
                                        "human_allowed_theorem_ref",
                                        "agent_claimed_theorem_ref",
                                    ],
                                },
                                "source_grounded": {"type": "boolean"},
                                "machine_verified": {"type": "boolean"},
                            },
                            "required": [
                                "type",
                                "ref",
                                "status",
                                "role",
                                "needed_statement",
                                "source_locator",
                                "reviewer_verdict",
                                "authority",
                                "source_grounded",
                                "machine_verified",
                            ],
                        },
                    },
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "gaps": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "line_id",
                    "statement",
                    "justification",
                    "dependencies",
                    "depends_on",
                    "source_refs",
                    "gaps",
                ],
            },
        },
    },
    "required": ["proof_kind", "lines"],
}
PROOF_CONSENSUS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string"},
        "looked_for": {"type": "string"},
        "finding": {"type": "string"},
        "cited_line_ids": {"type": "array", "items": {"type": "string"}},
        "residual_objections": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "looked_for",
        "finding",
        "cited_line_ids",
        "residual_objections",
    ],
}
PROOF_GAP_RESOLVER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "line_id": {"type": "string"},
                    "dependency_ref": {"type": "string"},
                    "dependency_type": {"type": "string"},
                    "action": {"type": "string"},
                    "proposed_ref": {"type": "string"},
                    "needed_statement": {"type": "string"},
                    "proposed_statement": {"type": "string"},
                    "source_locator": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_kind": {"type": "string"},
                    "applicability_review": {"type": "string"},
                    "reviewer_notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "line_id",
                    "dependency_ref",
                    "dependency_type",
                    "action",
                    "proposed_ref",
                    "needed_statement",
                    "proposed_statement",
                    "source_locator",
                    "source_url",
                    "source_kind",
                    "applicability_review",
                    "reviewer_notes",
                ],
            },
        },
    },
    "required": ["candidates"],
}


def _required_json_schema_for_system(system: str) -> dict:
    lowered = system.lower()
    if "line-numbered proof writer" in lowered:
        return LINE_NUMBERED_PROOF_OUTPUT_SCHEMA
    if "proof-consensus review" in lowered:
        return PROOF_CONSENSUS_OUTPUT_SCHEMA
    if "proof gap resolver" in lowered:
        return PROOF_GAP_RESOLVER_OUTPUT_SCHEMA
    return AGENT_OUTPUT_SCHEMA
