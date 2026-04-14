"""
Figure improvement loop for PAT.

Runs up to :data:`DEFAULT_MAX_ITERATIONS` iterations of::

    review figures -> coding model rewrites scripts -> execute -> re-render

Each iteration uses all figure agents to evaluate the current rendered
figures; the aggregated feedback is handed to a coding LLM which returns a
complete rewritten script.  The harness generates a unified diff, runs the
script inside an isolated venv, and retries on execution errors.

All artifacts go to PAT's output directory; the source repo is never modified.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import AgentResult, BaseAgent
    from providers import LLMProvider


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_MAX_RETRIES = 3
DEFAULT_EXEC_TIMEOUT_SECONDS = 120
CODER_MAX_TOKENS = 8192
FEEDBACK_PREVIEW_CHARS = 500

# Directory names at the source-repo root that we should not treat as
# importable Python packages when setting up the isolated working directory.
_RESERVED_DIRNAMES = frozenset({
    "data", "outputs", "reports", "figures", ".git", "__pycache__", ".venv",
    "venv", "env", "tests", "test", "docs", "doc",
})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FigureEditResult:
    """Result of editing one script in one iteration."""
    iteration: int
    script_name: str
    original_code: str
    modified_code: str
    diff: str               # unified diff
    feedback_summary: str   # what motivated the change
    success: bool
    error: str | None = None
    output_figure_path: str | None = None


@dataclass
class FigureEditLoopResult:
    """Complete result of the edit loop across all iterations."""
    iterations: list[list[FigureEditResult]] = field(default_factory=list)
    before_figures: list[str] = field(default_factory=list)
    after_figures: list[str] = field(default_factory=list)
    total_iterations: int = 0
    total_elapsed: float = 0.0


# ---------------------------------------------------------------------------
# Venv management
# ---------------------------------------------------------------------------

def _venv_bin(venv_dir: Path, name: str) -> Path:
    """Return path to a venv binary, preferring 'name' over 'name3'."""
    candidate = venv_dir / "bin" / name
    if candidate.exists():
        return candidate
    return venv_dir / "bin" / f"{name}3"


def create_figure_venv(venv_dir: Path) -> Path:
    """Create an isolated Python venv for figure script execution.
    Pre-installs: scipy, numpy, matplotlib, h5py, scikit-learn, pandas, tqdm."""
    if _venv_bin(venv_dir, "python").exists():
        return venv_dir
    venv_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True, capture_output=True,
    )
    pip = str(_venv_bin(venv_dir, "pip"))
    subprocess.run(
        [pip, "install", "-q",
         "scipy", "numpy", "matplotlib", "h5py",
         "scikit-learn", "pandas", "tqdm"],
        check=True, capture_output=True,
    )
    return venv_dir


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_CODER_SYSTEM = """\
You are a scientific figure improvement assistant. You receive:
1. A Python script that generates a figure for a scientific manuscript
2. Review feedback from specialized figure review agents

Your task: modify the script to address the review feedback.

RULES:
- Do NOT modify any function calls to external packages (data loading,
  computation, statistical tests, peak detection, etc.). Only change
  visualization code.
- You may change: matplotlib calls, formatting, layout, colors, annotations,
  font sizes, axis labels, legends, error bars, panel labels, figure size,
  DPI, line styles, marker styles.
- PRESERVE the argparse / command-line interface EXACTLY. Do not add, remove,
  rename, or change the default values of command-line arguments or file paths.
- If you cannot address a piece of feedback without changing computation
  or data loading, leave that part unchanged and add a comment explaining why.
- Output the COMPLETE modified script. Do not output diffs or partial code.
- Wrap your output in ```python ... ``` code fences.
"""

_FIX_SYSTEM = """\
Fix the following Python script error. Output the complete fixed script \
in ```python``` fences. Do not explain — just output the corrected code. \
Preserve the argparse / CLI interface exactly — do not change argument names or defaults.\
"""


def _collect_figure_feedback(results: list, figure_agent_ids: list[str]) -> str:
    """Collect all figure agent feedback into a single block for the coding model."""
    parts = []
    for r in results:
        if r.agent_id in figure_agent_ids and r.findings:
            parts.append(
                f"## {r.agent_name} [{r.severity.upper()}]\n{r.findings}"
            )
    return "\n\n---\n\n".join(parts)


def _build_edit_prompt(script_code: str, feedback: str) -> tuple[str, str]:
    """Build (system, user) prompts for the coding model."""
    user = (
        f"FIGURE SCRIPT:\n```python\n{script_code}\n```\n\n"
        f"REVIEW FEEDBACK:\n{feedback}\n\n"
        f"Output the complete modified script:"
    )
    return _CODER_SYSTEM, user


def _build_fix_prompt(broken_code: str, traceback: str) -> tuple[str, str]:
    """Build (system, user) prompts for error recovery."""
    user = (
        f"SCRIPT:\n```python\n{broken_code}\n```\n\n"
        f"ERROR:\n{traceback}"
    )
    return _FIX_SYSTEM, user


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def _extract_code(response: str) -> str | None:
    """Extract Python code from ```python ... ``` fences in model response."""
    m = re.search(r'```python\s*\n(.*?)```', response, re.DOTALL)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Execution environment
# ---------------------------------------------------------------------------

def _prepare_workdir(
    script_code: str,
    script_name: str,
    source_repo_dir: Path,
    output_base: Path,
    iteration: int,
) -> tuple[Path, Path]:
    """Create a temp working directory that mirrors the source repo structure.

    Returns (workdir, script_path) where workdir has:
      - The modified script copied in
      - data/ symlinked from source repo (read-only access)
      - outputs/ directory for new figures
    """
    workdir = output_base / f"iter_{iteration}" / script_name.replace(".py", "")
    workdir.mkdir(parents=True, exist_ok=True)

    # Symlink data directory if it exists
    data_src = source_repo_dir / "data"
    data_link = workdir / "data"
    if data_src.exists() and not data_link.exists():
        data_link.symlink_to(data_src.resolve())

    # Create outputs directory
    outputs_dir = workdir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    # Symlink any local Python package (directory with __init__.py) from the
    # source repo so the script's imports keep working.  This is generic -
    # the pipeline does not assume any particular package name.
    for child in source_repo_dir.iterdir():
        if not child.is_dir() or child.name in _RESERVED_DIRNAMES:
            continue
        if child.name.startswith("."):
            continue
        if not (child / "__init__.py").exists():
            continue
        link = workdir / child.name
        if not link.exists():
            link.symlink_to(child.resolve())

    script_path = workdir / script_name
    script_path.write_text(script_code, encoding="utf-8")

    return workdir, script_path


def _execute_script(
    script_path: Path,
    venv_dir: Path,
    workdir: Path,
    timeout: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Execute a figure script in the isolated venv.

    Returns (success, stdout_or_stderr).
    """
    python = str(_venv_bin(venv_dir, "python"))
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"  # Non-interactive matplotlib

    try:
        result = subprocess.run(
            [python, str(script_path)],
            capture_output=True, text=True,
            timeout=timeout, env=env,
            cwd=str(workdir),
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, f"Script timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------

def _generate_diff(original: str, modified: str, filename: str) -> str:
    """Generate unified diff between original and modified scripts."""
    return "\n".join(difflib.unified_diff(
        original.splitlines(), modified.splitlines(),
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
        lineterm="",
    ))


# ---------------------------------------------------------------------------
# Main edit loop
# ---------------------------------------------------------------------------

def run_edit_loop(
    fig_scripts: list[Path],
    fig_dir: Path,
    source_repo_dir: Path,
    output_dir: Path,
    review_provider: "LLMProvider",
    figure_agents: list["BaseAgent"],
    config: dict,
    sections: dict,
    paper_text: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    coder_model: str = "qwen3-coder-next:q8_0",
    on_status=None,
    on_iteration=None,
    on_script_start=None,
    on_retry=None,
    on_script_done=None,
    on_review_start=None,
    on_review_done=None,
    create_streaming_cb=None,
) -> FigureEditLoopResult:
    """Run the figure improvement loop.

    Args:
        fig_scripts: Paths to figure-generating Python scripts.
        fig_dir: Directory containing current rendered figures (PNG).
        source_repo_dir: Root of the source repo (for data/ symlinks).
        output_dir: Where to write all artifacts.
        review_provider: The review model (qwen3.5:27b-bf16).
        figure_agents: List of 8 figure agent instances.
        config: Journal configuration dict.
        sections: Parsed paper sections.
        paper_text: Full paper text.
        max_iterations: Maximum edit-review cycles (default 5).
        max_retries: Max retries on execution failure (default 3).
        on_status: Optional callback for plain status: on_status(message).
        on_iteration: Optional callback: on_iteration(iteration, max_iter).
        on_script_start: Optional callback: on_script_start(script_name).
        on_retry: Optional callback: on_retry(retry, max_retries, error).
        on_script_done: Optional callback: on_script_done(name, success, diff_lines).
        on_review_start: Optional callback: on_review_start(agent_name).
        on_review_done: Optional callback: on_review_done(name, severity, elapsed, summary).
        create_streaming_cb: Optional factory: create_streaming_cb(name) -> (live_ctx, on_chunk).

    Returns:
        FigureEditLoopResult with all iterations, diffs, and figure paths.
    """
    from agents import Context, AgentResult
    from providers import OllamaProvider

    # Create coding model provider
    coder = OllamaProvider(model=coder_model)

    # Create execution venv
    venv_dir = output_dir / ".fig_venv"
    if on_status:
        on_status("Setting up figure execution environment...")
    create_figure_venv(venv_dir)

    # Track state
    current_figures = sorted(fig_dir.glob("*.png"))
    before_figures = [str(p) for p in current_figures]
    working_scripts: dict[str, str] = {}
    original_scripts: dict[str, str] = {}

    for script in fig_scripts:
        code = script.read_text(encoding="utf-8")
        working_scripts[script.name] = code
        original_scripts[script.name] = code

    figure_agent_ids = [a.id for a in figure_agents]
    all_iteration_results: list[list[FigureEditResult]] = []
    t_start = time.time()

    for iteration in range(max_iterations):
        if on_iteration:
            on_iteration(iteration + 1, max_iterations)
        elif on_status:
            on_status(f"Figure edit iteration {iteration + 1}/{max_iterations}")

        # -- Step 1: review current figures with every figure agent --
        agent_results: list[AgentResult] = []
        for agent in figure_agents:
            if on_review_start:
                on_review_start(agent.name)
            elif on_status:
                on_status(f"  Reviewing: {agent.name}...")

            on_chunk = None
            live_ctx = None
            if create_streaming_cb:
                live_ctx, on_chunk = create_streaming_cb(agent.name)

            ctx = Context(
                paper_text=paper_text,
                figures=[str(p) for p in current_figures],
                prior_results={r.agent_id: r for r in agent_results},
                config=config,
                provider=review_provider,
                sections=sections,
                on_chunk=on_chunk,
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
                    summary=f"Error: {e}", findings=str(e),
                    severity="ok", references_found=[], elapsed=0.0,
                )
            agent_results.append(result)

            if on_review_done:
                on_review_done(agent.name, result.severity,
                               result.elapsed, result.summary)

        # -- Step 2: aggregate the per-agent feedback --
        feedback = _collect_figure_feedback(agent_results, figure_agent_ids)

        # Swap models: unload the review model so the coder can load.
        review_provider.unload()

        # -- Step 3: ask the coding model to rewrite each script --
        iteration_results: list[FigureEditResult] = []

        for script_name, current_code in working_scripts.items():
            if on_script_start:
                on_script_start(script_name)
            elif on_status:
                on_status(f"  Rewriting {script_name}...")

            system, user = _build_edit_prompt(current_code, feedback)

            coder_chunk = None
            coder_live = None
            if create_streaming_cb:
                coder_live, coder_chunk = create_streaming_cb(
                    f"Rewriting {script_name}")

            if coder_live:
                with coder_live:
                    response = coder.call(system, user, max_tokens=CODER_MAX_TOKENS,
                                          on_chunk=coder_chunk)
            else:
                response = coder.call(system, user, max_tokens=CODER_MAX_TOKENS)
            new_code = _extract_code(response)

            if not new_code:
                iteration_results.append(FigureEditResult(
                    iteration=iteration, script_name=script_name,
                    original_code=original_scripts[script_name],
                    modified_code=current_code,
                    diff="", feedback_summary="Model did not produce valid code",
                    success=False, error="Could not extract code from response",
                ))
                continue

            # -- Step 4: execute the modified script in an isolated venv --
            workdir, script_path = _prepare_workdir(
                new_code, script_name, source_repo_dir,
                output_dir, iteration,
            )

            success = False
            error = None

            for retry in range(max_retries):
                ok, output = _execute_script(script_path, venv_dir, workdir)
                if ok:
                    success = True
                    break

                error = output
                if on_retry:
                    on_retry(retry + 1, max_retries, output)
                elif on_status:
                    on_status(f"    Retry {retry + 1}/{max_retries}: "
                              f"fixing execution error...")

                # Feed traceback to coder for fix
                fix_sys, fix_user = _build_fix_prompt(new_code, output)

                fix_chunk = None
                fix_live = None
                if create_streaming_cb:
                    fix_chunk_label = f"Fixing {script_name} ({retry + 1}/{max_retries})"
                    fix_live, fix_chunk = create_streaming_cb(fix_chunk_label)

                if fix_live:
                    with fix_live:
                        fix_response = coder.call(fix_sys, fix_user,
                                                  max_tokens=CODER_MAX_TOKENS,
                                                  on_chunk=fix_chunk)
                else:
                    fix_response = coder.call(fix_sys, fix_user, max_tokens=CODER_MAX_TOKENS)
                fixed = _extract_code(fix_response)
                if fixed:
                    new_code = fixed
                    script_path.write_text(new_code, encoding="utf-8")

            if not success:
                # Revert to last working version
                new_code = current_code

            diff = _generate_diff(
                original_scripts[script_name], new_code, script_name)

            result = FigureEditResult(
                iteration=iteration, script_name=script_name,
                original_code=original_scripts[script_name],
                modified_code=new_code,
                diff=diff,
                feedback_summary=feedback[:FEEDBACK_PREVIEW_CHARS],
                success=success, error=error,
                output_figure_path=str(workdir / "outputs") if success else None,
            )
            iteration_results.append(result)

            diff_lines = len([l for l in diff.splitlines()
                              if l.startswith('+') or l.startswith('-')])
            if on_script_done:
                on_script_done(script_name, success, diff_lines)

            if success:
                working_scripts[script_name] = new_code

        all_iteration_results.append(iteration_results)

        # Swap models: unload coder so the review model can reload next iter.
        coder.unload()

        # -- Step 5: point current_figures at the newly rendered outputs --
        any_new = False
        for result in iteration_results:
            if result.success and result.output_figure_path:
                new_pngs = sorted(
                    Path(result.output_figure_path).glob("*.png"))
                if new_pngs:
                    # Replace matching figures in current set
                    new_names = {p.name for p in new_pngs}
                    current_figures = [
                        p for p in current_figures
                        if p.name not in new_names
                    ] + new_pngs
                    any_new = True

        if not any_new:
            if on_status:
                on_status(f"  No successful edits in iteration {iteration + 1}, "
                          f"stopping early.")
            break

    return FigureEditLoopResult(
        iterations=all_iteration_results,
        before_figures=before_figures,
        after_figures=[str(p) for p in current_figures],
        total_iterations=len(all_iteration_results),
        total_elapsed=time.time() - t_start,
    )
