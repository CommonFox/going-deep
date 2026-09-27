"""Build `drop_candidates`: rank my own roster against replacement level, per league — issue #171,
part of the waiver epic (#110).

Pure warehouse-to-warehouse SQL — no fetch step, no network. Same shape as `waiver_rankings.py`,
built off `my_roster`, `ros_points`, `draft_board`, `player_role_trend`, `injuries` and
`sleeper_nfl_state`, all already loaded.

`waiver_rankings` prices every free agent against replacement level; nothing priced the other half
of an add/drop. This table is that other half: my own roster, floored against the identical
replacement-level constant, so a drop decision doesn't run on memory of draft-day expectations.

## Identity: the same crosswalk `waiver_rankings.py` already built off `draft_board`

`my_roster.platform_player_id` is Sleeper's own ID or ESPN's own numeric id, exactly as
`waiver_rankings.py` documents for `free_agents.platform_player_id` — neither is the warehouse's
canonical `player_id` (gsis_id, or a team abbreviation for `DST`). Resolved through the identical
`sleeper_identity`/`espn_identity` crosswalk built off `draft_board`'s `sleeper_id`/`espn_id`
columns, rather than re-derived a third time.

## The grain is `platform_player_id`, not `player_id`

Unlike `waiver_rankings` (where an unresolved free agent still has a name and position from
`free_agents` to show), a roster spot that fails to resolve a `player_id` is still a roster spot: one
row per `(league_key, platform_player_id)` currently on `my_roster` is the acceptance bar, so
`player_id` rides along as a nullable enrichment column rather than the join key the row's existence
depends on. A depth player `draft_board` never priced would otherwise silently vanish from his own
team's drop board — a worse failure than showing him with every value column null.

## Replacement level: `draft_board`'s (league_key, position) constant, normalized for one quirk

`draft_board.replacement_level_points` is joined on `(league_key, position)`, the identical
league-and-position constant `waiver_rankings.py` uses — a rostered player and a free agent are
floored against the same number. One normalization is needed first: `my_roster.position` for a team
defense is Sleeper's own raw label, `'DEF'`, while everywhere else that prices one (`draft_board`,
ESPN's own roster rows, `sleeper_ids.py`/`espn_ids.py`) it's `'DST'`. Joining on the raw label would
silently null out the one column that's actually priced for a Sleeper defense, so the join
normalizes `'DEF'` to `'DST'`; the row's own displayed `position` is left exactly as `my_roster`
reports it.

## The ranking column is `points_over_replacement`, not "surplus"

`ros_points.ros_points` (by `league_key`, `player_id`) minus that replacement-level constant is the
ranking value — ascending, so the top of the table is the most droppable. `draft_board` already
carries a column with this exact name for the analogous season-total quantity
(`projected_points_adjusted - replacement_level_points`); this is the same idea against the
rest-of-season number instead, so it keeps `draft_board`'s name. The ticket that opened this table
called it "surplus," but that word is already spent in this warehouse for a different, draft-price-
relative idea (CONTEXT.md's **Surplus** entry: what a player returned relative to what his draft
price implied) — `points_over_replacement` is CONTEXT.md's own term for a player's points minus his
position's floor, which is what this column actually is.

`ros_points` structurally never carries a K/DST/P row at all (`ros_points.py`: `weekly_stats` has
none), so `points_over_replacement` is null for those positions regardless of whether replacement
level resolved — not a coverage gap, a different question `ros_points` was never asked. Ordered with
nulls last, so an unpriced roster spot doesn't read as the single most droppable player by defaulting
to the top of an ascending sort.

## Role trend and injury status ride along unblended

`player_role_trend`'s seven `_delta` columns, at that player's own most recent (season, week) —
across the whole table, not scoped to the current NFL week, because a role-trend row only exists for
a week a player actually played, and a currently-injured player's last real game is exactly the
context worth keeping. Matches `weekly_player_context`'s rule: level and direction stay their own
columns, never folded into a ranking value. `player_role_trend` is itself scoped to QB/RB/WR/TE, so a
K or DST always carries null deltas here — the same structural gap as `ros_points`, not a coverage
miss.

`injuries.report_status`, for `gsis_id = player_id` in the current season/week (`sleeper_nfl_state`).
More than one report can land for the same player the same week — a Wednesday DNP and a Friday full
participation are two different rows, per `nfl_data.fetch_injuries`'s docstring — so the latest
`date_modified` wins. No report that week means healthy, not missing data: the row still ships, with
`report_status` null exactly as a clean bill of health would look, since a left join can't otherwise
distinguish "checked and fine" from "never checked."
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_OUTPUT_COLUMNS = [
    "league_key", "platform_player_id", "player_id", "player_name", "position",
    "ros_points", "replacement_level_points", "points_over_replacement",
    "snap_share_delta", "target_share_delta", "air_yards_share_delta", "wopr_delta",
    "carries_share_delta", "depth_rank_delta", "is_starter_delta",
    "report_status",
]

_BUILD_SQL = """
CREATE OR REPLACE TABLE drop_candidates AS
WITH current_week AS (
    SELECT CAST(season AS BIGINT) AS season, week FROM sleeper_nfl_state
),
sleeper_identity AS (
    SELECT sleeper_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE sleeper_id IS NOT NULL GROUP BY sleeper_id
),
espn_identity AS (
    SELECT espn_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE espn_id IS NOT NULL GROUP BY espn_id
),
resolved_roster AS (
    SELECT
        mr.league_key,
        mr.platform_player_id,
        mr.player_name,
        mr.position,
        COALESCE(si.player_id, ei.player_id) AS player_id
    FROM my_roster mr
    LEFT JOIN sleeper_identity si
        ON mr.league_key = 'sleeper' AND mr.platform_player_id = si.sleeper_id
    LEFT JOIN espn_identity ei
        ON mr.league_key = 'espn' AND mr.platform_player_id = ei.espn_id
),
replacement_levels AS (
    SELECT DISTINCT league_key, position, replacement_level_points
    FROM draft_board
),
latest_role_trend AS (
    SELECT
        player_id, snap_share_delta, target_share_delta, air_yards_share_delta, wopr_delta,
        carries_share_delta, depth_rank_delta, is_starter_delta
    FROM player_role_trend
    QUALIFY ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY season DESC, week DESC) = 1
),
current_injuries AS (
    SELECT i.gsis_id AS player_id, i.report_status
    FROM injuries i, current_week cw
    WHERE CAST(i.season AS BIGINT) = cw.season AND CAST(i.week AS BIGINT) = cw.week
    QUALIFY ROW_NUMBER() OVER (PARTITION BY i.gsis_id ORDER BY i.date_modified DESC) = 1
)
SELECT
    r.league_key,
    r.platform_player_id,
    r.player_id,
    r.player_name,
    r.position,
    ros.ros_points,
    rl.replacement_level_points,
    ros.ros_points - rl.replacement_level_points AS points_over_replacement,
    role.snap_share_delta,
    role.target_share_delta,
    role.air_yards_share_delta,
    role.wopr_delta,
    role.carries_share_delta,
    role.depth_rank_delta,
    role.is_starter_delta,
    inj.report_status
FROM resolved_roster r
LEFT JOIN ros_points ros
    ON ros.league_key = r.league_key AND ros.player_id = r.player_id
LEFT JOIN replacement_levels rl
    ON rl.league_key = r.league_key
    AND rl.position = CASE WHEN r.position = 'DEF' THEN 'DST' ELSE r.position END
LEFT JOIN latest_role_trend role ON role.player_id = r.player_id
LEFT JOIN current_injuries inj ON inj.player_id = r.player_id
ORDER BY r.league_key, points_over_replacement ASC NULLS LAST
"""


def build_drop_candidates() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_BUILD_SQL)

    (unresolved,) = con.execute(
        "SELECT COUNT(*) FROM drop_candidates WHERE player_id IS NULL"
    ).fetchone()
    if unresolved:
        console.note(f"drop_candidates: {unresolved} rostered players have no player_id crosswalk")

    (count,) = con.execute("SELECT COUNT(*) FROM drop_candidates").fetchone()
    con.close()

    console.table("drop_candidates", count)


if __name__ == "__main__":
    build_drop_candidates()
