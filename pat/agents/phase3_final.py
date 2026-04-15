"""
Phase 3 - synthesis and submission-readiness agents.

* :class:`OrchestratorAgent`         - Cross-cutting action plan from all prior results.
* :class:`ChecklistAgent`            - Programmatic + LLM submission-readiness checklist.
* :class:`ResponseToReviewersAgent`  - Drafts a structured Response to Reviewers.
"""

from __future__ import annotations

import re
import textwrap
import time

from pat.parser import get_section, get_sections_combined

from .base import AgentResult, BaseAgent, Context, _journal_context
from .constants import (
    CHECKLIST_MAX_TOKENS,
    LONG_FORM_MAX_TOKENS,
    PRIOR_FINDINGS_FALLBACK_CHARS,
    SYNTHESIS_MAX_TOKENS,
)


class OrchestratorAgent(BaseAgent):
    """Reads every prior agent's ``top_issues`` and produces a prioritised action plan."""

    id = "orchestrator"
    name = "Synthesis & Prioritized Action Plan"
    description = "Synthesizes all agent findings into a ranked action plan"
    priority = 3

    SYSTEM = textwrap.dedent("""
        You are a senior scientific editor. You have received review reports from multiple
        specialized agents that have audited different dimensions of a manuscript.

        Your task:
        1. Synthesize all findings into a coherent overall assessment.
        2. Identify cross-cutting themes (e.g., "the writing problems and the structural
           problems share a common root: the main contribution is never stated clearly").
        3. Produce a PRIORITIZED ACTION PLAN - the top 10 most impactful revisions,
           ordered from most to least important.
        4. Give an overall readiness verdict:
           - Not ready: fundamental problems require major restructuring
           - Needs revision: several moderate issues; revisions achievable in one pass
           - Nearly ready: minor issues only; one careful editing pass suffices
           - Ready to submit: only cosmetic issues remain

        Format the action plan as:
        ### Action Plan
        1. [SEVERITY] Area: specific action
        ...

        Then give the overall verdict with a brief rationale (2-3 sentences).
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        # Build a compact digest of prior agent results using TOP_ISSUES where
        # available - the orchestrator should not re-process raw findings text.
        prior_text = ""
        for _, result in ctx.prior_results.items():
            if not result or not result.findings:
                continue
            prior_text += (
                f"\n\n## {result.agent_name} [{result.severity.upper()}]\n"
                f"Summary: {result.summary}\n"
            )
            if result.top_issues:
                prior_text += "Key issues:\n"
                for i, issue in enumerate(result.top_issues, 1):
                    prior_text += f"  {i}. {issue}\n"
            else:
                prior_text += result.findings[:PRIOR_FINDINGS_FALLBACK_CHARS]

        paper_context = get_sections_combined(
            ctx.sections, ["abstract", "conclusion"]
        )

        user = f"PAPER CONTEXT:\n{paper_context}\n\nAGENT REPORTS:\n{prior_text}"
        user += _journal_context(ctx.config)
        findings = self._call(ctx, self.SYSTEM, user, max_tokens=SYNTHESIS_MAX_TOKENS)
        return self._build_result(findings, t0)


class ChecklistAgent(BaseAgent):
    """Submission-readiness checklist: programmatic checks plus LLM-verified items."""

    id = "checklist"
    name = "Submission Checklist"
    description = "Concrete submission readiness checklist with auto-verified items"
    priority = 3

    SYSTEM = textwrap.dedent("""
        You are a journal submission coordinator. Based on the paper and
        journal requirements provided, evaluate these submission readiness items.

        For each item, answer YES, NO, or N/A with a brief note:

        1. Title: Is it informative, specific, and concise (<15 words)?
        2. Abstract: Does it contain all required sections (if structured)?
        3. Keywords: Are key terms / keywords listed?
        4. Author contributions: Is there an author contribution statement?
        5. Conflict of interest: Is there a COI disclosure?
        6. Funding: Is funding acknowledged?
        7. Conclusion: Does the paper end with a clear conclusion (not just "future work")?
        8. Statistical reporting: Are test statistics, p-values, and effect sizes reported?
        9. Ethical approval: Is ethical approval mentioned (if human/animal subjects)?
        10. Data/code availability: Is a data/code availability statement present?

        Output as a checklist:
        - [x] Item - passes (note)
        - [ ] Item - fails (note)
        - [-] Item - not applicable (note)

        Then give a count: X/Y items pass.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        from pat.metrics import compute_metrics
        t0 = time.time()

        # ---- Programmatic (deterministic) checks ----
        m = compute_metrics(ctx.paper_text, ctx.config)
        auto_checks: list[str] = []

        word_limit = ctx.config.get("word_limit")
        if word_limit:
            ok = m["word_count"] <= word_limit
            auto_checks.append(
                f"- [{'x' if ok else ' '}] Word count: {m['word_count']:,} "
                f"(limit: {word_limit:,})"
            )

        abstract_limit = ctx.config.get("abstract_word_limit")
        if abstract_limit and "abstract" in ctx.sections:
            abs_words = len(ctx.sections["abstract"].split())
            ok = abs_words <= abstract_limit
            auto_checks.append(
                f"- [{'x' if ok else ' '}] Abstract length: {abs_words} words "
                f"(limit: {abstract_limit})"
            )

        has_refs = "references" in ctx.sections
        auto_checks.append(
            f"- [{'x' if has_refs else ' '}] References section present"
        )

        fig_refs = set(re.findall(r'(?:Figure|Fig\.?)\s*(\d+)', ctx.paper_text, re.I))
        table_refs = set(re.findall(r'Table\s*(\d+)', ctx.paper_text, re.I))
        if fig_refs:
            auto_checks.append(
                f"- [x] Figures referenced in text: {', '.join(sorted(fig_refs))}"
            )
        if table_refs:
            auto_checks.append(
                f"- [x] Tables referenced in text: {', '.join(sorted(table_refs))}"
            )

        has_data_stmt = bool(re.search(
            r'data\s+availab|code\s+availab|data\s+sharing|open\s+access',
            ctx.paper_text, re.I,
        ))
        auto_checks.append(
            f"- [{'x' if has_data_stmt else ' '}] Data/code availability statement detected"
        )

        has_ethics = bool(re.search(
            r'ethic|IRB|institutional review|informed consent|Helsinki',
            ctx.paper_text, re.I,
        ))
        if ctx.config.get("requires_ethics_statement"):
            auto_checks.append(
                f"- [{'x' if has_ethics else ' '}] Ethics statement detected"
            )

        auto_section = "## Auto-Verified Checks\n\n" + "\n".join(auto_checks)

        # ---- LLM checks (subjective items) ----
        paper_excerpt = get_sections_combined(
            ctx.sections, ["abstract", "introduction", "conclusion"]
        )
        user = f"PAPER:\n\n{paper_excerpt}"
        user += _journal_context(ctx.config)
        llm_findings = self._call(
            ctx, self.SYSTEM, user, max_tokens=CHECKLIST_MAX_TOKENS
        )

        findings = f"{auto_section}\n\n## LLM-Verified Checks\n\n{llm_findings}"

        pass_count = findings.count("[x]")
        fail_count = findings.count("[ ]")
        total = pass_count + fail_count
        summary = f"{pass_count}/{total} checklist items pass"

        if fail_count == 0:
            severity = "ok"
        elif fail_count <= 2:
            severity = "minor"
        else:
            severity = "moderate"

        fail_items = [
            line.strip() for line in findings.splitlines() if "[ ]" in line
        ][:3]

        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=summary,
            findings=findings,
            severity=severity,
            references_found=[],
            elapsed=time.time() - t0,
            top_issues=fail_items,
        )


class ResponseToReviewersAgent(BaseAgent):
    """Drafts a structured Response to Reviewers using Reviewer #2's findings."""

    id = "response"
    name = "Response to Reviewers (Draft)"
    description = "Generates preemptive response to Reviewer #2 concerns"
    priority = 3

    SYSTEM = textwrap.dedent("""
        You are an experienced academic helping authors draft a "Response to Reviewers"
        document. Based on the Adversarial Reviewer #2 findings, generate a professional,
        structured response that addresses each concern.

        For each reviewer concern:
        1. **Concern Summary:** Restate the reviewer's point concisely
        2. **Response Strategy:** How to address it (revise text, add data, add analysis,
           or provide a rebuttal explaining why the concern is already addressed)
        3. **Draft Response:** Write the actual response text in the format:
           "We thank the reviewer for this observation. [Specific response]."
        4. **Suggested Revision:** If text changes are needed, specify which section
           and what to change.

        Use a professional, respectful tone throughout. Never be defensive.
        Acknowledge valid points and explain clearly when a concern is based
        on a misunderstanding.

        Format as a numbered response document suitable for journal submission.
    """).strip()

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        reviewer2_result = ctx.prior_results.get("reviewer2")
        if not reviewer2_result or not reviewer2_result.findings:
            return AgentResult(
                agent_id=self.id,
                agent_name=self.name,
                summary="Skipped - no Reviewer #2 findings available",
                findings=(
                    "The Adversarial Reviewer #2 agent did not produce findings. "
                    "Run the full pipeline to enable this agent."
                ),
                severity="ok",
                references_found=[],
                elapsed=0.0,
                score=0.5,
            )

        abstract = get_section(ctx.sections, "abstract")
        user = (
            f"PAPER ABSTRACT:\n{abstract}\n\n"
            f"REVIEWER #2 CONCERNS:\n{reviewer2_result.findings}"
        )
        findings = self._call(
            ctx, self.SYSTEM, user, max_tokens=LONG_FORM_MAX_TOKENS
        )
        return self._build_result(findings, t0)
