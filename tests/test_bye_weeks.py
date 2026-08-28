"""A player's bye week, derived from the schedule rather than read off an ADP source.

Every case here is agreed before the implementation, per the repo's testing rule, and asserts what
comes *out* of the two functions for a given schedule and board — never how either gets there.

The fixture is a four-team, three-week season rather than a slice of the real one, because a bye
week is only checkable by hand if the whole season is small enough to hold in your head. In `FULL`
below, KC and LA rest in week 3 and SF and BUF rest in week 2, which can be read straight off the
four games.
"""

import pandas as pd
import pytest

from src.gold.draft_board import add_bye_weeks, bye_weeks


def schedule(*games: tuple[int, str, str]) -> pd.DataFrame:
    """A schedule frame as `(week, home, away)` triples, in the columns the derivation reads."""
    return pd.DataFrame(
        [{"week": week, "home_team": home, "away_team": away} for week, home, away in games],
        # Named explicitly so an empty frame still has the shape the warehouse hands back, the way
        # the ingestion and ranking suites build theirs.
        columns=["week", "home_team", "away_team"],
    )


# Three weeks, four teams, one bye each: KC and LA sit out week 3, SF and BUF sit out week 2. BUF
# is away in both of its games, which is case 2 — it exists in this fixture on purpose.
FULL = schedule(
    (1, "KC", "SF"),
    (1, "LA", "BUF"),
    (2, "KC", "LA"),
    (3, "SF", "BUF"),
)

# The same season spelled the way the sources actually spell it: the schedule says ARI and LA,
# while the board carries AZ from `rosters` and LAR from anything that follows Sleeper.
DIVERGENT = schedule(
    (1, "ARI", "LA"),
    (1, "KC", "SF"),
    (2, "ARI", "KC"),
    (3, "LA", "SF"),
)


def board(*rows: dict) -> pd.DataFrame:
    """A board with the column the join reads, plus one it must carry through untouched."""
    defaults = {
        "player_id": "00-0000001",
        "player_name": "A Back",
        "position": "RB",
        "team": "KC",
        "points_over_replacement": 100.0,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows], columns=list(defaults))


def byes(frame: pd.DataFrame) -> dict:
    """A bye frame as a plain dict, so an expectation reads as one line."""
    return dict(zip(frame["team"], frame["bye_week"]))


# 1. A team that plays every week but one gets that week as its bye.
def test_the_week_a_team_has_no_game_is_its_bye():
    assert byes(bye_weeks(FULL)) == {"KC": 3, "SF": 2, "LA": 3, "BUF": 2}


# 2. A team appearing only as an away team is still covered.
def test_a_team_that_is_never_at_home_still_gets_a_bye():
    # BUF is the away side of both its games, so a derivation reading only `home_team` would not
    # know it exists at all.
    assert byes(bye_weeks(FULL))["BUF"] == 2


# 3. A team missing two weeks fails loudly, naming the team and both weeks.
def test_a_team_with_two_byes_fails_naming_the_team_and_the_weeks():
    incomplete = schedule((1, "KC", "SF"), (2, "KC", "SF"), (3, "LA", "BUF"))

    with pytest.raises(ValueError) as failure:
        bye_weeks(incomplete)

    message = str(failure.value)
    assert "LA" in message and "BUF" in message
    assert "1" in message and "2" in message


# 4. A team missing no week fails the same way.
def test_a_team_that_plays_every_week_fails_too():
    # Not what the acceptance criteria asked for, and agreed anyway: a team with no bye at all
    # means the same thing a team with two byes means — the schedule is not a real season — and
    # returning a frame with that team silently absent would be worse than stopping.
    every_week = schedule((1, "KC", "SF"), (2, "KC", "SF"))

    with pytest.raises(ValueError) as failure:
        bye_weeks(every_week)

    assert "KC" in str(failure.value)


# 5. Every board row for a team in the schedule carries that team's bye week.
def test_every_board_row_carries_its_team_bye_week():
    priced = board({"team": "KC"}, {"team": "SF"}, {"team": "LA"}, {"team": "BUF"})

    assert list(add_bye_weeks(priced, FULL)["bye_week"]) == [3, 2, 3, 2]


# 6. A board row spelled AZ and one spelled LAR both still find their bye.
def test_a_divergent_abbreviation_still_finds_its_bye():
    # The board holds both spellings of Arizona at once — twenty players arrive from `rosters` as
    # AZ and the defense arrives from Sleeper as ARI — so this is a live collision, not a
    # hypothetical one.
    priced = board({"team": "AZ"}, {"team": "ARI"}, {"team": "LAR"}, {"team": "LA"})

    assert list(add_bye_weeks(priced, DIVERGENT)["bye_week"]) == [3, 3, 2, 2]


# 7. A team defense gets the same bye as that team's players.
def test_a_defense_gets_its_team_bye_week():
    priced = board(
        {"player_id": "00-0000009", "position": "RB", "team": "SF"},
        {"player_id": "SF", "player_name": "SF", "position": "DST", "team": "SF"},
    )

    assert list(add_bye_weeks(priced, FULL)["bye_week"]) == [2, 2]


# 8. A board row whose team has no schedule rows gets a null bye, not a wrong one.
def test_a_team_absent_from_the_schedule_gets_no_bye():
    priced = board({"team": "MIA"}, {"team": "KC"})

    with_byes = add_bye_weeks(priced, FULL)

    assert pd.isna(with_byes["bye_week"][0])
    assert with_byes["bye_week"][1] == 3


# 9. A board row with no team at all gets a null bye and does not raise.
def test_a_player_with_no_team_gets_no_bye():
    # Free agents, mostly: twenty-two players on the real board were on a 2025 roster and no 2026
    # one. They cannot have a bye because they do not have a team.
    priced = board({"team": None}, {"team": "KC"})

    with_byes = add_bye_weeks(priced, FULL)

    assert pd.isna(with_byes["bye_week"][0])
    assert with_byes["bye_week"][1] == 3


# 10. The input board is not mutated and keeps its other columns.
def test_the_board_handed_in_is_left_alone():
    priced = board({"team": "KC"})
    before = priced.copy()

    with_byes = add_bye_weeks(priced, FULL)

    pd.testing.assert_frame_equal(priced, before)
    assert "bye_week" not in priced.columns
    assert list(with_byes.columns) == [*before.columns, "bye_week"]
    assert with_byes["points_over_replacement"][0] == 100.0
