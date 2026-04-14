"""
Inline SVG chart builders for the PAT HTML report.

Three shapes:

* :func:`build_radar_svg` - single polygon of dimension scores.
* :func:`build_comparison_radar_svg` - two polygons overlaying before / after.
* :func:`build_agreement_heatmap` - section x agent severity matrix.

All functions return a standalone string suitable for direct insertion into the
HTML report.  An empty string is returned when the input is too sparse for the
chart to be useful.
"""

from __future__ import annotations

import math

from .shared import esc


def build_radar_svg(results: list) -> str:
    """Build a single-polygon radar chart across scored agents."""
    scored = [
        (r.agent_name, r.score) for r in results
        if getattr(r, "score", -1) >= 0 and r.agent_id != "metrics"
    ]
    if len(scored) < 3:
        return ""

    n = len(scored)
    cx, cy, r = 180, 180, 150
    angle_step = 2 * math.pi / n

    grid = ""
    for level in (0.25, 0.5, 0.75, 1.0):
        gr = r * level
        grid += (
            f'<circle cx="{cx}" cy="{cy}" r="{gr:.0f}" fill="none" '
            f'stroke="#d4c5a9" stroke-width="0.5" stroke-dasharray="3,3"/>\n'
        )

    axes = ""
    labels = ""
    points: list[str] = []
    for i, (name, score) in enumerate(scored):
        angle = -math.pi / 2 + i * angle_step
        ex = cx + r * math.cos(angle)
        ey = cy + r * math.sin(angle)
        axes += (
            f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="#d4c5a9" stroke-width="0.5"/>\n'
        )
        lx = cx + (r + 20) * math.cos(angle)
        ly = cy + (r + 20) * math.sin(angle)
        anchor = "middle"
        if lx < cx - 10:
            anchor = "end"
        elif lx > cx + 10:
            anchor = "start"
        short_name = name[:18] + "..." if len(name) > 18 else name
        labels += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="9" font-family="DM Sans, system-ui, sans-serif" '
            f'fill="#7a6e5c">{esc(short_name)}</text>\n'
        )
        px = cx + r * score * math.cos(angle)
        py = cy + r * score * math.sin(angle)
        points.append(f"{px:.1f},{py:.1f}")

    polygon = (
        f'<polygon points="{" ".join(points)}" '
        f'fill="rgba(139,105,20,.15)" stroke="#8b6914" stroke-width="2"/>\n'
    )

    dots = ""
    for pt in points:
        x, y = pt.split(",")
        dots += f'<circle cx="{x}" cy="{y}" r="3" fill="#8b6914"/>\n'

    return (
        '<svg viewBox="0 0 360 360" xmlns="http://www.w3.org/2000/svg">\n'
        f"{grid}{axes}{polygon}{dots}{labels}\n"
        "</svg>"
    )


def build_comparison_radar_svg(new_results: list,
                               old_scores: dict[str, float]) -> str:
    """Overlay two polygons (original vs revised) for shared agent dimensions.

    ``old_scores`` is ``{agent_name: score}`` extracted from a prior report.
    When fewer than three agents overlap the renderer falls back to the single
    polygon chart.
    """
    from .shared import FIGURE_AGENT_NAME_PREFIXES

    new_map: dict[str, float] = {}
    for r in new_results:
        if r.agent_id == "metrics":
            continue
        if any(r.agent_name.startswith(p) for p in FIGURE_AGENT_NAME_PREFIXES):
            continue
        if getattr(r, "score", -1) >= 0:
            new_map[r.agent_name] = r.score

    shared = sorted(
        name for name in new_map
        if name in old_scores and old_scores[name] >= 0
    )
    if len(shared) < 3:
        return build_radar_svg(new_results)

    n = len(shared)
    cx, cy, radius = 200, 200, 160
    angle_step = 2 * math.pi / n

    grid = ""
    for level in (0.25, 0.5, 0.75, 1.0):
        gr = radius * level
        grid += (
            f'<circle cx="{cx}" cy="{cy}" r="{gr:.0f}" fill="none" '
            f'stroke="#d4c5a9" stroke-width="0.5" stroke-dasharray="3,3"/>\n'
        )

    axes = ""
    labels = ""
    old_points: list[str] = []
    new_points: list[str] = []
    for i, name in enumerate(shared):
        angle = -math.pi / 2 + i * angle_step
        ex = cx + radius * math.cos(angle)
        ey = cy + radius * math.sin(angle)
        axes += (
            f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="#d4c5a9" stroke-width="0.5"/>\n'
        )
        lx = cx + (radius + 22) * math.cos(angle)
        ly = cy + (radius + 22) * math.sin(angle)
        anchor = "middle"
        if lx < cx - 10:
            anchor = "end"
        elif lx > cx + 10:
            anchor = "start"
        short = name[:18] + "..." if len(name) > 18 else name
        labels += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="8" font-family="DM Sans, system-ui, sans-serif" '
            f'fill="#7a6e5c">{esc(short)}</text>\n'
        )
        os_ = old_scores[name]
        ox = cx + radius * os_ * math.cos(angle)
        oy = cy + radius * os_ * math.sin(angle)
        old_points.append(f"{ox:.1f},{oy:.1f}")
        ns = new_map[name]
        nx_ = cx + radius * ns * math.cos(angle)
        ny = cy + radius * ns * math.sin(angle)
        new_points.append(f"{nx_:.1f},{ny:.1f}")

    old_poly = (
        f'<polygon points="{" ".join(old_points)}" '
        f'fill="rgba(150,150,150,.08)" stroke="#999" '
        f'stroke-width="1.5" stroke-dasharray="6,4"/>\n'
    )
    new_poly = (
        f'<polygon points="{" ".join(new_points)}" '
        f'fill="rgba(139,105,20,.15)" stroke="#8b6914" stroke-width="2"/>\n'
    )

    old_dots = ""
    for pt in old_points:
        x, y = pt.split(",")
        old_dots += f'<circle cx="{x}" cy="{y}" r="2.5" fill="#999"/>\n'
    new_dots = ""
    for pt in new_points:
        x, y = pt.split(",")
        new_dots += f'<circle cx="{x}" cy="{y}" r="3" fill="#8b6914"/>\n'

    legend = (
        f'<g transform="translate(10, {cy * 2 - 18})">\n'
        '  <line x1="0" y1="0" x2="18" y2="0" stroke="#999" '
        'stroke-width="1.5" stroke-dasharray="6,4"/>\n'
        '  <text x="22" y="4" font-size="9" font-family="DM Sans, system-ui, sans-serif" '
        'fill="#999">Original</text>\n'
        '  <line x1="90" y1="0" x2="108" y2="0" stroke="#8b6914" stroke-width="2"/>\n'
        '  <text x="112" y="4" font-size="9" font-family="DM Sans, system-ui, sans-serif" '
        'fill="#8b6914">Revised</text>\n'
        '</g>\n'
    )

    return (
        '<svg viewBox="0 0 400 410" xmlns="http://www.w3.org/2000/svg">\n'
        f"{grid}{axes}{old_poly}{old_dots}{new_poly}{new_dots}{labels}{legend}\n"
        "</svg>"
    )


def build_agreement_heatmap(agreement: dict) -> str:
    """Render the inter-agent agreement matrix as an HTML heatmap."""
    matrix = agreement.get("section_matrix", {})
    if not matrix:
        return ""

    sections = list(matrix.keys())
    agents: set = set()
    for s in sections:
        agents.update(matrix[s].keys())
    agents = sorted(agents)
    if not agents or not sections:
        return ""

    header = "<tr><th></th>" + "".join(
        f'<th style="writing-mode:vertical-lr;transform:rotate(180deg)">{esc(a[:12])}</th>'
        for a in agents
    ) + "</tr>\n"

    rows = ""
    for s in sections:
        cells = ""
        for a in agents:
            sev = matrix[s].get(a)
            if sev:
                css = f"heat-{sev}"
                label = sev[0].upper()
            else:
                css = "heat-none"
                label = "-"
            cells += f'<td class="{css}">{label}</td>'
        rows += f"<tr><th style='text-align:right'>{esc(s.title())}</th>{cells}</tr>\n"

    kappa = agreement.get("fleiss_kappa", 0)
    agree_pct = agreement.get("agent_agreement_pct", 0)

    return (
        "<h2>Inter-Agent Agreement</h2>\n"
        '<p style="font-family:var(--ui-font);font-size:.88rem;color:#6b5e4b">\n'
        f"Fleiss' &kappa; = {kappa:.3f} &nbsp;|&nbsp; "
        f"Overall agreement: {agree_pct}%</p>\n"
        f'<table class="heatmap-table">{header}{rows}</table>'
    )
