"""Fetch and load nflverse data (via nfl_data_py) into the DuckDB warehouse."""

from pathlib import Path

import duckdb
import nfl_data_py as nfl

RAW_DIR = Path("data/raw/nfl_data_py")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")


def fetch_weekly_stats(season: int) -> Path:
    """Fetch one season of weekly player stats and save it raw to parquet."""
    df = nfl.import_weekly_data([season])

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"weekly_{season}.parquet"
    df.to_parquet(raw_path)

    print(f"Weekly stats for {season} saved to {raw_path}")
    return raw_path


def load_weekly_stats(raw_path: Path) -> None:
    """Load a raw weekly stats parquet file into the DuckDB warehouse (idempotent)."""
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(
        "CREATE OR REPLACE TABLE weekly_stats AS SELECT * FROM read_parquet(?)",
        [str(raw_path)],
    )
    con.close()

    print(f"Loaded {raw_path} into {WAREHOUSE_PATH} (table: weekly_stats)")


if __name__ == "__main__":
    season = 2023
    path = fetch_weekly_stats(season)
    load_weekly_stats(path)
