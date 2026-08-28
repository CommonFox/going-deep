"""Spec cases 10, 14, 15, 25, 26 and 27 from the live draft assistant's ranking ticket.

These assert what comes *out* of `rank_candidates` for a given board and set of taken players —
never how it gets there. Fixtures are a handful of players with values chosen so the expected
order can be worked out by eye, which is the only kind of expectation that survives a rebuild.

The function takes the set of taken player IDs rather than the picks payload or the whole
ingestion result. The payload has already been dealt with by `ingest_picks`, and a second
function that re-read it would be a second place for duplicate and unmatched picks to be
handled differently. One test at the bottom runs the two halves together, so the claim that
they actually join on `player_id` is checked rather than assumed.
"""

import pandas as pd

from src.draft.candidates import rank_candidates
from src.draft.picks import ingest_picks

CANDIDATE_COLUMNS = ["player_id", "player_name", "position", "team", "points_over_replacement"]


def board(*rows: dict) -> pd.DataFrame:
    """A board with the columns ranking reads, plus one it must leave behind."""
    defaults = {
        "player_id": "00-0000001",
        "player_name": "A Back",
        "position": "RB",
        "team": "SF",
        "points_over_replacement": 100.0,
        # The real board carries thirty-odd columns. One of them is here so that "narrowed to
        # the candidate columns" is a claim a test can fail rather than a description of a
        # fixture that only ever had five columns to begin with.
        "consensus_adp": 24.5,
    }
    return pd.DataFrame(
        [{**defaults, **row} for row in rows],
        # Named explicitly for the same reason the ingestion suite names them: a bare
        # DataFrame([]) has no columns at all, which is not a shape the warehouse hands back.
        columns=list(defaults),
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


def names(candidates: pd.DataFrame) -> list[str]:
    """The candidate names in the order they came back."""
    return list(candidates["player_name"])


# 10. A player present in the picks payload never appears among the candidates.
def test_a_taken_player_never_appears_among_the_candidates():
    result = rank_candidates(
        board(
            player("00-0000001", "Taken Back", 150.0),
            player("00-0000002", "Free Back", 120.0),
            player("00-0000003", "Free Receiver", 90.0, position="WR"),
        ),
        taken={"00-0000001"},
    )

    assert names(result) == ["Free Back", "Free Receiver"]
    # The whole feature exists to prevent a drafted player being shown as available, so the
    # claim is his absence by ID, not merely that the list got shorter.
    assert "00-0000001" not in set(result["player_id"])


# 14. An empty payload returns the full board, ranked.
def test_an_empty_payload_returns_the_whole_board_ranked():
    result = rank_candidates(
        board(
            player("00-0000001", "Third Best", 90.0),
            player("00-0000002", "Best", 150.0),
            player("00-0000003", "Second Best", 120.0),
        ),
        taken=set(),
    )

    # Every row survives, and the board's own order is not what comes back — value order is.
    assert names(result) == ["Best", "Second Best", "Third Best"]
    assert list(result.columns) == CANDIDATE_COLUMNS


# 15. A payload in which every player has been taken returns no candidates and does not raise.
def test_a_fully_drafted_board_returns_no_candidates_without_raising():
    the_board = board(
        player("00-0000001", "A Back", 150.0),
        player("00-0000002", "A Receiver", 120.0, position="WR"),
    )

    result = rank_candidates(the_board, taken={"00-0000001", "00-0000002"})

    assert len(result) == 0
    # An empty frame with no columns would break the renderer at the end of a draft, which is
    # exactly when nobody wants to find out. The shape has to survive the emptiness.
    assert list(result.columns) == CANDIDATE_COLUMNS


# 25. Points over replacement is returned unchanged from the board and is never recomputed or
#     rescaled.
def test_points_over_replacement_comes_back_exactly_as_the_board_priced_it():
    the_board = board(
        player("00-0000001", "Gone One", 200.0),
        player("00-0000002", "Gone Two", 180.0),
        player("00-0000003", "Gone Three", 160.0),
        player("00-0000004", "Still Here", 140.0),
        # A negative value: replacement level is a real player, so someone ranked below him
        # prices out under zero. A rescale or a floor-at-zero would quietly eat this.
        player("00-0000005", "Below Replacement", -12.5, position="WR"),
    )

    # Most of the board is gone, which is the condition under which a recompute against the
    # remaining players would move every surviving number. Under this rule none of them move.
    result = rank_candidates(
        the_board, taken={"00-0000001", "00-0000002", "00-0000003"}
    )

    assert list(result["points_over_replacement"]) == [140.0, -12.5]


# 26. Neither input frame is mutated.
def test_the_inputs_are_left_exactly_as_they_were_handed_in():
    the_board = board(
        player("00-0000001", "A Back", 150.0),
        player("00-0000002", "A Receiver", 120.0, position="WR"),
    )
    board_before = the_board.copy(deep=True)
    taken = {"00-0000001"}
    taken_before = set(taken)

    rank_candidates(the_board, taken=taken)

    # The board is rebuilt days before the draft and is read by every other surface. Sorting or
    # filtering it in place here would silently change what those surfaces see.
    pd.testing.assert_frame_equal(the_board, board_before)
    assert taken == taken_before


# 27. Filtering to a single position returns only that position, ranked by the same rule.
def test_filtering_to_one_position_returns_only_that_position_ranked_the_same_way():
    the_board = board(
        player("00-0000001", "Best Receiver", 150.0, position="WR"),
        player("00-0000002", "Best Back", 190.0),
        player("00-0000003", "Second Receiver", 130.0, position="WR"),
        player("00-0000004", "Taken Receiver", 170.0, position="WR"),
        player("00-0000005", "A Quarterback", 210.0, position="QB"),
    )

    result = rank_candidates(the_board, taken={"00-0000004"}, position="WR")

    # Same two rules as the unfiltered ranking: taken players are gone, the rest are in value
    # order. The higher-valued back and quarterback are excluded for position alone.
    assert names(result) == ["Best Receiver", "Second Receiver"]
    assert set(result["position"]) == {"WR"}


# The case below is not from the ticket's numbered list. Ties were left undecided by the spec
# and were agreed in conversation: order them by name, so that the ordering is stable across
# runs and a reader can see from the equal values that it *is* a tie rather than a judgement.
def test_players_of_equal_value_are_ordered_by_name():
    result = rank_candidates(
        board(
            player("00-0000001", "Zeta Back", 120.0),
            player("00-0000002", "Alpha Back", 120.0),
            player("00-0000003", "Mid Back", 120.0),
            player("00-0000004", "Clear Best", 200.0),
        ),
        taken=set(),
    )

    assert names(result) == ["Clear Best", "Alpha Back", "Mid Back", "Zeta Back"]
    # The tie is visible in the output rather than resolved out of sight.
    assert list(result["points_over_replacement"])[1:] == [120.0, 120.0, 120.0]


# Also agreed in conversation rather than numbered: the two halves of the seam are written and
# tested separately, so the one thing neither suite checks is that they join at all. They meet
# on the board's `player_id`, and nothing else in either signature says so.
def test_the_ingestion_result_feeds_the_ranking():
    the_board = pd.DataFrame([
        {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "Drafted Back",
         "position": "RB", "team": "SF", "points_over_replacement": 150.0},
        {"player_id": "00-0000002", "sleeper_id": "5849", "player_name": "Free Receiver",
         "position": "WR", "team": "KC", "points_over_replacement": 120.0},
    ])
    payload = [{
        "player_id": "4034", "roster_id": 7, "pick_no": 1,
        "metadata": {"first_name": "Drafted", "last_name": "Back", "position": "RB"},
    }]

    ingested = ingest_picks(payload, the_board, {
        "seat": 1, "roster_id": 1, "team_count": 14, "rounds": 15,
        "slots": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1,
                  "K": 1, "DST": 1, "BENCH": 6},
    })

    assert names(rank_candidates(the_board, taken=ingested["taken"])) == ["Free Receiver"]
