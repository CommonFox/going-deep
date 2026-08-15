"""Build a recency- and playing-time-weighted multi-year PPG baseline per player.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from nflverse's weekly stats
(already loaded by nfl_data.py's fetch_weekly_stats/load_weekly_stats).

For every player, and for every "target season" one year past each season present in the
warehouse, looks back up to `_MAX_YEARS_BACK` prior seasons and computes a recency-weighted PPR
points-per-game baseline — the further back a season is, the less it counts (1.0 / 0.9 / 0.8 /
0.7, the same decay VinGuar/Fantasy-Football-Rankings-With-ML uses for this problem). A season
only counts at all if the player played at least `_MIN_GAMES_PLAYED` games in it: weekly_stats has
plenty of one-game cameo rows (a Week 18 call-up, a player who tore an ACL in Week 2), and letting
those set a full-weight per-game rate would badly distort the baseline rather than just add noise
to it.

Alongside the PPG baseline, also outputs a `weighted_games_per_season` durability signal — the
same recency-weighted average, but of games played rather than points per game — since a player's
expected next-season games played is itself a useful feature (workload correlates with role) and
the multiplier needed to turn a PPG prediction into a season-total point projection.

This is a feature-engineering building block, not a projection itself — it's meant to feed a
future in-house predictive model (prior-year weighted baseline + team context -> next-year PPG).
"""

from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# A season needs at least this many games played before its per-game rate is trusted at all —
# dropped entirely below this, not just down-weighted.
_MIN_GAMES_PLAYED = 6

# How far back to look, and how much each season counts: most recent season full weight, decaying
# 0.1/year out to 4 years back.
_MAX_YEARS_BACK = 4
_RECENCY_WEIGHT_CASE = """
    CASE t.target_season - s.season
        WHEN 1 THEN 1.0
        WHEN 2 THEN 0.9
        WHEN 3 THEN 0.8
        WHEN 4 THEN 0.7
    END
"""

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE player_weighted_baselines AS
WITH season_stats AS (
    SELECT
        player_id,
        ANY_VALUE(player_display_name) AS player_name,
        ANY_VALUE(position) AS position,
        season,
        COUNT(*) AS games_played,
        SUM(fantasy_points_ppr) / COUNT(*) AS ppg_ppr
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
    HAVING COUNT(*) >= {_MIN_GAMES_PLAYED}
),
-- One row per season past every season actually in the warehouse (e.g. seasons 2021-2023 produce
-- target seasons 2022-2024), so the most recent target season is next season's live baseline and
-- earlier ones are backtestable against that season's real outcome.
target_seasons AS (
    SELECT UNNEST(GENERATE_SERIES(MIN(season) + 1, MAX(season) + 1)) AS target_season
    FROM season_stats
),
history AS (
    SELECT
        t.target_season,
        s.player_id,
        s.player_name,
        s.position,
        s.season,
        s.games_played,
        s.ppg_ppr,
        {_RECENCY_WEIGHT_CASE} AS recency_weight
    FROM target_seasons t
    JOIN season_stats s
        ON s.season < t.target_season
        AND t.target_season - s.season <= {_MAX_YEARS_BACK}
),
most_recent_season AS (
    SELECT
        target_season, player_id, player_name, position,
        ROW_NUMBER() OVER (PARTITION BY target_season, player_id ORDER BY season DESC) AS rn
    FROM history
)
SELECT
    h.target_season,
    h.player_id,
    m.player_name,
    m.position,
    COUNT(*) AS seasons_used,
    SUM(h.games_played) AS games_used,
    SUM(h.ppg_ppr * h.recency_weight) / SUM(h.recency_weight) AS weighted_ppg_ppr,
    SUM(h.games_played * h.recency_weight) / SUM(h.recency_weight) AS weighted_games_per_season
FROM history h
JOIN most_recent_season m
    ON m.target_season = h.target_season AND m.player_id = h.player_id AND m.rn = 1
GROUP BY h.target_season, h.player_id, m.player_name, m.position
ORDER BY h.target_season, weighted_ppg_ppr DESC
"""


def build_player_weighted_baselines() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM player_weighted_baselines").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: player_weighted_baselines)")


if __name__ == "__main__":
    build_player_weighted_baselines()
