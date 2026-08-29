"""Issue #40: what the plan table records about the spread behind each plan's mean.

The plan table has always said how many drafts a plan was simulated in and what it averaged over
them, and nothing about how much those drafts disagreed. That makes a gap between two well-scoring
openings unreadable: twenty points apart could be a real difference or could be the same number
twice with noise on it, and there was no way to tell from the table which.

These assert what comes *out* of the summary for a given set of simulated drafts — the columns the
warehouse table is written from — never how the numbers are arrived at. Fixtures are a handful of
trials with values chosen so every expected mean, standard deviation and standard error can be
done on paper.

## Why the standard error and not something cleverer

Every plan meets the identical room within a trial, so the honest comparison between two of them is
paired, and a paired difference is measurably tighter than either plan's own standard error
suggests — the two leading openings correlate at about 0.7 across rooms. No single number on a row
can carry that, because it is a property of the *pair*. So the row carries the plain standard error
of its own mean, which combines across two plans as the root of the sum of squares and is therefore
*conservative* under positive correlation: a gap that clears it is real, and a gap that does not
may still be.

The standard deviation is kept beside it because it answers a different question that a mean cannot
— how much a plan's outcome swings room to room, which is risk rather than precision.
"""

import math

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.draft.composition import composition_guidance
from src.gold.draft_plan import _PLAN_COLUMNS, _summarize_plans

SEAT = 3
ANOTHER_SEAT = 7

# A twelve-team league, so "top third" is a finish of four or better and the rates below are
# workable by hand.
TEAMS = 12

# Nine trials whose deviations from their mean are four at -1, one at 0 and four at +1, scaled to
# whatever spread a case wants. At this size the sum of squared deviations is 8 over 8 degrees of
# freedom, so a scale of s gives a standard deviation of exactly s and a standard error of s/3.
NINE = [-1, -1, -1, -1, 0, 1, 1, 1, 1]


def trials(draft_slot, plan, starter_points, points_vs_field=None, finish_rank=None):
    """One row per simulated draft, as the simulation hands them to the summary."""
    points_vs_field = points_vs_field or [value - 1000 for value in starter_points]
    finish_rank = finish_rank or [1] * len(starter_points)
    return pd.DataFrame({
        "draft_slot": draft_slot,
        "plan": plan,
        "starter_points": starter_points,
        "points_vs_field": points_vs_field,
        "finish_rank": finish_rank,
    })


def spread(mean, scale):
    """Nine trials with a mean of `mean` and a standard deviation of exactly `scale`."""
    return [mean + scale * deviation for deviation in NINE]


def row(summary, plan, draft_slot=SEAT):
    (found,) = summary[
        (summary["plan"] == plan) & (summary["draft_slot"] == draft_slot)
    ].to_dict("records")
    return found


def test_every_plan_row_carries_dispersion_beside_its_trial_count():
    results = pd.concat([
        trials(SEAT, "2QB3RB", spread(1700, 3)),
        trials(SEAT, "2QB2RB1TE", spread(1720, 6)),
    ], ignore_index=True)

    summary = _summarize_plans(results, TEAMS)

    measures = [
        "starter_points_stdev", "starter_points_stderr",
        "points_vs_field_stdev", "points_vs_field_stderr",
    ]
    for measure in measures:
        assert measure in summary.columns
        assert summary[measure].notna().all()
        # The table the warehouse is written from, not just the frame on the way to it.
        assert measure in _PLAN_COLUMNS


def test_the_measure_is_the_spread_of_that_plans_own_trials():
    results = trials(
        SEAT, "2QB3RB",
        starter_points=[98, 100, 102],
        points_vs_field=[5, 10, 15],
        finish_rank=[1, 2, 3],
    )

    plan = row(_summarize_plans(results, TEAMS), "2QB3RB")

    assert plan["trials"] == 3
    assert plan["starter_points_stdev"] == pytest.approx(2.0)
    assert plan["starter_points_stderr"] == pytest.approx(2.0 / math.sqrt(3))
    assert plan["points_vs_field_stdev"] == pytest.approx(5.0)
    assert plan["points_vs_field_stderr"] == pytest.approx(5.0 / math.sqrt(3))


def test_two_plans_with_the_same_mean_are_told_apart_by_their_spread():
    results = pd.concat([
        trials(SEAT, "steady", spread(1700, 2)),
        trials(SEAT, "swingy", spread(1700, 20)),
    ], ignore_index=True)

    summary = _summarize_plans(results, TEAMS)
    steady, swingy = row(summary, "steady"), row(summary, "swingy")

    assert steady["starter_points"] == pytest.approx(swingy["starter_points"])
    assert steady["starter_points_stdev"] == pytest.approx(2.0)
    assert swingy["starter_points_stdev"] == pytest.approx(20.0)
    assert steady["starter_points_stderr"] == pytest.approx(2.0 / 3)
    assert swingy["starter_points_stderr"] == pytest.approx(20.0 / 3)


def test_each_seat_reports_its_own_spread():
    results = pd.concat([
        trials(SEAT, "2QB3RB", spread(1700, 3)),
        trials(ANOTHER_SEAT, "2QB3RB", spread(1700, 12)),
    ], ignore_index=True)

    summary = _summarize_plans(results, TEAMS)

    assert row(summary, "2QB3RB")["starter_points_stdev"] == pytest.approx(3.0)
    assert row(summary, "2QB3RB", ANOTHER_SEAT)["starter_points_stdev"] == pytest.approx(12.0)


def test_trials_that_all_scored_the_same_report_zero_dispersion():
    results = trials(SEAT, "2QB3RB", starter_points=[1700, 1700, 1700])

    plan = row(_summarize_plans(results, TEAMS), "2QB3RB")

    assert plan["starter_points_stdev"] == 0.0
    assert plan["starter_points_stderr"] == 0.0
    assert plan["points_vs_field_stdev"] == 0.0
    assert plan["points_vs_field_stderr"] == 0.0


def test_a_plan_simulated_once_reports_no_dispersion_rather_than_zero():
    results = trials(SEAT, "2QB3RB", starter_points=[1700])

    plan = row(_summarize_plans(results, TEAMS), "2QB3RB")

    assert plan["trials"] == 1
    assert math.isnan(plan["starter_points_stdev"])
    assert math.isnan(plan["starter_points_stderr"])
    assert math.isnan(plan["points_vs_field_stdev"])
    assert math.isnan(plan["points_vs_field_stderr"])


def test_a_real_gap_and_a_noise_gap_read_differently_from_the_recorded_numbers():
    """The point of the whole ticket: two means and their standard errors settle the question.

    Every plan here has a standard error of exactly one point. `apart` beats `middle` by ten, which
    is seven combined standard errors; `alike` trails it by half a point, which is a third of one.
    """
    results = pd.concat([
        trials(SEAT, "apart", spread(1710, 3)),
        trials(SEAT, "middle", spread(1700, 3)),
        trials(SEAT, "alike", spread(1699.5, 3)),
    ], ignore_index=True)

    summary = _summarize_plans(results, TEAMS)
    apart, middle, alike = row(summary, "apart"), row(summary, "middle"), row(summary, "alike")

    def gaps(better, worse):
        difference = better["starter_points"] - worse["starter_points"]
        combined = math.hypot(better["starter_points_stderr"], worse["starter_points_stderr"])
        return difference / combined

    assert gaps(apart, middle) > 3
    assert gaps(middle, alike) < 1


def test_the_existing_columns_keep_their_meaning():
    results = trials(
        SEAT, "2QB3RB",
        starter_points=[1690, 1700, 1710],
        points_vs_field=[90, 100, 110],
        finish_rank=[1, 2, 5],
    )

    plan = row(_summarize_plans(results, TEAMS), "2QB3RB")

    assert plan["trials"] == 3
    assert plan["starter_points"] == pytest.approx(1700.0)
    assert plan["points_vs_field"] == pytest.approx(100.0)
    assert plan["finish_rank"] == pytest.approx(8 / 3)
    assert plan["win_rate"] == pytest.approx(1 / 3)
    # Top third of twelve is a finish of four or better: two of the three drafts.
    assert plan["top_third_rate"] == pytest.approx(2 / 3)


def test_there_is_still_one_row_per_seat_and_plan():
    results = pd.concat([
        trials(seat, plan, spread(1700, 3))
        for seat in (SEAT, ANOTHER_SEAT)
        for plan in ("2QB3RB", "2QB2RB1TE")
    ], ignore_index=True)

    summary = _summarize_plans(results, TEAMS)

    assert len(summary) == 4
    assert not summary.duplicated(subset=["draft_slot", "plan"]).any()


def test_composition_guidance_reads_the_same_frame_the_same_way():
    """The consumer that was already reading this table is not moved by the new columns."""
    plans = pd.DataFrame([
        {"draft_slot": SEAT, "plan": "2QB2RB1TE", "trials": 300,
         "points_vs_field": 120.0, "win_rate": 0.44},
        {"draft_slot": SEAT, "plan": "1QB2RB1WR1TE", "trials": 300,
         "points_vs_field": 40.0, "win_rate": 0.22},
        {"draft_slot": SEAT, "plan": "0QB2RB2WR1TE", "trials": 300,
         "points_vs_field": -30.0, "win_rate": 0.09},
    ])
    with_dispersion = plans.assign(
        starter_points_stdev=[60.0, 55.0, 58.0],
        starter_points_stderr=[3.5, 3.2, 3.3],
        points_vs_field_stdev=[52.0, 49.0, 51.0],
        points_vs_field_stderr=[3.0, 2.8, 2.9],
    )
    picks = {"mine": [{"position": "QB"}, {"position": "RB"}]}
    league = {"seat": SEAT}

    before = composition_guidance(plans, picks, league)
    after = composition_guidance(with_dispersion, picks, league)

    assert_frame_equal(before.pop("bands"), after.pop("bands"))
    assert before == after
