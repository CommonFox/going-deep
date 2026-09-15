"""Build `waiver_rankings`: one row per (league, week, available player), so "who helps me this
week" and "who helps me the rest of the season" are both answerable from the free-agent pool —
and stay visibly distinct, never collapsed into one blended waiver score.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `free_agents` (the
available-player pool per league), `weekly_projections` (this week's points), `ros_points` (rest-
of-season points) and `draft_board` (identity crosswalk and each position's replacement level),
all already loaded.

Scoped to skill positions (QB/RB/WR/TE), matching `ros_points.py` and `points_over_replacement.py`:
`ros_points` itself carries no K/DST rows (`weekly_stats` has none), so including them here would
show a free-agent kicker or defense as permanently missing his rest-of-season number — indistin-
guishable from the "no projection at all" case this table is built to call out explicitly.

## Identity: through `draft_board`'s crosswalk, not a second name-matching pass

`free_agents` carries each platform's own ID (Sleeper's `player_id`, ESPN's numeric athlete id) —
neither is the warehouse's canonical `player_id` (gsis_id) that `weekly_projections`/`ros_points`
key on. `draft_board` already resolved every board row to both `sleeper_id` and `espn_id` (see
`sleeper_ids.py`/`espn_ids.py`), so that crosswalk is reused here rather than re-derived: a free
agent's platform ID is matched against whichever of `draft_board.sleeper_id`/`espn_id` corresponds
to his league's platform, giving `player_id`.

A free agent who was never priced by `consensus_projections` at draft time has no row in
`draft_board` at all, so no crosswalk entry and no `player_id` here either — the same gap
`sleeper_ids.py`/`espn_ids.py` already report on the draft board. Rather than dropping the row (the
one thing this table's acceptance criteria rule out), the free agent's own name and position from
`free_agents` carry the row, with `player_id`, `weekly_points`, `fantasypros_rank_ecr`/
`fantasypros_pos_rank` and `ros_points` all null — an explicit "no projection", never a zero and
never a silently omitted row. This is the same "rookie nobody has projected yet" case `ros_points`
already documents for a player who has no `weekly_stats` row; here it can additionally mean no
`consensus_projections` row at all.

## `weekly_points`, not `sleeper_points`

`weekly_projections.sleeper_points` is renamed to `weekly_points` here to match this table's own
two-column contract (`weekly_points` vs `ros_points`); `fantasypros_rank_ecr`/`fantasypros_pos_rank`
ride along unrenamed as the corroborating signal `weekly_projections` already keeps separate from
its points number, for the same reason: a rank and a points figure are not the same unit and were
never meant to be blended into one.

Filtered to the current week and each league's own scoring basis (`sleeper_nfl_state.week`,
`draft_board.scoring` for that `league_key`) — the same week `weekly_projections` carries its
FantasyPros columns for, and the pairing `draft_board`/`weekly_projections` already use so this
table can't drift onto a different scoring basis than either of them.

## Replacement level: a (league, position) constant, not a per-player lookup

`draft_board.replacement_level_points`/`starters_at_position` are league-and-position constants —
every RB in a league's board carries the same value — so they are joined on `(league_key,
position)` rather than `player_id`. That means a free agent whose `player_id` never resolved still
gets his position's replacement level: "worth a roster spot at all" doesn't depend on having
already identified which specific player this is.

This is deliberately the existing season-total figure from `draft_board`, not a rest-of-season-
scaled recomputation — `ros_points` and `replacement_level_points` are therefore not on quite the
same time window (partial season remaining vs. full season), matching the ticket's ask for "the
position's replacement level" from `draft_board`, not a new replacement-level model. Comparable
*across positions* is what this buys; comparable-in-magnitude to `ros_points` down to the point is
a job for whoever reads this table next, not this build.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

_OUTPUT_COLUMNS = [
    "league_key", "season", "week", "player_id", "player_name", "position",
    "weekly_points", "fantasypros_rank_ecr", "fantasypros_pos_rank",
    "ros_points", "replacement_level_points", "starters_at_position",
]

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE waiver_rankings AS
WITH current_week AS (
    SELECT CAST(season AS BIGINT) AS season, week FROM sleeper_nfl_state
),
league_scoring_basis AS (
    SELECT DISTINCT league_key, scoring FROM draft_board
),
sleeper_identity AS (
    SELECT sleeper_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE sleeper_id IS NOT NULL GROUP BY sleeper_id
),
espn_identity AS (
    SELECT espn_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE espn_id IS NOT NULL GROUP BY espn_id
),
available AS (
    SELECT
        fa.league_key, fa.position,
        fa.player_name AS free_agent_name,
        COALESCE(si.player_id, ei.player_id) AS player_id
    FROM free_agents fa
    LEFT JOIN sleeper_identity si
        ON fa.league_key = 'sleeper' AND fa.platform_player_id = si.sleeper_id
    LEFT JOIN espn_identity ei
        ON fa.league_key = 'espn' AND fa.platform_player_id = ei.espn_id
    WHERE fa.position IN {_SKILL_POSITIONS}
),
replacement_levels AS (
    SELECT DISTINCT league_key, position, replacement_level_points, starters_at_position
    FROM draft_board
    WHERE position IN {_SKILL_POSITIONS}
)
SELECT
    available.league_key,
    current_week.season,
    current_week.week,
    available.player_id,
    available.free_agent_name AS player_name,
    available.position,
    wp.sleeper_points AS weekly_points,
    wp.fantasypros_rank_ecr,
    wp.fantasypros_pos_rank,
    ros.ros_points,
    rl.replacement_level_points,
    rl.starters_at_position
FROM available
CROSS JOIN current_week
JOIN league_scoring_basis ON league_scoring_basis.league_key = available.league_key
LEFT JOIN weekly_projections wp
    ON wp.player_id = available.player_id
    AND wp.season = current_week.season
    AND wp.week = current_week.week
    AND wp.scoring = league_scoring_basis.scoring
LEFT JOIN ros_points ros
    ON ros.league_key = available.league_key AND ros.player_id = available.player_id
LEFT JOIN replacement_levels rl
    ON rl.league_key = available.league_key AND rl.position = available.position
"""


def build_waiver_rankings() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)

    (no_projection,) = con.execute(
        "SELECT COUNT(*) FROM waiver_rankings WHERE player_id IS NULL"
    ).fetchone()
    if no_projection:
        console.note(f"waiver_rankings: {no_projection} free agents have no projection at all")

    (count,) = con.execute("SELECT COUNT(*) FROM waiver_rankings").fetchone()
    con.close()

    console.table("waiver_rankings", count)


if __name__ == "__main__":
    build_waiver_rankings()
