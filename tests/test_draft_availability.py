"""Issue #72: what the availability model may and may not call impossible.

`_probability_available` prices one overall pick for every player carrying a market ADP, and the
table built from it is the only thing on draft night that says how long a player will last. These
assert what comes *out* of it for a given board — never how the tail is arrived at — with numbers
chosen so every expectation can be checked against a normal table by hand.

## Why the observed range was never a bound

`consensus_adp` is a blend of several sources. `adp_stdev`, `adp_high` and `adp_low` come from
whichever FFC board feeds that row's format, and `adp_consensus`'s own docstring says so. The two
describe different populations, so clipping the blended centre to one source's observed extremes
is not a conservative choice — it is a claim about a distribution nobody measured.

It showed as flatly contradictory rows. On the ESPN board 26 of 335 players carry a
`consensus_adp` beyond `adp_low` entirely: Keenan Allen's blend puts him at 199.4 while FFC's
range stops at 152, so from pick 153 the mask called him certainly gone while his own distribution
had him 99.93% available. Sleeper carries no such row, so the live board never saw it — but once
`waiting` reads a dropped row as *gone* rather than as unknown (#70), a wrong zero stops being a
hidden row and becomes a confident wrong answer at the top of the board.

## What is deliberately still clipped

The mask that pins a player to 1.0 before his earliest observed pick stays. Measured across both
leagues, no player has an `adp_high` later than his own `consensus_adp`, so unlike the other end it
never contradicts the distribution it is applied to. One thing changes here, not two.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.gold.draft_plan import _MINIMUM_STDEV, _probability_available


def board(**overrides) -> pd.DataFrame:
    """One player with a market price and an observed range around it."""
    defaults = {
        "player_name": "A Receiver",
        "consensus_adp": 50.0,
        "adp_stdev": 10.0,
        "adp_high": 30.0,
        "adp_low": 70.0,
    }
    return pd.DataFrame([{**defaults, **overrides}])


def only(probabilities: pd.Series) -> float:
    """The single player's probability, so a claim is about that row alone."""
    assert len(probabilities) == 1
    return float(probabilities.iloc[0])


def test_a_pick_past_the_last_one_ever_observed_is_unlikely_not_impossible():
    """Nobody has been seen going this late, which is not the same as nobody ever will.

    `adp_low` is the maximum of a finite sample. A sample maximum can only move outward as more
    drafts are observed, so hardening it into a bound assigns probability zero to a region that was
    never measured as empty — only as unvisited.
    """
    # Centre 50, spread 10, never observed later than 70. At pick 71 the tail is 2.05 sigma out.
    probability = only(_probability_available(board(), 71))

    assert probability > 0.0
    assert probability == pytest.approx(stats.norm.sf(70.5, 50.0, 10.0))
    assert probability == pytest.approx(0.0202, abs=1e-4)


def test_a_consensus_beyond_the_observed_range_is_not_called_gone():
    """The row that justifies the change, taken off the ESPN board as it stands.

    `consensus_adp` is blended across sources and the range comes from one of them, so the two can
    disagree outright. Keenan Allen's blend has him going at 199 while FFC never saw him last past
    152 — and the mask believed the 152.
    """
    keenan = board(consensus_adp=199.4, adp_stdev=12.2, adp_high=101.0, adp_low=152.0)

    # One pick past the last FFC ever saw him go, and 3.9 sigma before his blended centre.
    probability = only(_probability_available(keenan, 153))

    assert probability > 0.99
    assert probability == pytest.approx(stats.norm.sf(152.5, 199.4, 12.2))


def test_the_probability_never_rises_as_the_draft_goes_on():
    """Availability is monotone — once a player is gone he stays gone.

    `waiting` divides one of these by another to get a conditional and clamps the result at 1.0. A
    ratio that could legitimately exceed one would mean the clamp was hiding something rather than
    guarding against it, so the property it rests on is worth pinning here rather than assuming.
    """
    the_board = board()
    probabilities = [only(_probability_available(the_board, pick)) for pick in range(1, 121)]

    assert probabilities == sorted(probabilities, reverse=True)
    assert probabilities[0] == 1.0
    assert probabilities[-1] < 1e-9


def test_a_pick_inside_the_observed_range_is_untouched():
    """This is a tail fix. The body of the distribution must not move at all."""
    the_board = board()

    for pick, expected in [(40, stats.norm.sf(39.5, 50.0, 10.0)),
                           (50, stats.norm.sf(49.5, 50.0, 10.0)),
                           (65, stats.norm.sf(64.5, 50.0, 10.0))]:
        assert only(_probability_available(the_board, pick)) == pytest.approx(expected)


def test_before_the_earliest_pick_he_has_ever_gone_he_is_certain():
    """The other clip stays: no player has an `adp_high` later than his own consensus, so unlike
    `adp_low` it never contradicts the distribution it overrides."""
    the_board = board()

    assert only(_probability_available(the_board, 20)) == 1.0
    # The boundary itself is included — he has been seen going *at* this pick, not before it.
    assert only(_probability_available(the_board, 30)) == 1.0
    assert only(_probability_available(the_board, 31)) < 1.0


def test_a_player_with_no_published_spread_is_floored_rather_than_pinned():
    """FantasyPros' blended ADP publishes no range, so the spread has to come from somewhere.

    A missing or implausibly tight spread is floored rather than taken at face value: a player with
    a stdev of zero would be certain to go at exactly his ADP and certain to be gone one pick
    later, which no draft has ever looked like.
    """
    expected = stats.norm.sf(51.5, 50.0, _MINIMUM_STDEV)

    assert only(_probability_available(board(adp_stdev=None), 52)) == pytest.approx(expected)
    assert only(_probability_available(board(adp_stdev=np.nan), 52)) == pytest.approx(expected)
    assert only(_probability_available(board(adp_stdev=0.2), 52)) == pytest.approx(expected)
