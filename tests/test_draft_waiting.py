"""Spec cases 20-24 from issue #37: rank the board by what it costs to pass, not by raw value.

These assert what comes *out* of `rank_by_cost_of_waiting` for a given board, survival frame and
set of picks — never how it gets there. Fixtures are a handful of players with values and
probabilities chosen so the expected number can be worked out on paper, which is the only kind of
expectation that survives a rebuild of the warehouse those inputs really come from.

## The one thing worth reading before the cases

`draft_availability` holds an **unconditional** probability: P(this player's draft position lands
at or after pick k), computed days ago from his ADP distribution. That is not the number a drafter
wants. He is looking at a board on which the player is *demonstrably still there*, so the question
is P(survives to my next pick **given** he is here now).

Availability is a monotone event — once a player is gone he stays gone — so that conditional is an
exact ratio of two numbers the table already holds, not a new model:

    p_survives = p(next pick) / p(the pick about to be made)

Case 20 is the case that pins this down. On the clock with back-to-back picks, the two picks are
the same pick, the ratio is 1, and cost of waiting is zero for everybody — which is the whole point
of user story 8: at the turn the tool must stop urging a reach when both players can simply be
taken. Read against the raw unconditional number instead, De'Von Achane shows a 12% survival
probability and a large cost of waiting at the exact moment the drafter is already on the clock.

## Vocabulary

`p_survives`, never `p_available`. The glossary already spends *availability* on the share of a
season a player can play, and the board carries a column with that meaning. Renaming the warehouse
table is out of scope; using its word for a different thing anywhere the drafter can see is not.
"""

import numpy as np
import pandas as pd
import pytest

from src.draft.candidates import CANDIDATE_COLUMNS
from src.draft.waiting import WAITING_COLUMNS, rank_by_cost_of_waiting

# The pick about to be made is `picks_made + 1`, and the conditional ratio's denominator is read
# there. Most cases below sit a seat mid-round: 29 picks gone, pick 30 on the clock, my next turn
# at 56. Cases that need the turn set them equal instead.
PICKS_MADE = 29
NOW = PICKS_MADE + 1
NEXT_PICK = 56


def board(*rows: dict) -> pd.DataFrame:
    """A board with the columns ranking reads, plus one it must leave behind."""
    defaults = {
        "player_id": "00-0000001",
        "player_name": "A Back",
        "position": "RB",
        "team": "SF",
        "points_over_replacement": 100.0,
        "consensus_adp": 24.5,
    }
    return pd.DataFrame(
        [{**defaults, **row} for row in rows], columns=list(defaults)
    )


def player(player_id: str, name: str, por: float, position: str = "RB", **rest) -> dict:
    """One board row, in the order the arguments read on the page."""
    return {
        "player_id": player_id,
        "player_name": name,
        "position": position,
        "points_over_replacement": por,
        **rest,
    }


def survival(*rows: tuple) -> pd.DataFrame:
    """The survival frame, as `(player_id, overall_pick, p_survives)` triples.

    Shaped like what the warehouse hands over: one row per player per pick, and *no row at all*
    where the player's probability has fallen under the table's own floor or past the latest pick
    he has ever actually lasted to. Absence is therefore data, not a gap — which is why two of the
    cases below are about telling one from the other.
    """
    return pd.DataFrame(
        list(rows), columns=["player_id", "overall_pick", "p_survives"]
    ).astype({"player_id": "object", "overall_pick": "int64", "p_survives": "float64"})


def at(pick: int, *pairs: tuple) -> list[tuple]:
    """Every player's probability at one pick, as rows for `survival`."""
    return [(player_id, pick, probability) for player_id, probability in pairs]


def picks(**overrides) -> dict:
    """What `ingest_picks` returns — only the three keys the ranking actually reads."""
    return {
        "taken": set(),
        "roster": pd.DataFrame(),
        "unmatched": [],
        "next_pick": NEXT_PICK,
        "picks_made": PICKS_MADE,
        **overrides,
    }


def names(result: dict) -> list[str]:
    """The candidate names in the order they came back."""
    return list(result["candidates"]["player_name"])


def row_for(result: dict, name: str) -> pd.Series:
    """One candidate's row, so a claim is about that player alone."""
    matched = result["candidates"].loc[result["candidates"]["player_name"] == name]
    assert len(matched) == 1, f"expected exactly one row for {name!r}, got {len(matched)}"
    return matched.iloc[0]


# 20. At the turn, where the next pick is the immediately following selection, every survival
#     probability is at its maximum and cost of waiting is therefore near zero for all candidates.
def test_at_the_turn_nothing_costs_anything_to_wait_for():
    # Seat 1 in a 14-team snake picks 28 and 29 back to back. On the clock at 28 with 27 gone,
    # the pick about to be made *is* my next pick, so there is no gap to survive.
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Fragile Back", 118.8),
            player("00-0000002", "Likely Back", 93.7),
            player("00-0000003", "Safe Receiver", 85.3, position="WR"),
        ),
        # Real numbers off the sleeper board at pick 28. Unconditionally the first of these is 88%
        # gone; conditional on him sitting in front of me right now he is certain to last a gap of
        # no picks at all. A ranking built on the raw column fails here, which is the point.
        survival(*at(28, ("00-0000001", 0.122), ("00-0000002", 0.562), ("00-0000003", 0.934))),
        picks(picks_made=27, next_pick=28),
    )

    assert list(result["candidates"]["p_survives"]) == [1.0, 1.0, 1.0]
    assert list(result["candidates"]["cost_of_waiting"]) == [0.0, 0.0, 0.0]
    assert result["degraded"] is False


def test_a_gap_of_real_picks_does_cost_something():
    """The other half of case 20: the collapse is the turn's doing, not the function's."""
    the_board = board(
        player("00-0000001", "Fragile Back", 118.8),
        player("00-0000002", "Likely Back", 93.7),
    )
    frame = survival(
        *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(NEXT_PICK, ("00-0000001", 0.1), ("00-0000002", 0.9)),
    )

    result = rank_by_cost_of_waiting(the_board, frame, picks())

    assert row_for(result, "Fragile Back")["cost_of_waiting"] > 0
    assert row_for(result, "Likely Back")["cost_of_waiting"] > 0


# 21. Given two players with equal points over replacement, the one less likely to survive to the
#     next pick ranks higher.
def test_of_two_equally_valuable_players_the_one_less_likely_to_last_ranks_higher():
    # Two positions built to be structurally identical — same top value, same backup value, same
    # backup probability — so the survival probability is the only thing that differs.
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Fragile Quarterback", 100.0, position="QB"),
            player("00-0000002", "Backup Quarterback", 50.0, position="QB"),
            player("00-0000003", "Safe Tight End", 100.0, position="TE"),
            player("00-0000004", "Backup Tight End", 50.0, position="TE"),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0),
                     ("00-0000003", 1.0), ("00-0000004", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.2), ("00-0000002", 1.0),
                           ("00-0000003", 0.8), ("00-0000004", 1.0)),
        ),
        picks(),
    )

    ranked = names(result)
    assert ranked.index("Fragile Quarterback") < ranked.index("Safe Tight End")
    # Equal value is the premise, so it has to be true of what came back rather than of the fixture.
    assert (
        row_for(result, "Fragile Quarterback")["points_over_replacement"]
        == row_for(result, "Safe Tight End")["points_over_replacement"]
    )
    # 100 - [0.2 x 100 + 0.8 x 50] against 100 - [0.8 x 100 + 0.2 x 50], worked out by hand.
    assert row_for(result, "Fragile Quarterback")["cost_of_waiting"] == pytest.approx(40.0)
    assert row_for(result, "Safe Tight End")["cost_of_waiting"] == pytest.approx(10.0)


# 22. Given two players with equal cost of waiting, the higher points over replacement ranks
#     higher.
def test_of_two_players_costing_the_same_to_wait_for_the_more_valuable_ranks_higher():
    # One player at each position, so cost of waiting is just value x P(gone): 100 x 0.5 and
    # 200 x 0.25 both come to 50.
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Lesser Back", 100.0),
            player("00-0000002", "Greater Receiver", 200.0, position="WR"),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 0.75)),
        ),
        picks(),
    )

    assert row_for(result, "Lesser Back")["cost_of_waiting"] == pytest.approx(50.0)
    assert row_for(result, "Greater Receiver")["cost_of_waiting"] == pytest.approx(50.0)
    assert names(result) == ["Greater Receiver", "Lesser Back"]


# 23. A player with no row in the survival frame is ranked by points over replacement and flagged
#     as lacking survival data, and his missing probability is never treated as a number.
def test_a_player_with_no_survival_data_is_ranked_by_value_and_flagged():
    priced = [
        player("00-0000001", "Priced Back", 120.0),
        player("00-0000002", "Mid Back", 60.0),
    ]
    frame = survival(
        *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 1.0)),
    )
    # Two of them, so "ranked by value" is an ordering that can be checked rather than a position
    # of one. The rookie outvalues everyone on the board and still cannot be given a cost.
    unpriced = [
        player("00-0000003", "Unpriced Rookie", 200.0, position="WR"),
        player("00-0000004", "Unpriced Camp Body", 30.0, position="WR"),
    ]

    result = rank_by_cost_of_waiting(board(*priced, *unpriced), frame, picks())

    rookie = row_for(result, "Unpriced Rookie")
    assert rookie["survival_known"] == False  # noqa: E712 — the flag itself is the claim
    assert pd.isna(rookie["p_survives"])
    assert pd.isna(rookie["cost_of_waiting"])
    # Ranked by value: after everyone who has a cost of waiting, and among themselves by value.
    assert names(result)[-2:] == ["Unpriced Rookie", "Unpriced Camp Body"]

    # The strong form of "never treated as a number": a missing probability is not silently a 0 or
    # a 1, so the players who *do* have one price identically whether or not he is on the board.
    without = rank_by_cost_of_waiting(board(*priced), frame, picks())
    assert row_for(result, "Priced Back")["cost_of_waiting"] == pytest.approx(
        row_for(without, "Priced Back")["cost_of_waiting"]
    )


# 24. A next pick number beyond the survival frame's coverage produces a result flagged as degraded
#     to value ranking.
def test_a_pick_past_the_survival_model_degrades_to_value_ranking():
    # The sleeper table stops at overall pick 180 against a 210-pick draft, so the last two rounds
    # genuinely fall off the end of it. This is that, in miniature.
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Best Left", 40.0),
            player("00-0000002", "Second Left", 30.0, position="WR"),
            player("00-0000003", "Third Left", 20.0, position="TE"),
        ),
        survival(
            *at(168, ("00-0000001", 0.9), ("00-0000002", 0.8), ("00-0000003", 0.7)),
            *at(180, ("00-0000001", 0.5), ("00-0000002", 0.4), ("00-0000003", 0.3)),
        ),
        picks(picks_made=195, next_pick=196),
    )

    assert result["degraded"] is True
    # Named, because "degraded" on its own does not tell a drafter what the tool still covers.
    assert result["covers_to"] == 180
    assert result["candidates"]["p_survives"].isna().all()
    assert result["candidates"]["cost_of_waiting"].isna().all()
    assert names(result) == ["Best Left", "Second Left", "Third Left"]


# The cases below were not numbered in the spec. They cover the acceptance criteria the numbered
# list leaves implicit, and the two judgement calls the spec did not reach.


def test_the_probability_is_anchored_to_the_stated_next_pick():
    """Change the pick, change the number: nothing here is a per-player constant."""
    the_board = board(player("00-0000001", "A Back", 100.0))
    frame = survival(
        *at(NOW, ("00-0000001", 1.0)),
        *at(40, ("00-0000001", 0.8)),
        *at(NEXT_PICK, ("00-0000001", 0.3)),
    )

    near = rank_by_cost_of_waiting(the_board, frame, picks(next_pick=40))
    far = rank_by_cost_of_waiting(the_board, frame, picks(next_pick=NEXT_PICK))

    assert row_for(near, "A Back")["p_survives"] == pytest.approx(0.8)
    assert row_for(far, "A Back")["p_survives"] == pytest.approx(0.3)
    assert row_for(far, "A Back")["cost_of_waiting"] > row_for(near, "A Back")["cost_of_waiting"]


def test_a_cliff_costs_more_to_wait_on_than_a_deep_position():
    """The headline claim of the ticket, and the reason value alone is not enough.

    Two players with identical value and identical survival probability. The one whose position
    falls off a cliff behind him is expensive to pass on; the one with a near-equal player behind
    him is cheap. Nothing about the players themselves distinguishes them.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Cliff Tight End", 100.0, position="TE"),
            player("00-0000002", "Cliff Backup", 10.0, position="TE"),
            player("00-0000003", "Deep Back", 100.0, position="RB"),
            player("00-0000004", "Deep Backup", 90.0, position="RB"),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0),
                     ("00-0000003", 1.0), ("00-0000004", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 1.0),
                           ("00-0000003", 0.5), ("00-0000004", 1.0)),
        ),
        picks(),
    )

    assert row_for(result, "Cliff Tight End")["cost_of_waiting"] == pytest.approx(45.0)
    assert row_for(result, "Deep Back")["cost_of_waiting"] == pytest.approx(5.0)
    assert names(result)[0] == "Cliff Tight End"


def test_a_player_the_table_drops_at_my_pick_is_gone_rather_than_unknown():
    """Absence at one pick is a probability of zero, not missing data.

    The warehouse drops a row once the probability falls under its own floor, and clips it to zero
    past the latest pick the player has ever actually lasted to. Both mean *gone*. Reading that as
    "no data" would flag the most certain players on the board as the least certain.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Certain Faller", 100.0),
            player("00-0000002", "Next Back", 40.0),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            # No row for the faller at my pick; the other player still has one, so the frame
            # covers that pick and the absence is about him rather than about the table.
            *at(NEXT_PICK, ("00-0000002", 1.0)),
        ),
        picks(),
    )

    faller = row_for(result, "Certain Faller")
    assert faller["survival_known"] == True  # noqa: E712 — the flag itself is the claim
    assert faller["p_survives"] == 0.0
    # Certain to be gone, so waiting costs the whole drop to the next back: 100 - 40.
    assert faller["cost_of_waiting"] == pytest.approx(60.0)


def test_a_player_the_table_already_had_gone_gets_no_probability_at_all():
    """The other side of that: the model has been overtaken by events, so it has nothing to say.

    A player with no row at the pick about to be made is one the table says is *already* gone —
    yet here he sits on the board. The conditional has a zero denominator, and there is no honest
    number to put in its place, so he is flagged like anyone else the model does not cover.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Long Faller", 100.0),
            player("00-0000002", "Priced Back", 40.0),
        ),
        survival(
            ("00-0000001", 1, 1.0),
            *at(NOW, ("00-0000002", 1.0)),
            *at(NEXT_PICK, ("00-0000002", 0.5)),
        ),
        picks(),
    )

    faller = row_for(result, "Long Faller")
    assert faller["survival_known"] == False  # noqa: E712 — the flag itself is the claim
    assert pd.isna(faller["p_survives"])
    assert pd.isna(faller["cost_of_waiting"])
    assert result["degraded"] is False


def test_a_finished_draft_degrades_rather_than_raising():
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Best Left", 40.0),
            player("00-0000002", "Second Left", 30.0, position="WR"),
        ),
        survival(*at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0))),
        picks(next_pick=None, picks_made=210),
    )

    assert result["degraded"] is True
    assert names(result) == ["Best Left", "Second Left"]


def test_a_fully_drafted_board_returns_no_candidates_without_raising():
    the_board = board(
        player("00-0000001", "A Back", 150.0),
        player("00-0000002", "A Receiver", 120.0, position="WR"),
    )

    result = rank_by_cost_of_waiting(
        the_board,
        survival(*at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 0.5))),
        picks(taken={"00-0000001", "00-0000002"}),
    )

    assert len(result["candidates"]) == 0
    # An empty frame with no columns would break the renderer at the end of a draft, which is
    # exactly when nobody wants to find out. The shape has to survive the emptiness.
    assert list(result["candidates"].columns) == WAITING_COLUMNS


def test_a_taken_player_never_appears_among_the_candidates():
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Taken Back", 150.0),
            player("00-0000002", "Free Back", 120.0),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 0.5)),
        ),
        picks(taken={"00-0000001"}),
    )

    assert names(result) == ["Free Back"]
    assert "00-0000001" not in set(result["candidates"]["player_id"])
    # A player already drafted is not a fallback either — the cost of passing on the free back is
    # measured against nobody, not against the man who has gone.
    assert row_for(result, "Free Back")["cost_of_waiting"] == pytest.approx(60.0)


def test_points_over_replacement_comes_back_exactly_as_the_board_priced_it():
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Still Here", 140.0),
            # Replacement level is a real player, so someone ranked below him prices out under
            # zero. A rescale or a floor-at-zero would quietly eat this.
            player("00-0000002", "Below Replacement", -12.5, position="WR"),
        ),
        survival(
            *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 0.5)),
        ),
        picks(),
    )

    assert list(result["candidates"]["points_over_replacement"]) == [140.0, -12.5]
    assert list(result["candidates"].columns) == WAITING_COLUMNS
    assert WAITING_COLUMNS[: len(CANDIDATE_COLUMNS)] == CANDIDATE_COLUMNS


def test_filtering_to_one_position_leaves_every_cost_of_waiting_unchanged():
    """A display filter, and nothing more.

    Cost of waiting is measured against the players who would still be there at my next pick,
    which is a fact about the whole board. Narrowing the screen to quarterbacks must not reprice
    the quarterbacks as though the rest of the board had been drafted.
    """
    the_board = board(
        player("00-0000001", "A Quarterback", 150.0, position="QB"),
        player("00-0000002", "Another Quarterback", 90.0, position="QB"),
        player("00-0000003", "A Back", 200.0),
    )
    frame = survival(
        *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0), ("00-0000003", 1.0)),
        *at(NEXT_PICK, ("00-0000001", 0.2), ("00-0000002", 0.9), ("00-0000003", 0.4)),
    )

    everyone = rank_by_cost_of_waiting(the_board, frame, picks())
    quarterbacks = rank_by_cost_of_waiting(the_board, frame, picks(), position="QB")

    assert names(quarterbacks) == ["A Quarterback", "Another Quarterback"]
    assert set(quarterbacks["candidates"]["position"]) == {"QB"}
    for name in ["A Quarterback", "Another Quarterback"]:
        assert row_for(quarterbacks, name)["cost_of_waiting"] == pytest.approx(
            row_for(everyone, name)["cost_of_waiting"]
        )


def test_the_inputs_are_left_exactly_as_they_were_handed_in():
    the_board = board(
        player("00-0000001", "A Back", 150.0),
        player("00-0000002", "A Receiver", 120.0, position="WR"),
    )
    frame = survival(
        *at(NOW, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(NEXT_PICK, ("00-0000001", 0.5), ("00-0000002", 0.5)),
    )
    board_before = the_board.copy(deep=True)
    frame_before = frame.copy(deep=True)
    the_picks = picks(taken={"00-0000001"})
    taken_before = set(the_picks["taken"])

    rank_by_cost_of_waiting(the_board, frame, the_picks)

    # Both frames are read by every other surface on the screen. Sorting or filtering either in
    # place here would silently change what those surfaces see.
    pd.testing.assert_frame_equal(the_board, board_before)
    pd.testing.assert_frame_equal(frame, frame_before)
    assert the_picks["taken"] == taken_before


def test_a_probability_is_never_read_off_the_wrong_pick():
    """The denominator is the pick about to be made, not the last one completed.

    An off-by-one here is invisible: every number stays in range and the ordering barely moves,
    which is the worst shape a draft-night error can have.
    """
    the_board = board(player("00-0000001", "A Back", 100.0))
    frame = survival(
        ("00-0000001", PICKS_MADE, 0.5),
        ("00-0000001", NOW, 0.4),
        ("00-0000001", NEXT_PICK, 0.2),
    )

    result = rank_by_cost_of_waiting(the_board, frame, picks())

    # 0.2 / 0.4, not 0.2 / 0.5.
    assert row_for(result, "A Back")["p_survives"] == pytest.approx(0.5)


def test_a_probability_never_comes_back_above_one_or_below_zero():
    """Belt and braces on the ratio, since a probability out of range would print as one."""
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "A Back", 100.0),
            player("00-0000002", "A Receiver", 80.0, position="WR"),
        ),
        survival(
            # A ratio a hair over one, which floating point reaches on its own.
            *at(NOW, ("00-0000001", 0.3), ("00-0000002", 1.0)),
            *at(NEXT_PICK, ("00-0000001", 0.30000000000000004), ("00-0000002", 0.0)),
        ),
        picks(),
    )

    probabilities = result["candidates"]["p_survives"]
    assert probabilities.between(0.0, 1.0).all()
    assert not np.isinf(probabilities).any()
