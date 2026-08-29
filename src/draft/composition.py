"""Which roster shapes the opening is still on track for, read from the plan table on the night.

The one signal in this package that is not about a player. Pure like the rest of it — a plan
frame and my picks so far in, bands and a leader out; no network, no warehouse connection, no
printing, and nothing handed in is modified.

## Why a ranking of players cannot say this

Cost of waiting looks exactly one pick ahead, at one player at a time. That is the right question
for the pick on the clock and it is structurally blind to the roster being assembled: there is
always a more urgent individual player, so a drafter following it greedily can walk past the shape
he needed and never see a number complaining. The plan table is the other half — it scores an
*opening* by the whole roster it produces, which is precisely what five picks cannot see about
themselves.

The two are complementary, so they are kept apart. Guidance never enters the cost-of-waiting
arithmetic and never reorders anything; it sits beside the ranking and says what a position would
keep open or close off, so the recommendation stays auditable at the moment it is being trusted.

## Nothing about the finding is written here

The plan table's own headline reverses between the two leagues this repo serves: opening with two
quarterbacks is worth roughly +22 points against the field in the superflex league and roughly -41
in the one-quarterback league. A rule encoding "take two quarterbacks early" would therefore be
actively harmful in the other league already on the shelf.

So no composition string appears in this module, and neither does the length of an opening. Both
are read out of the frame: a plan's label is parsed into counts, and how many picks an opening
spends is the total of those counts. Point this at the other league's rows and it says the
opposite thing, which is the property worth having rather than the answer.

## Bands, and why nothing finer is offered

The plan table records the spread behind each of its means, and what that spread says is that the
leading compositions mostly cannot be told apart: at ten of this league's fourteen seats the best
two openings finish within one combined standard error of each other over three hundred trials,
and at only three is the leader clear by more than two. In the other league it is all ten seats.
What is *not* close is the band structure underneath them: quarterback count across the opening
moves the outcome by roughly a hundred points and triples the win rate, monotonically.

A **band** is therefore every open plan sharing one position's count — "the openings that take two
quarterbacks" — scored as the mean of its members. Coarse enough to survive the noise, and the
unit the guidance is stated in. A single composition is never named as optimal.

Every open band is reported, not only the best one, because being warned away from a badly-scoring
shape is half of what this is for and a leader on its own does not say what the alternatives cost.

## Open, and closing

A plan is **open** when it is still reachable: it wants at least as many of every position as I
have already taken. Since every plan spends the same number of picks, that single test is enough —
the surplus it still owes is exactly the picks I have left.

That makes "what does this pick cost me" answerable without simulating anything. Taking one more
of position *p* keeps a plan open only if that plan wanted more of *p* than I already have, so the
positions that keep the best band alive are the ones some member of it still wants. Everything
else closes it — a kicker included, which is why the message names what keeps the band rather than
what closes it: a position the plan table has never heard of closes every plan at once.

## When it says nothing, it says that

Three quiet states, and they are not the same:

- **Withdrawn.** The opening the table covers is behind us, or the table holds nothing for this
  seat. There is no guidance to give and none is extrapolated — the plan model constrained the
  first rounds and says nothing about what follows.
- **No open plans.** The opening so far matches no plan in the table. That is information, not an
  error: it is a shape nobody simulated, and it is reported as such rather than raising.
- **Everything is open.** Before the first pick, which is when the bands are widest and the
  guidance is worth the most.

## Known limitation, inherited

The plans were scored against a field drafting straight off ADP, which the glossary calls the
friendliest possible room. Read a band's margin as an upper bound on what the same opening is
worth against a room that is also deviating.
"""

import re

import numpy as np
import pandas as pd

# What a plan's label looks like when it is a composition: a count and a position, repeated, with
# the positions a plan takes none of simply absent (`2QB2RB1TE`). Anything else — the pure-ADP
# control the table carries beside the compositions, an ordering written as a sequence — is not an
# opening anybody can be on track for and is left out rather than guessed at.
_COMPOSITION = re.compile(r"(\d+)([A-Za-z]+)")

# What the guidance reads out of `draft_plans`. `trials` is the weight behind a plan's mean, and
# is carried so a band of plans simulated different numbers of times is still an honest average.
PLAN_COLUMNS = ["draft_slot", "plan", "trials", "points_vs_field", "win_rate"]

# One row per open band: the position and count that define it, how many open plans it holds, and
# what those plans average.
BAND_COLUMNS = ["position", "count", "plans", "points_vs_field", "win_rate"]


def _counts(label) -> dict[str, int] | None:
    """A plan's label parsed into position counts, or None if it is not a composition at all.

    The whole label has to be counts and positions with nothing left over, so `best available`
    parses to nothing rather than to something. A label naming a position twice is refused for the
    same reason: it is not a shape this can read, and reading it wrongly would be worse.
    """
    text = str(label).strip()
    matches = list(_COMPOSITION.finditer(text))
    if not matches or "".join(match.group(0) for match in matches) != text:
        return None

    counts: dict[str, int] = {}
    for match in matches:
        position = match.group(2).upper()
        if position in counts:
            return None
        counts[position] = int(match.group(1))
    return counts


def _plans_for_seat(plans: pd.DataFrame, seat: int) -> list[dict]:
    """This seat's compositions, parsed, and only those spending a full opening's worth of picks.

    A snake gives every slot a completely different draft, which is why the plan table is keyed on
    the seat: reading another seat's rows would be guidance for somebody else's draft.
    """
    if plans.empty:
        return []

    mine = plans.loc[plans["draft_slot"] == seat]
    parsed = []
    for row in mine.itertuples():
        counts = _counts(row.plan)
        if counts is None:
            continue
        parsed.append({
            "counts": counts,
            "picks": sum(counts.values()),
            "trials": float(row.trials),
            "points_vs_field": float(row.points_vs_field),
            "win_rate": float(row.win_rate),
        })

    # How long an opening is, read from the plans rather than assumed. Anything shorter is not an
    # opening of the kind the rest of the table describes and cannot be compared with one.
    if not parsed:
        return []
    opening = max(plan["picks"] for plan in parsed)
    return [plan for plan in parsed if plan["picks"] == opening]


def _positions(plans: list[dict]) -> list[str]:
    """Every position the plans mention, in the order the labels write them.

    First appearance rather than alphabetical, so the screen reads QB, RB, WR, TE — the order the
    warehouse writes a composition in, and the one a drafter's eye already knows.
    """
    order = []
    for plan in plans:
        for position in plan["counts"]:
            if position not in order:
                order.append(position)
    return order


def _weighted(plans: list[dict], column: str) -> float:
    """One band's mean, weighted by the drafts behind each plan."""
    weights = [plan["trials"] for plan in plans]
    values = [plan[column] for plan in plans]
    if sum(weights) <= 0:
        return float(np.mean(values))
    return float(np.average(values, weights=weights))


def _bands(open_plans: list[dict], positions: list[str]) -> pd.DataFrame:
    """Every position-count band the open plans still describe, in the order it is read down.

    The position holding the best band comes first, then its counts in order, so the table leads
    with the choice the guidance above it is actually about and the rest read as alternatives to
    that one.

    The tempting ordering — by how far a position's bands are spread apart, widest first — is
    quietly unfair between positions: the sweep behind the plan table caps an opening at two
    quarterbacks and two tight ends but allows five backs or five receivers, so the positions with
    more counts to their name have more room to be extreme and sort above the one the guidance is
    about. `5WR` is the widest thing on this table and nobody is drafting it.
    """
    rows = []
    for position in positions:
        for count in sorted({plan["counts"].get(position, 0) for plan in open_plans}):
            members = [
                plan for plan in open_plans if plan["counts"].get(position, 0) == count
            ]
            rows.append({
                "position": position,
                "count": count,
                "plans": len(members),
                "points_vs_field": _weighted(members, "points_vs_field"),
                "win_rate": _weighted(members, "win_rate"),
            })

    frame = pd.DataFrame(rows, columns=BAND_COLUMNS)
    if frame.empty:
        return frame

    leader = frame.groupby("position")["points_vs_field"].max()
    return (
        frame.assign(
            _leader=frame["position"].map(leader),
            _position=frame["position"].map(positions.index),
        )
        .sort_values(
            ["_leader", "_position", "count"], ascending=[False, True, True]
        )
        .drop(columns=["_leader", "_position"])
        .reset_index(drop=True)
    )


def _best(bands: pd.DataFrame, positions: list[str]) -> dict:
    """The best-scoring band of the lot.

    Read out rather than taken off the top of the table, which is ordered to *lead with the
    position* holding this band and then to run that position's counts in order — the count that
    reads first there is the lowest, not the best. Ties go to the earlier position and then to the
    smaller count, so the same frame always names the same band.
    """
    ordered = bands.assign(_position=bands["position"].map(positions.index)).sort_values(
        ["points_vs_field", "_position", "count"], ascending=[False, True, True]
    )
    return ordered.drop(columns="_position").iloc[0].to_dict()


def _nothing(reason: str, **rest) -> dict:
    """A result with no bands in it, saying why rather than being silently empty."""
    return {
        "withdrawn": False,
        "reason": reason,
        "opening_rounds": None,
        "picks_spent": 0,
        "taken": {},
        "open_plans": 0,
        "total_plans": 0,
        "bands": pd.DataFrame(columns=BAND_COLUMNS),
        "best": None,
        "keeps_best": [],
        "closes_best": [],
        **rest,
    }


def composition_guidance(plans: pd.DataFrame, picks: dict, league: dict) -> dict:
    """Which shapes this seat's opening is still on track for, and what the next pick closes.

    `plans` is `draft_plans` for one league, carrying `PLAN_COLUMNS`; the league filter belongs to
    the read because it names a table's key, and the seat filter belongs here because the seat is
    resolved on the night. `picks` is what `ingest_picks` returned — `mine` is the only key read
    from it — and `league` supplies `seat`.

    Returns a dict of:

    - `withdrawn` — True once the opening the plan table covers is behind us, or when the table
      holds nothing for this seat. There is nothing to say and nothing is extrapolated.
    - `reason` — why there is nothing to say, as a sentence for the screen, or None.
    - `opening_rounds` — how many picks an opening spends, read from the plans themselves.
    - `picks_spent` — how many of them I have made.
    - `taken` — my opening so far as position counts, including positions no plan mentions.
    - `open_plans` / `total_plans` — how many of this seat's plans are still reachable.
    - `bands` — every open band with `BAND_COLUMNS`, the position holding the best one first.
    - `best` — the best-scoring open band, or None when none are open.
    - `keeps_best` / `closes_best` — the plans' own positions, split by whether taking one more of
      them leaves the best band reachable. A position the plans never mention is in neither, and
      closes every plan there is.
    """
    mine = list(picks.get("mine") or [])
    spent = len(mine)

    seat_plans = _plans_for_seat(plans, league["seat"])
    if not seat_plans:
        return _nothing(
            withdrawn=True,
            reason=(
                f"the plan table holds no opening plans for seat {league['seat']}, so there is "
                "nothing to be on track for"
            ),
            picks_spent=spent,
        )

    positions = _positions(seat_plans)
    opening_rounds = seat_plans[0]["picks"]

    # My opening as counts, plans' positions first so the screen reads in the table's own order,
    # then anything else I have taken — a kicker in the opening is exactly why nothing matches.
    drafted = [player["position"] for player in mine]
    taken = {
        position: drafted.count(position)
        for position in positions + [p for p in drafted if p not in positions]
        if drafted.count(position)
    }

    shared = {
        "opening_rounds": opening_rounds,
        "picks_spent": spent,
        "taken": taken,
        "total_plans": len(seat_plans),
    }

    if spent >= opening_rounds:
        return _nothing(
            withdrawn=True,
            reason=(
                f"the plan table covers the first {opening_rounds} rounds and says nothing "
                "about the picks that follow"
            ),
            **shared,
        )

    # Still reachable: wants at least as many of every position as I have already taken.
    open_plans = [
        plan
        for plan in seat_plans
        if all(plan["counts"].get(position, 0) >= count for position, count in taken.items())
    ]
    if not open_plans:
        return _nothing(
            reason=(
                "no plan for this seat matches this opening, so there is nothing left to be on "
                "track for"
            ),
            **shared,
        )

    bands = _bands(open_plans, positions)
    best = _best(bands, positions)

    # A plan in the best band survives one more pick at a position only if it wanted more of that
    # position than I already have. Everything else — a position no plan mentions included —
    # closes the band, which is why the screen says what keeps it open.
    in_best = [
        plan for plan in open_plans if plan["counts"].get(best["position"], 0) == best["count"]
    ]
    keeps = [
        position
        for position in positions
        if any(plan["counts"].get(position, 0) > taken.get(position, 0) for plan in in_best)
    ]
    return {
        "withdrawn": False,
        "reason": None,
        "open_plans": len(open_plans),
        "bands": bands,
        "best": best,
        "keeps_best": keeps,
        "closes_best": [position for position in positions if position not in keeps],
        **shared,
    }
