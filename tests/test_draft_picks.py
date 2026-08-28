"""Spec cases 11, 12, 13, 16, 17, 18, 19 and 28 from the live draft assistant's ingestion ticket.

These assert what comes *out* of `ingest_picks` for a given payload, board and league shape —
never how it gets there. Every fixture is small enough to work out by hand, which is the only
kind that stays true across a warehouse rebuild.

The payload shapes are Sleeper's own, not invented:

- A pick keys its player on `player_id`, which is Sleeper's identifier — the string the board
  now carries in `sleeper_id`, and never a name.
- Ownership is `roster_id`. The drafter's seat is a separate thing, used only for pick arithmetic.
- `pick_no` is the overall pick number, 1-indexed across the whole draft.
- `metadata` carries the player's name, which is the only thing that makes an *unmatched* pick
  reportable: if the ID resolves to nothing, the name is all there is to put on screen.
"""

import pandas as pd

from src.draft.picks import ingest_picks

TEAM_COUNT = 14
ROUNDS = 15

MY_ROSTER = 1
ANOTHER_ROSTER = 7

# This league's real starting lineup, superflex included — the shape spec case 28 is about.
SUPERFLEX_SLOTS = {
    "QB": 1, "RB": 2, "WR": 3, "TE": 1,
    "FLEX": 1, "SUPER_FLEX": 1,
    "K": 1, "DST": 1, "BENCH": 6,
}


def board(*rows: dict) -> pd.DataFrame:
    """A board with only the columns ingestion reads, defaulted to one findable running back."""
    defaults = {
        "player_id": "00-0000001",
        "sleeper_id": "4034",
        "player_name": "A Back",
        "position": "RB",
    }
    return pd.DataFrame(
        [{**defaults, **row} for row in rows],
        # Named explicitly for the same reason the identity suite names them: a bare
        # DataFrame([]) has no columns at all, which is not a shape the warehouse can hand back,
        # so it is not one worth making the code under test tolerate.
        columns=list(defaults),
    )


def pick(sleeper_id: str, pick_no: int, roster_id: int = ANOTHER_ROSTER, **metadata) -> dict:
    """One pick as Sleeper hands it back."""
    named = {"first_name": "Some", "last_name": "Player", "position": "RB", **metadata}
    return {
        "player_id": sleeper_id,
        "roster_id": roster_id,
        "pick_no": pick_no,
        "round": (pick_no - 1) // TEAM_COUNT + 1,
        "metadata": named,
    }


def league(**overrides) -> dict:
    """The league's shape: which seat is mine, how big the draft is, what a lineup requires."""
    return {
        "seat": 1,
        "roster_id": MY_ROSTER,
        "team_count": TEAM_COUNT,
        "rounds": ROUNDS,
        "slots": SUPERFLEX_SLOTS,
        **overrides,
    }


def players_in(roster: pd.DataFrame, slot: str) -> list[str]:
    """The names sitting in one slot of a returned roster."""
    return roster.loc[roster["slot"] == slot, "players"].iloc[0]


def assert_same_result(left: dict, right: dict) -> None:
    """Every part of two results agrees — used where the claim is about order-independence."""
    assert left["taken"] == right["taken"]
    assert left["unmatched"] == right["unmatched"]
    assert left["next_pick"] == right["next_pick"]
    assert left["picks_made"] == right["picks_made"]
    pd.testing.assert_frame_equal(left["roster"], right["roster"])


# 11. A pick whose player ID matches nothing is returned in the unmatched list.
def test_a_pick_matching_no_board_row_is_returned_as_unmatched():
    result = ingest_picks(
        [pick("9999", pick_no=1, first_name="Ghost", last_name="Runner", position="WR")],
        board({"sleeper_id": "4034", "player_name": "A Back"}),
        league(),
    )

    assert result["unmatched"] == [
        {"sleeper_id": "9999", "player_name": "Ghost Runner", "position": "WR", "pick_no": 1}
    ]


# 12. An unmatched pick removes nobody from the board and leaves the rest of the result intact.
def test_an_unmatched_pick_removes_nobody_and_leaves_the_rest_intact():
    result = ingest_picks(
        [
            pick("9999", pick_no=1, first_name="Ghost", last_name="Runner"),
            pick("4034", pick_no=2, roster_id=ANOTHER_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "A Back"},
            {"player_id": "00-0000002", "sleeper_id": "5849", "player_name": "A Receiver",
             "position": "WR"},
        ),
        league(seat=5),
    )

    # Only the pick that matched takes anybody off the board. The ghost removes nobody, and in
    # particular does not remove the row it sorts next to.
    assert result["taken"] == {"00-0000001"}
    assert len(result["unmatched"]) == 1
    # Both picks still happened, so both still count against the seat's next turn.
    assert result["picks_made"] == 2
    assert result["next_pick"] == 5
    assert result["roster"]["filled"].sum() == 0


# 13. A pick belonging to the drafter's own roster ID appears in the roster and is also removed
#     from the board.
def test_a_pick_on_the_drafters_roster_fills_a_slot_and_is_also_taken():
    result = ingest_picks(
        [
            pick("4034", pick_no=1, roster_id=MY_ROSTER),
            pick("5849", pick_no=2, roster_id=ANOTHER_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "My Back"},
            {"player_id": "00-0000002", "sleeper_id": "5849", "player_name": "Their Receiver",
             "position": "WR"},
        ),
        league(),
    )

    assert result["taken"] == {"00-0000001", "00-0000002"}
    assert players_in(result["roster"], "RB") == ["My Back"]
    # Somebody else's pick is off the board but is emphatically not on my roster.
    assert players_in(result["roster"], "WR") == []


# 16. The same pick appearing twice in a payload is counted once.
def test_the_same_pick_twice_is_counted_once():
    duplicated = pick("4034", pick_no=1, roster_id=MY_ROSTER)
    unknown = pick("9999", pick_no=2, first_name="Ghost", last_name="Runner")

    result = ingest_picks(
        [duplicated, dict(duplicated), unknown, dict(unknown)],
        board({"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "My Back"}),
        league(),
    )

    assert players_in(result["roster"], "RB") == ["My Back"]
    assert result["roster"].loc[result["roster"]["slot"] == "RB", "filled"].iloc[0] == 1
    assert len(result["unmatched"]) == 1
    assert result["picks_made"] == 2


# 17. Picks supplied out of order produce the same result as the same picks in order.
def test_picks_out_of_order_give_the_same_result_as_picks_in_order():
    in_order = [
        pick("4034", pick_no=1, roster_id=MY_ROSTER),
        pick("5849", pick_no=2, roster_id=ANOTHER_ROSTER),
        pick("9999", pick_no=3, first_name="Ghost", last_name="Runner"),
        pick("7564", pick_no=4, roster_id=MY_ROSTER),
    ]
    shuffled = [in_order[2], in_order[0], in_order[3], in_order[1]]

    the_board = board(
        {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "My Back"},
        {"player_id": "00-0000002", "sleeper_id": "5849", "player_name": "Their Receiver",
         "position": "WR"},
        {"player_id": "00-0000003", "sleeper_id": "7564", "player_name": "My End",
         "position": "TE"},
    )

    assert_same_result(
        ingest_picks(shuffled, the_board, league()),
        ingest_picks(in_order, the_board, league()),
    )


# 18. The next pick number is correct for a seat in an odd-numbered round.
def test_the_next_pick_is_correct_in_an_odd_round():
    # Round 1 runs 1-14 in seat order, so seat 5 is pick 5 before anything has happened.
    assert ingest_picks([], board(), league(seat=5))["next_pick"] == 5

    # Round 3 is odd too, so the order is seat order again: it runs 29-42 and seat 5 is pick 33.
    made = [pick(str(9000 + n), pick_no=n) for n in range(1, 31)]
    assert ingest_picks(made, board(), league(seat=5))["next_pick"] == 33


# 19. The next pick number is correct for a seat in an even-numbered round, reflecting the snake
#     reversal.
def test_the_next_pick_is_correct_in_an_even_round_and_reverses():
    # Round 2 runs 15-28 backwards, so seat 5 is 14 + (14 - 5 + 1) = pick 24, not pick 19.
    made = [pick(str(9000 + n), pick_no=n) for n in range(1, 21)]
    assert ingest_picks(made, board(), league(seat=5))["next_pick"] == 24

    # Seat 1 is the extreme case: last in round 2, first in round 3, so picks 28 and 29 are
    # back to back and the reversal is the whole reason for it.
    assert ingest_picks(made, board(), league(seat=1))["next_pick"] == 28
    made_through_28 = [pick(str(9000 + n), pick_no=n) for n in range(1, 29)]
    assert ingest_picks(made_through_28, board(), league(seat=1))["next_pick"] == 29

    # Past the last round there is no next pick, and that must not be a number.
    whole_draft = [pick(str(9000 + n), pick_no=n) for n in range(1, TEAM_COUNT * ROUNDS + 1)]
    assert ingest_picks(whole_draft, board(), league(seat=1))["next_pick"] is None


# 28. A roster is reported against the league's actual starting slots, including the superflex
#     slot.
def test_the_roster_is_reported_against_the_leagues_real_starting_slots():
    result = ingest_picks(
        [
            pick("1001", pick_no=1, roster_id=MY_ROSTER),
            pick("1002", pick_no=28, roster_id=MY_ROSTER),
            pick("1003", pick_no=29, roster_id=MY_ROSTER),
            pick("1004", pick_no=56, roster_id=MY_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "sleeper_id": "1001", "player_name": "First QB",
             "position": "QB"},
            {"player_id": "00-0000002", "sleeper_id": "1002", "player_name": "Second QB",
             "position": "QB"},
            {"player_id": "00-0000003", "sleeper_id": "1003", "player_name": "A Back",
             "position": "RB"},
            {"player_id": "00-0000004", "sleeper_id": "1004", "player_name": "A Receiver",
             "position": "WR"},
        ),
        league(),
    )

    expected = pd.DataFrame([
        # The second quarterback lands in the superflex slot, which is the slot that only exists
        # because this league is superflex — in the ESPN league he would be a bench body.
        {"slot": "QB", "starts": 1, "filled": 1, "open": 0, "players": ["First QB"]},
        {"slot": "RB", "starts": 2, "filled": 1, "open": 1, "players": ["A Back"]},
        {"slot": "WR", "starts": 3, "filled": 1, "open": 2, "players": ["A Receiver"]},
        {"slot": "TE", "starts": 1, "filled": 0, "open": 1, "players": []},
        {"slot": "FLEX", "starts": 1, "filled": 0, "open": 1, "players": []},
        {"slot": "SUPER_FLEX", "starts": 1, "filled": 1, "open": 0, "players": ["Second QB"]},
        {"slot": "K", "starts": 1, "filled": 0, "open": 1, "players": []},
        {"slot": "DST", "starts": 1, "filled": 0, "open": 1, "players": []},
        {"slot": "BENCH", "starts": 6, "filled": 0, "open": 6, "players": []},
    ])

    pd.testing.assert_frame_equal(result["roster"], expected)


# The two cases below are not from the ticket's numbered list. They were agreed in conversation
# after the implementation, because the payload defences above exist for a situation Sleeper's own
# API cannot create: it refuses to draft an already-drafted player, so a duplicate never arrives
# from the network. Both duplicates and unnumbered picks arrive from #36's hand-marking, where the
# drafter's own entry is unioned into the same list the API returns. Without these, the two rules
# that handle that are untested for the reason they were written.


# A hand-marked entry carries no pick number: the drafter knows the player is gone, not when.
HAND_MARKED = {"player_id": "4034", "roster_id": MY_ROSTER, "pick_no": None, "metadata": {}}

ONE_BACK = {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "My Back"}


# A. A hand-marked player and his own API pick are one player.
#
# Two claims, because the two ways of getting this wrong fail in opposite directions and a test
# making only one of them would sleep through the other. Keying identity on the pick number drops
# the hand-mark, because its number is None — the player silently returns to the board after the
# drafter has said he is gone. Not deduplicating at all keeps both copies, and he fills two slots.
def test_a_hand_marked_player_is_taken_before_the_api_reports_him():
    result = ingest_picks([HAND_MARKED], board(ONE_BACK), league())

    assert result["taken"] == {"00-0000001"}
    assert players_in(result["roster"], "RB") == ["My Back"]


def test_a_hand_marked_player_and_his_api_pick_are_one_player():
    from_the_api = pick("4034", pick_no=12, roster_id=MY_ROSTER)

    result = ingest_picks([HAND_MARKED, from_the_api], board(ONE_BACK), league())

    # Two RB slots, so a player counted twice would fill both and report the position as done.
    assert players_in(result["roster"], "RB") == ["My Back"]
    assert result["roster"].loc[result["roster"]["slot"] == "RB", "open"].iloc[0] == 1


# B. A hand-marked player does not advance the draft.
def test_a_hand_marked_player_does_not_advance_the_draft():
    api = [pick(str(9000 + n), pick_no=n) for n in range(1, 24)]
    hand_marked = {"player_id": "4034", "roster_id": ANOTHER_ROSTER, "pick_no": None,
                   "metadata": {}}

    result = ingest_picks([*api, hand_marked], board(), league(seat=5))

    # 23 selections have happened. Counting the list would make it 24, and seat 5's turn would
    # read as pick 33 — nine away — at the exact moment the drafter is on the clock at pick 24.
    assert result["picks_made"] == 23
    assert result["next_pick"] == 24
