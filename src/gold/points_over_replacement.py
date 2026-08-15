"""Build points-over-replacement per league, season, and player.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `weekly_stats`
(already loaded by nfl_data.py) and `league_settings` (already built by league_settings.py).

Recomputes each player-season's real fantasy points under each league's own scoring rules — not
nflverse's canned `fantasy_points_ppr`, which won't match either league's actual pass-TD/INT/
reception values — from weekly_stats' raw counting stats times that league's `league_settings`
coefficients. `league_settings` only has each league's current (2026) scoring row, so those
coefficients are applied uniformly across every historical season in the warehouse. Season-total
points, not per-game: this is a value-over-replacement calculation, not a projection, so durability
(games played) should count rather than be normalized away, and low-game players simply rank low
on their own without needing an artificial games-played floor.

Replacement level is a combined-flex-pool Value-Based-Drafting calculation: each position's
dedicated starters (`team_count x slots`) are filled first, then whatever's left over from
RB/WR/TE is pooled, ranked by points, and FLEX slots (`team_count x flex_slots`) are filled from
that pool regardless of position. A position's replacement level is whoever's first left off after
both its dedicated slots and its share of FLEX are accounted for — so a position that wins more
flex spots in a given season automatically gets a deeper (lower) replacement level, with no
hardcoded split. Both leagues currently have zero superflex slots, so QB has no flex eligibility
and sits outside the pool.

Scoped to skill positions (QB/RB/WR/TE), matching adp_consensus.py/player_baselines.py.
"""

from pathlib import Path

import duckdb
import pandas as pd

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
_FLEX_POSITIONS = ("RB", "WR", "TE")

_SLOT_COLUMNS = {"QB": "qb_slots", "RB": "rb_slots", "WR": "wr_slots", "TE": "te_slots"}

# weekly_stats raw counting stat -> the league_settings column with its per-unit point value.
_STAT_COEFFICIENTS = {
    "passing_yards": "pass_yd_pts",
    "passing_tds": "pass_td_pts",
    "passing_2pt_conversions": "pass_2pt_pts",
    "interceptions": "pass_int_pts",
    "rushing_yards": "rush_yd_pts",
    "rushing_tds": "rush_td_pts",
    "rushing_2pt_conversions": "rush_2pt_pts",
    "receptions": "rec_pts",
    "receiving_yards": "rec_yd_pts",
    "receiving_tds": "rec_td_pts",
    "receiving_2pt_conversions": "rec_2pt_pts",
}
# Every league scores all three fumble-lost types (pass-sack/rush/rec) with one shared value.
_FUMBLE_LOST_COLUMNS = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")

_OUTPUT_COLUMNS = [
    "league_key", "season", "player_id", "player_name", "position", "games_played",
    "league_points", "position_rank", "starters_at_position", "replacement_level_points",
    "points_over_replacement",
]


def _season_totals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    sum_columns = ", ".join(
        f"SUM({c}) AS {c}" for c in (*_STAT_COEFFICIENTS, *_FUMBLE_LOST_COLUMNS)
    )
    return con.sql(f"""
        SELECT
            player_id,
            ANY_VALUE(player_display_name) AS player_name,
            ANY_VALUE(position) AS position,
            season,
            COUNT(*) AS games_played,
            {sum_columns}
        FROM weekly_stats
        WHERE season_type = 'REG'
            AND fantasy_points_ppr IS NOT NULL
            AND position IN {_SKILL_POSITIONS}
        GROUP BY player_id, season
    """).df()


def _league_points(season_totals: pd.DataFrame, league: pd.Series) -> pd.Series:
    points = sum(
        season_totals[stat] * league[coefficient]
        for stat, coefficient in _STAT_COEFFICIENTS.items()
    )
    fumbles_lost = season_totals[list(_FUMBLE_LOST_COLUMNS)].sum(axis=1)
    return points + fumbles_lost * league["fum_lost_pts"]


def _replacement_levels(season_df: pd.DataFrame, league: pd.Series) -> pd.DataFrame:
    """One row per skill position: its starter count (dedicated + earned FLEX) and the
    league-points value of the first player left off after those starters are filled."""
    ranked = {
        pos: season_df[season_df["position"] == pos]
        .sort_values("league_points", ascending=False)
        .reset_index(drop=True)
        for pos in _SKILL_POSITIONS
    }
    dedicated_starters = {
        pos: int(league["team_count"] * league[_SLOT_COLUMNS[pos]]) for pos in _SKILL_POSITIONS
    }

    leftover = pd.concat(
        ranked[pos].iloc[dedicated_starters[pos]:] for pos in _FLEX_POSITIONS
    ).sort_values("league_points", ascending=False)
    flex_starters_count = int(league["team_count"] * league["flex_slots"])
    flex_starters_by_position = leftover.iloc[:flex_starters_count]["position"].value_counts()

    rows = []
    for pos in _SKILL_POSITIONS:
        starters = dedicated_starters[pos] + int(flex_starters_by_position.get(pos, 0))
        cutoff = min(starters, len(ranked[pos]) - 1)
        rows.append({
            "position": pos,
            "starters_at_position": starters,
            "replacement_level_points": ranked[pos].loc[cutoff, "league_points"],
        })
    return pd.DataFrame(rows)


def _build_league_season(season_df: pd.DataFrame, league: pd.Series) -> pd.DataFrame:
    df = season_df.copy()
    df["position_rank"] = (
        df.groupby("position")["league_points"].rank(ascending=False, method="first").astype(int)
    )
    df = df.merge(_replacement_levels(season_df, league), on="position")
    df["points_over_replacement"] = df["league_points"] - df["replacement_level_points"]
    return df


def build_points_over_replacement() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    season_totals = _season_totals(con)
    leagues = con.sql("SELECT * FROM league_settings").df()

    frames = []
    for _, league in leagues.iterrows():
        df = season_totals[["player_id", "player_name", "position", "season", "games_played"]].copy()
        df["league_key"] = league["league_key"]
        df["league_points"] = _league_points(season_totals, league)

        frames.extend(_build_league_season(season_df, league) for _, season_df in df.groupby("season"))

    result = pd.concat(frames, ignore_index=True)[_OUTPUT_COLUMNS]
    con.execute("CREATE OR REPLACE TABLE points_over_replacement AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM points_over_replacement").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: points_over_replacement)")


if __name__ == "__main__":
    build_points_over_replacement()
