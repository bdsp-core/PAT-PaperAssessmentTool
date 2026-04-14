"""
Section-aware manuscript parser for PAT.

Detects the standard scientific-paper sections (Abstract, Introduction,
Methods, Results, Discussion, Conclusion, Related Work, References,
Supplementary) and returns a ``{section: text}`` dictionary that downstream
agents use to target their reviews.

Two header styles are recognised:

1. Ordinary headers, optionally numbered::

       1. Introduction
       2.1 Related work

2. Character-spaced headers commonly produced by PDF extractors::

       A B S T R A C T
       M E T H O D S
"""

from __future__ import annotations

import re


# Common section header patterns (case-insensitive).  Each entry is
# ``(regex, canonical_name)``.
_SECTION_PATTERNS: list[tuple[str, str]] = [
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?abstract\s*(?:\n|$)", "abstract"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?introduction\s*(?:\n|$)", "introduction"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?(?:materials?\s+and\s+)?methods?\s*(?:\n|$)", "methods"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?results?\s*(?:and\s+discussion)?\s*(?:\n|$)", "results"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?discussion\s*(?:\n|$)", "discussion"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?conclusions?\s*(?:\n|$)", "conclusion"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?(?:related\s+)?work\s*(?:\n|$)", "related_work"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?(?:references|bibliography|works\s+cited)\s*(?:\n|$)", "references"),
    (r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?(?:supplementary|appendix|appendices)\s*", "supplementary"),
]

# Spaced-out headers frequently emitted by PDF text extraction.
# Map each canonical header word to the target section name.
_SPACED_HEADERS: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "introduction",
    "methods": "methods",
    "results": "results",
    "discussion": "discussion",
    "conclusions": "conclusion",
    "conclusion": "conclusion",
    "references": "references",
}
for _word, _section in _SPACED_HEADERS.items():
    _spaced = r"\s+".join(_word)
    _SECTION_PATTERNS.append(
        (rf"(?:^|\n)\s*{_spaced}\s*(?:\n|$)", _section)
    )


def parse_sections(text: str) -> dict[str, str]:
    """Parse a manuscript into named sections.

    Always returns a ``"full"`` entry with the complete text; every other
    section is included only when its header is located.

    Args:
        text: The raw manuscript text.

    Returns:
        Mapping of ``section_name -> section_text`` (plus ``"full"``).
    """
    sections: dict[str, str] = {"full": text}

    # Locate every section header.
    boundaries: list[tuple[int, str]] = []
    for pattern, name in _SECTION_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            boundaries.append((match.start(), name))

    if not boundaries:
        return sections

    boundaries.sort(key=lambda x: x[0])

    # Slice the text between consecutive boundaries.
    for i, (pos, name) in enumerate(boundaries):
        header_end = text.find("\n", pos + 1)
        if header_end == -1:
            header_end = pos
        start = header_end + 1
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)

        content = text[start:end].strip()
        if content:
            sections[name] = content

    return sections


def get_section(
    sections: dict[str, str],
    name: str,
    fallback: str = "full",
) -> str:
    """Return a section by name, falling back to ``sections[fallback]``."""
    return sections.get(name, sections.get(fallback, ""))


def get_sections_combined(
    sections: dict[str, str],
    names: list[str],
    fallback: str = "full",
) -> str:
    """Concatenate several sections as markdown headings.

    Used by agents that need more context than one section but less than the
    entire paper (for example: abstract + introduction for VSNC, or
    methods + results for statistics).
    """
    parts: list[str] = []
    for name in names:
        if name in sections:
            parts.append(f"## {name.title()}\n\n{sections[name]}")
    if parts:
        return "\n\n".join(parts)
    return sections.get(fallback, "")
