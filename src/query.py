"""Read-only warehouse access for notebooks and ad-hoc exploration.

    from src.query import q, tables, columns, peek

    q("SELECT * FROM punter_projections ORDER BY projected_points DESC LIMIT 10")

Every function here opens its own read-only connection, runs, and closes it before returning.
That is deliberate and it is the whole reason this module exists.

DuckDB takes a **file lock** on the warehouse. One writer or many readers, never both — and a
connection holds its lock until it is closed or the process exits. A notebook kernel is a process
that stays alive for hours, so the obvious thing to write at the top of a notebook:

    con = duckdb.connect("data/warehouse.duckdb", read_only=True)   # DON'T

will make `scripts/build_warehouse.sh` fail with `Could not set lock on file` until the kernel is
restarted, and there is nothing in the build's error message to suggest that an idle notebook in
another window is the cause. Connecting per call costs about 4ms against a query floor of ~18ms,
which is not worth a class of bug that only shows up when you're trying to rebuild.

The read-only flag is the other half: it means a notebook can never write to the warehouse, so
exploration can't corrupt what the pipeline built. Models write through their own connections in
`src/gold/`; this is strictly for reading.
"""

from pathlib import Path

import duckdb
import pandas as pd

# Resolved from this file rather than the working directory, unlike the pipeline modules' relative
# `Path("data/warehouse.duckdb")`. Those are always run as `python -m src...` from the repo root;
# a notebook's working directory depends on the editor and on where the kernel was started, and
# "it works in VS Code but not from the terminal" is not a puzzle worth handing anyone.
WAREHOUSE_PATH = Path(__file__).resolve().parent.parent / "data" / "warehouse.duckdb"

# Mirrors src/summary.py's split so a notebook groups tables the same way the build reports them.
_SILVER_PREFIXES = (
    "weekly_stats", "schedules", "rosters", "snap_counts", "injuries", "depth_chart", "ids",
    "players", "ngs_", "ftn_", "pbp_", "pfr_advstats", "sleeper_", "espn_", "fantasypros_", "ffc_",
    "fftoday_", "cbs_",
)


def _connect() -> duckdb.DuckDBPyConnection:
    if not WAREHOUSE_PATH.exists():
        raise FileNotFoundError(
            f"{WAREHOUSE_PATH} not found — run scripts/build_warehouse.sh to build it."
        )
    try:
        return duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    except duckdb.IOException as error:
        raise RuntimeError(
            "Could not open the warehouse read-only. A build is probably running and holding the "
            "write lock — wait for it to finish and retry."
        ) from error


def q(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame.

    `params` are passed straight through to DuckDB's `?` placeholders, which is the right way to
    put a variable into a query — string-formatting a value into SQL breaks on any name with an
    apostrophe in it (which, in a table full of player names, is a matter of time):

        q("SELECT * FROM punter_seasons WHERE player_name = ?", ["Pat O'Donnell"])
    """
    con = _connect()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def tables(layer: str | None = None) -> pd.DataFrame:
    """Every table in the warehouse with its row count, labelled silver or gold."""
    con = _connect()
    try:
        names = [row[0] for row in con.execute(
            "SELECT table_name FROM duckdb_tables() ORDER BY table_name"
        ).fetchall()]
        rows = [
            {
                "table": name,
                "layer": "silver" if name.startswith(_SILVER_PREFIXES) else "gold",
                "rows": con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0],
            }
            for name in names
        ]
    finally:
        con.close()

    frame = pd.DataFrame(rows)
    if layer:
        frame = frame[frame["layer"] == layer].reset_index(drop=True)
    return frame


def columns(table: str) -> pd.DataFrame:
    """Column names and types for one table — the fastest way to remember what's in it."""
    return q(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    )


def peek(table: str, n: int = 5) -> pd.DataFrame:
    """First few rows of a table. Table names can't be parameterized, so this one is quoted."""
    safe = table.replace('"', '""')
    return q(f'SELECT * FROM "{safe}" LIMIT {int(n)}')
