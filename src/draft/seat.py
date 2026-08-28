"""Work out which seat is mine, and what shape the draft is, from the draft record itself.

Sleeper's `GET /draft/<id>` already knows everything the pick arithmetic needs — where I sit, how
many teams there are, how many rounds, and which lineup slots the league starts. So none of it is
typed in. That is worth more than tidiness: the one thing a drafter would have to type is the
number that silently corrupts every pick estimate if it is off by one, and it would be typed in
the thirty seconds before a draft starts.

## Two maps, and they are not the same map

The record carries both `draft_order` and `slot_to_roster_id`, and the temptation is to read one
and assume the other:

- `draft_order` maps a **user** to a **seat** — where in the snake that manager picks.
- `slot_to_roster_id` maps a **seat** to a **roster** — which roster that seat's picks land on.

In this league seat 1 is roster 6, so the two disagree for almost everybody. The seat drives the
pick arithmetic and the roster ID is how a pick is recognised as *mine*, which means confusing
them does not fail loudly: it fills my lineup with somebody else's players while my own picks come
back as another manager's, and the board keeps looking plausible throughout.

## What it refuses

A draft that is not a snake, and a draft whose order has not been drawn yet. Both are refusals
rather than defaults because the alternative is a pick number that is wrong without looking wrong
— `next_pick_number` reverses every even round, so against a linear draft it is right in round one
and wrong from round two onwards, which is the worst shape an error can have on a draft night.
"""

# Sleeper's own settings keys, mapped onto the slot names `picks.SLOT_ELIGIBILITY` uses. `slots_bn`
# is left out: the bench is whoever does not fit a starting slot, not a slot players are assigned
# to. `slots_p` has never been seen on a Sleeper league — the punter is the ESPN league's oddity —
# and is carried anyway so that a league which does start one is described rather than silently
# short a slot.
SLOT_SETTINGS = {
    "QB": "slots_qb",
    "RB": "slots_rb",
    "WR": "slots_wr",
    "TE": "slots_te",
    "FLEX": "slots_flex",
    "SUPER_FLEX": "slots_super_flex",
    "K": "slots_k",
    "P": "slots_p",
    "DST": "slots_def",
}


def _slots(settings: dict) -> dict[str, int]:
    """The starting lineup, keeping only the slots this league actually starts.

    A slot the league does not use is absent rather than zero, so the one-quarterback league's
    lineup never shows an empty superflex row and this league's never shows a punter.
    """
    return {
        slot: int(settings[key])
        for slot, key in SLOT_SETTINGS.items()
        if int(settings.get(key) or 0) > 0
    }


def resolve_seat(draft: dict, user_id: str) -> dict:
    """The league shape `ingest_picks` takes, read out of one draft record.

    Returns `seat` and `roster_id` for the drafter, `team_count` and `rounds` for the snake, and
    `slots` for the lineup — the exact dict the ingestion seam expects, so nothing between here
    and there has to know Sleeper's spelling of anything.
    """
    if draft.get("type") != "snake":
        raise ValueError(
            f"This draft is a {draft.get('type')!r} draft, and every pick number this tool "
            "reports assumes a snake. Refusing rather than reporting pick numbers that would be "
            "right in round one and wrong from round two."
        )

    order = draft.get("draft_order") or {}
    if not order:
        raise ValueError(
            "This draft has no draft order yet — Sleeper populates it when the order is drawn. "
            "Until then there is no seat to resolve, and no honest way to guess one."
        )

    seat = order.get(str(user_id))
    if seat is None:
        raise ValueError(
            f"User {user_id} holds no seat in this draft, which has {len(order)}. Check the "
            "configured username against the league you are drafting in."
        )
    seat = int(seat)

    roster_id = (draft.get("slot_to_roster_id") or {}).get(str(seat))
    if roster_id is None:
        raise ValueError(
            f"Seat {seat} maps to no roster in this draft. Without that, a pick of mine cannot "
            "be told apart from anyone else's."
        )

    settings = draft.get("settings") or {}
    return {
        "seat": seat,
        "roster_id": int(roster_id),
        "team_count": int(settings["teams"]),
        "rounds": int(settings["rounds"]),
        "slots": _slots(settings),
    }
