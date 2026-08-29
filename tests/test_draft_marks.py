"""Issue #36: the drafter says a player is gone, and the board believes him.

This is the one place in the feature where a player is identified by *name*. Everywhere else the
rule is identity by ID, and it holds here too either side of one step: a typed string is resolved
against the board once, and what comes out is a board row carrying its own IDs. Nothing downstream
ever sees the string.

That one step is where the whole risk sits, so the cases below are mostly about refusing:

- A name matching nobody, and a name matching several, must both come back rejected and named.
  Marking the wrong player hides somebody who is genuinely available, which is the mirror image of
  the failure this feature exists to prevent — and it is invisible, because a board that is missing
  a player looks exactly like a board where somebody took him.
- A refusal has to say so out loud. The drafter is typing at a status line that repaints every
  three seconds; a mark that quietly did nothing would be read as a mark that worked.

The last two cases go through `ingest_picks` rather than inspecting what `as_picks` builds. What
matters about a hand-mark is what the board does with it — the player gone, the draft not advanced,
and one player rather than two once Sleeper catches up — and that is a claim about the union, not
about the shape of a dict.
"""

import pandas as pd

from src.draft.marks import as_picks, read_mark
from src.draft.picks import ingest_picks

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": SLOTS}

# Names chosen for the ways a typed one can go wrong: three players sharing a surname, two whose
# punctuation nobody types under a pick clock, a pair where one full name is a prefix of the other,
# and the rookie kicker the crosswalk missed.
BOARD = pd.DataFrame(
    [
        {"player_id": "00-0000001", "sleeper_id": "4984", "player_name": "Josh Allen",
         "position": "QB", "team": "BUF"},
        {"player_id": "00-0000002", "sleeper_id": "4033", "player_name": "Keenan Allen",
         "position": "WR", "team": "CHI"},
        {"player_id": "00-0000003", "sleeper_id": "9509", "player_name": "Braelon Allen",
         "position": "RB", "team": "NYJ"},
        {"player_id": "00-0000004", "sleeper_id": "8112", "player_name": "Amon-Ra St. Brown",
         "position": "WR", "team": "DET"},
        {"player_id": "00-0000005", "sleeper_id": "6790", "player_name": "D'Andre Swift",
         "position": "RB", "team": "CHI"},
        {"player_id": "00-0000006", "sleeper_id": "7002", "player_name": "Michael Carter",
         "position": "RB", "team": "ARI"},
        {"player_id": "00-0000007", "sleeper_id": "7003", "player_name": "Michael Carter II",
         "position": "RB", "team": "NYJ"},
        # The residue issue #31 reports at build time: on the board, priced, and unresolvable.
        {"player_id": "00-0000008", "sleeper_id": None, "player_name": "Rookie Kicker",
         "position": "K", "team": "LV"},
    ]
)


def marked(*names: str) -> dict:
    """The hand-marked set, keyed the way the running tool keys it: by the board's player ID."""
    rows = BOARD.loc[BOARD["player_name"].isin(names)]
    return {row["player_id"]: dict(row) for _, row in rows.iterrows()}


# 1. The plain case: a name typed in full marks that player, and what comes back is a board row.
def test_a_full_name_marks_that_player():
    outcome = read_mark("Josh Allen", BOARD, {})

    assert outcome["action"] == "mark"
    assert outcome["player"]["player_id"] == "00-0000001"
    assert outcome["player"]["sleeper_id"] == "4984"
    assert "Josh Allen" in outcome["message"]


# 2. Typed at a pick clock, on a line that is repainting under the cursor.
def test_case_and_spacing_do_not_matter():
    outcome = read_mark("  josh   ALLEN  ", BOARD, {})

    assert outcome["action"] == "mark"
    assert outcome["player"]["player_name"] == "Josh Allen"


# 3. Nobody types the apostrophe, the period or the hyphen.
def test_punctuation_in_the_board_name_need_not_be_typed():
    assert read_mark("dandre swift", BOARD, {})["player"]["player_name"] == "D'Andre Swift"
    assert read_mark("amon ra st brown", BOARD, {})["player"]["player_name"] == "Amon-Ra St. Brown"


# 4. Part of a name is enough when only one player has it, which is what makes this typeable.
def test_part_of_a_name_marks_the_one_player_who_has_it():
    assert read_mark("swift", BOARD, {})["player"]["player_name"] == "D'Andre Swift"
    assert read_mark("keenan", BOARD, {})["player"]["player_name"] == "Keenan Allen"


# 5. The failure that costs a pick: a player hidden who is actually there. It must be refused,
# and refused loudly enough to be read on a repainting line.
def test_a_name_matching_nobody_is_refused_and_marks_nobody():
    outcome = read_mark("jsoh allen", BOARD, {})

    assert outcome["action"] == "none"
    assert outcome["player"] is None
    assert "jsoh allen" in outcome["message"]


# 6. The same failure by a different route. A surname three players share must never resolve to
# whichever of them the board happened to list first — and the message has to name them, because
# the only thing the drafter can do about it is type one of them.
def test_a_name_matching_several_players_is_refused_and_names_them():
    outcome = read_mark("allen", BOARD, {})

    assert outcome["action"] == "none"
    assert outcome["player"] is None
    for name in ("Josh Allen", "Keenan Allen", "Braelon Allen"):
        assert name in outcome["message"]


# 7. "Michael Carter" is both a whole name and the start of another one. Treating that as ambiguous
# would make the shorter name permanently untypeable, so the exact match wins.
def test_a_whole_name_wins_over_a_longer_name_it_is_part_of():
    outcome = read_mark("michael carter", BOARD, {})

    assert outcome["action"] == "mark"
    assert outcome["player"]["player_name"] == "Michael Carter"


# 8. A board row with no Sleeper ID cannot be told apart from his own pick when it arrives, so a
# mark on him would be a mark that silently did nothing. Refused, saying which player and why.
def test_a_player_with_no_sleeper_id_is_refused_rather_than_marked():
    outcome = read_mark("rookie kicker", BOARD, {})

    assert outcome["action"] == "none"
    assert outcome["player"] is None
    assert "Rookie Kicker" in outcome["message"]
    assert "Sleeper ID" in outcome["message"]


# 9. Typing the same name twice is what happens when the first one scrolled off. It is not an
# error, but it is not a second mark either, and the screen has to say which.
def test_marking_a_player_already_marked_says_so_and_changes_nothing():
    outcome = read_mark("josh allen", BOARD, marked("Josh Allen"))

    assert outcome["action"] == "none"
    assert "already" in outcome["message"].lower()
    assert "Josh Allen" in outcome["message"]


# 10. The way back out. A wrong mark hides an available player for the rest of the session, so it
# has to be undoable from the same line it was made on.
def test_a_leading_dash_takes_a_mark_back():
    outcome = read_mark("-josh allen", BOARD, marked("Josh Allen"))

    assert outcome["action"] == "unmark"
    assert outcome["player"]["player_id"] == "00-0000001"
    assert "Josh Allen" in outcome["message"]


# 11. Only a hand-mark can be taken back. A player Sleeper reported is gone because he is gone, and
# an unmark that appeared to work on him would be the same lie in the other direction.
def test_a_player_who_was_never_marked_cannot_be_unmarked():
    outcome = read_mark("-keenan allen", BOARD, marked("Josh Allen"))

    assert outcome["action"] == "none"
    assert outcome["player"] is None
    assert "keenan allen" in outcome["message"]


# 12. Ambiguity has to be refused on the way back out too, against the marked set rather than the
# board — the drafter is choosing among what he has marked, not among everyone.
def test_an_ambiguous_unmark_is_refused_and_names_the_marked_players():
    outcome = read_mark("-allen", BOARD, marked("Josh Allen", "Keenan Allen"))

    assert outcome["action"] == "none"
    assert "Josh Allen" in outcome["message"]
    assert "Keenan Allen" in outcome["message"]
    assert "Braelon Allen" not in outcome["message"]


# 13. A dash with nothing after it is a slip, not a command.
def test_a_dash_with_no_name_is_refused():
    outcome = read_mark("-", BOARD, marked("Josh Allen"))

    assert outcome["action"] == "none"
    assert outcome["message"]


# 14. Nothing typed at this function is ever silently swallowed: every outcome carries a line for
# the screen, whether it worked or not.
def test_every_outcome_carries_a_message():
    typed = ["josh allen", "allen", "jsoh allen", "rookie kicker", "-", "-allen", "michael carter"]
    for text in typed:
        assert read_mark(text, BOARD, marked("Josh Allen", "Keenan Allen"))["message"]


# 15. The board is the warehouse's, read-only here as everywhere else in this package.
def test_reading_a_mark_does_not_touch_the_board():
    before = BOARD.copy()
    read_mark("josh allen", BOARD, {})
    pd.testing.assert_frame_equal(BOARD, before)


# 16. What a mark is *for*: the player off the board, and the draft not advanced by it. Asserted
# through the real ingestion seam, because that is where a mark either works or does not.
def test_a_marked_player_is_taken_without_advancing_the_draft():
    api = [{"player_id": "4033", "roster_id": 3, "pick_no": 1, "metadata": {}}]
    outcome = read_mark("josh allen", BOARD, {})

    result = ingest_picks([*api, *as_picks([outcome["player"]])], BOARD, LEAGUE)

    assert result["taken"] == {"00-0000001", "00-0000002"}
    assert result["picks_made"] == 1
    assert result["unmatched"] == []


# 17. And when Sleeper catches up, the pick it reports is the one that counts: one player, on the
# roster that actually drafted him, at the number he actually went.
def test_a_mark_gives_way_to_the_pick_when_the_api_reports_it():
    outcome = read_mark("josh allen", BOARD, {})
    api = [{"player_id": "4984", "roster_id": 6, "pick_no": 4, "metadata": {}}]

    result = ingest_picks([*api, *as_picks([outcome["player"]])], BOARD, LEAGUE)

    assert result["taken"] == {"00-0000001"}
    assert result["picks_made"] == 4
    qb = result["roster"].loc[result["roster"]["slot"] == "QB"]
    assert list(qb["players"].iloc[0]) == ["Josh Allen"]
