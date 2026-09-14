"""Which live Sunday/Monday-night matchups are still worth watching, and who's left to play.

`src.gameday.storylines` turns a week's live Sleeper matchup payload into a short list of
storylines: head-to-heads that (a) are still in question because someone has a starter yet to
kick off tonight, ranked closest-margin-first. Every function here is pure — frames and payloads
in, dicts or a string out — so these assert against hand-built fixtures rather than a live poll or
the warehouse.
"""

import pandas as pd

from src.gameday.storylines import (
    build_storylines,
    group_by_matchup,
    player_lookup,
    remaining_starters,
    render,
    team_owners,
    tonight_teams,
)

# ---------------------------------------------------------------------------
# team_owners
# ---------------------------------------------------------------------------


def rosters(*rows: dict) -> pd.DataFrame:
    defaults = {"roster_id": 1, "owner_id": "u1"}
    return pd.DataFrame([{**defaults, **row} for row in rows])


def users(*rows: dict) -> pd.DataFrame:
    defaults = {"user_id": "u1", "display_name": "someuser", "metadata.team_name": None}
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_team_owners_prefers_the_sleeper_team_name():
    owners = team_owners(
        rosters({"roster_id": 1, "owner_id": "u1"}),
        users({"user_id": "u1", "display_name": "jhart2324", "metadata.team_name": "Jacks Team"}),
    )
    assert owners[1] == "Jacks Team"


def test_team_owners_falls_back_to_display_name_with_no_team_name_set():
    owners = team_owners(
        rosters({"roster_id": 1, "owner_id": "u1"}),
        users({"user_id": "u1", "display_name": "jhart2324", "metadata.team_name": None}),
    )
    assert owners[1] == "jhart2324"


def test_team_owners_keys_every_roster_separately():
    owners = team_owners(
        rosters(
            {"roster_id": 1, "owner_id": "u1"},
            {"roster_id": 2, "owner_id": "u2"},
        ),
        users(
            {"user_id": "u1", "display_name": "alice", "metadata.team_name": None},
            {"user_id": "u2", "display_name": "bob", "metadata.team_name": "Bob's Squad"},
        ),
    )
    assert owners == {1: "alice", 2: "Bob's Squad"}


# ---------------------------------------------------------------------------
# player_lookup
# ---------------------------------------------------------------------------


def players_frame(*rows: dict) -> pd.DataFrame:
    defaults = {
        "player_id": "1",
        "full_name": "A Player",
        "first_name": "A",
        "last_name": "Player",
        "position": "WR",
        "team": "SF",
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_player_lookup_uses_full_name_and_normalizes_team():
    lookup = player_lookup(
        players_frame({"player_id": "7523", "full_name": "Trevor Lawrence", "team": "JAX"})
    )
    assert lookup["7523"] == {"name": "Trevor Lawrence", "position": "WR", "team": "JAX"}


def test_player_lookup_falls_back_to_first_and_last_name_for_a_defense():
    lookup = player_lookup(
        players_frame(
            {
                "player_id": "JAX",
                "full_name": None,
                "first_name": "Jacksonville",
                "last_name": "Jaguars",
                "position": "DEF",
                "team": "JAX",
            }
        )
    )
    assert lookup["JAX"]["name"] == "Jacksonville Jaguars"


def test_player_lookup_normalizes_an_alias_team_abbreviation():
    lookup = player_lookup(players_frame({"player_id": "1", "team": "LAR"}))
    assert lookup["1"]["team"] == "LA"


def test_player_lookup_leaves_a_missing_team_as_none():
    lookup = player_lookup(players_frame({"player_id": "1", "team": None}))
    assert lookup["1"]["team"] is None


# ---------------------------------------------------------------------------
# tonight_teams
# ---------------------------------------------------------------------------


def schedule(*rows: dict) -> pd.DataFrame:
    defaults = {
        "week": 1, "weekday": "Sunday", "gametime": "13:00",
        "home_team": "SEA", "away_team": "NE",
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_tonight_teams_picks_out_only_the_night_game():
    week1 = schedule(
        {"gametime": "13:00", "home_team": "CAR", "away_team": "CHI"},
        {"gametime": "16:25", "home_team": "MIN", "away_team": "GB"},
        {"gametime": "20:20", "home_team": "NYG", "away_team": "DAL"},
    )
    assert tonight_teams(week1, week=1, weekday="Sunday") == {"NYG", "DAL"}


def test_tonight_teams_covers_a_monday_doubleheader():
    week2 = schedule(
        {"weekday": "Monday", "gametime": "19:15", "home_team": "PIT", "away_team": "NYJ"},
        {"weekday": "Monday", "gametime": "20:15", "home_team": "KC", "away_team": "DEN"},
    )
    assert tonight_teams(week2, week=1, weekday="Monday") == {"PIT", "NYJ", "KC", "DEN"}


def test_tonight_teams_is_empty_when_nothing_qualifies():
    week1 = schedule({"gametime": "13:00"})
    assert tonight_teams(week1, week=1, weekday="Monday") == set()


def test_tonight_teams_ignores_a_qualifying_game_in_a_different_week():
    frame = schedule(
        {"week": 1, "gametime": "20:20", "home_team": "NYG", "away_team": "DAL"},
        {"week": 2, "gametime": "20:20", "home_team": "KC", "away_team": "DEN"},
    )
    assert tonight_teams(frame, week=1, weekday="Sunday") == {"NYG", "DAL"}


def test_tonight_teams_normalizes_an_alias_abbreviation():
    frame = schedule({"gametime": "20:20", "home_team": "LAR", "away_team": "SF"})
    assert tonight_teams(frame, week=1, weekday="Sunday") == {"LA", "SF"}


# ---------------------------------------------------------------------------
# group_by_matchup
# ---------------------------------------------------------------------------


def test_group_by_matchup_pairs_rosters_sharing_a_matchup_id():
    rows = [
        {"roster_id": 1, "matchup_id": 5},
        {"roster_id": 2, "matchup_id": 5},
        {"roster_id": 3, "matchup_id": 6},
        {"roster_id": 4, "matchup_id": 6},
    ]
    grouped = group_by_matchup(rows)
    assert {r["roster_id"] for r in grouped[5]} == {1, 2}
    assert {r["roster_id"] for r in grouped[6]} == {3, 4}


# ---------------------------------------------------------------------------
# remaining_starters
# ---------------------------------------------------------------------------

PLAYERS = {
    "100": {"name": "Justin Herbert", "position": "QB", "team": "LAC"},
    "200": {"name": "Drake Maye", "position": "QB", "team": "NE"},
    "JAX": {"name": "Jacksonville Jaguars", "position": "DEF", "team": "JAX"},
}
PROJECTIONS = {"100": 19.4}
TONIGHT = {"LAC"}


def test_remaining_starters_keeps_only_players_on_tonights_teams():
    remaining = remaining_starters(["100", "200"], PLAYERS, PROJECTIONS, TONIGHT)
    assert [r["player_id"] for r in remaining] == ["100"]


def test_remaining_starters_reports_a_known_projection():
    remaining = remaining_starters(["100"], PLAYERS, PROJECTIONS, TONIGHT)
    assert remaining[0]["projected"] == 19.4


def test_remaining_starters_reports_none_for_an_unprojected_player():
    remaining = remaining_starters(["JAX"], PLAYERS, PROJECTIONS, {"JAX"})
    assert remaining[0]["projected"] is None


def test_remaining_starters_skips_empty_lineup_slots():
    remaining = remaining_starters(["100", "0", 0], PLAYERS, PROJECTIONS, TONIGHT)
    assert [r["player_id"] for r in remaining] == ["100"]


def test_remaining_starters_skips_an_id_missing_from_the_player_dictionary():
    remaining = remaining_starters(["nonexistent"], PLAYERS, PROJECTIONS, TONIGHT)
    assert remaining == []


# ---------------------------------------------------------------------------
# build_storylines
# ---------------------------------------------------------------------------

OWNERS = {1: "Team One", 2: "Team Two", 3: "Team Three", 4: "Team Four"}


def matchup_row(roster_id, matchup_id, points, starters) -> dict:
    return {
        "roster_id": roster_id, "matchup_id": matchup_id, "points": points,
        "starters": starters,
    }


def test_build_storylines_includes_a_matchup_with_someone_left_to_play():
    matchups = [
        matchup_row(1, 1, 98.64, ["100"]),
        matchup_row(2, 1, 94.10, []),
    ]
    lines = build_storylines(matchups, OWNERS, PLAYERS, PROJECTIONS, TONIGHT)
    assert len(lines) == 1
    assert round(lines[0]["margin"], 2) == 4.54


def test_build_storylines_drops_a_matchup_thats_already_decided():
    matchups = [
        matchup_row(1, 1, 98.64, []),
        matchup_row(2, 1, 94.10, []),
    ]
    assert build_storylines(matchups, OWNERS, PLAYERS, PROJECTIONS, TONIGHT) == []


def test_build_storylines_sorts_closest_margin_first():
    matchups = [
        matchup_row(1, 1, 100.0, ["100"]),
        matchup_row(2, 1, 80.0, []),
        matchup_row(3, 2, 100.0, ["100"]),
        matchup_row(4, 2, 97.0, []),
    ]
    lines = build_storylines(matchups, OWNERS, PLAYERS, PROJECTIONS, TONIGHT)
    assert [line["matchup_id"] for line in lines] == [2, 1]


def test_build_storylines_puts_the_leading_team_first():
    matchups = [
        matchup_row(1, 1, 80.0, ["100"]),
        matchup_row(2, 1, 100.0, []),
    ]
    lines = build_storylines(matchups, OWNERS, PLAYERS, PROJECTIONS, TONIGHT)
    assert [team["roster_id"] for team in lines[0]["teams"]] == [2, 1]


def test_build_storylines_includes_a_matchup_with_only_one_side_still_alive():
    matchups = [
        matchup_row(1, 1, 60.0, []),
        matchup_row(2, 1, 90.0, ["100"]),
    ]
    lines = build_storylines(matchups, OWNERS, PLAYERS, PROJECTIONS, TONIGHT)
    assert len(lines) == 1
    trailing = [t for t in lines[0]["teams"] if t["roster_id"] == 1][0]
    assert trailing["remaining"] == []


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def line_naming(out: str, text: str) -> str:
    matches = [line for line in out.splitlines() if text in line]
    assert matches, f"no line mentions {text!r}:\n{out}"
    return matches[0]


def test_render_names_both_teams_and_the_score():
    storylines = build_storylines(
        [matchup_row(1, 1, 98.64, ["100"]), matchup_row(2, 1, 94.10, [])],
        OWNERS, PLAYERS, PROJECTIONS, TONIGHT,
    )
    out = render(storylines, week=1, weekday="Sunday")
    line_naming(out, "Team One")
    line_naming(out, "Team Two")
    line_naming(out, "98.64")
    line_naming(out, "94.1")


def test_render_names_a_remaining_player_and_its_projection():
    storylines = build_storylines(
        [matchup_row(1, 1, 98.64, ["100"]), matchup_row(2, 1, 94.10, [])],
        OWNERS, PLAYERS, PROJECTIONS, TONIGHT,
    )
    out = render(storylines, week=1, weekday="Sunday")
    line_naming(out, "Justin Herbert")
    line_naming(out, "19.4")


def test_render_says_so_when_nothing_is_still_in_question():
    out = render([], week=1, weekday="Sunday")
    assert "no matchups" in out.lower()
