"""Pairing each waiver add with the drop it implies, ranked by net change to the starting lineup —
issue #172, under the waiver epic (#110).

Every fixture is hand-built and small, in the same spirit as `test_lineup_fill.py` and
`test_optimal_lineup.py`: no warehouse, no DataFrame read from disk, so each expected value can be
checked by hand.
"""

import pandas as pd

from src.gold.waiver_pairs import net_lineup_gain, roster_pool


def roster(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "position"])


# 1. A roster row with a resolved player_id and a points entry this week becomes a candidate.
def test_resolved_player_with_points_becomes_a_candidate():
    pool = roster_pool(
        roster({"player_id": "rb1", "position": "RB"}), {"rb1": 12.5},
    )

    assert pool == [("rb1", "RB", 12.5)]


# 2. A roster row with no resolved player_id (the identity crosswalk never caught up) is excluded
#    — there is no number to seat him with.
def test_unresolved_player_is_excluded():
    pool = roster_pool(
        roster({"player_id": None, "position": "WR"}), {},
    )

    assert pool == []


# 3. A resolved player with no weekly_projections row this week (a bye) is excluded the same way —
#    "nothing to start him on" reads identically whether the gap is identity or the week's number.
def test_resolved_player_with_no_points_this_week_is_excluded():
    pool = roster_pool(
        roster({"player_id": "rb1", "position": "RB"}), {},
    )

    assert pool == []


# 4. Sleeper's own raw label for a team defense, 'DEF', is normalized to 'DST' — the identical
#    quirk drop_candidates.py already normalizes for its replacement-level join, generalized here
#    to the eligibility bucket fill_lineup keys its dedicated slots on. Without it, a Sleeper
#    defense's own slot would come back empty every week regardless of his real points.
def test_sleeper_def_label_is_normalized_to_dst():
    pool = roster_pool(
        roster({"player_id": "DEN", "position": "DEF"}), {"DEN": 7.9},
    )

    assert pool == [("DEN", "DST", 7.9)]


# 5. Any other position passes through unchanged — only Sleeper's defense label is special-cased.
def test_other_positions_pass_through_unchanged():
    pool = roster_pool(
        roster({"player_id": "wr1", "position": "WR"}), {"wr1": 9.0},
    )

    assert pool == [("wr1", "WR", 9.0)]


# 6. The epic's own motivating example: a bench upgrade that never enters the lineup is worth
#    approximately nothing. Three better starters never leave the lineup, so swapping the bench
#    WR4 for a marginally-better free agent changes nothing about the total.
def test_bench_upgrade_that_never_starts_has_zero_gain():
    baseline_candidates = [
        ("wr1", "WR", 20.0), ("wr2", "WR", 18.0), ("wr3", "WR", 16.0), ("wr4", "WR", 5.0),
    ]
    slots = {"WR": 3}
    _, baseline_total = _fill(baseline_candidates, slots, flex=0, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, slots, flex=0, superflex=0,
        drop_player_id="wr4", add_player=("fa1", "WR", 6.0),
    )

    assert gain == 0.0


# 7. A free agent who actually beats the current worst starter improves the total by exactly the
#    difference — the literal fill_lineup output, not an estimate.
def test_free_agent_beating_the_worst_starter_gains_the_difference():
    baseline_candidates = [("qb1", "QB", 10.0)]
    slots = {"QB": 1}
    _, baseline_total = _fill(baseline_candidates, slots, flex=0, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, slots, flex=0, superflex=0,
        drop_player_id="qb1", add_player=("qb2", "QB", 15.0),
    )

    assert gain == 5.0


# 8. A free agent with no resolved identity or no weekly points has nothing to compute a swap
#    with, so the pair's gain is null rather than guessed at.
def test_unresolved_free_agent_gives_null_gain():
    baseline_candidates = [("qb1", "QB", 10.0)]
    _, baseline_total = _fill(baseline_candidates, {"QB": 1}, flex=0, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, {"QB": 1}, flex=0, superflex=0,
        drop_player_id="qb1", add_player=None,
    )

    assert gain is None


# 9. A drop candidate who was never actually a fill_lineup candidate (unresolved identity, or a
#    bye — roster_pool already excluded him from baseline_candidates) makes the "drop" a no-op:
#    the swap becomes a pure addition, which is exactly correct since he wasn't contributing to
#    the lineup total to begin with.
def test_drop_not_in_baseline_pool_is_a_pure_addition():
    baseline_candidates = [("qb1", "QB", 10.0)]
    slots = {"QB": 1, "WR": 1}
    _, baseline_total = _fill(baseline_candidates, slots, flex=0, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, slots, flex=0, superflex=0,
        drop_player_id="bye_week_wr", add_player=("fa1", "WR", 9.0),
    )

    # WR1 was empty before (no WR candidate at all) and is now filled by the free agent.
    assert gain == 9.0


# 10. Superflex/flex eligibility is exactly what fill_lineup already resolves — no separate
#     eligibility check here. Dropping the RB filling FLEX for a free-agent WR who outscores him
#     still seats through the identical flex pool fill_lineup itself draws from. `slots` names
#     every dedicated position with a real count (0 for RB/WR, matching `_league_slots`'s own
#     shape) — `fill_lineup` only seeds a position's FLEX-leftover pool from a key present in
#     `slots` at all, the same contract `test_lineup_fill.py` observes.
def test_flex_eligibility_comes_from_fill_lineup_alone():
    baseline_candidates = [("qb1", "QB", 10.0), ("rb1", "RB", 8.0)]
    slots = {"QB": 1, "RB": 0, "WR": 0}
    _, baseline_total = _fill(baseline_candidates, slots, flex=1, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, slots, flex=1, superflex=0,
        drop_player_id="rb1", add_player=("wr1", "WR", 9.0),
    )

    assert gain == 1.0


# 11. A harmful pair still reports its real (negative) number — no floor at zero, matching the
#     ticket's "no materiality threshold" rule.
def test_harmful_pair_reports_a_negative_gain():
    baseline_candidates = [("rb1", "RB", 20.0)]
    slots = {"RB": 1}
    _, baseline_total = _fill(baseline_candidates, slots, flex=0, superflex=0)

    gain = net_lineup_gain(
        baseline_candidates, baseline_total, slots, flex=0, superflex=0,
        drop_player_id="rb1", add_player=("fa1", "RB", 4.0),
    )

    assert gain == -16.0


def _fill(players, slots, flex, superflex):
    from src.gold.lineup_fill import fill_lineup

    return fill_lineup(players, slots, flex, superflex)
