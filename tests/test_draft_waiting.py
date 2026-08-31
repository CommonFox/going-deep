"""Spec cases 20-24 from issue #37: rank the board by what it costs to pass, not by raw value.

These assert what comes *out* of `rank_by_cost_of_waiting` for a given board, survival frame and
set of picks — never how it gets there. Fixtures are a handful of players with values and
probabilities chosen so the expected number can be worked out on paper, which is the only kind of
expectation that survives a rebuild of the warehouse those inputs really come from.

## The two pick numbers, which are the whole of these cases

Cost of waiting prices a decision at one pick against a fallback at another. The seat has both:
`next_pick` is the turn being decided, `pick_after_next` is the turn a player passed on has to
survive to. Seat 1 of 14 deciding pick 1 is waiting until 28; deciding 28 he is waiting until 29,
which is the immediately following selection and therefore no wait at all.

`draft_availability` holds an **unconditional** probability: P(this player's draft position lands
at or after pick k), computed days ago from his ADP distribution. That is not the number a drafter
wants, because it counts every pick from the top of the draft — including his own, and including
the ones already made. Availability is a monotone event, so the conditional he does want is an
exact ratio of two numbers the table already holds, not a new model:

    p_survives = p(pick after next) / p(next pick + 1)

The denominator starts one past the pick being decided, because that pick is *mine* and I am the
one passing: my own turn cannot be part of the hazard a player has to survive.

Case 20 is the case that pins this down. At the turn the two picks are adjacent, numerator and
denominator are the same pick, the ratio is one, and cost of waiting is zero for everybody — user
story 8, that the tool stop urging a reach when both players can simply be taken. Read off the raw
column instead, De'Von Achane shows a 9% survival probability and a large cost of waiting from a
seat that is about to pick twice in a row.

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

# Seat 1 of 14 picks 1, 28, 29, 56, 57. Most cases below have it deciding pick 29 and waiting
# until 56, so the gap it has to survive opens at pick 30. Cases about the turn use 28 and 29
# instead, where that gap is empty.
NEXT_PICK = 29
WAIT_TO = 56
GAP_STARTS = NEXT_PICK + 1


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
        "pick_after_next": WAIT_TO,
        "picks_made": NEXT_PICK - 1,
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
    # Seat 1 in a 14-team snake picks 28 and 29 back to back. Deciding 28, the turn a passed-over
    # player must survive to is 29 — the immediately following selection, so there is no gap.
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Fragile Back", 118.8),
            player("00-0000002", "Likely Back", 93.7),
            player("00-0000003", "Safe Receiver", 85.3, position="WR"),
        ),
        # Real numbers off the sleeper board at pick 29. Unconditionally the first of these is 91%
        # gone; conditional on his surviving the picks *other* people make in between — of which
        # there are none — he is certain. A ranking built on the raw column fails here.
        survival(*at(29, ("00-0000001", 0.086), ("00-0000002", 0.500), ("00-0000003", 0.917))),
        picks(next_pick=28, pick_after_next=29, picks_made=27),
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
        *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(WAIT_TO, ("00-0000001", 0.1), ("00-0000002", 0.9)),
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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0),
                     ("00-0000003", 1.0), ("00-0000004", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.2), ("00-0000002", 1.0),
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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 0.75)),
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
        *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 1.0)),
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
    # Ranked by value, among everyone whose waiting costs nothing rather than beneath the lot of
    # them: the rookie outvalues the mid back, who is certain to last and therefore also free to
    # wait on, so he sorts above him. Only the priced back, who costs something, is ahead of both.
    assert names(result) == [
        "Priced Back", "Unpriced Rookie", "Mid Back", "Unpriced Camp Body",
    ]

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
        picks(next_pick=195, pick_after_next=196, picks_made=194),
    )

    assert result["degraded"] is True
    # Named, because "degraded" on its own does not tell a drafter what the tool still covers.
    assert result["covers_to"] == 180
    assert result["candidates"]["p_survives"].isna().all()
    assert result["candidates"]["cost_of_waiting"].isna().all()
    assert names(result) == ["Best Left", "Second Left", "Third Left"]


# The cases below were not numbered in the spec. They cover the acceptance criteria the numbered
# list leaves implicit, and the two judgement calls the spec did not reach.


def test_the_probability_is_anchored_to_the_pick_it_waits_to():
    """Change the pick waited to, change the number: nothing here is a per-player constant."""
    the_board = board(player("00-0000001", "A Back", 100.0))
    frame = survival(
        *at(GAP_STARTS, ("00-0000001", 1.0)),
        *at(40, ("00-0000001", 0.8)),
        *at(WAIT_TO, ("00-0000001", 0.3)),
    )

    near = rank_by_cost_of_waiting(the_board, frame, picks(pick_after_next=40))
    far = rank_by_cost_of_waiting(the_board, frame, picks(pick_after_next=WAIT_TO))

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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0),
                     ("00-0000003", 1.0), ("00-0000004", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 1.0),
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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            # No row for the faller at my pick; the other player still has one, so the frame
            # covers that pick and the absence is about him rather than about the table.
            *at(WAIT_TO, ("00-0000002", 1.0)),
        ),
        picks(),
    )

    faller = row_for(result, "Certain Faller")
    assert faller["survival_known"] == True  # noqa: E712 — the flag itself is the claim
    assert faller["p_survives"] == 0.0
    # Certain to be gone, so waiting costs the whole drop to the next back: 100 - 40.
    assert faller["cost_of_waiting"] == pytest.approx(60.0)


def test_a_player_the_table_already_had_gone_is_gone_rather_than_unknown():
    """The deep end of the rule above: the table wrote him off before the gap even opened.

    A player with no row at the pick the gap opens on is one the table had gone *already* — and
    yet here he sits on the board. The conditional's denominator is missing, which reads at first
    like nothing to divide by. It is not. The warehouse computed a zero for him and then dropped
    the row for falling under its own floor, which is the same thing it does on the numerator side
    and means the same thing here: gone.

    Reading that absence as a gap is what put Jalen Hurts 219th of 947 in a live mock while he sat
    available at 2.14 with the second-highest value on the board (#70). So the answer is the one
    the numerator already gives, and waiting on him costs the whole drop to whoever is behind him.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Long Faller", 100.0),
            player("00-0000002", "Next Back", 40.0),
        ),
        survival(
            # One row far back at pick 1 and nothing after it: the shape the warehouse leaves for
            # a player whose whole distribution sits ahead of the picks being decided.
            ("00-0000001", 1, 1.0),
            *at(GAP_STARTS, ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000002", 1.0)),
        ),
        picks(),
    )

    faller = row_for(result, "Long Faller")
    assert faller["survival_known"] == True  # noqa: E712 — the flag itself is the claim
    assert faller["p_survives"] == 0.0
    # Certain to be gone, so waiting costs the whole drop to the next back: 100 - 40.
    assert faller["cost_of_waiting"] == pytest.approx(60.0)
    assert result["degraded"] is False


def test_the_most_valuable_faller_leads_the_one_list():
    """The regression for #70, stated the way a drafter would have seen it go wrong.

    There is one list, and the best pick on the board is at the top of it. A faller ranked behind
    every player the model happens to cover is a faller nobody reads, because the screen is cut to
    fifteen rows and he was 218th.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Long Faller", 100.0),
            player("00-0000002", "Next Back", 40.0),
            player("00-0000003", "Priced Receiver", 90.0, position="WR"),
            player("00-0000004", "Backup Receiver", 30.0, position="WR"),
        ),
        survival(
            ("00-0000001", 1, 1.0),
            *at(
                GAP_STARTS,
                ("00-0000002", 1.0), ("00-0000003", 1.0), ("00-0000004", 1.0),
            ),
            *at(
                WAIT_TO,
                ("00-0000002", 1.0), ("00-0000003", 0.5), ("00-0000004", 1.0),
            ),
        ),
        picks(),
    )

    # The faller costs the full 100 - 40; the receiver, half likely to last, costs 90 - 60.
    assert names(result)[0] == "Long Faller"
    assert row_for(result, "Long Faller")["cost_of_waiting"] == pytest.approx(60.0)
    assert row_for(result, "Priced Receiver")["cost_of_waiting"] == pytest.approx(30.0)


def test_a_faller_certain_to_be_gone_is_nobody_elses_fallback():
    """Zero means gone, and a man who is gone is not the man you fall back to.

    Three backs in value order with the faller in the middle, so the claim is about the player
    *above* him: he must be priced against the third back, because falling back onto somebody who
    is certainly gone is not falling back at all. This is the half of the semantics a flag on its
    own cannot state — `p_survives` of 0 has to flow through the expectation, not just the column.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Top Back", 100.0),
            player("00-0000002", "Long Faller", 80.0),
            player("00-0000003", "Third Back", 20.0),
        ),
        survival(
            ("00-0000002", 1, 1.0),
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000003", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.0), ("00-0000003", 1.0)),
        ),
        picks(),
    )

    # Expected best available walks up from the bottom: the third back is certain at 20, the
    # faller is gone so the expectation past him is still 20, and the top back — gone himself —
    # is priced against that 20 rather than against the faller's 80.
    assert row_for(result, "Third Back")["cost_of_waiting"] == pytest.approx(0.0)
    assert row_for(result, "Long Faller")["cost_of_waiting"] == pytest.approx(60.0)
    assert row_for(result, "Top Back")["cost_of_waiting"] == pytest.approx(80.0)


def test_at_the_turn_even_a_player_the_model_never_covered_is_certain_to_last():
    """An empty gap is certainty for everybody, and that is arithmetic rather than a model.

    Deciding pick 28 and picking again at 29, nobody else selects in between, so there is no
    hazard for anyone to survive — whether the table has an opinion about him or not. Case 20
    already pins this for players the frame covers; the two it does not cover are the ones that
    used to come back `NaN` and sink to the bottom of a list where nothing costs anything.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Priced Back", 90.0),
            player("00-0000002", "Long Faller", 120.0),
            player("00-0000003", "Unpriced Rookie", 60.0, position="WR"),
        ),
        # Only the first of the three is in the frame at all.
        survival(*at(29, ("00-0000001", 0.086))),
        picks(next_pick=28, pick_after_next=29, picks_made=27),
    )

    assert list(result["candidates"]["p_survives"]) == [1.0, 1.0, 1.0]
    assert list(result["candidates"]["cost_of_waiting"]) == [0.0, 0.0, 0.0]
    assert list(result["candidates"]["survival_known"]) == [True, True, True]
    # Nothing costs anything, so the list falls through to value — with the faller leading it
    # rather than buried under the one player the table happens to price.
    assert names(result) == ["Long Faller", "Priced Back", "Unpriced Rookie"]
    assert result["degraded"] is False


# 71. A missing cost of waiting is an unknown urgency, not a known lack of value. The board carries
#     728 players with no ADP at all, and `na_position="last"` sorted every one of them below every
#     player the survival model happens to price.
def test_a_player_with_no_cost_sorts_by_value_rather_than_below_everyone():
    """No ADP source lists him, so nothing in the market is about to take him.

    That silence is a statement about *urgency* — wait on him, he will keep — and not a statement
    about worth. Sorting it as "less than everybody" quietly turned the one into the other.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Priced Back", 90.0),
            player("00-0000002", "Unpriced Rookie", 200.0, position="WR"),
        ),
        survival(
            *at(GAP_STARTS, ("00-0000001", 1.0)),
            *at(WAIT_TO, ("00-0000001", 1.0)),
        ),
        picks(),
    )

    # Both are free to wait on. Between two players who cost nothing, value decides.
    assert names(result) == ["Unpriced Rookie", "Priced Back"]

    # Placed, not priced: the row still says it has no number, because it has none.
    rookie = row_for(result, "Unpriced Rookie")
    assert rookie["survival_known"] == False  # noqa: E712 — the flag itself is the claim
    assert pd.isna(rookie["p_survives"])
    assert pd.isna(rookie["cost_of_waiting"])


def test_an_unknown_urgency_never_outranks_a_known_one():
    """The boundary that keeps this from swallowing the faller fix.

    A player who costs something to pass on is ahead of a player who costs nothing, however much
    more valuable the second one is. Otherwise a board with no ADP coverage would bury exactly the
    players cost of waiting exists to surface.
    """
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Urgent Back", 40.0),
            player("00-0000002", "Cheap Back", 10.0),
            player("00-0000003", "Unpriced Star", 200.0, position="WR"),
        ),
        survival(
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.0), ("00-0000002", 1.0)),
        ),
        picks(),
    )

    # The urgent back costs 40 - 10 to pass on; the star costs nothing anybody can name.
    assert names(result) == ["Urgent Back", "Unpriced Star", "Cheap Back"]
    assert row_for(result, "Urgent Back")["cost_of_waiting"] == pytest.approx(30.0)


def test_players_without_survival_data_are_ordered_by_value_then_name():
    """Among themselves, the same two keys as everybody else, in the same order."""
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Anchor Back", 70.0),
            player("00-0000002", "Unpriced Top", 100.0, position="TE"),
            player("00-0000003", "Unpriced Zeta", 50.0, position="WR"),
            player("00-0000004", "Unpriced Alpha", 50.0, position="WR"),
        ),
        survival(
            *at(GAP_STARTS, ("00-0000001", 1.0)),
            *at(WAIT_TO, ("00-0000001", 1.0)),
        ),
        picks(),
    )

    # Everything costs nothing, so this is value order throughout, with the two equal receivers
    # broken alphabetically the way every other tie on this board is.
    assert names(result) == [
        "Unpriced Top", "Anchor Back", "Unpriced Alpha", "Unpriced Zeta",
    ]


def test_a_finished_draft_degrades_rather_than_raising():
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Best Left", 40.0),
            player("00-0000002", "Second Left", 30.0, position="WR"),
        ),
        survival(*at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0))),
        picks(next_pick=None, pick_after_next=None, picks_made=210),
    )

    assert result["degraded"] is True
    assert names(result) == ["Best Left", "Second Left"]


def test_the_last_round_has_nothing_to_wait_for_either():
    """A seat's final pick has no later turn, so there is no gap and no cost to price."""
    result = rank_by_cost_of_waiting(
        board(
            player("00-0000001", "Best Left", 40.0),
            player("00-0000002", "Second Left", 30.0, position="WR"),
        ),
        survival(*at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0))),
        picks(next_pick=197, pick_after_next=None, picks_made=196),
    )

    assert result["degraded"] is True
    assert result["candidates"]["cost_of_waiting"].isna().all()
    assert names(result) == ["Best Left", "Second Left"]


def test_a_fully_drafted_board_returns_no_candidates_without_raising():
    the_board = board(
        player("00-0000001", "A Back", 150.0),
        player("00-0000002", "A Receiver", 120.0, position="WR"),
    )

    result = rank_by_cost_of_waiting(
        the_board,
        survival(*at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 0.5))),
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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 0.5)),
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
            *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 0.5)),
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
        *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0), ("00-0000003", 1.0)),
        *at(WAIT_TO, ("00-0000001", 0.2), ("00-0000002", 0.9), ("00-0000003", 0.4)),
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
        *at(GAP_STARTS, ("00-0000001", 1.0), ("00-0000002", 1.0)),
        *at(WAIT_TO, ("00-0000001", 0.5), ("00-0000002", 0.5)),
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
    """The denominator starts one past my own turn, not at it.

    The pick being decided is mine, and I am the one passing, so it cannot be part of the hazard a
    player has to survive — counting it would charge me for the chance that I take him myself. An
    off-by-one here is invisible: every number stays in range and the ordering barely moves, which
    is the worst shape a draft-night error can have.
    """
    the_board = board(player("00-0000001", "A Back", 100.0))
    frame = survival(
        ("00-0000001", NEXT_PICK, 0.5),
        ("00-0000001", GAP_STARTS, 0.4),
        ("00-0000001", WAIT_TO, 0.2),
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
            *at(GAP_STARTS, ("00-0000001", 0.3), ("00-0000002", 1.0)),
            *at(WAIT_TO, ("00-0000001", 0.30000000000000004), ("00-0000002", 0.0)),
        ),
        picks(),
    )

    probabilities = result["candidates"]["p_survives"]
    assert probabilities.between(0.0, 1.0).all()
    assert not np.isinf(probabilities).any()
