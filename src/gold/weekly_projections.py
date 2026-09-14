"""Build `weekly_projections`: one row per (player, season, week) with a single week's expected
points, as distinct from every other projection table in this warehouse, which is season-total.
This is the number both the waiver board (#83) and the lineup optimizer (#89, separate epic) rank
on.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `sleeper_projections`,
`fantasypros_weekly_rankings_<qb|rb|wr|te|k|dst>` and `draft_board`, all already loaded.

## Two signals, kept apart, because they are not the same unit

Only two real weekly signals exist in the warehouse, and one is points while the other is a rank —
this is **not** a blended composite the way `consensus.py` averages five point-projections into
one number:

- **`sleeper_points`** — Sleeper's own weekly projection, the primary points number. Sleeper hands
  back std/half-PPR/PPR side by side (see `sleeper.load_projections`), not one canonical number, so
  this is Sleeper's *canned* bucket closest to a league's scoring rather than any given league's
  exact per-yard/per-TD coefficients — an approximation, since Sleeper exposes no raw weekly
  counting stats to recompute a true per-league number the way `points_over_replacement.py` does
  for actuals.
- **`fantasypros_rank_ecr`** / **`fantasypros_pos_rank`** — FantasyPros' weekly consensus expert
  rank, a corroborating signal kept in its own columns and never averaged into `sleeper_points`.

## `scoring`, for the same reason `consensus_projections` carries it

The two leagues in this warehouse score receptions differently (`league_settings.rec_pts`: 0.5 for
the Sleeper league, 1.0 for ESPN's), and Sleeper's weekly projection is not one number to convert
between the two — it is three numbers already, published side by side. So this table carries a
`scoring` column (`'ppr'` / `'half_ppr'`), one row per (player, season, week) *per scoring basis*,
exactly mirroring `consensus_projections`' own axis for the identical reason. `waiver_rankings`
(#83) picks a row's basis the way `draft_board._scoring_basis` already does for the season-total
board — this table doesn't resolve "per league" itself, so it stays one shared table rather than
`draft_board`'s per-`league_key` shape.

## Identity: through `draft_board.sleeper_id`, not a second name-matching pass

`sleeper_projections.sleeper_id` is joined onto `draft_board.sleeper_id` (already resolved by the
draft-assistant epic's crosswalk work — see `sleeper_ids.py`) to reach `player_id`/`player_name`/
`position`, rather than building a second identity map for the Sleeper side. A player Sleeper
projects but that no season-total source in `draft_board` covers (so `sleeper_id` never got
resolved there) has no home here — the same tradeoff `draft_board` itself makes, not a new one.

FantasyPros carries no platform ID here (its `player_id` is FantasyPros' own), so it is matched by
name through `players.merge_name`, exactly the approach `adp_consensus.py` uses for
`fantasypros_adp` — except for defenses, which FantasyPros publishes on a team abbreviation
(`player_team_id`, e.g. `"JAC"`) that already matches `draft_board`'s own team-keyed identity for
DST once normalized, so no name matching happens for that position at all (its `player_name` is a
full team name like "Jacksonville Jaguars", not the abbreviation identity is keyed on).

The `flex` table (`fantasypros_weekly_rankings_flex`) is deliberately unused: it is RB/WR/TE
unioned again under one cross-position rank, so every player in it already has a row in his own
position's table, and folding it in would either duplicate him or overwrite his position-scoped
`fantasypros_pos_rank` (e.g. `"RB1"`) with a flex-wide one.

## Why a player can be missing his FantasyPros columns

`fantasypros_weekly_rankings_*` holds only the current NFL week at any moment — `fantasypros.py`
overwrites each position's table on every build (`CREATE OR REPLACE`), it is never a weekly
archive. So `fantasypros_rank_ecr`/`fantasypros_pos_rank` are only ever populated for the week
`sleeper_nfl_state` currently names; every other week's rows carry Sleeper's points with those two
columns null, same as a deep bench player FantasyPros doesn't rank at all this week — both are the
same "no signal for this row" case, not a bug.
"""

from pathlib import Path

import duckdb

from src import console
from src.silver.players import merge_name
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# gold's `scoring` <- which of Sleeper's own buckets becomes `sleeper_points`. Mirrors
# consensus_projections'/draft_board's `_SCORINGS`/`_SCORING_BASES` axis for the same reason.
_SCORINGS = {"ppr": "pts_ppr", "half_ppr": "pts_half_ppr"}

# The position-scoped FantasyPros weekly tables, excluding `flex` (see module docstring).
_FANTASYPROS_POSITIONS = {"qb": "QB", "rb": "RB", "wr": "WR", "te": "TE", "k": "K", "dst": "DST"}


def _fantasypros_union() -> str:
    """Every position-scoped FantasyPros weekly table, tagged with the position it covers."""
    arms = [
        f"""
        SELECT player_name, player_team_id, '{position}' AS position,
               rank_ecr AS fantasypros_rank_ecr, pos_rank AS fantasypros_pos_rank
        FROM fantasypros_weekly_rankings_{table}
        """
        for table, position in _FANTASYPROS_POSITIONS.items()
    ]
    return "\n    UNION ALL\n".join(arms)


def _scoring_arm(scoring: str, points_column: str) -> str:
    """One scoring basis' rows: Sleeper's matching points bucket, joined to this week's FantasyPros
    rank if the row's week is the one FantasyPros' snapshot currently represents."""
    return f"""
    SELECT
        sleeper.player_id, sleeper.player_name, sleeper.position, sleeper.season, sleeper.week,
        '{scoring}' AS scoring, sleeper.{points_column} AS sleeper_points,
        fp.fantasypros_rank_ecr, fp.fantasypros_pos_rank
    FROM sleeper
    LEFT JOIN fantasypros_resolved fp
        ON fp.player_id = sleeper.player_id
        AND sleeper.week = sleeper.current_week
    """


_BUILD_SQL = f"""
CREATE OR REPLACE TABLE weekly_projections AS
WITH identity AS (
    -- One row per Sleeper ID, even though draft_board holds one per (league_key x player): every
    -- league resolves the same player to the same sleeper_id (see sleeper_ids.py), so this is a
    -- dedup, not a choice between disagreeing sources.
    SELECT sleeper_id, ANY_VALUE(player_id) AS player_id, ANY_VALUE(player_name) AS player_name,
           ANY_VALUE(position) AS position
    FROM draft_board
    WHERE sleeper_id IS NOT NULL
    GROUP BY sleeper_id
),
current_week AS (
    SELECT week FROM sleeper_nfl_state
),
ids_normalized AS (
    SELECT gsis_id, merge_name, CASE WHEN position = 'PK' THEN 'K' ELSE position END AS position
    FROM ids
),
fantasypros_players AS (
{_fantasypros_union()}
),
fantasypros_resolved AS (
    SELECT ids.gsis_id AS player_id, fp.fantasypros_rank_ecr, fp.fantasypros_pos_rank
    FROM fantasypros_players fp
    JOIN ids_normalized ids
        ON ids.merge_name = to_merge_name(fp.player_name) AND ids.position = fp.position
    WHERE fp.position != 'DST'

    UNION ALL

    SELECT normalize_team(fp.player_team_id) AS player_id,
           fp.fantasypros_rank_ecr, fp.fantasypros_pos_rank
    FROM fantasypros_players fp
    WHERE fp.position = 'DST'
),
sleeper AS (
    SELECT
        identity.player_id, identity.player_name, identity.position,
        CAST(s.season AS BIGINT) AS season, s.week, s.pts_ppr, s.pts_half_ppr,
        current_week.week AS current_week
    FROM sleeper_projections s
    JOIN identity ON identity.sleeper_id = s.sleeper_id
    CROSS JOIN current_week
)
SELECT * FROM (
{"    UNION ALL".join(_scoring_arm(scoring, column) for scoring, column in _SCORINGS.items())}
)
"""


def build_weekly_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("to_merge_name", merge_name, ["VARCHAR"], "VARCHAR")
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM weekly_projections").fetchone()
    con.close()

    console.table("weekly_projections", count)


if __name__ == "__main__":
    build_weekly_projections()
