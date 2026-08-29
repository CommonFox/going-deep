"""Keep the positions that will still be there in round 14 off the board until round 14.

Pure, like everything in this package bar `live`: a turn, a league shape and a frame in, a decision
and a shorter frame out. No network, no warehouse connection, no printing, and nothing handed in
is modified.

## The ranking is not wrong, it is answering a smaller question

The first live mock put defenses on the board in round 7 and kickers in round 8, and every number
behind that was correct. A starting kicker really does clear replacement level by a wide margin:
there is one K slot, fourteen of them get rostered in a fourteen-team league, and the drop from the
best kicker to the fifteenth is arithmetic rather than opinion.

`waiting` then prices the drop behind *one player*, weighted by *that player's* chance of being
gone. Both halves are about a single row. Neither can say the thing that decides the pick, which is
that the whole position will still be sitting there ten rounds from now while the receiver next to
it will not — the cost of waiting on a kicker is near zero because *every* kicker is still there
later, and that is a fact about a position rather than about a player.

So this does not reprice anything and does not reorder anything. It removes a question from the
screen until the round in which it has an answer worth having.

## The round is read off the league, not typed here

"The last two rounds" is the right answer for this league and the wrong shape of answer. It is
right because fifteen rounds with one K slot and one DST slot leaves exactly two picks that must be
spent on them; change either number and the sentence quietly stops being true while the constant
stays. So the reserve is one round per late slot the league actually starts, counted back from the
end, and round 14 falls out of it here rather than being asserted.

`draft_plans` was checked before choosing that, because the ticket asked and because the plan table
is the only thing in the repo that has simulated a draft. It has nothing to say: every plan it
holds is a five-pick opening of QB, RB, WR and TE, and the simulation never takes a kicker at all.

## Which positions, and why the punter is one of them

`LATE_POSITIONS` intersected with the slots the league starts. The punter is the same problem in
the other league's colours — one slot, thirty on the board, replacement level flat behind the first
few — and `seat.SLOT_SETTINGS` already carries `slots_p` "so that a league which does start one is
described rather than silently short a slot". Intersecting means the Sleeper league, which starts
no punter, never holds one and never names one, the same way `picks._roster_frame` never shows the
one-quarterback league an empty superflex row.

A league starting none of them holds nothing, rather than holding all three for a whole draft.

## A filter rather than a penalty, and the escape hatch that makes that safe

A soft de-rank leaves a kicker on screen at a price nobody can audit, which is the one thing this
repo keeps saying it will not do. A filter is readable: he is not there, and the screen says why.

What makes that a default rather than a wall is that the way past it already exists. Typing `k` at
the running tool narrows the board to kickers, and the hold lifts when the drafter has named the
position — so this is a position off the *default* board, never a position the tool has lost.
`render` says both things in one line, because a board short of two positions with nothing on
screen to say so reads as a board that has lost them.

## Applied twice, by one rule

The candidate list and the depth block are two frames about the same board, so both go through
`withhold`. A position held out of the list and still reported by the depth block would leave the
screen arguing with itself — "K   3 above a 12.0 drop" is the round-7 kicker case, made in the one
block that would still be making it.
"""

import pandas as pd

# The positions whose supply outlasts any draft: one slot each, dozens on the board, and a
# replacement level that is flat behind the first few. Held only where the league starts the slot,
# so this is the candidate set rather than the answer.
LATE_POSITIONS = ("K", "DST", "P")


def _round_of(overall_pick: int, team_count: int) -> int:
    """Which round an overall pick number falls in."""
    return (overall_pick - 1) // team_count + 1


def held_positions(picks: dict, league: dict, position: str | None = None) -> dict:
    """Which positions are being kept off this screen, and the round they come back.

    `picks` is what `ingest_picks` returned — `next_pick` is the only key read, because the hold is
    keyed on the pick being *decided* rather than on how far the draft has got: two seats in the
    same round are deciding picks fourteen apart and must see the same board. `league` is the shape
    `resolve_seat` read out of the draft record. `position` is what the board has been narrowed to,
    and naming a held position lifts the hold on everything — the drafter has asked the question
    this module exists to stop asking on his behalf.

    Returns a dict of:

    - `positions` — the board's own spelling of what is held, in `LATE_POSITIONS` order so the note
      on screen reads the same from one tick to the next. Empty when nothing is held.
    - `from_round` — the round they come back in, or None when nothing is being held.
    """
    nothing = {"positions": [], "from_round": None}

    late = [
        late_position
        for late_position in LATE_POSITIONS
        if int(league["slots"].get(late_position, 0)) > 0
    ]
    if not late or position in late:
        return nothing

    reserve = sum(int(league["slots"][late_position]) for late_position in late)
    from_round = int(league["rounds"]) - reserve + 1

    next_pick = picks["next_pick"]
    if next_pick is None or _round_of(next_pick, int(league["team_count"])) >= from_round:
        return nothing
    return {"positions": late, "from_round": from_round}


def withhold(frame: pd.DataFrame, hold: dict) -> pd.DataFrame:
    """One frame with the held positions taken out of it, in the order it arrived in.

    `frame` is anything carrying a `position` column — the candidate list and the depth block are
    both handed here, which is what keeps the two halves of the screen saying the same thing.
    `hold` is what `held_positions` returned. Holding nothing hands the frame straight back.
    """
    if not hold["positions"]:
        return frame
    return frame.loc[~frame["position"].isin(hold["positions"])].reset_index(drop=True)
