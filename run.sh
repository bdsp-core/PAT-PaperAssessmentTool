#!/usr/bin/env bash
#
# PAT - Paper Assessment Tool
#
# Convenience wrapper that verifies prerequisites (Python, Ollama, dependencies),
# creates a local virtual environment if needed, and runs a full review.
#
# Usage:
#   ./run.sh <paper.pdf>           # run with the default local review model
#   ./run.sh -local <paper.pdf>    # force local inference
#   ./run.sh -cloud <paper.pdf>    # use an Ollama cloud model
#   ./run.sh -reset                # remove generated reports/ and exit
#
# Override the default models via environment variables:
#   PAT_REVIEW_MODEL=<ollama-tag>
#   PAT_CODER_MODEL=<ollama-tag>

set -euo pipefail

cd "$(dirname "$0")"

# ---- Optional reset subcommand ------------------------------------------
if [[ "${1:-}" == "-reset" ]]; then
    echo "  Resetting: removing reports/ ..."
    rm -rf reports
    echo "  Done."
    exit 0
fi

# ---- Model selection -----------------------------------------------------
REVIEW_MODEL="${PAT_REVIEW_MODEL:-qwen3.5:27b-bf16}"
CODER_MODEL="${PAT_CODER_MODEL:-qwen3-coder-next:q8_0}"

if [[ "${1:-}" == "-cloud" ]]; then
    MODELS=("qwen3.5:cloud")
    shift
elif [[ "${1:-}" == "-local" ]]; then
    MODELS=("$REVIEW_MODEL")
    shift
else
    MODELS=("$REVIEW_MODEL")
fi

# ---- Paper to review -----------------------------------------------------
if [[ $# -lt 1 ]]; then
    echo "  Usage: ./run.sh [<-local|-cloud>] <paper.pdf>" >&2
    exit 1
fi
PAPER="$1"
shift
EXTRA_ARGS=("$@")

echo ""
echo "  =========================================="
echo "    PAT - Paper Assessment Tool"
echo "  =========================================="
echo ""
echo "  Paper:   $PAPER"
echo "  Review:  ${MODELS[*]}"
echo "  Coder:   $CODER_MODEL"
echo ""

# ---- Python check --------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "  ERROR: python3 not found. Install Python 3.10+." >&2
    exit 1
fi

# ---- Ollama check --------------------------------------------------------
if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
    echo "  ERROR: Ollama is not running." >&2
    echo "  Start it with: ollama serve" >&2
    exit 1
fi
echo "  [ok] Ollama is running"

# ---- Model availability --------------------------------------------------
for MODEL in "${MODELS[@]}" "$CODER_MODEL"; do
    if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
        echo "  Model $MODEL not found. Pulling ..."
        ollama pull "$MODEL"
    fi
    echo "  [ok] Model $MODEL available"
done

# ---- Virtual environment -------------------------------------------------
if [ ! -d .venv ]; then
    echo "  Creating virtual environment ..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ---- Package install (only when something is actually missing) ----------
if ! python3 -c "import pat, ollama, rich, markdown, fitz, typing_extensions, PIL, requests" &>/dev/null; then
    echo "  Installing PAT package and dependencies ..."
    pip install -q -e .
fi
echo "  [ok] Package installed"

# ---- Paper present -------------------------------------------------------
if [ ! -f "$PAPER" ]; then
    echo "  ERROR: $PAPER not found in $(pwd)" >&2
    exit 1
fi
echo "  [ok] Paper found: $PAPER"
echo ""

# ---- Run the review once per model --------------------------------------
for MODEL in "${MODELS[@]}"; do
    TAG="${MODEL##*:}"
    OUTDIR="reports/$TAG"
    echo "  -- Running with $MODEL -> $OUTDIR --"
    pat "$PAPER" \
        --model "$MODEL" \
        --output-dir "$OUTDIR" \
        --fresh --html --ref-backend pubmed \
        "${EXTRA_ARGS[@]}"
done
