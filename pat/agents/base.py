"""
Base types, parsers, and shared helpers for PAT agents.

Every agent inherits from `BaseAgent` and returns an `AgentResult`.
The parser helpers extract structured fields (severity, score, summary,
top issues, section references) from free-form LLM output in two passes:

  1. Try to parse a JSON object emitted by the model.
  2. Fall back to the ``SEVERITY: ... SCORE: ... SUMMARY: ...`` footer that
     the standardised output template instructs every agent to include.
  3. As a last resort, apply a lightweight string heuristic.

The structured-footer template is appended to every system prompt through
`BaseAgent._call` / `BaseAgent._call_with_images`.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .constants import (
    SEVERITY_TO_SCORE,
    STANDARD_MAX_TOKENS,
    TOP_ISSUES_MAX,
)

if TYPE_CHECKING:
    from pat.providers import LLMProvider


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Structured output of a single agent run.

    Attributes:
        agent_id: Stable short identifier (e.g. ``"vsnc"``).
        agent_name: Human-readable name (e.g. ``"VSNC Framework"``).
        summary: One-line verdict shown in the report summary table.
        findings: Full markdown body of the agent's review.
        severity: One of ``"ok"``, ``"minor"``, ``"moderate"``, ``"major"``.
        references_found: Populated only by reference-search agents.
        elapsed: Wall-clock seconds spent running this agent.
        top_issues: Up to ``TOP_ISSUES_MAX`` short issue strings for the
            orchestrator's cross-agent synthesis.
        score: 0-1 quality score on this dimension. ``-1`` means unscored.
        section_refs: Lower-case paper sections this agent flagged
            (e.g. ``["methods", "results"]``). Used by the agreement matrix.
    """

    agent_id: str
    agent_name: str
    summary: str
    findings: str
    severity: str
    references_found: list
    elapsed: float = 0.0
    top_issues: list = field(default_factory=list)
    score: float = -1.0
    section_refs: list = field(default_factory=list)


@dataclass
class Context:
    """Execution context passed to every agent's ``run()`` method."""

    paper_text: str
    code_text: str = ""
    figures: list = field(default_factory=list)
    prior_results: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    provider: Optional["LLMProvider"] = None
    sections: dict = field(default_factory=dict)          # From parser.parse_sections
    on_chunk: Optional[object] = None                     # Streaming callback
    ref_backend: Optional[object] = None                  # ReferenceSearchBackend


# ---------------------------------------------------------------------------
# Structured output footer (appended to every LLM agent system prompt)
# ---------------------------------------------------------------------------

STRUCTURED_FOOTER = """

## Required Output Footer
End your response with exactly this structure:
---
SEVERITY: [ok|minor|moderate|major]
SCORE: <0.0-1.0 quality score for this dimension>
SUMMARY: <one sentence summarizing your key finding>
SECTION_REFS: <comma-separated list of sections with issues, e.g. introduction,methods>
TOP_ISSUES:
1. <most important issue found>
2. <second most important issue>
3. <third most important issue>
"""


# ---------------------------------------------------------------------------
# Parsers for the structured output footer
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = ("major", "moderate", "minor", "ok")
_FOOTER_KEYS = ("SEVERITY:", "SCORE:", "SECTION_REFS:", "TOP_ISSUES:")
_KNOWN_SECTIONS = (
    "abstract", "introduction", "methods", "results", "discussion", "conclusion",
)


def _parse_json_result(text: str) -> dict | None:
    """Extract a JSON object from the model's reply, if one is present."""
    # Look for JSON wrapped in ```json fences, or a bare object containing "severity".
    for pattern in (r'```json\s*(\{.*?\})\s*```', r'(\{[^{}]*"severity"[^{}]*\})'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
    # Maybe the whole reply is JSON.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_severity(text: str) -> str:
    """Extract severity from JSON, structured footer, or heuristic fallback."""
    obj = _parse_json_result(text)
    if obj and "severity" in obj:
        val = str(obj["severity"]).lower().strip()
        if val in _VALID_SEVERITIES:
            return val

    # Structured footer wins when JSON is absent or invalid.
    for line in reversed(text.splitlines()):
        stripped = line.strip().lower()
        if stripped.startswith("severity:"):
            val = stripped.split(":", 1)[1].strip()
            if val in _VALID_SEVERITIES:
                return val

    # Free-text heuristic: look for "severity: X" anywhere in body prose.
    text_lower = text.lower()
    if "severity: major" in text_lower:
        return "major"
    if "severity: moderate" in text_lower:
        return "moderate"
    if "severity: minor" in text_lower:
        return "minor"
    return "ok"


def _parse_summary(text: str) -> str:
    """Extract SUMMARY: line (with continuation) or fall back to first non-empty line."""
    obj = _parse_json_result(text)
    if obj and "summary" in obj:
        return str(obj["summary"]).strip()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()
            # LLMs sometimes wrap the summary across multiple lines; keep
            # stitching until we hit a blank line or another footer key.
            for j in range(i + 1, len(lines)):
                cont = lines[j].strip()
                if not cont or any(cont.upper().startswith(k) for k in _FOOTER_KEYS):
                    break
                summary += " " + cont
            return summary

    for line in lines:
        if line.strip():
            return line.strip()
    return "(no summary)"


def _parse_top_issues(text: str) -> list[str]:
    """Extract the enumerated issues under a ``TOP_ISSUES:`` header."""
    obj = _parse_json_result(text)
    if obj and "top_issues" in obj and isinstance(obj["top_issues"], list):
        return [str(i).strip() for i in obj["top_issues"][:TOP_ISSUES_MAX]]

    issues: list[str] = []
    in_issues = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TOP_ISSUES:"):
            in_issues = True
            continue
        if not in_issues:
            continue

        m = re.match(r'^(?:\d+[\.\)]\s*|-\s*)(.*)', stripped)
        if m and m.group(1).strip():
            issues.append(m.group(1).strip())
        elif stripped and not stripped.startswith(("SEVERITY:", "SUMMARY:")):
            # Continuation line for the previous issue.
            if issues:
                issues[-1] += " " + stripped
        else:
            break

        if len(issues) >= TOP_ISSUES_MAX:
            break
    return issues


def _parse_section_refs(text: str) -> list[str]:
    """Extract SECTION_REFS or infer flagged sections by name mention."""
    obj = _parse_json_result(text)
    if obj and "section_refs" in obj and isinstance(obj["section_refs"], list):
        return [str(s).strip().lower() for s in obj["section_refs"]]

    text_lower = text.lower()
    return [name for name in _KNOWN_SECTIONS if name in text_lower]


def _parse_score(text: str, severity: str) -> float:
    """Extract a numeric SCORE value or derive one from severity."""
    obj = _parse_json_result(text)
    if obj and "score" in obj:
        try:
            value = float(obj["score"])
            if 0.0 <= value <= 1.0:
                return value
        except (ValueError, TypeError):
            pass
    return SEVERITY_TO_SCORE.get(severity, 0.5)


# ---------------------------------------------------------------------------
# Figure agent helpers
# ---------------------------------------------------------------------------

def _get_image_metadata(path: str) -> str:
    """Return a FILE METADATA block for use with the figure-format agent."""
    size_mb = os.path.getsize(path) / (1024 * 1024)
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
            mode = img.mode
            fmt = img.format or os.path.splitext(path)[1].upper().lstrip(".")
        return (
            "FILE METADATA:\n"
            f"- filename: {os.path.basename(path)}\n"
            f"- file_size_mb: {size_mb:.2f}\n"
            f"- width_px: {w}\n"
            f"- height_px: {h}\n"
            f"- file_format: {fmt}\n"
            f"- color_mode: {mode}\n"
        )
    except ImportError:
        # PIL not installed: fall back to filename + size only.
        return (
            "FILE METADATA:\n"
            f"- filename: {os.path.basename(path)}\n"
            f"- file_size_mb: {size_mb:.2f}\n"
        )


def _skip_result(agent_id: str, agent_name: str) -> AgentResult:
    """Neutral result used by figure agents when no figures or no vision model."""
    return AgentResult(
        agent_id=agent_id,
        agent_name=agent_name,
        summary="Skipped - no figures provided or model lacks vision",
        findings="This agent requires figure images and a vision-capable model.",
        severity="ok",
        references_found=[],
        elapsed=0.0,
        score=0.5,
    )


# ---------------------------------------------------------------------------
# Journal configuration helper (used by several agents)
# ---------------------------------------------------------------------------

def _journal_context(config: dict) -> str:
    """Format journal requirements as trailing user-prompt context."""
    if not config:
        return ""
    parts = ["\n\n--- JOURNAL REQUIREMENTS ---"]
    if config.get("journal_name"):
        parts.append(f"Target journal: {config['journal_name']}")
    if config.get("word_limit"):
        parts.append(f"Word limit: {config['word_limit']}")
    if config.get("abstract_word_limit"):
        parts.append(f"Abstract word limit: {config['abstract_word_limit']}")
    if config.get("max_figures"):
        parts.append(f"Max figures: {config['max_figures']}")
    if config.get("max_tables"):
        parts.append(f"Max tables: {config['max_tables']}")
    if config.get("citation_style"):
        parts.append(f"Citation style: {config['citation_style']}")
    if config.get("style_guide"):
        parts.append(f"Style guide: {config['style_guide']}")
    for note in config.get("custom_notes", []):
        parts.append(f"Note: {note}")
    return "\n".join(parts) if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent:
    """Abstract parent for every agent in the pipeline.

    Subclasses override :meth:`run` and supply ``id``, ``name``, ``description``
    class attributes.  ``priority`` controls phase ordering:

        * 0 - instant programmatic agent (no LLM)
        * 1 - independent analysis, runs early
        * 2 - whole-document synthesis, may use Phase 1 results
        * 3 - final synthesis / orchestration
    """

    id: str = ""
    name: str = ""
    description: str = ""
    priority: int = 1
    needs_code: bool = False
    needs_scholar: bool = False

    def _call(
        self,
        ctx: Context,
        system: str,
        user: str,
        max_tokens: int = STANDARD_MAX_TOKENS,
    ) -> str:
        """Invoke the provider with the structured output footer appended."""
        system_with_footer = system + STRUCTURED_FOOTER
        return ctx.provider.call(
            system_with_footer, user,
            max_tokens=max_tokens,
            on_chunk=ctx.on_chunk,
        )

    def _call_with_images(
        self,
        ctx: Context,
        system: str,
        user: str,
        images: list[str],
        max_tokens: int = STANDARD_MAX_TOKENS,
    ) -> str:
        """Multimodal provider call with the structured output footer appended."""
        system_with_footer = system + STRUCTURED_FOOTER
        return ctx.provider.call_with_images(
            system_with_footer, user, images,
            max_tokens=max_tokens,
            on_chunk=ctx.on_chunk,
        )

    def _build_result(
        self,
        findings: str,
        t0: float,
        references_found: list | None = None,
    ) -> AgentResult:
        """Parse an LLM reply into an :class:`AgentResult`."""
        severity = _parse_severity(findings)
        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=_parse_summary(findings),
            findings=findings,
            severity=severity,
            references_found=references_found or [],
            elapsed=time.time() - t0,
            top_issues=_parse_top_issues(findings),
            score=_parse_score(findings, severity),
            section_refs=_parse_section_refs(findings),
        )

    def run(self, ctx: Context) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError


# textwrap is re-exported for agent modules that build SYSTEM prompts.
__all__ = [
    "AgentResult",
    "Context",
    "BaseAgent",
    "STRUCTURED_FOOTER",
    "_parse_severity",
    "_parse_summary",
    "_parse_top_issues",
    "_parse_section_refs",
    "_parse_score",
    "_get_image_metadata",
    "_skip_result",
    "_journal_context",
    "textwrap",
    "time",
    "re",
]
