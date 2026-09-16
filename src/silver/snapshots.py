"""Shared archiving for feeds that describe a single point in time — a weekly ranking, a weekly
projection, an injury report — and would otherwise be overwritten on every build (#117).

The pattern, used by `fantasypros.py`'s weekly rankings, `espn.py`'s weekly projections,
`sleeper.py`'s projections and `nfl_data.py`'s injuries:

- `fetch_*` calls `save_json_snapshot`/`save_parquet_snapshot`, which archives the fetch as a new
  raw file stamped with when it was captured (`captured_at`), unless it is identical to the most
  recently archived snapshot for the same key — a rebuild that changed nothing shouldn't grow the
  archive. It always returns a real, existing path, so a caller that only ever wants "the current
  snapshot" can read it exactly as it read the single raw file this replaces.
- `load_*` reads every archived snapshot for its key with `existing_snapshots` (not just the
  newest), tags each with `captured_at_of`, and writes the full accumulated history to a
  `<table>_snapshots` table. It then calls `refresh_latest_snapshot` to materialize the original
  table name as just the most recently captured snapshot per key — so anything already reading,
  say, `sleeper_projections` keeps working unchanged, and only code that wants history has to know
  `_snapshots` tables exist at all.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

# Sortable (lexicographic order matches capture order) and filename-safe. Microsecond resolution
# because two builds seconds apart is plausible (a fetch retried, a script re-run) and a filename
# collision would silently discard the earlier snapshot instead of archiving both.
_CAPTURED_AT_FORMAT = "%Y%m%dT%H%M%S%f"
_CAPTURED_AT_RE = re.compile(r"_(\d{8}T\d{12})\.")


def new_captured_at() -> str:
    """A stamp for 'right now', used to name a freshly archived snapshot."""
    return datetime.now(timezone.utc).strftime(_CAPTURED_AT_FORMAT)


def existing_snapshots(raw_dir: Path, stem: str, ext: str) -> list[Path]:
    """Every archived snapshot for this key, oldest to newest."""
    return sorted(raw_dir.glob(f"{stem}_*.{ext}"))


def captured_at_of(path: Path) -> str:
    """The capture timestamp stamped into a snapshot's filename."""
    match = _CAPTURED_AT_RE.search(path.name)
    if not match:
        raise ValueError(f"{path} has no captured_at timestamp in its filename")
    return match.group(1)


def save_json_snapshot(raw_dir: Path, stem: str, payload: list) -> tuple[Path, bool]:
    """Archive `payload` (a JSON-serializable list) as a new snapshot, unless it matches the most
    recently archived one for this key. Returns `(path, is_new)`."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = existing_snapshots(raw_dir, stem, "json")
    if existing and json.loads(existing[-1].read_text()) == payload:
        return existing[-1], False
    path = raw_dir / f"{stem}_{new_captured_at()}.json"
    path.write_text(json.dumps(payload))
    return path, True


def save_parquet_snapshot(raw_dir: Path, stem: str, df: pd.DataFrame) -> tuple[Path, bool]:
    """As `save_json_snapshot`, for a DataFrame archived to parquet."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = existing_snapshots(raw_dir, stem, "parquet")
    if existing and pd.read_parquet(existing[-1]).equals(df):
        return existing[-1], False
    path = raw_dir / f"{stem}_{new_captured_at()}.parquet"
    df.to_parquet(path)
    return path, True


def refresh_latest_snapshot(
    con: duckdb.DuckDBPyConnection, table_name: str, snapshots_table: str, key_columns: list[str]
) -> None:
    """(Re)build `table_name` as just the most recently captured snapshot per `key_columns`, read
    from `snapshots_table` (which must carry a `captured_at` column).

    A tie for the newest `captured_at` within a key keeps every row from that snapshot, not one
    row per key — "latest snapshot", not "latest row".
    """
    partition = ", ".join(key_columns)
    con.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT * EXCLUDE (captured_at, __snapshot_rank)
        FROM (
            SELECT *,
                   DENSE_RANK() OVER (PARTITION BY {partition} ORDER BY captured_at DESC)
                       AS __snapshot_rank
            FROM {snapshots_table}
        )
        WHERE __snapshot_rank = 1
    """)
