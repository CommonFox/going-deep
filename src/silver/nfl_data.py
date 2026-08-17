"""Fetch and load nflverse data (via nfl_data_py) into the DuckDB warehouse."""

import contextlib
import io
import time
import urllib.error
from pathlib import Path

import duckdb
import nfl_data_py as nfl
import pandas as pd

from src import console

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
    console.archived(raw_path, len(df))
    return raw_path


def _load_parquet_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw parquet file into a DuckDB table (idempotent)."""
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(
        f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet(?)",
        [str(raw_path)],
    )
    rows = con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
    con.close()

    console.table(table_name, rows)


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
# column at all. The two can't share a table — concatenating them just unions the columns and
# leaves every 2025 row with a null season — so each era is archived and loaded in its own shape,
# and src/gold/depth_charts.py reconciles them into one weekly view.
_DEPTH_CHART_SCHEMA_BREAK = 2025

# The season the models project: not yet played, so it has no stats, but its schedule and
# depth charts are already published. Bump this once a year.
_UPCOMING_SEASON = 2026


def fetch_depth_charts(seasons: list[int]) -> Path:
    """Fetch legacy per-week depth charts (pre-2025 format) and save raw to parquet."""
    seasons = [s for s in seasons if s < _DEPTH_CHART_SCHEMA_BREAK]
    df = nfl.import_depth_charts(seasons)
    return _save_raw(df, f"depth_charts_{_seasons_label(seasons)}")


def load_depth_charts(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "depth_charts")


def fetch_depth_chart_snapshots(seasons: list[int]) -> Path:
    """Fetch dated depth-chart snapshots (2025-on format) and save raw to parquet."""
    seasons = [s for s in seasons if s >= _DEPTH_CHART_SCHEMA_BREAK]
    df = nfl.import_depth_charts(seasons)
    # The snapshot feed carries no season column — it's implicit in the release requested — so it's
    # stamped on here. Without it a multi-season archive couldn't be told apart after the fact, and
    # a snapshot's own `dt` can't stand in: a season's snapshots run from the previous August into
    # the following March, so calendar year and season year disagree for a third of them.
    df["season"] = df["dt"].str.slice(0, 4).astype(int)
    df.loc[df["dt"].str.slice(5, 7) < "07", "season"] -= 1
    return _save_raw(df, f"depth_chart_snapshots_{_seasons_label(seasons)}")


def load_depth_chart_snapshots(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "depth_chart_snapshots")


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
    # import_ftn_data ends with a bare `print('Downcasting floats.')`, which lands mid-build
    # between two table lines. Swallowed at this one call rather than globally, so a genuine
    # message from anywhere else still gets through.
    with contextlib.redirect_stdout(io.StringIO()):
        df = nfl.import_ftn_data(seasons)
    # Same cross-season dtype inconsistency as rosters' jersey_number/draft_number, this time
    # bool in some seasons' files and float in others.
    df["is_trick_play"] = df["is_trick_play"].astype(float)
    return _save_raw(df, f"ftn_{_seasons_label(seasons)}")


def load_ftn_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ftn_data")


# Play-by-play, restricted to punt plays. nfl_data_py's import_pbp_data 404s on recent seasons for
# the same reason import_weekly_data does (see _STATS_PLAYER_URL), so the release parquet is read
# directly.
_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
)

# The punt-play columns worth archiving. Everything needed to reconstruct a punter's scoring line
# under any league's rules, plus the context that plausibly explains it: where the punt started
# (`yardline_100`, which caps how far a punter can kick before the touchback risk dominates), and
# the weather and roof a punt was struck in.
_PUNT_COLUMNS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam",
    "punter_player_id", "punter_player_name",
    "yardline_100", "kick_distance", "return_yards", "touchback",
    "punt_inside_twenty", "punt_in_endzone", "punt_out_of_bounds", "punt_downed",
    "punt_fair_catch", "punt_blocked",
    "punt_returner_player_id", "punt_returner_player_name",
    "roof", "surface", "temp", "wind",
]


def fetch_pbp_punts(seasons: list[int]) -> Path:
    """Fetch every punt play for the given seasons and save raw to parquet.

    Play-by-play is the only nflverse feed carrying where a punt actually came to rest, which is
    what `punts inside the 10` — a scoring category in the ESPN league and in no per-player feed —
    has to be derived from. A full pbp archive is ~370 columns over ~50k plays a season; punts are
    ~2k of those plays, so the rows are filtered and the columns pruned at fetch time rather than
    archiving two orders of magnitude more data than any punt model will ever read. DuckDB pushes
    both down into ranged reads against the remote parquet, so only the relevant column chunks come
    over the wire.
    """
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    columns = ", ".join(_PUNT_COLUMNS)
    df = pd.concat(
        [
            con.sql(
                f"SELECT {columns} FROM read_parquet('{_PBP_URL.format(season=season)}') "
                "WHERE play_type = 'punt'"
            ).df()
            for season in sorted(seasons)
        ],
        ignore_index=True,
    )
    con.close()
    return _save_raw(df, f"pbp_punts_{_seasons_label(seasons)}")


def load_pbp_punts(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "pbp_punts")


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
    """Fetch the cross-platform player ID crosswalk and save raw to parquet.

    import_ids() reads straight from raw.githubusercontent.com with no retry of its own, and that
    endpoint 429s under light, unpredictable load unrelated to us — so retry with backoff here
    rather than failing the whole build over a transient rate limit.
    """
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            df = nfl.import_ids()
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == max_attempts - 1:
                raise
            delay = 30 * (2**attempt)
            console.note(f"import_ids hit HTTP 429, retrying in {delay}s "
                         f"(attempt {attempt + 1}/{max_attempts})")
            time.sleep(delay)
    return _save_raw(df, "ids")


def load_ids(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ids")


if __name__ == "__main__":
    # 2015 onward: enough draft classes for recency-weighted backtesting while staying in the
    # modern pass-heavy/PPR era. 2025 was previously excluded on the belief that nflverse hadn't
    # published its player stats yet; in fact nflverse had moved them to the `stats_player` release
    # and nfl_data_py was still asking for the retired one (see _STATS_PLAYER_URL).
    #
    # Bump _UPCOMING_SEASON once a year, after the last one has been played out.
    played_seasons = list(range(2015, _UPCOMING_SEASON))

    # Three of these feeds describe a season *before* it's played rather than after, and the
    # upcoming season is exactly the one the models project — so they're fetched a year further
    # forward than everything else. The schedule is published in May; depth-chart snapshots run
    # from the March after the previous season right through the summer, which is what lets
    # inhouse_projections know who is actually starting for the season it's projecting rather than
    # inferring role from last year's box scores; and preseason rosters carry `draft_number` and
    # `years_exp`, which is how the rookie arm gets draft capital and identifies a first-season
    # player at all. Every other feed here is a record of games already played, and asking for a
    # season that hasn't happened returns nothing.
    #
    # Rosters specifically: the `players` release is the more natural home for draft capital, but
    # nflverse publishes it there on a long lag — as of the 2026 preseason it still had no 2026
    # draft class at all, while the roster feed already carried the full board. Reading draft
    # capital from rosters is what makes the rookie arm work in the season it's needed.
    forward_looking_seasons = played_seasons + [_UPCOMING_SEASON]

    load_weekly_stats(fetch_weekly_stats(played_seasons))
    load_schedules(fetch_schedules(forward_looking_seasons))
    load_rosters(fetch_rosters(forward_looking_seasons))
    load_snap_counts(fetch_snap_counts(played_seasons))
    load_injuries(fetch_injuries(played_seasons))
    load_depth_charts(fetch_depth_charts(played_seasons))
    load_depth_chart_snapshots(fetch_depth_chart_snapshots(forward_looking_seasons))
    load_ids(fetch_ids())
    load_players(fetch_players())
    load_ngs_data(fetch_ngs_data(played_seasons))
    load_ftn_data(fetch_ftn_data(played_seasons))
    load_pbp_punts(fetch_pbp_punts(played_seasons))

    for stat_type in ["pass", "rush", "rec"]:
        load_pfr_advstats(fetch_pfr_advstats(stat_type, played_seasons), stat_type)
