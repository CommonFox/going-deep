"""Refuse to draft off a board priced for a different league than the one on screen.

The live tool reads `draft_board`, `draft_availability` and `draft_plans` filtered to one
`league_key` — a board priced for 14-team superflex. Until now the draft it watched was found
*through* that league, so the two could not disagree. Pointing the tool at a draft ID by hand
breaks that guarantee: a Sleeper mock is a draft record with no league behind it, and its
settings are whatever the person who opened the lobby chose.

A 12-team 1QB mock read against a 14-team superflex board is wrong in the way that matters most
here — every number still renders, nothing looks broken, and the replacement level under all of
them is for a league nobody is drafting in. That is the same shape of silent error `resolve_seat`
already refuses for when it will not guess a seat, so it gets the same treatment.

What is compared is what the *pricing* depends on: how many teams there are and what each of them
starts. Those two set replacement level. Rounds do not — see the last case.
"""

import pytest

from src.draft.seat import check_priced_for

# The shape the warehouse priced the `sleeper` board for, as `league_settings` records it.
PRICED = {
    "team_count": 14,
    "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1},
}


def league(**overrides) -> dict:
    """What `resolve_seat` returns for a draft record — the mock's own shape."""
    resolved = {
        "seat": 1,
        "roster_id": 1,
        "team_count": 14,
        "rounds": 15,
        "slots": dict(PRICED["slots"]),
    }
    return {**resolved, **overrides}


# 1. A draft whose shape matches the board it will be read against is allowed through.
def test_a_matching_draft_passes():
    assert check_priced_for(league(), PRICED) is None


# 2. A different team count is refused, and the message names both numbers so the drafter can
#    see which side is wrong without opening the lobby settings.
def test_a_different_team_count_is_refused():
    with pytest.raises(ValueError) as raised:
        check_priced_for(league(team_count=12), PRICED)
    message = str(raised.value)
    assert "12" in message and "14" in message


# 3. A 1QB mock read against a superflex board is refused, naming the slot that differs. This is
#    the case the guard exists for: superflex is what makes this league draft differently, and a
#    board priced with it is wrong from the first pick in a league without it.
def test_a_different_starting_lineup_is_refused():
    without_superflex = {slot: n for slot, n in PRICED["slots"].items() if slot != "SUPER_FLEX"}
    with pytest.raises(ValueError) as raised:
        check_priced_for(league(slots=without_superflex), PRICED)
    assert "SUPER_FLEX" in str(raised.value)


# 4. A shallower or deeper draft is *not* refused. Rounds set how far the draft goes, not what a
#    player is worth: replacement level falls out of team count and starting slots, both of which
#    still match. A 10-round mock off a 15-round board prices every player correctly.
def test_a_different_round_count_is_allowed():
    assert check_priced_for(league(rounds=10), PRICED) is None
