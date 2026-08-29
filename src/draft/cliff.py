"""How many are left at each position before the next real drop.

Pure, like everything in this package bar `live`: one frame in, one frame out, nothing handed in
modified. No network, no warehouse connection, no printing.

## The question the ranking cannot answer

Cost of waiting already prices the drop behind one player — that is half of what it multiplies,
and the reason a cliff position is expensive to pass on even when the player will probably last.
But it hands back one number per player, and a drafter reading the top of that list sees whichever
positions happen to rank highest across the whole board at once. "Are there six more backs like
this one, or is this the last of them" is a question nothing on the screen answers.

That question is about a position rather than a player, so it needs its own answer rather than
another column on a row.

## What counts as a cliff

The largest drop in points over replacement between two consecutive players still available at the
position. How many sit above it is the answer: three left before it gets materially worse, or
eleven. Both numbers go on screen, because either alone is unreadable — three left is comfortable
behind a four-point step and an emergency behind a forty-point one.

This is arithmetic over frozen inputs, in exactly the sense `waiting` is. Every value comes back
from the board as the rebuild priced it; nothing here refits, rescales or reprices anything.

## Two consequences worth stating, because they look like special cases and are not

**Below the last player at a position is replacement level.** Points over replacement is measured
against a freely available player, so the drop below the last quarterback on the board is his own
points over replacement and nothing has to be invented to say so. `waiting._cost_within_position`
floors its expectation at zero for the same reason. It makes the last startable player at a
position the biggest cliff on the board, which is correct and is exactly when a drafter most needs
telling.

**A cliff is only looked for among the next few.** The largest drop in the whole remaining tail of
a position is usually somewhere in the forties, and reporting it would say "39 left" — true, and
an answer to nothing being decided now. So the search stops at `LOOKAHEAD`, and the cliff reported
is the next one rather than the biggest one. Equal drops report the nearer, for the same reason.

## What this is not

Not a recommendation, and not part of the ranking. It sits beside the candidate list the way the
opening guidance does, and reorders nothing — the repo's standing preference for keeping a
metric's inputs visible rather than folding them into a single score, and the same reason a
drafter can overrule what he is shown.

Not roster-conditional either. How thin quarterback is has nothing to do with how many the drafter
already has; what a position is *worth* to a partly-filled roster is a different question and is
deliberately out of scope for this feature.
"""

import numpy as np
import pandas as pd

# How far down a position the next cliff is looked for. Roughly a round's worth of one position in
# a fourteen-team league: far enough that a real shelf two or three players away is found, near
# enough that the answer is about the pick being made. A drop past this is a fact about the fourth
# round, and the count above it would be a number nobody can act on.
LOOKAHEAD = 10

# What a cliff row carries: the position, how many are above the drop, how big the drop is, and
# how many are left at the position at all — "3 of 4" and "3 of 40" are different boards.
CLIFF_COLUMNS = ["position", "above", "drop", "remaining"]


def _cliff(values: list[float], lookahead: int) -> tuple[int, float]:
    """How many are above the next cliff at one position, and how far it drops.

    `values` is that position's remaining players in value order. The walk looks at the steps
    between the first `lookahead` of them and whoever is immediately behind — or replacement level,
    zero, when nobody is, which is what makes the last player at a position a cliff without a
    special case.
    """
    window = values[:lookahead]
    behind = values[lookahead:lookahead + 1] or [0.0]
    steps = window + behind

    drops = [steps[index] - steps[index + 1] for index in range(len(steps) - 1)]
    # `index` of the max takes the first, which is the nearer cliff when two drops are equal: the
    # near one is the one being decided, and the far one will still be there to report next tick.
    deepest = drops.index(max(drops))
    return deepest + 1, drops[deepest]


def position_cliffs(candidates: pd.DataFrame, lookahead: int = LOOKAHEAD) -> pd.DataFrame:
    """The next cliff at every position still on the board.

    `candidates` is who is left, carrying `position` and `points_over_replacement` — the frame
    `rank_candidates` hands over, in any order. It is sorted here rather than assumed, because the
    ranking above this is sorted by cost of waiting and a cliff is a fact about value order.

    Returns one row per position with `CLIFF_COLUMNS`, sorted by position so the result is
    deterministic; a caller wanting the league's own reading order applies it. A position nobody is
    left at gets no row — a zero in a column of counts reads as a count.
    """
    rows = []
    for position, group in candidates.groupby("position", sort=True):
        values = list(
            group["points_over_replacement"].sort_values(ascending=False).astype(float)
        )
        if not values or np.isnan(values[0]):
            continue
        above, drop = _cliff(values, lookahead)
        rows.append(
            {"position": position, "above": above, "drop": drop, "remaining": len(values)}
        )
    return pd.DataFrame(rows, columns=CLIFF_COLUMNS)
