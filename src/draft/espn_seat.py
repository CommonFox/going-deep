"""Work out which seat is mine, and what shape the ESPN draft is, from the league record itself.

The ESPN counterpart to `seat.py` — same intent (nothing about my seat or the league's shape is
typed in, because the one thing a drafter would type is the number that silently corrupts every
pick estimate if it is off by one), a genuinely different payload.

## One map, not two

Sleeper needs two maps because a user's seat and the roster that seat's picks land on can disagree
(a traded pick, or a mock with no rosters at all — see `seat.py`'s docstring). ESPN's
`draftSettings.pickOrder` is simpler: a list of team IDs in snake order, so the team *is* the seat
holder, with nothing indirected through a roster ID. What ESPN needs that Sleeper doesn't is
finding *my* team ID at all — Sleeper's draft order is already keyed by user ID; ESPN's is keyed by
team ID, so the drafter is found by matching the `SWID` cookie against a team's `owners`.

## Why this refuses a draft that hasn't opened

`data/raw/espn/league_96973123_2026.json` already carries a `pickOrder` — `[9, 7, 6, 2, 3, 8, 1,
4, 10, 5]` — weeks before the draft, with `draftDetail.drafted` and `inProgress` both `false` and
`draftSettings.orderType` set to `DRAFT_START`. ESPN randomizes the order when the draft room
opens under that setting, so a `pickOrder` read before then is provisional and resolving a seat
from it would be exactly the silent-wrong-answer failure `seat.py` refuses for an undrawn Sleeper
order — a number that is wrong without looking wrong. So this refuses until the draft record says
the room has actually opened (`inProgress`) or the draft is done (`drafted`).

## Rounds is every roster spot, not just the starting lineup

Sleeper's own `settings.rounds` says this directly. ESPN's league record doesn't carry an explicit
round count, so it is read as the sum of every `lineupSlotCounts` entry — starters, bench and IR
alike — since a team drafts to fill its whole roster, not just what starts.
"""

# ESPN's numeric lineup slot IDs -> the slot names `picks.SLOT_ELIGIBILITY` uses, restricted to the
# slots a player is actually assigned to. Slot 20 (bench) and slot 21 (IR) are excluded on purpose,
# for the same reason `seat.py`'s `SLOT_SETTINGS` excludes `slots_bn`: the bench is whoever does not
# fit a starting slot, not a slot players are assigned to — but both still count toward `rounds`.
_SLOT_IDS = {
    "0": "QB", "2": "RB", "4": "WR", "6": "TE", "23": "FLEX", "7": "SUPER_FLEX",
    "16": "DST", "17": "K", "18": "P",
}


def _slots(counts: dict) -> dict[str, int]:
    """The starting lineup, keeping only the slots this league actually starts.

    A slot the league does not use is absent rather than zero, matching `seat.py:_slots`.
    """
    return {
        name: int(counts.get(slot_id) or 0)
        for slot_id, name in _SLOT_IDS.items()
        if int(counts.get(slot_id) or 0) > 0
    }


def _my_team_id(teams: list[dict], swid: str) -> int:
    """The ESPN team ID my SWID owns, or a refusal naming the SWID that owns nothing."""
    for team in teams:
        if swid in (team.get("owners") or []):
            return int(team["id"])
    raise ValueError(
        f"SWID {swid} owns no team in this league. Check ESPN_S2/SWID in .env against the "
        "account that's actually in this league."
    )


def resolve_seat(league: dict, swid: str) -> dict:
    """The league shape `espn_picks.ingest_picks` takes, read out of one combined league record.

    `league` is the ESPN league payload with `mSettings`, `mTeam` and `mDraftDetail` all requested
    — `draftDetail` for whether the room has opened, `settings` for the draft's shape, `teams` for
    finding my own team ID. Returns `seat` and `roster_id` (my own ESPN team ID) for the drafter,
    `team_count` and `rounds` for the snake, and `slots` for the lineup — the same shape
    `seat.resolve_seat` returns, so every generic consumer downstream needs no changes.
    """
    detail = league.get("draftDetail") or {}
    if not detail.get("inProgress") and not detail.get("drafted"):
        raise ValueError(
            "This ESPN draft has not started — the pick order is not final until the draft room "
            "opens (draftSettings.orderType is DRAFT_START, which randomizes it then). Run this "
            "again once the room is open."
        )

    draft_settings = (league.get("settings") or {}).get("draftSettings") or {}
    if draft_settings.get("type") != "SNAKE":
        raise ValueError(
            f"This draft is a {draft_settings.get('type')!r} draft, and every pick number this "
            "tool reports assumes a snake. Refusing rather than reporting pick numbers that would "
            "be right in round one and wrong from round two."
        )

    roster_id = _my_team_id(league.get("teams") or [], swid)

    pick_order = draft_settings.get("pickOrder") or []
    if roster_id not in pick_order:
        raise ValueError(
            f"Team {roster_id} holds no seat in this draft's pick order, which has "
            f"{len(pick_order)} teams."
        )
    seat = pick_order.index(roster_id) + 1

    settings = league.get("settings") or {}
    counts = ((settings.get("rosterSettings") or {}).get("lineupSlotCounts")) or {}
    return {
        "seat": seat,
        "roster_id": roster_id,
        "team_count": int(settings["size"]),
        "rounds": sum(int(n or 0) for n in counts.values()),
        "slots": _slots(counts),
    }
