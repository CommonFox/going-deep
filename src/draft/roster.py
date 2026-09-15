"""What a candidate is worth to *this* roster, not to a freely available one.

Issue #77. `candidates.rank_candidates` and `waiting.rank_by_cost_of_waiting` price every player
against the league's replacement level — a season-long, roster-independent fact, and deliberately
so: see both modules' docstrings, and `cliff.py`'s "not roster-conditional either". That is the
right zero point for a team with an open slot at a position and the wrong one for a team that has
already filled it, whose real alternative is its own bench.

Pure, like the rest of the package bar `live`: frames and the picks dict in, a series or a frame
out. No network, no warehouse connection, no printing, nothing handed in modified. Nothing here
recomputes `points_over_replacement` either — a candidate's board price is exactly what the
warehouse rebuild priced, and everything below only asks how much of it is *usable*.

## Displacement value

For a candidate at position P, look at every slot P is eligible to start in — its own dedicated
slot, plus FLEX and SUPER_FLEX when P qualifies for them, exactly as `picks.SLOT_ELIGIBILITY`
already defines. If any of those slots still has room, the candidate is fully additive to the
lineup and keeps his whole points over replacement. If they are all full, the honest question is
not "is he good" but "is he better than what he would sit ahead of" — so his value becomes the gap
between his own price and the weakest player currently occupying one of those slots, floored at
zero. A player worse than the weakest starter is worth nothing to this roster; points over
replacement is measured against a freely available player, so a below-replacement value is never
the honest answer, the same reasoning `waiting._cost_within_position` already floors at zero for.

The occupants themselves come from the same chronological, dedicated-slot-first assignment
`picks._assign_to_slots` makes for the roster block on screen — reimplemented here rather than
imported, because that function returns names for display and this one needs the values behind
them. A back who ends up in the superflex because the flex filled first is exactly the player a QB
candidate should be compared against if the flex happened to hold the weaker starter, and this
mirrors that assignment rather than re-deriving a different one.

No attempt is made to re-optimise the assignment — a bench QB is never priced as though it could
bump an elite back out of the superflex to make room for itself two slots away. That kind of
whole-roster reassignment is a bigger question than this ticket asks, and the displacement number
is a lower bound on a player's true marginal value rather than an exact one for that reason.

## Must-fill endgame

Displacement alone does not guarantee a startable roster: nothing stops every remaining player at
every open position being worth less, on paper, than a huge pile of zeroed-out backups, and a
drafter who never sees a hard rule can still walk out of a draft with an empty kicker slot.
`gold/draft_strategy.py`'s own simulator hit exactly this and fixed it the same way — once picks
remaining fall to or below starting slots still open, stop asking what is best and start asking
what still fits. `must_fill` reads that directly off `picks["roster"]` (the same frame the roster
block already shows), rather than re-deriving open slots from `picks["mine"]` a second time.
"""

import pandas as pd

from src.draft.picks import FILL_ORDER, SLOT_ELIGIBILITY


def _eligible_slots(position: str) -> list[str]:
    """Every slot a position may start in, dedicated slot first."""
    return [slot for slot in FILL_ORDER if position in SLOT_ELIGIBILITY[slot]]


def _placed_values(players: list[dict], slots: dict[str, int]) -> dict[str, list[float]]:
    """The same greedy, dedicated-slot-first assignment `picks._assign_to_slots` makes.

    `players` is in draft order and carries `position` and `points_over_replacement`. Kept apart
    from `picks._assign_to_slots` because that function returns names for the roster block on
    screen, and this one needs the values behind them instead — the two must not disagree about
    who lands where, so the walk is identical, just carrying a different payload through it.
    """
    placed: dict[str, list[float]] = {slot: [] for slot in FILL_ORDER}
    remaining = list(players)
    for slot in FILL_ORDER:
        eligible = SLOT_ELIGIBILITY[slot]
        openings = slots.get(slot, 0)
        still_open = []
        for player in remaining:
            if openings > 0 and player["position"] in eligible:
                placed[slot].append(player["points_over_replacement"])
                openings -= 1
            else:
                still_open.append(player)
        remaining = still_open
    return placed


def marginal_value(
    candidates: pd.DataFrame, picks: dict, league: dict, board: pd.DataFrame
) -> pd.Series:
    """What each candidate is worth to my roster as it stands, aligned to `candidates`' index.

    `candidates` carries `position` and `points_over_replacement` — what `rank_candidates` or
    `rank_by_cost_of_waiting` hands over. `picks` is what `ingest_picks` returned; only `mine` is
    read. `league` supplies `slots`. `board` is read once to look up my own players' points over
    replacement, since `mine` itself carries only identity and position — never a price.
    """
    por_by_id = dict(zip(board["player_id"], board["points_over_replacement"]))
    my_players = [
        {"position": player["position"], "points_over_replacement": por_by_id.get(player["player_id"], 0.0)}
        for player in (picks.get("mine") or [])
    ]
    placed = _placed_values(my_players, league["slots"])

    values = []
    for position, value in zip(candidates["position"], candidates["points_over_replacement"]):
        slots_for = _eligible_slots(position)
        capacity = sum(int(league["slots"].get(slot, 0)) for slot in slots_for)
        if capacity == 0:
            # No slot in this league ever starts this position — nothing to be additive to, and
            # nothing to displace either.
            values.append(0.0)
            continue

        occupants = [occupant for slot in slots_for for occupant in placed[slot]]
        if len(occupants) < capacity:
            # At least one eligible slot still has room: fully additive to the lineup.
            values.append(value)
        else:
            values.append(max(0.0, value - min(occupants)))

    return pd.Series(values, index=candidates.index, dtype="float64")


def must_fill(picks: dict, league: dict) -> dict:
    """Whether the endgame has arrived, and which positions can still fill an empty starting slot.

    `picks` is what `ingest_picks` returned — `next_pick` and `roster` are the two keys read, the
    same roster frame the screen's own roster block shows, so this never disagrees with what a
    drafter can see is still open. `league` supplies `rounds`.

    Active once the picks I have left are no more than the starting slots I still have open —
    `<=`, not `<`, mirroring `gold/draft_strategy.py`'s own `unfilled` branch: with exactly as many
    picks as holes, every one of them has to fill a hole, and with fewer than that there is no
    honest alternative either. A finished draft, or a lineup with nothing left open, is never
    active — there is nothing left to steer towards.

    Returns a dict of:

    - `active` — whether the restriction applies.
    - `positions` — every position eligible for a slot the roster frame reports open, empty when
      not active.
    """
    next_pick = picks.get("next_pick")
    roster = picks["roster"]
    open_rows = roster.loc[roster["open"] > 0]

    if next_pick is None or open_rows.empty:
        return {"active": False, "positions": set()}

    # Slots, not rows: an RB row can carry two open starts at once, and each one is a pick that
    # still has to land somewhere.
    unfilled = int(open_rows["open"].sum())
    remaining_picks = int(league["rounds"]) - len(picks.get("mine") or [])
    if remaining_picks > unfilled:
        return {"active": False, "positions": set()}

    positions = {position for slot in open_rows["slot"] for position in SLOT_ELIGIBILITY[slot]}
    return {"active": True, "positions": positions}


def restrict(frame: pd.DataFrame, fill: dict) -> pd.DataFrame:
    """One frame with everything outside the must-fill positions taken out, in arrival order.

    `frame` is anything carrying a `position` column — the candidate list and the depth block are
    both handed here, the same way `hold.withhold` is applied to both. `fill` is what `must_fill`
    returned; an inactive result hands the frame straight back.
    """
    if not fill["active"]:
        return frame
    return frame.loc[frame["position"].isin(fill["positions"])].reset_index(drop=True)
