"""Fetch and load nflverse data (via nfl_data_py) into the DuckDB warehouse.

Every feed below is asked for every season through `_UPCOMING_SEASON`, not just the ones already
finished. Two kinds of season sit inside that one range:

- **Forward-looking** feeds (`schedules`, `rosters`, `depth_chart_snapshots`) describe a season
  *before* it's played, so `_UPCOMING_SEASON` — the one the models project — is exactly the one
  they need.
- **Record-of-play** feeds (`weekly_stats`, `snap_counts`, `injuries`, `depth_charts`, `ngs_data`,
  `ftn_data`, `pbp_punts`, `pfr_advstats_*`) describe a season *after* it's played. Once
  `_UPCOMING_SEASON` is under way, "after it's played" is true for part of it every week, so these
  are asked for it too — a feed that's asked for a season it can't yet answer for just returns
  nothing for that season, the same as it always has for one that hasn't started at all.

The one feed that currently returns nothing for `_UPCOMING_SEASON` in practice is
`pfr_advstats_pass`/`_rush`/`_rec`: PFR's charting publishes on a season-end cadence, not weekly, so
the in-progress season stays absent from those tables until the season finishes (checked directly
against the live feed, week 1 of 2026). Every other record-of-play feed above does publish weekly.
Each fetch function notes via `console.note()` when a requested season comes back empty, rather
than treating it as a failure, so a season that stops publishing (or hasn't started yet) shows up
in build output without stopping the build.

This module never tracks "is this season complete" as a column, because it doesn't need to: a
season is complete once every regular-season game in `schedules` has a `result`.
`src/gold/seasons.py` is that predicate, shared by every gold model that assumes a full season
(a season total, a finish rank, a backtest fold) rather than trusting fetch scope to have kept a
partial season out — which, as of this module fetching `_UPCOMING_SEASON`'s record of play, it no
longer does.
"""

import contextlib
import io
import time
import urllib.error
from pathlib import Path

import duckdb
import nfl_data_py as nfl
import pandas as pd

from src import console
from src.silver import snapshots

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


def _is_not_yet_published(error: Exception) -> bool:
    """True if `error` is a 404 against a per-season nflverse release file.

    The two shapes a 404 shows up in here: `urllib.error.HTTPError` from a direct
    `pandas.read_parquet`/`nfl_data_py` call, or `duckdb.HTTPException` from a `read_parquet` run
    through DuckDB's httpfs. Either means "this season hasn't been published (yet)", the expected
    case for `_UPCOMING_SEASON` before its first game — anything else is a real failure.
    """
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 404
    if isinstance(error, duckdb.HTTPException):
        return error.status_code == 404
    return False


def _note_if_season_missing(df: pd.DataFrame, seasons: list[int], label: str) -> None:
    """Flag any requested season absent from the fetched rows.

    Expected for `_UPCOMING_SEASON` when a feed hasn't caught up to it yet (or hasn't started
    publishing it at all) — a finding to record, not a failure to raise over.
    """
    missing = sorted(set(seasons) - set(df["season"].unique()))
    if missing:
        console.note(f"{label}: no rows for season(s) {missing}")


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
    """Fetch weekly player stats for the given seasons and save raw to parquet.

    One release file per season, so a season with nothing published yet (`_UPCOMING_SEASON` before
    its first game) 404s on its own rather than failing every other season in the request.
    """
    frames = []
    for season in sorted(seasons):
        try:
            frames.append(pd.read_parquet(_STATS_PLAYER_URL.format(season=season)))
        except urllib.error.HTTPError as e:
            if not _is_not_yet_published(e):
                raise
    df = pd.concat(frames, ignore_index=True)
    _note_if_season_missing(df, seasons, "weekly_stats")
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
    """Fetch weekly snap counts for the given seasons and save raw to parquet.

    `import_snap_counts` reads one release file per season internally but concatenates them in a
    single call, so one missing season (`_UPCOMING_SEASON` before its first game) 404s the whole
    request. Fetched one season at a time here instead, so that isolates the same way it does for
    every other feed in this module.
    """
    frames = []
    for season in sorted(seasons):
        try:
            frames.append(nfl.import_snap_counts([season]))
        except urllib.error.HTTPError as e:
            if not _is_not_yet_published(e):
                raise
    df = pd.concat(frames, ignore_index=True)
    _note_if_season_missing(df, seasons, "snap_counts")
    return _save_raw(df, f"snap_counts_{_seasons_label(seasons)}")


def load_snap_counts(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "snap_counts")


def fetch_injuries(seasons: list[int]) -> Path:
    """Fetch weekly injury reports for the given seasons and archive as a new snapshot.

    An injury report is a point-in-time status: the same player-week's Wednesday DNP and Friday
    full participation are two different facts, and nflverse's current-season release updates in
    place as the week progresses — so, unlike every sibling fetch in this module, a re-fetch is
    archived alongside the earlier one rather than overwriting it (#117). The stem carries no
    season range (unlike `weekly_stats`/`schedules`/etc.), since that range grows every year and
    would otherwise split one continuous archive across differently-named files at each year
    boundary. `save_parquet_snapshot` skips writing when nothing has changed since the last
    snapshot, so rebuilding on a quiet day doesn't grow the archive — though any real change,
    including to a season completed years ago, currently re-archives the full multi-season fetch
    rather than just the changed rows, which is the simple, accepted tradeoff per #117.

    Like `fetch_snap_counts`, fetched one season at a time: `import_injuries` reads one release
    file per season but 404s its whole combined request if any single season (`_UPCOMING_SEASON`
    before its first game) isn't published yet.
    """
    frames = []
    for season in sorted(seasons):
        try:
            frames.append(nfl.import_injuries([season]))
        except urllib.error.HTTPError as e:
            if not _is_not_yet_published(e):
                raise
    df = pd.concat(frames, ignore_index=True)
    _note_if_season_missing(df, seasons, "injuries")
    path, is_new = snapshots.save_parquet_snapshot(RAW_DIR, "injuries", df)
    if is_new:
        console.archived(path, len(df))
    else:
        console.note("injuries: unchanged, skipping snapshot")
    return path


def _captured_snapshots(paths: list[Path]) -> list[tuple[Path, str]]:
    """Pair each path with its captured_at, skipping any that predates snapshot archiving.

    Before #117, `fetch_injuries` overwrote `injuries_<season-range>.parquet` on every build (e.g.
    `injuries_2015_2025.parquet`) — files that share the new constant `injuries` stem this module
    globs on, but carry no captured_at suffix at all. Skipping them (rather than raising) is what
    lets a repo that already has one of these left over from before this ticket keep building.
    """
    result = []
    for path in paths:
        try:
            result.append((path, snapshots.captured_at_of(path)))
        except ValueError:
            console.note(f"{path.name}: predates snapshot archiving, skipping")
    return result


def load_injuries() -> None:
    """Load every injuries snapshot archived so far, then materialize `injuries` as just each
    (season, week)'s most recently captured snapshot — so `draft_board.py` and everything else
    already reading that name keeps working unchanged. The full history lives alongside it, in
    `injuries_snapshots`.
    """
    paths = _captured_snapshots(snapshots.existing_snapshots(RAW_DIR, "injuries", "parquet"))
    if not paths:
        raise RuntimeError(
            f"No archived injuries snapshots found in {RAW_DIR} — run fetch_injuries first."
        )

    frames = []
    for path, captured_at in paths:
        df = pd.read_parquet(path)
        df["captured_at"] = captured_at
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE injuries_snapshots AS SELECT * FROM combined")
    snapshots.refresh_latest_snapshot(con, "injuries", "injuries_snapshots", ["season", "week"])
    rows = con.execute("SELECT count(*) FROM injuries").fetchone()[0]
    con.close()

    console.table("injuries", rows)


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
    _note_if_season_missing(df, seasons, "ngs_data")
    return _save_raw(df, f"ngs_{_seasons_label(seasons)}")


def load_ngs_data(raw_path: Path) -> None:
    _load_parquet_to_table(raw_path, "ngs_data")


def fetch_ftn_data(seasons: list[int]) -> Path:
    """Fetch FTN charting data (routes, target quality, play context) and save raw to parquet.

    FTN data is only available from 2022 onward; earlier seasons are dropped. Like
    `fetch_snap_counts`, fetched one season at a time: `import_ftn_data` reads one release file per
    season and 404s its whole combined request if any single season (`_UPCOMING_SEASON` before its
    first game) isn't published yet.
    """
    seasons = [s for s in seasons if s >= 2022]
    # import_ftn_data ends with a bare `print('Downcasting floats.')`, which lands mid-build
    # between two table lines. Swallowed at this one call rather than globally, so a genuine
    # message from anywhere else still gets through.
    frames = []
    with contextlib.redirect_stdout(io.StringIO()):
        for season in sorted(seasons):
            try:
                frames.append(nfl.import_ftn_data([season]))
            except urllib.error.HTTPError as e:
                if not _is_not_yet_published(e):
                    raise
    df = pd.concat(frames, ignore_index=True)
    _note_if_season_missing(df, seasons, "ftn_data")
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

    One release file per season, fetched one at a time: a season with nothing published yet
    (`_UPCOMING_SEASON` before its first game) raises `duckdb.HTTPException` on its own read rather
    than failing every other season if it were read in one combined query.
    """
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    columns = ", ".join(_PUNT_COLUMNS)
    frames = []
    for season in sorted(seasons):
        try:
            frames.append(
                con.sql(
                    f"SELECT {columns} FROM read_parquet('{_PBP_URL.format(season=season)}') "
                    "WHERE play_type = 'punt'"
                ).df()
            )
        except duckdb.HTTPException as e:
            if not _is_not_yet_published(e):
                raise
    con.close()
    df = pd.concat(frames, ignore_index=True)
    _note_if_season_missing(df, seasons, "pbp_punts")
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
    _note_if_season_missing(df, seasons, f"pfr_advstats_{stat_type}")
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
    # Every feed gets the same range, through `_UPCOMING_SEASON` — see the module docstring for why
    # that's safe for a record-of-play feed even though `_UPCOMING_SEASON` may be in progress, or
    # not yet started at all right after this constant is bumped. Rosters specifically: the
    # `players` release is the more natural home for draft capital (`draft_number`/`years_exp`,
    # which is how the rookie arm identifies a first-season player and its draft capital at all),
    # but nflverse publishes it there on a long lag — as of the 2026 preseason it still had no 2026
    # draft class at all, while the roster feed already carried the full board.
    #
    # Bump _UPCOMING_SEASON once a year, after the last one has been played out.
    seasons = list(range(2015, _UPCOMING_SEASON + 1))

    load_weekly_stats(fetch_weekly_stats(seasons))
    load_schedules(fetch_schedules(seasons))
    load_rosters(fetch_rosters(seasons))
    load_snap_counts(fetch_snap_counts(seasons))
    fetch_injuries(seasons)
    load_injuries()
    load_depth_charts(fetch_depth_charts(seasons))
    load_depth_chart_snapshots(fetch_depth_chart_snapshots(seasons))
    load_ids(fetch_ids())
    load_players(fetch_players())
    load_ngs_data(fetch_ngs_data(seasons))
    load_ftn_data(fetch_ftn_data(seasons))
    load_pbp_punts(fetch_pbp_punts(seasons))

    for stat_type in ["pass", "rush", "rec"]:
        load_pfr_advstats(fetch_pfr_advstats(stat_type, seasons), stat_type)
