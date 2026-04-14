"""
Phase 0 and Phase 1 - content and methodology agents.

These agents evaluate substantive aspects of the manuscript (figures, methods,
statistics, reproducibility) rather than prose-level writing quality.

* :class:`TextMetricsAgent`          - Phase 0 programmatic metrics (no LLM).
* :class:`FiguresTablesAgent`        - Figure / table coverage + Tufte vision pass.
* :class:`ReproducibilityAgent`      - Requires ``--code-file``; cross-checks code.
* :class:`StatisticalReviewAgent`    - Biostatistics review of methods + results.
* :class:`MethodsCompletenessAgent`  - Reproducibility-by-description checklist.
"""

from __future__ import annotations

import textwrap
import time

from parser import get_section, get_sections_combined

from .base import AgentResult, BaseAgent, Context
from .constants import (
    FK_GRADE_DIFFICULT,
    LONG_SENTENCES_HIGH_PCT,
    LONG_FORM_MAX_TOKENS,
    PASSIVE_VOICE_HIGH_PCT,
    VISION_MAX_TOKENS,
)


class TextMetricsAgent(BaseAgent):
    """Programmatic readability + sentence stats (runs instantly, no LLM)."""

    id = "metrics"
    name = "Text Metrics"
    description = "Instant readability, passive voice, sentence stats (no LLM)"
    priority = 0

    def run(self, ctx: Context) -> AgentResult:
        from metrics import compute_metrics, format_metrics_text
        t0 = time.time()
        m = compute_metrics(ctx.paper_text, ctx.config)
        findings = format_metrics_text(m)

        issues: list[str] = []
        if m.get("passive_voice_pct", 0) > PASSIVE_VOICE_HIGH_PCT:
            issues.append("high passive voice")
        if m.get("flesch_kincaid_grade", 0) > FK_GRADE_DIFFICULT:
            issues.append("very difficult readability")
        if m.get("long_sentences_pct", 0) > LONG_SENTENCES_HIGH_PCT:
            issues.append("many long sentences")
        if m.get("over_word_limit", 0) > 0:
            issues.append(f"over word limit by {m['over_word_limit']}")

        if len(issues) >= 2:
            severity = "moderate"
        elif issues:
            severity = "minor"
        else:
            severity = "ok"

        summary = (
            f"{m['word_count']:,} words | "
            f"FK grade {m['flesch_kincaid_grade']} | "
            f"{m['passive_voice_pct']}% passive | "
            f"avg {m['avg_sentence_length']} words/sent"
        )

        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=summary,
            findings=findings,
            severity=severity,
            references_found=[],
            elapsed=time.time() - t0,
        )


class FiguresTablesAgent(BaseAgent):
    """Figure / table / caption audit, with an optional per-figure vision pass."""

    id = "figures_tables"
    name = "Figures, Tables & Captions"
    description = "Coverage - Caption completeness - Figure necessity - Tufte principles - Multimodal"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific editor auditing figures, tables, and their captions.

        ## Figure Coverage & Necessity
        - Does the paper have an appropriate number of figures for its claims?
        - Is there a clear 'story figure' or schematic that communicates the main idea
          visually? (Freeman: "most readers will skim; figures and captions tell the story")
        - Are there figures that are redundant with others or with text?
        - Are there results or methods that are described only in text but would benefit
          from a figure or table?
        - Are all figures referenced in the main text in logical order?

        ## Caption Quality (evaluate every figure and table caption)
        For each caption, check:
        - Does the caption TITLE (first sentence) state the main finding/point of the figure,
          not just describe what it shows? (A good caption title: "Model X outperforms
          baselines across all conditions." Bad: "Results of the comparison experiment.")
        - Are all panels labeled and described?
        - Are error bars, shading, and uncertainty quantification explained?
        - Are sample sizes reported in or near the caption?
        - Are statistical test results (p-values, effect sizes) explained?
        - Is the caption self-contained? Can the figure be understood without reading
          the main text?
        - Does the caption direct the reader to what to notice?
          (Freeman: "the caption should tell the reader what to notice about the figure")

        ## Table Audit
        - Are tables properly titled with a finding-oriented title?
        - Are units specified in all columns?
        - Are significance markers explained in footnotes?
        - Is the table readable (not overly wide, columns clear)?

        ## Cross-reference Audit
        - List any figures/tables mentioned in text but apparently not present.
        - List any figures/tables present but never cited in the text.

        Output: one-line SUMMARY + SEVERITY, then per-figure/table findings.
    """).strip()

    VISION_SYSTEM = textwrap.dedent("""
        You are an expert scientific figure reviewer applying Tufte's principles
        of data visualization. Evaluate this figure from a scientific manuscript.

        ## Tufte's Principles - evaluate each:
        1. **Data-ink ratio**: Is ink used efficiently? Flag decorative elements
           that don't convey data (unnecessary gridlines, 3D effects, borders, shadows).
        2. **Chartjunk**: Flag unnecessary visual elements - heavy grids, moire patterns,
           gratuitous color, decorative borders, unnecessary 3D effects.
        3. **Lie factor**: Does the visual representation accurately reflect the data?
           Are axes scaled appropriately? Do bar charts start at zero?
        4. **Labeling**: Are axes clearly labeled with units? Is direct labeling used
           instead of legends where possible? Is text horizontal (not rotated)?
        5. **Color usage**: Do colors encode data or are they decorative? Is the palette
           colorblind-friendly? Are colors consistent across panels?
        6. **Clarity**: Can the reader understand the main message within seconds?
           Is there a clear visual hierarchy?

        ## Additional checks:
        - Are all panels labeled (a, b, c...)?
        - Is resolution sufficient?
        - Are font sizes readable?
        - Would a different chart type better convey this data?
        - Flag pie charts (poor for comparison) and suggest alternatives.

        Output: one-line SUMMARY, then per-principle evaluation with specific fixes.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")

        # Optional multimodal pass: one vision call per figure when supported.
        if ctx.figures and ctx.provider.supports_vision():
            figure_findings: list[str] = []
            for i, fig_path in enumerate(ctx.figures, 1):
                try:
                    fig_review = ctx.provider.call_with_images(
                        self.VISION_SYSTEM,
                        f"Evaluate Figure {i} from the manuscript. File: {fig_path}",
                        images=[fig_path],
                        max_tokens=VISION_MAX_TOKENS // 2,
                    )
                    figure_findings.append(
                        f"\n### Figure {i} - Visual Analysis (Tufte)\n{fig_review}"
                    )
                except Exception as e:
                    figure_findings.append(
                        f"\n### Figure {i}\n*Visual analysis failed: {e}*"
                    )
            if figure_findings:
                findings += "\n\n---\n## Visual Figure Analysis (Multimodal)\n"
                findings += "\n".join(figure_findings)

        return self._build_result(findings, t0)


class ReproducibilityAgent(BaseAgent):
    """Cross-checks paper claims against provided code / data (needs --code-file)."""

    id = "reproducibility"
    name = "Reproducibility Check"
    description = "Results match code/data - Methods accurately described"
    priority = 1
    needs_code = True

    SYSTEM = textwrap.dedent("""
        You are a reproducibility reviewer. Verify that the paper accurately represents
        the provided code and data.

        ## Verification Checklist

        Quantitative Results
        - Every number (accuracy, AUC, p-values, effect sizes, sample sizes, runtimes)
          should appear in or be derivable from the code/data.
        - Flag numbers that cannot be traced to a specific code path or data file.
        - Flag discrepancies between numbers in different sections.

        Methods vs. Code
        - Does the methods section accurately describe what the code does?
        - Flag: steps described in paper but absent from code.
        - Flag: steps in code not mentioned in paper.
        - Flag: parameters (learning rate, threshold, window size, etc.) described
          differently than implemented.
        - Flag: statistical tests described differently than implemented.

        Data Descriptions
        - Do sample sizes, demographics, inclusion/exclusion criteria match the data?
        - Are preprocessing steps fully described?

        Output format:
        PASS Verified: [claim] - confirmed by [code location/data file]
        WARN Unverifiable: [claim] - cannot confirm without [missing element]
        FAIL Discrepancy: paper says [X], code/data shows [Y]
        NOTE Risk: plausible but not fully checkable

        Output: one-line SUMMARY + SEVERITY, then the checklist.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.code_text.strip():
            return AgentResult(
                agent_id=self.id,
                agent_name=self.name,
                summary="Skipped - no code/data provided",
                findings="No code or data was provided. Pass --code-file to enable this agent.",
                severity="ok",
                references_found=[],
                elapsed=0.0,
            )
        text = get_sections_combined(ctx.sections, ["methods", "results"])
        user = f"PAPER:\n\n{text}\n\nCODE / DATA:\n\n{ctx.code_text}"
        findings = self._call(ctx, self.SYSTEM, user)
        return self._build_result(findings, t0)


class StatisticalReviewAgent(BaseAgent):
    """Biostatistics review: test selection, sample size, multiple comparisons, effect sizes."""

    id = "statistics"
    name = "Statistical Methods Review"
    description = "Test selection - Sample sizes - Multiple comparisons - Effect sizes - Reporting standards"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are an expert biostatistician reviewing the statistical methods and
        reporting in a scientific manuscript.

        ## Statistical Test Selection
        - Are the chosen statistical tests appropriate for the data type and design?
        - Are parametric tests used when assumptions (normality, homoscedasticity) are met?
        - Are non-parametric alternatives used when assumptions are violated?
        - For categorical outcomes: chi-square, Fisher's exact, logistic regression?
        - For survival data: Kaplan-Meier, Cox regression?
        - For repeated measures: mixed models, GEE?

        ## Sample Size & Power
        - Is the sample size justified (power analysis, prior studies)?
        - Are subgroup analyses adequately powered?
        - Flag underpowered secondary analyses presented as primary findings.

        ## Multiple Comparisons
        - Are multiple comparisons corrected (Bonferroni, Holm, FDR/BH)?
        - How many statistical tests are reported? Flag if >10 without correction.
        - Are exploratory vs confirmatory analyses clearly distinguished?

        ## Effect Sizes & Precision
        - Are effect sizes reported alongside p-values (Cohen's d, odds ratios, etc.)?
        - Are confidence intervals provided?
        - Flag "statistically significant" findings with trivially small effect sizes.
        - Flag reliance on p-values alone without effect size context.

        ## Reporting Quality
        - Are test statistics reported (t, F, chi-square values)?
        - Are degrees of freedom reported?
        - Are exact p-values given (not just p<0.05)?
        - For Bayesian analyses: are priors specified and justified?

        ## Red Flags (p-hacking indicators)
        - Many tests with selective reporting of significant results
        - Unusual p-value distributions (cluster just below 0.05)
        - Post-hoc hypotheses presented as a priori
        - Outcome switching: primary outcome in methods differs from results
        - "Marginally significant" (p=0.05-0.10) treated as meaningful

        ## Reporting Standards
        - Clinical trials: CONSORT compliance
        - Observational: STROBE compliance
        - Diagnostic: STARD compliance
        - Prediction models: TRIPOD compliance
        - Systematic reviews: PRISMA compliance

        For each issue found, provide:
        - The specific claim or analysis
        - What is wrong or missing
        - What should be done instead
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_sections_combined(ctx.sections, ["methods", "results"])
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{text}")
        return self._build_result(findings, t0)


class MethodsCompletenessAgent(BaseAgent):
    """Checks that the Methods section contains enough detail to reproduce the study."""

    id = "methods_completeness"
    name = "Methods Completeness"
    description = "Reproducible methods - Parameters - Design - Protocol details"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific reviewer evaluating whether the Methods section is
        complete enough for an independent researcher to reproduce the study.

        ## Study Design
        - Is the study design clearly stated (RCT, cohort, cross-sectional, etc.)?
        - Is the rationale for the chosen design explained?
        - For experimental studies: randomization, blinding, controls described?

        ## Participants / Data
        - Sample size: is it stated and justified?
        - Inclusion/exclusion criteria: are they specific and complete?
        - Recruitment: how and where were participants recruited?
        - Demographics: are key characteristics described?
        - For datasets: source, version, preprocessing steps, train/test splits?

        ## Procedures & Protocols
        - Are all procedures described in enough detail to replicate?
        - Are instruments, questionnaires, or tools named with versions?
        - Are software tools named with versions?
        - Are hyperparameters and configuration settings specified?
        - Are data collection procedures described step by step?

        ## Variables & Measurements
        - Are outcome variables clearly defined?
        - Are predictor/exposure variables clearly defined?
        - Are measurement methods described (instruments, scales, units)?
        - Are validity and reliability of measures addressed?

        ## Statistical Analysis Plan
        - Is the primary analysis specified?
        - Are secondary/exploratory analyses distinguished from primary?
        - Are missing data handling strategies described?
        - Are significance thresholds stated?
        - Is software for statistical analysis named?

        ## Ethics & Approvals
        - IRB/ethics committee approval mentioned?
        - Informed consent described?
        - Data privacy/anonymization addressed?
        - Clinical trial registration number (if applicable)?

        ## Completeness Checklist
        For each item, mark:
        PASS Adequately described
        WARN Partially described (specify what's missing)
        FAIL Not described (but should be for this study type)
        N/A Not applicable
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_section(ctx.sections, "methods")
        findings = self._call(ctx, self.SYSTEM, f"METHODS SECTION:\n\n{text}")
        return self._build_result(findings, t0)
