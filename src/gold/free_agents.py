"""Every player rostered by no team, per league — the actual waiver pool.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from `sleeper_players` and
`sleeper_rosters` (Sleeper) and `espn_player_ownership` (ESPN), all already loaded.

Availability is genuinely per-league: a player owned in the Sleeper league can still be a free
agent in the ESPN league and vice versa, so this is never a single global list, and the two arms
below use unrelated identity spaces on purpose rather than being crosswalked to one shared ID —
that crosswalk, if a downstream consumer needs it, is its job, not this table's.

## Sleeper: set difference, not a status flag

Sleeper's player dictionary carries no ownership flag of its own, so a free agent is whoever's ID
appears in `sleeper_players` but in no roster's `players` array, unioned across every roster in the
league (not just one team's). Team defenses carry no `full_name` in `sleeper_players` — only a
`player_id`/`team` equal to the team's own abbreviation (e.g. `"HOU"`) — so the name falls back to
that abbreviation rather than going out null.

## ESPN: a direct flag, with both of its "not on a team" values kept and told apart

`espn_player_ownership.status` is `FREEAGENT` for an outright free agent, `ONTEAM` for a rostered
player, and `WAIVERS` for a player who was just dropped and is sitting in that league's waiver
claim period — not on a roster, but not addable outright either; claiming him spends waiver
priority rather than a plain pickup. `onTeamId` is `0` for both `FREEAGENT` and `WAIVERS` rows, so
`status` (not `onTeamId`) is the column that actually distinguishes them.

This table originally dropped `WAIVERS` rows entirely, on the reasoning that they weren't
addable outright. #104 found the real cost of that: a player dropped right after a big week — the
single most likely reason anyone checks the waiver board on a given day — sits in exactly that
state, and was invisible here until his league's waiver period cleared, often a day or more later
than the tool would have been useful. Both statuses are kept now, with `availability` saying which
is which, so a hot `WAIVERS` name is visible today even though claiming him still costs priority
rather than being a same-day add.

## `availability`: ESPN reads it, Sleeper's is derived

`FREEAGENT` maps to `'free_agent'`, `WAIVERS` to `'on_waivers'` — ESPN's own system already tracks
this and hands it back as a flag, no derivation needed.

Sleeper runs the same real mechanic (any player, rostered or not, is locked to whoever holds him
once his game starts, and free agents specifically stay locked out of same-day pickup until the
league's Wednesday waiver run) but exposes no flag for it anywhere this warehouse reads — a Sleeper
free agent is a set difference (see above), not a status field. So it's derived here instead: a
Sleeper free agent is `'on_waivers'` if his team's game for the *current* week (`sleeper_nfl_state`)
already has a final score in `schedules`, `'free_agent'` otherwise. `schedules` disagrees with
Sleeper on how to spell some teams (`"LA"` vs. Sleeper's `"LAR"`, the same mismatch `draft_board.py`
already normalizes for), so both sides go through `normalize_team` before the two are compared. A
free agent with no current team (`sleeper_players.team` null) can't be matched to a game either way
and stays `'free_agent'` — there's no schedule row to say otherwise.

`player.defaultPositionId` is resolved through `espn.POSITION_IDS`, the same numeric map
`espn.py`'s own `load_projections` already uses, rather than re-deriving it here.
"""

from pathlib import Path

import duckdb

from src import console
from src.silver.espn import POSITION_IDS
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_BUILD_SQL = """
CREATE OR REPLACE TABLE free_agents AS
WITH current_week AS (
    SELECT CAST(season AS BIGINT) AS season, week FROM sleeper_nfl_state
),
played_teams AS (
    SELECT normalize_team(s.home_team) AS team
    FROM schedules s, current_week
    WHERE s.season = current_week.season AND s.week = current_week.week
        AND s.game_type = 'REG' AND s.home_score IS NOT NULL

    UNION

    SELECT normalize_team(s.away_team) AS team
    FROM schedules s, current_week
    WHERE s.season = current_week.season AND s.week = current_week.week
        AND s.game_type = 'REG' AND s.away_score IS NOT NULL
),
sleeper_rostered AS (
    SELECT DISTINCT UNNEST(players) AS player_id FROM sleeper_rosters
),
sleeper_free_agents AS (
    SELECT
        'sleeper' AS league_key,
        sp.player_id AS platform_player_id,
        COALESCE(sp.full_name, sp.player_id) AS player_name,
        sp.position,
        CASE WHEN pt.team IS NOT NULL THEN 'on_waivers' ELSE 'free_agent' END AS availability
    FROM sleeper_players sp
    LEFT JOIN sleeper_rostered sr ON sr.player_id = sp.player_id
    LEFT JOIN played_teams pt ON pt.team = normalize_team(sp.team)
    WHERE sr.player_id IS NULL
),
espn_free_agents AS (
    SELECT
        'espn' AS league_key,
        CAST(o.id AS VARCHAR) AS platform_player_id,
        o."player.fullName" AS player_name,
        espn_position(o."player.defaultPositionId") AS position,
        CASE WHEN o.status = 'WAIVERS' THEN 'on_waivers' ELSE 'free_agent' END AS availability
    FROM espn_player_ownership o
    WHERE o.status IN ('FREEAGENT', 'WAIVERS')
)
SELECT * FROM sleeper_free_agents
UNION ALL
SELECT * FROM espn_free_agents
"""


def _espn_position(position_id: int | None) -> str | None:
    return POSITION_IDS.get(position_id)


def build_free_agents() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("espn_position", _espn_position, ["BIGINT"], "VARCHAR")
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM free_agents").fetchone()
    con.close()

    console.table("free_agents", count)


if __name__ == "__main__":
    build_free_agents()
