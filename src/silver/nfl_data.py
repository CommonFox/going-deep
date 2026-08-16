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


# nflverse retired the `player_stats` release in favour of `stats_player`, but nfl_data_py 0.3.3
# (the latest release, and seemingly unmaintained) still requests the old path — so
# import_weekly_data 404s for any season published after the switch while silently continuing to
# work for older ones. Read the current release directly rather than pinning the warehouse to
# whatever the last season in the retired release happened to be.
_STATS_PLAYER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/"
    "stats_player_week_{season}.parquet"
)


def fetch_weekly_stats(seasons: list[int]) -> Path:
    """Fetch weekly player stats for the given seasons and save raw to parquet."""
    df = pd.concat(
        [pd.read_parquet(_STATS_PLAYER_URL.format(season=season)) for season in sorted(seasons)],
        ignore_index=True,
    )
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
    # nflverse published jersey_number/draft_number as strings in some seasons and floats in
    # others; concatenated across seasons that's a mixed-type object column, which fastparquet
    # can't serialize. Coercing to a single numeric type resolves it without touching other columns.
    for column in ("jersey_number", "draft_number"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
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


# fetch_seasonal_data/load_seasonal_data used to live here, sourced from nfl_data_py's
# import_seasonal_data — which reads the same retired `player_stats` release as import_weekly_data
# and 404s for the same reason. Nothing in src/ ever read the seasonal_data table, and the
# replacement release (stats_player_reg) has a different shape, so it was dropped rather than
# repointed. Season-level aggregates are derivable from weekly_stats if they're ever wanted.


# nflverse reshaped depth charts from 2025 on: the old per-week rows (season/week/club_code/
# depth_team/...) became dated snapshots (dt/team/pos_grp/pos_rank/...) with no season or week
# column at all. Concatenating the two just unions their columns and leaves 2025 with a null
# season, so seasons past the break are dropped rather than silently half-loaded. Nothing in src/
# reads depth_charts yet; wiring the new format in (deriving season/week from `dt`) is its own
# piece of work, and belongs with whatever first needs a role/snap-share feature.
_DEPTH_CHART_SCHEMA_BREAK = 2025


def fetch_depth_charts(seasons: list[int]) -> Path:
    """Fetch weekly depth charts for the given seasons and save raw to parquet."""
    seasons = [s for s in seasons if s < _DEPTH_CHART_SCHEMA_BREAK]
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
    """Fetch seasonal Next Gen Stats (passing, receiving, rushing) and save raw to parquet.

    NGS tracking data is only available from 2016 onward; earlier seasons are dropped.
    """
    seasons = [s for s in seasons if s >= 2016]
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
    # Same cross-season dtype inconsistency as rosters' jersey_number/draft_number, this time
    # bool in some seasons' files and float in others.
    df["is_trick_play"] = df["is_trick_play"].astype(float)
    return _save_raw(df, f"ftn_{_seasons_label(seasons)}")


def load_ftn_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ftn_data")


def fetch_pfr_advstats(stat_type: str, seasons: list[int]) -> Path:
    """Fetch PFR advanced season-level stats (pass/rush/rec/def) and save raw to parquet.

    Not available before 2018 (nfl_data_py raises if any requested season predates that); earlier
    seasons are dropped.
    """
    seasons = [s for s in seasons if s >= 2018]
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
    # 2015-2025: enough draft classes for recency-weighted backtesting while staying in the modern
    # pass-heavy/PPR era. 2025 was previously excluded on the belief that nflverse hadn't published
    # its player stats yet; in fact nflverse had moved them to the `stats_player` release and
    # nfl_data_py was still asking for the retired one (see _STATS_PLAYER_URL). 2026 is excluded
    # since it hasn't been played yet — it's the season the models project, not train on.
    seasons = list(range(2015, 2026))

    load_weekly_stats(fetch_weekly_stats(seasons))
    load_schedules(fetch_schedules(seasons))
    load_rosters(fetch_rosters(seasons))
    load_snap_counts(fetch_snap_counts(seasons))
    load_injuries(fetch_injuries(seasons))
    load_depth_charts(fetch_depth_charts(seasons))
    load_ids(fetch_ids())
    load_players(fetch_players())
    load_ngs_data(fetch_ngs_data(seasons))
    load_ftn_data(fetch_ftn_data(seasons))

    for stat_type in ["pass", "rush"]:
        load_pfr_advstats(fetch_pfr_advstats(stat_type, seasons), stat_type)
