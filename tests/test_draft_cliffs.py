"""Issue #29, user story 18: telling a thin position from a deep one, at a glance.

Cost of waiting already prices the drop behind *one player* — that is half of what it multiplies.
What it cannot show is the shape of a whole position, because it hands back one number per player
and the drafter reading the top of the list sees only the players who happen to rank highest
across every position at once. "Are there six more backs like this one, or is this the last of
them" is a question the ranking answers nowhere.

## What a cliff is here, since the ticket did not say

The largest drop in points over replacement between two consecutive players still available at a
position, looked for among the next few rather than the whole tail. The count above it is the
answer: three left before it gets materially worse, or eleven.

Two decisions inside that are worth stating, because they are the ones these cases pin down.

**The drop below the last player at a position is his own points over replacement.** Not a special
case bolted on — points over replacement is measured against a freely available player, so a
fallback below the last player at a position is replacement level, which is zero by construction.
`waiting._cost_within_position` already floors its expectation at zero for exactly this reason.
It makes the last startable quarterback the biggest cliff on the board, which is correct.

**A cliff is only looked for among the next few.** A twenty-point step between the fortieth and
forty-first receiver is not a fact about this pick, and reporting the largest drop in the whole
remaining tail would find it and say "39 left" — a true number that answers nothing. The lookahead
is a parameter here so these cases can state the boundary rather than assume it.
"""

import pandas as pd

from src.draft.cliff import position_cliffs


def remaining(*rows: tuple) -> pd.DataFrame:
    """Who is left, as the ranking hands them over: a position and a price, in value order."""
    return pd.DataFrame(
        [
            {
                "player_id": f"00-000000{index}",
                "player_name": f"Player {index}",
                "position": position,
                "team": "SF",
                "points_over_replacement": value,
            }
            for index, (position, value) in enumerate(rows, start=1)
        ],
        columns=["player_id", "player_name", "position", "team", "points_over_replacement"],
    )


def row_for(cliffs: pd.DataFrame, position: str) -> dict:
    """The one row about one position, so a claim is about that position alone."""
    matches = cliffs.loc[cliffs["position"] == position]
    assert len(matches) == 1, f"expected one row for {position}, got {len(matches)}"
    return matches.iloc[0].to_dict()


# 1. The whole of the story: how many are left before the drop, and how big the drop is.
def test_the_cliff_is_the_largest_drop_and_the_count_is_who_is_above_it():
    cliffs = position_cliffs(
        remaining(("RB", 100.0), ("RB", 95.0), ("RB", 90.0), ("RB", 40.0), ("RB", 38.0)),
        lookahead=5,
    )
    back = row_for(cliffs, "RB")

    assert back["above"] == 3
    assert back["drop"] == 50.0


# 2. The reading the block exists for. Same count above the cliff, opposite meaning: one position
# falls off a shelf and the other slopes, and only the size of the drop says which.
def test_a_deep_position_and_a_thin_one_are_told_apart_by_the_size_of_the_drop():
    cliffs = position_cliffs(
        remaining(
            ("RB", 100.0), ("RB", 99.0), ("RB", 98.0), ("RB", 90.0), ("RB", 89.0),
            ("WR", 100.0), ("WR", 99.0), ("WR", 98.0), ("WR", 40.0), ("WR", 39.0),
        ),
        lookahead=4,
    )

    assert row_for(cliffs, "RB")["above"] == row_for(cliffs, "WR")["above"] == 3
    assert row_for(cliffs, "WR")["drop"] > row_for(cliffs, "RB")["drop"]


# 3. The last one at a position is the biggest cliff there is, and falls out of the same rule
# rather than a special case: below him is replacement level, which is zero by construction.
def test_the_last_player_at_a_position_is_a_cliff_to_replacement_level():
    cliffs = position_cliffs(remaining(("TE", 41.0)), lookahead=5)
    end = row_for(cliffs, "TE")

    assert end["above"] == 1
    assert end["drop"] == 41.0


# 4. The boundary the lookahead exists for. The largest drop in the whole tail is at the far end;
# reporting it would say "4 left" about a position whose next real step down is two players away.
def test_a_cliff_past_the_lookahead_is_not_the_one_reported():
    cliffs = position_cliffs(
        remaining(("WR", 100.0), ("WR", 80.0), ("WR", 78.0), ("WR", 76.0), ("WR", 10.0)),
        lookahead=3,
    )
    wideout = row_for(cliffs, "WR")

    assert wideout["above"] == 1
    assert wideout["drop"] == 20.0


# 5. Two equal drops is a real board, not a contrived one — the near one is the one being decided.
def test_equal_drops_report_the_nearer_cliff():
    cliffs = position_cliffs(
        remaining(("RB", 100.0), ("RB", 80.0), ("RB", 60.0), ("RB", 55.0), ("RB", 50.0)),
        lookahead=2,
    )

    assert row_for(cliffs, "RB")["above"] == 1


# 6. Depth is a fact about who is left, so a drafted player is not part of it. Driven through the
# frame the ranking actually hands over rather than asserted about a set.
def test_a_player_already_taken_is_not_part_of_his_position_s_depth():
    board = remaining(("QB", 150.0), ("QB", 60.0), ("QB", 55.0))
    without_the_best = position_cliffs(board.iloc[1:], lookahead=5)

    assert row_for(without_the_best, "QB")["above"] == 2
    assert row_for(without_the_best, "QB")["drop"] == 55.0


# 7. Every position, not only the interesting ones: "how deep is tight end" is a question asked by
# a drafter who has not been shown a reason to ask it.
def test_every_position_still_on_the_board_gets_a_row():
    cliffs = position_cliffs(
        remaining(("QB", 90.0), ("RB", 80.0), ("WR", 70.0), ("TE", 60.0)), lookahead=5
    )

    assert set(cliffs["position"]) == {"QB", "RB", "WR", "TE"}


# 8. A position nobody is left at is not depth information, and a zero in a column of counts reads
# as one. It is absent, and the screen says nothing about it.
def test_a_position_with_nobody_left_is_not_reported():
    assert position_cliffs(remaining(), lookahead=5).empty


# 9. How many are left at all, beside how many are above the cliff — "3 of 4" and "3 of 40" are
# different boards, and the count above the cliff is the same in both.
def test_the_number_left_at_the_position_is_reported_beside_the_count():
    cliffs = position_cliffs(
        remaining(("RB", 100.0), ("RB", 40.0), ("RB", 38.0), ("RB", 36.0)), lookahead=5
    )

    assert row_for(cliffs, "RB")["remaining"] == 4


# 10. Read by every other surface on the screen: sorting or cutting it here would silently change
# what the ranking, the roster and the guidance are computed from.
def test_the_frame_handed_in_is_not_modified():
    frame = remaining(("RB", 100.0), ("RB", 40.0), ("WR", 90.0))
    before = frame.copy()

    position_cliffs(frame, lookahead=5)

    pd.testing.assert_frame_equal(frame, before)
