"""
Programmatic text-analysis metrics for PAT.

Runs in well under a second on any manuscript and produces an objective,
LLM-independent set of writing-quality signals:

* Word / sentence / paragraph counts.
* Flesch-Kincaid grade and reading ease.
* Long-sentence share, average and maximum sentence length.
* Passive-voice percentage.
* Hedging density per 1000 words.
* Unique acronyms.

These metrics feed :class:`agents.phase1_content.TextMetricsAgent` (Phase 0)
and are surfaced in both the markdown and HTML reports.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Thresholds (documentation-oriented; tweak cautiously)
# ---------------------------------------------------------------------------

_LONG_SENTENCE_WORDS = 30
_TARGET_AVG_SENTENCE_LEN = 22
_MAX_SENTENCE_WARN = 40
_LONG_SENTENCES_WARN_PCT = 15
_ACADEMIC_FK_LOW = 12
_ACADEMIC_FK_HIGH = 16
_PASSIVE_VOICE_WARN_PCT = 20
_HEDGE_DENSITY_LOW = 5
_HEDGE_DENSITY_MODERATE = 10
_ACRONYM_LIST_PREVIEW = 10

_HEDGE_WORDS: tuple[str, ...] = (
    "somewhat", "relatively", "rather", "quite", "arguably",
    "perhaps", "possibly", "apparently", "seemingly",
    "tend to", "tends to", "may", "might", "could",
)

# Common English words that match the ``[A-Z]{2,}`` acronym heuristic but
# are not actually acronyms.
_ACRONYM_EXCLUSIONS: frozenset[str] = frozenset({
    "THE", "AND", "FOR", "BUT", "NOT", "THIS", "THAT", "WITH",
    "FROM", "ARE", "WAS", "WERE", "HAS", "HAD", "HAVE", "WILL",
    "CAN", "ALL",
})


def compute_metrics(text: str, config: dict | None = None) -> dict:
    """Compute every metric and return a flat mapping of name -> value."""
    sentences = _split_sentences(text)
    words = text.split()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    word_count = len(words)
    sentence_count = len(sentences)
    syllable_count = sum(_count_syllables(w) for w in words)

    sent_lengths = [len(s.split()) for s in sentences]
    avg_sent_len = _mean(sent_lengths) if sent_lengths else 0
    max_sent_len = max(sent_lengths) if sent_lengths else 0
    long_sents = sum(1 for n in sent_lengths if n > _LONG_SENTENCE_WORDS)
    long_sent_pct = (long_sents / sentence_count * 100) if sentence_count else 0

    para_lengths = [len(p.split()) for p in paragraphs]
    avg_para_len = _mean(para_lengths) if para_lengths else 0

    fk_grade = _flesch_kincaid_grade(word_count, sentence_count, syllable_count)
    fk_ease = _flesch_reading_ease(word_count, sentence_count, syllable_count)

    passive_count = _count_passive(sentences)
    passive_pct = (passive_count / sentence_count * 100) if sentence_count else 0

    text_lower = text.lower()
    hedge_count = sum(text_lower.count(h) for h in _HEDGE_WORDS)
    hedge_density = (hedge_count / word_count * 1000) if word_count else 0

    acronyms = set(re.findall(r"\b[A-Z]{2,}\b", text)) - _ACRONYM_EXCLUSIONS

    word_limit = config.get("word_limit") if config else None

    metrics: dict = {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "paragraph_count": len(paragraphs),
        "avg_sentence_length": round(avg_sent_len, 1),
        "max_sentence_length": max_sent_len,
        "long_sentences_pct": round(long_sent_pct, 1),
        "avg_paragraph_length": round(avg_para_len, 1),
        "flesch_kincaid_grade": round(fk_grade, 1),
        "flesch_reading_ease": round(fk_ease, 1),
        "passive_voice_pct": round(passive_pct, 1),
        "hedge_density_per_1k": round(hedge_density, 1),
        "unique_acronyms": len(acronyms),
        "acronym_list": sorted(acronyms),
    }

    if word_limit:
        metrics["word_limit"] = word_limit
        metrics["over_word_limit"] = max(0, word_count - word_limit)

    return metrics


def format_metrics_text(m: dict) -> str:
    """Render the metric dict as a readable markdown table for reports."""
    word_cell = (
        f"OVER LIMIT by {m['over_word_limit']}"
        if m.get("over_word_limit", 0) > 0 else "OK"
    )
    avg_cell = (
        "Good" if m["avg_sentence_length"] <= _TARGET_AVG_SENTENCE_LEN
        else f"Long - aim for <{_TARGET_AVG_SENTENCE_LEN}"
    )
    max_cell = (
        "OK" if m["max_sentence_length"] <= _MAX_SENTENCE_WARN
        else "Very long - consider splitting"
    )
    long_cell = (
        "Good" if m["long_sentences_pct"] <= _LONG_SENTENCES_WARN_PCT
        else "High - simplify long sentences"
    )
    fk_cell = (
        f"Academic range ({_ACADEMIC_FK_LOW}-{_ACADEMIC_FK_HIGH})"
        if _ACADEMIC_FK_LOW <= m["flesch_kincaid_grade"] <= _ACADEMIC_FK_HIGH
        else f"Outside academic range ({_ACADEMIC_FK_LOW}-{_ACADEMIC_FK_HIGH})"
    )
    ease_cell = (
        "Difficult (academic)" if m["flesch_reading_ease"] < 30
        else "Moderate" if m["flesch_reading_ease"] < 60
        else "Easy"
    )
    passive_cell = (
        f"Good (<{_PASSIVE_VOICE_WARN_PCT}%)"
        if m["passive_voice_pct"] <= _PASSIVE_VOICE_WARN_PCT
        else "High - use more active voice"
    )
    hedge_cell = (
        "Low" if m["hedge_density_per_1k"] < _HEDGE_DENSITY_LOW
        else "Moderate" if m["hedge_density_per_1k"] < _HEDGE_DENSITY_MODERATE
        else "High - reduce hedging"
    )
    acronym_preview = ", ".join(
        m.get("acronym_list", [])[:_ACRONYM_LIST_PREVIEW]
    )
    acronym_ellipsis = "..." if m["unique_acronyms"] > _ACRONYM_LIST_PREVIEW else ""

    lines = [
        "## Text Metrics (Programmatic)",
        "",
        "| Metric | Value | Assessment |",
        "|--------|-------|------------|",
        f"| Words | {m['word_count']:,} | {word_cell} |",
        f"| Sentences | {m['sentence_count']:,} | |",
        f"| Paragraphs | {m['paragraph_count']} | |",
        f"| Avg sentence length | {m['avg_sentence_length']} words | {avg_cell} |",
        f"| Longest sentence | {m['max_sentence_length']} words | {max_cell} |",
        f"| Sentences >{_LONG_SENTENCE_WORDS} words | {m['long_sentences_pct']}% | {long_cell} |",
        f"| Flesch-Kincaid grade | {m['flesch_kincaid_grade']} | {fk_cell} |",
        f"| Flesch reading ease | {m['flesch_reading_ease']} | {ease_cell} |",
        f"| Passive voice | {m['passive_voice_pct']}% | {passive_cell} |",
        f"| Hedging density | {m['hedge_density_per_1k']}/1k words | {hedge_cell} |",
        f"| Unique acronyms | {m['unique_acronyms']} | {acronym_preview}{acronym_ellipsis} |",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Abbreviations whose trailing period should not split a sentence.
_ABBREV_RE = re.compile(
    r"(Dr|Mr|Mrs|Ms|Prof|Fig|Eq|et al|vs|etc|i\.e|e\.g)\."
)


def _split_sentences(text: str) -> list[str]:
    """Heuristic sentence splitter that respects common abbreviations."""
    text = _ABBREV_RE.sub(r"\1<DOT>", text)
    sentences = re.split(r"[.!?]+\s+", text)
    return [s.replace("<DOT>", ".").strip() for s in sentences if s.strip()]


def _count_syllables(word: str) -> int:
    """Approximate English syllable count using vowel-group counting."""
    word = word.lower().strip(".,;:!?\"'()-")
    if not word:
        return 0
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _mean(values: list[int | float]) -> float:
    return sum(values) / len(values) if values else 0


def _flesch_kincaid_grade(words: int, sentences: int, syllables: int) -> float:
    if sentences == 0 or words == 0:
        return 0
    return 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59


def _flesch_reading_ease(words: int, sentences: int, syllables: int) -> float:
    if sentences == 0 or words == 0:
        return 0
    return 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)


# Detects many common passive-voice constructions.  Kept pragmatic - this is
# a writing-quality signal, not a grammar checker.
_PASSIVE_RE = re.compile(
    r"\b(am|is|are|was|were|be|been|being)\s+"
    r"(\w+\s+)?"  # optional adverb
    r"(\w+ed|written|shown|known|seen|done|made|given|found|taken|"
    r"thought|said|used|called|considered|observed|reported|described|"
    r"measured|detected|identified|associated|performed|conducted|"
    r"obtained|achieved|demonstrated|proposed|suggested|examined|"
    r"analyzed|compared|evaluated|assessed|determined|established)\b",
    re.IGNORECASE,
)


def _count_passive(sentences: list[str]) -> int:
    """Count sentences that contain a passive-voice construction."""
    return sum(1 for s in sentences if _PASSIVE_RE.search(s))
