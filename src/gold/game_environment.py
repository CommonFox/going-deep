"""Describe the game each team is playing: implied scoring environment, gamescript lean, and
kickoff conditions — the weekly context layer (#108) wants this sitting next to a projection.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built entirely from
`schedules` (loaded by nfl_data.py), which already carries every betting-line and weather column
needed; this table adds no new source, only reshapes and derives.

One row per (season, week, team) rather than per game, so a player row joins onto it directly
without the consumer working out which side of `schedules` he is on.

## `spread_line`'s sign convention, checked rather than assumed

nflverse's docs say `spread_line` is home-relative but not which sign favours the home team, and
getting it backwards produces numbers that look entirely reasonable and are exactly wrong. Checked
against `home_moneyline`, an independent number from the same books (2020-2025 regular season,
`corr(spread_line, home_moneyline) = -0.95`): a deeply negative moneyline (a big home favourite)
pairs with a large *positive* `spread_line`. So **positive `spread_line` means the home team is
favoured** by that many points; negative means the home team is the underdog.

Hand-verified against three 2024 games, two with the away team favoured:

    game                       spread_line   actual score
    BAL (home) vs CLE (away)    +19.5        BAL 35 - CLE 10   (home favourite, blowout home win)
    NYG (home) vs BAL (away)    -16.5        NYG 14 - BAL 35   (away favourite, blowout away win)
    LV  (home) vs KC  (away)     -9.0        LV  20 - KC  27   (away favourite, away win by 7)

`spread_line` itself is left on the table exactly as `schedules` carries it — home-relative — so it
stays traceable back to the source. `implied_margin` below is the derived, team-perspective number
(the home row gets `spread_line` as-is; the away row gets its negation).

## Coverage: lines arrive as the week approaches, not at schedule release

`spread_line`/`total_line` are null for a season/week that betting markets haven't priced yet
(checked live against 2026: week 3 partially populated, week 4 onward entirely null), while
`gameday`/`gametime` are populated the moment the schedule is released. Every column derived from
the line (`implied_team_total`, `implied_margin`, `is_favorite`, `gamescript_lean`) stays null
rather than being coerced to zero for those rows — a team with no line yet is not the same as a
team priced as a pick'em, and collapsing the two would read as "even game" instead of "no signal
yet". `temp`/`wind` are null on the same principle: a dome has no wind reading, which is a fact
about the stadium, not a missing measurement — `punt_environment.py` documents the identical case.

Every game type in `schedules` is included, not just `REG` — the coverage this ticket was written
against (285 games in 2024 and 2025) is `REG` plus three rounds of playoffs, and there's no reason
a playoff-eligible fantasy week should have no game_environment row.

## `gamescript_lean` is a named bucket of the spread, not a model

"A team expected to trail throws more" is the intuition; this only names where `implied_margin`
sits, the way a broadcast graphic would. Whether the bucket actually predicts anything week to week
is #114's job, not this table's — this stays a documented transformation of the spread.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# implied_margin thresholds, in points, naming where a team's expected outcome sits. Symmetric and
# round rather than fit to anything.
_BIG_LEAD_POINTS = 10
_LEAD_POINTS = 3

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE game_environment AS
WITH team_games AS (
    SELECT
        game_id, season, week, gameday, gametime,
        home_team AS team, away_team AS opponent, TRUE AS is_home,
        spread_line, total_line, spread_line AS implied_margin,
        roof, temp, wind, div_game, home_rest AS rest_days
    FROM schedules
    UNION ALL
    SELECT
        game_id, season, week, gameday, gametime,
        away_team AS team, home_team AS opponent, FALSE AS is_home,
        spread_line, total_line, -spread_line AS implied_margin,
        roof, temp, wind, div_game, away_rest AS rest_days
    FROM schedules
)
SELECT
    season,
    week,
    team,
    opponent,
    game_id,
    CAST(gameday || ' ' || gametime AS TIMESTAMP) AS kickoff,
    is_home,
    total_line,
    spread_line,
    implied_margin,
    implied_margin > 0 AS is_favorite,
    total_line / 2 + implied_margin / 2 AS implied_team_total,
    CASE
        WHEN implied_margin IS NULL THEN NULL
        WHEN implied_margin >= {_BIG_LEAD_POINTS} THEN 'big_favorite'
        WHEN implied_margin >= {_LEAD_POINTS} THEN 'favorite'
        WHEN implied_margin > -{_LEAD_POINTS} THEN 'pick_em'
        WHEN implied_margin > -{_BIG_LEAD_POINTS} THEN 'underdog'
        ELSE 'big_underdog'
    END AS gamescript_lean,
    roof,
    temp,
    wind,
    rest_days,
    CAST(div_game AS BOOLEAN) AS div_game
FROM team_games
ORDER BY season, week, team
"""


def _build_sql() -> str:
    return _BUILD_SQL


def build_game_environment() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_build_sql())
    (count,) = con.execute("SELECT COUNT(*) FROM game_environment").fetchone()
    console.table("game_environment", count)
    con.close()


if __name__ == "__main__":
    build_game_environment()
