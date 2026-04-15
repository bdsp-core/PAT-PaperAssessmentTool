"""
Figure-oriented vision agents.

These agents require both (a) figure images supplied via ``--figures`` or auto
extracted from a PDF, and (b) a vision-capable provider model.  When either is
unavailable they return a neutral skip result (see :func:`_skip_result`).

* Per-figure (priority 1): :class:`FigureStoryAgent`, :class:`FigureCompositionAgent`,
  :class:`FigureFormatAgent`, :class:`FigureCaptionAgent`, :class:`FigureStatisticsAgent`.
* Whole-figure-set (priority 2): :class:`FigureColorAgent`, :class:`FigureTypographyAgent`.
* Cross-figure synthesis (priority 3): :class:`FigureCrossConsistencyAgent`.
"""

from __future__ import annotations

import textwrap
import time

from .base import AgentResult, BaseAgent, Context, _get_image_metadata, _skip_result
from .constants import (
    FIGURE_PRIOR_FINDINGS_CHARS,
    LARGE_VISION_MAX_TOKENS,
    VISION_MAX_TOKENS,
)


class FigureStoryAgent(BaseAgent):
    """Narrative clarity per figure: single-claim, self-sufficient, chart type fit."""

    id = "fig_story"
    name = "Figure Story & Purpose"
    description = "Narrative clarity - Chart type match - Noise elements - Redundancy"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in narrative clarity.

        Your job: determine whether this figure earns its place in a scientific manuscript.

        For each figure, evaluate:

        1. MAIN CLAIM - Can you state the figure's single main claim in one sentence?
           If not, the figure isn't ready. State what you think the claim is, or flag
           that it's unclear.

        2. SELF-SUFFICIENCY - Can the story be read from the visualization alone,
           before reading any text? (Tufte's "data-ink" test.) Flag elements where the
           reader would be confused without the caption.

        3. CHART TYPE MATCH - Is this figure explaining a process, comparing groups,
           showing change over time, or establishing a relationship? Does the chosen
           chart type match that purpose? Flag mismatches and suggest alternatives.

        4. NOISE ELEMENTS - Identify any visual elements that don't serve the main
           claim. These are candidates for removal.

        5. REDUNDANCY - Note if this figure appears to duplicate information that
           could be shown in a table or is likely shown in another figure. Flag as
           a candidate for the supplement.

        Output format:
        MAIN CLAIM: [one sentence, or "UNCLEAR - <explanation>"]
        SELF-SUFFICIENT: [Yes / No] - [explanation]
        CHART TYPE: [Appropriate / Mismatch] - [explanation; if mismatch, suggest alternatives]
        NOISE ELEMENTS: [bulleted list, or "None identified"]
        REDUNDANCY RISK: [None / Low / Medium / High] - [explanation]
        ISSUES:
          - [BLOCKING] <description>
          - [ADVISORY] <description>
        SUGGESTED FIXES:
          - <description>
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        all_findings: list[str] = []
        for i, fig_path in enumerate(ctx.figures, 1):
            try:
                review = self._call_with_images(
                    ctx, self.SYSTEM,
                    f"Evaluate Figure {i} ({fig_path.split('/')[-1]})",
                    images=[fig_path], max_tokens=VISION_MAX_TOKENS,
                )
                all_findings.append(f"### Figure {i}\n\n{review}")
            except Exception as e:
                all_findings.append(f"### Figure {i}\n*Analysis failed: {e}*")
        return self._build_result("\n\n".join(all_findings), t0)


class FigureCompositionAgent(BaseAgent):
    """Data-ink ratio, chartjunk, reading flow: Tufte compliance, per figure."""

    id = "fig_composition"
    name = "Figure Composition & Layout"
    description = "Data-ink ratio - Chartjunk - Reading flow - Tufte principles"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in visual composition,
        applying Edward Tufte's principles of analytical design.

        Core Tufte principles to enforce:
        - Maximize the data-ink ratio: every mark should encode information
        - Erase non-data ink; erase redundant data ink
        - Above all else, show the data

        For each figure, evaluate:

        1. DATA-INK RATIO - Flag:
           - Unnecessary borders or bounding boxes
           - Background fills or shading that encode no data
           - 3D effects on 2D data
           - Gridlines heavier than the data they reference
           - Drop shadows, glows, or other decorative effects
           - Redundant axis labels or tick marks

        2. READING FLOW - Natural flow is left-right, top-bottom, or clockwise.
           Flag anything that forces the reader to backtrack or jump.

        3. CHARTJUNK - Flag decorative elements, redundant legends, unnecessary
           graphical ornamentation that adds no information.

        4. WHITE SPACE - Flag overcrowded panels. White space aids comprehension.
           Also flag excessive white space that wastes figure real estate.

        5. AXIS CONVENTIONS - Axes should begin at zero unless there is a documented
           scientific reason not to. Flag violations.

        6. ERROR BARS - If present: are they defined (SD, SEM, 95% CI)?
           Flag undefined error bars as BLOCKING.

        7. PANEL LABELS - In multi-panel figures, check that A/B/C labels are:
           - Present on every panel
           - Consistently positioned
           - Consistently styled (bold, same size)

        8. ASPECT RATIO - Is the aspect ratio appropriate for the data?

        9. ANNOTATION BOXES - Flag overlapping annotations or text boxes that
           compete visually with data.

        10. LEGEND VS. DIRECT LABELING - For line charts with 2-5 series:
            check whether a legend box could be eliminated by direct labeling.

        Output format:
        DATA-INK RATIO:   [Good / Needs work] - [specifics]
        READING FLOW:     [Natural / Problematic] - [specifics]
        CHARTJUNK:        [bulleted list, or "None identified"]
        WHITE SPACE:      [Adequate / Crowded / Excessive]
        AXES:             [OK / Issues] - [specifics]
        ERROR BARS:       [Defined / Undefined / Not present]
        PANEL LABELS:     [Consistent / Inconsistent / Not applicable]
        ASPECT RATIO:     [Appropriate / Needs adjustment] - [specifics]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        all_findings: list[str] = []
        for i, fig_path in enumerate(ctx.figures, 1):
            try:
                review = self._call_with_images(
                    ctx, self.SYSTEM,
                    f"Evaluate Figure {i} ({fig_path.split('/')[-1]}) for composition and layout.",
                    images=[fig_path], max_tokens=VISION_MAX_TOKENS,
                )
                all_findings.append(f"### Figure {i}\n\n{review}")
            except Exception as e:
                all_findings.append(f"### Figure {i}\n*Analysis failed: {e}*")
        return self._build_result("\n\n".join(all_findings), t0)


class FigureFormatAgent(BaseAgent):
    """Technical format: DPI, resolution, dimensions, color mode, file size."""

    id = "fig_format"
    name = "Figure Technical Format"
    description = "DPI - Resolution - File format - Dimensions - Color mode"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in technical
        publication requirements.

        The orchestration layer will provide a FILE METADATA block before the figure image.
        Use these values for DPI calculations - do not attempt to infer DPI from the image.

        For each figure, evaluate:

        1. DIMENSIONS & COLUMN FIT
           - Calculate DPI at single-column width (3.25 in) and double-column width (6.875 in)
           - Recommend which column width is most appropriate
           - Flag if aspect ratio is unsuitable for the recommended width

        2. RESOLUTION
           - At the recommended column width, is DPI >= 300 (halftone) or >= 600 (line art)?
           - Classify the figure: halftone (photographs, gradients) vs. line art (graphs, diagrams)
           - Flag resolution below threshold as BLOCKING

        3. FILE FORMAT
           - Is the current format acceptable for submission?
           - If not, specify the required conversion

        4. FILE SIZE
           - Flag any figure exceeding 10 MB as BLOCKING

        5. TEXT RENDERING
           - Does text appear vector (smooth) or rasterized (pixel edges)?

        6. COLOR MODE
           - Note if CMYK conversion could cause visible shifts

        Output format:
        DIMENSIONS:     [W x H px] - recommended column - [DPI] at that width
        RESOLUTION:     [Adequate / Below threshold] - [figure type, actual DPI]
        FORMAT:         [Acceptable / Needs conversion] - [details]
        FILE SIZE:      [OK / Exceeds limit] - [size in MB]
        TEXT RENDERING: [Vector / Rasterized / Mixed]
        COLOR MODE:     [OK / Potential shift] - [specifics]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        all_findings: list[str] = []
        for i, fig_path in enumerate(ctx.figures, 1):
            try:
                metadata = _get_image_metadata(fig_path)
                review = self._call_with_images(
                    ctx, self.SYSTEM,
                    f"{metadata}\nEvaluate Figure {i} ({fig_path.split('/')[-1]}) "
                    f"for technical publication compliance.",
                    images=[fig_path], max_tokens=VISION_MAX_TOKENS,
                )
                all_findings.append(f"### Figure {i}\n\n{review}")
            except Exception as e:
                all_findings.append(f"### Figure {i}\n*Analysis failed: {e}*")
        return self._build_result("\n\n".join(all_findings), t0)


class FigureCaptionAgent(BaseAgent):
    """Caption completeness: title, panel coverage, error bars, n, statistics."""

    id = "fig_caption"
    name = "Figure Caption & Legend"
    description = "Title - Panel descriptions - Error bars - Sample size - Statistical detail"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in captions and legends.
        Captions are half the figure - a technically perfect image with an incomplete
        caption will be rejected or require major revision.

        You will receive both the figure image and its draft caption/legend text.
        If no caption is provided, flag every item below as BLOCKING.

        For each figure, evaluate:

        1. FIGURE TITLE - Is there a concise, informative bold title as the first
           sentence of the legend? Flag missing or non-bold titles.

        2. PANEL DESCRIPTIONS - For multi-panel figures, does the legend describe
           each panel in order (A, B, C...)? Flag missing or out-of-order descriptions.

        3. ERROR BARS - Are error bars defined exactly once in the legend?
           Flag missing definitions - BLOCKING.

        4. SAMPLE SIZE - Is n= stated for each group or condition? Flag missing n.

        5. STATISTICAL REPORTING - Does the legend name the statistical test,
           test statistic, degrees of freedom, and exact p-values?

        6. ABBREVIATIONS - Are all abbreviations used in the figure defined?

        7. COLOR / SYMBOL KEY - If colors or symbols encode groups, are they
           explained in the legend?

        8. COMPLETENESS TEST - Could a reader fully understand this figure using
           only the figure and its legend, without reading the main text?

        Output format:
        TITLE:              [Present & bold / Missing / Not bold]
        PANEL DESCRIPTIONS: [Complete / Missing panels: <list> / Not applicable]
        ERROR BARS:         [Defined / Undefined / Not present]
        SAMPLE SIZE:        [Stated / Missing]
        STATISTICAL DETAIL: [Complete / Partial / Missing] - [what is absent]
        ABBREVIATIONS:      [All defined / Undefined: <list>]
        SELF-CONTAINED:     [Yes / No] - [what is missing]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        all_findings: list[str] = []
        for i, fig_path in enumerate(ctx.figures, 1):
            try:
                review = self._call_with_images(
                    ctx, self.SYSTEM,
                    f"Evaluate caption and legend completeness for Figure {i} "
                    f"({fig_path.split('/')[-1]}).",
                    images=[fig_path], max_tokens=VISION_MAX_TOKENS,
                )
                all_findings.append(f"### Figure {i}\n\n{review}")
            except Exception as e:
                all_findings.append(f"### Figure {i}\n*Analysis failed: {e}*")
        return self._build_result("\n\n".join(all_findings), t0)


class FigureStatisticsAgent(BaseAgent):
    """Statistical integrity of each figure: plot type, truncation, overplotting."""

    id = "fig_statistics"
    name = "Figure Statistical Integrity"
    description = "Plot type vs data - Sample visibility - Axis truncation - Overplotting"
    priority = 1

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in statistical
        visualization integrity.

        For each figure, evaluate:

        1. PLOT TYPE VS. DATA DISTRIBUTION
           - Bar graphs with error bars hide the underlying distribution. For n < 20
             or non-normal data, recommend dot plots, box plots, violin plots, or
             beeswarm plots instead.
           - Flag bar graphs where the individual data points are not also shown.
           - Flag pie charts for data with more than 3 categories.

        2. SAMPLE SIZE VISIBILITY
           - Is n visible or inferable from the figure itself?
           - For small n (< 10), individual data points should always be shown.

        3. COMPARISON VALIDITY
           - Are group comparisons shown with appropriate context?
           - Flag unpaired visualization of paired data.

        4. AXIS TRUNCATION
           - Is the y-axis truncated in a way that visually exaggerates differences?
           - Flag truncated axes on bar charts especially.

        5. OVERPLOTTING
           - In scatter plots or dot plots with many points, is overplotting obscuring
             the data density? Suggest jitter, transparency, or density plots.

        6. MULTIPLE COMPARISONS
           - If many pairwise comparisons are annotated, flag whether multiple
             comparison correction is likely needed.

        7. TIME SERIES INTEGRITY
           - For longitudinal data, are missing timepoints or uneven intervals
             clearly shown? Flag misleading interpolation across gaps.

        Output format:
        PLOT TYPE:          [Appropriate / At risk] - [specifics]
        SAMPLE SIZE:        [Visible / Not visible] - [details]
        COMPARISON:         [Valid / Concern] - [specifics]
        AXIS TRUNCATION:    [None / Present] - [describe]
        OVERPLOTTING:       [None / Present] - [specifics]
        TIME SERIES:        [OK / Concern / Not applicable]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        all_findings: list[str] = []
        for i, fig_path in enumerate(ctx.figures, 1):
            try:
                review = self._call_with_images(
                    ctx, self.SYSTEM,
                    f"Evaluate statistical visualization integrity for Figure {i} "
                    f"({fig_path.split('/')[-1]}).",
                    images=[fig_path], max_tokens=VISION_MAX_TOKENS,
                )
                all_findings.append(f"### Figure {i}\n\n{review}")
            except Exception as e:
                all_findings.append(f"### Figure {i}\n*Analysis failed: {e}*")
        return self._build_result("\n\n".join(all_findings), t0)


class FigureColorAgent(BaseAgent):
    """Colorblind safety, palette quality, and cross-figure colour consistency."""

    id = "fig_color"
    name = "Figure Color & Accessibility"
    description = "Colorblind safety - Palette quality - Cross-figure color consistency"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in color usage and
        accessibility. You receive ALL figures from the manuscript simultaneously
        so you can check both per-figure quality and cross-figure consistency.

        For EACH figure individually, evaluate:

        1. COLOR PURPOSE - Does each distinct color encode a distinct variable or group?
           Flag purely decorative color use.

        2. COLOR ECONOMY - Good figures use 1-2 accent colors; the rest should be
           neutral or grayscale. Flag excessive color variety (>4 distinct hues).

        3. COLORBLINDNESS SAFETY - Would critical distinctions disappear under:
           - Deuteranopia (red-green, most common)
           - Protanopia (red-green variant)
           - Achromatopsia (full grayscale simulation)
           Flag any red/green pair used as the primary categorical contrast.
           Recommended safe palettes: Okabe-Ito, ColorBrewer, viridis/magma/plasma/cividis.

        4. PALETTE QUALITY - Are colors from a principled, perceptually uniform palette?

        Then evaluate the FULL FIGURE SET for cross-figure consistency:

        5. SAME GROUP = SAME COLOR - The same condition/group/category must use the
           identical color in every figure where it appears. Flag any violations.

        6. PALETTE HARMONY - Do the figures look like they belong to the same paper?

        Output format:
        PER-FIGURE:
          [figure_name]:
            COLOR PURPOSE:      [Meaningful / Decorative] - [specifics]
            COLOR ECONOMY:      [Good / Excessive] - [specifics]
            COLORBLIND SAFE:    [Yes / At risk] - [specifics]
            PALETTE QUALITY:    [Good / Needs work] - [specifics]

        CROSS-FIGURE:
          - [description of any cross-figure inconsistency]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        try:
            findings = self._call_with_images(
                ctx, self.SYSTEM,
                f"Review all {len(ctx.figures)} figures for color usage and accessibility.",
                images=ctx.figures, max_tokens=LARGE_VISION_MAX_TOKENS,
            )
        except Exception as e:
            findings = f"*Color analysis failed: {e}*"
        return self._build_result(findings, t0)


class FigureTypographyAgent(BaseAgent):
    """Per-figure and cross-figure typography (fonts, sizes, abbreviations)."""

    id = "fig_typography"
    name = "Figure Typography & Annotation"
    description = "Font consistency - Size compliance - Abbreviations - Cross-figure type"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are a scientific figure reviewer specializing in typography and
        text annotation. You receive ALL figures simultaneously to check both
        per-figure quality and cross-figure consistency.

        For EACH figure individually, evaluate:

        1. FONT CONSISTENCY - Is a single sans-serif font (Helvetica or Arial preferred)
           used throughout the figure? Flag mixed fonts.

        2. FONT SIZE - Minimum 6 pt (absolute floor). Recommended:
           - 7-8 pt: axis tick labels
           - 8-9 pt: axis titles, legend text
           - 9-10 pt: panel labels (A, B, C)
           Flag text that appears too small at the figure's intended publication width.

        3. ABBREVIATIONS - Are all abbreviations defined within the figure or caption?

        4. AXIS LABELS - Must include units in parentheses where applicable.

        5. STATISTICAL ANNOTATIONS - If *, **, *** or "ns" markers are used,
           are they defined?

        Then evaluate the FULL FIGURE SET for cross-figure consistency:

        6. FONT FAMILY - Same typeface across all figures.
        7. FONT SIZES - Same pt sizes for equivalent elements across all figures.
        8. CAPITALIZATION STYLE - Same convention across all figures.

        Output format:
        PER-FIGURE:
          [figure_name]:
            FONT:               [Consistent / Mixed] - [specifics]
            SIZE:               [Adequate / Too small] - [smallest element]
            ABBREVIATIONS:      [All defined / Undefined: <list>]
            AXIS LABELS:        [Complete / Missing units: <list>]

        CROSS-FIGURE:
          - [description of any cross-figure typography inconsistency]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)
        try:
            findings = self._call_with_images(
                ctx, self.SYSTEM,
                f"Review all {len(ctx.figures)} figures for typography and annotation consistency.",
                images=ctx.figures, max_tokens=LARGE_VISION_MAX_TOKENS,
            )
        except Exception as e:
            findings = f"*Typography analysis failed: {e}*"
        return self._build_result(findings, t0)


class FigureCrossConsistencyAgent(BaseAgent):
    """Final cross-figure synthesis; consumes the other figure agents' reports."""

    id = "fig_consistency"
    name = "Cross-Figure Consistency"
    description = "Color palette - Typography - Panel labels - Visual weight - Overall cohesion"
    priority = 3

    _FIGURE_AGENT_IDS = (
        "fig_story", "fig_composition", "fig_color",
        "fig_typography", "fig_format", "fig_caption", "fig_statistics",
    )

    SYSTEM = textwrap.dedent("""
        You are the final reviewer in a multi-agent figure audit pipeline for a
        scientific manuscript submission. You receive:
          - All figures simultaneously
          - Reports from 7 specialist figure agents

        Your job is PURELY cross-figure consistency and final synthesis. Do not repeat
        issues already flagged by specialist agents unless they have a cross-figure
        dimension that was not captured.

        Evaluate:

        1. COLOR PALETTE CONSISTENCY - Same condition = same color everywhere.
        2. TYPOGRAPHIC CONSISTENCY - Font family, sizes, capitalization must match.
        3. LINE WEIGHTS & MARKER CONSISTENCY - Same plot types must match.
        4. ERROR BAR CONVENTION - SD, SEM, or CI must be consistent throughout.
        5. PANEL LABEL STYLE - Bold, size, position of A/B/C labels must be uniform.
        6. LEGEND POSITION CONVENTION - Consistent placement across figures.
        7. VISUAL WEIGHT BALANCE - No single figure should look dramatically different.
        8. AXIS STYLING - Line weights, tick direction/length must be consistent.
        9. FIGURE ORDERING - Are figures designed to be cited in logical narrative order?

        Output a PRIORITIZED revision checklist:

        BLOCKING ISSUES (must resolve before submission):
          1. <description> - affects: [Figure X, Figure Y]

        ADVISORY ISSUES (strongly recommended):
          1. <description> - affects: [Figure X, Figure Y]

        OVERALL ASSESSMENT:
          Publication readiness: [Ready / Minor revisions / Major revisions]
          [2-3 sentence summary]
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        if not ctx.figures or not ctx.provider.supports_vision():
            return _skip_result(self.id, self.name)

        # Pull together the other figure agents' findings for the final pass.
        prior_text = ""
        for aid in self._FIGURE_AGENT_IDS:
            result = ctx.prior_results.get(aid)
            if result and result.findings:
                prior_text += (
                    f"\n\n=== {result.agent_name} "
                    f"[{result.severity.upper()}] ===\n"
                    f"{result.findings[:FIGURE_PRIOR_FINDINGS_CHARS]}"
                )

        user = (
            f"Previous figure agent reports:\n{prior_text}\n\n"
            f"Now review all {len(ctx.figures)} figures for cross-figure "
            f"consistency and provide a final synthesis."
        )
        try:
            findings = self._call_with_images(
                ctx, self.SYSTEM, user,
                images=ctx.figures, max_tokens=LARGE_VISION_MAX_TOKENS,
            )
        except Exception as e:
            findings = f"*Cross-figure analysis failed: {e}*"
        return self._build_result(findings, t0)
