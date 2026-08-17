#!/usr/bin/env bash
# Re-execute every notebook in notebooks/ against the current warehouse, in place.
#
# Notebooks in this repo compute their findings rather than quoting them, so running this after
# scripts/build_warehouse.sh is what turns "the numbers were right in August" into "the numbers are
# right". A cell that now errors is a real signal — a column got renamed, or a model stopped
# producing a table something here reads.
#
# Needs the optional notebook dependencies: pip install -r requirements-notebooks.txt
#
# Usage: run_notebooks.sh [notebook ...]   (default: all of notebooks/*.ipynb)
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python -c "import nbconvert" 2>/dev/null; then
    echo "nbconvert not installed — pip install -r requirements-notebooks.txt" >&2
    exit 1
fi

notebooks=("$@")
if [ ${#notebooks[@]} -eq 0 ]; then
    shopt -s nullglob
    notebooks=(notebooks/*.ipynb)
fi

if [ ${#notebooks[@]} -eq 0 ]; then
    echo "no notebooks found in notebooks/"
    exit 0
fi

# --execute runs every cell top to bottom in a fresh kernel, so a notebook that only works because
# of state left behind by an earlier manual run fails here, which is the point.
#
# Clearing cell metadata is what keeps these notebooks reviewable. nbconvert stamps every cell with
# wall-clock execution timings, so without this a re-run that changed nothing still produced a
# ~130-line diff of pure timestamp churn — which trains you to skim notebook diffs, exactly when a
# notebook diff is the thing telling you a finding moved. With it, re-running an unchanged
# warehouse produces a byte-identical file, so any diff at all is a real change in the numbers.
# Notebook-level metadata is preserved: that's where kernelspec lives.
NBCONVERT_ARGS=(
    --to notebook --execute --inplace
    --ClearMetadataPreprocessor.enabled=True
    --ClearMetadataPreprocessor.clear_cell_metadata=True
    --ClearMetadataPreprocessor.clear_notebook_metadata=False
)

failed=0
for notebook in "${notebooks[@]}"; do
    start=$SECONDS
    printf '%s' "$notebook"
    if jupyter nbconvert "${NBCONVERT_ARGS[@]}" "$notebook" >/dev/null 2>&1; then
        printf '  ok (%ds)\n' "$((SECONDS - start))"
    else
        printf '  FAILED\n'
        # Re-run without swallowing output so the traceback is visible.
        jupyter nbconvert "${NBCONVERT_ARGS[@]}" "$notebook" 2>&1 | tail -20 || true
        failed=1
    fi
done

exit "$failed"
