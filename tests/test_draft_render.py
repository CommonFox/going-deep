"""What the drafter actually sees, for a given board and a given set of picks.

Cases 10-17 of the terminal render ticket. `render_board` is pure — frames in, one string out —
so these assert the screen itself rather than capturing stdout, and none of them reaches into how
the string is assembled.

Two things are asserted harder than the rest, because they are the ones that cost a pick:

- An unmatched pick is named *and* appears above the candidate list. A warning printed below
  thirty rows of board is a warning nobody reads at pick eleven.
- A player with no bye week shows a dash. `nan` and `<NA>` are what pandas prints for a missing
  Int64, and either one on a draft card reads as data rather than as a gap.
"""

import pandas as pd

from src.draft.candidates import rank_candidates
from src.draft.picks import ingest_picks
from src.draft.render import render_board

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
    out = render_board(candidates({}), ingested(next_pick=None, picks_made=210), LEAGUE)
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
    result = ingest_picks(picks, board, LEAGUE)
    ranked = rank_candidates(board, result["taken"]).merge(
        board[["player_id", "bye_week"]], on="player_id", how="left"
    )

    out = render_board(ranked, result, LEAGUE)
    assert "A Receiver" in out
    # Drafted, by me: off the board and onto the roster, not both and not neither.
    assert any("A Back" in row for row in section(out, "My roster"))
    assert not any("A Back" in row for row in section(out, "Best available"))
