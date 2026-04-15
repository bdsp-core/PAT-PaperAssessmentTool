"""
Command-line entry point for the PAT (Paper Assessment Tool) review pipeline.

After installation (``pip install -e .``) the ``pat`` console script resolves
to :func:`main`.  The same entry point is available via ``python -m pat``.

Usage examples
--------------
Full review (Ollama, default model)::

    pat paper.txt

Review a PDF (text + figures auto-extracted)::

    pat paper.pdf

With code for reproducibility checking::

    pat paper.txt --code-file analysis.py

Run only specific agents::

    pat paper.txt --agents vsnc,intro,paragraphs

Use Anthropic backend::

    pat paper.txt --provider anthropic

Use a different Ollama model::

    pat paper.txt --model llama3.1:8b

List all available agents::

    pat --list-agents

Generate HTML report too::

    pat paper.txt --html

Dry run (show what would run, don't call the model)::

    pat paper.txt --dry-run

Reports are saved to ``reports/review_<paper>_YYYYMMDD_HHMMSS.md`` (and
``.html`` when ``--html`` is passed).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pat.agents import (
    AGENT_REGISTRY,
    ALL_AGENTS,
    AgentResult,
    Context,
    create_ref_backend,
)
from pat.checkpoint import (
    checkpoint_exists,
    clear_checkpoint,
    clear_edit_checkpoint,
    load_checkpoint,
    save_checkpoint,
    save_edit_checkpoint,
)
from pat.diff import compare_reviews, format_revision_progress
from pat.parser import parse_sections
from pat.providers import PROVIDER_DEFAULTS, OllamaProvider, create_provider
from pat.report import (
    write_annotated_manuscript,
    write_html_report,
    write_markdown_report,
)


# ---------------------------------------------------------------------------
# Optional Rich UI (graceful fallback to plain ANSI output)
# ---------------------------------------------------------------------------

try:
    from pat.ui import (
        console,
        create_streaming_callback,
        print_agent_done,
        print_agent_start,
        print_completion,
        print_header,
        print_phase_banner,
        print_summary_table,
        print_watch_dashboard,
    )
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ---------------------------------------------------------------------------
# PDF support
# ---------------------------------------------------------------------------

# Figures smaller than this in both axes are treated as icons / bullets.
_MIN_FIGURE_EDGE_PX = 100

# Page rendered as a fallback when this many vector drawings are present
# but no bitmap figures were captured.
_MIN_VECTOR_DRAWINGS_TO_RENDER = 10

# DPI for rendered vector-drawing fallback pages.
_FALLBACK_RENDER_DPI = 150

# Aspect ratio above which we assume an artifact (banner, logo).
_ARTIFACT_ASPECT_RATIO = 4.0

# Heuristic for first-page publisher logos / cover images.
_FIRST_PAGE_LOGO_MAX_EDGE = 300

# Variance threshold for near-uniform (likely corrupted) images.
_NEAR_UNIFORM_STDEV_THRESHOLD = 15
_ARTIFACT_SAMPLE_MIN = 100
_ARTIFACT_SAMPLE_MAX = 500


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract and concatenate plain text from every page of ``pdf_path``."""
    import fitz
    doc = fitz.open(pdf_path)
    return "\n\n".join(page.get_text() for page in doc)


def _is_artifact_image(pix, page_num: int) -> bool:
    """Return ``True`` if ``pix`` looks like a logo, banner, or corrupt image."""
    w, h = pix.width, pix.height
    ratio = max(w, h) / max(min(w, h), 1)

    if ratio > _ARTIFACT_ASPECT_RATIO:
        return True
    if (page_num == 0
            and w < _FIRST_PAGE_LOGO_MAX_EDGE
            and h < _FIRST_PAGE_LOGO_MAX_EDGE):
        return True

    samples = pix.samples
    if len(samples) > _ARTIFACT_SAMPLE_MIN:
        step = max(1, len(samples) // _ARTIFACT_SAMPLE_MAX)
        vals = [
            samples[i]
            for i in range(0, min(len(samples), _ARTIFACT_SAMPLE_MAX * step), step)
        ]
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        if variance ** 0.5 < _NEAR_UNIFORM_STDEV_THRESHOLD:
            return True

    return False


def extract_figures_from_pdf(pdf_path: str, output_dir: str) -> list[str]:
    """Extract figures from ``pdf_path`` into ``output_dir`` and return paths."""
    import fitz
    doc = fitz.open(pdf_path)
    paths: list[str] = []
    pages_with_images: set[int] = set()

    # Pass 1: extract embedded bitmap images.
    for page_num, page in enumerate(doc):
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n >= 5:
                # CMYK / CMYK+alpha -> RGB so PIL / vision models can read it.
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if pix.width < _MIN_FIGURE_EDGE_PX or pix.height < _MIN_FIGURE_EDGE_PX:
                continue
            if _is_artifact_image(pix, page_num):
                continue
            path = os.path.join(
                output_dir, f"fig_p{page_num + 1}_{img_idx + 1}.png"
            )
            pix.save(path)
            paths.append(path)
            pages_with_images.add(page_num)

    # Pass 2: render any page rich in vector drawings that yielded no bitmap.
    for page_num, page in enumerate(doc):
        if page_num in pages_with_images:
            continue
        drawings = page.get_drawings()
        if len(drawings) < _MIN_VECTOR_DRAWINGS_TO_RENDER:
            continue
        pix = page.get_pixmap(dpi=_FALLBACK_RENDER_DPI)
        path = os.path.join(output_dir, f"fig_p{page_num + 1}_render.png")
        pix.save(path)
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# Plain-ANSI fallback UI (used when Rich is not installed)
# ---------------------------------------------------------------------------

def _plain_print_header(paper_name, model, provider, num_agents, word_count):
    print(f"\n  Paper: {paper_name}  (~{word_count:,} words)")
    print(f"  Model: {provider} / {model}")
    print(f"  Agents: {num_agents}\n")


def _plain_phase_banner(phase_num, phase_name, agent_names):
    print(f"\n{'-' * 60}")
    print(f"  Phase {phase_num} - {phase_name}")
    print(f"{'-' * 60}")


def _plain_agent_start(name):
    print(f"  > {name} ...", end="", flush=True)


def _plain_agent_done(name, severity, elapsed, summary):
    cols = shutil.get_terminal_size().columns
    print(f"\r  [{severity.upper()}]  {name}  ({elapsed:.1f}s)")
    avail = max(20, cols - 7)
    trunc = (
        summary if len(summary) <= avail
        else summary[:avail - 3].rsplit(" ", 1)[0] + "..."
    )
    print(f"       {trunc}")


def _plain_summary(results):
    cols = shutil.get_terminal_size().columns
    print(f"\n{'=' * 60}")
    for r in results:
        prefix = f"  [{r.severity.upper():8s}]  {r.agent_name}: "
        avail = max(20, cols - len(prefix))
        s = r.summary
        trunc = (
            s if len(s) <= avail
            else s[:avail - 3].rsplit(" ", 1)[0] + "..."
        )
        print(f"{prefix}{trunc}")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Anthropic pricing per 1M tokens.  Maintained by API model-family prefix so
# new revisions of each family inherit the existing rate automatically.
_ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (0.80, 4.0),
}

# Rough English-prose conversion from words to tokens.
_TOKENS_PER_WORD = 1.3

# Ballpark system-prompt overhead added to every call.
_SYSTEM_PROMPT_TOKEN_OVERHEAD = 500

# Default reply budget used by agents unless they override it.
_DEFAULT_MAX_OUTPUT_TOKENS = 2048

# Orchestrator input size - it consumes structured summaries, not raw text.
_ORCHESTRATOR_INPUT_TOKENS = 2000


def _estimate_tokens(text: str) -> int:
    """Rough English token estimate: ``~1.3`` tokens per whitespace word."""
    return int(len(text.split()) * _TOKENS_PER_WORD)


def _estimate_cost(
    selected_agents,
    paper_text: str,
    sections: dict,
    provider_name: str,
    model: str,
    config: dict,
) -> None:
    """Print a per-agent token / cost breakdown without calling the model."""
    from pat.parser import get_section, get_sections_combined

    total_input = 0
    total_output = 0
    rows: list[tuple[str, int, int]] = []

    for agent in selected_agents:
        # Estimate input based on what each agent actually receives.
        if agent.id in ("vsnc", "intro"):
            text = get_sections_combined(sections, ["abstract", "introduction"])
        elif agent.id == "discussion":
            text = get_sections_combined(
                sections, ["introduction", "discussion", "conclusion"]
            )
        elif agent.id in ("reproducibility", "statistics"):
            text = get_sections_combined(sections, ["methods", "results"])
        elif agent.id == "methods_completeness":
            text = get_section(sections, "methods")
        elif agent.id == "abstract":
            text = get_section(sections, "abstract")
        elif agent.id == "metrics":
            rows.append((agent.name, 0, 0))
            continue
        elif agent.id == "checklist":
            text = get_sections_combined(
                sections, ["abstract", "introduction", "conclusion"]
            )
        elif agent.id == "orchestrator":
            input_tok = _ORCHESTRATOR_INPUT_TOKENS
            output_tok = _DEFAULT_MAX_OUTPUT_TOKENS
            total_input += input_tok
            total_output += output_tok
            rows.append((agent.name, input_tok, output_tok))
            continue
        else:
            text = paper_text

        input_tok = _SYSTEM_PROMPT_TOKEN_OVERHEAD + _estimate_tokens(text)
        output_tok = _DEFAULT_MAX_OUTPUT_TOKENS
        total_input += input_tok
        total_output += output_tok
        rows.append((agent.name, input_tok, output_tok))

    print(f"\n  Token Estimate ({len(selected_agents)} agents)")
    print(f"  {'-' * 55}")
    for name, inp, out in rows:
        print(f"  {name:35s}  in:{inp:>7,}  out:{out:>7,}")
    print(f"  {'-' * 55}")
    print(f"  {'TOTAL':35s}  in:{total_input:>7,}  out:{total_output:>7,}")

    if provider_name == "anthropic":
        pricing = None
        for prefix, rates in _ANTHROPIC_PRICING.items():
            if model and prefix in model:
                pricing = rates
                break
        if not pricing:
            pricing = _ANTHROPIC_PRICING.get("claude-sonnet-4", (3.0, 15.0))

        input_cost = (total_input / 1_000_000) * pricing[0]
        output_cost = (total_output / 1_000_000) * pricing[1]
        total_cost = input_cost + output_cost
        print(f"\n  Estimated Anthropic cost: ${total_cost:.2f}")
        print(
            f"    Input:  ${input_cost:.2f} "
            f"({total_input:,} tokens @ ${pricing[0]}/1M)"
        )
        print(
            f"    Output: ${output_cost:.2f} "
            f"({total_output:,} tokens @ ${pricing[1]}/1M)"
        )
    else:
        print(f"\n  Provider: {provider_name} (local - no API cost)")
    print()


# ---------------------------------------------------------------------------
# Phase execution helpers
# ---------------------------------------------------------------------------

def _build_context(
    agent,
    paper_text: str,
    code_text: str,
    figure_paths: list[str],
    prior_results: dict,
    config: dict,
    provider,
    sections: dict,
    ref_backend,
    on_chunk=None,
) -> Context:
    return Context(
        paper_text=paper_text,
        code_text=code_text,
        figures=figure_paths,
        prior_results=prior_results,
        config=config,
        provider=provider,
        sections=sections,
        on_chunk=on_chunk,
        ref_backend=ref_backend,
    )


def _safe_run_agent(agent, ctx: Context) -> AgentResult:
    """Invoke ``agent.run(ctx)`` and convert any exception into a placeholder result."""
    try:
        return agent.run(ctx)
    except Exception as e:
        return AgentResult(
            agent_id=agent.id,
            agent_name=agent.name,
            summary=f"Agent error: {e}",
            findings=f"Error:\n\n```\n{e}\n```",
            severity="ok",
            references_found=[],
            elapsed=0.0,
        )


def _run_phase_parallel(
    agents,
    paper_text,
    code_text,
    figure_paths,
    prior_results,
    config,
    provider,
    sections,
    max_workers,
    output_dir,
    ref_backend=None,
    verbose=False,
) -> list[AgentResult]:
    """Run a list of independent agents concurrently using a thread pool."""
    results: list[AgentResult] = []

    def run_agent(agent):
        ctx = _build_context(
            agent, paper_text, code_text, figure_paths, prior_results,
            config, provider, sections, ref_backend,
        )
        return _safe_run_agent(agent, ctx)

    if HAS_RICH:
        from rich.progress import (
            Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
        )
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            TextColumn("[dim]{task.fields[status]}"),
            TimeElapsedColumn(),
            console=console,
        )
        task_map: dict[str, object] = {}
        with progress:
            for agent in agents:
                task_map[agent.id] = progress.add_task(
                    agent.name, status="running...", total=None
                )

            def wrap(agent):
                result = run_agent(agent)
                progress.update(
                    task_map[agent.id],
                    status=f"[{result.severity}] {result.elapsed:.1f}s",
                )
                progress.stop_task(task_map[agent.id])
                return result

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(wrap, a): a for a in agents}
                for fut in as_completed(futures):
                    results.append(fut.result())
    else:
        print(
            f"  Running {len(agents)} agents in parallel "
            f"({max_workers} workers)..."
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(run_agent, a): a for a in agents}
            for fut in as_completed(futures):
                result = fut.result()
                _plain_agent_done(
                    result.agent_name, result.severity,
                    result.elapsed, result.summary,
                )
                results.append(result)

    return results


# ---------------------------------------------------------------------------
# CLI construction
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser; kept separate for clarity and testing."""
    parser = argparse.ArgumentParser(
        description="Multi-agent scientific paper review pipeline (PAT)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "paper", nargs="?",
        help="Path to paper file (.txt, .md, .tex, .pdf)",
    )
    parser.add_argument(
        "--code-file", "-c",
        help="Path to code / data file (enables reproducibility agent)",
    )
    parser.add_argument(
        "--figures", "-f", nargs="*",
        help="Paths to figure image files for multimodal analysis",
    )
    parser.add_argument(
        "--agents", "-a",
        help="Comma-separated agent IDs to run (default: all)",
    )
    parser.add_argument(
        "--no-scholar", action="store_true",
        help="Skip agents that query literature search backends",
    )
    parser.add_argument(
        "--output-dir", "-o", default="reports",
        help="Directory for report files (default: reports/)",
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Also generate an HTML report (opens in browser)",
    )

    # Provider / model.
    parser.add_argument(
        "--provider", "-p", default="ollama",
        choices=["ollama", "anthropic"],
        help="LLM provider (default: ollama)",
    )
    parser.add_argument(
        "--model", "-m", default=None,
        help="Model name (default depends on provider)",
    )
    parser.add_argument(
        "--ollama-host",
        help="Ollama server URL (default: http://localhost:11434)",
    )

    # Checkpoint / resume.
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint (if available)",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Ignore checkpoints and start a fresh review",
    )
    parser.add_argument(
        "--fresh-edits", action="store_true",
        help=(
            "Re-run Phase 4 (figure edit loop) from scratch while "
            "preserving Phase 1-3 agent results"
        ),
    )

    # Revision comparison.
    parser.add_argument(
        "--compare",
        help="Path to a previous review report (.md) to compare against",
    )

    # Watch mode (metrics-only live dashboard).
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch paper file for changes, re-run metrics live",
    )

    parser.add_argument(
        "--config", default=None,
        help=(
            "Path to journal config JSON "
            "(see examples/review_config.json for a template)"
        ),
    )

    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="Enter interactive rewrite mode after review",
    )

    parser.add_argument(
        "--parallel", type=int, default=None, metavar="N",
        help=(
            "Run Phase 1 agents in parallel with N workers "
            "(default: 3 for ollama, 5 for anthropic)"
        ),
    )

    parser.add_argument(
        "--estimate", action="store_true",
        help="Show estimated token/cost breakdown without running agents",
    )

    parser.add_argument(
        "--annotate", action="store_true",
        help=(
            "Generate an annotated copy of the manuscript "
            "with inline comments"
        ),
    )

    parser.add_argument(
        "--ref-backend",
        choices=["pubmed", "biorxiv", "pubmed+biorxiv", "none"],
        default="pubmed",
        help="Reference search backend (default: pubmed)",
    )

    # Figure edit loop.
    parser.add_argument(
        "--fig-scripts", type=str, default=None,
        help=(
            "Path to directory containing figure-generating Python scripts "
            "(enables Phase 4 figure improvement loop)"
        ),
    )
    parser.add_argument(
        "--fig-dir", type=str, default=None,
        help=(
            "Path to directory containing rendered figure images "
            "(used with --fig-scripts for the edit loop)"
        ),
    )
    parser.add_argument(
        "--coder-model", type=str,
        default="qwen3-coder-next:q8_0",
        help=(
            "Ollama model for figure code rewriting "
            "(default: qwen3-coder-next:q8_0)"
        ),
    )

    # Utility flags.
    parser.add_argument(
        "--list-agents", action="store_true",
        help="Print all agent IDs and descriptions, then exit",
    )
    parser.add_argument(
        "--list-models", action="store_true",
        help="List available Ollama models, then exit",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check provider connectivity, then exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would run without calling the model",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print full findings to stdout",
    )

    return parser


# ---------------------------------------------------------------------------
# Utility subcommands (exit after running)
# ---------------------------------------------------------------------------

def _handle_list_agents() -> None:
    if HAS_RICH:
        from rich.table import Table
        table = Table(
            title="Available Agents", show_header=True,
            border_style="bright_blue",
        )
        table.add_column("Phase", style="bold", width=7)
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("Description")
        table.add_column("Notes", style="dim")
        for a in ALL_AGENTS:
            notes: list[str] = []
            if a.needs_code:
                notes.append("needs --code-file")
            if a.needs_scholar:
                notes.append("uses literature search")
            table.add_row(
                str(a.priority), a.id, a.name,
                a.description, ", ".join(notes),
            )
        console.print(table)
    else:
        for a in ALL_AGENTS:
            print(f"  Phase {a.priority}  {a.id:24s}  {a.description}")


def _handle_list_models(host: str | None) -> None:
    try:
        models = OllamaProvider.list_models(host=host)
    except Exception as e:
        print(f"Error connecting to Ollama: {e}", file=sys.stderr)
        print("Is Ollama running? Start it with: ollama serve", file=sys.stderr)
        sys.exit(1)

    if HAS_RICH:
        from rich.table import Table
        table = Table(
            title="Available Ollama Models", border_style="bright_blue",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Model", style="bold cyan")
        for i, m in enumerate(models, 1):
            table.add_row(str(i), m)
        console.print(table)
    else:
        for m in models:
            print(f"  {m}")


def _handle_check(args) -> None:
    if args.provider == "ollama":
        try:
            OllamaProvider.check_connection(host=args.ollama_host)
            models = OllamaProvider.list_models(host=args.ollama_host)
            model = args.model or PROVIDER_DEFAULTS["ollama"]
            found = any(model in m for m in models)
            status = "available" if found else "NOT FOUND"
            print(f"  Ollama: connected ({len(models)} models)")
            print(f"  Model '{model}': {status}")
            if not found:
                print(f"  Pull it with: ollama pull {model}")
                sys.exit(1)
        except Exception as e:
            print(f"  Ollama: NOT reachable ({e})")
            print("  Start with: ollama serve")
            sys.exit(1)
    elif args.provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            print(f"  Anthropic: API key set (starts with {api_key[:8]}...)")
        else:
            print("  Anthropic: ANTHROPIC_API_KEY not set")
            sys.exit(1)
    print("  All checks passed.")


# ---------------------------------------------------------------------------
# Manuscript loading
# ---------------------------------------------------------------------------

def _load_manuscript(args, argparser) -> tuple[str, str, list[str], bool, str | None]:
    """Resolve the paper input and return ``(paper_path, paper_text, figure_paths, figure_only, temp_dir)``."""
    figure_only = False
    temp_dir: str | None = None

    if not args.paper and args.fig_dir:
        figure_only = True
        paper_path = "(figures only)"
        paper_text = ""
        fig_dir_arg = Path(args.fig_dir)
        figure_paths = sorted(str(p) for p in fig_dir_arg.glob("*.png"))
        if not figure_paths:
            print(
                f"Error: no PNG files found in {args.fig_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        return paper_path, paper_text, figure_paths, figure_only, temp_dir

    if not args.paper:
        argparser.print_help()
        sys.exit(1)

    paper_path = args.paper
    if not Path(paper_path).exists():
        print(f"Error: paper file not found: {paper_path}", file=sys.stderr)
        sys.exit(1)

    figure_paths = list(args.figures or [])

    if paper_path.lower().endswith(".pdf"):
        try:
            paper_text = extract_text_from_pdf(paper_path)
            temp_dir = tempfile.mkdtemp(prefix="pat_figs_")
            pdf_figures = extract_figures_from_pdf(paper_path, temp_dir)
            figure_paths.extend(pdf_figures)
            if pdf_figures and HAS_RICH:
                console.print(
                    f"  [dim]Extracted {len(pdf_figures)} figure(s) "
                    f"from PDF[/dim]"
                )
        except ImportError:
            print(
                "Error: pymupdf required for PDF support. "
                "Install with: pip install pymupdf",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        paper_text = Path(paper_path).read_text(encoding="utf-8")

    return paper_path, paper_text, figure_paths, figure_only, temp_dir


def _load_config(config_path: str | None) -> dict:
    if not config_path or not Path(config_path).exists():
        return {}
    import json
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if HAS_RICH:
        jname = config.get("journal_name", "custom")
        console.print(f"  [dim]Journal config: {jname}[/dim]")
    return config


# ---------------------------------------------------------------------------
# Agent selection
# ---------------------------------------------------------------------------

def _select_agents(args, figure_only: bool):
    """Return the agents to run, sorted by phase priority."""
    if args.agents:
        ids = [x.strip() for x in args.agents.split(",")]
        unknown = [i for i in ids if i not in AGENT_REGISTRY]
        if unknown:
            print(f"Unknown agent IDs: {', '.join(unknown)}", file=sys.stderr)
            print("Run --list-agents to see valid IDs.")
            sys.exit(1)
        selected = [AGENT_REGISTRY[i] for i in ids]
    elif figure_only:
        selected = [a for a in ALL_AGENTS if a.id.startswith("fig_")]
    else:
        selected = list(ALL_AGENTS)

    if args.no_scholar:
        selected = [a for a in selected if not a.needs_scholar]

    selected.sort(key=lambda a: a.priority)
    return selected


# ---------------------------------------------------------------------------
# Provider setup
# ---------------------------------------------------------------------------

def _setup_provider(args, model: str):
    """Construct the LLM provider, exiting on fatal configuration problems."""
    provider_kwargs: dict[str, str] = {}
    if args.provider == "ollama":
        if args.ollama_host:
            provider_kwargs["host"] = args.ollama_host
    elif args.provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "Error: ANTHROPIC_API_KEY environment variable not set.",
                file=sys.stderr,
            )
            sys.exit(1)
        provider_kwargs["api_key"] = api_key

    try:
        return create_provider(args.provider, model=model, **provider_kwargs)
    except Exception as e:
        print(f"Error creating provider: {e}", file=sys.stderr)
        if args.provider == "ollama":
            print(
                "Is Ollama running? Start it with: ollama serve",
                file=sys.stderr,
            )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------

_PHASE_NAMES: dict[int, str] = {
    0: "Programmatic Analysis",
    1: "Independent Agents",
    2: "Whole-Document Agents",
    3: "Synthesis",
    4: "Figure Improvement Loop",
}


def _run_all_phases(
    selected,
    paper_path: str,
    paper_text: str,
    code_text: str,
    figure_paths: list[str],
    sections: dict,
    config: dict,
    provider,
    ref_backend,
    output_dir: Path,
    parallel_workers: int | None,
    results: list[AgentResult],
    prior_results: dict[str, AgentResult],
    completed_ids: set[str],
    verbose: bool,
) -> None:
    """Run every selected agent, grouped by phase, updating ``results`` in place."""
    phase_groups: dict[int, list] = defaultdict(list)
    for agent in selected:
        if agent.id not in completed_ids:
            phase_groups[agent.priority].append(agent)

    for phase_num in sorted(phase_groups.keys()):
        phase_agents = phase_groups[phase_num]
        if not phase_agents:
            continue

        agent_names = [a.name for a in phase_agents]
        phase_name = _PHASE_NAMES.get(phase_num, f"Phase {phase_num}")
        if HAS_RICH:
            print_phase_banner(phase_num, phase_name, agent_names)
        else:
            _plain_phase_banner(phase_num, phase_name, agent_names)

        # Phase 1 is the only phase safe to parallelise; later phases have
        # ordering dependencies through prior_results.
        use_parallel = bool(
            parallel_workers and parallel_workers > 1
            and phase_num == 1 and len(phase_agents) > 1
        )

        if use_parallel:
            phase_results = _run_phase_parallel(
                phase_agents, paper_text, code_text, figure_paths,
                prior_results, config, provider, sections,
                max_workers=parallel_workers,
                output_dir=output_dir, ref_backend=ref_backend,
                verbose=verbose,
            )
            for result in phase_results:
                results.append(result)
                prior_results[result.agent_id] = result
                if HAS_RICH:
                    print_agent_done(
                        result.agent_name, result.severity,
                        result.elapsed, result.summary,
                    )
            save_checkpoint(
                paper_path, paper_text, provider.model_name,
                results, output_dir,
            )
        else:
            _run_phase_sequential(
                phase_agents, paper_path, paper_text, code_text, figure_paths,
                prior_results, config, provider, sections, ref_backend,
                output_dir, results, verbose,
            )


def _run_phase_sequential(
    phase_agents,
    paper_path, paper_text, code_text, figure_paths,
    prior_results, config, provider, sections, ref_backend,
    output_dir, results, verbose,
) -> None:
    """Run agents one after another, streaming output when Rich is available."""
    for agent in phase_agents:
        on_chunk = None
        live_ctx = None

        if HAS_RICH and agent.priority > 0:
            live_ctx, on_chunk = create_streaming_callback(agent.name)
        elif HAS_RICH:
            print_agent_start(agent.name)
        else:
            _plain_agent_start(agent.name)

        ctx = _build_context(
            agent, paper_text, code_text, figure_paths, prior_results,
            config, provider, sections, ref_backend, on_chunk=on_chunk,
        )

        try:
            if live_ctx:
                with live_ctx:
                    result = agent.run(ctx)
            else:
                result = agent.run(ctx)
        except Exception as e:
            result = AgentResult(
                agent_id=agent.id, agent_name=agent.name,
                summary=f"Agent error: {e}",
                findings=f"This agent encountered an error:\n\n```\n{e}\n```",
                severity="ok", references_found=[], elapsed=0.0,
            )

        results.append(result)
        prior_results[agent.id] = result

        if HAS_RICH:
            print_agent_done(
                agent.name, result.severity,
                result.elapsed, result.summary,
            )
        else:
            _plain_agent_done(
                agent.name, result.severity,
                result.elapsed, result.summary,
            )

        if verbose and result.findings:
            if HAS_RICH:
                from rich.markdown import Markdown
                from rich.padding import Padding
                truncated = "\n".join(result.findings.splitlines()[:30])
                console.print(Padding(Markdown(truncated), (0, 6)))
            else:
                for line in result.findings.splitlines()[:30]:
                    print(f"      {line}")

        # Checkpoint after every agent so crashes are recoverable.
        save_checkpoint(
            paper_path, paper_text, provider.model_name,
            results, output_dir,
        )


def _run_figure_edit_loop(
    args,
    provider,
    sections,
    paper_text,
    config,
    output_dir,
    paper_path,
):
    """Execute Phase 4 (figure improvement) if requested; return edit_results or ``None``."""
    if not (args.fig_scripts and args.fig_dir):
        return None

    fig_scripts_dir = Path(args.fig_scripts)
    fig_dir_path = Path(args.fig_dir)
    if not (fig_scripts_dir.exists() and fig_dir_path.exists()):
        return None

    from pat.figure_editor import run_edit_loop

    figure_agent_ids = (
        "fig_story", "fig_composition", "fig_color",
        "fig_typography", "fig_format", "fig_caption",
        "fig_statistics", "fig_consistency",
    )
    figure_agents = [
        AGENT_REGISTRY[aid] for aid in figure_agent_ids
        if aid in AGENT_REGISTRY
    ]

    scripts = sorted(fig_scripts_dir.glob("figure*.py"))
    if not (scripts and figure_agents):
        return None

    if HAS_RICH:
        print_phase_banner(
            4, _PHASE_NAMES[4],
            [f"{len(scripts)} scripts, up to 5 iterations"],
        )
    else:
        print(f"\n  Phase 4: {_PHASE_NAMES[4]}")
        print(f"    Scripts: {len(scripts)}")

    source_repo_dir = fig_scripts_dir.parent

    ui_cbs: dict[str, object] = {}
    if HAS_RICH:
        from pat.ui import (
            print_edit_iteration,
            print_edit_retry,
            print_edit_review_done,
            print_edit_review_start,
            print_edit_script_done,
            print_edit_script_start,
            print_edit_summary,
        )
        ui_cbs = dict(
            on_iteration=print_edit_iteration,
            on_script_start=print_edit_script_start,
            on_retry=print_edit_retry,
            on_script_done=print_edit_script_done,
            on_review_start=print_edit_review_start,
            on_review_done=print_edit_review_done,
            create_streaming_cb=create_streaming_callback,
        )
    else:
        ui_cbs["on_status"] = lambda msg: print(f"    {msg}")

    try:
        edit_results = run_edit_loop(
            fig_scripts=scripts,
            fig_dir=fig_dir_path,
            source_repo_dir=source_repo_dir,
            output_dir=output_dir / "figure_edits",
            review_provider=provider,
            figure_agents=figure_agents,
            config=config,
            sections=sections,
            paper_text=paper_text,
            coder_model=args.coder_model,
            **ui_cbs,
        )
        if HAS_RICH:
            successes = sum(
                1 for itr in edit_results.iterations for r in itr if r.success
            )
            total_scripts = sum(len(itr) for itr in edit_results.iterations)
            # Note: we use print_edit_summary via the earlier ui import path.
            from pat.ui import print_edit_summary  # local to keep import lazy
            print_edit_summary(
                edit_results.total_iterations,
                edit_results.total_elapsed,
                successes, total_scripts,
            )
        else:
            print(
                f"    Done: {edit_results.total_iterations} iterations, "
                f"{edit_results.total_elapsed:.1f}s"
            )
        save_edit_checkpoint(paper_path, edit_results, output_dir)
        return edit_results
    except Exception as e:
        if HAS_RICH:
            console.print(f"  [red]Figure edit loop error: {e}[/red]")
        else:
            print(
                f"    ERROR: Figure edit loop failed: {e}", file=sys.stderr,
            )
        return None


# ---------------------------------------------------------------------------
# Report dispatch
# ---------------------------------------------------------------------------

def _write_reports(
    args,
    results,
    paper_path,
    paper_text,
    sections,
    config,
    provider,
    output_dir,
    agreement_data,
    edit_results,
) -> tuple[Path, Path | None, Path | None, dict | None]:
    """Write markdown, optional HTML, and optional annotated manuscript outputs."""
    comparison_data_for_html = None
    if args.compare:
        try:
            comparison_data_for_html = compare_reviews(args.compare, results)
            progress = format_revision_progress(comparison_data_for_html)
            if HAS_RICH:
                from rich.markdown import Markdown
                console.print()
                console.print(Markdown(progress))
            else:
                print(progress)
        except Exception as e:
            print(
                f"Warning: could not compare with {args.compare}: {e}",
                file=sys.stderr,
            )
            comparison_data_for_html = None

    md_path = write_markdown_report(
        results, paper_path, provider.model_name, args.provider, output_dir,
        agreement=agreement_data, config=config, edit_results=edit_results,
    )

    if comparison_data_for_html:
        progress = format_revision_progress(comparison_data_for_html)
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n{progress}")

    html_path: Path | None = None
    if args.html:
        html_path = write_html_report(
            results, paper_path, provider.model_name, args.provider,
            output_dir, open_browser=True, agreement=agreement_data,
            config=config, edit_results=edit_results,
            comparison=comparison_data_for_html,
            old_report_path=args.compare if comparison_data_for_html else None,
        )

    annotated_path: Path | None = None
    if args.annotate:
        annotated_path = write_annotated_manuscript(
            results, paper_text, sections, output_dir,
        )
        if HAS_RICH:
            console.print(
                f"  [dim]Annotated manuscript: {annotated_path}[/dim]"
            )
        else:
            print(f"  Annotated manuscript: {annotated_path}")

    return md_path, html_path, annotated_path, comparison_data_for_html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    argparser = _build_arg_parser()
    args = argparser.parse_args()

    # ---- Utility subcommands ------------------------------------------------
    if args.list_agents:
        _handle_list_agents()
        sys.exit(0)
    if args.list_models:
        _handle_list_models(args.ollama_host)
        sys.exit(0)
    if args.check:
        _handle_check(args)
        sys.exit(0)

    # ---- Load manuscript ----------------------------------------------------
    paper_path, paper_text, figure_paths, figure_only, _temp_dir = (
        _load_manuscript(args, argparser)
    )
    word_count = len(paper_text.split())

    if figure_only:
        sections: dict = {}
    else:
        sections = parse_sections(paper_text)
        section_names = [k for k in sections if k != "full"]
        if section_names and HAS_RICH:
            console.print(
                f"  [dim]Sections detected: {', '.join(section_names)}[/dim]"
            )

    # ---- Journal config (optional) -----------------------------------------
    config = _load_config(args.config)

    # ---- Watch mode (metrics only, no LLM) ---------------------------------
    if args.watch:
        _run_watch_mode(paper_path, config)
        sys.exit(0)

    # ---- Optional code / data file -----------------------------------------
    code_text = ""
    if args.code_file:
        p = Path(args.code_file)
        if not p.exists():
            print(
                f"Warning: code file not found: {args.code_file}",
                file=sys.stderr,
            )
        else:
            code_text = p.read_text(encoding="utf-8")

    # ---- Select agents and estimate cost -----------------------------------
    selected = _select_agents(args, figure_only)
    model = args.model or PROVIDER_DEFAULTS.get(args.provider)

    if args.estimate:
        _estimate_cost(
            selected, paper_text, sections, args.provider, model, config,
        )
        sys.exit(0)

    # ---- Provider + reference backend --------------------------------------
    provider = _setup_provider(args, model)
    provider.set_cacheable_context(paper_text)
    ref_backend = create_ref_backend(args.ref_backend)

    # ---- Header + dry-run short-circuit ------------------------------------
    if HAS_RICH:
        print_header(
            paper_path, provider.model_name, args.provider,
            len(selected), word_count,
        )
    else:
        _plain_print_header(
            paper_path, provider.model_name, args.provider,
            len(selected), word_count,
        )

    if args.dry_run:
        if HAS_RICH:
            console.print(
                "[yellow]  \\[dry-run] No model calls made.[/yellow]\n"
            )
        else:
            print("  [dry-run] No model calls made.\n")
        sys.exit(0)

    # ---- Checkpoint resume --------------------------------------------------
    output_dir = Path(args.output_dir)
    results: list[AgentResult] = []
    prior_results: dict[str, AgentResult] = {}
    completed_ids: set[str] = set()

    # --fresh-edits implies --resume (keep agent results, redo edit loop).
    if args.fresh_edits:
        args.resume = True
        clear_edit_checkpoint(paper_path, output_dir)
        edit_out = output_dir / "figure_edits"
        if edit_out.exists():
            shutil.rmtree(edit_out)

    if not args.fresh and (
        args.resume or checkpoint_exists(paper_path, output_dir)
    ):
        loaded = load_checkpoint(paper_path, paper_text, output_dir)
        if loaded:
            prev_results, prev_model = loaded
            if HAS_RICH:
                console.print(
                    f"\n  [bold yellow]Checkpoint found:[/bold yellow] "
                    f"{len(prev_results)} agent(s) completed "
                    f"(model: {prev_model})"
                )
            else:
                print(
                    f"\n  Checkpoint found: {len(prev_results)} agents completed"
                )
            results = prev_results
            for r in prev_results:
                prior_results[r.agent_id] = r
                completed_ids.add(r.agent_id)

    # ---- Parallel settings --------------------------------------------------
    parallel_workers = args.parallel
    if parallel_workers is None and args.provider == "anthropic":
        parallel_workers = 5  # API calls parallelise safely; local inference does not.

    # ---- Run Phase 0 through Phase 3 ---------------------------------------
    t_start = time.time()
    _run_all_phases(
        selected, paper_path, paper_text, code_text, figure_paths, sections,
        config, provider, ref_backend, output_dir, parallel_workers,
        results, prior_results, completed_ids, args.verbose,
    )

    # ---- Phase 4 (figure edit loop, optional) ------------------------------
    edit_results = _run_figure_edit_loop(
        args, provider, sections, paper_text, config, output_dir, paper_path,
    )

    # ---- Agreement analysis -------------------------------------------------
    agreement_data = None
    if len(results) >= 3:
        try:
            from pat.agreement import compute_agreement
            agreement_data = compute_agreement(results, sections)
        except Exception:
            # Agreement analysis is a nicety; never fail the run for it.
            pass

    total = time.time() - t_start
    sev_counts = dict(Counter(r.severity for r in results))

    if HAS_RICH:
        print_summary_table(results)

    # ---- Write reports ------------------------------------------------------
    md_path, html_path, annotated_path, _cmp = _write_reports(
        args, results, paper_path, paper_text, sections, config,
        provider, output_dir, agreement_data, edit_results,
    )

    # Clear checkpoints on successful completion.
    clear_checkpoint(paper_path, output_dir)
    clear_edit_checkpoint(paper_path, output_dir)

    # ---- Final completion message ------------------------------------------
    if HAS_RICH:
        print_completion(
            total, len(results), str(md_path),
            str(html_path) if html_path else None,
            sev_counts,
        )
    else:
        _plain_summary(results)
        print(f"\n  Time: {total:.1f}s")
        print(f"  Report: {md_path}")
        if html_path:
            print(f"  HTML:   {html_path}")
        print()

    # ---- Optional interactive REPL -----------------------------------------
    if args.interactive:
        interactive_rewrite(paper_text, results, provider)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def _run_watch_mode(paper_path: str, config: dict) -> None:
    """Watch ``paper_path`` and re-run the programmatic metrics on every save."""
    from pat.metrics import compute_metrics

    last_mtime = 0.0
    print(f"  Watching {paper_path} for changes... (Ctrl+C to stop)\n")

    try:
        while True:
            mtime = os.stat(paper_path).st_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                text = Path(paper_path).read_text(encoding="utf-8")
                m = compute_metrics(text, config)
                sections = parse_sections(text)
                section_names = [k for k in sections if k != "full"]

                if HAS_RICH:
                    print_watch_dashboard(m, section_names, paper_path)
                else:
                    print(
                        f"\n  Words: {m['word_count']} | "
                        f"FK: {m['flesch_kincaid_grade']} | "
                        f"Passive: {m['passive_voice_pct']}%"
                    )

            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Watch mode stopped.")


# ---------------------------------------------------------------------------
# Interactive rewrite mode
# ---------------------------------------------------------------------------

def interactive_rewrite(
    paper_text: str,
    results: list[AgentResult],
    provider,
) -> None:
    """Post-review REPL for exploring findings and generating targeted rewrites."""
    if HAS_RICH:
        console.print("\n[bold cyan]Interactive Mode[/bold cyan]")
        console.print(
            "[dim]Commands: rewrite <n>, explain <agent_id>, "
            "fix <section>, quit[/dim]\n"
        )
    else:
        print("\nInteractive Mode")
        print("Commands: rewrite <n>, explain <agent_id>, fix <section>, quit\n")

    numbered_findings = [
        r for r in results if r.findings and r.severity != "ok"
    ]

    while True:
        try:
            cmd = input("pat> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd or cmd == "quit":
            break

        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if action == "rewrite" and arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(numbered_findings):
                finding = numbered_findings[idx]
                print(f"\n  Rewriting based on {finding.agent_name}...\n")
                prompt = (
                    "Based on this review finding, rewrite the problematic "
                    "sections of the paper. Show BEFORE and AFTER for each "
                    "fix.\n\n"
                    f"FINDING:\n{finding.findings[:2000]}\n\n"
                    f"PAPER:\n{paper_text[:3000]}"
                )
                response = provider.call(
                    "You are a scientific writing editor. Provide concrete rewrites.",
                    prompt,
                )
                print(response)
                print()
            else:
                print(f"  Invalid number. Range: 1-{len(numbered_findings)}")

        elif action == "explain":
            matched = [
                r for r in results
                if r.agent_id == arg or arg in r.agent_name.lower()
            ]
            if matched:
                r = matched[0]
                print(f"\n  Getting deeper explanation of {r.agent_name}...\n")
                response = provider.call(
                    "You are a scientific writing expert. Explain this review "
                    "finding in more detail and provide actionable advice.",
                    f"FINDING:\n{r.findings}\n\nPAPER EXCERPT:\n"
                    f"{paper_text[:2000]}",
                )
                print(response)
                print()
            else:
                print(f"  Agent not found: {arg}")

        elif action == "fix":
            section = arg.lower()
            related = [
                r for r in results
                if section in r.agent_name.lower()
                or section in r.findings.lower()[:200]
            ]
            if related:
                print(f"\n  Generating fixes for '{section}'...\n")
                findings_text = "\n\n".join(
                    f"## {r.agent_name}\n{r.findings[:1000]}" for r in related
                )
                response = provider.call(
                    "You are a scientific writing editor. Based on the review "
                    "findings below, provide a comprehensive rewrite of the "
                    "specified section. Show the complete revised text.",
                    f"SECTION TO FIX: {section}\n\n"
                    f"FINDINGS:\n{findings_text}\n\n"
                    f"PAPER:\n{paper_text[:4000]}",
                )
                print(response)
                print()
            else:
                print(f"  No findings related to '{section}'")

        elif action == "list":
            for i, r in enumerate(numbered_findings, 1):
                print(
                    f"  {i}. [{r.severity}] {r.agent_name}: {r.summary[:60]}"
                )
            print()

        else:
            print(
                "  Unknown command. Try: rewrite <n>, explain <id>, "
                "fix <section>, list, quit"
            )


if __name__ == "__main__":
    main()
