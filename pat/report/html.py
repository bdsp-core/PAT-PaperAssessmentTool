"""
HTML report writer for PAT.

Generates a single self-contained HTML document with:

* A fixed nav bar (severity filter pills, section jump, text search).
* A composite score radar (or before / after overlay for revision runs).
* An optional NLP metrics comparison table.
* A quick-wins summary for minor severity issues.
* The summary table and full agent findings.
* An optional inter-agent agreement heatmap.
* An optional figure-edit-loop transcript with before / after images.

The writer delegates rich-findings parsing to :func:`render_findings` so the
heavy markdown-to-HTML post-processing can be reused (and unit-tested) in
isolation.
"""

from __future__ import annotations

import base64
import re
import webbrowser
from datetime import datetime
from pathlib import Path

import markdown

from ._assets import CSS, JS
from .charts import (
    build_agreement_heatmap,
    build_comparison_radar_svg,
    build_radar_svg,
)
from .shared import SEVERITY_BADGE, esc, weighted_composite


def render_findings(findings_text: str) -> str:
    """Convert agent findings markdown to styled HTML.

    The LLM's structured footer (SEVERITY / SCORE / SUMMARY / ...) is stripped
    first; then markdown is rendered; then recognised structured components
    (graded statements, review quotes, diff blocks, Reviewer #2 triptychs,
    action-plan severity tags) are upgraded to styled HTML.
    """
    if not findings_text or not findings_text.strip():
        return '<p class="findings-markdown"><em>No findings.</em></p>'

    text = findings_text

    # Strip structured-footer metadata from the end of the body.
    text = re.sub(
        r'\n*-{2,}\s*\nSEVERITY:.*?(?=\n-{2,}\s*\n|\Z)',
        '', text, flags=re.DOTALL,
    )
    text = re.sub(
        r'(?:^|\n)SEVERITY:\s*\w+\s*\n(?:SCORE:.*?\n)?(?:SUMMARY:.*?\n)?'
        r'(?:SECTION_REFS:.*?\n)?(?:TOP_ISSUES:.*)?$',
        '', text, flags=re.DOTALL,
    )
    text = re.sub(
        r'^One-line\s+SUMMARY:.*?\nSEVERITY:\s*\w+\s*\n?',
        '', text, flags=re.IGNORECASE,
    )
    text = re.sub(r'^SCORE:\s*[\d.]+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^SECTION_REFS:\s*.+$', '', text, flags=re.MULTILINE)
    text = text.strip()
    if not text:
        return '<p class="findings-markdown"><em>No findings.</em></p>'

    # markdown library doesn't recognise GitHub-style checkboxes, so translate
    # them to HTML entities before handing the text over.
    text = re.sub(r'^(\s*)- \[x\]', r'\1- &#9745;', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)- \[ \]', r'\1- &#9744;', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)- \[-\]', r'\1- &#9723;', text, flags=re.MULTILINE)

    # Agents often emit `*   **Field:**` which the markdown parser reads as
    # emphasis rather than a bullet.  Normalise to `- `.
    text = re.sub(r'^(\s*)\*(\s{2,})', r'\1-\2', text, flags=re.MULTILINE)

    # A markdown list needs a blank line before the first bullet or it gets
    # swallowed into the paragraph above; same for consecutive numbered items.
    text = re.sub(r'(\S[^\n]*)\n(\s*- )', r'\1\n\n\2', text)
    text = re.sub(r'(\S[^\n]*)\n(\d+[\.\)]\s)', r'\1\n\n\2', text)

    # Protect LaTeX math blocks from the markdown parser by replacing them
    # with placeholders and restoring them after conversion.
    math_blocks: list[str] = []

    def _save_math(m):
        math_blocks.append(m.group(0))
        return f'\x00MATH{len(math_blocks) - 1}\x00'

    text = re.sub(r'\$\$(.+?)\$\$', _save_math, text, flags=re.DOTALL)
    text = re.sub(r'\$([^\$\n]+?)\$', _save_math, text)

    html = markdown.markdown(text, extensions=["tables", "fenced_code"])

    for i, block in enumerate(math_blocks):
        html = html.replace(f'\x00MATH{i}\x00', block)

    # ---- Structured review components ---------------------------------------

    def _grade_badge(m):
        grade = m.group(1).strip().upper()
        cls = {
            "PASS": "rv-pass", "WARN": "rv-warn", "FAIL": "rv-fail",
            "A": "rv-pass", "B": "rv-pass", "C": "rv-warn",
            "D": "rv-fail", "F": "rv-fail",
        }.get(grade, "rv-warn")
        return f'<strong>Grade:</strong> <span class="rv-grade {cls}">{grade}</span>'

    html = re.sub(r'<strong>Grade:?</strong>:?\s*(\w+)', _grade_badge, html)

    html = re.sub(
        r'<p><strong>Quote:?</strong>:?\s*["\u201c](.+?)["\u201d]'
        r'(?:\s*\(([^)]+)\))?\s*</p>',
        lambda m: (
            f'<blockquote class="rv-quote">{m.group(1)}'
            + (f'<cite>{m.group(2)}</cite>' if m.group(2) else '')
            + '</blockquote>'
        ),
        html, flags=re.DOTALL,
    )
    html = re.sub(
        r'<p><strong>Quote:?</strong>:?\s*(.+?)</p>',
        lambda m: (
            f'<blockquote class="rv-quote">{m.group(1)}</blockquote>'
            if '"' in m.group(1) or '\u201c' in m.group(1)
            else m.group(0)
        ),
        html, flags=re.DOTALL,
    )

    for label in ('Suggestion', 'Rewrite'):
        html = re.sub(
            rf'<p><strong>{label}:?</strong>:?\s*(.+?)</p>',
            rf'<div class="rv-callout"><span class="rv-callout-label">{label}</span>\1</div>',
            html, flags=re.DOTALL,
        )

    for label in ('Comment', 'Critique'):
        html = re.sub(
            rf'<p><strong>{label}:?</strong>:?\s*',
            f'<p><span class="rv-field-label">{label}</span> ',
            html,
        )

    # Current / Add diff pairs used by suggested rewrites.
    html = re.sub(
        r'<p><em>Current:</em>\s*["\u201c]?(.+?)["\u201d]?\s*</p>',
        r'<div class="rv-diff"><div class="rv-before">'
        r'<span class="rv-diff-label">Current</span>\1</div>',
        html, flags=re.DOTALL,
    )
    html = re.sub(
        r'<div class="rv-before"><span class="rv-diff-label">Current</span>(.+?)</div>'
        r'\s*<p><em>Add:</em>\s*["\u201c]?(.+?)["\u201d]?\s*</p>',
        r'<div class="rv-before"><span class="rv-diff-label">Current</span>\1</div>'
        r'<div class="rv-after"><span class="rv-diff-label">Proposed</span>\2</div></div>',
        html, flags=re.DOTALL,
    )
    html = re.sub(
        r'(<div class="rv-before">.*?</div>)(?!\s*<div class="rv-after">)',
        r'\1</div>',
        html,
    )

    # Original -> Compressed diffs emitted by the Conciseness audit.
    html = re.sub(
        r'Original:\s*["\u201c](.+?)["\u201d]\s*(?:→|->)\s*'
        r'Compressed:\s*["\u201c](.+?)["\u201d]\s*(\([^)]*\))?',
        lambda m: (
            '<div class="rv-diff rv-diff-inline">'
            f'<div class="rv-before"><span class="rv-diff-label">Original</span>'
            f'{m.group(1)}</div>'
            f'<div class="rv-after"><span class="rv-diff-label">Compressed</span>'
            f'{m.group(2)}'
            + (f' <span class="rv-saved">{m.group(3)}</span>' if m.group(3) else '')
            + '</div></div>'
        ),
        html,
    )

    # Reviewer #2 triptych: weakness / why it matters / suggested defense.
    for label, cls in (
        ('The weakness', 'rv-weakness'),
        ('Why it matters', 'rv-impact'),
        ('Suggested defense', 'rv-defense'),
    ):
        html = re.sub(
            rf'<p><strong>{label}:?</strong>:?\s*(.+?)</p>',
            rf'<div class="{cls}"><span class="rv-field-label">{label}</span> \1</div>',
            html, flags=re.DOTALL,
        )

    # Action-plan severity tags.
    html = re.sub(r'\[MAJOR\]', '<span class="badge badge-major">MAJOR</span>', html)
    html = re.sub(r'\[MODERATE\]', '<span class="badge badge-moderate">MODERATE</span>', html)
    html = re.sub(r'\[MINOR\]', '<span class="badge badge-minor">MINOR</span>', html)

    return f'<div class="findings-markdown">{html}</div>'


def render_code_diff_html(diff_text: str) -> str:
    """Syntax-highlight a unified diff as an HTML block."""
    if not diff_text:
        return ""
    lines_html: list[str] = []
    for line in diff_text.splitlines():
        escaped = esc(line)
        if line.startswith("+++") or line.startswith("---"):
            lines_html.append(f'<span class="diff-header">{escaped}</span>')
        elif line.startswith("+"):
            lines_html.append(f'<span class="diff-add">{escaped}</span>')
        elif line.startswith("-"):
            lines_html.append(f'<span class="diff-remove">{escaped}</span>')
        elif line.startswith("@@"):
            lines_html.append(f'<span class="diff-header">{escaped}</span>')
        else:
            lines_html.append(escaped)
    return '<div class="code-diff">' + "\n".join(lines_html) + "</div>"


def build_figure_edit_html(edit_results) -> str:
    """Assemble the figure-edit-loop section with before / after comparisons."""
    if not edit_results or not edit_results.iterations:
        return ""

    parts = [
        '<h2>Figure Improvement Loop</h2>',
        '<p style="font-family:var(--ui-font);font-size:.88rem;color:#6b5e4b">'
        f'{edit_results.total_iterations} iterations &nbsp;|&nbsp; '
        f'{edit_results.total_elapsed:.1f}s total</p>',
    ]

    if edit_results.before_figures and edit_results.after_figures:
        parts.append('<h3>Before / After</h3>')
        before_by_name = {Path(p).name: p for p in edit_results.before_figures}
        after_by_name = {Path(p).name: p for p in edit_results.after_figures}

        for name in sorted(set(before_by_name) | set(after_by_name)):
            parts.append(f'<h4>{esc(name)}</h4>')
            parts.append('<div class="fig-compare">')

            before_path = before_by_name.get(name)
            after_path = after_by_name.get(name)

            if before_path and Path(before_path).exists():
                b64 = base64.standard_b64encode(
                    Path(before_path).read_bytes()
                ).decode()
                parts.append(
                    '<div><div class="fig-label">Before</div>'
                    f'<img src="data:image/png;base64,{b64}" alt="Before"></div>'
                )

            if after_path and Path(after_path).exists():
                b64 = base64.standard_b64encode(
                    Path(after_path).read_bytes()
                ).decode()
                parts.append(
                    '<div><div class="fig-label">After</div>'
                    f'<img src="data:image/png;base64,{b64}" alt="After"></div>'
                )

            parts.append('</div>')

    for iter_results in edit_results.iterations:
        if not iter_results:
            continue
        iter_num = iter_results[0].iteration + 1
        parts.append('<div class="iteration-section">')
        parts.append(f'<div class="iteration-header">Iteration {iter_num}</div>')

        for r in iter_results:
            badge_cls = "badge-ok" if r.success else "badge-major"
            status = "Success" if r.success else "Failed"
            parts.append(
                f'<h4>{esc(r.script_name)} '
                f'<span class="badge {badge_cls}">{status}</span></h4>'
            )
            if r.error and not r.success:
                parts.append(
                    '<p style="color:var(--major);font-size:.85rem">'
                    f'Error: {esc(r.error[:300])}</p>'
                )
            if r.diff:
                parts.append(render_code_diff_html(r.diff))

        parts.append('</div>')

    return "\n".join(parts)


def write_html_report(
    results: list,
    paper_path: str,
    model_name: str,
    provider_name: str,
    output_dir: Path,
    open_browser: bool = True,
    agreement: dict | None = None,
    config: dict | None = None,
    edit_results=None,
    comparison: dict | None = None,
    old_report_path: str | None = None,
) -> Path:
    """Render the full HTML review report. Returns the output path."""
    from .shared import FIGURE_AGENT_NAME_PREFIXES

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(paper_path).stem if paper_path else "paper"
    out_path = output_dir / f"review_{stem}_{ts}.html"

    has_scores = any(getattr(r, "score", -1) >= 0 for r in results)
    composite = weighted_composite(results, config or {}) if has_scores else 0.0
    score_color = (
        "var(--ok)" if composite >= 0.7
        else "var(--moderate)" if composite >= 0.5
        else "var(--major)"
    )

    sev_counts: dict[str, int] = {}
    for r in results:
        sev_counts[r.severity] = sev_counts.get(r.severity, 0) + 1

    # ---- Summary table rows ----
    rows = ""
    for i, r in enumerate(results, 1):
        badge = SEVERITY_BADGE.get(r.severity, r.severity)
        score_cell = f"<td>{r.score:.0%}</td>" if has_scores else ""
        rows += (
            f'<tr data-severity="{r.severity}">'
            f"<td>{i}</td><td>{esc(r.agent_name)}</td>"
            f"<td>{badge}</td>{score_cell}"
            f"<td>{esc(r.summary)}</td></tr>\n"
        )

    # ---- Quick wins (first issue from up to 5 minor-severity agents) ----
    quick_wins_html = ""
    minor_items = [r for r in results if r.severity == "minor"]
    if minor_items:
        qw_items = ""
        for r in minor_items[:5]:
            top = getattr(r, "top_issues", [])
            issue = top[0] if top else r.summary
            qw_items += (
                f"<li><strong>{esc(r.agent_name)}:</strong> {esc(issue)}</li>\n"
            )
        quick_wins_html = (
            '<div class="quick-wins">\n'
            '  <h3>Quick Wins</h3>\n'
            f'  <ul>{qw_items}</ul>\n'
            '</div>'
        )

    # ---- Section jump dropdown ----
    jump_options = '<option value="">Jump to section\u2026</option>\n'
    for r in results:
        anchor = r.agent_id.replace("_", "-")
        jump_options += (
            f'<option value="agent-{anchor}">{esc(r.agent_name)}</option>\n'
        )

    # ---- Agent findings cards ----
    sections_html = ""
    for r in results:
        badge = SEVERITY_BADGE.get(r.severity, r.severity)
        anchor = r.agent_id.replace("_", "-")
        findings_html = render_findings(r.findings)
        sections_html += (
            f'\n<div class="agent-section" data-severity="{r.severity}" id="agent-{anchor}">\n'
            '  <div class="agent-header" onclick="toggleSection(this)">\n'
            f'    {badge}\n'
            f'    <span class="agent-name">{esc(r.agent_name)}</span>\n'
            '    <span class="toggle-icon">&#9662;</span>\n'
            '  </div>\n'
            f'  <div class="findings">{findings_html}</div>\n'
            '</div>\n'
        )

    # ---- Reference suggestions ----
    refs_html = ""
    all_refs = [item for r in results for item in r.references_found]
    if all_refs:
        seen: set[str] = set()
        ref_items = ""
        for item in all_refs:
            ref = item.get("ref", {})
            title = ref.get("title", "")
            if title and title not in seen:
                seen.add(title)
                ref_items += (
                    f"<li><strong>{esc(title)}</strong> &mdash; "
                    f"{esc(ref.get('authors', ''))} "
                    f"({esc(ref.get('year', ''))}) "
                    f"<em>{esc(ref.get('venue', ''))}</em></li>\n"
                )
        if ref_items:
            refs_html = f"<h2>Suggested References</h2>\n<ul>{ref_items}</ul>"

    display_name = stem.replace("_", " ").replace("-", " ").title()
    date_str = datetime.now().strftime('%B %d, %Y')

    # ---- Radar chart (with optional revision overlay) ----
    radar_html = ""
    nlp_html = ""
    if has_scores and comparison and old_report_path:
        from pat.diff import parse_report
        old_parsed = parse_report(old_report_path)
        old_scores_map = {
            name: info["score"] for name, info in old_parsed.items()
            if not name.startswith("_") and info.get("score", -1) >= 0
        }
        radar_svg = build_comparison_radar_svg(results, old_scores_map)

        # Recompute the old composite over manuscript-only (non-figure) agents.
        old_ms_scores = [
            v for k, v in old_scores_map.items()
            if not any(k.startswith(p) for p in FIGURE_AGENT_NAME_PREFIXES)
        ]
        old_composite = (
            sum(old_ms_scores) / len(old_ms_scores) if old_ms_scores else 0
        )
        delta_pp = (composite - old_composite) * 100
        delta_cls = "positive" if delta_pp >= 0 else "negative"
        delta_sign = "+" if delta_pp >= 0 else ""
        new_color = (
            "var(--ok)" if composite >= 0.7
            else "var(--moderate)" if composite >= 0.5
            else "var(--major)"
        )

        radar_html = (
            '<div class="radar-container">\n'
            f'  {radar_svg}\n'
            '  <div class="comparison-scores">\n'
            '    <div class="score-row">\n'
            f'      <span class="score-old">{old_composite:.0%}</span>\n'
            '      <span class="score-arrow">&rarr;</span>\n'
            f'      <span class="score-new" style="color:{new_color}">{composite:.0%}</span>\n'
            '    </div>\n'
            f'    <div class="score-delta {delta_cls}">{delta_sign}{delta_pp:.1f}pp</div>\n'
            '    <div class="score-label">Submission Readiness</div>\n'
            '  </div>\n'
            '</div>'
        )

        nlp_delta = comparison.get("nlp_metrics_delta", {})
        if nlp_delta:
            nlp_labels = {
                "word_count": ("Word Count", "", "0"),
                "fk_grade": ("FK Grade", "", "1"),
                "passive_pct": ("Passive Voice", "%", "1"),
                "avg_sent_len": ("Avg Sentence Length", " words", "1"),
            }
            nlp_rows = ""
            for key, (label, unit, fmt) in nlp_labels.items():
                if key not in nlp_delta:
                    continue
                d = nlp_delta[key]
                delta = d["delta"]
                # For most text metrics, lower = better; word_count is neutral.
                is_good = delta <= 0 if key != "word_count" else True
                dcls = "delta-positive" if is_good else "delta-negative"
                dsign = "+" if delta > 0 else ""
                if fmt == "0":
                    nlp_rows += (
                        f'<tr><td>{label}</td>'
                        f'<td>{d["old"]:.0f}{unit}</td>'
                        f'<td>{d["new"]:.0f}{unit}</td>'
                        f'<td class="{dcls}">{dsign}{delta:.0f}{unit}</td></tr>\n'
                    )
                else:
                    nlp_rows += (
                        f'<tr><td>{label}</td>'
                        f'<td>{d["old"]:.1f}{unit}</td>'
                        f'<td>{d["new"]:.1f}{unit}</td>'
                        f'<td class="{dcls}">{dsign}{delta:.1f}{unit}</td></tr>\n'
                    )
            if nlp_rows:
                nlp_html = (
                    '<div class="nlp-comparison">\n'
                    '  <h3>Text Metrics Comparison</h3>\n'
                    '  <table>\n'
                    '  <thead><tr><th>Metric</th><th>Original</th>'
                    '<th>Revised</th><th>Change</th></tr></thead>\n'
                    f'  <tbody>{nlp_rows}</tbody>\n'
                    '  </table>\n'
                    '</div>'
                )
    elif has_scores:
        radar_html = (
            '<div class="radar-container">\n'
            f'  {build_radar_svg(results)}\n'
            '  <div class="composite-score">\n'
            f'    <div class="score-value" style="color:{score_color}">{composite:.0%}</div>\n'
            '    <div class="score-label">Submission Readiness</div>\n'
            '  </div>\n'
            '</div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review: {esc(stem)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=Playfair+Display:wght@400;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.21/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.21/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.21/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}],throwOnError:false}});"></script>
<style>{CSS}</style>
</head>
<body>

<nav class="top-nav">
  <div class="nav-inner">
    <button class="pill pill-active" onclick="filterSeverity('')">All <span class="pill-count">{len(results)}</span></button>
    <button class="pill" data-sev="major" onclick="filterSeverity('major')">Major <span class="pill-count">{sev_counts.get('major', 0)}</span></button>
    <button class="pill" data-sev="moderate" onclick="filterSeverity('moderate')">Moderate <span class="pill-count">{sev_counts.get('moderate', 0)}</span></button>
    <button class="pill" data-sev="minor" onclick="filterSeverity('minor')">Minor <span class="pill-count">{sev_counts.get('minor', 0)}</span></button>
    <button class="pill" data-sev="ok" onclick="filterSeverity('ok')">OK <span class="pill-count">{sev_counts.get('ok', 0)}</span></button>
    <span class="nav-sep"></span>
    <button class="pill" onclick="toggleAll()">Collapse All</button>
    <select class="section-jump" onchange="jumpToSection(this.value)">
      {jump_options}
    </select>
    <input class="search-box" id="search" type="text" placeholder="Search findings\u2026" oninput="searchFindings()">
  </div>
</nav>

<header>
  <div class="report-label">Manuscript Review</div>
  <h1>{esc(display_name)}</h1>
  <div class="meta">
    <span>{esc(provider_name)} / {esc(model_name)}</span>
    <span class="meta-sep">&middot;</span>
    <span>{date_str}</span>
    <span class="meta-sep">&middot;</span>
    <span>{len(results)} dimensions</span>
  </div>
</header>

{radar_html}

{nlp_html}

{quick_wins_html}

<h2>Summary</h2>
<table>
<thead><tr><th>#</th><th>Agent</th><th>Severity</th>{"<th>Score</th>" if has_scores else ""}<th>Finding</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>

{build_agreement_heatmap(agreement) if agreement else ""}

<h2>Detailed Findings</h2>
{sections_html}

{refs_html}

{build_figure_edit_html(edit_results) if edit_results else ""}

{JS}
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")

    if open_browser:
        webbrowser.open(f"file://{out_path.resolve()}")

    return out_path
