"""
Markdown report writer.

Emits a plain-markdown review document to ``output_dir`` with the summary
table, optional agreement section, every agent's full findings, consolidated
new-reference suggestions, and optional figure-edit-loop transcript.  Returns
the output path so the caller can display or log it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .shared import SEVERITY_ICON, weighted_composite


def write_markdown_report(
    results: list,
    paper_path: str,
    model_name: str,
    provider_name: str,
    output_dir: Path,
    agreement: dict | None = None,
    config: dict | None = None,
    edit_results=None,
) -> Path:
    """Write the review report as markdown and return the output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(paper_path).stem if paper_path else "paper"
    out_path = output_dir / f"review_{stem}_{ts}.md"

    composite = weighted_composite(results, config or {})

    lines = [
        "# Paper Review Report",
        "",
        f"**Paper:** {paper_path}",
        f"**Model:** {provider_name} / {model_name}",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Submission Readiness Score:** {composite:.0%}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Agent | Severity | Score | Summary |",
        "|-------|----------|-------|---------|",
    ]
    for r in results:
        icon = SEVERITY_ICON.get(r.severity, "?")
        score_str = f"{r.score:.0%}" if getattr(r, "score", -1) >= 0 else "-"
        lines.append(f"| {r.agent_name} | {icon} | {score_str} | {r.summary[:80]} |")
    lines += ["", "---", ""]

    if agreement:
        from agreement import format_agreement_text
        lines.append(format_agreement_text(agreement))
        lines.append("---")
        lines.append("")

    lines.append("## Detailed Findings")
    for r in results:
        score_str = f" | **Score:** {r.score:.0%}" if getattr(r, "score", -1) >= 0 else ""
        lines += [
            "",
            f"### {r.agent_name}",
            f"**Severity:** {SEVERITY_ICON.get(r.severity, '?')}  |  "
            f"**Elapsed:** {r.elapsed:.1f}s{score_str}",
            "",
            r.findings,
            "",
            "---",
        ]

    # Consolidated new reference suggestions (deduplicated by title).
    all_refs = [item for r in results for item in r.references_found]
    if all_refs:
        lines += ["", "## Suggested New References", ""]
        seen: set[str] = set()
        for item in all_refs:
            ref = item.get("ref", {})
            title = ref.get("title", "")
            if title and title not in seen:
                seen.add(title)
                lines.append(
                    f"- **{title}** - {ref.get('authors', '')} "
                    f"({ref.get('year', '')}) *{ref.get('venue', '')}*  "
                    f"  Para {item.get('para', '?')}: "
                    f"\"{item.get('claim', '')}\""
                )
        lines.append("")

    if edit_results and edit_results.iterations:
        lines += [
            "## Figure Improvement Loop",
            "",
            f"**Iterations:** {edit_results.total_iterations}  |  "
            f"**Elapsed:** {edit_results.total_elapsed:.1f}s",
            "",
        ]
        for iter_results in edit_results.iterations:
            iter_num = iter_results[0].iteration + 1 if iter_results else 0
            lines.append(f"### Iteration {iter_num}")
            lines.append("")
            for r in iter_results:
                status = "Success" if r.success else "Failed"
                lines += [f"#### {r.script_name} - {status}", ""]
                if r.error and not r.success:
                    lines += [f"**Error:** `{r.error[:200]}`", ""]
                if r.diff:
                    lines += ["```diff", r.diff, "```", ""]
            lines += ["---", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
