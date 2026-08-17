"""Console output for the build pipeline.

A build prints two different things. Progress — which table just landed and how big it is — is
what you read every run to confirm the pipeline is alive and see where it got to. Model analysis —
backtest folds, quantile calibration, archetype edges, the live draft board — is a report you read
occasionally, when deciding whether to trust a model. The analysis runs to well over a hundred
lines and buries the progress in a full build.

So the split is by caller, not by deletion. Running a module directly (`python -m
src.gold.player_archetypes`) prints everything, because reading the report is the reason to run it
that way. `scripts/build_warehouse.sh` sets GOING_DEEP_QUIET, which drops the analysis and the
per-file archive chatter and leaves one line per table. Nothing is lost either way: the reports are
either persisted to a table of their own (`inhouse_backtest`, `archetype_outcomes`) or reproduced
by running that one module, and `--verbose` on the script turns them all back on.
"""

import functools
import os
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}

QUIET = os.environ.get("GOING_DEEP_QUIET", "").strip().lower() in _TRUTHY

# Table names run to ~30 characters (fantasypros_weekly_rankings_dst); pad past that so the row
# counts line up in a column instead of ragging against the longest name.
_NAME_WIDTH = 34


def table(name: str, rows: int | None = None, detail: str = "") -> None:
    """Report a warehouse table that was just written. Always printed — this is the progress."""
    count = f"{rows:>12,} rows" if rows is not None else f"{'':>17}"
    suffix = f"  {detail}" if detail else ""
    print(f"  {name:<{_NAME_WIDTH}}{count}{suffix}", flush=True)


def archived(path: Path, rows: int | None = None) -> None:
    """Report a raw file written to the archive. Detail: suppressed in a full build.

    Sources that archive one file per season/position emit dozens of these, which say nothing a
    build needs — the table line that follows already reports what landed in the warehouse.
    """
    if QUIET:
        return
    count = f" ({rows:,} rows)" if rows is not None else ""
    print(f"  saved {path}{count}", flush=True)


def note(message: str) -> None:
    """Report something unusual — a skipped fetch, a file with no data. Always printed."""
    print(f"  {message}", flush=True)


def analysis(fn):
    """Mark a function as report output, to be skipped in a full build.

    Applied to the `_print_*`/`_report` helpers in the gold layer, so their call sites stay a plain
    call and the decision lives in one place. Only valid on functions that exist purely to print;
    every one it decorates returns None.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if QUIET:
            return None
        return fn(*args, **kwargs)

    return wrapper
