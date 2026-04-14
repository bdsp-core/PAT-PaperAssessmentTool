# PAT - Paper Assessment Tool

![PAT-PaperBoy-CrispyPineapple](PaperBoyCrispyPineapplePAT.png)

Multi-agent AI pipeline for reviewing scientific manuscripts before journal
submission. Thirty-one specialized agents evaluate distinct dimensions of
writing quality, methodology, statistics, figures, and positioning - grounded
in established frameworks (VSNC/Winston, Gopen & Swan, Adelson/Freeman,
Strunk & White, Tufte, CONSORT/STROBE/TRIPOD/PRISMA/STARD).

The output is a quantitative **Submission Readiness Score** with a per-dimension
radar chart, inter-agent agreement analysis (Fleiss' kappa), automated literature
positioning via PubMed/bioRxiv, reporting-guideline compliance checking, and a
drafted Response to Reviewers.

**Model-agnostic.** Runs on any Ollama model or Anthropic Claude.
**Free.** Literature search uses the PubMed E-utilities and bioRxiv REST APIs;
no paid keys are required.

## Quick Start

```bash
# One-command run (handles venv, dependencies, model pull)
./run.sh my_paper.pdf

# Or manually
pip install -r requirements.txt
ollama pull qwen3.5:27b-bf16
python run_review.py my_paper.pdf --html
```

## Agents

| # | Phase | Agent | What it checks |
|---|-------|-------|----------------|
| 1 | 0 | **Text Metrics** | Instant readability scores, passive voice %, sentence stats, hedging density (no LLM) |
| 2 | 1 | **VSNC Framework** | Vision - Steps - News - Contributions - 5 S's (slogan, symbol, salient, surprise, story) |
| 3 | 1 | **Introduction Audit** | Adelson formula - Kajiya "dynamite intro" - Freeman tone |
| 4 | 1 | **Sentence Architecture** | Gopen & Swan: stress positions, topic positions, subject-verb proximity, nominalizations |
| 5 | 1 | **Voice & Tense** | Active/passive ratio - Past for methods/results - Present for facts - Tense shifts |
| 6 | 1 | **Conciseness Audit** | Wordy phrases - Nominalizations - Redundancy - Throat-clearing openers |
| 7 | 1 | **Paragraph Quality** | Topic sentences - Unity - Logical flow - Reader-first (Knuth) |
| 8 | 1 | **Acronym Audit** | Every acronym defined before first use - Double-definitions - Post-definition consistency |
| 9 | 1 | **Figures, Tables & Captions** | Coverage - Caption quality - Tufte principles - Multimodal figure analysis |
| 10 | 1 | **Reproducibility Check** | Results traceable to code/data - Methods match implementation *(requires `--code-file`)* |
| 11 | 1 | **Statistical Review** | Appropriate tests - Multiple comparisons - Effect sizes - Sample size justification |
| 12 | 1 | **Methods Completeness** | Protocol detail - Participant criteria - Outcome definitions - Bias controls |
| 13-17 | 1 | **Figure agents (per-figure)** | Story, composition, format, caption, statistical integrity |
| 18 | 2 | **Internal Consistency** | Terminology - Numbers - Claims across abstract/methods/results/discussion |
| 19 | 2 | **Discussion & Related Work** | Positioning in literature - Gaps - Limitations - Strength of conclusion |
| 20 | 2 | **Abstract Quality** | Structure - Completeness - Accuracy vs body text - Word limits |
| 21 | 2 | **Adversarial Reviewer #2** | Overclaiming - Missing baselines - Logical gaps - Statistical concerns |
| 22 | 2 | **Missing References** | Paragraph-by-paragraph scan for uncited claims -> PubMed/bioRxiv search |
| 23 | 2 | **Reference Quality** | Verifies cited refs via PubMed/bioRxiv - Flags wrong papers - Suggests better refs |
| 24 | 2 | **Paper Positioning** | Competitive landscape - Prior art - Must-cite papers - Novelty assessment |
| 25 | 2 | **Reporting Guidelines** | Auto-detects study type -> CONSORT/STROBE/TRIPOD/PRISMA/STARD compliance |
| 26-27 | 2 | **Figure agents (hybrid)** | Color & accessibility, typography (all figures together) |
| 28 | 3 | **Synthesis & Action Plan** | Cross-cutting synthesis -> ranked top-10 action plan + readiness verdict |
| 29 | 3 | **Submission Checklist** | Journal-specific formatting, data/code/ethics statements, author checklist |
| 30 | 3 | **Response to Reviewers** | Auto-generated structured response document from Reviewer #2 findings |
| 31 | 3 | **Cross-Figure Consistency** | Final cross-figure synthesis (palette, typography, panel labels, axis style) |

## Usage

```bash
# Basic review
python run_review.py paper.pdf

# HTML report with radar chart, heatmap, and scoring
python run_review.py paper.pdf --html

# With code for reproducibility checking
python run_review.py paper.pdf --code-file analysis.py

# With explicit figure images for multimodal analysis
python run_review.py paper.pdf --figures fig1.png fig2.png

# Run only specific agents
python run_review.py paper.pdf --agents vsnc,intro,paragraphs,discussion

# Reference search backends
python run_review.py paper.pdf --ref-backend pubmed           # default
python run_review.py paper.pdf --ref-backend biorxiv
python run_review.py paper.pdf --ref-backend pubmed+biorxiv   # most thorough
python run_review.py paper.pdf --ref-backend none             # skip refs

# Use Anthropic Claude (with prompt caching for cost savings)
python run_review.py paper.pdf --provider anthropic

# Use a different Ollama model
python run_review.py paper.pdf --model llama3.1:8b

# Compare with a previous review (tracks revision progress)
python run_review.py paper_v2.pdf --compare reports/review_paper_v1_*.md

# Generate annotated manuscript with inline comments
python run_review.py paper.pdf --annotate

# Estimate token/cost without running
python run_review.py paper.pdf --estimate

# Resume after a crash (checkpoint auto-saved after each agent)
python run_review.py paper.pdf --resume

# Interactive rewrite mode (REPL after review)
python run_review.py paper.pdf --interactive

# Custom journal config
python run_review.py paper.pdf --config review_config.json

# See all agent IDs
python run_review.py --list-agents

# Parallel execution (API providers)
python run_review.py paper.pdf --provider anthropic --parallel 5

# Dry run (show plan without calling model)
python run_review.py paper.pdf --dry-run
```

Reports are saved to `reports/review_<papername>_YYYYMMDD_HHMMSS.md`
(and `.html` with `--html`).

## Pipeline Architecture

```
Phase 0 (instant)   Phase 1 (independent)       Phase 2 (whole-doc)        Phase 3 (synthesis)
-----------------   --------------------------   ---------------------    --------------------
metrics ----------> vsnc                 -+
                    intro                 |
                    sentences             |
                    voice                 |-->  consistency            -+
                    conciseness           |     discussion              |
                    paragraphs            |     abstract                |-->  orchestrator
                    acronyms              |     reviewer2               |     checklist
                    figures_tables        |     missing_refs            |     response
                    reproducibility       |     ref_quality             |
                    statistics            |     positioning             |
                    methods_completeness -+     guidelines             -+
```

## Quantitative Scoring

Every agent returns a **0-100% score** on its dimension. These combine into a
weighted **Submission Readiness Score** that appears in the report header and
as a radar chart in the HTML report. Weights are configurable via
`review_config.json`:

```json
{
    "dimension_weights": {
        "statistics": 1.5,
        "guidelines": 1.5,
        "reviewer2": 1.5,
        "reproducibility": 1.2,
        "methods_completeness": 1.2,
        "acronyms": 0.5,
        "response": 0.3
    }
}
```

## Inter-Agent Agreement Analysis

The agent panel is treated as a set of peer reviewers. The pipeline computes:

- A **section x agent matrix** showing which agents flagged which sections.
- **Fleiss' kappa** for inter-rater reliability.
- **Consensus issues** (three or more agents flag the same section) vs.
  **singleton concerns**.
- **Per-section quality scores** based on weighted agent findings.

This is rendered as a heatmap in the HTML report.

## Paper Positioning (Literature Search)

The Paper Positioning agent performs a two-pass analysis:

1. The LLM extracts key claims and methodology and generates search queries.
2. It searches PubMed and/or bioRxiv and analyses overlap and gaps.

The result is a competitive landscape, potential prior art, must-cite papers,
and positioning suggestions. All searches use free APIs (no keys required).

## Reporting Guideline Compliance

Auto-detects the study type and checks compliance against:

- **CONSORT** (23 items) - Randomised controlled trials
- **STROBE** (22 items) - Observational studies
- **TRIPOD** (21 items) - Prediction models
- **PRISMA** (20 items) - Systematic reviews
- **STARD** (17 items) - Diagnostic accuracy studies

This is useful for catching desk-rejection risks from incomplete guideline
adherence before submission.

## Revision Tracking

Compare successive drafts with quantitative score deltas:

```bash
# First review
python run_review.py paper_v1.pdf --html -o reports/v1

# After revisions, compare
python run_review.py paper_v2.pdf --html --compare reports/v1/review_paper_v1_*.md
```

The output shows what improved, what persists, what regressed, and what's new,
with per-agent score deltas.

## Provider Configuration

### Ollama (default)

Local inference. No API keys needed.

```bash
ollama pull qwen3.5:27b-bf16
python run_review.py paper.pdf
```

Any Ollama model works. Multimodal models (e.g. `llava`, `llama3.2-vision`,
`qwen3.5`) enable figure vision analysis.

### Anthropic

Includes automatic prompt caching: the paper text is cached across agent
calls, reducing cost by roughly 50% for multi-agent reviews.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run_review.py paper.pdf --provider anthropic
python run_review.py paper.pdf --provider anthropic --model claude-sonnet-4-20250514
```

## Checkpoint / Resume

Each agent's results are checkpointed after completion. If the process
crashes you can pick up where you left off:

```bash
python run_review.py paper.pdf --resume    # use the latest checkpoint
python run_review.py paper.pdf --fresh     # ignore checkpoints, start over
```

## Configuring for Your Journal

Edit `review_config.json` with your target journal's specs:

```json
{
    "journal_name": "Nature Medicine",
    "word_limit": 3000,
    "abstract_word_limit": 150,
    "max_figures": 4,
    "citation_style": "numbered superscript",
    "requires_data_availability_statement": true,
    "dimension_weights": {
        "statistics": 1.5,
        "guidelines": 1.5
    }
}
```

## Repository Layout

```
agents/           Agent package: base types, phase modules, reference backends
report/           Report package: markdown, HTML, charts, annotations, summary
run_review.py     Command-line entry point
providers.py      Ollama / Anthropic provider abstraction
parser.py         Scientific-paper section parser
metrics.py        Programmatic text metrics (no LLM)
agreement.py      Inter-agent agreement analysis (Fleiss' kappa)
checkpoint.py     Crash-recovery checkpoints
diff.py           Revision-progress diff across two runs
figure_editor.py  Optional Phase 4 figure improvement loop
ui.py             Rich terminal UI (optional)
run.sh            Convenience wrapper (environment + models + run)
review_config.json Journal configuration template
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) running locally, or an Anthropic API key
- See `requirements.txt`: `ollama`, `anthropic`, `rich`, `markdown`, `pymupdf`,
  `Pillow`, `requests`, `typing_extensions`

## License

MIT
