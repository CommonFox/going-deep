"""Classify each player-season into a preseason-ADP-relative outcome bucket.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `points_over_replacement`
(already built by points_over_replacement.py), `adp_consensus` (already built by adp_consensus.py),
`league_settings` (for each league's team count) and `player_depth_chart` (to tell a player who got
hurt from one who never had the job).

Step 3 of the "league-winning RB" idea: line each player's actual season up against what his
preseason draft capital implied, in units that survive a change of league size or scoring format.

## Two questions, two measures — the absolute one is the point

There are two different things people mean by "did he boom", and conflating them is how this table
used to mislead:

- *Was he great?* — an absolute question. `finish_tier` / `is_elite_finish` answer it. A top-12
  finish at your position is a top-12 finish whether you cost the 3rd pick or went undrafted.
- *Did he beat his price?* — a relative question. `tier_delta` (and the continuous
  `percentile_delta`) answer it.

The bucket below is built on the absolute measure first, then qualified by the relative one. This
is a deliberate correction. An earlier version defined "Boomed" purely as a +20-percentile-point
*move* from preseason percentile, which is structurally unreachable from the top of the board: a
first-round pick already sits near the 99th percentile and has no room to gain 20 points, so the
metric reported a 0% boom rate for rounds 1-4 and ~25% for round 15 no matter what the players
actually did. That is a fact about the bounded scale, not about football. `is_elite_finish` is
reachable from every draft slot, so rates computed on it are comparable across the board.

## Tiers rather than percentiles

`finish_tier` buckets a player's positional finish into groups of `team_count` — the "RB1 / RB2 /
WR3" language commentators use, derived rather than hardcoded to 12, so a 10-team league gets
10-wide tiers. `expected_tier` applies the same width to his rank *by preseason ADP* within the
season's position pool, so "he was drafted as a mid-range RB2 and finished as an RB1" becomes
`tier_delta = +1`. Both sides are ranks within the same reference population (players with a
`consensus_adp` that season/position), for the same reason the percentile pair below is: the actual
outcome pool is far deeper than the ADP-tracked pool (135 RBs recorded a stat in 2024 vs. 93 any
ADP source tracked as draftable), and ranking one side against a scrub-padded pool would inflate
everyone's delta upward.

`preseason_percentile` / `actual_percentile` / `percentile_delta` are kept alongside as the
continuous version of the same comparison — finer-grained than whole tiers, and still the right
column to sort on. They are simply no longer what the bucket is computed from.

## Outcome buckets

- No Preseason ADP: no `consensus_adp` that season, so there is no preseason expectation to compare
  against (mostly waiver adds no source tracked as draftable) — checked first, ahead of the
  games-played bar, since a shortened season is only a meaningful label for someone who was
  expected to contribute in the first place.
- Got Injured / Never Had The Job: had preseason ADP but played fewer than `_MIN_GAMES_FOR_OUTCOME`
  games. These two used to be one bucket, which conflated the fallen star with the career backup:
  the old single label rose from 18% of round-1 picks to 30% of round-15 picks, which read as late
  picks getting hurt more when it was really that late picks are mostly backups who were never
  going to play. They are split on whether the player was listed as a starter in the majority of
  the weeks he actually appeared in — "when he was on the field, was it his job?" — which separates
  them cleanly: the injured half averages ADP 147, the never-had-the-job half averages ADP 276.
  A player whose played weeks never overlap a depth chart at all (~7%, averaging 3.9 games and ADP
  280) has no evidence of a role by any column available here and is grouped with the latter.
- League Winner: finished inside the top `team_count` at his position *from outside* that
  expectation. The archetype the whole idea is about — the pick that returns a starter's season
  from a bench price.
- Delivered: finished inside the top `team_count` and was drafted to. Not a lesser outcome, just
  not a market-beating one; an early pick that delivers is exactly what it was bought for.
- Beat / Met His Price: finished better than, or level with, the tier his ADP implied, without
  reaching the elite tier.
- Busted: finished `_BUST_TIERS` or more tiers below the one his ADP implied, having played a full
  season — i.e. the failure is a performance read, not an availability one.
- Fine: everything left over — mild underperformance inside the bust threshold.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Reused from the source idea's own qualifier for a "full" season: fewer games than this means the
# season is an availability story, and gets an availability label rather than a value read.
_MIN_GAMES_FOR_OUTCOME = 12

# Share of his *played* weeks a player must have been listed as a starter for a short season to
# read as "got hurt" rather than "was a backup". A majority, so the question is literally "when he
# was on the field, was it his job?".
_STARTER_SHARE_FOR_INJURY = 0.5

# How many whole tiers below his ADP-implied tier counts as a bust rather than noise. One tier is
# only `team_count` positional spots — well inside the year-to-year churn of any position — so a
# single-tier slip is "Fine", not a failure.
_BUST_TIERS = 2

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE boom_bust AS
WITH preseason AS (
    SELECT
        gsis_id, season, position, consensus_adp,
        -- Rank drives the tier; percentile is kept as the continuous version of the same idea.
        RANK() OVER (PARTITION BY season, position ORDER BY consensus_adp ASC) AS adp_rank,
        PERCENT_RANK() OVER (
            PARTITION BY season, position ORDER BY consensus_adp DESC
        ) AS preseason_percentile
    FROM adp_consensus
    WHERE consensus_adp IS NOT NULL
),
-- "When he was on the field, was it his job?" — the depth chart restricted to the weeks he
-- actually recorded a game, so a starter who is hurt from week 4 on isn't diluted by the weeks he
-- spent listed behind his own replacement.
played_role AS (
    SELECT
        d.season,
        d.gsis_id,
        AVG(CASE WHEN d.is_starter THEN 1.0 ELSE 0.0 END) AS started_when_available
    FROM player_depth_chart d
    JOIN weekly_stats w
        ON w.player_id = d.gsis_id AND w.season = d.season AND w.week = d.week
        AND w.season_type = 'REG' AND w.fantasy_points_ppr IS NOT NULL
    GROUP BY d.season, d.gsis_id
),
tracked AS (
    SELECT
        por.league_key, por.season, por.player_id, por.player_name, por.position,
        por.games_played, por.points_over_replacement,
        por.position_rank, por.starters_at_position,
        pre.consensus_adp, pre.adp_rank, pre.preseason_percentile,
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
        por.position_rank, por.starters_at_position,
        CAST(NULL AS DOUBLE) AS consensus_adp, CAST(NULL AS BIGINT) AS adp_rank,
        CAST(NULL AS DOUBLE) AS preseason_percentile,
        CAST(NULL AS DOUBLE) AS actual_percentile
    FROM points_over_replacement por
    WHERE NOT EXISTS (
        SELECT 1 FROM preseason pre
        WHERE pre.gsis_id = por.player_id AND pre.season = por.season
            AND pre.position = por.position
    )
),
combined AS (SELECT * FROM tracked UNION ALL SELECT * FROM untracked),
tiered AS (
    SELECT
        c.*,
        ls.team_count,
        r.started_when_available,
        -- Tier width is the league's team count, not a hardcoded 12: "RB1" means "a top-12 RB"
        -- only because leagues are usually 12-team, and this warehouse holds a 10-team one too.
        CAST(CEIL(c.position_rank * 1.0 / ls.team_count) AS BIGINT) AS finish_tier,
        CAST(CEIL(c.adp_rank * 1.0 / ls.team_count) AS BIGINT) AS expected_tier,
        c.position_rank <= ls.team_count AS is_elite_finish,
        c.position_rank <= c.starters_at_position AS is_startable_finish
    FROM combined c
    JOIN league_settings ls ON ls.league_key = c.league_key
    LEFT JOIN played_role r ON r.gsis_id = c.player_id AND r.season = c.season
)
SELECT
    league_key, season, player_id, player_name, position, games_played,
    points_over_replacement, position_rank, starters_at_position, team_count,
    consensus_adp, preseason_percentile, actual_percentile,
    actual_percentile - preseason_percentile AS percentile_delta,
    finish_tier, expected_tier,
    expected_tier - finish_tier AS tier_delta,
    is_elite_finish, is_startable_finish,
    CASE
        WHEN preseason_percentile IS NULL THEN 'No Preseason ADP'
        WHEN games_played < {_MIN_GAMES_FOR_OUTCOME}
            AND COALESCE(started_when_available, 0) >= {_STARTER_SHARE_FOR_INJURY}
            THEN 'Got Injured'
        WHEN games_played < {_MIN_GAMES_FOR_OUTCOME} THEN 'Never Had The Job'
        WHEN is_elite_finish AND expected_tier - finish_tier >= 1 THEN 'League Winner'
        WHEN is_elite_finish THEN 'Delivered'
        WHEN expected_tier - finish_tier >= 1 THEN 'Beat His Price'
        WHEN expected_tier - finish_tier <= -{_BUST_TIERS} THEN 'Busted'
        WHEN expected_tier = finish_tier THEN 'Met His Price'
        ELSE 'Fine'
    END AS outcome_bucket
FROM tiered
"""


def build_boom_bust() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM boom_bust").fetchone()
    con.close()

    console.table("boom_bust", count)


if __name__ == "__main__":
    build_boom_bust()
