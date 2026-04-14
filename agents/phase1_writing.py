"""
Phase 1 - writing quality agents.

These seven agents evaluate prose style and structure independently of one
another and run early in the pipeline.  Each focuses on a specific rhetorical
or structural dimension drawn from an established framework:

* :class:`VSNCAgent`                - Winston's VSNC + 5 S's (abstract + intro).
* :class:`IntroductionAgent`        - Adelson / Kajiya / Freeman introduction test.
* :class:`SentenceArchitectureAgent` - Gopen & Swan structural principles.
* :class:`VoiceAndTenseAgent`       - Voice / tense conventions, energy.
* :class:`ConcistnessAgent`         - Strunk & White compression pass.
* :class:`ParagraphQualityAgent`    - Topic sentences, unity, reader-first flow.
* :class:`AcronymAgent`             - Acronym definition consistency audit.
"""

from __future__ import annotations

import textwrap
import time

from parser import get_sections_combined

from .base import AgentResult, BaseAgent, Context, _journal_context


class VSNCAgent(BaseAgent):
    """Winston's VSNC framework and 5 S's applied to abstract + introduction."""

    id = "vsnc"
    name = "VSNC Framework"
    description = "Vision - Steps - News - Contributions - 5 S's (Patrick Winston/MIT)"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are an expert scientific writing reviewer applying the VSNC framework
        (Patrick Winston, MIT) and the 5 S's paradigm.

        ## VSNC Framework (evaluate abstract AND introduction)
        - Vision: Is the big idea stated explicitly? What concrete advances does it enable?
          Is there an "empowerment promise" - does the reader know what they'll gain?
        - Steps: Are the concrete steps needed to execute the idea enumerated?
        - News: Are specific results listed with maximum specificity? (numbers, benchmarks)
        - Contributions: Are contributions stated using strong sanctioned verbs -
          *prove, demonstrate, implement, test, frame, survey, identify, present, show*?

        ## 5 S's (memorability)
        - Slogan: Is there a repeated phrase that anchors the paper in the reader's mind?
        - Symbol: Is there a repeated figure/visual that embodies the main idea?
        - Salient: Is there ONE standout idea? Too many competing ideas means none sticks.
        - Surprise: Is there something unexpected that hooks the reader?
        - Story: Is there a narrative arc - problem, journey, resolution?

        ## Inversion Heuristic (Winston)
        Put yourself in the reader's shoes. Does the abstract read cold?
        Does skimming topic sentences give the paper's full argument?

        ## Output format
        Start with a one-line SUMMARY and SEVERITY (ok / minor / moderate / major).
        Then grade each VSNC component and each S with PASS / WARN / FAIL.
        Quote the paper and suggest concrete text to add where missing.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_sections_combined(ctx.sections, ["abstract", "introduction"])
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{text}")
        return self._build_result(findings, t0)


class IntroductionAgent(BaseAgent):
    """Adelson formula + Kajiya 'dynamite intro' + Freeman tone review."""

    id = "intro"
    name = "Introduction Audit"
    description = "Adelson formula - Kajiya 'dynamite intro' - Freeman tone"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific writing reviewer applying the Freeman/Adelson/Kajiya framework.

        ## The Adelson Formula - grade each step A-F
        1. Problem stated clearly and early?
        2. Reader told WHY they should care? Significance made explicit?
        3. Prior work surveyed and critiqued - is it clear WHY prior work is unsatisfactory?
        4. New approach introduced in the intro (not buried in methods)?
        5. Is it clear why this work is better and in what specific ways?

        ## Kajiya Test ("dynamite intro") - can any reader quickly determine:
        - What the paper is about?
        - What problem it solves?
        - Why the problem is interesting?
        - What is genuinely new?
        - Why it's exciting?

        ## Tone (Freeman/Efros)
        - Is competing work described generously, from security not competition?
        - Are novelty claims scrupulously honest?
        - Is there a "future work" section at the end? (Flag: very weak ending.)

        Output: one-line SUMMARY + SEVERITY, then per-criterion findings with quotes
        and suggested rewrites.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_sections_combined(ctx.sections, ["abstract", "introduction"])
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{text}")
        return self._build_result(findings, t0)


class SentenceArchitectureAgent(BaseAgent):
    """Gopen & Swan structural principles: stress, topic, subject-verb, nominalization."""

    id = "sentences"
    name = "Sentence Architecture"
    description = "Gopen & Swan: stress positions, topic positions, subject-verb proximity"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific editor applying Gopen & Swan's structural principles.
        Readers have predictable expectations. Violating them causes comprehension
        failures that no simplification can fix.

        ## Four Structural Principles - cite specific sentences with fixes

        1. Stress Position: important new information belongs at the END of a sentence.
           Find sentences that end flatly and bury the key finding mid-sentence.
           Use colons/semicolons to create secondary stress positions in long sentences.

        2. Topic Position: sentence openings should (a) declare whose story this is
           and (b) link backward to what was just said. Old info first, new info second.
           Flag sentences that open with unanchored new material, breaking the thread.

        3. Subject-Verb Separation: find sentences where a long interruptive phrase
           separates subject from verb, overloading working memory.

        4. Action in Verbs, Not Nouns: fix nominalizations -
           "performed an analysis of" -> "analyzed"
           "provided an indication that" -> "indicated"
           "there was an inhibition of X" -> "X was inhibited"

        ## Structural-Conceptual Diagnosis
        Where structural problems likely reflect unclear thinking, say so.

        Output: one-line SUMMARY + SEVERITY, then the top 15 highest-impact fixes,
        quoting each sentence and providing the rewrite.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)


class VoiceAndTenseAgent(BaseAgent):
    """Voice / tense conventions and Strunk & White sentence-energy audit."""

    id = "voice"
    name = "Voice & Tense"
    description = "Active voice - Past for methods/results - Present for facts"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific editor specializing in voice and tense.

        ## Active vs. Passive Voice
        Flag passive constructions where active would be stronger.
        Estimate the active/passive ratio as an overall health metric.
        Note: some passives are conventional and acceptable.

        ## Tense Conventions - flag violations
        - Past tense (correct): your specific methods, results, what you did
        - Present tense (correct): established scientific facts, what figures show,
          your model as a standing contribution, universal truths
        - Future tense: use sparingly; only for describing paper structure

        Common errors:
        - Present tense for specific experiments: "We train the model" -> "We trained"
        - Past tense for established facts: "...was involved in memory" -> "...is involved"
        - Tense shifts within a paragraph

        ## Sentence Energy (Strunk & White)
        Flag: "it is worth noting that", "as mentioned previously", "in this paper we",
        sentences starting "There is/are", "It is", "It was".

        Output: one-line SUMMARY + SEVERITY, then top 15 issues with quote + rewrite.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)


class ConcistnessAgent(BaseAgent):
    """Strunk & White compression pass (wordiness, nominalizations, throat-clearing)."""

    id = "conciseness"
    name = "Conciseness Audit"
    description = "Omit needless words - Nominalizations - Throat-clearing"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific editor applying Strunk & White's core rule:
        "Omit needless words. Vigorous writing is concise."

        Find the top 20 opportunities for compression, categorized:

        Category 1 - Wordy phrases with crisp equivalents:
        "due to the fact that" -> "because"
        "in order to" -> "to"
        "it is important to note that" -> [delete]
        "despite the fact that" -> "although"
        "at this point in time" -> "now"
        "in the present study" -> [usually delete]

        Category 2 - Nominalizations:
        "perform an analysis" -> "analyze"
        "make a comparison" -> "compare"
        "conduct an investigation" -> "investigate"

        Category 3 - Redundancy:
        "end result", "final conclusion", "completely eliminate", "past history"
        Sentences that restate the previous sentence.

        Category 4 - Throat-clearing openers:
        "In this paper, we...", "The purpose of this study is to..."

        Category 5 - Hedging clutter:
        Excessive "somewhat", "rather", "quite", "in some sense"

        For each: quote original, compressed version, word savings.
        Give total estimated word reduction at the end.

        Output: one-line SUMMARY + SEVERITY, then findings.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        user = f"PAPER:\n\n{ctx.paper_text}"
        user += _journal_context(ctx.config)
        findings = self._call(ctx, self.SYSTEM, user)
        return self._build_result(findings, t0)


class ParagraphQualityAgent(BaseAgent):
    """Topic sentences, unity, and reader-first (Knuth) paragraph assessment."""

    id = "paragraphs"
    name = "Paragraph Quality"
    description = "Topic sentences - Unity - Flow - Reader-first (Knuth)"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a writing editor evaluating paragraph-level quality in a scientific paper.

        For each paragraph check:
        - Topic sentence: does the first sentence declare the paragraph's subject?
          Can a skimmer read only topic sentences and follow the argument?
        - Unity: does every sentence serve the topic sentence? Flag strays.
        - Logical flow: does the argument build, or just list facts?
        - Reader-first (Knuth): at each moment, does each sentence answer the reader's
          implicit next question? What does the reader know so far; what do they expect?
        - Completeness: are claims supported? Flag unsupported assertions.
        - Conciseness: flag redundant sentences that repeat what was just said.

        Output format:
        Rate each section's paragraphs:
          PASS Strong | WARN Needs work (specify) | FAIL Needs rewrite (specify)

        Identify the 5 weakest paragraphs with specific actionable revision guidance.
        Note structural issues: paragraphs to split, merge, or reorder.

        Final check: if a hurried reader skims only first sentences, do they get the story?

        Output: one-line SUMMARY + SEVERITY, then findings.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)


class AcronymAgent(BaseAgent):
    """Acronym audit: definitions before first use, redundancy, inconsistency."""

    id = "acronyms"
    name = "Acronym Audit"
    description = "Every acronym defined before first use - Consistency after definition"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a meticulous scientific editor auditing acronym and abbreviation usage.

        1. List ALL acronyms/abbreviations (2+ capital letters, or abbreviated technical terms).
        2. For each, find its FIRST appearance.
        3. Verify it is spelled out at or before that first appearance.
        4. After definition, verify the short form is used consistently.

        Present a table:
        | Acronym | Full Form | First Appears | Defined? | Issue |

        Then classify:
        - Undefined acronyms: used without definition
        - Redundant definitions: defined but rarely/never used afterward
        - Double definitions: defined more than once
        - Inconsistent usage: long and short form mixed after first definition
        - Possibly standard (may not need definition for the audience)

        Special cases:
        - Acronyms in titles/headings: still need definition in text
        - Acronyms in captions: either defined in caption or already in text
        - Abstract and body often treated independently

        Output: one-line SUMMARY + SEVERITY, then the table and classified lists.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)
