"""
PAT (Paper Assessment Tool) - agent package.

Re-exports the public API so callers continue to write::

    from pat.agents import ALL_AGENTS, Context, AgentResult, BaseAgent, create_ref_backend

regardless of the internal module layout.  The pipeline is organised as four
phases:

* Phase 0 - instant programmatic metrics (no LLM).
* Phase 1 - independent per-document / per-figure agents.
* Phase 2 - whole-document synthesis and reference lookup.
* Phase 3 - final orchestrator, checklist, and response drafting.

See :mod:`agents.registry` for the canonical agent ordering.
"""

from __future__ import annotations

from .base import AgentResult, BaseAgent, Context
from .reference_backends import (
    BioRxivBackend,
    CombinedBackend,
    NullBackend,
    PubMedBackend,
    ReferenceSearchBackend,
    create_ref_backend,
)
from .registry import AGENT_REGISTRY, ALL_AGENTS


__all__ = [
    "ALL_AGENTS",
    "AGENT_REGISTRY",
    "AgentResult",
    "BaseAgent",
    "Context",
    "ReferenceSearchBackend",
    "PubMedBackend",
    "BioRxivBackend",
    "CombinedBackend",
    "NullBackend",
    "create_ref_backend",
]
