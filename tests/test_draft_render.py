"""What the drafter actually sees, for a given board and a given set of picks.

Cases 10-17 of the terminal render ticket, and the cost-of-waiting cases from issue #37.
`render_board` is pure — frames in, one string out — so these assert the screen itself rather
than capturing stdout, and none of them reaches into how the string is assembled.

Two things are asserted harder than the rest, because they are the ones that cost a pick:

- An unmatched pick is named *and* appears above the candidate list. A warning printed below
  thirty rows of board is a warning nobody reads at pick eleven.
- A player with no bye week shows a dash. `nan` and `<NA>` are what pandas prints for a missing
  Int64, and either one on a draft card reads as data rather than as a gap.
"""

import pandas as pd

from src.draft.picks import ingest_picks
from src.draft.render import render_board
from src.draft.waiting import rank_by_cost_of_waiting

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": SLOTS}


def candidates(*rows: dict) -> pd.DataFrame:
    """The frame the ranking hands over, with the bye week joined on beside it."""
    defaults = {
        "player_id": "00-0000001",
        "player_name": "A Back",
        "position": "RB",
        "team": "SF",
        "points_over_replacement": 100.0,
        # Cost of waiting (issue #37) widened what the ranking hands over. The three columns are
        # defaulted here rather than asserted, so the cases above stay about what they were about.
        "p_survives": 0.35,
        "cost_of_waiting": 42.0,
        "survival_known": True,
        "bye_week": 9,
    }
    frame = pd.DataFrame(
        [{**defaults, **row} for row in rows], columns=list(defaults)
    )
    # The board carries the bye as a nullable integer, which is the type that prints `<NA>`.
    frame["bye_week"] = frame["bye_week"].astype("Int64")
    return frame


def roster(*rows: tuple) -> pd.DataFrame:
    """A lineup as `ingest_picks` reports it: one row per slot the league actually starts."""
    return pd.DataFrame(
        [
            {
                "slot": slot,
                "starts": starts,
                "filled": len(players),
                "open": max(starts - len(players), 0),
                "players": list(players),
            }
            for slot, starts, players in rows
        ]
    )


def ingested(**overrides) -> dict:
    """What the ingestion seam returns, defaulted to a quiet draft with nothing wrong."""
    return {
        "taken": set(),
        "roster": roster(("QB", 1, []), ("RB", 2, []), ("SUPER_FLEX", 1, [])),
        "unmatched": [],
        "next_pick": 29,
        "pick_after_next": 56,
        "picks_made": 28,
        **overrides,
    }


def line_naming(out: str, text: str) -> str:
    """The one rendered line that mentions something, so a claim is about that row alone."""
    matches = [line for line in out.splitlines() if text in line]
    assert len(matches) == 1, f"expected exactly one line naming {text!r}, got {matches}"
    return matches[0]


def section(out: str, title: str) -> list[str]:
    """The indented rows under one section heading — headings start at the left margin."""
    lines = out.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(title))
    rows = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        if line.strip():
            rows.append(line)
    return rows


# 10. Each candidate line carries value, position, team and bye week.
def test_a_candidate_is_shown_with_his_value_position_team_and_bye():
    out = render_board(
        candidates({"player_name": "Bijan Robinson", "position": "RB", "team": "ATL",
                    "bye_week": 11, "points_over_replacement": 178.4}),
        ingested(),
        LEAGUE,
    )
    row = line_naming(out, "Bijan Robinson")
    assert "RB" in row
    assert "ATL" in row
    assert "11" in row
    assert "178.4" in row


# 11. A missing bye week renders blank, never `nan` and never a guessed week.
def test_a_player_with_no_bye_week_shows_a_gap_rather_than_a_number():
    out = render_board(
        candidates({"player_name": "No Schedule", "bye_week": None}), ingested(), LEAGUE
    )
    row = line_naming(out, "No Schedule")
    assert "nan" not in row.lower()
    assert "<NA>" not in row
    assert "-" in row


# 12. The next overall pick number and the picks-made count appear.
def test_the_next_pick_number_and_the_picks_made_are_shown():
    out = render_board(candidates({}), ingested(next_pick=29, picks_made=28), LEAGUE)
    header = out.splitlines()[0]
    assert "#29" in header
    assert "28" in header


# 13. The roster is shown slot by slot, superflex included, with open slots visible.
def test_the_roster_is_shown_by_slot_including_the_superflex():
    out = render_board(
        candidates({}),
        ingested(
            roster=roster(
                ("QB", 1, ["Josh Allen"]),
                ("RB", 2, []),
                ("SUPER_FLEX", 1, ["Jayden Daniels"]),
            )
        ),
        LEAGUE,
    )
    rows = section(out, "My roster")
    assert any("QB" in row and "Josh Allen" in row for row in rows)
    assert any("SUPER_FLEX" in row and "Jayden Daniels" in row for row in rows)
    # Two backs still to find, which is the whole reason to look at this block mid-draft.
    assert any(row.strip().startswith("RB") and "0/2" in row for row in rows)


# 14. An unmatched pick becomes a prominent warning naming the player; several all appear.
def test_an_unmatched_pick_is_a_warning_naming_the_player_above_the_board():
    out = render_board(
        candidates({"player_name": "Bijan Robinson"}),
        ingested(unmatched=[
            {"sleeper_id": "12345", "player_name": "Someone Unknown", "position": "WR",
             "pick_no": 14},
        ]),
        LEAGUE,
    )
    assert "UNMATCHED" in out
    warning = line_naming(out, "Someone Unknown")
    assert out.index(warning) < out.index(line_naming(out, "Bijan Robinson"))


def test_every_unmatched_pick_is_named():
    out = render_board(
        candidates({}),
        ingested(unmatched=[
            {"sleeper_id": "1", "player_name": "First Unknown", "position": "WR", "pick_no": 3},
            {"sleeper_id": "2", "player_name": "Second Unknown", "position": "TE", "pick_no": 9},
        ]),
        LEAGUE,
    )
    assert "First Unknown" in out
    assert "Second Unknown" in out


# 15. With nothing unmatched there is no warning block at all.
def test_a_clean_draft_shows_no_warning():
    out = render_board(candidates({}), ingested(), LEAGUE)
    assert "UNMATCHED" not in out


# 16. A finished draft says so instead of printing `None` for the next pick.
def test_a_finished_draft_says_so_rather_than_printing_none():
    out = render_board(
        candidates({}),
        ingested(next_pick=None, pick_after_next=None, picks_made=210),
        LEAGUE,
    )
    header = out.splitlines()[0]
    assert "None" not in header
    assert "complete" in header.lower()


# 17. An empty candidate list renders without raising.
def test_a_board_with_nobody_left_still_renders():
    empty = candidates().iloc[0:0]
    out = render_board(empty, ingested(), LEAGUE)
    assert "My roster" in out


def test_only_the_asked_for_number_of_candidates_are_shown():
    many = candidates(*[
        {"player_id": f"00-000000{index}", "player_name": f"Player {index}",
         "points_over_replacement": 100.0 - index}
        for index in range(10)
    ])
    out = render_board(many, ingested(), LEAGUE, limit=3)
    assert "Player 2" in out
    assert "Player 3" not in out


def test_the_three_halves_join_on_a_real_payload():
    """Ingestion, ranking and rendering run together, so the joins are checked not assumed."""
    board = pd.DataFrame([
        {"player_id": "00-0000001", "sleeper_id": "4034", "player_name": "A Back",
         "position": "RB", "team": "SF", "points_over_replacement": 120.0, "bye_week": 9},
        {"player_id": "00-0000002", "sleeper_id": "4035", "player_name": "A Receiver",
         "position": "WR", "team": "KC", "points_over_replacement": 80.0, "bye_week": 6},
    ])
    board["bye_week"] = board["bye_week"].astype("Int64")

    picks = [{
        "player_id": "4034", "roster_id": 6, "pick_no": 1,
        "metadata": {"first_name": "A", "last_name": "Back", "position": "RB"},
    }]
    survival = pd.DataFrame(
        [
            {"player_id": "00-0000001", "overall_pick": 2, "p_survives": 1.0},
            {"player_id": "00-0000002", "overall_pick": 2, "p_survives": 1.0},
            {"player_id": "00-0000001", "overall_pick": 28, "p_survives": 0.4},
            {"player_id": "00-0000002", "overall_pick": 28, "p_survives": 0.6},
        ]
    )
    result = ingest_picks(picks, board, LEAGUE)
    ranked = rank_by_cost_of_waiting(board, survival, result)["candidates"].merge(
        board[["player_id", "bye_week"]], on="player_id", how="left"
    )

    out = render_board(ranked, result, LEAGUE)
    assert "A Receiver" in out
    # Drafted, by me: off the board and onto the roster, not both and not neither.
    assert any("A Back" in row for row in section(out, "My roster"))
    assert not any("A Back" in row for row in section(out, "Best available"))


# The cases below belong to issue #37, which changed the ranking rule from raw value to cost of
# waiting. Two numbers joined the candidate row, and the screen now has to say which rule it
# ranked by — because the tool falls back to value ranking on its own, past the point the survival
# model covers, and a drafter who cannot see that has no way to know what he is reading.


def test_a_candidate_shows_his_survival_probability_and_what_waiting_costs():
    out = render_board(
        candidates({"player_name": "Bijan Robinson", "p_survives": 0.34,
                    "cost_of_waiting": 72.3, "points_over_replacement": 178.4}),
        ingested(),
        LEAGUE,
    )
    row = line_naming(out, "Bijan Robinson")
    assert "34%" in row
    assert "72.3" in row
    # The value it is traded off against stays on the row: the ranking is auditable or it is
    # obeyed, and the whole design says auditable.
    assert "178.4" in row


def test_the_probabilities_are_anchored_to_a_named_pick():
    """A bare percentage means nothing without the pick it is a probability of reaching."""
    out = render_board(
        candidates({}), ingested(next_pick=29, pick_after_next=56, picks_made=28), LEAGUE
    )
    heading = next(line for line in out.splitlines() if line.startswith("Best available"))
    # The turn after the one being decided, which is what a passed-over player has to survive to.
    assert "#56" in heading
    assert "cost of waiting" in heading


def test_the_screen_says_survives_and_survival_probability():
    """The vocabulary rule, on the surface it exists to protect."""
    out = render_board(candidates({}), ingested(), LEAGUE)
    assert "survives" in out
    assert "survival probability" in out


def test_the_screen_never_says_availability():
    """The glossary spends that word on how much of a season a player can play.

    Two different meanings under one word, on a screen read under a pick clock, is how a drafter
    ends up reading a supply number as an injury number.
    """
    out = render_board(
        candidates({"p_survives": 0.34, "cost_of_waiting": 72.3}),
        ingested(unmatched=[{"sleeper_id": "1", "player_name": "Someone Unknown",
                             "position": "WR", "pick_no": 14}]),
        LEAGUE,
    )
    assert "availability" not in out.lower()


def test_a_candidate_with_no_survival_data_shows_gaps_rather_than_numbers():
    out = render_board(
        candidates({"player_name": "Unpriced Rookie", "p_survives": None,
                    "cost_of_waiting": None, "survival_known": False}),
        ingested(),
        LEAGUE,
    )
    row = line_naming(out, "Unpriced Rookie")
    assert "nan" not in row.lower()
    assert "<NA>" not in row
    assert "0%" not in row
    # He is still on the board — a missing number hides him from nobody.
    assert "Unpriced Rookie" in row


def test_a_degraded_result_says_it_has_fallen_back_to_value_ranking():
    out = render_board(
        candidates({"player_name": "Best Left", "p_survives": None, "cost_of_waiting": None,
                    "survival_known": False}),
        ingested(next_pick=195, pick_after_next=196, picks_made=194),
        LEAGUE,
        degraded=True,
        covers_to=180,
    )
    assert "points over replacement" in out
    # Both numbers named, so the drafter can see how far past the model he is rather than being
    # told only that something is wrong.
    assert "#196" in out
    assert "#180" in out
    # And it must not still claim to be ranking by a rule it no longer has the inputs for.
    assert "cost of waiting" not in out


# Issue #36's render cases. A hand-marked player is off the board on the drafter's own say-so
# rather than Sleeper's, and that is the one subtraction nothing else on screen can account for:
# the header's pick count does not move for a mark, so a board short of two players would look
# like a board that had lost them. Both cases below are about being able to see what was marked.


def test_the_players_marked_by_hand_are_named_on_screen():
    out = render_board(
        candidates({"player_name": "Still There"}),
        ingested(),
        LEAGUE,
        marked=[
            {"player_id": "00-0000009", "player_name": "Gone Already", "position": "QB",
             "team": "BUF"},
            {"player_id": "00-0000010", "player_name": "Also Gone", "position": "RB",
             "team": "ATL"},
        ],
    )
    # Named rather than counted, for the same reason an unmatched pick is: a mistyped mark hides
    # a player who is actually available, and the name is the only part of that a drafter can act
    # on before his next pick.
    assert "Gone Already" in out
    assert "Also Gone" in out
    # And the count belongs on the line that gets checked against the Sleeper app, because that
    # line's "28 picks made" is not what this board has subtracted.
    assert "2 by hand" in line_naming(out, "picks made")


def test_a_draft_with_nothing_marked_by_hand_says_nothing_about_it():
    out = render_board(candidates({"player_name": "Still There"}), ingested(), LEAGUE)
    assert "hand" not in out.lower()


# Issue #38's render cases. Composition guidance is the one signal on this screen that is not
# about a player: it says which roster shapes the opening is still on track for, read from the
# plan table at run time. It sits *beside* the ranking rather than inside it — the ranking is the
# recommendation and this is the thing a drafter overrules it with, and folding one into the other
# would leave neither auditable.


def guidance(**overrides) -> dict:
    """What `composition_guidance` hands over, defaulted to a live band with a leader."""
    bands = pd.DataFrame(
        [
            {"position": "QB", "count": 1, "plans": 3, "points_vs_field": 0.0,
             "win_rate": 0.25},
            {"position": "QB", "count": 2, "plans": 1, "points_vs_field": 38.0,
             "win_rate": 0.44},
            {"position": "RB", "count": 1, "plans": 3, "points_vs_field": 12.7,
             "win_rate": 0.31},
            {"position": "RB", "count": 2, "plans": 1, "points_vs_field": 0.0,
             "win_rate": 0.25},
        ]
    )
    return {
        "withdrawn": False,
        "reason": None,
        "opening_rounds": 5,
        "picks_spent": 4,
        "taken": {"QB": 1, "RB": 1, "WR": 1, "TE": 1},
        "open_plans": 4,
        "total_plans": 10,
        "bands": bands,
        "best": {"position": "QB", "count": 2, "plans": 1, "points_vs_field": 38.0,
                 "win_rate": 0.44},
        "keeps_best": ["QB"],
        "closes_best": ["RB", "WR", "TE"],
        **overrides,
    }


def test_the_opening_shape_is_shown_beside_the_ranking():
    out = render_board(
        candidates({"player_name": "Bijan Robinson"}), ingested(), LEAGUE,
        guidance=guidance(),
    )
    heading = line_naming(out, "Opening shape")

    # Beside the ranking means above it and outside it: a block of its own, not a column on a
    # candidate row, so that nothing about it can be mistaken for part of the order below.
    assert out.index(heading) < out.index(line_naming(out, "Best available"))
    assert not any("Opening shape" in row for row in section(out, "Best available"))


def test_the_guidance_names_a_band_rather_than_a_composition():
    out = render_board(candidates(), ingested(), LEAGUE, guidance=guidance())
    rows = section(out, "Opening shape")

    # A position and a count, and the score behind it — never a composition string, because the
    # plan table records no dispersion and cannot tell the leading compositions apart.
    assert any("QB" in row and "2" in row and "38" in row for row in rows)
    assert "2QB2RB1TE" not in out


# 33. Guidance never changes the order of the recommendations.
def test_guidance_does_not_reorder_the_recommendations():
    board = candidates(
        {"player_name": "A Quarterback", "position": "QB", "cost_of_waiting": 60.0},
        {"player_name": "A Back", "position": "RB", "cost_of_waiting": 40.0},
        {"player_name": "A Receiver", "position": "WR", "cost_of_waiting": 20.0},
    )
    without = render_board(board, ingested(), LEAGUE)
    with_guidance = render_board(board, ingested(), LEAGUE, guidance=guidance())

    assert section(without, "Best available") == section(with_guidance, "Best available")


# 35. A candidate whose position would close the best-scoring open band is reported as doing so.
def test_the_positions_that_would_close_the_best_band_are_named_on_screen():
    out = render_board(candidates(), ingested(), LEAGUE, guidance=guidance())
    rows = section(out, "Opening shape")

    closing = [row for row in rows if "RB" in row and "WR" in row and "TE" in row]
    assert closing, f"no line named the positions that close the band: {rows}"


# 32. Guidance is absent, and *marked* absent, once the opening the plan table covers is over.
def test_withdrawn_guidance_says_so_rather_than_quietly_vanishing():
    out = render_board(
        candidates(), ingested(), LEAGUE,
        guidance=guidance(
            withdrawn=True,
            reason="the plan table covers the first 5 rounds, which are behind us",
            bands=pd.DataFrame(columns=["position", "count", "plans", "points_vs_field",
                                        "win_rate"]),
            best=None, keeps_best=[], closes_best=[], open_plans=0, picks_spent=5,
        ),
    )
    assert "withdrawn" in line_naming(out, "Opening shape").lower()
    assert "the plan table covers the first 5 rounds" in out


def test_an_opening_no_plan_covers_says_so_rather_than_showing_a_band():
    out = render_board(
        candidates(), ingested(), LEAGUE,
        guidance=guidance(
            reason="no plan for this seat matches the opening so far",
            bands=pd.DataFrame(columns=["position", "count", "plans", "points_vs_field",
                                        "win_rate"]),
            best=None, keeps_best=[], closes_best=[], open_plans=0,
        ),
    )
    assert "no plan for this seat matches the opening so far" in out


def test_a_screen_with_no_guidance_says_nothing_about_the_opening():
    out = render_board(candidates(), ingested(), LEAGUE)
    assert "Opening shape" not in out


# Issue #29, user story 17: the screen when the board has been narrowed to one position.
#
# The narrowing itself happens in the ranking, which was written to take a position and is tested
# for it. What is asserted here is the half that stops a narrowed board being read as the whole
# one: the heading has to say which position it is showing, and an empty one has to say which
# position is empty. "nobody left on the board" under a quarterback filter is a sentence that would
# end a draft.


# 33. A board showing one position says so where the count is read.
def test_a_narrowed_board_names_the_position_it_is_showing():
    out = render_board(
        candidates(
            {"player_id": "00-0000001", "player_name": "A Passer", "position": "QB"},
        ),
        ingested(), LEAGUE, position="QB",
    )
    assert "QB" in line_naming(out, "Best available")


# 34. And a board showing everything claims nothing about a position.
def test_an_unnarrowed_board_names_no_position():
    out = render_board(candidates(), ingested(), LEAGUE)
    heading = line_naming(out, "Best available")

    assert "only" not in heading.lower()


# 35. The sentence that would cost a pick. Nobody left *at this position* is a reason to look
# elsewhere; nobody left *on the board* is a finished draft, and the two must not read alike.
def test_an_empty_narrowed_board_says_which_position_is_empty():
    out = render_board(candidates(), ingested(), LEAGUE, position="QB")

    assert "QB" in line_naming(out, "left")


# 36. The filter narrows the list and nothing else. My roster is my roster whatever the board is
# showing, and the opening shape is a claim about the roster rather than about the list below it.
def test_narrowing_the_board_leaves_the_roster_and_the_opening_shape_alone():
    rows = [
        {"player_id": "00-0000001", "player_name": "A Passer", "position": "QB"},
        {"player_id": "00-0000002", "player_name": "A Back", "position": "RB"},
    ]
    full = render_board(candidates(*rows), ingested(), LEAGUE, guidance=guidance())
    narrowed = render_board(
        candidates(rows[0]), ingested(), LEAGUE, guidance=guidance(), position="QB"
    )

    assert section(narrowed, "My roster") == section(full, "My roster")
    assert section(narrowed, "Opening shape") == section(full, "Opening shape")


def cliffs(*rows: tuple) -> pd.DataFrame:
    """What `position_cliffs` hands over: one row per position still on the board."""
    return pd.DataFrame(
        [
            {"position": position, "above": above, "drop": drop, "remaining": left}
            for position, above, drop, left in rows
        ],
        columns=["position", "above", "drop", "remaining"],
    )


# Issue #29, user story 18: how many are left before the next real drop, position by position.
#
# The block answers a question the ranking cannot, because the ranking hands back one number per
# player and the eye reading the top of it sees whichever positions happen to rank highest. So
# what is asserted here is that the two numbers a drafter compares — the count above the cliff and
# the size of the drop — are both on the line, and that the block reads in the order the rest of
# the screen already uses.


def depth_row(out: str, position: str) -> str:
    """The one depth line about one position.

    Found inside the depth section rather than anywhere on screen: the roster block above names
    positions too, so `QB` alone matches two lines that mean entirely different things.
    """
    rows = [row for row in section(out, "Depth") if row.split()[0] == position]
    assert len(rows) == 1, f"expected one depth row for {position}, got {rows}"
    return rows[0]


# 37. Both numbers, because either alone is unreadable: three left is fine behind a four-point
# step and an emergency behind a forty-point one.
def test_each_position_shows_how_many_are_left_above_the_drop_and_how_big_it_is():
    out = render_board(
        candidates(), ingested(), LEAGUE,
        cliffs=cliffs(("QB", 3, 80.4, 12), ("RB", 6, 8.2, 41)),
    )
    passer = depth_row(out, "QB")

    assert "3" in passer and "80.4" in passer


# 38. The count of who is left at all, beside the count above the cliff. "3 of 4" and "3 of 40"
# are different boards, and the first number is the same in both.
def test_a_position_shows_how_many_are_left_at_it_in_total():
    out = render_board(
        candidates(), ingested(), LEAGUE, cliffs=cliffs(("QB", 3, 80.4, 12))
    )
    assert "12" in depth_row(out, "QB")


# 39. The same order the roster block reads in. A block whose rows moved between ticks would be
# re-read from the top every time, which is the opposite of at a glance.
def test_the_positions_read_in_the_order_the_league_starts_them():
    out = render_board(
        candidates(), ingested(), LEAGUE,
        cliffs=cliffs(("DST", 1, 5.0, 20), ("RB", 6, 8.2, 41), ("QB", 3, 80.4, 12)),
    )
    rows = section(out, "Depth")

    assert [row.split()[0] for row in rows] == ["QB", "RB", "DST"]


# 40. A screen with nothing to say about depth says nothing, rather than an empty heading.
def test_a_screen_with_no_cliffs_says_nothing_about_depth():
    assert "Depth" not in render_board(candidates(), ingested(), LEAGUE)
    assert "Depth" not in render_board(candidates(), ingested(), LEAGUE, cliffs=cliffs())


# 41. Beside the ranking, above it, and changing nothing about it — the same rule the opening
# shape follows, for the same reason: a drafter has to be able to overrule what he is shown.
def test_depth_sits_above_the_candidate_list_and_does_not_reorder_it():
    rows = [
        {"player_id": "00-0000001", "player_name": "A Passer", "position": "QB"},
        {"player_id": "00-0000002", "player_name": "A Back", "position": "RB"},
    ]
    plain = render_board(candidates(*rows), ingested(), LEAGUE)
    with_depth = render_board(
        candidates(*rows), ingested(), LEAGUE, cliffs=cliffs(("QB", 3, 80.4, 12))
    )

    assert with_depth.index("Depth") < with_depth.index("Best available")
    assert available(with_depth) == available(plain)


def available(out: str) -> str:
    """The candidate list out of one screen — the part the depth block must not touch."""
    return out.split("Best available")[-1]
