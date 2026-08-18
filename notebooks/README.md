# Notebooks

Findings that are worth keeping, written as live queries against `data/warehouse.duckdb`.

The point of putting them here rather than in a document is that **nothing is transcribed by
hand**. Every number in a notebook is computed by the cell above it, so re-running after a rebuild
updates the findings instead of quietly leaving them stale. A markdown write-up of "punts inside
the 10 are worth 3.73 points" is true until the archive grows; a notebook cell that computes it
stays true.

| Notebook | What's in it |
|---|---|
| `punting.ipynb` | Everything the warehouse knows about punters: what the league scores, what's predictable, why bad offenses make good fantasy punters, whether matchup matters, the model and its backtest, and this year's projections. |
| `draft_strategy.ipynb` | What roster construction is worth in each league: whether running back scarcity is real, what each round returns by position, ~89k simulated drafts ranking every five-round opening, and why Sleeper's real superflex format now dominates that answer while ESPN's non-superflex one still comes down to noise. |

## Setup

```bash
source .venv/bin/activate
pip install -r requirements-notebooks.txt
```

Then open the `.ipynb` in VS Code and pick the `.venv` interpreter as the kernel — VS Code renders
notebooks natively, so JupyterLab is optional (`pip install jupyterlab` if you want the browser UI).

To re-run every notebook against a freshly built warehouse:

```bash
scripts/run_notebooks.sh
```

## Querying the warehouse

```python
from src.query import q, tables, columns, peek

q("SELECT * FROM punter_projections ORDER BY projected_points DESC LIMIT 10")

tables()                    # every table with row counts, labelled silver/gold
tables("gold")              # just the models
columns("punter_seasons")   # what's in a table
peek("punt_environment")    # first few rows
```

Put a variable into a query with a `?` placeholder rather than an f-string — player names contain
apostrophes and it is only a matter of time:

```python
q("SELECT * FROM punter_seasons WHERE player_name = ?", ["Pat O'Donnell"])
```

Every notebook starts with the same cell, which finds the repo root from wherever the kernel
happened to start so `src` is importable regardless:

```python
import sys
from pathlib import Path

ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "src").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

## Don't hold a connection open

This is the one thing that will bite you, so it's worth knowing *why* `src/query.py` looks the way
it does.

DuckDB takes a **file lock** on the warehouse: one writer or many readers, never both. A connection
holds its lock until it's closed or the process dies — and a notebook kernel is a process that
stays alive for hours. So this, at the top of a notebook:

```python
con = duckdb.connect("data/warehouse.duckdb", read_only=True)   # DON'T
```

will make `scripts/build_warehouse.sh` fail with `Could not set lock on file` until you restart the
kernel, and nothing in the build's error message will point at the idle notebook in your other
window.

`q()` sidesteps it by opening a read-only connection, running, and closing it before it returns.
That costs about 4ms against a query floor of ~18ms. Read-only is the other half: a notebook
physically cannot write to the warehouse, so exploring can't corrupt what the pipeline built.

If a query does fail with a lock error, a build is running — wait for it to finish.

## Adding a notebook

- One subject per notebook, named for the subject (`punting.ipynb`, not `analysis_2.ipynb`).
- Compute the numbers; don't paste them into the prose. If a claim can't be computed, say it's an
  observation rather than dressing it as a result.
- Lead each section with the question it answers, and give the answer in the markdown above the
  cell — the tables are evidence, not the finding.
- Report the negative results too. Half of `punting.ipynb` is things that turned out not to
  work (matchup, weather, field position as a predictor), and that half is what stops the same
  dead ends being explored twice.
- Commit with outputs, and refresh them through `scripts/run_notebooks.sh` rather than by hand.
  The outputs *are* the saved answer, which is the whole point of committing them; the script
  strips the wall-clock execution timings nbconvert stamps on every cell, so re-running against an
  unchanged warehouse rewrites the file byte for byte identically. That matters more than it
  sounds: without it a no-op re-run produced a ~130-line diff, and a notebook diff you've learned
  to skim is worthless exactly when it's telling you a finding moved.
- Models and pipeline code belong in `src/`, not here. A notebook that starts growing functions
  other notebooks want is telling you it should be a module.
