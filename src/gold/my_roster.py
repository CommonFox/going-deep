"""My own roster of actual players, per league, resolved from already-loaded warehouse tables.

Issue #88, under the lineup-optimizer epic (#86). `src/draft/seat.py` and `src/draft/espn_seat.py`
resolve the identical "which team is mine" question live, on a tick, because a draft is a moving
target — see `src/draft/live.py`'s docstring. A roster between drafts isn't: nothing on it changes
between one warehouse rebuild and the next, so this reads the same identity a build already has on
disk rather than polling either platform.

## Two platforms, two identity paths, unified by neither

Sleeper's `sleeper_users` has no roster column and `sleeper_rosters` has no username column, so
getting from a typed-in username to a list of players is two matches, not one: `display_name` to
`user_id` on `sleeper_users`, then `user_id` to `owner_id` on `sleeper_rosters` for that row's
`players`. `co_owners` is deliberately not matched against — the configured identity is the
league's `owner_id`, and a co-owner is not the identity this ticket is asked to resolve.

ESPN has no per-user table at all. `espn_teams.owners` carries the SWID(s) that own each team
directly, so the match is one step — SWID to team `id` — but that step is the only place ESPN's
identity model differs from Sleeper's team-per-user assumption: `owners` is a list because ESPN
allows co-owned teams, and it is scanned rather than indexed for the same reason `espn_seat.py`'s
`_my_team_id` does.

Neither arm is crosswalked to a shared player identifier — `platform_player_id` is Sleeper's own
ID or ESPN's own `id`, exactly as `free_agents.py` leaves its own two arms uncombined, for the same
reason: a downstream consumer that wants both leagues on one identity space has to build that
crosswalk for its own purpose, and baking one in here would be guessing at a need this ticket was
never asked to serve.

## Empty is a refusal, not a result

A wrong username, a wrong SWID, and a build taken before either draft has happened all produce the
same shape of trouble: a resolved team with no rostered players. None of those are "no players
yet" — they are every one of them a fact this code got the identity or the timing wrong, and a
roster of zero players downstream would price a lineup that starts nobody without saying why. So
both arms raise the moment the player list comes back empty, rather than handing back an empty
frame that reads exactly like a legitimately roster-less team. This is why `my_roster` was left out
of `scripts/build_warehouse.sh` until this league's ESPN draft had actually happened — before then,
`espn_player_ownership` carried no `ONTEAM` rows at all, and every build would have hit this
refusal.

## Where the two identities configured elsewhere come from

The Sleeper username is `src.draft.live.USERNAME` — the one hand-typed identity in this whole
warehouse, resolved there against the live draft the same way it is resolved here against a
finished roster, so the repo holds one username rather than two that can quietly disagree. The
ESPN SWID is `src.silver.espn.SWID`, already read from `.env` by the fetch step that pulls the
league in the first place.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.draft.live import USERNAME
from src.query import q
from src.silver.espn import SWID, POSITION_IDS

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_ROSTER_COLUMNS = ["league_key", "platform_player_id", "player_name", "position"]


def resolve_sleeper_roster(
    users: pd.DataFrame, rosters: pd.DataFrame, players: pd.DataFrame, username: str
) -> pd.DataFrame:
    """`username`'s own Sleeper roster, enriched with name and position.

    `users` and `rosters` are `sleeper_users`/`sleeper_rosters` as loaded; `players` is
    `sleeper_players` cut to `player_id`/`full_name`/`position`. Raises if the username matches no
    user, the user owns no roster, or the roster it owns carries no players.
    """
    matches = users.loc[users["display_name"] == username, "user_id"]
    if matches.empty:
        raise ValueError(
            f"No Sleeper user named {username!r} in sleeper_users. Check the configured username "
            "against the league that was actually loaded."
        )
    user_id = matches.iloc[0]

    roster_matches = rosters.loc[rosters["owner_id"] == user_id, "players"]
    if roster_matches.empty:
        raise ValueError(
            f"Sleeper user {username!r} (user_id {user_id}) owns no roster in sleeper_rosters. "
            "Check the configured username against the league that was actually loaded."
        )
    raw_players = roster_matches.iloc[0]
    # `sleeper_rosters.players` is a DuckDB VARCHAR[], which `q()` hands back as a numpy array —
    # `array or []` raises ("truth value... is ambiguous") rather than doing what it looks like it
    # does, so None is checked explicitly instead.
    player_ids = [] if raw_players is None else list(raw_players)
    if not player_ids:
        raise ValueError(
            f"Sleeper user {username!r} owns a roster with no players. Either the wrong league "
            "was loaded, or this build predates the draft."
        )

    rows = players.loc[players["player_id"].isin(player_ids)]
    by_id = dict(zip(rows["player_id"], zip(rows["full_name"], rows["position"])))
    return pd.DataFrame(
        [
            {
                "league_key": "sleeper",
                "platform_player_id": player_id,
                # A team defense's `full_name` is null in `sleeper_players` — only its
                # `player_id`, itself the team abbreviation, is set — matching `free_agents.py`'s
                # identical fallback.
                "player_name": (by_id.get(player_id) or (None, None))[0] or player_id,
                "position": (by_id.get(player_id) or (None, None))[1],
            }
            for player_id in player_ids
        ],
        columns=_ROSTER_COLUMNS,
    )


def resolve_espn_roster(teams: pd.DataFrame, ownership: pd.DataFrame, swid: str) -> pd.DataFrame:
    """The SWID's own ESPN roster, read straight off `espn_player_ownership`.

    `teams` and `ownership` are `espn_teams`/`espn_player_ownership` as loaded. Raises if the SWID
    owns no team, or the team it owns carries no players.

    Filtered on `status == "ONTEAM"`, not `onTeamId` alone: `onTeamId` is `0` for both a
    `FREEAGENT` and a `WAIVERS` row, exactly the distinction `free_agents.py` already documents,
    so `status` is the column that actually says a player is rostered.
    """
    owned = teams.loc[
        teams["owners"].apply(lambda owners: owners is not None and swid in owners)
    ]
    if owned.empty:
        raise ValueError(
            f"SWID {swid} owns no team in espn_teams. Check ESPN_S2/SWID in .env against the "
            "account that's actually in this league."
        )
    team_id = int(owned.iloc[0]["id"])

    mine = ownership.loc[
        (ownership["onTeamId"] == team_id) & (ownership["status"] == "ONTEAM")
    ]
    if mine.empty:
        raise ValueError(
            f"ESPN team {team_id} (SWID {swid}) owns no players in espn_player_ownership. Either "
            "the wrong SWID or league is configured, or this build predates the draft."
        )

    return pd.DataFrame(
        {
            "league_key": "espn",
            "platform_player_id": mine["id"].astype(str),
            "player_name": mine["player.fullName"],
            "position": mine["player.defaultPositionId"].map(POSITION_IDS.get),
        },
        columns=_ROSTER_COLUMNS,
    ).reset_index(drop=True)


def build_my_roster() -> None:
    frame = pd.concat(
        [
            resolve_sleeper_roster(
                q("SELECT user_id, display_name FROM sleeper_users"),
                q("SELECT owner_id, players FROM sleeper_rosters"),
                q("SELECT player_id, full_name, position FROM sleeper_players"),
                USERNAME,
            ),
            resolve_espn_roster(
                q("SELECT id, owners FROM espn_teams"),
                q(
                    'SELECT id, "onTeamId", status, "player.fullName", '
                    '"player.defaultPositionId" FROM espn_player_ownership'
                ),
                SWID,
            ),
        ],
        ignore_index=True,
    )

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE my_roster AS SELECT * FROM frame")
    con.close()

    console.table("my_roster", len(frame))


if __name__ == "__main__":
    build_my_roster()
