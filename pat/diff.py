"""
Revision-progress comparison for PAT.

Parses a previous markdown review report, compares it against the current
run's results, and produces a structured diff of what improved, regressed,
newly appeared, or got resolved.  Also extracts the composite submission
readiness score and the programmatic NLP metrics (word count, FK grade,
passive voice, average sentence length) so the HTML report can render a
full revision-impact panel.
"""

from __future__ import annotations

import re
from pathlib import Path


_SEVERITY_RANK: dict[str, int] = {
    "ok": 0, "minor": 1, "moderate": 2, "major": 3,
}


def parse_report(report_path: str) -> dict[str, dict]:
    """Parse a markdown review report into agent name -> {severity, summary, score}.

    Returns dict like:
        {"VSNC Framework": {"severity": "major", "summary": "...", "score": 0.45}, ...}
    """
    text = Path(report_path).read_text(encoding="utf-8")
    agents = {}

    # Parse summary-table rows.  The current format has four columns:
    #   | Agent Name | SEVERITY | SCORE | Summary text |
    # Older PAT reports used an em-dash ("—") to denote an unscored agent;
    # newer reports use a plain hyphen ("-").  Both are accepted.
    for match in re.finditer(
        r"\|\s*(.+?)\s*\|\s*(OK|MINOR|MODERATE|MAJOR)\s*\|"
        r"\s*(\d+%|—|-)\s*\|\s*(.+?)\s*\|",
        text, re.IGNORECASE,
    ):
        name = match.group(1).strip()
        severity = match.group(2).strip().lower()
        score_str = match.group(3).strip()
        summary = match.group(4).strip()
        if name and name not in ("Agent", "---"):
            score = (
                int(score_str.rstrip("%")) / 100.0
                if score_str not in ("—", "-") else -1.0
            )
            agents[name] = {
                "severity": severity, "summary": summary, "score": score,
            }

    # Fallback: old format without score column
    if not agents:
        for match in re.finditer(
            r"\|\s*(.+?)\s*\|\s*(OK|MINOR|MODERATE|MAJOR)\s*\|\s*(.+?)\s*\|",
            text, re.IGNORECASE,
        ):
            name = match.group(1).strip()
            severity = match.group(2).strip().lower()
            summary = match.group(3).strip()
            if name and name != "Agent":
                agents[name] = {"severity": severity, "summary": summary, "score": -1.0}

    # Parse composite score if present
    composite_match = re.search(r"\*\*Submission Readiness Score:\*\*\s*(\d+)%", text)
    if composite_match:
        agents["_composite"] = {"score": int(composite_match.group(1)) / 100.0}

    return agents


def parse_nlp_metrics_from_summary(summary: str) -> dict:
    """Parse Text Metrics summary string into individual metric values.

    Expected format: '7,166 words | FK grade 15.9 | 17.0% passive | avg 23.4 words/sent'
    """
    metrics: dict[str, float | None] = {}
    m = re.search(r'([\d,]+)\s*words', summary)
    metrics['word_count'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'FK grade\s*([\d.]+)', summary)
    metrics['fk_grade'] = float(m.group(1)) if m else None
    m = re.search(r'([\d.]+)%\s*passive', summary)
    metrics['passive_pct'] = float(m.group(1)) if m else None
    m = re.search(r'avg\s*([\d.]+)\s*words/sent', summary)
    metrics['avg_sent_len'] = float(m.group(1)) if m else None
    return metrics


def compare_reviews(old_path: str, new_results: list) -> dict:
    """Compare a previous report with current results.

    Returns:
        {
            "improved": [(name, old_sev, new_sev), ...],
            "persists": [(name, severity), ...],
            "regressed": [(name, old_sev, new_sev), ...],
            "new_issues": [(name, severity), ...],
            "resolved": [(name, old_sev), ...],
            "old_score": int,
            "new_score": int,
            "score_deltas": [(name, old_score, new_score, delta), ...],
            "old_composite": float,
            "new_composite": float,
        }
    """
    old_agents = parse_report(old_path)
    new_agents = {r.agent_name: {
        "severity": r.severity,
        "summary": r.summary,
        "score": getattr(r, "score", -1.0),
    } for r in new_results}

    sev_rank = _SEVERITY_RANK

    improved = []
    persists = []
    regressed = []
    new_issues = []
    resolved = []
    score_deltas = []

    for name in old_agents:
        if name.startswith("_"):
            continue
        old_sev = old_agents[name]["severity"]
        if name in new_agents:
            new_sev = new_agents[name]["severity"]
            old_r = sev_rank.get(old_sev, 0)
            new_r = sev_rank.get(new_sev, 0)
            if new_r < old_r:
                improved.append((name, old_sev, new_sev))
            elif new_r > old_r:
                regressed.append((name, old_sev, new_sev))
            elif old_r > 0:
                persists.append((name, old_sev))

            # Score delta
            old_sc = old_agents[name].get("score", -1)
            new_sc = new_agents[name].get("score", -1)
            if old_sc >= 0 and new_sc >= 0:
                delta = new_sc - old_sc
                score_deltas.append((name, old_sc, new_sc, delta))
        else:
            if sev_rank.get(old_sev, 0) > 0:
                resolved.append((name, old_sev))

    for name in new_agents:
        if name not in old_agents:
            new_sev = new_agents[name]["severity"]
            if sev_rank.get(new_sev, 0) > 0:
                new_issues.append((name, new_sev))

    old_score = sum(sev_rank.get(a.get("severity", "ok"), 0)
                    for k, a in old_agents.items() if not k.startswith("_"))
    new_score = sum(sev_rank.get(a.get("severity", "ok"), 0)
                    for k, a in new_agents.items())

    # Composite scores
    old_composite = old_agents.get("_composite", {}).get("score", -1)
    new_scored = [r for r in new_results if getattr(r, "score", -1) >= 0
                  and r.agent_id != "metrics"]
    new_composite = sum(r.score for r in new_scored) / len(new_scored) if new_scored else -1

    # NLP metrics comparison
    nlp_delta = {}
    old_metrics_agent = old_agents.get("Text Metrics", {})
    old_nlp = parse_nlp_metrics_from_summary(old_metrics_agent.get("summary", ""))
    new_metrics_agent = next(
        (r for r in new_results if r.agent_id == "metrics"), None)
    new_nlp = parse_nlp_metrics_from_summary(
        new_metrics_agent.summary if new_metrics_agent else "")
    for key in old_nlp:
        if old_nlp[key] is not None and new_nlp.get(key) is not None:
            nlp_delta[key] = {
                "old": old_nlp[key], "new": new_nlp[key],
                "delta": new_nlp[key] - old_nlp[key],
            }

    return {
        "improved": improved,
        "persists": persists,
        "regressed": regressed,
        "new_issues": new_issues,
        "resolved": resolved,
        "old_score": old_score,
        "new_score": new_score,
        "score_deltas": score_deltas,
        "old_composite": old_composite,
        "new_composite": new_composite,
        "nlp_metrics_delta": nlp_delta,
    }


def format_revision_progress(comparison: dict) -> str:
    """Format comparison as markdown for the report."""
    lines = ["## Revision Progress\n"]

    # Composite score comparison
    old_c = comparison.get("old_composite", -1)
    new_c = comparison.get("new_composite", -1)
    if old_c >= 0 and new_c >= 0:
        delta_pct = (new_c - old_c) * 100
        direction = "improved" if delta_pct > 0 else "regressed" if delta_pct < 0 else "unchanged"
        lines.append(f"**Submission Readiness:** {old_c:.0%} -> {new_c:.0%} "
                     f"({direction}, {delta_pct:+.1f}pp)\n")

    delta = comparison["old_score"] - comparison["new_score"]
    if delta > 0:
        lines.append(f"Overall improvement: severity score decreased by {delta} points.\n")
    elif delta < 0:
        lines.append(f"Warning: severity score increased by {-delta} points.\n")
    else:
        lines.append("No change in overall severity score.\n")

    # Score deltas table
    score_deltas = comparison.get("score_deltas", [])
    if score_deltas:
        lines.append("### Score Changes by Agent\n")
        lines.append("| Agent | Before | After | Change |")
        lines.append("|-------|--------|-------|--------|")
        for name, old_sc, new_sc, delta in sorted(score_deltas, key=lambda x: -abs(x[3])):
            arrow = "+" if delta > 0 else ""
            lines.append(f"| {name} | {old_sc:.0%} | {new_sc:.0%} | {arrow}{delta:.0%} |")
        lines.append("")

    if comparison["improved"]:
        lines.append("### Improved")
        for name, old, new in comparison["improved"]:
            lines.append(f"- **{name}**: {old} -> {new}")
        lines.append("")

    if comparison["resolved"]:
        lines.append("### Resolved")
        for name, old in comparison["resolved"]:
            lines.append(f"- **{name}**: was {old}, now resolved")
        lines.append("")

    if comparison["persists"]:
        lines.append("### Still Needs Work")
        for name, sev in comparison["persists"]:
            lines.append(f"- **{name}**: still {sev}")
        lines.append("")

    if comparison["regressed"]:
        lines.append("### Regressed")
        for name, old, new in comparison["regressed"]:
            lines.append(f"- **{name}**: {old} -> {new}")
        lines.append("")

    if comparison["new_issues"]:
        lines.append("### New Issues")
        for name, sev in comparison["new_issues"]:
            lines.append(f"- **{name}**: {sev}")
        lines.append("")

    return "\n".join(lines)
