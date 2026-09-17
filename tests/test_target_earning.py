"""Blend prior-season shares into a role-aware projected target share — issue #67.

Targets per game is a good descriptor and a mediocre predictor (see the issue's motivation): the
median top-12 WR season already carries a WR1-sized workload, so carrying last year's raw share
forward mostly restates whoever already had one. `project_target_share` instead blends two prior
inputs (`target_share_prior`, `air_yards_share_prior`) and then gates the result by this year's
depth-chart role in both directions: a demoted player's inflated prior share is pulled down to what
a non-starting role can actually produce, and a promoted player's thin or missing prior history is
raised to what a confirmed starting role actually produces — the role gate has to cut both ways, or
the promoted case this table exists for (a backup or rookie who wins a job) gets none of the model's
benefit and passes through no differently than the naive carry-forward it replaces.

Full test list (see the PR body for the same list with rationale):
 1. `_blend_prior_shares` averages the two shares (target-share-weighted) when both are present
 2. `_blend_prior_shares` falls back to `target_share_prior` alone when `air_yards_share_prior` is
    missing, rather than diluting it with a weight that has nothing to average against
 3. `_blend_prior_shares` falls back to `air_yards_share_prior` alone when `target_share_prior` is
    missing
 4. `_blend_prior_shares` is NaN when both inputs are missing
 5. `_apply_role_adjustment` leaves a confirmed starter's blended share unchanged when it already
    sits between the floor and the ceiling — a real starting role isn't second-guessed by last
    year's number
 6. `_apply_role_adjustment` caps a non-starter's blended share down to the ceiling
 7. `_apply_role_adjustment` leaves a non-starter's blended share unchanged when it already sits
    below the ceiling — the cap never inflates a legitimately small share up to it
 8. `_apply_role_adjustment` treats a missing depth-chart role (no week-1 row at all) as a
    non-starter, since an unconfirmed role is not evidence of a starting job
 9. `_apply_role_adjustment` raises a confirmed starter's blended share up to the floor when it sits
    below it — the case that makes this a role *model* rather than a one-directional cap
10. `_apply_role_adjustment` floors a confirmed starter with no blended share at all (a rookie with
    no prior-season history to blend from) to the floor, rather than leaving him NaN
11. `project_target_share` end to end: a demoted player (high blended share carried over from last
    year, `depth_role=False` this year) is pulled down to the ceiling — the exact case a flat
    targets-per-game carry-forward would miss
12. `project_target_share` end to end: a promoted player with a thin prior history but
    `depth_role=True` this year is raised to the floor rather than passed through at face value
13. `project_target_share` end to end: a steady returning starter, comfortably between the floor and
    the ceiling either way, passes through unchanged — the no-op case
"""

import numpy as np
import pandas as pd
import pytest

from src.gold.target_earning import _apply_role_adjustment, _blend_prior_shares, project_target_share


# 1. Both shares present: the blend is the target-share-weighted average, not a plain 50/50 split.
def test_blend_averages_both_shares_when_present():
    target_share = pd.Series([0.20])
    air_yards_share = pd.Series([0.30])

    blended = _blend_prior_shares(target_share, air_yards_share)

    assert blended.iloc[0] == pytest.approx(0.7 * 0.20 + 0.3 * 0.30)


# 2. air_yards_share_prior missing: fall back to target_share_prior alone rather than averaging it
#    against a weight with nothing behind it.
def test_blend_falls_back_to_target_share_when_air_yards_share_missing():
    target_share = pd.Series([0.18])
    air_yards_share = pd.Series([np.nan])

    blended = _blend_prior_shares(target_share, air_yards_share)

    assert blended.iloc[0] == 0.18


# 3. target_share_prior missing: fall back to air_yards_share_prior alone.
def test_blend_falls_back_to_air_yards_share_when_target_share_missing():
    target_share = pd.Series([np.nan])
    air_yards_share = pd.Series([0.25])

    blended = _blend_prior_shares(target_share, air_yards_share)

    assert blended.iloc[0] == 0.25


# 4. Both missing: nothing to blend, so the result is NaN rather than a fabricated zero.
def test_blend_is_nan_when_both_shares_missing():
    target_share = pd.Series([np.nan])
    air_yards_share = pd.Series([np.nan])

    blended = _blend_prior_shares(target_share, air_yards_share)

    assert pd.isna(blended.iloc[0])


# 5. A confirmed starter's blended share between the floor and ceiling passes through unchanged.
def test_role_adjustment_leaves_starters_unchanged_between_floor_and_ceiling():
    blended = pd.Series([0.20])
    depth_role = pd.Series([True])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.20


# 6. A non-starter's blended share above the ceiling is pulled down to it.
def test_role_adjustment_caps_non_starters_above_ceiling():
    blended = pd.Series([0.30])
    depth_role = pd.Series([False])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.15


# 7. A non-starter's blended share already below the ceiling is left as-is, not bumped up to it.
def test_role_adjustment_leaves_non_starters_below_ceiling_unchanged():
    blended = pd.Series([0.05])
    depth_role = pd.Series([False])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.05


# 8. No week-1 depth-chart row at all (depth_role is NaN) is treated as a non-starter, and therefore
#    capped rather than floored.
def test_role_adjustment_treats_missing_role_as_non_starter():
    blended = pd.Series([0.30])
    depth_role = pd.Series([np.nan])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.15


# 9. A confirmed starter's blended share below the floor is raised to it — the floor is a real
#    adjustment, not just documentation of a case that never fires.
def test_role_adjustment_raises_starters_below_floor():
    blended = pd.Series([0.03])
    depth_role = pd.Series([True])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.12


# 10. A confirmed starter with no blended share at all (e.g. a rookie with no weekly_stats history)
#     is floored rather than left NaN — role is evidence on its own, not just a modifier of history.
def test_role_adjustment_floors_starters_with_no_blended_share():
    blended = pd.Series([np.nan])
    depth_role = pd.Series([True])

    adjusted = _apply_role_adjustment(blended, depth_role)

    assert adjusted.iloc[0] == 0.12


def _frame(target_share_prior, air_yards_share_prior, depth_role) -> pd.DataFrame:
    return pd.DataFrame({
        "target_share_prior": [target_share_prior],
        "air_yards_share_prior": [air_yards_share_prior],
        "depth_role": [depth_role],
    })


# 11. A demoted player: a strong prior share, but this year's depth chart says he isn't a starter.
#     The ceiling pulls him down instead of carrying his old share forward at face value.
def test_project_target_share_caps_a_demoted_players_carried_over_share():
    frame = _frame(target_share_prior=0.28, air_yards_share_prior=0.32, depth_role=False)

    projected = project_target_share(frame)

    assert projected.iloc[0] == 0.15


# 12. A promoted player: little prior history, but this year's depth chart says he starts. The
#     floor raises him to what a confirmed starter actually earns, rather than carrying his thin
#     history forward at face value.
def test_project_target_share_floors_a_promoted_players_thin_history():
    frame = _frame(target_share_prior=np.nan, air_yards_share_prior=0.04, depth_role=True)

    projected = project_target_share(frame)

    assert projected.iloc[0] == 0.12


# 13. A steady returning starter sits between the floor and the ceiling either way — the no-op case.
def test_project_target_share_passes_through_a_steady_returning_starter():
    frame = _frame(target_share_prior=0.19, air_yards_share_prior=0.17, depth_role=True)

    projected = project_target_share(frame)

    assert projected.iloc[0] == pytest.approx(0.7 * 0.19 + 0.3 * 0.17)
