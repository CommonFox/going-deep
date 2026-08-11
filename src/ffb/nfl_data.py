"""Fetch and load nflverse data (via nfl_data_py) into the DuckDB warehouse."""

from pathlib import Path

import duckdb
import nfl_data_py as nfl

RAW_DIR = Path("data/raw/nfl_data_py")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")


def _seasons_label(seasons: list[int]) -> str:
    seasons = sorted(seasons)
    if len(seasons) == 1:
        return str(seasons[0])
    return f"{seasons[0]}_{seasons[-1]}"


def _save_raw(df, name: str, seasons: list[int]) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{name}_{_seasons_label(seasons)}.parquet"
    df.to_parquet(raw_path)
    print(f"{name} for {seasons} saved to {raw_path}")
    return raw_path


def _load_parquet_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw parquet file into a DuckDB table (idempotent)."""
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(
        f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet(?)",
        [str(raw_path)],
    )
    con.close()

    print(f"Loaded {raw_path} into {WAREHOUSE_PATH} (table: {table_name})")


def fetch_weekly_stats(seasons: list[int]) -> Path:
    """Fetch weekly player stats for the given seasons and save raw to parquet."""
    df = nfl.import_weekly_data(seasons)
    return _save_raw(df, "weekly", seasons)


def load_weekly_stats(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "weekly_stats")


def fetch_schedules(seasons: list[int]) -> Path:
    """Fetch game schedules for the given seasons and save raw to parquet."""
    df = nfl.import_schedules(seasons)
    return _save_raw(df, "schedules", seasons)


def load_schedules(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "schedules")


def fetch_rosters(seasons: list[int]) -> Path:
    """Fetch weekly rosters for the given seasons and save raw to parquet."""
    df = nfl.import_weekly_rosters(seasons)
    return _save_raw(df, "rosters", seasons)


def load_rosters(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "rosters")


if __name__ == "__main__":
    seasons = list(range(2021, 2024))

    load_weekly_stats(fetch_weekly_stats(seasons))
    load_schedules(fetch_schedules(seasons))
    load_rosters(fetch_rosters(seasons))
