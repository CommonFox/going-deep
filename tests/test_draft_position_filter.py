"""Issue #29, user story 17: "who is the best quarterback left", answered directly.

The pure ranking has taken a `position` argument since cost of waiting was written, and the case
that narrowing does not reprice anything is already covered in `test_draft_waiting`. What was
missing is any way for a drafter to reach it. The tool refreshes until Ctrl-C, so a flag fixed at
startup is not a filter anybody can use at pick eleven, and restarting the tool mid-draft is the
one thing the loop exists to avoid.

So the filter is typed at the running tool, down the same channel a hand-mark is typed at. That
makes the only real decision here one of precedence: a line is read as a position first and as a
name second. The cases below pin that down, because the two ways of getting it wrong are not
symmetric.

Reading a name as a position cannot happen — a position token is short, fixed, and has to be one
the board actually carries. Reading a position as a name is what would happen without the
precedence, and it costs nothing only because a one- or two-letter mark matches half the board and
would be refused anyway.

What is not harmless is a filter that changes the screen without saying so. A drafter who typed
`qb`, got a narrowed board, and did not notice would read three quarterbacks as the whole of what
is left. So there is no silent branch here either: every outcome carries a line to print, the same
rule `marks` is written to.
"""

import pandas as pd

from src.draft.filter import read_position

# One player at each position the board carries, plus the collision the precedence rule exists
# for: `Kyle Pitts` contains a `k`, so a bare `k` is both a substring of a name and the kicker
# position. Nobody types one letter meaning a player, and the screen says which reading it took.
BOARD = pd.DataFrame(
    [
        {"player_id": "00-0000001", "sleeper_id": "4984", "player_name": "Josh Allen",
         "position": "QB", "team": "BUF"},
        {"player_id": "00-0000002", "sleeper_id": "8138", "player_name": "Bijan Robinson",
         "position": "RB", "team": "ATL"},
        {"player_id": "00-0000003", "sleeper_id": "6794", "player_name": "Justin Jefferson",
         "position": "WR", "team": "MIN"},
        {"player_id": "00-0000004", "sleeper_id": "7553", "player_name": "Kyle Pitts",
         "position": "TE", "team": "ATL"},
        {"player_id": "00-0000005", "sleeper_id": "9224", "player_name": "Brandon Aubrey",
         "position": "K", "team": "DAL"},
    ]
)


# 1. The whole of the story: a position typed at the tool narrows the board to it.
def test_a_typed_position_narrows_the_board_to_it():
    outcome = read_position("QB", BOARD)

    assert outcome["action"] == "show"
    assert outcome["position"] == "QB"


# 2. Nobody types in capitals under a pick clock, and the board's own spelling is the one that has
# to match downstream — so the case is normalized here rather than by the drafter.
def test_a_position_is_recognised_whatever_case_it_is_typed_in():
    for typed in ("qb", "QB", "Qb", " qb "):
        outcome = read_position(typed, BOARD)
        assert outcome is not None, f"{typed!r} was not read as a position"
        assert outcome["position"] == "QB", f"{typed!r} resolved to {outcome['position']!r}"


# 3. A narrowed board that did not announce itself is three quarterbacks read as the whole board.
def test_narrowing_says_on_screen_which_position_is_being_shown():
    assert "QB" in read_position("qb", BOARD)["message"]


# 4. The way back. Without it the only route to the full board is restarting the tool, which is
# the thing the refresh loop exists to make unnecessary.
def test_all_puts_the_whole_board_back():
    outcome = read_position("all", BOARD, position="QB")

    assert outcome["action"] == "clear"
    assert outcome["position"] is None
    assert outcome["message"]


# 5. The precedence rule, stated as the thing it must not break: a name is still a mark. This is
# the case that keeps the filter from eating the safety-critical channel it shares.
def test_a_name_is_not_a_filter_command():
    assert read_position("bijan", BOARD) is None
    assert read_position("kyle pitts", BOARD) is None
    assert read_position("-bijan", BOARD) is None


# 6. The other half of precedence: a position token wins even though it is also a substring of a
# player's name. Safe in this direction only, because a one-letter mark is ambiguous and would be
# refused; and visible either way, because the message says which reading was taken.
def test_a_position_wins_over_a_name_it_is_a_substring_of():
    outcome = read_position("k", BOARD)

    assert outcome is not None and outcome["position"] == "K"
    assert "K" in outcome["message"]


# 7. A position no player on this board plays is not a filter — it falls through to be read as a
# name, where an unknown one is already refused by name and said out loud.
def test_a_position_the_board_does_not_carry_is_not_a_filter_command():
    assert read_position("p", BOARD) is None
    assert read_position("dst", BOARD) is None


# 8. No silent branch: clearing a board that is not narrowed still prints something, so a drafter
# who types `all` twice is told the second one did nothing rather than left wondering.
def test_clearing_a_board_that_is_not_narrowed_says_so():
    outcome = read_position("all", BOARD, position=None)

    assert outcome["action"] == "none"
    assert outcome["message"]


# 9. And the same going the other way, for the same reason.
def test_narrowing_to_the_position_already_shown_says_so():
    outcome = read_position("qb", BOARD, position="QB")

    assert outcome["action"] == "none"
    assert outcome["message"]
