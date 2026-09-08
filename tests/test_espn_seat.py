"""Which seat is mine in the ESPN draft, read from the league record rather than typed in.

ESPN's counterpart to `test_draft_seat.py`. The fixture below reproduces the real shape already
seen in `data/raw/espn/league_96973123_2026.json`: `draftDetail.drafted`/`inProgress` say whether
the room has opened, `settings.draftSettings.pickOrder` is a list of team IDs in snake order (not
a user->seat map the way Sleeper's `draft_order` is), and `settings.rosterSettings.lineupSlotCounts`
is keyed on ESPN's numeric slot IDs.

Unlike Sleeper, there is only one map here, not two: `pickOrder` already lists team IDs directly,
so there's no separate "which roster does this seat's picks land on" step. What ESPN does need
that Sleeper doesn't is finding *my* team ID at all — Sleeper's draft order is keyed by user ID
already; ESPN's is keyed by team ID, so the drafter is found by matching the `SWID` cookie against
a team's `owners`.
"""

import pytest

from src.draft.espn_seat import resolve_seat

MY_SWID = "{3F67B74A-22B5-4D5C-BBF3-125E8E543948}"
MY_TEAM_ID = 1
SOMEONE_ELSES_SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"


def league(**overrides) -> dict:
    """One league record, combining ESPN's mSettings + mTeam + mDraftDetail views."""
    record = {
        "draftDetail": {"drafted": False, "inProgress": True},
        "settings": {
            "draftSettings": {
                "type": "SNAKE",
                # Team 1 (mine) picks third; nine other teams fill out the order.
                "pickOrder": [9, 7, 1, 2, 3, 8, 4, 10, 5, 6],
            },
            "rosterSettings": {
                "lineupSlotCounts": {
                    "0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "7": 1,
                    "16": 1, "17": 1, "18": 1, "20": 7, "21": 2,
                },
            },
            "size": 10,
        },
        "teams": [
            {"id": MY_TEAM_ID, "owners": [MY_SWID], "primaryOwner": MY_SWID},
            {"id": 2, "owners": [SOMEONE_ELSES_SWID], "primaryOwner": SOMEONE_ELSES_SWID},
        ],
    }
    return {**record, **overrides}


# 1. The seat is read from pickOrder's position for the team my SWID owns.
def test_the_seat_comes_from_pick_order():
    assert resolve_seat(league(), MY_SWID)["seat"] == 3


# 2. The roster ID is my own ESPN team ID, found by matching my SWID against a team's owners.
def test_the_roster_id_is_my_own_team_id():
    resolved = resolve_seat(league(), MY_SWID)
    assert resolved["seat"] == 3
    assert resolved["roster_id"] == MY_TEAM_ID


# 3. Team count comes from the league's own size.
def test_the_team_count_comes_from_settings_size():
    assert resolve_seat(league(), MY_SWID)["team_count"] == 10


# 4. Rounds is every roster spot a team drafts to fill — starters, bench and IR alike.
def test_rounds_is_the_full_roster_size_bench_and_ir_included():
    # 1 QB + 2 RB + 2 WR + 1 TE + 1 FLEX + 1 SUPER_FLEX + 1 DST + 1 K + 1 P + 7 bench + 2 IR = 20.
    assert resolve_seat(league(), MY_SWID)["rounds"] == 20


# 5. Starting slots come from the numeric lineup-slot-ID mapping, superflex included.
def test_the_starting_slots_come_from_the_numeric_slot_ids():
    slots = resolve_seat(league(), MY_SWID)["slots"]
    assert slots["QB"] == 1
    assert slots["RB"] == 2
    assert slots["WR"] == 2
    assert slots["TE"] == 1
    assert slots["FLEX"] == 1
    assert slots["SUPER_FLEX"] == 1
    assert slots["K"] == 1
    assert slots["P"] == 1
    assert slots["DST"] == 1
    # Bench and IR are not startable slots, matching `seat.py`'s own "the bench is whoever does
    # not fit a starting slot" rule — they count toward rounds, never toward the lineup shown.
    assert "BENCH" not in slots
    assert "IR" not in slots


# 6. A slot the league does not start is not reported as one.
def test_a_slot_the_league_does_not_start_is_not_reported_as_one():
    without_superflex = league()
    without_superflex["settings"]["rosterSettings"]["lineupSlotCounts"]["7"] = 0
    slots = resolve_seat(without_superflex, MY_SWID)["slots"]
    assert slots.get("SUPER_FLEX", 0) == 0
    assert "SUPER_FLEX" not in slots


# 7. An SWID that owns no team in this league raises, naming it.
def test_an_swid_that_owns_no_team_raises_and_names_it():
    stranger = "{99999999-9999-9999-9999-999999999999}"
    with pytest.raises(ValueError) as caught:
        resolve_seat(league(), stranger)
    assert stranger in str(caught.value)


# 8. A draft that hasn't opened yet raises rather than trusting a provisional pickOrder — ESPN
#    randomizes the order when the room opens if draftSettings.orderType is DRAFT_START, so a
#    pickOrder read before then is not the real one.
def test_a_draft_that_has_not_started_raises_rather_than_guessing_a_seat():
    with pytest.raises(ValueError) as caught:
        resolve_seat(league(draftDetail={"drafted": False, "inProgress": False}), MY_SWID)
    assert "start" in str(caught.value).lower()


# 9. A draft ESPN has already marked complete is fine to resolve a seat from — the order is real
#    either way once the room has opened.
def test_a_completed_draft_still_resolves_a_seat():
    resolved = resolve_seat(league(draftDetail={"drafted": True, "inProgress": False}), MY_SWID)
    assert resolved["seat"] == 3


# 10. A non-snake draft refuses rather than computing snake pick numbers.
def test_a_draft_that_is_not_a_snake_refuses():
    non_snake = league()
    non_snake["settings"]["draftSettings"]["type"] = "AUCTION"
    with pytest.raises(ValueError) as caught:
        resolve_seat(non_snake, MY_SWID)
    assert "snake" in str(caught.value).lower()
