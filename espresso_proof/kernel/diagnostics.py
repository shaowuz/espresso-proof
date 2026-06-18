from __future__ import annotations

import os


def current_capabilities() -> list[dict[str, str]]:
    """Return runtime capability statuses for CLI/UI display."""
    return [
        {"label": "local/mock kernel", "status": "exists"},
        {"label": "schema-checked artifacts", "status": "exists"},
        {"label": "example feedback CLI", "status": "exists"},
        {"label": "feedback summary CLI", "status": "exists"},
        {"label": "strategy score grading", "status": "exists"},
        {"label": "strategy-choice write API", "status": "exists"},
        {"label": "end-to-end multiagent flow", "status": "exists"},
        {"label": "real-agent doctor", "status": "exists"},
        {
            "label": "OpenAI provider key",
            "status": "ready" if os.environ.get("OPENAI_API_KEY") else "missing OPENAI_API_KEY",
        },
        {
            "label": "External JSON agent command",
            "status": (
                "ready"
                if os.environ.get("ESPRESSO_EXTERNAL_AGENT_COMMAND")
                else "missing ESPRESSO_EXTERNAL_AGENT_COMMAND"
            ),
        },
        {"label": "live arXiv/Crossref Literature Discovery", "status": "exists"},
        {"label": "ADS Literature Discovery", "status": "exists with ADS_API_TOKEN"},
        {"label": "PDF/text theorem ingestion", "status": "conservative"},
        {"label": "reference-support verification", "status": "conservative"},
        {"label": "LLM prover/verifier CLI", "status": "initial"},
        {"label": "per-round prover/reviewer audit trail", "status": "exists"},
        {"label": "real Evidence Auditor adapters", "status": "not implemented"},
        {"label": "Mathlib integration", "status": "not implemented"},
        {"label": "long-running workers", "status": "not implemented"},
        {"label": "proof automation", "status": "not implemented"},
    ]
