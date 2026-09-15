"""Resolving "my roster" per league, from warehouse tables alone — issue #88.

Every fixture is hand-written and small: `sleeper_users`/`sleeper_rosters`/`sleeper_players` and
`espn_teams`/`espn_player_ownership`, cut to the columns resolution actually reads. Nothing here
touches the warehouse — `conftest.py` makes that fail on contact — so these assert what the
resolvers do with a given identity and a given set of tables, never how a real build's data
happens to look today.
"""

import pandas as pd
import pytest

from src.gold.my_roster import resolve_espn_roster, resolve_sleeper_roster

MY_SWID = "{3F67B74A-22B5-4D5C-BBF3-125E8E543948}"
SOMEONE_ELSES_SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"


def sleeper_users(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["user_id", "display_name"])


def sleeper_rosters(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["owner_id", "players"])


def sleeper_players(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "full_name", "position"])


def espn_teams(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["id", "owners"])


def espn_ownership(*rows: dict) -> pd.DataFrame:
    defaults = {"onTeamId": 0, "status": "FREEAGENT"}
    return pd.DataFrame(
        [{**defaults, **row} for row in rows],
        columns=["id", "onTeamId", "status", "player.fullName", "player.defaultPositionId"],
    )


# 1. A configured username resolves through user_id to the roster it owns, enriched with name
#    and position.
def test_sleeper_username_resolves_to_its_own_roster():
    resolved = resolve_sleeper_roster(
        sleeper_users({"user_id": "u1", "display_name": "commonfox"}),
        sleeper_rosters({"owner_id": "u1", "players": ["100", "200"]}),
        sleeper_players(
            {"player_id": "100", "full_name": "A Back", "position": "RB"},
            {"player_id": "200", "full_name": "A Receiver", "position": "WR"},
        ),
        "commonfox",
    )

    assert resolved["league_key"].tolist() == ["sleeper", "sleeper"]
    assert resolved["platform_player_id"].tolist() == ["100", "200"]
    assert resolved["player_name"].tolist() == ["A Back", "A Receiver"]
    assert resolved["position"].tolist() == ["RB", "WR"]


# 2. A team defense carries no full_name in sleeper_players — its player_id is its own team
#    abbreviation — so the name falls back to that abbreviation, matching free_agents.py.
def test_sleeper_defense_falls_back_to_its_team_abbreviation():
    resolved = resolve_sleeper_roster(
        sleeper_users({"user_id": "u1", "display_name": "commonfox"}),
        sleeper_rosters({"owner_id": "u1", "players": ["HOU"]}),
        sleeper_players({"player_id": "HOU", "full_name": None, "position": "DEF"}),
        "commonfox",
    )

    assert resolved["player_name"].tolist() == ["HOU"]


# 3. A username with no matching user raises, naming the username.
def test_sleeper_unknown_username_raises():
    with pytest.raises(ValueError, match="nobody"):
        resolve_sleeper_roster(
            sleeper_users({"user_id": "u1", "display_name": "somebodyelse"}),
            sleeper_rosters({"owner_id": "u1", "players": ["100"]}),
            sleeper_players({"player_id": "100", "full_name": "A Back", "position": "RB"}),
            "nobody",
        )


# 4. A resolved user_id that owns no roster raises rather than returning nothing.
def test_sleeper_user_with_no_roster_raises():
    with pytest.raises(ValueError, match="owns no roster"):
        resolve_sleeper_roster(
            sleeper_users({"user_id": "u1", "display_name": "commonfox"}),
            sleeper_rosters({"owner_id": "someone_else", "players": ["100"]}),
            sleeper_players({"player_id": "100", "full_name": "A Back", "position": "RB"}),
            "commonfox",
        )


# 5. A roster resolved with zero players raises — indistinguishable from "no players yet"
#    otherwise, which is exactly the case a pre-draft build or a wrong league produces.
def test_sleeper_empty_roster_raises():
    with pytest.raises(ValueError, match="no players"):
        resolve_sleeper_roster(
            sleeper_users({"user_id": "u1", "display_name": "commonfox"}),
            sleeper_rosters({"owner_id": "u1", "players": []}),
            sleeper_players(),
            "commonfox",
        )


# 6. A configured SWID resolves through espn_teams.owners to that team's own rostered players.
def test_espn_swid_resolves_to_its_own_roster():
    resolved = resolve_espn_roster(
        espn_teams(
            {"id": 1, "owners": [MY_SWID]},
            {"id": 2, "owners": [SOMEONE_ELSES_SWID]},
        ),
        espn_ownership(
            {"id": 300, "onTeamId": 1, "status": "ONTEAM", "player.fullName": "A Back",
             "player.defaultPositionId": 2},
            {"id": 400, "onTeamId": 2, "status": "ONTEAM", "player.fullName": "Someone Else's",
             "player.defaultPositionId": 3},
        ),
        MY_SWID,
    )

    assert resolved["league_key"].tolist() == ["espn"]
    assert resolved["platform_player_id"].tolist() == ["300"]
    assert resolved["player_name"].tolist() == ["A Back"]
    assert resolved["position"].tolist() == ["RB"]


# 7. onTeamId alone is not enough to call a player rostered — a FREEAGENT/WAIVERS row can carry
#    the same onTeamId a real owner has, so status has to be checked too, exactly as
#    free_agents.py already documents for the opposite (availability) question.
def test_espn_status_is_checked_not_just_onteamid():
    resolved = resolve_espn_roster(
        espn_teams({"id": 1, "owners": [MY_SWID]}),
        espn_ownership(
            {"id": 300, "onTeamId": 1, "status": "ONTEAM", "player.fullName": "A Back",
             "player.defaultPositionId": 2},
            {"id": 301, "onTeamId": 1, "status": "WAIVERS", "player.fullName": "Just Dropped",
             "player.defaultPositionId": 4},
        ),
        MY_SWID,
    )

    assert resolved["platform_player_id"].tolist() == ["300"]


# 8. A SWID owning no team raises, naming the SWID.
def test_espn_unknown_swid_raises():
    with pytest.raises(ValueError, match="owns no team"):
        resolve_espn_roster(
            espn_teams({"id": 1, "owners": [SOMEONE_ELSES_SWID]}),
            espn_ownership(),
            MY_SWID,
        )


# 9. A team resolved with zero owned players raises — the pre-draft case this ticket calls out
#    explicitly (espn_rosters/espn_player_ownership carry no ownership until the ESPN draft has
#    actually happened).
def test_espn_team_with_no_players_raises():
    with pytest.raises(ValueError, match="owns no players"):
        resolve_espn_roster(
            espn_teams({"id": 1, "owners": [MY_SWID]}),
            espn_ownership(
                {"id": 300, "onTeamId": 0, "status": "FREEAGENT", "player.fullName": "A Back",
                 "player.defaultPositionId": 2},
            ),
            MY_SWID,
        )
