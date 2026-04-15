"""
Crash-recovery checkpoint store for PAT.

Local inference can take 15 to 30 minutes per agent, so losing progress
mid-run is expensive.  This module persists:

* **Agent checkpoints** - the full list of completed :class:`AgentResult`
  objects, plus the model name and a hash of the paper text.  Reload is
  rejected when the paper hash has changed.
* **Figure-edit-loop checkpoints** - progress of the optional Phase 4 figure
  improvement loop so that ``--fresh-edits`` can re-run it without redoing
  the upstream text review.

Checkpoints are plain JSON under ``output_dir``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pat.agents import AgentResult


# ---------------------------------------------------------------------------
# Agent checkpoint
# ---------------------------------------------------------------------------

_HASH_PREFIX_LEN = 12


def _paper_hash(paper_text: str) -> str:
    """Stable short hash used to detect whether the manuscript changed."""
    return hashlib.sha256(paper_text.encode()).hexdigest()[:_HASH_PREFIX_LEN]


def _checkpoint_path(paper_path: str, output_dir: Path) -> Path:
    return output_dir / f".checkpoint_{Path(paper_path).stem}.json"


def save_checkpoint(
    paper_path: str,
    paper_text: str,
    model_name: str,
    results: list[AgentResult],
    output_dir: Path,
) -> None:
    """Persist current agent progress to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(paper_path, output_dir)

    data = {
        "paper_path": paper_path,
        "paper_hash": _paper_hash(paper_text),
        "model": model_name,
        "timestamp": datetime.now().isoformat(),
        "results": [
            {
                "agent_id": r.agent_id,
                "agent_name": r.agent_name,
                "summary": r.summary,
                "findings": r.findings,
                "severity": r.severity,
                "references_found": r.references_found,
                "elapsed": r.elapsed,
                "top_issues": getattr(r, "top_issues", []),
                "score": getattr(r, "score", -1.0),
                "section_refs": getattr(r, "section_refs", []),
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_checkpoint(
    paper_path: str,
    paper_text: str,
    output_dir: Path,
) -> Optional[tuple[list[AgentResult], str]]:
    """Load and validate a checkpoint.

    Returns ``(results, model_name)`` if one exists and the paper hash still
    matches; otherwise ``None`` (which signals a fresh run).
    """
    path = _checkpoint_path(paper_path, output_dir)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt checkpoint should never block a fresh run.
        return None

    if data.get("paper_hash") != _paper_hash(paper_text):
        return None

    results = [
        AgentResult(
            agent_id=r["agent_id"],
            agent_name=r["agent_name"],
            summary=r["summary"],
            findings=r["findings"],
            severity=r["severity"],
            references_found=r.get("references_found", []),
            elapsed=r.get("elapsed", 0.0),
            top_issues=r.get("top_issues", []),
            score=r.get("score", -1.0),
            section_refs=r.get("section_refs", []),
        )
        for r in data.get("results", [])
    ]
    return results, data.get("model", "unknown")


def checkpoint_exists(paper_path: str, output_dir: Path) -> bool:
    """Return ``True`` if a checkpoint file exists for this paper."""
    return _checkpoint_path(paper_path, output_dir).exists()


def clear_checkpoint(paper_path: str, output_dir: Path) -> None:
    """Delete the checkpoint file if one is present."""
    path = _checkpoint_path(paper_path, output_dir)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Figure-edit-loop checkpoint
# ---------------------------------------------------------------------------

def _edit_checkpoint_path(paper_path: str, output_dir: Path) -> Path:
    return output_dir / f".edit_checkpoint_{Path(paper_path).stem}.json"


def save_edit_checkpoint(
    paper_path: str,
    edit_results,
    output_dir: Path,
) -> None:
    """Persist figure edit loop progress for crash recovery."""
    from pat.figure_editor import FigureEditLoopResult
    if not isinstance(edit_results, FigureEditLoopResult):
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _edit_checkpoint_path(paper_path, output_dir)

    data = {
        "paper_path": paper_path,
        "timestamp": datetime.now().isoformat(),
        "total_iterations": edit_results.total_iterations,
        "total_elapsed": edit_results.total_elapsed,
        "before_figures": edit_results.before_figures,
        "after_figures": edit_results.after_figures,
        "iterations": [
            [
                {
                    "iteration": r.iteration,
                    "script_name": r.script_name,
                    "diff": r.diff,
                    "feedback_summary": r.feedback_summary,
                    "success": r.success,
                    "error": r.error,
                    "output_figure_path": r.output_figure_path,
                }
                for r in iter_results
            ]
            for iter_results in edit_results.iterations
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_edit_checkpoint(paper_path: str, output_dir: Path):
    """Load the figure edit loop checkpoint, or return ``None``.

    The full original / modified source is intentionally omitted from the
    saved payload (it can be large); only the diff is preserved.
    """
    path = _edit_checkpoint_path(paper_path, output_dir)
    if not path.exists():
        return None

    try:
        from pat.figure_editor import FigureEditLoopResult, FigureEditResult
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ImportError):
        return None

    iterations = [
        [
            FigureEditResult(
                iteration=r["iteration"],
                script_name=r["script_name"],
                original_code="",   # Intentionally not stored (large).
                modified_code="",   # Intentionally not stored (large).
                diff=r.get("diff", ""),
                feedback_summary=r.get("feedback_summary", ""),
                success=r.get("success", False),
                error=r.get("error"),
                output_figure_path=r.get("output_figure_path"),
            )
            for r in iter_results
        ]
        for iter_results in data.get("iterations", [])
    ]

    return FigureEditLoopResult(
        iterations=iterations,
        before_figures=data.get("before_figures", []),
        after_figures=data.get("after_figures", []),
        total_iterations=data.get("total_iterations", 0),
        total_elapsed=data.get("total_elapsed", 0.0),
    )


def clear_edit_checkpoint(paper_path: str, output_dir: Path) -> None:
    """Delete the figure edit loop checkpoint if present."""
    path = _edit_checkpoint_path(paper_path, output_dir)
    if path.exists():
        path.unlink()
