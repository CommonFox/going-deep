"""Build `viewing_guide` and `viewing_guide_starters`: one row per (league_key, season, week,
game_id) for every game with at least one of my `optimal_lineup` starters in it, plus the
per-starter detail behind it — issue #168, under the viewing-guide epic (#112).

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `optimal_lineup` (#89),
`weekly_player_context` (#124, read here only for a starter's current-week `team`) and
`game_environment` (#118, for `game_id`/`kickoff`/`opponent`/`is_home`) — no new data source. See
#168's own issue body for the case against parsing the actual-submitted lineup instead of
`optimal_lineup`.

## Starters, not slots

`optimal_lineup` carries one row per lineup *slot*, filled or not — `backfill_unprojected` can leave
a dedicated slot with no candidate at all. Only a slot with a real `player_id` is a starter here; an
empty slot names no one to watch for.

## Two tables, built in sequence rather than off a shared CTE

`viewing_guide_starters` is built first, straight off the three-way join described above.
`viewing_guide` is then built *from* `viewing_guide_starters` rather than repeating that join — it
only needs the game-level shape (`starter_count`, `total_projected_points`) plus
`home_team`/`away_team`/`kickoff`, looked up once per `game_id` directly off `game_environment`
(both of a game's two rows, home and away, picked apart by `is_home`) rather than off any single
starter's own row. That distinction matters because a game can have my starters on *both* sides —
exactly why this table is keyed by `game_id` and not by `team`.

## A starter that doesn't resolve is left out, never zeroed

`weekly_player_context` has no row for a player-week `weekly_projections` never priced (a bye, or an
identity gap `optimal_lineup.py` already documents); `game_environment` has no row for a team not
playing that week. Either way the starter is left out of both tables here — never shipped with a
guessed team or game — and reported by name (`_missing_sql`), the same "missing is reported, never
silently dropped" convention `optimal_lineup.py` and `weekly_player_context.py` both already follow.

A starter can still resolve with a *null* `projected_points` — a backfilled slot seated with no
projection to compare (see `optimal_lineup.py`). That's a different, legitimate case:
`weekly_player_context` still knows his team, so he ships in `viewing_guide_starters` with
`projected_points` null, and `total_projected_points` (a plain SQL `SUM`, which already skips nulls)
counts him toward `starter_count` without reading his absence as a real zero.

## `broadcast_window`

Named for the five standard NFL windows: Thursday, Sunday early, Sunday late, Sunday night, Monday.
`weekday`/`gametime` are derived from `kickoff` (`strftime('%A'`/`'%H:%M'`) rather than a second join
back to `schedules`, which `game_environment` already flattened into `kickoff` once. Sunday's
early/late boundary is 16:00; late/night is 18:00 — the same threshold
`src/gameday/storylines.py`'s `NIGHT_GAMETIME_FLOOR` uses for "tonight's game", restated here as a
local constant rather than imported, since a live/gameday module has no business being a dependency
of a warehouse build. A game that isn't Thursday/Sunday/Monday at all (a Saturday playoff slate, a
Friday international game) reports its own raw weekday rather than being forced into a bucket it
doesn't belong in.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Sunday's own early/late boundary. Late/night below matches src/gameday/storylines.py's
# NIGHT_GAMETIME_FLOOR ("18:00") — see the module docstring for why that's restated, not imported.
_SUNDAY_EARLY_LATE_BOUNDARY = "16:00"
_SUNDAY_LATE_NIGHT_BOUNDARY = "18:00"

_STARTERS_SQL = """
CREATE OR REPLACE TABLE viewing_guide_starters AS
WITH starters AS (
    SELECT league_key, season, week, slot, player_id, player_name, projected_points
    FROM optimal_lineup
    WHERE player_id IS NOT NULL
),
with_team AS (
    SELECT starters.*, wpc.team
    FROM starters
    JOIN weekly_player_context wpc
        ON wpc.league_key = starters.league_key AND wpc.player_id = starters.player_id
        AND wpc.season = starters.season AND wpc.week = starters.week
)
SELECT
    with_team.league_key, with_team.season, with_team.week,
    ge.game_id,
    with_team.slot, with_team.player_id, with_team.player_name, with_team.team,
    with_team.projected_points
FROM with_team
JOIN game_environment ge
    ON ge.season = with_team.season AND ge.week = with_team.week AND ge.team = with_team.team
"""

_GUIDE_SQL = f"""
CREATE OR REPLACE TABLE viewing_guide AS
WITH relevant_games AS (
    SELECT DISTINCT league_key, season, week, game_id FROM viewing_guide_starters
),
game_info AS (
    SELECT
        rg.league_key, rg.season, rg.week, rg.game_id,
        MAX(CASE WHEN ge.is_home THEN ge.team END) AS home_team,
        MAX(CASE WHEN NOT ge.is_home THEN ge.team END) AS away_team,
        MAX(ge.kickoff) AS kickoff
    FROM relevant_games rg
    JOIN game_environment ge
        ON ge.season = rg.season AND ge.week = rg.week AND ge.game_id = rg.game_id
    GROUP BY rg.league_key, rg.season, rg.week, rg.game_id
),
with_weekday AS (
    SELECT *, strftime(kickoff, '%A') AS weekday, strftime(kickoff, '%H:%M') AS gametime
    FROM game_info
),
aggregated AS (
    SELECT
        league_key, season, week, game_id,
        COUNT(*) AS starter_count,
        SUM(projected_points) AS total_projected_points
    FROM viewing_guide_starters
    GROUP BY league_key, season, week, game_id
)
SELECT
    ww.league_key, ww.season, ww.week, ww.game_id,
    ww.home_team, ww.away_team,
    ww.kickoff, ww.weekday, ww.gametime,
    CASE
        WHEN ww.weekday = 'Thursday' THEN 'thursday'
        WHEN ww.weekday = 'Monday' THEN 'monday'
        WHEN ww.weekday = 'Sunday' AND ww.gametime < '{_SUNDAY_EARLY_LATE_BOUNDARY}'
            THEN 'sunday_early'
        WHEN ww.weekday = 'Sunday' AND ww.gametime < '{_SUNDAY_LATE_NIGHT_BOUNDARY}'
            THEN 'sunday_late'
        WHEN ww.weekday = 'Sunday' THEN 'sunday_night'
        ELSE ww.weekday
    END AS broadcast_window,
    a.starter_count, a.total_projected_points
FROM with_weekday ww
JOIN aggregated a
    ON a.league_key = ww.league_key AND a.season = ww.season AND a.week = ww.week
    AND a.game_id = ww.game_id
"""

_MISSING_SQL = """
SELECT ol.league_key, ol.season, ol.week, ol.player_id, ol.player_name
FROM optimal_lineup ol
LEFT JOIN viewing_guide_starters vgs
    ON vgs.league_key = ol.league_key AND vgs.player_id = ol.player_id
    AND vgs.season = ol.season AND vgs.week = ol.week
WHERE ol.player_id IS NOT NULL AND vgs.player_id IS NULL
"""


def _starters_sql() -> str:
    return _STARTERS_SQL


def _guide_sql() -> str:
    return _GUIDE_SQL


def _missing_sql() -> str:
    return _MISSING_SQL


def build_viewing_guide() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_starters_sql())
    con.execute(_guide_sql())

    missing = con.execute(_missing_sql()).df()
    for row in missing.itertuples():
        console.note(
            f"{row.league_key} week {row.week}: no weekly_player_context/game_environment row for "
            f"{row.player_name} — left out of the viewing guide"
        )

    (guide_count,) = con.execute("SELECT COUNT(*) FROM viewing_guide").fetchone()
    (starters_count,) = con.execute("SELECT COUNT(*) FROM viewing_guide_starters").fetchone()
    con.close()

    console.table("viewing_guide", guide_count)
    console.table("viewing_guide_starters", starters_count)


if __name__ == "__main__":
    build_viewing_guide()
