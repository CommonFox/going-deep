"""Turn a live ESPN `draftDetail.picks` payload into who is gone, who is mine, and when I pick next.

The ESPN counterpart to `picks.py` — same pure seam, same return contract (`taken`, `roster`,
`mine`, `unmatched`, `next_pick`, `pick_after_next`, `picks_made`), a genuinely simpler payload.

## No fallback needed

Sleeper needs `roster_id` with a `draft_slot` fallback because a Sleeper *mock* carries no rosters
at all, and a traded pick's `draft_slot` is the seat that used to hold it rather than the manager
who actually made the pick (see `picks.py`'s docstring). ESPN's `draftDetail.picks` entries always
carry `teamId` directly — there is no roster/seat indirection to fall through, because
`espn_seat.py`'s `pickOrder` already lists team IDs, not a separate seat number. `_is_mine` is
therefore one comparison, not two.

## No name on an unmatched pick

Sleeper's picks carry `metadata.first_name`/`last_name`, which is what makes an unmatched pick
nameable on screen. ESPN's `mDraftDetail` picks carry no such metadata — only IDs and numbers — so
an unmatched ESPN pick can only be reported by its raw `playerId`. `player_name` and `position`
come back `None` for one, and `render.py`'s `_text` renders that as a gap rather than a value.

## What's reused from `picks.py`

`SLOT_ELIGIBILITY`, `FILL_ORDER`, `SLOT_ORDER`, `BENCH`, `_assign_to_slots`, `_roster_frame` and
`next_pick_number` are all already platform-agnostic — they operate on the *resolved* `mine` list
(`player_name`/`position` dicts) and on `slots`/`seat`/`team_count`/`rounds`, never on a raw pick's
platform-specific fields. Only the parsing of ESPN's own raw shape is new here.
"""

import pandas as pd

from src.draft.picks import (
    BENCH,
    FILL_ORDER,
    SLOT_ELIGIBILITY,
    SLOT_ORDER,
    _assign_to_slots,
    _roster_frame,
    next_pick_number,
)

__all__ = ["ingest_picks", "picks_made", "SLOT_ELIGIBILITY", "FILL_ORDER", "SLOT_ORDER", "BENCH"]


def _unique_picks(picks: list[dict]) -> list[dict]:
    """The payload's picks, deduplicated on player and put in draft order.

    Same reasoning as `picks._unique_picks`: a hand-marked player has no `overallPickNumber`, so
    once ESPN's own pick for him arrives there are two entries for one player. The real one is kept
    by listing the API's entries first (see `combine` in `marks.py`) and taking the first seen.
    """
    first_seen = {}
    for entry in picks:
        player = entry.get("playerId")
        if player is None or player in first_seen:
            continue
        first_seen[player] = entry

    return sorted(
        first_seen.values(), key=lambda entry: (entry.get("overallPickNumber") or float("inf"))
    )


def _is_mine(entry: dict, league: dict) -> bool:
    """Whether one pick belongs to the drafter — by team ID, which every real pick carries."""
    return entry.get("teamId") == league["roster_id"]


def picks_made(picks: list[dict]) -> int:
    """How far the draft has actually got, read from pick numbers rather than list length."""
    numbered = [
        entry["overallPickNumber"] for entry in picks if entry.get("overallPickNumber") is not None
    ]
    return max(numbered, default=0)


def ingest_picks(picks: list[dict], board, league: dict) -> dict:
    """Read a raw ESPN picks payload against one league's board.

    Same contract as `picks.ingest_picks` — see that module's docstring for what each returned key
    means. `board` carries `espn_id` (from `src.gold.espn_ids`) rather than `sleeper_id`.
    """
    ordered = _unique_picks(picks)

    by_espn_id = {
        int(espn_id): {"player_id": player_id, "player_name": name, "position": position}
        for espn_id, player_id, name, position in zip(
            board["espn_id"], board["player_id"], board["player_name"], board["position"]
        )
        if pd.notna(espn_id)
    }

    taken = set()
    mine = []
    unmatched = []

    for entry in ordered:
        row = by_espn_id.get(entry.get("playerId"))
        if row is None:
            unmatched.append({
                "espn_id": str(entry.get("playerId")),
                "player_name": None,
                "position": None,
                "pick_no": entry.get("overallPickNumber"),
            })
            continue

        taken.add(row["player_id"])
        if _is_mine(entry, league):
            mine.append(row)

    made = picks_made(ordered)
    next_pick = next_pick_number(made, league["seat"], league["team_count"], league["rounds"])
    return {
        "taken": taken,
        "roster": _roster_frame(_assign_to_slots(mine, league["slots"]), league["slots"]),
        "mine": mine,
        "unmatched": unmatched,
        "next_pick": next_pick,
        "pick_after_next": (
            None
            if next_pick is None
            else next_pick_number(next_pick, league["seat"], league["team_count"], league["rounds"])
        ),
        "picks_made": made,
    }
