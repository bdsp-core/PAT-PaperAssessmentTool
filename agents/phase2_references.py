"""
Phase 2 - reference agents.

These two agents both rely on the :class:`ReferenceSearchBackend` configured
by ``--ref-backend``:

* :class:`MissingReferencesAgent` - scans paragraphs for uncited claims and
  looks up candidate citations.
* :class:`ReferenceQualityAgent`  - extracts the existing reference list and
  evaluates whether each reference is appropriate / correct.
"""

from __future__ import annotations

import re
import textwrap
import time

from .base import AgentResult, BaseAgent, Context
from .constants import (
    MISSING_REFS_MAJOR_THRESHOLD,
    MISSING_REFS_MODERATE_THRESHOLD,
    PARAGRAPH_QUOTE_PREVIEW_CHARS,
    REFERENCE_ASSESS_MAX_TOKENS,
    REFERENCE_EXTRACT_MAX_TOKENS,
    REFERENCE_VERIFY_RATE_LIMIT_SECONDS,
    REF_QUALITY_MAJOR_THRESHOLD,
    REF_QUALITY_MODERATE_THRESHOLD,
    SCHOLAR_RATE_LIMIT_SECONDS,
    SHORT_BLOCK_MIN_CHARS,
    SHORT_CLASSIFY_MAX_TOKENS,
)


class MissingReferencesAgent(BaseAgent):
    """Paragraph-level scan for uncited claims, backed by a literature search."""

    id = "missing_refs"
    name = "Missing References"
    description = "Finds uncited claims - Searches for candidate references"
    priority = 2
    needs_scholar = True

    IDENTIFY_SYSTEM = textwrap.dedent("""
        You are a scientific editor identifying claims that need citations.

        For the paragraph below, list every statement that:
        - Makes a factual claim about the world, prior work, or prior results
        - Quantifies something (rates, prevalence, performance benchmarks)
        - Attributes a method, concept, or finding to prior work - even implicitly
        - Describes what "has been shown", "is known", "is well-established"
        - Describes limitations of prior approaches

        EXCLUDE:
        - Claims clearly about the authors' own work presented in this paper
        - Methodological descriptions of the authors' own procedure
        - Obvious common knowledge needing no citation

        For each claim found, output EXACTLY this format (one per line):
        CLAIM: <verbatim short phrase from the paragraph>
        QUERY: <3-5 word Google Scholar search query to find the right reference>

        If no claims need references, output: NONE
    """).strip()

    def _split_paragraphs(self, text: str) -> list[tuple[int, str]]:
        """Yield ``(paragraph_number, text)`` pairs, skipping the references section."""
        paragraphs: list[tuple[int, str]] = []
        in_refs = False
        for i, block in enumerate(re.split(r'\n{2,}', text)):
            block = block.strip()
            if not block:
                continue
            if re.match(r'^(references|bibliography|works cited)', block, re.I):
                in_refs = True
            if in_refs:
                continue
            if len(block) > SHORT_BLOCK_MIN_CHARS:  # skip short headers
                paragraphs.append((i + 1, block))
        return paragraphs

    def _ref_search(self, ctx: Context, query: str, n: int = 3) -> list[dict]:
        """Search references via the configured backend (falls back to PubMed)."""
        if ctx.ref_backend:
            return ctx.ref_backend.search(query, n)
        from .reference_backends import PubMedBackend
        return PubMedBackend().search(query, n)

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        paragraphs = self._split_paragraphs(ctx.paper_text)

        all_claims: list[dict] = []
        report_lines: list[str] = []

        for para_num, para_text in paragraphs:
            resp = self._call(
                ctx, self.IDENTIFY_SYSTEM,
                f"PARAGRAPH {para_num}:\n\n{para_text}",
                max_tokens=512,
            )

            if resp.strip().upper() == "NONE":
                continue

            # Parse CLAIM / QUERY pairs out of the LLM reply.
            claims_in_para: list[tuple[str, str]] = []
            claim_text = ""
            for line in resp.splitlines():
                if line.startswith("CLAIM:"):
                    claim_text = line[6:].strip()
                elif line.startswith("QUERY:"):
                    query_text = line[6:].strip()
                    if claim_text:
                        claims_in_para.append((claim_text, query_text))
                        claim_text = ""
                else:
                    claim_text = ""

            if not claims_in_para:
                continue

            preview = para_text[:PARAGRAPH_QUOTE_PREVIEW_CHARS]
            ellipsis = "..." if len(para_text) > PARAGRAPH_QUOTE_PREVIEW_CHARS else ""
            report_lines.append(f"\n### Paragraph {para_num}")
            report_lines.append(f"> {preview}{ellipsis}\n")

            for claim, query in claims_in_para:
                report_lines.append(f"**Uncited claim:** {claim}")
                report_lines.append(f"*Search query:* `{query}`")
                hits = self._ref_search(ctx, query, n=3)
                for j, hit in enumerate(hits, 1):
                    if not hit["title"].startswith("[Scholar"):
                        ref_str = (
                            f"{j}. **{hit['title']}** - "
                            f"{hit['authors']} ({hit['year']}) "
                            f"*{hit['venue']}*"
                        )
                        if hit["url"]:
                            ref_str += f" [link]({hit['url']})"
                        report_lines.append(ref_str)
                        all_claims.append({
                            "para": para_num, "claim": claim, "ref": hit,
                        })
                    else:
                        report_lines.append(f"  *(Scholar unavailable: {hit['title']})*")
                report_lines.append("")
                time.sleep(SCHOLAR_RATE_LIMIT_SECONDS)

        if not report_lines:
            findings = "No obviously uncited claims found. The paper appears well-referenced."
            severity = "ok"
        else:
            n_claims = sum(1 for r in report_lines if r.startswith("**Uncited claim:**"))
            header = (
                f"Found **{n_claims} uncited claim(s)** across "
                f"{len(paragraphs)} paragraphs.\n\n"
            )
            findings = header + "\n".join(report_lines)
            if n_claims > MISSING_REFS_MAJOR_THRESHOLD:
                severity = "major"
            elif n_claims > MISSING_REFS_MODERATE_THRESHOLD:
                severity = "moderate"
            else:
                severity = "minor"

        n_found = sum(1 for r in report_lines if r.startswith("**Uncited claim"))
        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=f"Found {n_found} uncited claims",
            findings=findings,
            severity=severity,
            references_found=all_claims,
            elapsed=time.time() - t0,
            top_issues=[c["claim"] for c in all_claims[:3]],
        )


class ReferenceQualityAgent(BaseAgent):
    """Extract the bibliography and evaluate correctness / appropriateness per entry."""

    id = "ref_quality"
    name = "Reference Quality & Correctness"
    description = "Checks cited refs for accuracy, appropriateness, and whether better refs exist"
    priority = 2
    needs_scholar = True

    EXTRACT_SYSTEM = textwrap.dedent("""
        Extract the reference list from the paper.
        For each reference output EXACTLY:
        REF_NUM: <number or key>
        TITLE: <title>
        AUTHORS: <authors>
        YEAR: <year>
        VENUE: <journal or conference>
        ---
        If no reference list is present, output: NO_REFS
    """).strip()

    ASSESS_SYSTEM = textwrap.dedent("""
        You are a senior scientific reviewer assessing whether a reference is:
        1. Correctly cited (title/authors/year match the real paper)
        2. The RIGHT reference for the claim being made (is it the seminal work?
           the most recent? the most directly relevant?)
        3. Whether a better or more canonical reference exists for this claim

        Context provided: the citing sentence in the paper, the reference details,
        and (if available) Google Scholar verification data.

        Output:
        STATUS: ok | wrong_paper | better_exists | unverifiable
        NOTES: <one sentence explanation>
        BETTER_REF: <suggest better ref title/authors if applicable>
    """).strip()

    def _extract_references(self, ctx: Context) -> list[dict]:
        resp = self._call(
            ctx, self.EXTRACT_SYSTEM,
            f"PAPER:\n\n{ctx.paper_text}",
            max_tokens=REFERENCE_EXTRACT_MAX_TOKENS,
        )
        if "NO_REFS" in resp:
            return []
        refs: list[dict] = []
        current: dict = {}
        for line in resp.splitlines():
            for field_name in ("REF_NUM", "TITLE", "AUTHORS", "YEAR", "VENUE"):
                if line.startswith(f"{field_name}:"):
                    current[field_name.lower()] = line[len(field_name) + 1:].strip()
            if line.strip() == "---" and current:
                refs.append(current)
                current = {}
        return refs

    def _find_citing_sentence(self, ref_num: str, text: str) -> str:
        """Return one or two sentences that cite ``ref_num``, if locatable."""
        patterns = [
            rf'\[{re.escape(ref_num)}\]',
            rf'\({re.escape(ref_num)}\)',
            rf'\b{re.escape(ref_num)}\b',
        ]
        for pat in patterns:
            matches = re.findall(r'[^.!?]*' + pat + r'[^.!?]*[.!?]', text)
            if matches:
                return " | ".join(matches[:2])
        return "(citing context not found)"

    def run(self, ctx: Context) -> AgentResult:
        t0 = time.time()
        refs = self._extract_references(ctx)
        if not refs:
            return AgentResult(
                agent_id=self.id,
                agent_name=self.name,
                summary="No reference list found in paper",
                findings="Could not extract a reference list. Check that references are included.",
                severity="moderate",
                references_found=[],
                elapsed=time.time() - t0,
            )

        report_lines = [f"Assessed **{len(refs)} references**.\n"]
        issues = 0

        for ref in refs:
            ref_num = ref.get("ref_num", "?")
            title = ref.get("title", "")
            citing = self._find_citing_sentence(ref_num, ctx.paper_text)

            scholar_info = ""
            if title and ctx.ref_backend:
                try:
                    hits = ctx.ref_backend.search(title, n=1)
                    if hits and not hits[0]["title"].startswith("["):
                        h = hits[0]
                        scholar_info = (
                            f"Verified: '{h['title']}' "
                            f"({h.get('year', '')}) {h.get('venue', '')}"
                        )
                    time.sleep(REFERENCE_VERIFY_RATE_LIMIT_SECONDS)
                except Exception:
                    # Verification is best-effort - skip on any failure.
                    pass

            user_content = (
                f"CITING SENTENCE: {citing}\n\n"
                f"REFERENCE DETAILS:\n"
                f"  Title: {title}\n"
                f"  Authors: {ref.get('authors', '')}\n"
                f"  Year: {ref.get('year', '')}\n"
                f"  Venue: {ref.get('venue', '')}\n"
            )
            if scholar_info:
                user_content += f"\nSCHOLAR VERIFICATION: {scholar_info}\n"

            assessment = self._call(
                ctx, self.ASSESS_SYSTEM, user_content,
                max_tokens=REFERENCE_ASSESS_MAX_TOKENS,
            )

            status = "ok"
            for line in assessment.splitlines():
                if line.startswith("STATUS:"):
                    status = line[7:].strip()

            icon = (
                "PASS" if status == "ok"
                else "WARN" if status == "unverifiable"
                else "FAIL"
            )
            if status != "ok":
                issues += 1

            report_lines.append(f"{icon} **[{ref_num}]** {title} ({ref.get('year', '')})")
            report_lines.append(f"   {assessment.strip()}")
            report_lines.append("")

        if issues > REF_QUALITY_MAJOR_THRESHOLD:
            severity = "major"
        elif issues > REF_QUALITY_MODERATE_THRESHOLD:
            severity = "moderate"
        elif issues > 0:
            severity = "minor"
        else:
            severity = "ok"

        return AgentResult(
            agent_id=self.id,
            agent_name=self.name,
            summary=f"{issues} reference issue(s) found across {len(refs)} references",
            findings="\n".join(report_lines),
            severity=severity,
            references_found=[],
            elapsed=time.time() - t0,
            top_issues=[
                f"Reference [{r.get('ref_num', '?')}]: {r.get('title', '')[:60]}"
                for r in refs[:3] if r.get("title")
            ],
        )
