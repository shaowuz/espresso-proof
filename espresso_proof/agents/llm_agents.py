"""Compatibility facade for the split LLM-agent runtime modules.

This module used to hold the whole LLM-agent runtime. The implementation now
lives in focused submodules; this facade only re-exports the public surface so
existing importers keep working unchanged:

- ``espresso_proof.agents.output_schemas`` — the agent JSON output schemas and
  ``_required_json_schema_for_system``.
- ``espresso_proof.agents.clients`` — LLM transport (``StubLLMClient``,
  ``OpenAIResponsesClient``, ``ExternalCommandClient`` + timeout/process-group
  machinery), ``build_client``, and the adapter diagnostics.
- ``espresso_proof.agents.prompts`` — system prompt constants plus
  ``agent_prompt`` / ``load_agent_context`` prompt assembly.
- ``espresso_proof.agents.sanitize`` — agent-output sanitization
  (``safe_prover_text`` / ``safe_prover_list`` / ``collect_unsafe_language`` /
  ``normalize_verdict`` / ``normalize_action`` / ``extract_response_text`` /
  ``strip_code_fence``); the redaction primitives themselves live in
  ``espresso_proof.kernel.proof_language``.
- ``espresso_proof.agents.agent_pass`` — the initial Prover -> Reviewers pass
  (``run_llm_agent_pass`` and friends) plus reviewer-blocker routing to the
  update inbox.
- ``espresso_proof.agents.prover_loop`` — the prover-loop round machinery
  (``build_prover_loop_report``, ``append_prover_loop_round``,
  ``resolve_agent_gate``, round artifacts/index).

``LLMClient`` and the proof-language redaction names keep their canonical
homes in ``espresso_proof.kernel`` and are re-exported here as before.
"""

from __future__ import annotations

import sys as _sys

# Tests patch ``espresso_proof.agents.llm_agents.urllib.request.urlopen``; keep
# ``urllib`` importable as an attribute of this module.
import urllib.request  # noqa: F401
from types import ModuleType as _ModuleType

from espresso_proof.agents import (
    agent_pass as _agent_pass_module,
    clients as _clients_module,
    output_schemas as _output_schemas_module,
    prompts as _prompts_module,
    prover_loop as _prover_loop_module,
    sanitize as _sanitize_module,
)
from espresso_proof.agents.agent_pass import (  # noqa: F401 - re-exported
    INPUT_ARTIFACTS,
    MISSING_CONTEXT_NOTES,
    _normalize_scoped_objections,
    ask_prover_for_candidate_route,
    ask_reviewers_to_attack_candidate_route,
    assemble_llm_agent_run_report,
    load_context_artifacts_for_initial_agent_pass,
    make_human_gate,
    make_prover_attempt,
    make_verifier_review,
    route_initial_reviewer_blockers_to_update_inbox,
    run_llm_agent_pass,
)
from espresso_proof.agents.clients import (  # noqa: F401 - re-exported
    ADAPTER_META_KEY,
    CODEX_ADAPTER_DIAGNOSTIC_KEY,
    CODEX_EXTERNAL_MODEL,
    DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS,
    DEFAULT_EXTERNAL_AGENT_RETRIES,
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    EXTERNAL_AGENT_RETRIES_ENV,
    EXTERNAL_AGENT_TIMEOUT_ENV,
    ExternalAgentTimeoutError,
    ExternalCommandClient,
    OpenAIResponsesClient,
    StubLLMClient,
    build_client,
    external_agent_diagnostic,
    recorded_model_for_events,
    openai_agent_diagnostic,
)
from espresso_proof.agents.output_schemas import (  # noqa: F401 - re-exported
    AGENT_OUTPUT_SCHEMA,
    LINE_NUMBERED_PROOF_OUTPUT_SCHEMA,
    PROOF_CONSENSUS_OUTPUT_SCHEMA,
    PROOF_GAP_RESOLVER_OUTPUT_SCHEMA,
    _required_json_schema_for_system,
)
from espresso_proof.agents.prompts import (  # noqa: F401 - re-exported
    ESPRESSO_REVIEWER_ROLES,
    agent_prompt,
    line_numbered_proof_writer_system_prompt,
    load_agent_context,
    proof_consensus_system_prompt,
    proof_gap_resolver_system_prompt,
    prover_system_prompt,
    verifier_system_prompt,
)
from espresso_proof.agents.prover_loop import (  # noqa: F401 - re-exported
    _human_feedback_advisory,
    _pointer_scope_from_review,
    _review_pointer,
    append_prover_loop_round,
    build_next_prover_input_from_reviewer_feedback,
    build_prover_loop_report,
    refresh_prover_round_index,
    resolve_agent_gate,
    write_initial_prover_round_artifacts,
)
from espresso_proof.agents.sanitize import (  # noqa: F401 - re-exported
    collect_unsafe_language,
    extract_response_text,
    normalize_action,
    normalize_verdict,
    safe_prover_list,
    safe_prover_text,
    strip_code_fence,
)
from espresso_proof.kernel.llm_client import LLMClient  # noqa: F401 - canonical home; re-exported for existing importers
from espresso_proof.kernel.proof_language import (  # noqa: F401 - re-exported
    FORBIDDEN_LANGUAGE,
    PROOF_COMPLETE_PHRASES,
    RESERVED_STATUS_LANGUAGE,
    forbidden_pattern,
    safe_list,
    safe_text,
)
from espresso_proof.kernel.time_allocation import (  # noqa: F401 - re-exported
    TIME_ALLOCATION_FILENAME,
    TimeAllocationPlan,
)

_SPLIT_MODULES = (
    _output_schemas_module,
    _sanitize_module,
    _prompts_module,
    _clients_module,
    _agent_pass_module,
    _prover_loop_module,
)


class _LLMAgentsFacadeModule(_ModuleType):
    """Keep ``monkeypatch.setattr(llm_agents, name, ...)`` behavior-preserving.

    Before the split, rebinding an attribute on this module rebound the name
    that the implementation itself resolved at call time. The implementations
    now live in the split modules above, so a facade-level ``setattr`` is
    mirrored into every split module whose globals bind the same name (and the
    original is restored the same way when a monkeypatch is undone).
    """

    def __setattr__(self, name: str, value: object) -> None:
        for module in _SPLIT_MODULES:
            if name in module.__dict__:
                module.__dict__[name] = value
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _LLMAgentsFacadeModule
