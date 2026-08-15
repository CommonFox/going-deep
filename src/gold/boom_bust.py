"""Classify each player-season into a preseason-ADP-relative outcome bucket.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `points_over_replacement`
(already built by points_over_replacement.py) and `adp_consensus` (already built by
adp_consensus.py).

Step 3 of the "league-winning RB" idea: turns each player's replacement-level outcome and their
preseason draft capital into percentiles within their season's position pool, so both are
comparable across leagues (different team counts) and scoring formats without a hardcoded PPG or
ADP-spot-count constant. `points_over_replacement` is ranked within (league_key, season, position)
since replacement level is league-specific; `adp_consensus`'s `consensus_adp` is ranked within
(season, position) only, since ADP reflects the real-world draft market rather than either
league's scoring rules.

Both percentiles are computed over the *same* reference population: players with a preseason
`consensus_adp` that season/position. `points_over_replacement` on its own is much deeper than the
ADP-tracked pool (e.g. 135 RBs recorded a stat somewhere in the 2024 season vs. 93 that any ADP
source tracked as draftable) — ranking the actual-outcome side against that full, scrub-padded
pool while ranking the preseason side against the narrower ADP pool would systematically inflate
everyone's percentile_delta upward (more players below you in a bigger pool isn't the same as
actually outperforming your draft slot). Players with no preseason ADP are bucketed separately
below rather than assigned a percentile at all.

Outcome buckets mirror the five archetypes from the source idea, made format-agnostic by using
percentile movement instead of a fixed PPG number or ADP-spot count:
- No Preseason ADP: no `consensus_adp` that season, so there's no preseason expectation to compare
  against (mostly waiver-wire adds no source tracked as draftable) — checked first, ahead of the
  games-played bar below, since "Got Injured" is only a meaningful label for someone who was
  actually expected to contribute; most sub-12-game players in this table are just replacement-
  level scrubs who were never fantasy-relevant, not fallen stars.
- Got Injured: had preseason ADP but played fewer than 12 games (this project's own bar for a
  "full" season, reused from the source idea rather than inventing a new number) — overrides the
  percentile-movement buckets below, since a shortened season explains the outcome rather than
  reflecting a skill/value read.
- Boomed / Returned on ADP / Fine / Busted: a symmetric +/-20-percentile-point band around "met
  expectation" (delta 0), translating the source idea's "beat/lost by 10 ADP spots" into a
  scoring-format- and league-size-independent unit. "Fine" (mild underperformance) and "Returned on
  ADP" (met or modestly beat expectation) split at delta 0, mirroring the source idea's own
  asymmetric shape ("lost 1-9 spots" vs. meeting-or-beating ADP).
"""

from pathlib import Path

import duckdb

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Percentile-points of preseason-vs-actual movement that counts as a genuine boom/bust rather than
# year-to-year noise.
_BOOM_BUST_THRESHOLD = 0.20

# Reused from the source idea's own qualifier for a "full" season: fewer games than this is
# "Got Injured" regardless of percentile movement.
_MIN_GAMES_FOR_OUTCOME = 12

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE boom_bust AS
WITH preseason AS (
    SELECT
        gsis_id, season, position, consensus_adp,
        PERCENT_RANK() OVER (
            PARTITION BY season, position ORDER BY consensus_adp DESC
        ) AS preseason_percentile
    FROM adp_consensus
    WHERE consensus_adp IS NOT NULL
),
tracked AS (
    SELECT
        por.league_key, por.season, por.player_id, por.player_name, por.position,
        por.games_played, por.points_over_replacement,
        pre.consensus_adp, pre.preseason_percentile,
        PERCENT_RANK() OVER (
            PARTITION BY por.league_key, por.season, por.position
            ORDER BY por.points_over_replacement ASC
        ) AS actual_percentile
    FROM points_over_replacement por
    JOIN preseason pre
        ON pre.gsis_id = por.player_id AND pre.season = por.season AND pre.position = por.position
),
untracked AS (
    SELECT
        por.league_key, por.season, por.player_id, por.player_name, por.position,
        por.games_played, por.points_over_replacement,
        CAST(NULL AS DOUBLE) AS consensus_adp, CAST(NULL AS DOUBLE) AS preseason_percentile,
        CAST(NULL AS DOUBLE) AS actual_percentile
    FROM points_over_replacement por
    WHERE NOT EXISTS (
        SELECT 1 FROM preseason pre
        WHERE pre.gsis_id = por.player_id AND pre.season = por.season AND pre.position = por.position
    )
)
SELECT
    *,
    actual_percentile - preseason_percentile AS percentile_delta,
    CASE
        WHEN preseason_percentile IS NULL THEN 'No Preseason ADP'
        WHEN games_played < {_MIN_GAMES_FOR_OUTCOME} THEN 'Got Injured'
        WHEN actual_percentile - preseason_percentile >= {_BOOM_BUST_THRESHOLD} THEN 'Boomed'
        WHEN actual_percentile - preseason_percentile <= -{_BOOM_BUST_THRESHOLD} THEN 'Busted'
        WHEN actual_percentile - preseason_percentile < 0 THEN 'Fine'
        ELSE 'Returned on ADP'
    END AS outcome_bucket
FROM (SELECT * FROM tracked UNION ALL SELECT * FROM untracked)
"""


def build_boom_bust() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM boom_bust").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: boom_bust)")


if __name__ == "__main__":
    build_boom_bust()
