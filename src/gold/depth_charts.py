"""Reconcile the two depth-chart formats into one weekly per-player view.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `depth_charts` (the
legacy per-week format, 2015-2024) and `depth_chart_snapshots` (the dated-snapshot format nflverse
switched to in 2025), both loaded by nfl_data.py.

The two eras do not carry the same information, and this module deliberately does not pretend
otherwise:

- Legacy rows have `depth_team`, a coarse first/second/third string. Every starting receiver
  shares depth_team = 1, so it orders strings, not players.
- Snapshot rows have `pos_rank`, a fine ordinal over the whole position group — receivers run
  1, 2, 3, 4 across the three receiver slots rather than tying.

Collapsing those into a single "rank" column would silently present a coarse signal and a fine one
as the same measurement. Instead each keeps its own column (`string_team` for the legacy era,
`depth_rank` for the snapshot era, each NULL outside it) and they meet in `is_starter`, which both
formats genuinely support. `is_starter` is the column to model on, since it's the only one that
spans 2015-2025 — a feature that exists solely from 2025 on has one season of history and can't
train anything.

Snapshots have no week of their own, so each is assigned the week it describes: the week whose
first regular-season game is the earliest kickoff on or after the snapshot date. Preseason
snapshots therefore roll forward into week 1, and snapshots taken after the last regular-season
game (the feed keeps publishing into March) fall outside the season and are dropped. Where several
snapshots map to the same week, the last one before kickoff wins as the most current picture.
"""

from pathlib import Path

import duckdb

from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# How many of a position a team actually starts, used to turn the snapshot era's fine ordinal into
# the same starter/non-starter call the legacy era makes natively. Three receivers because the
# snapshot feed groups the offense as "3WR 1TE".
_STARTER_SLOTS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE player_depth_chart AS
WITH legacy AS (
    -- depth_position filters out the special-teams duplicates: a back listed as both RB2 and the
    -- kick returner appears twice, and the returning row isn't a statement about offensive depth.
    SELECT
        CAST(season AS BIGINT) AS season,
        CAST(week AS BIGINT) AS week,
        normalize_team(club_code) AS team,
        gsis_id,
        ANY_VALUE(full_name) AS player_name,
        position,
        MIN(CAST(depth_team AS BIGINT)) AS string_team,
        CAST(NULL AS BIGINT) AS depth_rank
    FROM depth_charts
    WHERE formation = 'Offense'
        AND position IN {_SKILL_POSITIONS}
        AND depth_position = position
        AND gsis_id IS NOT NULL
        AND depth_team IS NOT NULL
    GROUP BY season, week, normalize_team(club_code), gsis_id, position
),
-- Each week's kickoff, used as the boundary a snapshot is measured against.
week_start AS (
    SELECT season, week, MIN(CAST(gameday AS DATE)) AS first_game
    FROM schedules
    WHERE game_type = 'REG'
    GROUP BY season, week
),
snapshot_weeks AS (
    SELECT
        s.dt,
        s.season,
        (
            SELECT w.week FROM week_start w
            WHERE w.season = s.season AND w.first_game >= CAST(s.dt AS DATE)
            ORDER BY w.first_game
            LIMIT 1
        ) AS week
    FROM (SELECT DISTINCT dt, season FROM depth_chart_snapshots) s
),
-- The most recent snapshot still ahead of each week's kickoff.
latest_per_week AS (
    SELECT season, week, MAX(dt) AS dt
    FROM snapshot_weeks
    WHERE week IS NOT NULL
    GROUP BY season, week
),
snapshots AS (
    SELECT
        lw.season,
        lw.week,
        normalize_team(d.team) AS team,
        d.gsis_id,
        ANY_VALUE(d.player_name) AS player_name,
        d.pos_abb AS position,
        CAST(NULL AS BIGINT) AS string_team,
        MIN(CAST(d.pos_rank AS BIGINT)) AS depth_rank
    FROM depth_chart_snapshots d
    JOIN latest_per_week lw ON lw.dt = d.dt
    WHERE d.pos_grp = '3WR 1TE'
        AND d.pos_abb IN {_SKILL_POSITIONS}
        AND d.gsis_id IS NOT NULL
    GROUP BY lw.season, lw.week, normalize_team(d.team), d.gsis_id, d.pos_abb
),
combined AS (
    SELECT * FROM legacy
    UNION ALL
    SELECT * FROM snapshots
)
SELECT
    season,
    week,
    team,
    gsis_id,
    player_name,
    position,
    string_team,
    depth_rank,
    COALESCE(
        string_team = 1,
        depth_rank <= CASE position {" ".join(
            f"WHEN '{position}' THEN {slots}" for position, slots in _STARTER_SLOTS.items()
        )} END
    ) AS is_starter
FROM combined
ORDER BY season, week, team, position, COALESCE(depth_rank, string_team)
"""


def build_player_depth_chart() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM player_depth_chart").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: player_depth_chart)")


if __name__ == "__main__":
    build_player_depth_chart()
