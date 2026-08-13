"""Build the consensus (median/floor/ceiling) projection table from every projection source.

Pure warehouse-to-warehouse SQL — no fetch step, no network, just a transform over tables that
the other source modules (espn, sleeper, fftoday, cbs) have already loaded, joined through the
nflverse `ids` player crosswalk (loaded by nfl_data.py) onto a common `gsis_id`.

DST is excluded: team defenses have no entry in the player-level `ids` crosswalk.
"""

from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Skill positions covered by every source below (DST is excluded — no entry in the player-level
# `ids` crosswalk, since it's a team, not a player).
_SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K")

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
)
SELECT
    gsis_id,
    ANY_VALUE(player_name) AS player_name,
    ANY_VALUE(position) AS position,
    COUNT(*) AS num_sources,
    MIN(projected_points) AS min_points,
    MAX(projected_points) AS max_points,
    PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY projected_points) AS floor_p20,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY projected_points) AS median_p50,
    PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY projected_points) AS ceiling_p80,
    STDDEV(projected_points) AS stddev_points
FROM source_projections
WHERE gsis_id IS NOT NULL
    AND projected_points IS NOT NULL
    AND position IN {_SKILL_POSITIONS}
GROUP BY gsis_id
"""


def build_consensus_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM consensus_projections").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: consensus_projections)")


if __name__ == "__main__":
    build_consensus_projections()
