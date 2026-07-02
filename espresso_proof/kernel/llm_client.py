"""The transport-neutral LLM client contract.

Every stage that talks to a model (front-half stages, literature agents, the
prover/reviewer pass, strategy generation) depends only on this Protocol, so
none of them needs the agents package for the type alone. Concrete clients
(stub / OpenAI / external-command) live in ``espresso_proof.agents``.
"""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    provider: str
    model: str

    def complete_json(self, *, system: str, prompt: str) -> dict:
        ...
