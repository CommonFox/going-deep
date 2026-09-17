"""Build `weekly_player_context`: one row per (league_key, season, week, player) — issue #124, the
last table in the weekly player context epic (#108).

Pure warehouse-to-warehouse SQL — no fetch step, no network. Every input is already a gold table:
`weekly_projections`, `game_environment`, `defense_vs_position`, `player_role_trend` and
`weekly_outcome_rates`, plus `draft_board`/`league_settings` for identity and league shape. This
table adds no new fact of its own beyond `weekly_position_rank`/`weekly_position_tier`, derived here
because nothing upstream already ranks a week's projections within a league.

## Per-`league_key`, following `draft_board`'s shape, not `weekly_projections`' shared one

`weekly_projections` stays one shared table because it doesn't resolve "per league" itself — see
its own docstring. This table has to: two of its five inputs (`defense_vs_position`,
`weekly_outcome_rates`) are already keyed by `league_key`, because they rescore `weekly_stats`
under each league's own coefficients, and `weekly_position_tier` needs a league's own `team_count`.
So a `weekly_projections` row fans out to exactly one league here, picked by matching its own
`scoring` column against `league_scoring_basis` (`SELECT DISTINCT league_key, scoring FROM
draft_board`) — the identical lookup `waiver_rankings.py` already uses to go from a league to the
scoring basis its board was priced in, rather than re-deriving `rec_pts -> scoring` a third time.

## Flat and unblended, every column traceable to the table it came from

No composite score, no weighting, no recommendation — the whole design point of #108, so that #114
can promote one input later as a change to a consumer rather than a rebuild of this table. Every
joined column keeps the source table's own name, prefixed with a short tag naming where it came
from (`game_`, `dvp_`, `role_`, `outcome_`) precisely so a column is traceable back to its table by
name alone, which is also what keeps three same-named columns — `games_observed` means a different
thing in `defense_vs_position`, `player_role_trend` and `weekly_outcome_rates` — from colliding into
one. `dvp_` and not `matchup_`: CONTEXT.md reserves **Matchup** for a head-to-head fantasy pairing
and carves out **Defense vs. position** specifically to stop the drift onto it, while allowing "DvP"
as code shorthand.

## Identity and team: through `draft_board`, not a second crosswalk

`weekly_projections` carries no `team` column, but `game_environment` is keyed on (season, week,
team) and needs one to join on. `draft_board` already resolved one per (league_key, player_id) —
including defenses, whose `team` is their own player_id — so this reads it from there rather than
building a second identity pass, the same reuse `weekly_projections` itself makes of `draft_board`
for `sleeper_id`/`espn_id`.

## The five joins, each on the key its own table is actually built around

- `game_environment` — on (season, week, team): the player's own team's game that week. Carries
  `implied_team_total`, `gamescript_lean`, `kickoff` and conditions (`roof`/`temp`/`wind`), plus
  `opponent`, which the next join needs and which is otherwise the only way to know who a defense
  figure is even about.
- `defense_vs_position` — on (league_key, season, week, defense_team, position), where
  `defense_team` is the opponent read off `game_environment` above, not a second schedule lookup.
  A QB and a WR facing the same defense the same week get that defense's QB-specific and
  WR-specific figures, never each other's.
- `player_role_trend` — on (player_id, season, week) only. Role isn't rescored by league (see that
  table's own docstring: "nothing here touches league scoring"), so it carries no `league_key` on
  either side and both leagues' rows for the same player-week get identical figures.
- `weekly_outcome_rates` — on (league_key, player_id, season, week): the two leagues can and do
  differ here, since a player's own scoring history is rescored under each league's coefficients.
- `draft_board` — on (league_key, player_id), for `team` alone, as above.

Every one of the five is a LEFT JOIN. Missing inputs are the normal case here, not an error, for
the same reasons those tables' own docstrings give — a defense's own season opener, a player not
yet on a depth chart, a position with no `weekly_outcome_rates` coverage at all (K, DST). A miss
leaves that table's columns null while every other column on the row stays intact, so "no game
lined up yet" stays distinguishable from "the game happened and every source measured him" the way
CONTEXT.md's whole weekly-context section insists on.

## `weekly_position_rank` / `weekly_position_tier`

Where this player's own weekly projection sits among his position, in this league, this week —
ranked on `sleeper_points`, the primary points number `weekly_projections` itself carries (its own
docstring: FantasyPros contributes a rank, not a points total, so it isn't a second candidate to
rank on). `RANK()` orders `sleeper_points DESC NULLS LAST` within (league_key, season, week,
position), so a tie shares the lower rank number the same way `defense_vs_position.rank` does
(`method="min"`), and putting nulls last keeps a missing projection from displacing the real ranks
upward — the real values still land at 1..N exactly as if the null rows weren't there. The rank
itself is then nulled back out for exactly those rows: "last place, arbitrarily" is not a fact worth
reporting, only "unranked" is.

`weekly_position_tier` buckets that rank into groups of the league's own `team_count` —
`CEIL(rank / team_count)` — the identical shape `boom_bust.finish_tier` already uses for a season
finish, so a 12-team league's QB13 lands in tier 2 the same way a season-long QB13 finish would.

## Row count is the correctness check

Acceptance for this table is that its row count matches `weekly_projections`' own exactly: every
`weekly_projections` row's `scoring` matches exactly one of the two current leagues, so the fan-out
into `league_key` is one-to-one, and everything after that is a LEFT JOIN, which can drop nothing
and duplicate a row only if one of the five tables above stopped being unique on the key this joins
it by. `build_weekly_player_context` checks the two counts and notes a mismatch rather than
silently shipping a table that gained or lost rows on a join.

## Out of scope

No column here is a blend of two others, and nothing here is weighted or scored — #114 decides
which of these inputs earns a coefficient, and this table's whole point is to let that happen as a
change to a consumer rather than a rebuild. Displaying any of it is #109/#110.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_BUILD_SQL = """
CREATE OR REPLACE TABLE weekly_player_context AS
WITH league_scoring_basis AS (
    SELECT DISTINCT league_key, scoring FROM draft_board
),
identity AS (
    SELECT league_key, player_id, team FROM draft_board
),
base AS (
    SELECT lsb.league_key, wp.*
    FROM weekly_projections wp
    JOIN league_scoring_basis lsb ON lsb.scoring = wp.scoring
),
with_team AS (
    SELECT base.*, identity.team
    FROM base
    LEFT JOIN identity
        ON identity.league_key = base.league_key AND identity.player_id = base.player_id
),
with_game AS (
    SELECT
        wt.*,
        ge.opponent AS game_opponent,
        ge.kickoff AS game_kickoff,
        ge.implied_team_total AS game_implied_team_total,
        ge.gamescript_lean AS game_gamescript_lean,
        ge.roof AS game_roof,
        ge.temp AS game_temp,
        ge.wind AS game_wind
    FROM with_team wt
    LEFT JOIN game_environment ge
        ON ge.season = wt.season AND ge.week = wt.week AND ge.team = wt.team
),
with_dvp AS (
    SELECT
        wg.*,
        dvp.points_allowed_per_game_season_to_date AS dvp_points_allowed_per_game_season_to_date,
        dvp.points_allowed_per_game_last3 AS dvp_points_allowed_per_game_last3,
        dvp.points_allowed_per_game_last5 AS dvp_points_allowed_per_game_last5,
        dvp.games_observed AS dvp_games_observed,
        dvp.league_avg_points_allowed AS dvp_league_avg_points_allowed,
        dvp.vs_league_avg_ratio AS dvp_vs_league_avg_ratio,
        dvp.vs_league_avg_zscore AS dvp_vs_league_avg_zscore,
        dvp.rank AS dvp_rank
    FROM with_game wg
    LEFT JOIN defense_vs_position dvp
        ON dvp.league_key = wg.league_key AND dvp.season = wg.season AND dvp.week = wg.week
        AND dvp.defense_team = wg.game_opponent AND dvp.position = wg.position
),
with_role AS (
    SELECT
        wd.*,
        rt.games_observed AS role_games_observed,
        rt.snap_share AS role_snap_share,
        rt.snap_share_delta AS role_snap_share_delta,
        rt.target_share AS role_target_share,
        rt.target_share_delta AS role_target_share_delta,
        rt.air_yards_share AS role_air_yards_share,
        rt.air_yards_share_delta AS role_air_yards_share_delta,
        rt.wopr AS role_wopr,
        rt.wopr_delta AS role_wopr_delta,
        rt.carries_share AS role_carries_share,
        rt.carries_share_delta AS role_carries_share_delta,
        rt.depth_rank AS role_depth_rank,
        rt.depth_rank_delta AS role_depth_rank_delta,
        rt.is_starter AS role_is_starter,
        rt.is_starter_delta AS role_is_starter_delta
    FROM with_dvp wd
    LEFT JOIN player_role_trend rt
        ON rt.player_id = wd.player_id AND rt.season = wd.season AND rt.week = wd.week
),
with_outcome AS (
    SELECT
        wr.*,
        o.games_observed AS outcome_games_observed,
        o.median AS outcome_median,
        o.floor AS outcome_floor,
        o.ceiling AS outcome_ceiling,
        o.ceiling_rate AS outcome_ceiling_rate,
        o.floor_rate AS outcome_floor_rate,
        o.position_ceiling_threshold AS outcome_position_ceiling_threshold,
        o.position_floor_threshold AS outcome_position_floor_threshold
    FROM with_role wr
    LEFT JOIN weekly_outcome_rates o
        ON o.league_key = wr.league_key AND o.player_id = wr.player_id
        AND o.season = wr.season AND o.week = wr.week
),
ranked AS (
    SELECT
        *,
        CASE WHEN sleeper_points IS NOT NULL THEN
            RANK() OVER (
                PARTITION BY league_key, season, week, position
                ORDER BY sleeper_points DESC NULLS LAST
            )
        END AS weekly_position_rank
    FROM with_outcome
)
SELECT
    ranked.league_key, ranked.season, ranked.week, ranked.player_id, ranked.player_name,
    ranked.position, ranked.team, ranked.scoring,
    ranked.sleeper_points, ranked.espn_points, ranked.fantasypros_rank_ecr,
    ranked.fantasypros_pos_rank, ranked.num_sources, ranked.points_gap, ranked.points_gap_pct,
    ranked.game_opponent, ranked.game_kickoff, ranked.game_implied_team_total,
    ranked.game_gamescript_lean, ranked.game_roof, ranked.game_temp, ranked.game_wind,
    ranked.dvp_points_allowed_per_game_season_to_date, ranked.dvp_points_allowed_per_game_last3,
    ranked.dvp_points_allowed_per_game_last5, ranked.dvp_games_observed,
    ranked.dvp_league_avg_points_allowed, ranked.dvp_vs_league_avg_ratio,
    ranked.dvp_vs_league_avg_zscore, ranked.dvp_rank,
    ranked.role_games_observed,
    ranked.role_snap_share, ranked.role_snap_share_delta,
    ranked.role_target_share, ranked.role_target_share_delta,
    ranked.role_air_yards_share, ranked.role_air_yards_share_delta,
    ranked.role_wopr, ranked.role_wopr_delta,
    ranked.role_carries_share, ranked.role_carries_share_delta,
    ranked.role_depth_rank, ranked.role_depth_rank_delta,
    ranked.role_is_starter, ranked.role_is_starter_delta,
    ranked.outcome_games_observed, ranked.outcome_median, ranked.outcome_floor,
    ranked.outcome_ceiling, ranked.outcome_ceiling_rate, ranked.outcome_floor_rate,
    ranked.outcome_position_ceiling_threshold, ranked.outcome_position_floor_threshold,
    ranked.weekly_position_rank,
    CASE WHEN ranked.weekly_position_rank IS NOT NULL THEN
        CAST(CEIL(ranked.weekly_position_rank * 1.0 / ls.team_count) AS BIGINT)
    END AS weekly_position_tier
FROM ranked
JOIN league_settings ls ON ls.league_key = ranked.league_key
"""


def _build_sql() -> str:
    return _BUILD_SQL


def build_weekly_player_context() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_build_sql())

    (count,) = con.execute("SELECT COUNT(*) FROM weekly_player_context").fetchone()
    (base_count,) = con.execute("SELECT COUNT(*) FROM weekly_projections").fetchone()
    if count != base_count:
        console.note(
            f"weekly_player_context: {count:,} rows against weekly_projections' {base_count:,} — "
            "a join above is fanning out or dropping rows instead of matching one-to-one"
        )

    con.close()
    console.table("weekly_player_context", count)


if __name__ == "__main__":
    build_weekly_player_context()
