"""
Agent registry.

``ALL_AGENTS`` is the canonical ordered list of agent instances; ``AGENT_REGISTRY``
maps each agent's ``id`` to that instance for quick lookup by the CLI.

The ordering here dictates phase grouping on the report and the default execution
order when ``--agents`` is not supplied.
"""

from __future__ import annotations

from .base import BaseAgent
from .figure_agents import (
    FigureCaptionAgent,
    FigureColorAgent,
    FigureCompositionAgent,
    FigureCrossConsistencyAgent,
    FigureFormatAgent,
    FigureStatisticsAgent,
    FigureStoryAgent,
    FigureTypographyAgent,
)
from .phase1_content import (
    FiguresTablesAgent,
    MethodsCompletenessAgent,
    ReproducibilityAgent,
    StatisticalReviewAgent,
    TextMetricsAgent,
)
from .phase1_writing import (
    AcronymAgent,
    ConcistnessAgent,
    IntroductionAgent,
    ParagraphQualityAgent,
    SentenceArchitectureAgent,
    VoiceAndTenseAgent,
    VSNCAgent,
)
from .phase2_references import MissingReferencesAgent, ReferenceQualityAgent
from .phase2_synthesis import (
    AbstractQualityAgent,
    ConsistencyAgent,
    DiscussionAgent,
    PaperPositioningAgent,
    ReportingGuidelineAgent,
    ReviewerTwoAgent,
)
from .phase3_final import (
    ChecklistAgent,
    OrchestratorAgent,
    ResponseToReviewersAgent,
)


ALL_AGENTS: list[BaseAgent] = [
    # Phase 0 - instant, no LLM
    TextMetricsAgent(),

    # Phase 1 - independent analysis
    VSNCAgent(),
    IntroductionAgent(),
    SentenceArchitectureAgent(),
    VoiceAndTenseAgent(),
    ConcistnessAgent(),
    ParagraphQualityAgent(),
    AcronymAgent(),
    FiguresTablesAgent(),
    ReproducibilityAgent(),
    StatisticalReviewAgent(),
    MethodsCompletenessAgent(),

    # Phase 1 - per-figure vision agents
    FigureStoryAgent(),
    FigureCompositionAgent(),
    FigureFormatAgent(),
    FigureCaptionAgent(),
    FigureStatisticsAgent(),

    # Phase 2 - whole-document synthesis
    ConsistencyAgent(),
    DiscussionAgent(),
    AbstractQualityAgent(),
    ReviewerTwoAgent(),
    MissingReferencesAgent(),
    ReferenceQualityAgent(),
    PaperPositioningAgent(),
    ReportingGuidelineAgent(),

    # Phase 2 - hybrid figure agents (all figures at once)
    FigureColorAgent(),
    FigureTypographyAgent(),

    # Phase 3 - final synthesis
    OrchestratorAgent(),
    ChecklistAgent(),
    ResponseToReviewersAgent(),

    # Phase 3 - cross-figure synthesis
    FigureCrossConsistencyAgent(),
]


AGENT_REGISTRY: dict[str, BaseAgent] = {a.id: a for a in ALL_AGENTS}
