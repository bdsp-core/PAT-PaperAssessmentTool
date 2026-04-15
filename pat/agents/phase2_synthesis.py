"""
Phase 2 - whole-document synthesis agents.

These agents see the full manuscript (and, for adversarial review, the
output of earlier agents) to catch cross-section problems:

* :class:`ConsistencyAgent`          - Terminology and numeric consistency.
* :class:`DiscussionAgent`           - Related work, positioning, limitations.
* :class:`AbstractQualityAgent`      - Abstract vs. results fidelity.
* :class:`ReviewerTwoAgent`          - Adversarial peer-review stance.
* :class:`PaperPositioningAgent`     - Two-pass literature comparison.
* :class:`ReportingGuidelineAgent`   - Auto-detects study type, then checks guideline.
"""

from __future__ import annotations

import textwrap
import time

from pat.parser import get_section, get_sections_combined

from ._reporting_guidelines import REPORTING_GUIDELINES
from .base import (
    AgentResult,
    BaseAgent,
    Context,
    _journal_context,
    _parse_score,
    _parse_section_refs,
    _parse_severity,
    _parse_summary,
    _parse_top_issues,
)
from .constants import (
    DOMAIN_DETECT_PREVIEW_CHARS,
    DOMAIN_KEYWORD_MIN_HITS,
    EXTRACTION_MAX_TOKENS,
    GUIDELINE_FAIL_MAJOR_THRESHOLD,
    GUIDELINE_FAIL_MODERATE_THRESHOLD,
    GUIDELINE_PARTIAL_MINOR_THRESHOLD,
    GUIDELINE_PARTIAL_MODERATE_THRESHOLD,
    LONG_FORM_MAX_TOKENS,
    POSITIONING_ABSTRACT_PREVIEW_CHARS,
    POSITIONING_MAX_CLAIMS,
    POSITIONING_RATE_LIMIT_SECONDS,
    SHORT_CLASSIFY_MAX_TOKENS,
    SYNTHESIS_MAX_TOKENS,
)
from .reference_backends import PubMedBackend


# ---------------------------------------------------------------------------
# Internal helper: domain-specific Reviewer #2 prompts
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "clinical": [
        "patient", "clinical trial", "randomized", "placebo",
        "cohort", "diagnosis", "treatment", "hospital",
    ],
    "ml": [
        "neural network", "deep learning", "training", "dataset",
        "baseline", "ablation", "model architecture", "transformer",
    ],
    "neuroscience": [
        "brain", "neural", "cortex", "fmri", "eeg",
        "hippocampus", "neuron", "cognitive",
    ],
    "epidemiology": [
        "prevalence", "incidence", "odds ratio", "hazard ratio",
        "epidemiolog", "population-based", "case-control",
    ],
}

_DOMAIN_PROMPTS: dict[str, str] = {
    "clinical": """

        ## Domain-Specific Concerns (Clinical)
        - CONSORT compliance: is the trial properly reported?
        - Intention-to-treat vs per-protocol analysis?
        - Blinding: who was blinded, how was it maintained?
        - Confounding: were confounders identified and controlled?
        - Generalizability: can results be applied beyond the study population?
        - Adverse events: are they fully reported?
        """,
    "ml": """

        ## Domain-Specific Concerns (Machine Learning)
        - Dataset leakage: is there any information leakage between train/test?
        - Train/test contamination: proper data splitting?
        - Hyperparameter sensitivity: how sensitive are results to tuning?
        - Ablation studies: what is the contribution of each component?
        - Computational cost: is the approach practical?
        - Comparison fairness: are baselines given the same tuning budget?
        """,
    "neuroscience": """

        ## Domain-Specific Concerns (Neuroscience)
        - Multiple comparisons: is correction applied (Bonferroni, FDR)?
        - Effect sizes: are they reported alongside p-values?
        - Power analysis: was sample size justified a priori?
        - ROI selection: were regions of interest pre-specified or post-hoc?
        - Circular analysis: was the same data used for selection and testing?
        """,
    "epidemiology": """

        ## Domain-Specific Concerns (Epidemiology)
        - STROBE compliance for observational studies
        - Selection bias: how were participants recruited?
        - Information bias: how were exposures/outcomes measured?
        - Confounding: is there residual confounding?
        - Temporality: is the direction of association clear?
        """,
}


def _detect_domain_prompts(ctx: Context) -> str:
    """Return domain-specific Reviewer #2 attack vectors, or an empty string."""
    domain = ctx.config.get("domain", "").lower()

    if not domain:
        text_lower = ctx.paper_text[:DOMAIN_DETECT_PREVIEW_CHARS].lower()
        scores = {
            name: sum(1 for kw in keywords if kw in text_lower)
            for name, keywords in _DOMAIN_KEYWORDS.items()
        }
        top = max(scores, key=scores.get)
        if scores[top] >= DOMAIN_KEYWORD_MIN_HITS:
            domain = top

    return _DOMAIN_PROMPTS.get(domain, "")


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class ConsistencyAgent(BaseAgent):
    """Internal consistency: terminology, numbers, and claims across sections."""

    id = "consistency"
    name = "Internal Consistency"
    description = "Terminology - Numbers - Claims across sections"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are a meticulous scientific editor checking for internal consistency.

        ## Terminology Consistency
        - Are key concepts referred to by ONE consistent name throughout?
        - Are method/model/dataset names consistent?
        - Are statistical terms used consistently?
        - Flag cases where inconsistent terminology could make readers think two things
          are different when they're the same (or vice versa).

        ## Numerical Consistency - check every number appearing in multiple places
        - Sample sizes (abstract vs. methods vs. results vs. tables)
        - Performance metrics (text vs. tables vs. figures)
        - Parameter values (methods vs. appendix)
        - Abstract numbers vs. results section numbers

        ## Claim Consistency
        - Do discussion claims match what results actually showed?
        - Does the introduction promise something the paper doesn't fully deliver?
        - Are limitations acknowledged consistently across sections?
        - Do baseline comparisons in intro match those actually reported?

        ## Figure/Table Consistency
        - Do captions accurately describe what is shown?
        - Do in-text numbers match tables/figures?

        Output format:
        PASS Consistent: [verified claim]
        WARN Discrepancy: [where / what differs - quote both]

        Output: one-line SUMMARY + SEVERITY, then findings.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)


class DiscussionAgent(BaseAgent):
    """Discussion + related work + limitations: positioning and scope audit."""

    id = "discussion"
    name = "Discussion & Related Work"
    description = "Coverage of related literature - Positioning - Limitations - Future directions"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are an expert reviewer evaluating the Discussion and Related Work sections
        of a scientific paper.

        ## Positioning in the Literature
        - Does the discussion clearly articulate how this work advances the field?
        - Is the paper positioned relative to the most important prior work?
        - Are there obvious related papers or lines of work that are not discussed?
          (Flag as gaps - these are likely to be raised by reviewers.)
        - Are direct comparisons made where comparison is possible?
        - Does the paper distinguish itself from closely related work with specificity,
          not vague claims like "our method is better"?

        ## Related Work Section Quality
        - Is related work merely listed, or is it synthesized into a coherent narrative?
        - Is the framing generous and accurate (Freeman: "written from a position of
          security, not competition")?
        - Does related work section explain WHY prior methods fall short, motivating
          the new approach?
        - Are the most recent and relevant papers included?

        ## Adelson's Formula Applied to Discussion
        The discussion should mirror the introduction: you stated a problem and solution;
        now confirm what was delivered, compare to prior methods, and situate the work.

        ## Limitations
        - Are limitations acknowledged honestly and specifically?
        - Are they appropriately scoped (not underselling OR overselling)?
        - Are failure modes addressed?

        ## Scope and Impact
        - Does the discussion communicate the broader significance?
        - Does it suggest where the work may lead WITHOUT a laundry-list "future work"
          section? (Freeman: "I can't stand future work sections.")
        - Does the paper end with a strong conclusion, not a whimper?

        Output: one-line SUMMARY + SEVERITY, then section-by-section findings with
        specific suggestions and any obvious missing related work topics.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_sections_combined(
            ctx.sections, ["introduction", "discussion", "conclusion"]
        )
        findings = self._call(ctx, self.SYSTEM, f"PAPER:\n\n{text}")
        return self._build_result(findings, t0)


class AbstractQualityAgent(BaseAgent):
    """Abstract structure + fidelity: cross-checks the abstract against results."""

    id = "abstract"
    name = "Abstract Quality"
    description = "Structure - Completeness - Fidelity to results - Sells the finding"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are a scientific editor specializing in abstract evaluation.
        The abstract is the most-read part of any paper - it must be precise,
        complete, and compelling.

        ## Structure Compliance
        - Does the abstract follow IMRAD structure (Background/Objective, Methods,
          Results, Conclusions) even if not explicitly labeled?
        - If the target journal requires a structured abstract, are all sections present?
        - Is the structure logical: problem -> approach -> key results -> significance?

        ## Completeness (check each element)
        - Background: Is the problem stated? Is significance/gap clear?
        - Objective: Is the specific aim/hypothesis stated?
        - Methods: Are the study design, setting, participants, and key methods described?
        - Results: Are the PRIMARY quantitative results stated with numbers?
        - Conclusions: Does it state what the findings mean, not just restate results?

        ## Fidelity to Paper
        Compare abstract claims against the results/conclusion sections provided.
        - Flag any numbers in the abstract that don't match the results section.
        - Flag any claims in the abstract that overstate what the results show.
        - Flag conclusions in the abstract that go beyond what the data supports.
        - Flag if the abstract mentions results not found in the results section.

        ## Writing Quality
        - Are acronyms avoided or defined within the abstract?
        - Is jargon minimized for a broad audience?
        - Is the abstract self-contained (understandable without reading the paper)?
        - Does it "sell" the key finding - is the most important result prominent?
        - Is the abstract within the word limit (if specified)?

        ## Common Problems
        - Vague conclusions: "These results have implications for..."
        - Missing quantitative results (only qualitative statements)
        - Burying the key finding after less important results
        - Background too long, results too short
        - Passive voice throughout diminishing impact
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        abstract = get_section(ctx.sections, "abstract")
        results = get_section(ctx.sections, "results", fallback="")
        conclusion = get_section(ctx.sections, "conclusion", fallback="")

        user = f"ABSTRACT:\n\n{abstract}"
        if results:
            user += f"\n\nRESULTS SECTION (for cross-checking):\n\n{results}"
        if conclusion:
            user += f"\n\nCONCLUSION SECTION:\n\n{conclusion}"
        user += _journal_context(ctx.config)

        findings = self._call(ctx, self.SYSTEM, user)
        return self._build_result(findings, t0)


class ReviewerTwoAgent(BaseAgent):
    """Simulates an adversarial peer reviewer; appends domain-specific prompts."""

    id = "reviewer2"
    name = "Adversarial Reviewer #2"
    description = "Simulates a skeptical, critical peer reviewer"
    priority = 2

    SYSTEM = textwrap.dedent("""
        You are Reviewer #2 - a skeptical, experienced peer reviewer who has seen
        hundreds of papers and has no patience for overclaiming, hand-waving, or
        methodological shortcuts. Your reviews are tough but fair.

        Your job is to find the weakest points of this manuscript - the ones that
        will get it rejected if not addressed. Be specific and quotable.

        ## What to Attack

        1. **Overclaiming**: Where do the claims exceed the evidence?
           - "You tested on dataset X but claim generalizability to Y"
           - "Your abstract says 'outperforms' but Table 2 shows overlapping CIs"
           - "You claim novelty but [obvious prior work] did this in [year]"

        2. **Missing Controls & Baselines**:
           - What obvious baselines are not compared against?
           - What ablation studies are missing?
           - What confounds are not controlled for?

        3. **Unstated Assumptions**:
           - What assumptions does the method depend on that aren't acknowledged?
           - Under what conditions would this approach fail?

        4. **Logical Gaps**:
           - Where does the argument skip steps?
           - Where do the methods not justify the conclusions?
           - Do the discussion claims actually follow from the results?

        5. **Statistical Concerns**:
           - Multiple comparisons without correction?
           - Small sample sizes?
           - Missing effect sizes or confidence intervals?
           - P-hacking risk?

        6. **Reproducibility Red Flags**:
           - Are hyperparameters specified?
           - Is the code/data available?
           - Could someone replicate this from the paper alone?

        ## Output Format

        Start with a one-line SEVERITY assessment.
        Then list your concerns as a numbered list, each with:
        - **The weakness** (specific, quotable)
        - **Why it matters** (what a reviewer would say)
        - **Suggested defense** (how the authors could address it)

        Be harsh but constructive. The goal is to help authors anticipate
        and preemptively address reviewer concerns.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        system = self.SYSTEM + _detect_domain_prompts(ctx)
        findings = self._call(ctx, system, f"PAPER:\n\n{ctx.paper_text}")
        return self._build_result(findings, t0)


class PaperPositioningAgent(BaseAgent):
    """Two-pass literature comparison: extract novelty claims, search, analyse overlap."""

    id = "positioning"
    name = "Paper Positioning & Novelty"
    description = "Competitive landscape - Prior art detection - Must-cite papers"
    priority = 2
    needs_scholar = True

    EXTRACT_SYSTEM = textwrap.dedent("""
        You are a scientific literature analyst. Extract the key claims from this
        manuscript that assert novelty or contribution. For each claim, generate
        a precise search query for finding related prior work.

        Output EXACTLY this format for each claim (5-10 claims):
        CLAIM: <the novelty claim, verbatim or paraphrased>
        QUERY: <5-8 word PubMed/bioRxiv search query>
        SECTION: <which section this claim appears in>
        ---

        Focus on:
        - "First to..." or "novel..." claims
        - Methodology claims ("we propose/develop/introduce...")
        - Performance claims ("outperforms/achieves state-of-the-art...")
        - Gap claims ("no prior work has..." or "existing methods fail to...")
    """).strip()

    ANALYZE_SYSTEM = textwrap.dedent("""
        You are a scientific literature expert analyzing how a manuscript positions
        itself relative to existing work.

        Given the manuscript's claims and search results from literature databases,
        assess:

        1. **Prior Art Risk**: Are any novelty claims already published? Rate each
           claim: NOVEL (no close matches), PARTIAL (related but different), RISK (very similar work exists)

        2. **Must-Cite Papers**: Which found papers should definitely be cited and discussed?
           Explain why each is important to cite.

        3. **Positioning Gaps**: What areas of related work are the authors not discussing
           that reviewers will expect? What obvious comparisons are missing?

        4. **Competitive Landscape**: How does this work sit relative to the state of the art?
           Is the claimed advance incremental or substantial?

        5. **Suggested Framing**: How should the authors position their contribution
           to be most compelling while remaining honest?

        Be specific and cite the found papers by title.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        text = get_sections_combined(
            ctx.sections, ["abstract", "introduction", "discussion"]
        )
        extract_resp = self._call(
            ctx, self.EXTRACT_SYSTEM, f"PAPER:\n\n{text}",
            max_tokens=EXTRACTION_MAX_TOKENS,
        )

        # Parse claim / query / section triples into dicts.
        claims: list[dict] = []
        current: dict = {}
        for line in extract_resp.splitlines():
            if line.startswith("CLAIM:"):
                current["claim"] = line[6:].strip()
            elif line.startswith("QUERY:"):
                current["query"] = line[6:].strip()
            elif line.startswith("SECTION:"):
                current["section"] = line[8:].strip()
            elif line.strip() == "---" and current.get("claim"):
                claims.append(current)
                current = {}
        if current.get("claim"):
            claims.append(current)

        if not claims:
            return self._build_result(
                "Could not extract novelty claims from the manuscript.", t0
            )

        search_results: list[dict] = []
        for claim in claims[:POSITIONING_MAX_CLAIMS]:
            query = claim.get("query", claim.get("claim", ""))
            if ctx.ref_backend:
                hits = ctx.ref_backend.search(query, n=3)
            else:
                hits = PubMedBackend().search(query, n=3)
            valid = [h for h in hits if not h["title"].startswith("[")]
            search_results.append({"claim": claim, "hits": valid})
            time.sleep(POSITIONING_RATE_LIMIT_SECONDS)

        analysis_input = "MANUSCRIPT CLAIMS AND LITERATURE SEARCH RESULTS:\n\n"
        for i, sr in enumerate(search_results, 1):
            c = sr["claim"]
            analysis_input += f"## Claim {i}: {c['claim']}\n"
            analysis_input += f"Section: {c.get('section', 'unknown')}\n"
            if sr["hits"]:
                analysis_input += "Related papers found:\n"
                for j, h in enumerate(sr["hits"], 1):
                    analysis_input += (
                        f"  {j}. \"{h['title']}\" - {h['authors']} "
                        f"({h['year']}) {h['venue']}\n"
                    )
                    if h.get("abstract"):
                        preview = h["abstract"][:POSITIONING_ABSTRACT_PREVIEW_CHARS]
                        analysis_input += f"     Abstract: {preview}\n"
            else:
                analysis_input += "No closely related papers found.\n"
            analysis_input += "\n"

        findings = self._call(
            ctx, self.ANALYZE_SYSTEM, analysis_input,
            max_tokens=SYNTHESIS_MAX_TOKENS,
        )

        refs_found: list[dict] = []
        for sr in search_results:
            for h in sr["hits"]:
                refs_found.append({
                    "para": 0,
                    "claim": sr["claim"]["claim"],
                    "ref": h,
                })

        severity = _parse_severity(findings)
        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=_parse_summary(findings),
            findings=findings,
            severity=severity,
            references_found=refs_found,
            elapsed=time.time() - t0,
            top_issues=_parse_top_issues(findings),
            score=_parse_score(findings, severity),
            section_refs=_parse_section_refs(findings),
        )


class ReportingGuidelineAgent(BaseAgent):
    """Auto-detects study type, then scores a checklist from CONSORT/STROBE/etc."""

    id = "guidelines"
    name = "Reporting Guideline Compliance"
    description = "Auto-detects study type - CONSORT/STROBE/TRIPOD/PRISMA/STARD checklist"
    priority = 2

    DETECT_SYSTEM = textwrap.dedent("""
        You are an expert at identifying study types in scientific manuscripts.
        Based on the methods section, classify this study into ONE primary type:

        - RCT (randomized controlled trial)
        - OBSERVATIONAL (cohort, case-control, cross-sectional)
        - PREDICTION_MODEL (machine learning, risk score, diagnostic/prognostic model)
        - SYSTEMATIC_REVIEW (systematic review, meta-analysis)
        - DIAGNOSTIC_ACCURACY (sensitivity/specificity, biomarker validation)
        - OTHER (basic science, qualitative, case report, etc.)

        Output EXACTLY:
        STUDY_TYPE: <type from above>
        CONFIDENCE: <high|medium|low>
        RATIONALE: <one sentence explaining why>
    """).strip()

    CHECK_SYSTEM = textwrap.dedent("""
        You are an expert in reporting guidelines ({guideline_name}).
        Evaluate whether this manuscript adequately addresses each checklist item.

        For each item, respond EXACTLY:
        ITEM {item_id}: PASS | PARTIAL | FAIL | NA
        EVIDENCE: <brief quote or explanation from the paper>

        Be strict but fair. PASS means clearly addressed. PARTIAL means mentioned
        but incomplete. FAIL means not addressed when it should be.
    """).strip()

    _STUDY_TYPE_TO_GUIDELINE = {
        "RCT": "CONSORT",
        "OBSERVATIONAL": "STROBE",
        "PREDICTION_MODEL": "TRIPOD",
        "SYSTEMATIC_REVIEW": "PRISMA",
        "DIAGNOSTIC_ACCURACY": "STARD",
    }

    def _detect_study_type(self, ctx: Context) -> tuple[str, str, str]:
        """Returns ``(study_type, confidence, rationale)``."""
        methods = get_section(ctx.sections, "methods")
        abstract = get_section(ctx.sections, "abstract")
        text = f"ABSTRACT:\n{abstract}\n\nMETHODS:\n{methods}"
        resp = self._call(
            ctx, self.DETECT_SYSTEM, text, max_tokens=SHORT_CLASSIFY_MAX_TOKENS
        )

        study_type = "OTHER"
        confidence = "low"
        rationale = ""
        for line in resp.splitlines():
            if line.startswith("STUDY_TYPE:"):
                study_type = line[11:].strip().upper()
            elif line.startswith("CONFIDENCE:"):
                confidence = line[11:].strip().lower()
            elif line.startswith("RATIONALE:"):
                rationale = line[10:].strip()
        return study_type, confidence, rationale

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()

        study_type, confidence, rationale = self._detect_study_type(ctx)
        guideline_key = self._STUDY_TYPE_TO_GUIDELINE.get(study_type)

        if not guideline_key or guideline_key not in REPORTING_GUIDELINES:
            findings = (
                f"## Study Type Detection\n\n"
                f"**Detected type:** {study_type} (confidence: {confidence})\n"
                f"**Rationale:** {rationale}\n\n"
                "No standard reporting guideline applies to this study type. "
                "Consider whether EQUATOR Network guidelines apply.\n\n"
                "---\nSEVERITY: ok\nSCORE: 0.8\n"
                f"SUMMARY: Study type {study_type} - no standard guideline checklist applicable"
            )
            return self._build_result(findings, t0)

        guideline = REPORTING_GUIDELINES[guideline_key]
        items = guideline["items"]

        system = self.CHECK_SYSTEM.format(
            guideline_name=f"{guideline_key} ({guideline['full_name']})"
        )
        items_text = "\n".join(f"- Item {item_id}: {desc}" for item_id, desc in items)
        user = (
            f"CHECKLIST ({guideline_key}):\n{items_text}\n\n"
            f"PAPER:\n{ctx.paper_text}"
        )
        resp = self._call(ctx, system, user, max_tokens=LONG_FORM_MAX_TOKENS)

        passes = partials = fails = na_count = 0
        for line in resp.splitlines():
            upper = line.strip().upper()
            if not upper.startswith("ITEM"):
                continue
            if "PASS" in upper:
                passes += 1
            elif "PARTIAL" in upper:
                partials += 1
            elif "FAIL" in upper:
                fails += 1
            elif ": NA" in upper:
                na_count += 1

        total = passes + partials + fails
        pct = (passes / total * 100) if total else 0

        findings = (
            f"## Reporting Guideline: {guideline_key}\n"
            f"**Full name:** {guideline['full_name']}\n"
            f"**Study type detected:** {study_type} (confidence: {confidence})\n"
            f"**Rationale:** {rationale}\n\n"
            f"### Compliance Summary\n"
            f"- **PASS:** {passes}/{total} items ({pct:.0f}%)\n"
            f"- **PARTIAL:** {partials}/{total} items\n"
            f"- **FAIL:** {fails}/{total} items\n"
            f"- **N/A:** {na_count} items\n\n"
            f"### Detailed Assessment\n\n{resp}"
        )

        if fails > GUIDELINE_FAIL_MAJOR_THRESHOLD:
            severity = "major"
        elif fails > GUIDELINE_FAIL_MODERATE_THRESHOLD or partials > GUIDELINE_PARTIAL_MODERATE_THRESHOLD:
            severity = "moderate"
        elif fails > 0 or partials > GUIDELINE_PARTIAL_MINOR_THRESHOLD:
            severity = "minor"
        else:
            severity = "ok"

        score = max(0.0, min(1.0, pct / 100.0))

        fail_items = [
            line.strip() for line in resp.splitlines()
            if "FAIL" in line.upper() and line.strip().startswith("ITEM")
        ][:3]

        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=f"{guideline_key}: {passes}/{total} items pass ({pct:.0f}%), {fails} failures",
            findings=findings,
            severity=severity,
            references_found=[],
            elapsed=time.time() - t0,
            top_issues=[f.strip() for f in fail_items],
            score=score,
            section_refs=["methods", "results"],
        )
