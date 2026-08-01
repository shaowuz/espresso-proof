"""Shared runtime rules for the external-agent adapters (stdlib-only).

This module is the single home of two mechanisms both shipped adapters need:

1. **Inner-timeout derivation.** The orchestration-side
   ``ExternalCommandClient`` enforces an OUTER timeout on the whole adapter
   process (``ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS``, family default
   otherwise) and reclaims the process group on expiry. Each adapter also
   passes an INNER timeout to the underlying CLI (``claude -p`` /
   ``codex exec``). Historically the inner values were hardcoded (110 / 300),
   so raising the env var never reached the actual model call — the inner
   timeout always fired first and the env var was silently dead.
   :func:`resolve_inner_timeout` derives the inner value from the same outer
   contract, minus a small margin so the adapter keeps enough time to parse
   and reply before the outer killpg arrives.

2. **Fallback schema validity.** On any failure the adapters emit a
   conservative prover-shaped fallback object so a weak reply can never crash
   a run. That is safe exactly when the requesting role's
   ``required_json_schema`` is satisfied by the fallback's keys (front-half
   and prover/reviewer roles). For authoritative-schema back-half roles
   (line-numbered proof writer, proof-consensus reviewer, gap resolver) the
   prover-shaped fallback is schema-INVALID and downstream normalization
   would quietly turn it into garbage (e.g. ``lines=[]``). Those roles must
   fail closed instead: :func:`fallback_is_schema_valid` decides purely from
   the request's required keys — no role names, no special cases.

Kept stdlib-only (no espresso imports) so both adapters can import it at top
level without dragging in the package or creating cycles.

NOTE: ``espresso_proof.agents.llm_agents`` still defines its own copies of
the timeout constants; unifying them onto this module is deferred until the
in-flight ``llm_agents`` package split lands (see the pipeline fix plan,
slice PR1b).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

EXTERNAL_AGENT_TIMEOUT_ENV = "ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS"
# A timeout exists to catch a HANG, not to clip a slow call. Measured across
# 140 successful calls on the cheapest model: median 54s, p90 91s, max 110s —
# and the max was the cap itself, with 13 calls killed at exactly 110s while 12
# more finished within 15s of it. The old 120s default (110s inner) therefore
# sat INSIDE the natural duration distribution and turned the slowest legitimate
# calls into fallbacks: the observed "CLI timeouts" were not hung processes.
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 420
DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS = 360
# The inner CLI call gets the outer budget minus this margin, so the adapter
# can still parse output and print its reply before the outer killpg fires.
INNER_TIMEOUT_MARGIN_SECONDS = 10
MIN_INNER_TIMEOUT_SECONDS = 5


# Roles whose prompts ask for the longest structured output get the longest
# budget, mirroring the per-role output-token budgets. An unrecognized role
# keeps the generous default rather than a tight one (fail open on time, closed
# on evidence).
_ROLE_TIMEOUT_SECONDS: tuple[tuple[str, int], ...] = (
    ("line-numbered proof writer", 900),
    ("proof gap resolver", 720),
    ("prover", 600),
    ("reviewer", 600),
)


def timeout_for_role(system: str, *, default: int = DEFAULT_EXTERNAL_TIMEOUT_SECONDS) -> int:
    """The outer budget this role legitimately needs."""

    text = (system or "").lower()
    for marker, seconds in _ROLE_TIMEOUT_SECONDS:
        if marker in text:
            return seconds
    return default


def resolve_inner_timeout(
    *,
    request: dict | None,
    default_outer: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Derive the inner CLI timeout from the outer adapter contract.

    Outer-budget precedence: ``request["timeout_seconds"]`` (written by
    ``ExternalCommandClient``; optional, additive) > the timeout env var >
    ``default_outer``. Malformed values are ignored on the adapter side (the
    client side already fails closed on them at construction time).
    """

    env = os.environ if environ is None else environ
    outer = default_outer

    raw_env = (env.get(EXTERNAL_AGENT_TIMEOUT_ENV) or "").strip()
    if raw_env:
        try:
            candidate = int(raw_env)
        except ValueError:
            candidate = 0
        if candidate > 0:
            outer = candidate

    raw_request = (request or {}).get("timeout_seconds")
    if isinstance(raw_request, int) and not isinstance(raw_request, bool) and raw_request > 0:
        outer = raw_request

    return max(outer - INNER_TIMEOUT_MARGIN_SECONDS, MIN_INNER_TIMEOUT_SECONDS)


def fallback_is_schema_valid(request: dict | None, fallback: dict) -> bool:
    """May this role's failure be answered with the conservative fallback?

    True iff every top-level key the request's ``required_json_schema``
    requires is present in the fallback object. Requests without a schema
    (old third-party callers, malformed stdin) are vacuously satisfiable —
    the historical exit-0 fallback contract for them is unchanged.
    """

    schema = (request or {}).get("required_json_schema") or {}
    required = schema.get("required") or []
    return all(key in fallback for key in required)
