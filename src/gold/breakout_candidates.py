"""Compare in-house predicted value against preseason ADP to surface ADP-blind breakout candidates.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `inhouse_projections`
(already built by inhouse_projections.py) and `adp_consensus` (already built by adp_consensus.py).

`inhouse_projections` already predicts every player's next-season value with zero ADP as an
input — this table's only job is to rank that prediction into a position-relative percentile and
line it up against where the real-world draft market expects the same player to go, so a player
the model likes but ADP doesn't know about (or ranks low) is directly visible as
`predicted_delta`, rather than the model's opinion only ever being checked against itself.

League-agnostic by design: ranks `inhouse_projections`' existing PPG-based `projected_points`
as-is rather than retraining against either league's own scoring (the way
`points_over_replacement`/`boom_bust` do) — "will this player be good" is a talent/production
question that doesn't hinge on half-PPR-vs-full-PPR scoring nuance, so one number per player-season
rather than one per league.

Unlike `boom_bust.py`, there's no bucketing into Boom/Bust labels and no games-played/injury check —
both describe an *actual* outcome that, for `inhouse_projections`' live target_season, hasn't
happened yet. `predicted_delta` is deliberately left as a continuous score (and left NULL for
players with no preseason ADP at all) for the caller to sort/filter — a high `predicted_delta`
paired with a NULL or deep `consensus_adp` is exactly the "ADP hasn't caught up to this player"
signal being asked for.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_BUILD_SQL = """
CREATE OR REPLACE TABLE breakout_candidates AS
WITH preseason AS (
    SELECT
        gsis_id, season, position, consensus_adp,
        PERCENT_RANK() OVER (
            PARTITION BY season, position ORDER BY consensus_adp DESC
        ) AS preseason_percentile
    FROM adp_consensus
    WHERE consensus_adp IS NOT NULL
),
predicted AS (
    SELECT
        player_id, player_name, position, target_season, projected_points,
        PERCENT_RANK() OVER (
            PARTITION BY target_season, position ORDER BY projected_points ASC
        ) AS predicted_percentile
    FROM inhouse_projections
    WHERE projected_points IS NOT NULL
)
SELECT
    p.target_season, p.player_id, p.player_name, p.position, p.projected_points,
    p.predicted_percentile,
    pre.consensus_adp, pre.preseason_percentile,
    p.predicted_percentile - pre.preseason_percentile AS predicted_delta
FROM predicted p
LEFT JOIN preseason pre
    ON pre.gsis_id = p.player_id AND pre.season = p.target_season AND pre.position = p.position
"""


def build_breakout_candidates() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM breakout_candidates").fetchone()
    con.close()

    console.table("breakout_candidates", count)


if __name__ == "__main__":
    build_breakout_candidates()
