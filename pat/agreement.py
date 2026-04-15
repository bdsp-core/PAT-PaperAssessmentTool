"""
Inter-agent agreement analysis for PAT.

The agent panel is treated as a set of peer reviewers and their severity
verdicts are fed through classic inter-rater reliability statistics.  The
output surfaces:

* **Consensus issues** - sections flagged by three or more agents.
* **Singleton concerns** - sections flagged by exactly one agent.
* **Per-section quality scores** - a weighted aggregate score per section.
* **Fleiss' kappa** - kappa coefficient over binary (flagged / not-flagged) ratings.
* **Overall agreement percentage** - pairwise agreement across agent pairs.

This module does not call the LLM.  It is pure Python and runs in milliseconds
after every review.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Thresholds and mappings
# ---------------------------------------------------------------------------

_CONSENSUS_MIN_AGENTS = 3

# Fallback severity -> score mapping for agents that don't return an explicit
# score.  Mirrors the mapping in :mod:`agents.constants`.
_SEVERITY_TO_SCORE: dict[str, float] = {
    "ok": 0.95,
    "minor": 0.75,
    "moderate": 0.45,
    "major": 0.15,
}

_SEVERITY_RANK: dict[str, int] = {
    "ok": 0, "minor": 1, "moderate": 2, "major": 3,
}

# Score assigned when an agent did not flag a section (implicit OK).
_UNFLAGGED_SCORE = 0.9

# Default sections when the manuscript wasn't segmented upstream.
_DEFAULT_SECTIONS: tuple[str, ...] = (
    "abstract", "introduction", "methods", "results",
    "discussion", "conclusion",
)

# Kappa interpretation bands (Landis & Koch, 1977).
_KAPPA_BANDS: tuple[tuple[float, str], ...] = (
    (0.00, "Poor (below chance)"),
    (0.20, "Slight agreement"),
    (0.40, "Fair agreement"),
    (0.60, "Moderate agreement"),
    (0.80, "Substantial agreement"),
    (float("inf"), "Almost perfect agreement"),
)

# Section-score interpretation bands.
_SECTION_SCORE_BANDS: tuple[tuple[float, str], ...] = (
    (0.8, "Strong"),
    (0.6, "Needs work"),
    (0.4, "Significant issues"),
    (0.0, "Major revision needed"),
)


def compute_agreement(results: list, sections: dict) -> dict:
    """Compute inter-agent agreement across manuscript sections.

    Returns a dict with keys:

    * ``section_matrix`` - ``{section: {agent_id: severity_or_None}}``
    * ``consensus_issues`` - list of ``(section, count, agents)`` sorted by count.
    * ``singleton_concerns`` - list of ``(section, agent_id)``.
    * ``section_scores`` - ``{section: avg_score}``.
    * ``fleiss_kappa`` - float, rounded to 3 decimals.
    * ``agent_agreement_pct`` - rounded pairwise-agreement percentage.
    """
    section_names = [k for k in sections if k != "full"]
    if not section_names:
        section_names = list(_DEFAULT_SECTIONS)

    section_matrix: dict[str, dict[str, str | None]] = {
        s: {} for s in section_names
    }

    # Build the section x agent severity matrix.
    for r in results:
        if r.agent_id == "metrics":
            continue

        flagged_sections = list(getattr(r, "section_refs", []) or [])
        if not flagged_sections:
            # Fall back to naive section-name mentions in the findings body.
            findings_lower = (r.findings or "").lower()
            flagged_sections = [s for s in section_names if s in findings_lower]

        for s in section_names:
            if s in flagged_sections and _SEVERITY_RANK.get(r.severity, 0) > 0:
                section_matrix[s][r.agent_id] = r.severity
            else:
                section_matrix[s][r.agent_id] = None

    # Identify consensus issues and singletons.
    consensus_issues: list[tuple[str, int, list[str]]] = []
    singleton_concerns: list[tuple[str, str]] = []
    for section in section_names:
        flagging = [
            aid for aid, sev in section_matrix[section].items() if sev is not None
        ]
        if len(flagging) >= _CONSENSUS_MIN_AGENTS:
            consensus_issues.append((section, len(flagging), flagging))
        elif len(flagging) == 1:
            singleton_concerns.append((section, flagging[0]))
    consensus_issues.sort(key=lambda x: -x[1])

    # Per-section average quality score.
    section_scores: dict[str, float] = {}
    for section in section_names:
        scores: list[float] = []
        for r in results:
            if r.agent_id == "metrics":
                continue
            score = getattr(r, "score", -1)
            if score < 0:
                score = _SEVERITY_TO_SCORE.get(r.severity, 0.5)
            if section_matrix[section].get(r.agent_id):
                scores.append(score)
            else:
                scores.append(_UNFLAGGED_SCORE)
        section_scores[section] = (
            sum(scores) / len(scores) if scores else 0.8
        )

    kappa = _fleiss_kappa(section_matrix, section_names)

    # Pairwise agreement across all section x pair(agent) combinations.
    total_pairs = 0.0
    agree_pairs = 0.0
    for section in section_names:
        flags = list(section_matrix[section].values())
        n = len(flags)
        if n < 2:
            continue
        flagged = sum(1 for f in flags if f is not None)
        not_flagged = n - flagged
        pairs = n * (n - 1) / 2
        agree = (
            flagged * (flagged - 1) / 2
            + not_flagged * (not_flagged - 1) / 2
        )
        total_pairs += pairs
        agree_pairs += agree
    agreement_pct = (agree_pairs / total_pairs * 100) if total_pairs > 0 else 0

    return {
        "section_matrix": section_matrix,
        "consensus_issues": consensus_issues,
        "singleton_concerns": singleton_concerns,
        "section_scores": section_scores,
        "fleiss_kappa": kappa,
        "agent_agreement_pct": round(agreement_pct, 1),
    }


def _fleiss_kappa(matrix: dict, sections: list[str]) -> float:
    """Fleiss' kappa over binary ``flagged vs. not-flagged`` ratings.

    Treats each section as a subject and each agent as a rater.  Returns 0
    when there are fewer than two raters or when expected agreement is
    already perfect.
    """
    n_subjects = len(sections)
    if n_subjects == 0:
        return 0.0

    all_agents: set = set()
    for section in sections:
        all_agents.update(matrix[section].keys())
    n_raters = len(all_agents)
    if n_raters < 2:
        return 0.0

    p_i_sum = 0.0
    total_ratings = 0
    total_flagged = 0

    for section in sections:
        n_flagged = sum(
            1 for sev in matrix[section].values() if sev is not None
        )
        n_not = n_raters - n_flagged
        total_ratings += n_raters
        total_flagged += n_flagged

        # P_i = proportion of rater pairs in agreement on this subject.
        p_i = n_flagged * (n_flagged - 1) + n_not * (n_not - 1)
        p_i = p_i / (n_raters * (n_raters - 1)) if n_raters > 1 else 0
        p_i_sum += p_i

    p_bar = p_i_sum / n_subjects
    p_flagged = total_flagged / total_ratings if total_ratings > 0 else 0
    p_e = p_flagged ** 2 + (1 - p_flagged) ** 2

    if p_e >= 1.0:
        return 1.0

    denom = 1 - p_e
    kappa = (p_bar - p_e) / denom if denom != 0 else 0.0
    return round(kappa, 3)


def format_agreement_text(agreement: dict) -> str:
    """Render the agreement dict as markdown for the text report."""
    lines: list[str] = [
        "## Inter-Agent Agreement Analysis",
        "",
        f"**Fleiss' Kappa:** {agreement['fleiss_kappa']:.3f}",
        f"**Overall Agreement:** {agreement['agent_agreement_pct']}%",
        "",
    ]

    k = agreement["fleiss_kappa"]
    interp = next(label for upper, label in _KAPPA_BANDS if k < upper)
    lines.append(f"**Interpretation:** {interp}")
    lines.append("")

    if agreement["consensus_issues"]:
        lines.append("### Consensus Issues (flagged by 3+ agents)")
        lines.append("")
        for section, count, agents in agreement["consensus_issues"]:
            agent_names = ", ".join(agents[:5])
            lines.append(
                f"- **{section.title()}**: flagged by {count} agents "
                f"({agent_names})"
            )
        lines.append("")

    if agreement["singleton_concerns"]:
        lines.append("### Singleton Concerns (flagged by only 1 agent)")
        lines.append("")
        for section, agent_id in agreement["singleton_concerns"]:
            lines.append(f"- **{section.title()}**: only {agent_id}")
        lines.append("")

    lines.append("### Per-Section Quality Scores")
    lines.append("")
    lines.append("| Section | Score | Assessment |")
    lines.append("|---------|-------|------------|")
    for section, score in sorted(agreement["section_scores"].items()):
        assessment = next(
            label for threshold, label in _SECTION_SCORE_BANDS
            if score >= threshold
        )
        lines.append(f"| {section.title()} | {score:.2f} | {assessment} |")
    lines.append("")

    return "\n".join(lines)
