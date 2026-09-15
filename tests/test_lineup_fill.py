"""Assigning named players to a league's starting slots — issue #87, under the lineup-optimizer
epic (#86).

`draft_strategy.py`'s `_lineup`/`_score_lineup` already prove the greedy fill is optimal — fill the
narrowest eligibility first, since superflex eligibility is a superset of flex eligibility, which is
a superset of a dedicated slot — but only on aggregate position *counts*. `fill_lineup` is the same
algorithm generalized to *named* players, so a real roster (not just a simulated draft's point
totals) can be seated.

Every fixture here is a small hand-built list of `(player_id, position, points)` tuples — no
warehouse, no DataFrame — so each expected assignment can be checked by hand.
"""

from src.gold.lineup_fill import fill_lineup


# 1. A league with no flex or superflex fills only its dedicated slots, one player per slot, best
#    points first.
def test_dedicated_slots_fill_best_player_first():
    players = [
        ("rb1", "RB", 10.0),
        ("rb2", "RB", 20.0),
        ("wr1", "WR", 15.0),
    ]

    assignment, total = fill_lineup(players, slots={"RB": 1, "WR": 1}, flex=0, superflex=0)

    assert assignment == {"RB1": "rb2", "WR1": "wr1"}
    assert total == 35.0


# 2. A league with no superflex: FLEX draws from whichever RB/WR/TE is left over after dedicated
#    slots are filled, not from the whole pool.
def test_flex_fills_from_leftover_rb_wr_te_only():
    players = [
        ("rb1", "RB", 20.0),
        ("rb2", "RB", 18.0),
        ("wr1", "WR", 12.0),
        ("te1", "TE", 8.0),
        ("qb1", "QB", 30.0),
    ]

    assignment, total = fill_lineup(
        players, slots={"QB": 1, "RB": 1, "WR": 1, "TE": 1}, flex=1, superflex=0,
    )

    # rb2 (18) beats te1's leftover (8) for the one FLEX spot; qb1 is not flex-eligible at all.
    assert assignment == {"QB1": "qb1", "RB1": "rb1", "WR1": "wr1", "TE1": "te1", "FLEX1": "rb2"}
    assert total == 20.0 + 18.0 + 12.0 + 8.0 + 30.0


# 3. A superflex league: the superflex slot draws from leftover RB/WR/TE *and* leftover QB, since
#    superflex eligibility is a superset of flex eligibility (the docstring's proof).
def test_superflex_adds_leftover_qb_to_the_flex_pool():
    players = [
        ("qb1", "QB", 25.0),
        ("qb2", "QB", 22.0),
        ("rb1", "RB", 14.0),
        ("wr1", "WR", 10.0),
    ]

    assignment, total = fill_lineup(
        players, slots={"QB": 1, "RB": 1, "WR": 1}, flex=1, superflex=1,
    )

    # After QB1=qb1, RB1=rb1, WR1=wr1: leftover is qb2 (22) and nothing else RB/WR/TE-eligible.
    # FLEX has no RB/WR/TE leftover to take, so it comes up empty; SUPERFLEX takes qb2.
    assert assignment == {
        "QB1": "qb1", "RB1": "rb1", "WR1": "wr1", "FLEX1": None, "SUPERFLEX1": "qb2",
    }
    assert total == 25.0 + 14.0 + 10.0 + 22.0


# 4. Tied points break deterministically on player_id, regardless of input order, so the same
#    roster always seats the same lineup.
def test_tied_points_break_on_player_id():
    players = [("z_back", "RB", 10.0), ("a_back", "RB", 10.0)]

    assignment, _ = fill_lineup(players, slots={"RB": 1}, flex=0, superflex=0)

    assert assignment == {"RB1": "a_back"}


# 5. A roster too thin to fill every slot leaves the slot explicitly None rather than raising or
#    reusing a stale value — the acceptance criterion this ticket calls out by name.
def test_too_few_eligible_players_leaves_slot_empty():
    players = [("rb1", "RB", 10.0)]

    assignment, total = fill_lineup(players, slots={"RB": 1, "WR": 1}, flex=1, superflex=0)

    assert assignment == {"RB1": "rb1", "WR1": None, "FLEX1": None}
    assert total == 10.0


# 6. The ESPN league's punter slot is just another dedicated position — not flex-eligible, so a
#    high-scoring punter never leaks into the FLEX pool.
def test_punter_slot_is_dedicated_not_flex_eligible():
    players = [
        ("p1", "P", 12.0),
        ("rb1", "RB", 5.0),
    ]

    assignment, total = fill_lineup(players, slots={"P": 1, "RB": 1}, flex=1, superflex=0)

    assert assignment == {"P1": "p1", "RB1": "rb1", "FLEX1": None}
    assert total == 17.0
