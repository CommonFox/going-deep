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

failed=0
for notebook in "${notebooks[@]}"; do
    start=$SECONDS
    printf '%s' "$notebook"
    # --execute runs every cell top to bottom in a fresh kernel, so a notebook that only works
    # because of state left behind by an earlier manual run fails here, which is the point.
    if jupyter nbconvert --to notebook --execute --inplace "$notebook" >/dev/null 2>&1; then
        printf '  ok (%ds)\n' "$((SECONDS - start))"
    else
        printf '  FAILED\n'
        # Re-run without swallowing output so the traceback is visible.
        jupyter nbconvert --to notebook --execute --inplace "$notebook" 2>&1 | tail -20 || true
        failed=1
    fi
done

exit "$failed"
