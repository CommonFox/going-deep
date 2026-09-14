"""Rest-of-season points: each player's season projection minus what he's already scored.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `draft_board` (the
season-total projection, already rescaled into each league's own scoring and slots) and
`weekly_stats` (recomputed into that same league's scoring via `league_scoring`, completed weeks
only), plus `sleeper_nfl_state` for which season/week is current.

`ros_points = projected_points_adjusted - points_already_scored` is the number a waiver add is
actually judged on: a player worth 150 points over a full season who has already scored 140 of
them is a very different pickup from one who has scored 10, and `projected_points_adjusted` alone
can't tell those two apart.

`points_already_scored` isn't read off `points_over_replacement` because that table only ever
holds *finished* seasons — this needs the season in progress, cut off at whichever week has
actually been played, which is a different slice of the same `weekly_stats` rows. Reuses
`league_scoring`'s stat-to-points mapping rather than re-deriving it, so the two can't drift apart
on the one thing they share.

Scoped to skill positions (QB/RB/WR/TE), matching `points_over_replacement.py` and the projection
this subtracts from. This isn't incidental here the way it is there: `weekly_stats` carries no
K/DST/P rows at all, so leaving them in would silently read every kicker and defense as having
scored zero points no matter how far into the season it is, reporting their full season projection
as still remaining.

A player with no `weekly_stats` rows yet this season — hasn't played a snap, or it's before any
games have been recorded — gets `points_already_scored = 0` and `ros_points` equal to his full
season projection, not null.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.league_scoring import STAT_COLUMNS, league_points

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

_OUTPUT_COLUMNS = [
    "league_key", "season", "player_id", "player_name", "position",
    "projected_points_adjusted", "points_already_scored", "ros_points",
]


def _stats_through_current_week(
    con: duckdb.DuckDBPyConnection, season: int, week: int
) -> pd.DataFrame:
    sum_columns = ", ".join(f"SUM({c}) AS {c}" for c in STAT_COLUMNS)
    return con.sql(f"""
        SELECT player_id, {sum_columns}
        FROM weekly_stats
        WHERE season = {season}
            AND season_type = 'REG'
            AND week <= {week}
            AND position IN {_SKILL_POSITIONS}
        GROUP BY player_id
    """).df()


def _build_league(board: pd.DataFrame, stats: pd.DataFrame, league: pd.Series) -> pd.DataFrame:
    scored = stats[["player_id"]].copy()
    scored["points_already_scored"] = league_points(stats, league)

    df = board.merge(scored, on="player_id", how="left")
    df["points_already_scored"] = df["points_already_scored"].fillna(0.0)
    df["ros_points"] = df["projected_points_adjusted"] - df["points_already_scored"]
    return df


def build_ros_points() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))

    season, week = con.execute("SELECT CAST(season AS BIGINT), week FROM sleeper_nfl_state").fetchone()
    leagues = con.sql("SELECT * FROM league_settings").df()
    stats = _stats_through_current_week(con, season, week)

    board = con.sql(f"""
        SELECT league_key, season, player_id, player_name, position, projected_points_adjusted
        FROM draft_board
        WHERE season = {season} AND position IN {_SKILL_POSITIONS}
    """).df()

    frames = [
        _build_league(board[board["league_key"] == league["league_key"]], stats, league)
        for _, league in leagues.iterrows()
    ]

    result = pd.concat(frames, ignore_index=True)[_OUTPUT_COLUMNS]
    con.execute("CREATE OR REPLACE TABLE ros_points AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM ros_points").fetchone()
    con.close()

    console.table("ros_points", count)


if __name__ == "__main__":
    build_ros_points()
