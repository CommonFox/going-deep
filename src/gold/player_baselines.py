"""Build a recency- and playing-time-weighted multi-year baseline per player.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from nflverse's weekly stats
(already loaded by nfl_data.py's fetch_weekly_stats/load_weekly_stats), plus snap counts joined
through the `ids` crosswalk.

For every player, and for every "target season" one year past each season present in the
warehouse, looks back up to `_MAX_YEARS_BACK` prior seasons and computes a recency-weighted
average — the further back a season is, the less it counts (1.0 / 0.9 / 0.8 / 0.7, the same decay
VinGuar/Fantasy-Football-Rankings-With-ML uses for this problem). A season only counts at all if
the player played at least `_MIN_GAMES_PLAYED` games in it: weekly_stats has plenty of one-game
cameo rows (a Week 18 call-up, a player who tore an ACL in Week 2), and letting those set a
full-weight per-game rate would badly distort the baseline rather than just add noise to it.

`weighted_ppg_ppr` is the headline column, but a single points-per-game number throws away
everything about *how* those points were earned, which is most of what makes next season
predictable. Volume is far more stable year over year than touchdown rate, so the same 17 PPG
means something very different at 9 targets a game than at 4 targets and three times the league's
touchdown rate. So the same recency weighting is also applied to:

- **Volume** (`_PER_GAME_TOTALS`) — targets, receptions, carries, pass attempts, yards and
  touchdowns, each as a per-game rate. Touchdowns are deliberately kept separate from yards rather
  than folded into a points total, so a model can see an unsustainable scoring rate as its own
  signal instead of having it baked into the same number as the volume that generated it.
- **Role** (`_PER_GAME_AVERAGES`) — target share, air-yards share, WOPR and snap share: what
  fraction of an offense the player actually commanded, which survives a change of team or
  quarterback far better than raw yardage does.
- **Efficiency** (`_PER_GAME_AVERAGES`) — passing/rushing/receiving EPA and CPOE. Sparse by
  construction (a TE has no passing EPA), left NULL rather than zero-filled so a consumer can tell
  "didn't do this" from "did this badly".
- **`weighted_td_regressed_ppg`** — PPG recomputed with the player's actual touchdowns swapped out
  for the touchdowns their yardage would have produced at that season's league-average rate. A
  direct "what would this have been with neutral touchdown luck" number, encoding a relationship
  that a model would otherwise have to learn as a multi-way interaction from a few thousand rows.

Alongside all of that, `weighted_games_per_season` is the same recency-weighted average applied to
games played — a durability signal, since a player's expected next-season games played is itself a
useful feature (workload correlates with role).

This is a feature-engineering building block, not a projection itself — it feeds
inhouse_projections.py, which imports the column lists below rather than restating them.
"""

from pathlib import Path

import duckdb

from src import console

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

# Counting stats, summed over a season then divided by games played. Keyed by the output column
# suffix, valued by the weekly_stats column being counted.
_PER_GAME_TOTALS = {
    "targets": "targets",
    "receptions": "receptions",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
    "carries": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "pass_attempts": "attempts",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "passing_interceptions": "passing_interceptions",
}

# Stats weekly_stats already expresses as a per-week rate or share, so a season value is their mean
# over the weeks that have one. AVG skips NULLs, which is what's wanted here: the efficiency
# columns are only populated in weeks the player actually did the thing (racr/EPA/CPOE are NULL for
# a receiver who wasn't targeted), so averaging over played games instead would silently drag a
# specialist's rate toward zero.
_PER_GAME_AVERAGES = {
    "target_share": "target_share",
    "air_yards_share": "air_yards_share",
    "wopr": "wopr",
    "passing_epa": "passing_epa",
    "rushing_epa": "rushing_epa",
    "receiving_epa": "receiving_epa",
    "passing_cpoe": "passing_cpoe",
}

# Populated from snap_counts rather than weekly_stats, so it's joined in separately below.
_SNAP_SHARE_COLUMN = "snap_share"

# The component columns this table exposes beyond weighted_ppg_ppr/weighted_games_per_season.
# inhouse_projections.py imports this instead of restating the list, so a column added here can't
# silently fail to reach the model.
_COMPONENT_COLUMNS = (
    [f"weighted_{name}_per_game" for name in _PER_GAME_TOTALS]
    + [f"weighted_{name}" for name in _PER_GAME_AVERAGES]
    + [f"weighted_{_SNAP_SHARE_COLUMN}", "weighted_td_regressed_ppg"]
)

# PPR values for the touchdowns swapped out in weighted_td_regressed_ppg. These mirror nflverse's
# own fantasy_points_ppr formula, which is what's being adjusted — if that formula ever changes,
# these have to change with it or the swap stops being a like-for-like substitution.
_RECEIVING_TD_POINTS = 6
_RUSHING_TD_POINTS = 6
_PASSING_TD_POINTS = 4

# A position-season needs this many league-wide yards before its own touchdown-per-yard rate is
# trusted; below it, the rate is borrowed from every position pooled. Without the guard, positions
# that barely do a thing produce absurd rates off a tiny denominator — RBs threw for 3 touchdowns
# on 9 yards league-wide in 2024, a rate of 0.33 touchdowns *per yard*, which would have credited
# every pass-catching back with phantom points.
_MIN_LEAGUE_YARDS_FOR_RATE = 5000


def _weighted(expression: str, alias: str) -> str:
    """Recency-weighted mean of a per-season value, skipping seasons where it's NULL.

    The FILTER matters for the sparse efficiency columns: without it a player with EPA in two of
    three qualifying seasons would have the numerator skip the NULL season while the denominator
    still counted its weight, quietly shrinking the result toward zero in proportion to how much
    was missing.
    """
    return (
        f"SUM({expression} * h.recency_weight) FILTER (WHERE {expression} IS NOT NULL)\n"
        f"        / SUM(h.recency_weight) FILTER (WHERE {expression} IS NOT NULL) AS {alias}"
    )


_SEASON_STAT_SELECTS = ",\n".join(
    [f"        SUM({column}) / COUNT(*) AS {name}_per_game"
     for name, column in _PER_GAME_TOTALS.items()]
    + [f"        AVG({column}) AS {name}" for name, column in _PER_GAME_AVERAGES.items()]
)

_HISTORY_PASSTHROUGH = ",\n".join(
    f"        s.{name}" for name in
    [f"{name}_per_game" for name in _PER_GAME_TOTALS]
    + list(_PER_GAME_AVERAGES)
    + [_SNAP_SHARE_COLUMN, "td_regressed_ppg"]
)

_WEIGHTED_SELECTS = ",\n    ".join(
    [_weighted(f"h.{name}_per_game", f"weighted_{name}_per_game") for name in _PER_GAME_TOTALS]
    + [_weighted(f"h.{name}", f"weighted_{name}") for name in _PER_GAME_AVERAGES]
    + [
        _weighted(f"h.{_SNAP_SHARE_COLUMN}", f"weighted_{_SNAP_SHARE_COLUMN}"),
        _weighted("h.td_regressed_ppg", "weighted_td_regressed_ppg"),
    ]
)

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE player_weighted_baselines AS
WITH snap_share_by_season AS (
    -- snap_counts is keyed on PFR's player id, so it reaches gsis_id through the `ids` crosswalk
    -- (99%+ of qualifying player-seasons match). Grouped to one row per player-season before it
    -- joins anything, so a duplicate crosswalk row can't fan out the season_stats join.
    SELECT i.gsis_id AS player_id, sc.season, AVG(sc.offense_pct) AS {_SNAP_SHARE_COLUMN}
    FROM snap_counts sc
    JOIN ids i ON i.pfr_id = sc.pfr_player_id
    WHERE sc.game_type = 'REG' AND i.gsis_id IS NOT NULL
    GROUP BY i.gsis_id, sc.season
),
-- League-average touchdowns per yard, for the weighted_td_regressed_ppg swap. Per position, since
-- a tight end scores at a genuinely different rate per yard than a running back, but falling back
-- to every position pooled when a position-season is too thin to estimate from (see
-- _MIN_LEAGUE_YARDS_FOR_RATE).
league_rates_pooled AS (
    SELECT
        season,
        SUM(receiving_tds) / NULLIF(SUM(receiving_yards), 0) AS receiving_td_per_yard,
        SUM(rushing_tds) / NULLIF(SUM(rushing_yards), 0) AS rushing_td_per_yard,
        SUM(passing_tds) / NULLIF(SUM(passing_yards), 0) AS passing_td_per_yard
    FROM weekly_stats
    WHERE season_type = 'REG' AND position IN {_SKILL_POSITIONS}
    GROUP BY season
),
league_rates AS (
    SELECT
        p.season,
        p.position,
        COALESCE(
            CASE WHEN SUM(p.receiving_yards) >= {_MIN_LEAGUE_YARDS_FOR_RATE}
                 THEN SUM(p.receiving_tds) / NULLIF(SUM(p.receiving_yards), 0) END,
            ANY_VALUE(lp.receiving_td_per_yard)
        ) AS receiving_td_per_yard,
        COALESCE(
            CASE WHEN SUM(p.rushing_yards) >= {_MIN_LEAGUE_YARDS_FOR_RATE}
                 THEN SUM(p.rushing_tds) / NULLIF(SUM(p.rushing_yards), 0) END,
            ANY_VALUE(lp.rushing_td_per_yard)
        ) AS rushing_td_per_yard,
        COALESCE(
            CASE WHEN SUM(p.passing_yards) >= {_MIN_LEAGUE_YARDS_FOR_RATE}
                 THEN SUM(p.passing_tds) / NULLIF(SUM(p.passing_yards), 0) END,
            ANY_VALUE(lp.passing_td_per_yard)
        ) AS passing_td_per_yard
    FROM weekly_stats p
    JOIN league_rates_pooled lp ON lp.season = p.season
    WHERE p.season_type = 'REG' AND p.position IN {_SKILL_POSITIONS}
    GROUP BY p.season, p.position
),
season_stats AS (
    SELECT
        w.player_id,
        ANY_VALUE(w.player_display_name) AS player_name,
        ANY_VALUE(w.position) AS position,
        w.season,
        COUNT(*) AS games_played,
        SUM(w.fantasy_points_ppr) / COUNT(*) AS ppg_ppr,
        -- The same points, with the player's own touchdowns removed and the touchdowns their
        -- yardage would have produced at league average added back. Yardage, receptions and
        -- turnovers are left exactly as they were: this isolates touchdown luck rather than
        -- rebuilding the whole stat line.
        (
            SUM(w.fantasy_points_ppr)
            - {_RECEIVING_TD_POINTS} * SUM(w.receiving_tds)
            - {_RUSHING_TD_POINTS} * SUM(w.rushing_tds)
            - {_PASSING_TD_POINTS} * SUM(w.passing_tds)
            + {_RECEIVING_TD_POINTS} * SUM(w.receiving_yards) * ANY_VALUE(r.receiving_td_per_yard)
            + {_RUSHING_TD_POINTS} * SUM(w.rushing_yards) * ANY_VALUE(r.rushing_td_per_yard)
            + {_PASSING_TD_POINTS} * SUM(w.passing_yards) * ANY_VALUE(r.passing_td_per_yard)
        ) / COUNT(*) AS td_regressed_ppg,
        ANY_VALUE(ss.{_SNAP_SHARE_COLUMN}) AS {_SNAP_SHARE_COLUMN},
{_SEASON_STAT_SELECTS}
    FROM weekly_stats w
    JOIN league_rates r ON r.season = w.season AND r.position = w.position
    LEFT JOIN snap_share_by_season ss
        ON ss.player_id = w.player_id AND ss.season = w.season
    WHERE w.season_type = 'REG'
        AND w.fantasy_points_ppr IS NOT NULL
        AND w.position IN {_SKILL_POSITIONS}
    GROUP BY w.player_id, w.season
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
{_HISTORY_PASSTHROUGH},
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
    SUM(h.games_played * h.recency_weight) / SUM(h.recency_weight) AS weighted_games_per_season,
    {_WEIGHTED_SELECTS}
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

    console.table("player_weighted_baselines", count)


if __name__ == "__main__":
    build_player_weighted_baselines()
