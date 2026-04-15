"""
Named constants used by the PAT agent package.

Collecting these values in one place makes calibration points (token budgets,
preview window sizes, severity bands, search windows) easy to audit and tune.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Severity bands
# ---------------------------------------------------------------------------
# Map a qualitative severity label into a 0-1 quality score.  The score is
# what feeds the weighted Submission Readiness Score on the report.
SEVERITY_TO_SCORE: dict[str, float] = {
    "ok": 0.95,
    "minor": 0.75,
    "moderate": 0.45,
    "major": 0.15,
}


# ---------------------------------------------------------------------------
# Token budgets (max_tokens passed to the LLM provider)
# ---------------------------------------------------------------------------
STANDARD_MAX_TOKENS = 2048        # Default agent reply budget.
LONG_FORM_MAX_TOKENS = 3072       # Deeper narrative agents (guidelines, etc).
VISION_MAX_TOKENS = 2048          # Per-figure vision responses.
LARGE_VISION_MAX_TOKENS = 4096    # Cross-figure / batched vision agents.
SHORT_CLASSIFY_MAX_TOKENS = 256   # Quick classification calls.
EXTRACTION_MAX_TOKENS = 1024      # Claim / reference extraction passes.
CHECKLIST_MAX_TOKENS = 1024
SYNTHESIS_MAX_TOKENS = 2048       # Orchestrator synthesis.
REFERENCE_ASSESS_MAX_TOKENS = 200
REFERENCE_EXTRACT_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# Preview / truncation lengths (characters)
# ---------------------------------------------------------------------------
ABSTRACT_PREVIEW_CHARS = 300          # Abstract snippet in search results.
PARAGRAPH_QUOTE_PREVIEW_CHARS = 200   # Quoted paragraph in missing-refs report.
DOMAIN_DETECT_PREVIEW_CHARS = 5000    # Chars inspected for heuristic domain detection.
POSITIONING_ABSTRACT_PREVIEW_CHARS = 200
PRIOR_FINDINGS_FALLBACK_CHARS = 800   # Fallback when an agent lacks top_issues.
FIGURE_PRIOR_FINDINGS_CHARS = 3000    # Truncation for per-agent reports fed to
                                      # the cross-figure synthesis agent.


# ---------------------------------------------------------------------------
# Search / external API
# ---------------------------------------------------------------------------
BIORXIV_SEARCH_WINDOW_DAYS = 180   # Rolling window bioRxiv checks for matches.
PUBMED_TIMEOUT_SECONDS = 10
PUBMED_FETCH_TIMEOUT_SECONDS = 15
BIORXIV_TIMEOUT_SECONDS = 15
SCHOLAR_RATE_LIMIT_SECONDS = 0.5   # Courtesy delay between external searches.
POSITIONING_RATE_LIMIT_SECONDS = 0.3
REFERENCE_VERIFY_RATE_LIMIT_SECONDS = 0.3


# ---------------------------------------------------------------------------
# Heuristic thresholds
# ---------------------------------------------------------------------------
PASSIVE_VOICE_HIGH_PCT = 30          # Above this, TextMetrics flags prose style.
FK_GRADE_DIFFICULT = 18              # Flesch-Kincaid grade deemed very difficult.
LONG_SENTENCES_HIGH_PCT = 25         # Share of sentences considered long.

MISSING_REFS_MAJOR_THRESHOLD = 8     # Uncited claims that promote severity to major.
MISSING_REFS_MODERATE_THRESHOLD = 3  # Uncited claims promoting severity to moderate.

REF_QUALITY_MAJOR_THRESHOLD = 5      # Reference issues that promote severity to major.
REF_QUALITY_MODERATE_THRESHOLD = 2

GUIDELINE_FAIL_MAJOR_THRESHOLD = 5   # Reporting guideline failures -> major.
GUIDELINE_FAIL_MODERATE_THRESHOLD = 2
GUIDELINE_PARTIAL_MODERATE_THRESHOLD = 5
GUIDELINE_PARTIAL_MINOR_THRESHOLD = 2

DOMAIN_KEYWORD_MIN_HITS = 3          # Hits required to trigger a domain prompt.

SHORT_BLOCK_MIN_CHARS = 60           # Shorter blocks are treated as headers, not paragraphs.

POSITIONING_MAX_CLAIMS = 8           # Cap on novelty claims searched per run.


# ---------------------------------------------------------------------------
# Miscellaneous
# ---------------------------------------------------------------------------
TOP_ISSUES_MAX = 5                   # How many TOP_ISSUES lines we parse.
REPORT_TITLE_TRUNCATE_CHARS = 60     # Used for dedupe keys when merging refs.
AUTHORS_PREVIEW_CHARS = 60           # Max length of author list preview.
