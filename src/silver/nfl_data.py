"""Fetch and load nflverse data (via nfl_data_py) into the DuckDB warehouse."""

from pathlib import Path

import duckdb
import nfl_data_py as nfl
import pandas as pd

RAW_DIR = Path("data/raw/nfl_data_py")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")


def _seasons_label(seasons: list[int]) -> str:
    seasons = sorted(seasons)
    if len(seasons) == 1:
        return str(seasons[0])
    return f"{seasons[0]}_{seasons[-1]}"


def _save_raw(df, filename_stem: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{filename_stem}.parquet"
    df.to_parquet(raw_path)
    print(f"Saved {len(df)} rows to {raw_path}")
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
    return _save_raw(df, f"weekly_{_seasons_label(seasons)}")


def load_weekly_stats(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "weekly_stats")


def fetch_schedules(seasons: list[int]) -> Path:
    """Fetch game schedules for the given seasons and save raw to parquet."""
    df = nfl.import_schedules(seasons)
    return _save_raw(df, f"schedules_{_seasons_label(seasons)}")


def load_schedules(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "schedules")


def fetch_rosters(seasons: list[int]) -> Path:
    """Fetch weekly rosters for the given seasons and save raw to parquet."""
    df = nfl.import_weekly_rosters(seasons)
    return _save_raw(df, f"rosters_{_seasons_label(seasons)}")


def load_rosters(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "rosters")


def fetch_snap_counts(seasons: list[int]) -> Path:
    """Fetch weekly snap counts for the given seasons and save raw to parquet."""
    df = nfl.import_snap_counts(seasons)
    return _save_raw(df, f"snap_counts_{_seasons_label(seasons)}")


def load_snap_counts(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "snap_counts")


def fetch_injuries(seasons: list[int]) -> Path:
    """Fetch weekly injury reports for the given seasons and save raw to parquet."""
    df = nfl.import_injuries(seasons)
    return _save_raw(df, f"injuries_{_seasons_label(seasons)}")


def load_injuries(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "injuries")


def fetch_seasonal_data(seasons: list[int]) -> Path:
    """Fetch season-level aggregated stats for the given seasons and save raw to parquet."""
    df = nfl.import_seasonal_data(seasons)
    return _save_raw(df, f"seasonal_{_seasons_label(seasons)}")


def load_seasonal_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "seasonal_data")


def fetch_depth_charts(seasons: list[int]) -> Path:
    """Fetch weekly depth charts for the given seasons and save raw to parquet."""
    df = nfl.import_depth_charts(seasons)
    return _save_raw(df, f"depth_charts_{_seasons_label(seasons)}")


def load_depth_charts(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "depth_charts")


def fetch_players() -> Path:
    """Fetch master player metadata (bios, draft info, cross-platform IDs) and save raw to parquet."""
    df = nfl.import_players()
    return _save_raw(df, "players")


def load_players(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "players")


def fetch_ngs_data(seasons: list[int]) -> Path:
    """Fetch seasonal Next Gen Stats (passing, receiving, rushing) and save raw to parquet."""
    stat_types = ["passing", "receiving", "rushing"]
    frames = []
    for stat_type in stat_types:
        df = nfl.import_ngs_data(stat_type, seasons)
        df["stat_type"] = stat_type
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    return _save_raw(df, f"ngs_{_seasons_label(seasons)}")


def load_ngs_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ngs_data")


def fetch_ftn_data(seasons: list[int]) -> Path:
    """Fetch FTN charting data (routes, target quality, play context) and save raw to parquet.

    FTN data is only available from 2022 onward; earlier seasons are dropped.
    """
    seasons = [s for s in seasons if s >= 2022]
    df = nfl.import_ftn_data(seasons)
    return _save_raw(df, f"ftn_{_seasons_label(seasons)}")


def load_ftn_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ftn_data")


def fetch_pfr_advstats(stat_type: str, seasons: list[int]) -> Path:
    """Fetch PFR advanced season-level stats (pass/rush/rec/def) and save raw to parquet.

    Not available before 2018 (nfl_data_py raises if any requested season predates that).
    """
    df = nfl.import_seasonal_pfr(stat_type, seasons)
    return _save_raw(df, f"pfr_advstats_{stat_type}_{_seasons_label(seasons)}")


def load_pfr_advstats(raw_path: Path, stat_type: str) -> None:
    _load_parquet_to_table(raw_path, f"pfr_advstats_{stat_type}")


def fetch_ids() -> Path:
    """Fetch the cross-platform player ID crosswalk and save raw to parquet."""
    df = nfl.import_ids()
    return _save_raw(df, "ids")


def load_ids(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ids")


if __name__ == "__main__":
    seasons = list(range(2021, 2024))

    load_weekly_stats(fetch_weekly_stats(seasons))
    load_schedules(fetch_schedules(seasons))
    load_rosters(fetch_rosters(seasons))
    load_snap_counts(fetch_snap_counts(seasons))
    load_injuries(fetch_injuries(seasons))
    load_seasonal_data(fetch_seasonal_data(seasons))
    load_depth_charts(fetch_depth_charts(seasons))
    load_ids(fetch_ids())
    load_players(fetch_players())
    load_ngs_data(fetch_ngs_data(seasons))
    load_ftn_data(fetch_ftn_data(seasons))

    for stat_type in ["pass", "rush"]:
        load_pfr_advstats(fetch_pfr_advstats(stat_type, seasons), stat_type)
