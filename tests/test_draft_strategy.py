"""`_score_lineup`'s behaviour after issue #87 moved its fill logic into `lineup_fill.fill_lineup`.

This is the regression check the ticket calls out by name: `_score_lineup` still takes aggregate
per-position point *counts* (a simulated draft never resolves player identity), so these tests
pin down that the totals it produces are unchanged now that the fill itself is delegated to the
generalized, named-player function `lineup_fill.py` already has its own tests for.
"""

from src.gold.draft_strategy import _score_lineup


def _lineup(slots: dict, flex: int = 0, superflex: int = 0) -> dict:
    return {"slots": slots, "flex": flex, "superflex": superflex}


# 1. No flex or superflex: each dedicated slot takes its own position's best scorers only.
def test_dedicated_only_lineup_sums_best_per_position():
    # _POSITIONS order is QB, RB, WR, TE (points_over_replacement._SKILL_POSITIONS).
    counts_pts = [[20.0, 10.0], [18.0, 12.0], [15.0], [9.0]]
    lineup = _lineup({"QB": 1, "RB": 1, "WR": 1, "TE": 1})

    assert _score_lineup(counts_pts, lineup) == 20.0 + 18.0 + 15.0 + 9.0


# 2. A 1QB league's FLEX draws from leftover RB/WR/TE only, best leftover first.
def test_flex_pulls_best_leftover_skill_player():
    counts_pts = [[25.0], [18.0, 12.0], [15.0, 7.0], [9.0]]
    lineup = _lineup({"QB": 1, "RB": 1, "WR": 1, "TE": 1}, flex=1)

    # Leftover after dedicated slots: RB 12.0, WR 7.0 — RB's 12.0 wins the one FLEX spot.
    assert _score_lineup(counts_pts, lineup) == 25.0 + 18.0 + 15.0 + 9.0 + 12.0


# 3. A superflex league's extra slot draws from leftover RB/WR/TE *and* leftover QB.
def test_superflex_pulls_leftover_qb_into_the_pool():
    counts_pts = [[25.0, 22.0], [18.0], [15.0], [9.0]]
    lineup = _lineup({"QB": 1, "RB": 1, "WR": 1, "TE": 1}, flex=1, superflex=1)

    # No leftover RB/WR/TE at all, so FLEX comes up empty; SUPERFLEX takes leftover QB (22.0).
    assert _score_lineup(counts_pts, lineup) == 25.0 + 18.0 + 15.0 + 9.0 + 22.0


# 4. Tied points at a dedicated slot still sum to the same total regardless of which tied player
#    is "seated" — the total is the regression check, not the identity.
def test_tied_points_sum_the_same_total():
    counts_pts = [[10.0, 10.0], [], [], []]
    lineup = _lineup({"QB": 1})

    assert _score_lineup(counts_pts, lineup) == 10.0


# 5. A roster too thin to fill every slot contributes zero for the slot it can't fill, rather than
#    raising.
def test_too_few_players_scores_only_the_filled_slots():
    counts_pts = [[20.0], [], [], []]
    lineup = _lineup({"QB": 1, "RB": 1}, flex=1)

    assert _score_lineup(counts_pts, lineup) == 20.0
