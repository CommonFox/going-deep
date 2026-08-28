"""Which seat is mine, read from the draft record rather than typed in.

Cases 1-7 of the terminal render ticket. Every one asserts what comes *out* of `resolve_seat`
for a draft record shaped the way Sleeper's own `GET /draft/<id>` returns it — string keys,
integer values, slots spelled `slots_super_flex`.

The fixture below is this league's real draft in miniature, and the one number in it worth
saying out loud is that **seat 1 holds roster 6**. Sleeper hands back two separate maps for a
reason: `draft_order` places a user in the snake, `slot_to_roster_id` says which roster that
seat's picks land on. A tool that assumed they were the same would put another manager's picks
on my roster and lose mine, and would do it silently. So they differ here in every fixture.
"""

import pytest

from src.draft.seat import resolve_seat

ME = "1154199555601219584"
SOMEONE_ELSE = "1263304885919551488"


def draft(**overrides) -> dict:
    """One draft record, as Sleeper returns it, with this league's real shape."""
    record = {
        "draft_id": "1390836962482487296",
        "type": "snake",
        "status": "pre_draft",
        "season": "2026",
        # Sleeper keys this on the user and values it with the seat, both as they arrive: the
        # user IDs are strings and the seats are bare integers.
        "draft_order": {ME: 1, SOMEONE_ELSE: 2},
        # And keys this one on the seat *as a string*, valued with the roster. Seat 1 is roster
        # 6 in the real draft, so nothing here can pass by treating the two as interchangeable.
        "slot_to_roster_id": {"1": 6, "2": 7},
        "settings": {
            "teams": 14,
            "rounds": 15,
            "slots_qb": 1,
            "slots_rb": 2,
            "slots_wr": 2,
            "slots_te": 1,
            "slots_flex": 1,
            "slots_super_flex": 1,
            "slots_k": 1,
            "slots_def": 1,
            "slots_bn": 5,
        },
    }
    return {**record, **overrides}


# 1. The seat is read from the draft order for the drafter's user ID.
def test_the_seat_comes_from_the_draft_order():
    assert resolve_seat(draft(), ME)["seat"] == 1
    assert resolve_seat(draft(), SOMEONE_ELSE)["seat"] == 2


# 2. The roster ID is read from slot_to_roster_id for that seat, which is not the seat.
def test_the_roster_id_comes_from_the_seat_to_roster_map():
    resolved = resolve_seat(draft(), ME)
    assert resolved["seat"] == 1
    assert resolved["roster_id"] == 6


# 3. Team count and rounds come from the draft's own settings.
def test_the_snake_shape_comes_from_the_drafts_settings():
    resolved = resolve_seat(draft(), ME)
    assert resolved["team_count"] == 14
    assert resolved["rounds"] == 15


# 4. Starting slots come from the settings, superflex included, in the shape ingestion wants.
def test_the_starting_slots_come_from_the_drafts_settings():
    slots = resolve_seat(draft(), ME)["slots"]
    assert slots["QB"] == 1
    assert slots["RB"] == 2
    assert slots["WR"] == 2
    assert slots["TE"] == 1
    assert slots["FLEX"] == 1
    # The slot that makes this league draft differently from the other one.
    assert slots["SUPER_FLEX"] == 1
    assert slots["K"] == 1
    assert slots["DST"] == 1


def test_a_slot_the_league_does_not_start_is_not_reported_as_one():
    # Sleeper leagues have no punter slot; the ESPN one does. A zero must not become a row in
    # the drafter's lineup, or the table describes a league nobody is in.
    slots = resolve_seat(draft(), ME)["slots"]
    assert slots.get("P", 0) == 0


# 5. A user absent from the draft order raises, naming him.
def test_a_user_who_is_not_in_this_draft_raises_and_names_him():
    stranger = "9999999999999999999"
    with pytest.raises(ValueError) as caught:
        resolve_seat(draft(), stranger)
    assert stranger in str(caught.value)


# 6. A draft whose order has not been drawn yet raises and says so.
def test_an_undrawn_draft_order_raises_rather_than_guessing_a_seat():
    with pytest.raises(ValueError) as caught:
        resolve_seat(draft(draft_order=None), ME)
    assert "order" in str(caught.value).lower()


# 7. A non-snake draft refuses rather than computing snake pick numbers.
def test_a_draft_that_is_not_a_snake_refuses():
    # Every pick number this feature reports assumes rounds alternate direction. Against a
    # linear draft that arithmetic is wrong from round two, and wrong quietly.
    with pytest.raises(ValueError) as caught:
        resolve_seat(draft(type="linear"), ME)
    assert "snake" in str(caught.value).lower()
