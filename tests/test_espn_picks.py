"""ESPN's counterpart to `test_draft_picks.py`: the same behaviour, a genuinely simpler payload.

The payload shapes are ESPN's own `draftDetail.picks` entries, not invented:

- A pick keys its player on `playerId`, ESPN's identifier — the string the board now carries in
  `espn_id`, never a name.
- Ownership is `teamId`, straight off the pick — there is no seat/roster split to fall back
  through the way Sleeper's `roster_id`/`draft_slot` pair needs, because ESPN's `pickOrder` already
  lists team IDs directly (see `espn_seat.py`'s docstring). Every real pick carries a `teamId`.
- `overallPickNumber` is the 1-indexed pick count across the whole draft — Sleeper's `pick_no`,
  spelled ESPN's way.
- There is no per-pick name or position anywhere in the payload. An unmatched pick can only be
  reported by its raw `playerId` — ESPN's `mDraftDetail` view carries no metadata a Sleeper pick's
  `metadata.first_name`/`last_name` would.
"""

import pandas as pd

from src.draft.espn_picks import ingest_picks

TEAM_COUNT = 10
ROUNDS = 20

MY_ROSTER = 1
ANOTHER_ROSTER = 7

SLOTS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1,
    "FLEX": 1, "SUPER_FLEX": 1,
    "K": 1, "P": 1, "DST": 1, "BENCH": 9,
}


def board(*rows: dict) -> pd.DataFrame:
    """A board with only the columns ingestion reads, defaulted to one findable running back."""
    defaults = {
        "player_id": "00-0000001",
        "espn_id": "4034",
        "player_name": "A Back",
        "position": "RB",
    }
    return pd.DataFrame(
        [{**defaults, **row} for row in rows],
        columns=list(defaults),
    )


def pick(espn_id: str, overall: int, team_id: int = ANOTHER_ROSTER) -> dict:
    """One pick as ESPN's `draftDetail.picks` hands it back — no name, no position."""
    return {
        "playerId": int(espn_id),
        "teamId": team_id,
        "overallPickNumber": overall,
        "roundId": (overall - 1) // TEAM_COUNT + 1,
    }


def league(**overrides) -> dict:
    return {
        "seat": 1,
        "roster_id": MY_ROSTER,
        "team_count": TEAM_COUNT,
        "rounds": ROUNDS,
        "slots": SLOTS,
        **overrides,
    }


def players_in(roster: pd.DataFrame, slot: str) -> list[str]:
    return roster.loc[roster["slot"] == slot, "players"].iloc[0]


def assert_same_result(left: dict, right: dict) -> None:
    assert left["taken"] == right["taken"]
    assert left["unmatched"] == right["unmatched"]
    assert left["next_pick"] == right["next_pick"]
    assert left["picks_made"] == right["picks_made"]
    pd.testing.assert_frame_equal(left["roster"], right["roster"])


# 1. A pick whose player ID matches nothing is returned in the unmatched list, named as best it
#    can be — which, on ESPN's own payload, is not at all.
def test_a_pick_matching_no_board_row_is_returned_as_unmatched():
    result = ingest_picks(
        [pick("9999", overall=1)],
        board({"espn_id": "4034", "player_name": "A Back"}),
        league(),
    )

    assert result["unmatched"] == [
        {"espn_id": "9999", "player_name": None, "position": None, "pick_no": 1}
    ]


# 2. An unmatched pick removes nobody from the board and leaves the rest of the result intact.
def test_an_unmatched_pick_removes_nobody_and_leaves_the_rest_intact():
    result = ingest_picks(
        [
            pick("9999", overall=1),
            pick("4034", overall=2, team_id=ANOTHER_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "espn_id": "4034", "player_name": "A Back"},
            {"player_id": "00-0000002", "espn_id": "5849", "player_name": "A Receiver",
             "position": "WR"},
        ),
        league(seat=5),
    )

    assert result["taken"] == {"00-0000001"}
    assert len(result["unmatched"]) == 1
    assert result["picks_made"] == 2
    assert result["next_pick"] == 5
    assert result["roster"]["filled"].sum() == 0


# 3. A pick belonging to the drafter's own team ID fills a slot and is also taken off the board.
def test_a_pick_on_the_drafters_team_fills_a_slot_and_is_also_taken():
    result = ingest_picks(
        [
            pick("4034", overall=1, team_id=MY_ROSTER),
            pick("5849", overall=2, team_id=ANOTHER_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "espn_id": "4034", "player_name": "My Back"},
            {"player_id": "00-0000002", "espn_id": "5849", "player_name": "Their Receiver",
             "position": "WR"},
        ),
        league(),
    )

    assert result["taken"] == {"00-0000001", "00-0000002"}
    assert players_in(result["roster"], "RB") == ["My Back"]
    assert players_in(result["roster"], "WR") == []


# 4. The same pick appearing twice in a payload is counted once.
def test_the_same_pick_twice_is_counted_once():
    duplicated = pick("4034", overall=1, team_id=MY_ROSTER)
    unknown = pick("9999", overall=2)

    result = ingest_picks(
        [duplicated, dict(duplicated), unknown, dict(unknown)],
        board({"player_id": "00-0000001", "espn_id": "4034", "player_name": "My Back"}),
        league(),
    )

    assert players_in(result["roster"], "RB") == ["My Back"]
    assert result["roster"].loc[result["roster"]["slot"] == "RB", "filled"].iloc[0] == 1
    assert len(result["unmatched"]) == 1
    assert result["picks_made"] == 2


# 5. Picks supplied out of order produce the same result as the same picks in order.
def test_picks_out_of_order_give_the_same_result_as_picks_in_order():
    in_order = [
        pick("4034", overall=1, team_id=MY_ROSTER),
        pick("5849", overall=2, team_id=ANOTHER_ROSTER),
        pick("9999", overall=3),
        pick("7564", overall=4, team_id=MY_ROSTER),
    ]
    shuffled = [in_order[2], in_order[0], in_order[3], in_order[1]]

    the_board = board(
        {"player_id": "00-0000001", "espn_id": "4034", "player_name": "My Back"},
        {"player_id": "00-0000002", "espn_id": "5849", "player_name": "Their Receiver",
         "position": "WR"},
        {"player_id": "00-0000003", "espn_id": "7564", "player_name": "My End", "position": "TE"},
    )

    assert_same_result(
        ingest_picks(shuffled, the_board, league()),
        ingest_picks(in_order, the_board, league()),
    )


# 6. Snake pick-number arithmetic is unchanged from Sleeper's — reused, not reimplemented.
def test_the_next_pick_reflects_the_snake_reversal():
    # Round 2 of a 10-team league runs 11-20 backwards, so seat 5 is 10 + (10 - 5 + 1) = pick 16.
    made = [pick(str(9000 + n), n) for n in range(1, 11)]
    assert ingest_picks(made, board(), league(seat=5))["next_pick"] == 16


# 7. A roster is reported against the league's actual starting slots, including the superflex slot
#    and the punter this league also starts.
def test_the_roster_is_reported_against_the_leagues_real_starting_slots():
    result = ingest_picks(
        [
            pick("1001", overall=1, team_id=MY_ROSTER),
            pick("1002", overall=11, team_id=MY_ROSTER),
        ],
        board(
            {"player_id": "00-0000001", "espn_id": "1001", "player_name": "First QB",
             "position": "QB"},
            {"player_id": "00-0000002", "espn_id": "1002", "player_name": "Second QB",
             "position": "QB"},
        ),
        league(),
    )

    roster = result["roster"].set_index("slot")
    assert roster.loc["QB", "players"] == ["First QB"]
    # The second quarterback lands in the superflex slot, the same as the Sleeper suite's case 28.
    assert roster.loc["SUPER_FLEX", "players"] == ["Second QB"]
    assert "P" in roster.index and roster.loc["P", "starts"] == 1


# A. A hand-marked player and his own API pick are one player. `marks.as_picks` builds an entry
#    with no `overallPickNumber` for ESPN, the same "gone, but not when" contract as Sleeper's
#    `pick_no: None`.
HAND_MARKED = {"playerId": 4034, "teamId": MY_ROSTER, "overallPickNumber": None}
ONE_BACK = {"player_id": "00-0000001", "espn_id": "4034", "player_name": "My Back"}


def test_a_hand_marked_player_is_taken_before_the_api_reports_him():
    result = ingest_picks([HAND_MARKED], board(ONE_BACK), league())

    assert result["taken"] == {"00-0000001"}
    assert players_in(result["roster"], "RB") == ["My Back"]


def test_a_hand_marked_player_and_his_api_pick_are_one_player():
    from_the_api = pick("4034", overall=12, team_id=MY_ROSTER)

    result = ingest_picks([HAND_MARKED, from_the_api], board(ONE_BACK), league())

    assert players_in(result["roster"], "RB") == ["My Back"]
    assert result["roster"].loc[result["roster"]["slot"] == "RB", "open"].iloc[0] == 1


# B. A hand-marked player does not advance the draft.
def test_a_hand_marked_player_does_not_advance_the_draft():
    api = [pick(str(9000 + n), n) for n in range(1, 6)]
    hand_marked = {"playerId": 4034, "teamId": ANOTHER_ROSTER, "overallPickNumber": None}

    result = ingest_picks([*api, hand_marked], board(), league(seat=6))

    assert result["picks_made"] == 5
    assert result["next_pick"] == 6


# C. A hand-marked player is gone, and is never mine — a mark carries no team ID, so it must not
#    fall through to whoever the drafter happens to be.
UNCLAIMED_HAND_MARK = {"playerId": 4034, "teamId": None, "overallPickNumber": None}


def test_a_hand_marked_player_is_gone_but_never_mine():
    result = ingest_picks([UNCLAIMED_HAND_MARK], board(ONE_BACK), league())

    assert result["taken"] == {"00-0000001"}
    assert result["mine"] == []
    assert players_in(result["roster"], "RB") == []


# D. My own picks are reported in draft order with their positions — what `composition_guidance`
#    reads to judge the opening.
def test_my_own_picks_are_reported_in_draft_order_with_their_positions():
    result = ingest_picks(
        [
            pick("5849", overall=1, team_id=MY_ROSTER),
            pick("4034", overall=2, team_id=ANOTHER_ROSTER),
            pick("6794", overall=3, team_id=MY_ROSTER),
        ],
        board(
            {"player_id": "00-0000002", "espn_id": "5849", "player_name": "My Receiver",
             "position": "WR"},
            {"player_id": "00-0000001", "espn_id": "4034", "player_name": "Their Back"},
            {"player_id": "00-0000003", "espn_id": "6794", "player_name": "My Quarterback",
             "position": "QB"},
        ),
        league(),
    )

    assert [player["player_name"] for player in result["mine"]] == [
        "My Receiver", "My Quarterback"
    ]
    assert [player["position"] for player in result["mine"]] == ["WR", "QB"]
