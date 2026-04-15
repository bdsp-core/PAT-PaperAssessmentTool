"""
Cross-manuscript revision summary dashboard.

Given a list of ``(paper_name, old_report_path, new_report_dir)`` tuples this
module produces a single HTML dashboard comparing before / after scores across
many papers.  The dashboard is designed to give a lab at-a-glance visibility
into whether a revision push actually moved the needle.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from .shared import FIGURE_AGENT_NAME_PREFIXES, esc


def write_summary_dashboard(
    papers: list[tuple[str, str, str]],
    output_dir: Path,
) -> Path:
    """Render a one-page dashboard comparing old vs new review scores.

    Args:
        papers: Sequence of ``(paper_name, old_report_path, new_report_dir)``.
            ``new_report_dir`` is scanned for the most recent ``review_*.md``.
        output_dir: Target directory for ``summary_dashboard.html``.

    Returns:
        The written dashboard's ``Path``.
    """
    from pat.diff import parse_nlp_metrics_from_summary, parse_report

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "summary_dashboard.html"

    cards_html = ""
    total_old = 0.0
    total_new = 0.0
    count = 0

    for paper_name, old_report_path, new_report_dir in papers:
        new_dir = Path(new_report_dir)
        md_files = sorted(
            new_dir.glob("review_*.md"), key=lambda p: p.stat().st_mtime
        )
        if not md_files:
            cards_html += (
                f'<div class="card"><h2>{esc(paper_name)}</h2>'
                '<p>No report found</p></div>\n'
            )
            continue
        new_report_path = str(md_files[-1])

        old_parsed = parse_report(old_report_path)
        new_parsed = parse_report(new_report_path)

        # Manuscript-only (exclude figure agents) scored dimensions.
        old_scores: dict[str, float] = {}
        for name, info in old_parsed.items():
            if name.startswith("_"):
                continue
            if any(name.startswith(p) for p in FIGURE_AGENT_NAME_PREFIXES):
                continue
            if info.get("score", -1) >= 0:
                old_scores[name] = info["score"]

        new_scores: dict[str, float] = {}
        for name, info in new_parsed.items():
            if name.startswith("_"):
                continue
            if any(name.startswith(p) for p in FIGURE_AGENT_NAME_PREFIXES):
                continue
            if info.get("score", -1) >= 0:
                new_scores[name] = info["score"]

        old_comp = sum(old_scores.values()) / len(old_scores) if old_scores else 0
        new_comp = sum(new_scores.values()) / len(new_scores) if new_scores else 0
        delta_pp = (new_comp - old_comp) * 100
        total_old += old_comp
        total_new += new_comp
        count += 1

        # Compact radar for the card.
        shared = sorted(name for name in new_scores if name in old_scores)
        radar_svg = ""
        if len(shared) >= 3:
            n = len(shared)
            cx, cy, radius = 110, 110, 90
            angle_step = 2 * math.pi / n
            grid = ""
            for level in (0.5, 1.0):
                gr = radius * level
                grid += (
                    f'<circle cx="{cx}" cy="{cy}" r="{gr:.0f}" fill="none" '
                    f'stroke="#d4c5a9" stroke-width="0.5" stroke-dasharray="3,3"/>\n'
                )
            old_pts: list[str] = []
            new_pts: list[str] = []
            for i, name in enumerate(shared):
                angle = -math.pi / 2 + i * angle_step
                ox = cx + radius * old_scores[name] * math.cos(angle)
                oy = cy + radius * old_scores[name] * math.sin(angle)
                old_pts.append(f"{ox:.1f},{oy:.1f}")
                nx = cx + radius * new_scores[name] * math.cos(angle)
                ny = cy + radius * new_scores[name] * math.sin(angle)
                new_pts.append(f"{nx:.1f},{ny:.1f}")

            old_poly = (
                f'<polygon points="{" ".join(old_pts)}" '
                f'fill="rgba(150,150,150,.08)" stroke="#999" '
                f'stroke-width="1" stroke-dasharray="4,3"/>\n'
            )
            new_poly = (
                f'<polygon points="{" ".join(new_pts)}" '
                f'fill="rgba(139,105,20,.15)" stroke="#8b6914" stroke-width="1.5"/>\n'
            )
            radar_svg = (
                '<svg viewBox="0 0 220 220" xmlns="http://www.w3.org/2000/svg">'
                f'{grid}{old_poly}{new_poly}</svg>'
            )

        # Top 3 most-improved agents.
        deltas = [
            (name, new_scores[name] - old_scores[name])
            for name in shared
            if new_scores[name] - old_scores[name] > 0
        ]
        deltas.sort(key=lambda x: -x[1])
        improved_html = "".join(
            f'<li>{esc(name)}: <span class="pos">+{d:.0%}</span></li>\n'
            for name, d in deltas[:3]
        )

        old_nlp_summary = old_parsed.get("Text Metrics", {}).get("summary", "")
        new_nlp_summary = new_parsed.get("Text Metrics", {}).get("summary", "")
        old_nlp = parse_nlp_metrics_from_summary(old_nlp_summary)
        new_nlp = parse_nlp_metrics_from_summary(new_nlp_summary)
        nlp_html = ""
        if old_nlp.get("fk_grade") is not None and new_nlp.get("fk_grade") is not None:
            nlp_html = (
                '<div class="nlp-row">'
                f'FK: {old_nlp["fk_grade"]:.1f} &rarr; {new_nlp["fk_grade"]:.1f} | '
                f'Passive: {old_nlp.get("passive_pct", 0):.0f}% &rarr; '
                f'{new_nlp.get("passive_pct", 0):.0f}%'
                '</div>'
            )

        delta_cls = "pos" if delta_pp >= 0 else "neg"
        delta_sign = "+" if delta_pp >= 0 else ""
        new_color = (
            "#1a6847" if new_comp >= 0.7
            else "#a07214" if new_comp >= 0.5
            else "#912626"
        )

        cards_html += f"""<div class="card">
  <h2>{esc(paper_name)}</h2>
  <div class="card-body">
    <div class="card-radar">{radar_svg}</div>
    <div class="card-scores">
      <div class="score-line">
        <span class="old-score">{old_comp:.0%}</span>
        <span class="arrow">&rarr;</span>
        <span class="new-score" style="color:{new_color}">{new_comp:.0%}</span>
        <span class="{delta_cls}">{delta_sign}{delta_pp:.1f}pp</span>
      </div>
      {f'<div class="top-improved"><strong>Most improved:</strong><ul>{improved_html}</ul></div>' if improved_html else ''}
      {nlp_html}
    </div>
  </div>
</div>
"""

    avg_old = (total_old / count * 100) if count else 0
    avg_new = (total_new / count * 100) if count else 0
    avg_delta = avg_new - avg_old
    date_str = datetime.now().strftime('%B %d, %Y')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PAT Revision Impact Summary</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #f7f5f0; --fg: #1c1917; --accent: #7c5e2a;
  --ok: #1a6847; --moderate: #a07214; --major: #912626;
  --border: #d6cfc0; --card-bg: #fffef9;
  --card-shadow: 0 1px 3px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.03);
  --ui-font: 'DM Sans', system-ui, sans-serif;
  --display-font: 'Playfair Display', Georgia, serif;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: var(--ui-font); background: var(--bg); color: var(--fg);
  max-width: 1100px; margin: 0 auto; padding: 3rem 2rem;
  line-height: 1.6;
}}
h1 {{
  font-family: var(--display-font); font-size: 2rem; font-weight: 700;
  margin-bottom: .3rem;
}}
.subtitle {{
  font-size: .85rem; color: #7a6e5c; margin-bottom: 2rem;
}}
.aggregate {{
  display: flex; gap: 2rem; margin-bottom: 2.5rem; flex-wrap: wrap;
}}
.agg-card {{
  background: var(--card-bg); border: 2px solid var(--border);
  border-radius: .6rem; padding: 1.5rem 2rem; text-align: center;
  box-shadow: var(--card-shadow); flex: 1; min-width: 180px;
}}
.agg-card .agg-value {{
  font-family: var(--display-font); font-size: 2.5rem; font-weight: 700;
}}
.agg-card .agg-label {{
  font-size: .72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: .08em; color: #7a6e5c; margin-top: .4rem;
}}
.grid {{ display: grid; grid-template-columns: 1fr; gap: 1.5rem; }}
@media (min-width: 700px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
.card {{
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .5rem; padding: 1.3rem 1.5rem;
  box-shadow: var(--card-shadow);
}}
.card h2 {{
  font-family: var(--display-font); font-size: 1.15rem; font-weight: 600;
  margin-bottom: .8rem; padding-bottom: .5rem;
  border-bottom: 1px solid var(--border);
}}
.card-body {{ display: flex; gap: 1rem; align-items: center; }}
.card-radar {{ flex-shrink: 0; }}
.card-radar svg {{ width: 160px; height: 160px; }}
.card-scores {{ flex: 1; }}
.score-line {{
  font-size: 1.1rem; font-weight: 600; margin-bottom: .5rem;
  display: flex; align-items: center; gap: .4rem;
}}
.old-score {{ color: #999; text-decoration: line-through; font-size: 1rem; }}
.arrow {{ color: #7a6e5c; }}
.new-score {{ font-size: 1.5rem; font-weight: 700; }}
.pos {{ color: var(--ok); font-weight: 600; }}
.neg {{ color: var(--major); font-weight: 600; }}
.top-improved {{ font-size: .82rem; margin-top: .3rem; }}
.top-improved ul {{ list-style: none; padding: 0; }}
.top-improved li {{ margin: .15rem 0; }}
.nlp-row {{
  font-size: .78rem; color: #7a6e5c; margin-top: .5rem;
}}
.legend {{
  display: flex; gap: 1.5rem; align-items: center;
  font-size: .78rem; color: #7a6e5c; margin-bottom: 1.5rem;
}}
.legend-item {{ display: flex; align-items: center; gap: .35rem; }}
.legend-line {{
  width: 20px; height: 2px; display: inline-block;
}}
.legend-line.original {{ background: #999; border-top: 1px dashed #999; height: 0; }}
.legend-line.revised {{ background: #8b6914; }}
</style>
</head>
<body>

<h1>Revision Impact Summary</h1>
<div class="subtitle">{date_str} &middot; {count} manuscripts &middot; Manuscript-only agents (no figure vision)</div>

<div class="legend">
  <div class="legend-item"><span class="legend-line original"></span> Original</div>
  <div class="legend-item"><span class="legend-line revised"></span> Revised</div>
</div>

<div class="aggregate">
  <div class="agg-card">
    <div class="agg-value" style="color:#999">{avg_old:.0f}%</div>
    <div class="agg-label">Avg Original Score</div>
  </div>
  <div class="agg-card">
    <div class="agg-value" style="color:{'var(--ok)' if avg_new >= 70 else 'var(--moderate)' if avg_new >= 50 else 'var(--major)'}">{avg_new:.0f}%</div>
    <div class="agg-label">Avg Revised Score</div>
  </div>
  <div class="agg-card">
    <div class="agg-value" style="color:var(--ok)">+{avg_delta:.1f}pp</div>
    <div class="agg-label">Avg Improvement</div>
  </div>
</div>

<div class="grid">
{cards_html}
</div>

</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    return out_path
