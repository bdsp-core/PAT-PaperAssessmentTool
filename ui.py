"""
Rich terminal UI for the PAT review pipeline.

Builds panels, tables, and a live-streaming view of LLM output.  Every
rendering function in this module is optional - ``run_review`` falls back
to plain ANSI output when Rich is not installed.

The streaming renderer does lightweight markdown-to-Rich-markup conversion
at stream time because Rich's own ``Markdown`` renderer struggles with
partial input.
"""

from __future__ import annotations

import re
import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

SEVERITY_STYLE = {
    "ok": "green",
    "minor": "cyan",
    "moderate": "yellow",
    "major": "red",
}

SEVERITY_ICON = {
    "ok": "[green]OK[/green]",
    "minor": "[cyan]MINOR[/cyan]",
    "moderate": "[yellow]MODERATE[/yellow]",
    "major": "[red]MAJOR[/red]",
}


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text at a word boundary, appending '...' if needed."""
    if max_chars < 4:
        return text[:max_chars]
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars - 3]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


def print_header(paper_name: str, model: str, provider: str,
                 num_agents: int, word_count: int) -> None:
    """Print the startup banner."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column()
    grid.add_row("Paper", paper_name)
    grid.add_row("Words", f"~{word_count:,}")
    grid.add_row("Provider", f"{provider} / {model}")
    grid.add_row("Agents", str(num_agents))

    console.print()
    console.print(Panel(
        grid,
        title="[bold]Paper Review Pipeline[/bold]",
        border_style="bright_blue",
        padding=(1, 2),
    ))


def print_phase_banner(phase_num: int, phase_name: str,
                       agent_names: list[str]) -> None:
    """Print a phase separator."""
    agents_str = " | ".join(agent_names)
    console.print()
    console.rule(f"[bold]Phase {phase_num} — {phase_name}[/bold]",
                 style="bright_blue")
    console.print(f"  [dim]{agents_str}[/dim]")
    console.print()


def print_agent_start(agent_name: str) -> None:
    """Show that an agent is starting."""
    console.print(f"  [bold cyan]>[/bold cyan] {agent_name} ...", end="")


def print_agent_done(agent_name: str, severity: str, elapsed: float,
                     summary: str) -> None:
    """Show agent completion."""
    icon = SEVERITY_ICON.get(severity, severity)
    console.print(f"\r  {icon}  [bold]{agent_name}[/bold]  "
                  f"[dim]({elapsed:.1f}s)[/dim]")
    avail = max(20, console.width - 7)
    console.print(f"       [dim]{_truncate(summary, avail)}[/dim]")


# ---------------------------------------------------------------------------
# Streaming panel — shows live tokens as the agent generates
# ---------------------------------------------------------------------------

def _md_to_rich(text: str) -> str:
    """Convert common markdown to Rich markup for the streaming panel.

    Rich's Markdown renderer chokes on partial/streaming text and patterns
    like ``**Vision: **`` (space before closing ``**``).  This lightweight
    converter handles the patterns LLMs actually produce.
    """
    # Escape brackets FIRST so LLM output like [Table 2] doesn't break
    # Text.from_markup().  Our [bold] etc. tags are added after this step.
    text = text.replace("[", "\\[")
    # Strip <br> tags (LLMs use these inside markdown table cells)
    text = re.sub(r"<br\s*/?>", "\n", text)

    lines: list[str] = []
    for line in text.splitlines():
        # Bold: **text** or **text:** (tolerant of spaces)
        line = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/bold]", line)
        # Italic: *text* (but not ** which is bold)
        line = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"[italic]\1[/italic]", line)
        # Headings: ### text
        if re.match(r"^#{1,4}\s", line):
            heading = re.sub(r"^#{1,4}\s+", "", line)
            line = f"[bold]{heading}[/bold]"
        # Numbered lists: 1. text
        elif re.match(r"^\s*\d+\.\s", line):
            line = re.sub(r"^(\s*)(\d+\.)\s", r"\1[dim]\2[/dim] ", line)
        # Bullet points: - text or * text
        line = re.sub(r"^(\s*)[-*]\s", r"\1[dim]•[/dim] ", line)
        # Inline code: `text`
        line = re.sub(r"`([^`]+)`", r"[cyan]\1[/cyan]", line)
        # Status markers
        line = re.sub(r"\bPASS\b", "[green]PASS[/green]", line)
        line = re.sub(r"\bWARN\b", "[yellow]WARN[/yellow]", line)
        line = re.sub(r"\bFAIL\b", "[red]FAIL[/red]", line)
        lines.append(line)
    return "\n".join(lines)


def _parse_md_table(lines: list[str]) -> Table:
    """Convert markdown table lines to a Rich Table."""
    table = Table(box=box.SIMPLE, padding=(0, 1), show_edge=False)

    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip().strip("|")
        # Skip separator rows like :--- | :---: | :---
        if re.match(r"^[\s|:*-]+$", stripped):
            continue
        cells = [c.strip() for c in stripped.split("|")]
        rows.append(cells)

    if not rows:
        return Text("")

    # First row is header
    header = rows[0]
    for col in header:
        col = col.replace("[", "\\[")
        table.add_column(col, style="bold")

    # Data rows
    for row in rows[1:]:
        while len(row) < len(header):
            row.append("")
        styled: list[str] = []
        for cell in row[:len(header)]:
            cell = cell.replace("[", "\\[")
            cell = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/bold]", cell)
            cell = re.sub(r"\bPASS\b", "[green]PASS[/green]", cell)
            cell = re.sub(r"\bWARN\b", "[yellow]WARN[/yellow]", cell)
            cell = re.sub(r"\bFAIL\b", "[red]FAIL[/red]", cell)
            styled.append(cell)
        table.add_row(*styled)

    return table


def _render_streaming_content(text: str):
    """Convert streaming markdown text to Rich renderables with table support."""
    # Pre-process: <br> → newline
    text = re.sub(r"<br\s*/?>", "\n", text)

    lines = text.splitlines()
    segments = []
    current_text: list[str] = []
    table_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        is_table_line = stripped.startswith("|") and stripped.endswith("|")

        if is_table_line:
            if current_text:
                markup = _md_to_rich("\n".join(current_text))
                try:
                    segments.append(Text.from_markup(markup))
                except Exception:
                    segments.append(Text("\n".join(current_text)))
                current_text = []
            table_lines.append(line)
        else:
            if table_lines:
                segments.append(_parse_md_table(table_lines))
                table_lines = []
            current_text.append(line)

    # Flush remaining
    if table_lines:
        segments.append(_parse_md_table(table_lines))
    if current_text:
        markup = _md_to_rich("\n".join(current_text))
        try:
            segments.append(Text.from_markup(markup))
        except Exception:
            segments.append(Text("\n".join(current_text)))

    if not segments:
        return Text("")
    if len(segments) == 1:
        return segments[0]
    return Group(*segments)


class StreamingCallback:
    """Collects chunks and updates a Live display with the last N lines."""

    def __init__(self, agent_name: str, live: Live, max_lines: int = 12):
        self.agent_name = agent_name
        self.live = live
        self.max_lines = max_lines
        self.chunks: list[str] = []
        self.start_time = time.time()

    def __call__(self, chunk: str) -> None:
        self.chunks.append(chunk)
        text = "".join(self.chunks)

        # Detect and display <think> blocks
        display_text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        # If still inside an open <think> tag, show the reasoning content
        if "<think>" in text and "</think>" not in text.split("<think>")[-1]:
            think_content = text.split("<think>")[-1]
            lines = think_content.strip().splitlines()
            visible = "\n".join(lines[-self.max_lines:])
            elapsed = time.time() - self.start_time
            try:
                content = _render_streaming_content(visible) if visible else Text.from_markup("[dim italic]reasoning...[/dim italic]")
            except Exception:
                content = Text(visible, style="dim")
            self.live.update(Panel(
                content,
                title=f"[bold cyan]{self.agent_name}[/bold cyan]  [dim]thinking {elapsed:.0f}s[/dim]",
                border_style="dim yellow",
                padding=(0, 1),
            ))
            return

        # Show last N lines of visible output
        lines = display_text.strip().splitlines()
        visible = "\n".join(lines[-self.max_lines:])
        elapsed = time.time() - self.start_time
        try:
            content = _render_streaming_content(visible) if visible else Text.from_markup("[dim]generating...[/dim]")
        except Exception:
            content = Text(visible)
        self.live.update(Panel(
            content,
            title=f"[bold cyan]{self.agent_name}[/bold cyan]  [dim]{elapsed:.0f}s[/dim]",
            border_style="cyan",
            padding=(0, 1),
        ))


def create_streaming_callback(agent_name: str) -> tuple:
    """Create a Live context and StreamingCallback for an agent.

    Returns (live, callback). Usage:
        live, cb = create_streaming_callback("VSNC Framework")
        with live:
            result = provider.call(system, user, on_chunk=cb)
    """
    live = Live(console=console, refresh_per_second=4, transient=True)
    cb = StreamingCallback(agent_name, live)
    return live, cb


# ---------------------------------------------------------------------------
# Summary and completion
# ---------------------------------------------------------------------------

def print_summary_table(results: list) -> None:
    """Print the final summary dashboard."""
    table = Table(
        title="Review Summary",
        show_header=True,
        header_style="bold",
        border_style="bright_blue",
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Agent", style="bold")
    table.add_column("Severity", justify="center")
    table.add_column("Time", justify="right", style="dim")
    finding_width = max(20, console.width - 45)
    table.add_column("Finding", max_width=finding_width)

    for i, r in enumerate(results, 1):
        style = SEVERITY_STYLE.get(r.severity, "dim")
        sev_text = Text(r.severity.upper(), style=style)
        table.add_row(
            str(i),
            r.agent_name,
            sev_text,
            f"{r.elapsed:.1f}s",
            Text(_truncate(r.summary, finding_width), style="dim"),
        )

    console.print()
    console.print(table)


def print_completion(total_time: float, num_agents: int,
                     report_path: str, html_path: str | None,
                     severity_counts: dict) -> None:
    """Print the final completion panel."""
    parts = []
    for sev in ["major", "moderate", "minor", "ok"]:
        count = severity_counts.get(sev, 0)
        if count:
            style = SEVERITY_STYLE.get(sev, "dim")
            parts.append(f"[{style}]{count} {sev}[/{style}]")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", justify="right")
    grid.add_column()
    grid.add_row("Time", f"{total_time:.1f}s")
    grid.add_row("Agents", str(num_agents))
    grid.add_row("Severity", " | ".join(parts) if parts else "none")
    grid.add_row("Report", f"[green]{report_path}[/green]")
    if html_path:
        grid.add_row("HTML", f"[green]{html_path}[/green]")

    console.print()
    console.print(Panel(
        grid,
        title="[bold green]Review Complete[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()


# ---------------------------------------------------------------------------
# Phase 4: Figure Edit Loop display
# ---------------------------------------------------------------------------

def print_edit_iteration(iteration: int, max_iter: int) -> None:
    """Print an iteration header for the figure edit loop."""
    console.print()
    console.print(
        f"  [bold yellow]Iteration {iteration}/{max_iter}[/bold yellow]  "
        f"[dim]review -> rewrite -> render[/dim]")


def print_edit_review_start(agent_name: str) -> None:
    """Show that a review agent is starting within the edit loop."""
    console.print(f"    [cyan]>[/cyan] {agent_name} ...", end="")


def print_edit_review_done(agent_name: str, severity: str,
                           elapsed: float, summary: str) -> None:
    """Show review agent completion within the edit loop."""
    icon = SEVERITY_ICON.get(severity, severity)
    console.print(f"\r    {icon}  {agent_name}  [dim]({elapsed:.1f}s)[/dim]")
    avail = max(20, console.width - 11)
    console.print(f"         [dim]{_truncate(summary, avail)}[/dim]")


def print_edit_script_start(script_name: str) -> None:
    """Show that a script is being rewritten."""
    console.print(f"    [cyan]>[/cyan] Rewriting [bold]{script_name}[/bold] ...",
                  end="")


def print_edit_retry(retry: int, max_retries: int,
                     error_summary: str) -> None:
    """Show a retry attempt after execution failure."""
    lines = (error_summary or "unknown error").strip().splitlines()
    tail = lines[-3:] if len(lines) > 3 else lines
    console.print(f"\r    [yellow]Retry {retry}/{max_retries}[/yellow]")
    for line in tail:
        console.print(f"    [dim]{_truncate(line, console.width - 8)}[/dim]")


def print_edit_script_done(script_name: str, success: bool,
                           diff_lines: int) -> None:
    """Show script edit result."""
    if success:
        console.print(
            f"\r    [green]OK[/green]   [bold]{script_name}[/bold]  "
            f"[dim]{diff_lines} lines changed[/dim]")
    else:
        console.print(
            f"\r    [red]FAIL[/red] [bold]{script_name}[/bold]  "
            f"[dim]reverted to original[/dim]")


def print_edit_summary(total_iterations: int, total_elapsed: float,
                       successes: int, total_scripts: int) -> None:
    """Print summary after the edit loop completes."""
    console.print()
    console.print(
        f"  [bold]Figure edit loop:[/bold] "
        f"{total_iterations} iterations, "
        f"{successes}/{total_scripts} scripts improved, "
        f"{total_elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Watch mode dashboard
# ---------------------------------------------------------------------------

def print_watch_dashboard(metrics: dict, sections: list[str],
                          paper_path: str) -> None:
    """Print a compact metrics dashboard for watch mode."""
    table = Table(title=f"Live Metrics: {paper_path}",
                  border_style="bright_blue", padding=(0, 1))
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Status")

    m = metrics
    table.add_row("Words", f"{m['word_count']:,}",
                  f"[red]OVER by {m['over_word_limit']}[/red]"
                  if m.get('over_word_limit', 0) > 0 else "[green]OK[/green]")
    table.add_row("FK Grade", str(m['flesch_kincaid_grade']),
                  "[green]Academic[/green]" if 12 <= m['flesch_kincaid_grade'] <= 16
                  else "[yellow]Check[/yellow]")
    table.add_row("Passive %", f"{m['passive_voice_pct']}%",
                  "[green]Good[/green]" if m['passive_voice_pct'] <= 20
                  else "[yellow]High[/yellow]")
    table.add_row("Avg Sent", f"{m['avg_sentence_length']} words",
                  "[green]Good[/green]" if m['avg_sentence_length'] <= 22
                  else "[yellow]Long[/yellow]")
    table.add_row("Hedging", f"{m['hedge_density_per_1k']}/1k",
                  "[green]Low[/green]" if m['hedge_density_per_1k'] < 5
                  else "[yellow]High[/yellow]")
    table.add_row("Sections", ", ".join(sections) if sections else "none", "")
    table.add_row("Acronyms", str(m['unique_acronyms']), "")

    console.clear()
    console.print(table)
    console.print("\n  [dim]Watching for changes... (Ctrl+C to stop)[/dim]")
