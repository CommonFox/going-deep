"""Build the consensus (median/floor/ceiling) projection tables from every projection source.

Pure warehouse-to-warehouse SQL — no fetch step, no network, just a transform over tables that
the other source modules (espn, sleeper, fftoday, cbs) and the in-house model
(inhouse_projections.py) have already loaded/built.

Two output tables, because team defenses need a different join key than individual players:
- consensus_projections: skill positions (QB/RB/WR/TE/K), joined through the nflverse `ids`
  player crosswalk (loaded by nfl_data.py) onto a common `gsis_id`. inhouse_projections is the one
  exception — it's already keyed on that same `gsis_id` (it's built from weekly_stats, which uses
  the same nflverse GSIS ID space), so it's unioned in directly with no crosswalk join.
- consensus_dst_projections: team defenses, joined on a normalized team abbreviation (see
  teams.py) instead, since `ids` is player-level and has no team-defense entries.

inhouse_projections can lag behind the other four sources: its prior-year feature data comes from
nflverse, which publishes noticeably slower than the external sites' own current-season
projections, so its latest `target_season` isn't guaranteed to be the season the other four
sources actually represent. consensus_projections' inhouse arm is scoped to that season (derived
from the external sources themselves), not inhouse's own `MAX(target_season)` — otherwise a stale
inhouse projection would get silently blended in alongside four current ones under the same
player, dragging the consensus toward last season's number with no signal that anything was off.
"""

from pathlib import Path

import duckdb

from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K")

_PERCENTILE_AGGREGATES = """
    COUNT(*) AS num_sources,
    MIN(projected_points) AS min_points,
    MAX(projected_points) AS max_points,
    PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY projected_points) AS floor_p20,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY projected_points) AS median_p50,
    PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY projected_points) AS ceiling_p80,
    STDDEV(projected_points) AS stddev_points
"""

# Every join also matches on position, since the `ids` crosswalk has a handful of duplicate
# external IDs among long-retired/free-agent players (verified harmless against current data —
# none of them appear in any of the active-player projection sources below — but matching on
# position too costs nothing and rules it out structurally rather than by luck). `ids` labels
# kickers "PK" where every source here uses "K", so the crosswalk is normalized to "K" first.
_BUILD_SQL = f"""
CREATE OR REPLACE TABLE consensus_projections AS
WITH ids_normalized AS (
    SELECT gsis_id, name, espn_id, sleeper_id, cbs_id, merge_name,
           CASE WHEN position = 'PK' THEN 'K' ELSE position END AS position
    FROM ids
),
-- The season the other four sources actually represent (sleeper_projections.season is text,
-- everyone else's is already numeric) — see module docstring for why inhouse_projections is
-- matched against this instead of its own MAX(target_season).
current_season AS (
    SELECT MAX(season) AS season FROM (
        SELECT MAX(season) AS season FROM espn_projections
        UNION ALL
        SELECT MAX(TRY_CAST(season AS BIGINT)) AS season FROM sleeper_projections
        UNION ALL
        SELECT MAX(season) AS season FROM cbs_projections
        UNION ALL
        SELECT MAX(season) AS season FROM fftoday_projections
    )
),
source_projections AS (
    SELECT ids.gsis_id, ids.name AS player_name, ids.position, 'espn' AS source,
           e.projected_points
    FROM espn_projections e
    JOIN ids_normalized ids ON ids.espn_id = e.espn_id AND ids.position = e.position

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'sleeper', SUM(s.pts_ppr)
    FROM sleeper_projections s
    JOIN ids_normalized ids
        ON TRY_CAST(s.sleeper_id AS DOUBLE) = ids.sleeper_id AND ids.position = s.position
    GROUP BY ids.gsis_id, ids.name, ids.position

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'cbs', c.projected_points
    FROM cbs_projections c
    JOIN ids_normalized ids ON ids.cbs_id = c.cbs_id AND ids.position = c.position
    WHERE c.scoring = 'ppr'

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'fftoday', f.projected_points
    FROM fftoday_projections f
    JOIN ids_normalized ids ON ids.merge_name = f.merge_name AND ids.position = f.position
    WHERE f.scoring = 'ppr'

    UNION ALL

    -- No ids crosswalk join here (see module docstring) and no position-matching safety net
    -- either — that guards against ids' duplicate-external-ID problem, which doesn't apply since
    -- this source never goes through ids at all. Scoped to current_season, not inhouse's own
    -- MAX(target_season) — if inhouse hasn't caught up to the season the other sources represent,
    -- it drops out of the blend entirely rather than contributing a stale number.
    SELECT player_id, player_name, position, 'inhouse', projected_points
    FROM inhouse_projections
    WHERE target_season = (SELECT season FROM current_season)
)
SELECT
    gsis_id,
    ANY_VALUE(player_name) AS player_name,
    ANY_VALUE(position) AS position,
    {_PERCENTILE_AGGREGATES}
FROM source_projections
WHERE gsis_id IS NOT NULL
    AND projected_points IS NOT NULL
    AND position IN {_SKILL_POSITIONS}
GROUP BY gsis_id
"""

# `team` values are normalized (via the normalize_team Python UDF registered below) before this
# runs, since each source represents defenses differently — a full name, a bare nickname, or a
# non-canonical abbreviation (e.g. "LAR" instead of this warehouse's "LA" for the Rams).
_BUILD_DST_SQL = f"""
CREATE OR REPLACE TABLE consensus_dst_projections AS
WITH source_projections AS (
    -- espn_projections.team is already normalized at load time (see espn.py:load_projections).
    SELECT team, 'espn' AS source, projected_points
    FROM espn_projections
    WHERE position = 'DST'

    UNION ALL

    SELECT normalize_team(team), 'sleeper', SUM(pts_ppr)
    FROM sleeper_projections
    WHERE position = 'DEF'
    GROUP BY team

    UNION ALL

    SELECT team, 'cbs', projected_points
    FROM cbs_projections
    WHERE position = 'DST' AND scoring = 'ppr'

    UNION ALL

    SELECT team, 'fftoday', projected_points
    FROM fftoday_projections
    WHERE position = 'DST' AND scoring = 'ppr'
)
SELECT
    team,
    {_PERCENTILE_AGGREGATES}
FROM source_projections
WHERE team IS NOT NULL AND projected_points IS NOT NULL
GROUP BY team
"""


def build_consensus_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    con.execute(_BUILD_DST_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM consensus_projections").fetchone()
    (dst_count,) = con.execute("SELECT COUNT(*) FROM consensus_dst_projections").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: consensus_projections)")
    print(f"Built {dst_count} rows into {WAREHOUSE_PATH} (table: consensus_dst_projections)")


if __name__ == "__main__":
    build_consensus_projections()
