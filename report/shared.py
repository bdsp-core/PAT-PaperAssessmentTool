"""
Shared helpers used across the PAT report package.

This module intentionally carries no third-party imports - every other report
sub-module can depend on it safely.
"""

from __future__ import annotations


SEVERITY_ICON: dict[str, str] = {
    "ok": "OK",
    "minor": "MINOR",
    "moderate": "MODERATE",
    "major": "MAJOR",
}

SEVERITY_BADGE: dict[str, str] = {
    "ok": '<span class="badge badge-ok">OK</span>',
    "minor": '<span class="badge badge-minor">Minor</span>',
    "moderate": '<span class="badge badge-moderate">Moderate</span>',
    "major": '<span class="badge badge-major">Major</span>',
}

# Figure-agent name prefixes - used to filter figure agents out of the
# manuscript-only composite score shown on the revision dashboard.
FIGURE_AGENT_NAME_PREFIXES: tuple[str, ...] = ("Figure", "Cross-Figure")


def esc(s: str) -> str:
    """Escape the three HTML special characters for safe embedding."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def weighted_composite(results: list, config: dict) -> float:
    """Weighted composite score over agents that returned a 0-1 ``score``.

    ``metrics`` is excluded because it is an informational dimension rather
    than a quality rating.  ``dimension_weights`` from the journal config
    lets reviewers emphasize particular dimensions (e.g. statistics, guidelines).
    """
    scored = [
        r for r in results
        if getattr(r, "score", -1) >= 0 and r.agent_id != "metrics"
    ]
    if not scored:
        return 0.0
    weights = config.get("dimension_weights", {})
    total_weight = 0.0
    total_score = 0.0
    for r in scored:
        w = weights.get(r.agent_id, 1.0)
        total_weight += w
        total_score += r.score * w
    return total_score / total_weight if total_weight > 0 else 0.0
