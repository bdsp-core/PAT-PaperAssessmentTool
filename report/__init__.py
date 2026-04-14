"""
PAT (Paper Assessment Tool) - report package.

Public API for callers of :mod:`run_review`::

    from report import (
        write_markdown_report,
        write_html_report,
        write_annotated_manuscript,
        write_summary_dashboard,
    )

Internal modules:

* :mod:`report.shared`       - small helpers shared by every other submodule.
* :mod:`report.charts`       - radar and heatmap SVG builders.
* :mod:`report.markdown`     - markdown report writer.
* :mod:`report.html`         - HTML report writer + findings renderer.
* :mod:`report.annotations`  - inline-annotated manuscript writer.
* :mod:`report.summary`      - cross-paper revision dashboard.
* :mod:`report._assets`      - embedded CSS / JS blobs for the HTML report.
"""

from __future__ import annotations

from .annotations import write_annotated_manuscript
from .html import write_html_report
from .markdown import write_markdown_report
from .summary import write_summary_dashboard


__all__ = [
    "write_markdown_report",
    "write_html_report",
    "write_annotated_manuscript",
    "write_summary_dashboard",
]
