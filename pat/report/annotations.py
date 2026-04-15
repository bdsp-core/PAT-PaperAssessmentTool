"""
Annotated manuscript writer.

Takes the full manuscript text plus the agent results and emits a copy of the
manuscript with HTML-style comments inserted after paragraphs where an issue
was flagged.  Any comments that couldn't be matched to a paragraph fall
through to an ``UNMATCHED REVIEW COMMENTS`` block at the bottom.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


# Parameters for quote harvesting - calibrated so quotes are long enough to
# be unique but short enough to still fit on one line.
_MIN_QUOTE_CHARS = 20
_MAX_QUOTE_CHARS = 120
_MAX_QUOTES_PER_AGENT = 5
_QUOTE_MATCH_PREFIX_CHARS = 40
_ANNOTATION_TRUNCATE_CHARS = 100


def write_annotated_manuscript(
    results: list,
    paper_text: str,
    sections: dict,
    output_dir: Path,
) -> Path:
    """Write a copy of the manuscript with inline review comments."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"annotated_{ts}.md"

    annotations: list[dict] = []
    for r in results:
        if r.severity == "ok" or not r.findings:
            continue
        # Collect quoted snippets: anything in straight quotes, plus blockquote
        # lines; both are typical agent output patterns.
        quotes = re.findall(
            rf'"([^"]{{{_MIN_QUOTE_CHARS},{_MAX_QUOTE_CHARS}}})"',
            r.findings,
        )
        quotes += re.findall(
            rf'>\s*(.{{{_MIN_QUOTE_CHARS},{_MAX_QUOTE_CHARS}}})',
            r.findings,
        )
        issues = getattr(r, "top_issues", []) or []
        for q in quotes[:_MAX_QUOTES_PER_AGENT]:
            annotations.append({
                "agent": r.agent_name,
                "severity": r.severity,
                "quote": q.strip()[:_ANNOTATION_TRUNCATE_CHARS],
                "issue": issues[0] if issues else r.summary,
            })

    paragraphs = paper_text.split("\n\n")
    annotated: list[str] = []
    for para in paragraphs:
        annotated.append(para)
        # Attach the first annotation whose leading prefix matches this paragraph
        # (each annotation consumes only once).
        for ann in annotations:
            if ann["quote"][:_QUOTE_MATCH_PREFIX_CHARS].lower() in para.lower():
                annotated.append(
                    f"\n<!-- [{ann['severity'].upper()}] {ann['agent']}: "
                    f"{ann['issue'][:_ANNOTATION_TRUNCATE_CHARS]} -->\n"
                )
                annotations.remove(ann)
                break

    if annotations:
        annotated.append("\n\n<!-- UNMATCHED REVIEW COMMENTS -->\n")
        for ann in annotations:
            annotated.append(
                f"<!-- [{ann['severity'].upper()}] {ann['agent']}: "
                f"{ann['issue'][:_ANNOTATION_TRUNCATE_CHARS]} -->"
            )

    out_path.write_text("\n\n".join(annotated), encoding="utf-8")
    return out_path
