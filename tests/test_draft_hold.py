"""Issue #58: keeping kickers and defenses off the board until there is nothing else to take.

The first live mock surfaced defenses in round 7 and kickers in round 8, and the ranking was not
wrong to do it. A starting kicker genuinely clears replacement level — there is one K slot and
fourteen of them get rostered, so the drop from K1 to K15 is arithmetic rather than opinion.

What the ranking is missing is a term, not a correction. Cost of waiting prices the drop behind
*one player* weighted by *that player's* chance of going; it has no way to say that the whole
position will still be sitting there in round 14 while the receiver next to it will not. So the
answer is not to reprice anything — it is to stop asking the question until the answer matters.

## Why the round is derived rather than typed

"The last two rounds" is the right answer for this league and is the wrong shape of answer. It is
right because fifteen rounds with one K slot and one DST slot leaves exactly two picks that have
to be spent on them; change either number and the sentence stops being true while the constant
stays. So the reserve is read off `league["slots"]` — one round per slot the league actually
starts — and the last-two-rounds answer falls out of it here rather than being asserted.

`draft_plans` was checked first, as the ticket asked, and has nothing to say: every plan in the
table is a five-pick opening of QB/RB/WR/TE, and the simulation never takes a kicker at all.

## Why a hard filter, and what stops it costing a pick

A soft penalty keeps a kicker on screen at a price nobody can audit, which is the thing this repo
keeps saying it will not do. A filter is readable: he is not there, and the screen says why.

What makes that safe is that the escape hatch already exists. Typing `k` at the running tool
narrows the board to kickers, and the hold lifts when the drafter has asked for the position by
name — so this is a position off the *default* board, never a position the tool has lost. The note
says so, in the same breath as saying they are being held.

## The punter is the same problem wearing the ESPN league's colours

One slot, thirty on the board, replacement level flat behind the first few. `seat.SLOT_SETTINGS`
already carries `slots_p` "so that a league which does start one is described rather than silently
short a slot", and this follows it: the held set is `LATE_POSITIONS` intersected with the slots the
league starts. The Sleeper league starts no punter, so P is never held and never named there.
"""

import pandas as pd
from pandas.testing import assert_frame_equal

from src.draft.hold import held_positions, withhold
from src.draft.live import screen

# One K slot, one DST slot, no punter: the Sleeper league, whose fifteen rounds and two late slots
# are what makes "the last two rounds" the right answer here and a bad constant everywhere else.
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": SLOTS}


def league(**overrides) -> dict:
    """The league shape `resolve_seat` reads out of a draft record, with one thing changed."""
    return {**LEAGUE, **overrides}


def turn(next_pick: int | None) -> dict:
    """What `ingest_picks` returns, cut to the one key this decision reads.

    The pick being *decided* is what the hold is keyed on, not how far the draft has got: two
    seats in the same round are deciding picks fourteen apart and must see the same board.
    """
    return {"next_pick": next_pick}


def remaining(*rows: tuple) -> pd.DataFrame:
    """Who is left, as either block hands them over — a position and a price, in order."""
    return pd.DataFrame(
        [
            {
                "player_id": f"00-000000{index}",
                "player_name": f"Player {index}",
                "position": position,
                "points_over_replacement": value,
            }
            for index, (position, value) in enumerate(rows, start=1)
        ],
        columns=["player_id", "player_name", "position", "points_over_replacement"],
    )


# --- Which rounds they are held for -----------------------------------------------------------

# 1. The whole of the ticket: in the rounds a real player is still available, they are not offered.
def test_kickers_and_defenses_are_held_off_the_board_early_in_the_draft():
    hold = held_positions(turn(85), LEAGUE)

    assert hold["positions"] == ["K", "DST"]


# 2. The round they come back is one per late slot the league starts, counted from the end. In this
# league that is round 14 of 15 — the ticket's answer, arrived at rather than typed.
def test_they_come_back_with_one_round_left_for_each_late_slot_the_league_starts():
    hold = held_positions(turn(85), LEAGUE)

    assert hold["from_round"] == 14


# 3. And the reserve is genuinely read off the league: a second defense costs a third round.
def test_a_league_starting_two_defenses_gets_them_back_a_round_earlier():
    hold = held_positions(turn(85), league(slots={**SLOTS, "DST": 2}))

    assert hold["from_round"] == 13


# 4. The boundary, held side. The last round before the reserve still shows a full board.
def test_the_round_before_the_reserve_still_holds_them():
    hold = held_positions(turn(169), LEAGUE)

    assert hold["positions"] == ["K", "DST"]


# 5. The boundary, released side. Seat 1's round-14 turn is pick 196, and nothing is held from it.
def test_the_first_round_of_the_reserve_releases_them():
    hold = held_positions(turn(196), LEAGUE)

    assert hold["positions"] == []
    assert hold["from_round"] is None


# 6. Counted from the end rather than fixed at 14: a longer draft holds them for longer.
def test_a_longer_draft_holds_them_a_round_longer():
    hold = held_positions(turn(196), league(rounds=16))

    assert hold["positions"] == ["K", "DST"]
    assert hold["from_round"] == 15


# 7. The punter, which the ESPN league starts and this one does not. Both halves of the rule in one
# case: a started late slot is held and counts toward the reserve.
def test_a_league_that_starts_a_punter_holds_him_too_and_reserves_a_round_for_him():
    espn = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "P": 1, "DST": 1}
    hold = held_positions(turn(85), league(team_count=10, slots=espn))

    assert hold["positions"] == ["K", "DST", "P"]
    assert hold["from_round"] == 13


# 8. And the other half: a slot the league does not start is never named. Same rule the roster
# block already follows, which never shows the one-quarterback league an empty superflex row.
def test_a_position_the_league_does_not_start_is_never_held_or_named():
    hold = held_positions(turn(85), LEAGUE)

    assert "P" not in hold["positions"]


# 9. Nothing to reserve, nothing to hold — rather than a position held for a whole draft.
def test_a_league_starting_none_of_them_holds_nothing():
    hold = held_positions(turn(85), league(slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1}))

    assert hold["positions"] == []
    assert hold["from_round"] is None


# --- What lifts the hold ------------------------------------------------------------------------

# 10. The escape hatch that makes a hard filter safe rather than lossy. A drafter who typed `k` has
# asked the question this module exists to stop asking on his behalf, and gets an answer.
def test_narrowing_to_a_held_position_shows_it():
    hold = held_positions(turn(85), LEAGUE, position="K")

    assert hold["positions"] == []


# 11. Narrowing elsewhere lifts nothing. This is not idle: the depth block covers every position
# whatever the board is narrowed to, so a hold that lapsed here would put K back on screen beside
# a list of backs — arguing for the round-7 kicker from the one block that still could.
def test_narrowing_to_another_position_leaves_the_hold_on():
    hold = held_positions(turn(85), LEAGUE, position="RB")

    assert hold["positions"] == ["K", "DST"]


# 12. A finished draft has nothing to recommend, so it has nothing to withhold — and a note about
# a round that will not happen is worse than no note.
def test_a_finished_draft_holds_nothing():
    hold = held_positions(turn(None), LEAGUE)

    assert hold["positions"] == []
    assert hold["from_round"] is None


# --- Applying it ----------------------------------------------------------------------------

# 13. The subtraction itself, and the promise that it is only a subtraction: what is left is in the
# order it arrived in, at the prices it arrived with.
def test_held_positions_come_off_the_frame_and_everyone_else_keeps_their_order():
    frame = remaining(("RB", 150.0), ("DST", 36.0), ("WR", 120.0), ("K", 22.0), ("TE", 90.0))

    kept = withhold(frame, held_positions(turn(85), LEAGUE))

    assert list(kept["position"]) == ["RB", "WR", "TE"]
    assert list(kept["points_over_replacement"]) == [150.0, 120.0, 90.0]


# 14. Holding nothing changes nothing. The last two rounds are the rounds this tool was written
# for, and the board it draws in them is the board it would have drawn without this module.
def test_holding_nothing_leaves_the_frame_exactly_as_it_was():
    frame = remaining(("RB", 150.0), ("DST", 36.0), ("K", 22.0))

    kept = withhold(frame, held_positions(turn(196), LEAGUE))

    assert_frame_equal(kept, frame)


# 15. One rule applied twice. The candidate list and the depth block are different frames about the
# same board, and a position held out of one and reported by the other is a screen at odds with
# itself: "K — 3 above a 12.0 drop" is the round-7 kicker argument, made where nothing answers it.
def test_the_same_rule_applies_to_the_depth_block_as_to_the_candidate_list():
    cliffs = pd.DataFrame(
        [
            {"position": "DST", "above": 2, "drop": 16.3, "remaining": 32},
            {"position": "K", "above": 3, "drop": 12.0, "remaining": 45},
            {"position": "RB", "above": 2, "drop": 24.0, "remaining": 61},
        ]
    )

    kept = withhold(cliffs, held_positions(turn(85), LEAGUE))

    assert list(kept["position"]) == ["RB"]


# --- The whole way through ---------------------------------------------------------------------
#
# The three cases above are about one decision each; these are about the screen a drafter is
# actually looking at, drawn through the real pipeline against a hand-written board. The hold is
# applied in two places by two different frames, and a rule that held one and not the other would
# pass every case above.

BOARD = pd.DataFrame(
    [
        {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "Alpha Back",
         "position": "RB", "team": "SF", "points_over_replacement": 150.0, "bye_week": 9},
        {"player_id": "00-0000002", "sleeper_id": "6786", "player_name": "Bravo Wideout",
         "position": "WR", "team": "KC", "points_over_replacement": 120.0, "bye_week": 6},
        {"player_id": "00-0000003", "sleeper_id": "8146", "player_name": "Charlie Wall",
         "position": "DST", "team": "SEA", "points_over_replacement": 36.0, "bye_week": 12},
        {"player_id": "00-0000004", "sleeper_id": "9221", "player_name": "Delta Boot",
         "position": "K", "team": "DAL", "points_over_replacement": 22.0, "bye_week": 7},
        {"player_id": "00-0000005", "sleeper_id": "8112", "player_name": "Echo Seam",
         "position": "TE", "team": "BUF", "points_over_replacement": 90.0, "bye_week": 5},
        # The pick that puts the draft where a case needs it. A back rather than the only tight end,
        # so that advancing the draft does not empty a position the depth cases are asserting about.
        {"player_id": "00-0000006", "sleeper_id": "9999", "player_name": "Foxtrot Gone",
         "position": "RB", "team": "NYJ", "points_over_replacement": 40.0, "bye_week": 11},
    ]
)

CONTEXT = {
    "board": BOARD,
    "survival": pd.DataFrame(columns=["player_id", "overall_pick", "p_survives"]),
    "plans": pd.DataFrame(
        columns=["draft_slot", "plan", "trials", "points_vs_field", "win_rate"]
    ),
    "draft": {"draft_id": "1"},
    "league": LEAGUE,
}


def drawn(picks_made: int, position: str | None = None) -> str:
    """The screen at a given point in the draft, with one pick made to put it there."""
    payload = [{"pick_no": picks_made, "player_id": "9999", "roster_id": 3, "metadata": {}}]
    return screen(CONTEXT, payload, position=position)


def depth_positions(out: str) -> list[str]:
    """The positions the depth block reports, which must agree with the list below it."""
    lines = out.splitlines()
    start = lines.index(next(line for line in lines if line.startswith("Depth")))
    rows = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        if line.strip():
            rows.append(line.split()[0])
    return rows


# 16. Round 7 of 15 — where the mock put a defense on the board, and where a startable receiver is
# still there to lose by taking one.
def test_in_the_middle_rounds_the_board_offers_neither_a_kicker_nor_a_defense():
    out = drawn(84)

    assert "Alpha Back" in out
    assert "Charlie Wall" not in out
    assert "Delta Boot" not in out


# 17. And the depth block agrees with it, because it is the one other place on screen that could
# still argue for the pick the list has stopped offering.
def test_the_depth_block_stops_reporting_them_too():
    assert depth_positions(drawn(84)) == ["RB", "WR", "TE"]


# 18. Round 14, and both are back — with two picks left and two slots to fill, they are the pick.
def test_in_the_last_rounds_both_come_back():
    out = drawn(169)

    assert "Charlie Wall" in out
    assert "Delta Boot" in out
    assert depth_positions(out) == ["RB", "WR", "TE", "K", "DST"]


# 19. The escape hatch, end to end: a drafter who wants a kicker in round 7 types `k` and gets one.
def test_asking_for_kickers_by_name_shows_them_whatever_round_it_is():
    out = drawn(84, position="K")

    assert "Delta Boot" in out


# 20. And asking for something else does not smuggle them back in through the depth block.
def test_narrowing_to_another_position_keeps_them_off_the_depth_block():
    assert depth_positions(drawn(84, position="RB")) == ["RB", "WR", "TE"]
