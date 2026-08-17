"""Report what is actually in the warehouse.

Two modes. `python -m src.summary` lists every table with its row count — the standalone "what have
I got" view, useful without rebuilding anything. `--brief` collapses that to a per-layer roll-up,
which is what scripts/build_warehouse.sh prints at the end: the build has already named every table
as it wrote it, so repeating all 58 there would just bury the totals.

Either way this reads the warehouse rather than tallying what the build printed, which is the
point: a table that failed to build, or one orphaned by a model that has since been renamed, shows
up here in the counts. A tally of a run's own output could only report the steps that ran.
"""

import sys
from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Silver tables are named after the source that loads them; everything else is gold. Grouping on
# that keeps the listing in the same shape as src/, so a missing table is easy to spot next to its
# siblings. Anything new and unprefixed lands in gold, which is where new models go.
_SILVER_PREFIXES = (
    "weekly_stats", "schedules", "rosters", "snap_counts", "injuries", "depth_chart", "ids",
    "players", "ngs_", "ftn_", "pfr_advstats", "sleeper_", "espn_", "fantasypros_", "ffc_",
    "fftoday_", "cbs_",
)


def _layer(table_name: str) -> str:
    return "silver" if table_name.startswith(_SILVER_PREFIXES) else "gold"


def summarize(brief: bool = False) -> None:
    if not WAREHOUSE_PATH.exists():
        print(f"{WAREHOUSE_PATH} does not exist — nothing built yet.")
        return

    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() ORDER BY table_name"
    ).fetchall()]
    # duckdb_tables() carries an estimated_size, but it is an optimizer estimate rather than a
    # count and reads low on freshly written tables — worth the explicit count for a report whose
    # whole job is to be trusted.
    counts = {name: con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in names}
    con.close()

    for layer in ("silver", "gold"):
        tables = [n for n in names if _layer(n) == layer]
        if not tables:
            continue
        rows = sum(counts[n] for n in tables)
        if brief:
            print(f"  {layer:<8}{len(tables):>3} tables{rows:>14,} rows")
            continue
        print(f"\n{layer} — {len(tables)} tables, {rows:,} rows")
        for name in tables:
            print(f"  {name:<34}{counts[name]:>12,} rows")

    size_mb = WAREHOUSE_PATH.stat().st_size / 1_000_000
    prefix = "  " if brief else "\n"
    print(f"{prefix}{WAREHOUSE_PATH}: {len(names)} tables, {sum(counts.values()):,} rows, "
          f"{size_mb:,.0f} MB")


if __name__ == "__main__":
    summarize(brief="--brief" in sys.argv[1:])
