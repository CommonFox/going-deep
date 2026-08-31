"""Rank what is left by what it costs to pass on it, rather than by what it is worth.

The last pure step of the live draft assistant's seam. `picks.ingest_picks` turns a payload into
who is gone; `candidates.rank_candidates` turns who is gone into who is left; this turns who is
left into who to take. No network, no warehouse connection, no printing, and nothing handed in is
modified.

## Why value is only half a draft decision

A player worth 180 points over replacement is worth nothing *at this pick* if he will still be
sitting there 26 picks from now, and a player worth 120 is urgent if he will not. Cost of waiting
is that second half: the points over replacement expected to be lost by passing on a player now
and hoping he survives to the seat's next pick.

It combines two things, and needs both. A deep position is cheap to wait on even when the
individual player is likely to go, because someone almost as good is behind him. A cliff position
is expensive even when the player will probably last, because nobody is. Neither the survival
probability alone nor the drop-off alone says that.

## The arithmetic, and why it is only arithmetic

Every number that goes in was computed by the warehouse rebuild days before the draft. Nothing is
refitted on the night — `points_over_replacement` comes back exactly as the board priced it, and
the probabilities come back exactly as `draft_availability` computed them.

Within one position, in value order, the expected best available at the next pick is a walk from
the bottom up:

    E(nobody left)  =  0
    E(from player i) =  p_i . V_i  +  (1 - p_i) . E(from player i+1)
    cost_i           =  V_i - E(from player i)

which rearranges to `cost_i = (1 - p_i) . (V_i - E(from player i+1))` — the chance he is gone,
times the drop to whoever is expected to be there instead. That is the sentence the ticket asks
for, written as a recursion so it holds for every player at the position rather than only the top
one.

`E` is floored at zero. Points over replacement is measured against a freely available player, so
a fallback cannot honestly be worth less than that: below the floor the drafter takes a different
position, and does not take a below-replacement player because a model told him to.

The known limitation is inherited from the table and stated in `draft_plan`: the probabilities are
marginal, one player at a time, so they do not encode that exactly one player can be taken per
pick. Multiplying them as though they were independent slightly overstates how often a whole
position vanishes at once. Read a whole-position panic as a little softer than the number says.

## Two picks, and the gap between them

Waiting is a thing that happens between two of the seat's own turns, so both are needed.
`next_pick` is the turn being decided; `pick_after_next` is where a player passed on has to still
be for the decision to have cost nothing. Seat 1 of 14 deciding pick 1 is waiting until 28 — a gap
of 26 picks by other people. Deciding 28 he is waiting until 29, and the gap is empty.

Ranking against `next_pick` instead would be a tool that says nothing at the only moment it is
read: on the clock, `next_pick` is the pick being made, so every player survives to it by
definition and every cost is zero. That is the shape of a bug that looks like a working screen.

## The probability is conditional, and that is the whole of user story 8

`draft_availability` holds P(this player's draft position lands at or after pick k), computed from
his ADP distribution. That is *unconditional*: it counts every pick from the top of the draft,
including the ones already made and including my own. Availability is a monotone event — once a
player is gone he stays gone — so the conditional this needs is an exact ratio of two numbers the
table already holds, not a new model:

    p_survives  =  p(pick after next) / p(next pick + 1)

The denominator starts one past the turn being decided because that turn is *mine*, and I am the
one passing: my own pick cannot be part of the hazard a player has to survive, or the tool charges
me for the chance that I take him myself.

At the turn the two picks are adjacent and there is no gap at all: nothing costs anything to wait
for, and that holds for every player on the board rather than only the ones the ratio can be taken
for. Zero picks by other people is certainty, whether or not the table has ever priced him. Read
off the raw column instead, De'Von Achane shows a 9% survival probability at pick 29 and a large
cost of waiting from a seat that is about to pick twice in a row.

## Three kinds of missing, and only one of them is missing

The warehouse drops a row once the probability falls under its own 1% floor, and clips it to zero
past the latest pick the player has ever actually lasted to. So absence in the frame is usually
data, and reading it as ignorance would flag the most certain players on the board as the least
certain. The three cases:

- **No row at the pick I am waiting to, but rows elsewhere.** He is gone. `p_survives` is 0, and
  waiting costs the full drop to whoever is behind him.
- **No row at the pick the gap opens on.** The table had him gone *before* the gap even opened,
  and yet here he sits. Same answer, and for the same reason: the warehouse computed a zero and
  then discarded the row for being too small, so the absence *is* that zero rather than a hole
  where one should be. `p_survives` is 0 and passing on him costs the full drop.
- **No row anywhere.** No ADP, so no distribution, so nothing to say — the rookies and camp bodies
  the board carries on its depth arm rather than its market arm. This case alone stays missing:
  `survival_known` is False, nothing is guessed, and he is ranked by value.

Reading the second case as ignorance is the bug in #70. A NaN in the primary sort key lands below
every player who has a number, so the value tiebreak is never reached — which put Jalen Hurts
219th of 947 at a live 2.14 while he sat there with the second-highest value on the board. The
players it hid were precisely the fallers, which is the one thing a live board exists to catch.

A missing probability is still never defaulted to a number, and never enters anyone else's
fallback expectation either — a player who cannot be weighted cannot be counted on. A zero is not
that: it is a weight, and it says a player certain to be gone is nobody's fallback, which the walk
handles by leaving the expectation past him where it was.

## Vocabulary

`p_survives`, never `p_available`. The glossary already spends *availability* on the share of a
season a player can play, and the board carries a column with exactly that meaning. Renaming the
warehouse table is a separate concern; using its word for a different thing on a screen read under
a pick clock is not something to make worse in the meantime.
"""

import numpy as np
import pandas as pd

from src.draft.candidates import CANDIDATE_COLUMNS, rank_candidates

# What the ranking hands over: the board's own identification and price, then the two numbers this
# module adds and the flag that says whether they mean anything.
WAITING_COLUMNS = CANDIDATE_COLUMNS + ["p_survives", "cost_of_waiting", "survival_known"]

# What a survival frame has to carry: one row per player per overall pick, with the probability
# already renamed away from the warehouse's colliding word.
SURVIVAL_COLUMNS = ["player_id", "overall_pick", "p_survives"]


def _at_pick(survival: pd.DataFrame, overall_pick: int | None) -> dict[str, float]:
    """Every player's probability at one pick, as a plain lookup."""
    if overall_pick is None:
        return {}
    rows = survival.loc[survival["overall_pick"] == overall_pick]
    return dict(zip(rows["player_id"], rows["p_survives"]))


def _survival_probabilities(
    player_ids: pd.Series, survival: pd.DataFrame, wait_to: int, gap_starts: int
) -> pd.Series:
    """P(still there at `wait_to` | still there when the gap opens), or NaN where it cannot be said.

    `gap_starts` is one past the turn being decided: the first pick somebody else makes, and so the
    first one that can take the player away. When it is `wait_to` itself the seat picks twice in a
    row, nobody selects in between, and survival is certain for every player on the board —
    including the ones the table has never had an opinion about, whose certainty here is
    arithmetic rather than a probability anybody modelled.
    """
    if wait_to == gap_starts:
        return pd.Series(1.0, index=player_ids.index, dtype="float64")

    covered = set(survival["player_id"])
    at_next = _at_pick(survival, wait_to)
    at_now = _at_pick(survival, gap_starts)

    probabilities = []
    for player_id in player_ids:
        if player_id not in covered:
            # No ADP anywhere, so no distribution and nothing to say. The one genuinely missing
            # number of the three, and the only one that stays missing.
            probabilities.append(np.nan)
            continue
        # An absent probability is one under the table's floor or past his observed range, and it
        # means the same thing at whichever end of the ratio it goes missing: gone. A dropped
        # denominator says the market had him taken before the gap even opened, and a player the
        # market says is already gone does not last another gap of picks.
        denominator = at_now.get(player_id)
        probabilities.append(
            0.0 if not denominator else min(at_next.get(player_id, 0.0) / denominator, 1.0)
        )
    return pd.Series(probabilities, index=player_ids.index, dtype="float64")


def _cost_within_position(values: list[float], probabilities: list[float]) -> list[float]:
    """Cost of waiting for each player at one position, given in value order.

    Walks up from the bottom so each player is priced against what is expected to be left behind
    him. A player with no probability is passed over: he gets no cost, and contributes nothing to
    anyone else's expectation, because there is no honest weight to give him.
    """
    costs = [np.nan] * len(values)
    expected = 0.0
    for index in reversed(range(len(values))):
        probability = probabilities[index]
        if np.isnan(probability):
            continue
        expected = max(
            probability * values[index] + (1.0 - probability) * expected, 0.0
        )
        costs[index] = values[index] - expected
    return costs


def _cost_of_waiting(candidates: pd.DataFrame) -> pd.Series:
    """Cost of waiting for every candidate, one position group at a time.

    `candidates` arrives in value order, which is the order the walk above depends on.
    """
    costs = pd.Series(np.nan, index=candidates.index, dtype="float64")
    for _, group in candidates.groupby("position", sort=False):
        costs.loc[group.index] = _cost_within_position(
            list(group["points_over_replacement"]), list(group["p_survives"])
        )
    return costs


def rank_by_cost_of_waiting(
    board: pd.DataFrame,
    survival: pd.DataFrame,
    picks: dict,
    position: str | None = None,
) -> dict:
    """The players still available, most expensive to pass on first.

    `board` is one league's priced board, `survival` carries `SURVIVAL_COLUMNS` for that league,
    and `picks` is what `ingest_picks` returned — `taken`, `next_pick` and `pick_after_next` are
    the three keys read from it. `position` narrows the screen to one position without repricing
    anything: cost of waiting is measured against the whole board's remaining players, so filtering
    must not make a position look like the rest of the board had been drafted.

    Returns a dict of:

    - `candidates` — one row per available player with `WAITING_COLUMNS`, ranked, always with those
      columns even when nobody is left.
    - `degraded` — True when there is no survival data for the pick being waited to, or no such
      pick at all, and the ranking has fallen back to points over replacement alone.
    - `covers_to` — the last overall pick the survival frame covers, so a caller can say how far
      past it the draft has got rather than only that something is wrong.
    """
    wait_to = picks["pick_after_next"]
    covers_to = None if survival.empty else int(survival["overall_pick"].max())

    # Everyone, unfiltered: a position's fallback structure is a fact about the board, not about
    # what the drafter happens to be looking at.
    candidates = rank_candidates(board, picks["taken"])

    # No later turn is as degraded as no data for it: a last pick, and a finished draft, both have
    # nothing to wait for and nothing to price.
    degraded = wait_to is None or covers_to is None or wait_to > covers_to
    if degraded:
        candidates["p_survives"] = np.nan
        candidates["cost_of_waiting"] = np.nan
    else:
        candidates["p_survives"] = _survival_probabilities(
            candidates["player_id"], survival, wait_to, picks["next_pick"] + 1
        )
        candidates["cost_of_waiting"] = _cost_of_waiting(candidates)
    candidates["survival_known"] = candidates["p_survives"].notna()

    if position is not None:
        candidates = candidates.loc[candidates["position"] == position]

    # Cost of waiting first, then value, then name. The second key is not only a tiebreak: it is
    # what ranks a player whose cost cannot be computed, and pandas puts those missing values last
    # whichever way the first key is sorted, which is exactly where a number nobody has belongs.
    ranked = candidates[WAITING_COLUMNS].sort_values(
        ["cost_of_waiting", "points_over_replacement", "player_name"],
        ascending=[False, False, True],
        na_position="last",
    )
    return {
        "candidates": ranked.reset_index(drop=True),
        "degraded": degraded,
        "covers_to": covers_to,
    }
