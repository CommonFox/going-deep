"""Issue #77: the ranking is roster-blind, and suggests a 3rd QB over an unfilled starting slot.

`candidates.rank_candidates` and `waiting.rank_by_cost_of_waiting` price a player against the
league's replacement level — a season-long, roster-independent fact, and deliberately so (see both
modules' docstrings). Neither says what a player is worth to *this* partly-filled roster, where a
third quarterback is worth roughly nothing and a first is worth everything. `src/draft/roster.py`
is the module that answers that question, without repricing anything the board already priced.

Two rules, both pure arithmetic over the board's frozen numbers:

- **Displacement value** (`marginal_value`). A candidate whose position still has an open slot
  keeps his full points over replacement. One whose eligible slots (dedicated, flex, superflex)
  are all filled is worth only what he beats the weakest current starter by — zero if he does not.
- **Must-fill endgame** (`must_fill` / `restrict`). Once picks remaining equal or trail starting
  slots still empty, the board is restricted to positions that can still fill one — mirroring the
  `unfilled` branch in `gold/draft_strategy.py`'s own simulator, which hit this same failure first.

Both are new facts about *the roster*, kept out of `candidates`/`waiting`/`cliff` on purpose —
see the module docstring on why cliffs and cost of waiting stay roster-blind.
"""

import pandas as pd

from src.draft.live import screen
from src.draft.picks import _assign_to_slots, _roster_frame, next_pick_number
from src.draft.roster import marginal_value, must_fill, restrict

ANOTHER_ROSTER = 7

# A superflex league: QB is eligible for QB and SUPER_FLEX, RB/WR for their own slot plus FLEX and
# SUPER_FLEX, TE for its own slot plus FLEX and SUPER_FLEX. Matches the Sleeper league in the other
# draft-package test files.
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": SLOTS}


def league(**overrides) -> dict:
    return {**LEAGUE, **overrides}


def drafted(player_id: str, position: str, name: str = "Some Starter") -> dict:
    """One of my own picks, exactly as `picks.ingest_picks` reports it in `mine`."""
    return {"player_id": player_id, "player_name": name, "position": position}


def mine(*players: dict) -> dict:
    """What `ingest_picks` returns, cut to the two keys `marginal_value` reads."""
    return {"mine": list(players)}


def board(*rows: tuple) -> pd.DataFrame:
    """A board with only the columns the module needs: identity, position and price."""
    return pd.DataFrame(
        [
            {"player_id": player_id, "player_name": name, "position": position,
             "points_over_replacement": value}
            for player_id, name, position, value in rows
        ],
        columns=["player_id", "player_name", "position", "points_over_replacement"],
    )


def candidates(*rows: tuple) -> pd.DataFrame:
    """Who is left, in the shape `rank_candidates` hands over."""
    return pd.DataFrame(
        [
            {"player_id": player_id, "player_name": name, "position": position, "team": "SF",
             "points_over_replacement": value}
            for player_id, name, position, value in rows
        ],
        columns=["player_id", "player_name", "position", "team", "points_over_replacement"],
    )


# --- Displacement value --------------------------------------------------------------------

# 1. Nobody drafted yet — every eligible slot is open, so nothing is displaced at pick one.
def test_before_any_pick_every_candidate_keeps_his_full_value():
    values = marginal_value(
        candidates(("00-02", "A Passer", "QB", 44.9), ("00-03", "A Tight End", "TE", 41.8)),
        mine(), LEAGUE, board(),
    )
    assert list(values) == [44.9, 41.8]


# 2. A shared slot still open (FLEX here) — a position whose *dedicated* slot is full is untouched
# as long as a slot it is eligible for still has room, matching "leaves RB/WR untouched while slots
# are open".
def test_a_dedicated_slot_full_but_a_shared_slot_open_keeps_full_value():
    the_board = board(
        ("00-01", "RB One", "RB", 150.0), ("00-02", "RB Two", "RB", 120.0),
    )
    values = marginal_value(
        candidates(("00-03", "RB Three", "RB", 60.0)),
        mine(drafted("00-01", "RB"), drafted("00-02", "RB")), LEAGUE, the_board,
    )
    # Both RB slots are full, but FLEX and SUPER_FLEX are both still open and both take a back.
    assert list(values) == [60.0]


# 3. The ticket's own worked example: both QB slots are full, and a genuine upgrade over the
# weaker of the two stays visible at the size of the upgrade.
def test_a_genuine_upgrade_over_the_weaker_starter_is_worth_the_difference():
    the_board = board(
        ("00-01", "Strong Starter", "QB", 70.0), ("00-02", "Mayfield QB2", "QB", 46.7),
    )
    values = marginal_value(
        candidates(("00-03", "Tyler Shough", "QB", 64.2)),
        mine(drafted("00-01", "QB"), drafted("00-02", "QB")), LEAGUE, the_board,
    )
    assert values.iloc[0] == 64.2 - 46.7


# 4. A third quarterback worse than the weaker starter is worth nothing, not a negative number —
# points over replacement is measured against a freely available player and a below-replacement
# player is never the honest answer.
def test_a_backup_worse_than_the_weakest_starter_floors_at_zero():
    the_board = board(
        ("00-01", "Strong Starter", "QB", 70.0), ("00-02", "Mayfield QB2", "QB", 46.7),
    )
    values = marginal_value(
        candidates(("00-03", "Malik Willis", "QB", 44.9)),
        mine(drafted("00-01", "QB"), drafted("00-02", "QB")), LEAGUE, the_board,
    )
    assert values.iloc[0] == 0.0


# 5. Mixed occupancy: the superflex slot is held by a non-quarterback, because the roster's other
# slots — RB, WR, TE and the flex — are all already full of better players first. The floor a new
# QB is measured against is the weaker of the two occupants of QB's eligible slots, not only the
# one who is himself a quarterback — displacing the elite back would be the wrong trade, and the
# arithmetic has to price against the weak starter instead.
def test_the_floor_is_the_weakest_occupant_of_an_eligible_slot_whoever_he_is():
    the_board = board(
        ("00-01", "Weak Starting QB", "QB", 30.0),
        ("00-02", "RB One", "RB", 50.0), ("00-03", "RB Two", "RB", 50.0),
        ("00-04", "WR One", "WR", 60.0), ("00-05", "WR Two", "WR", 60.0),
        ("00-06", "The Starting TE", "TE", 40.0),
        ("00-07", "Flex WR", "WR", 55.0),
        ("00-08", "Elite Back In The Superflex", "RB", 200.0),
    )
    roster = mine(
        drafted("00-01", "QB"), drafted("00-02", "RB"), drafted("00-03", "RB"),
        drafted("00-04", "WR"), drafted("00-05", "WR"), drafted("00-06", "TE"),
        drafted("00-07", "WR"), drafted("00-08", "RB"),
    )
    # RB, WR, TE and the flex are all spoken for before the superflex is ever reached, so the
    # elite back lands there rather than in a dedicated slot — the same chronological, dedicated-
    # first assignment the roster block on screen is built from.
    values = marginal_value(candidates(("00-09", "A Better QB", "QB", 50.0)), roster, LEAGUE, the_board)
    # QB's eligible slots are QB and SUPER_FLEX; both are full. The floor is the weaker of the two
    # occupants (30.0, the QB) rather than the stronger one (200.0, the back) — displacing the back
    # would be the wrong trade, so the arithmetic must not price against him.
    assert values.iloc[0] == 20.0


# 6. A position this league starts no slot for at all: no eligible slot ever has room, and the
# module returns a defined answer rather than raising on an empty floor.
def test_a_position_with_no_starting_slot_in_this_league_does_not_raise():
    values = marginal_value(
        candidates(("00-01", "A Punter", "P", 5.0)),
        mine(), league(slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1}), board(),
    )
    assert list(values) == [0.0]


# --- Must-fill endgame -------------------------------------------------------------------------

def picks(next_pick: int | None, roster: list[dict]) -> dict:
    """What `ingest_picks` returns, cut to what `must_fill` reads: my picks and my next turn.

    `roster` is built through the real `picks._assign_to_slots`/`_roster_frame` — the same slot
    assignment the live roster block is drawn from — so a third back correctly lands in the flex
    rather than being lost the way a naive per-position count would lose it.
    """
    return {
        "mine": roster,
        "next_pick": next_pick,
        "roster": _roster_frame(_assign_to_slots(roster, SLOTS), SLOTS),
    }


# 7. More picks left than slots to fill — plenty of room, nothing restricted yet.
def test_inactive_when_picks_remaining_exceed_open_slots():
    result = must_fill(picks(next_pick=100, roster=[]), league(rounds=15))
    assert result["active"] is False


# 8. The boundary from the ticket's own reproduction: two of my picks left (183 and 210 in a
# 15-round draft, meaning 13 already made) and two open slots (K, DST) — the exact shape that put
# an unstartable third quarterback over a kicker you must draft.
def test_active_at_the_boundary_where_picks_remaining_equal_open_slots():
    thirteen_starters = [
        drafted(f"00-{index:02d}", position)
        for index, position in enumerate(
            ["QB", "RB", "RB", "WR", "WR", "TE", "QB", "RB", "WR", "WR", "RB", "WR", "TE"]
        )
    ]
    result = must_fill(picks(next_pick=183, roster=thirteen_starters), league(rounds=15))
    assert result["active"] is True
    assert result["positions"] == {"K", "DST"}


# 9. Fewer picks left than slots to fill — cannot possibly finish the roster, and the rule still
# applies rather than giving up: make the best of the picks there are.
def test_active_when_picks_remaining_trail_open_slots():
    fourteen_starters = [drafted(f"00-{i:02d}", "RB") for i in range(14)]
    result = must_fill(picks(next_pick=210, roster=fourteen_starters), league(rounds=15))
    assert result["active"] is True


# 10. A fully-started lineup with only bench picks left — nothing is unfilled, so nothing is
# restricted, whatever round it is. Ten picks fill all ten starting slots exactly: the dedicated
# slots, then two extra backs for the flex and the superflex.
def test_inactive_once_every_starting_slot_is_filled():
    full = [
        drafted(f"00-{index:02d}", position)
        for index, position in enumerate(
            ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DST", "RB", "RB"]
        )
    ]
    result = must_fill(picks(next_pick=210, roster=full), league(rounds=15))
    assert result["active"] is False


# 11. A finished draft has nothing left to fill, and is not reported as an emergency.
def test_inactive_once_the_draft_is_over():
    result = must_fill(picks(next_pick=None, roster=[]), league(rounds=15))
    assert result["active"] is False


# 12. `restrict` is a no-op when the rule is not active — the same contract `hold.withhold` makes.
def test_restrict_is_a_no_op_when_inactive():
    frame = candidates(("00-01", "A Back", "RB", 150.0), ("00-02", "A Kicker", "K", 20.0))
    kept = restrict(frame, {"active": False, "positions": set()})
    pd.testing.assert_frame_equal(kept, frame)


# 13. `restrict` drops everyone outside the allowed positions and keeps the rest in order, the same
# shape as `hold.withhold`'s own subtraction test.
def test_restrict_drops_everyone_outside_the_allowed_positions():
    frame = candidates(
        ("00-01", "A Quarterback", "QB", 28.1), ("00-02", "A Defense", "DST", 24.6),
        ("00-03", "A Kicker", "K", 6.7),
    )
    kept = restrict(frame, {"active": True, "positions": {"K", "DST"}})
    assert list(kept["position"]) == ["DST", "K"]


# --- End to end, through the live screen --------------------------------------------------------
#
# The two cases above are about one decision each; these are the ticket's own two symptoms, drawn
# through the real pipeline (`ingest_picks` -> `rank_by_cost_of_waiting` -> `roster` -> `render`)
# against a hand-written board, the same way `test_draft_hold.py`'s end-to-end section is.


def sleeper_board(*rows: tuple) -> pd.DataFrame:
    """A board carrying what `ingest_picks` and the ranking both need."""
    return pd.DataFrame(
        [
            {"player_id": player_id, "sleeper_id": player_id.replace("00-", ""),
             "player_name": name, "position": position, "team": "SF",
             "points_over_replacement": value, "bye_week": 9}
            for player_id, name, position, value in rows
        ]
    )


def sleeper_pick(player_id: str, pick_no: int, roster_id: int) -> dict:
    return {
        "player_id": player_id.replace("00-", ""), "roster_id": roster_id, "pick_no": pick_no,
        "metadata": {"first_name": "Some", "last_name": "Player", "position": "RB"},
    }


def context(the_board: pd.DataFrame) -> dict:
    return {
        "board": the_board,
        "survival": pd.DataFrame(columns=["player_id", "overall_pick", "p_survives"]),
        "plans": pd.DataFrame(columns=["draft_slot", "plan", "trials", "points_vs_field", "win_rate"]),
        "draft": {"draft_id": "1"},
        "league": LEAGUE,
    }


def best_available(out: str) -> str:
    """The name on the very top row of the candidate list."""
    lines = [line for line in out.splitlines() if line.strip().startswith("1 ")]
    assert lines, f"no ranked row found in:\n{out}"
    return lines[0]


# 14. Symptom 1. Both of my quarterback slots are filled (70.0 in the dedicated slot, Mayfield's
# 46.7 in the superflex), and two backup quarterbacks worth less than Mayfield are still on the
# board alongside a tight end who has an open flex to be additive to. Raw PoR alone puts the
# backups first — Willis outranks everyone at 44.9 — which is the ticket's whole complaint.
# Roster-adjusted, both backups collapse to zero and the tight end, still fully valued, wins.
def test_symptom_one_a_third_quarterback_no_longer_tops_a_full_starting_pair():
    the_board = sleeper_board(
        ("00-01", "Strong Starter", "QB", 70.0),
        ("00-02", "Mayfield QB2", "QB", 46.7),
        ("00-03", "Malik Willis", "QB", 44.9),
        ("00-04", "Jordan Love", "QB", 44.0),
        ("00-05", "Tyler Warren", "TE", 41.8),
    )
    payload = [
        sleeper_pick("00-01", 1, LEAGUE["roster_id"]),
        sleeper_pick("00-02", 2, LEAGUE["roster_id"]),
    ]
    out = screen(context(the_board), payload)

    assert "Tyler Warren" in best_available(out)


def _my_pick_numbers(count: int, league: dict) -> list[int]:
    """The seat's own next `count` overall pick numbers, walked the same way `ingest_picks` does.

    Computed rather than typed, so a fixture claiming "thirteen of my picks are made" actually
    describes a reachable point in this seat's real snake sequence — 1, 28, 29, 56, 57, ... for
    seat 1 of 14 — rather than an overall pick count that could never belong to one seat alone.
    """
    numbers = []
    made = 0
    for _ in range(count):
        made = next_pick_number(made, league["seat"], league["team_count"], league["rounds"])
        numbers.append(made)
    return numbers


# 15. Symptom 2. Thirteen of my fifteen picks are made, every starting slot but K and DST is full,
# and two of my picks remain — the exact boundary that put an unstartable quarterback over a
# kicker you must draft. Every other overall pick is filled by another roster, so the draft state
# is one a real snake could actually reach rather than an overall pick count no single seat could
# own. Both backup quarterbacks on the board are worth more than either the kicker or the defense
# in raw PoR, and neither can ever start.
def test_symptom_two_an_unstartable_quarterback_no_longer_outranks_a_must_draft_slot():
    the_board = sleeper_board(
        ("00-01", "Starting QB", "QB", 70.0), ("00-02", "Superflex QB", "QB", 60.0),
        ("00-03", "RB One", "RB", 90.0), ("00-04", "RB Two", "RB", 80.0),
        ("00-05", "Flex RB", "RB", 75.0),
        ("00-06", "WR One", "WR", 85.0), ("00-07", "WR Two", "WR", 70.0),
        ("00-08", "Starting TE", "TE", 65.0),
        ("00-09", "Bench Filler One", "RB", 20.0), ("00-10", "Bench Filler Two", "RB", 15.0),
        ("00-11", "Bench Filler Three", "RB", 10.0), ("00-12", "Bench Filler Four", "RB", 8.0),
        ("00-13", "Bench Filler Five", "RB", 5.0),
        ("00-14", "Backup QB One", "QB", 28.1), ("00-15", "Backup QB Two", "QB", 24.8),
        ("00-16", "The Defense", "DST", 24.6), ("00-17", "The Kicker", "K", 6.7),
    )
    my_numbers = _my_pick_numbers(13, LEAGUE)
    my_ids = [f"00-{index:02d}" for index in range(1, 14)]

    payload = []
    for overall in range(1, max(my_numbers) + 1):
        if overall in my_numbers:
            payload.append(sleeper_pick(my_ids[my_numbers.index(overall)], overall, LEAGUE["roster_id"]))
        else:
            # Somebody else's pick — a unique, off-board player, so it neither collides with a
            # real player nor gets deduplicated away by `ingest_picks`.
            payload.append(sleeper_pick(f"filler-{overall}", overall, ANOTHER_ROSTER))

    out = screen(context(the_board), payload)

    top = best_available(out)
    assert "The Defense" in top or "The Kicker" in top
    assert "Backup QB" not in out
