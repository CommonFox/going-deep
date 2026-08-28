"""Turn a live Sleeper picks payload into who is gone, who is mine, and when I pick next.

This is the first half of the live draft assistant's single seam, and it is pure: no network, no
warehouse connection, no printing, no mutation of anything handed in. It takes the payload
*exactly* as Sleeper's API returns it rather than something pre-parsed, which is deliberate —
every awkward thing a payload can do is then handled inside a boundary that tests can reach.

## Why the payload needs defending against at all

`GET /draft/<id>/picks` returns the whole draft from pick one, every single poll. Never a delta.
So the obvious worries are the wrong ones: the list is complete and sorted when it arrives.

The disorder comes from above. The tool unions hand-marked players in with the API's picks so a
drafter can keep going with no network, and a hand-marked player has no pick number and no place
in the ordering. The moment the API catches up with a hand-mark, that player is in the list twice.
Both of those must not double-fill a roster slot or double-count against the next pick, and
neither is a hypothetical.

## Unmatched picks are the whole point of the module

A pick arrives carrying Sleeper's player ID. If no board row has that ID in `sleeper_id`, two very
different things could be true and there is no way to tell them apart from here:

- some deep flyer nobody was going to draft, who was never on the board; or
- somebody who *is* on the board but whose ID resolution failed upstream — meaning he has just
  been drafted and the board still shows him available.

The second is exactly the failure the whole feature exists to prevent, and it costs a pick. So an
unmatched pick is never dropped, never guessed at, and is returned named so the renderer can put
it on screen. `sleeper_ids.report_unmapped` is the same instinct applied days earlier.

## Snake arithmetic

Rounds alternate direction, so a seat's pick number depends on the round's parity:

    odd  round r, seat s  ->  (r - 1) * team_count + s
    even round r, seat s  ->  (r - 1) * team_count + (team_count - s + 1)

Seat 1 in a 14-team draft therefore picks 1, 28, 29, 56, 57 — the back-to-back pairs the glossary
describes — and a version that forgot the reversal would give 1, 15, 29 and be wrong from round two
onwards. Picks made is read from the highest pick number seen rather than from the length of the
list, so a hand-marked player (no pick number) cannot push the next turn a pick further away.

Two of the seat's picks are reported, not one. `next_pick` is the turn being decided; the whole
point of `pick_after_next` is the gap between them, which is what a player has to survive to still
be there if he is passed on. At the turn those two are adjacent and the gap is empty, which is
seat 1 being able to take both players at 28 and 29; from the middle of the board the same pair is
14 picks apart. One number cannot say that, which is why both are returned.

## Filling slots

A quarterback can start at QB or in the superflex; a back can start at RB, in the flex, or in the
superflex. So "which slots are still open" is not a count, it is an assignment, and the assignment
order changes the answer. The order here is fixed and dull on purpose — dedicated slot, then flex,
then superflex, then bench — so that what lands where can be worked out by eye at pick five rather
than trusted. Within a slot, players are taken in the order they were drafted.
"""

import pandas as pd

# Which positions may start in each slot. FLEX and SUPER_FLEX are the only ones that overlap with
# anything, and the superflex slot is the reason this league drafts differently to the other one.
SLOT_ELIGIBILITY = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "P": {"P"},
    "DST": {"DST"},
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}

# Dedicated slots first, so a back only reaches the flex once the RB slots are full, and the
# superflex is only reached once the flex is too. Everyone left over is bench.
FILL_ORDER = ["QB", "RB", "WR", "TE", "K", "P", "DST", "FLEX", "SUPER_FLEX"]

# How a lineup reads on screen, which is not how it fills: the superflex sits with the flex rather
# than after the defense, because that is where a drafter looks for it.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "P", "DST", "BENCH"]

BENCH = "BENCH"


def _unique_picks(picks: list[dict]) -> list[dict]:
    """The payload's picks, deduplicated on player and put in draft order.

    Deduplication is on the player rather than on the pick number, because the duplicate this
    guards against is a hand-marked player meeting his own API pick — same player, one of them
    with no pick number at all. Sorting last means an out-of-order payload and an in-order one
    reduce to the same list before anything is decided from it.
    """
    first_seen = {}
    for entry in picks:
        player = entry.get("player_id")
        if player is None or player in first_seen:
            continue
        first_seen[player] = entry

    # Hand-marked entries carry no pick number; they sort after everything the API has reported,
    # which is where they belong — they are known to have happened, but not known when.
    return sorted(first_seen.values(), key=lambda entry: (entry.get("pick_no") or float("inf")))


def _picked_name(entry: dict) -> str | None:
    """The name Sleeper attached to a pick, for a player the board could not identify."""
    metadata = entry.get("metadata") or {}
    parts = [metadata.get("first_name"), metadata.get("last_name")]
    return " ".join(part for part in parts if part) or None


def _picks_made(picks: list[dict]) -> int:
    """How far the draft has actually got, read from pick numbers rather than list length.

    A hand-marked player is a pick that happened but has no number; counting the list would
    treat him as an extra selection and push the seat's next turn one pick too far away.
    """
    numbered = [entry["pick_no"] for entry in picks if entry.get("pick_no") is not None]
    return max(numbered, default=0)


def next_pick_number(picks_made: int, seat: int, team_count: int, rounds: int) -> int | None:
    """The overall number of the seat's next turn, or None once the draft is over.

    Walks the seat's own pick numbers in round order and returns the first one still ahead. A
    closed form would need the same parity branch and be harder to check by eye against the
    Sleeper app, which is the thing this number exists to be verified against.
    """
    for round_number in range(1, rounds + 1):
        within_round = (
            seat if round_number % 2 == 1 else team_count - seat + 1
        )
        overall = (round_number - 1) * team_count + within_round
        if overall > picks_made:
            return overall
    return None


def _assign_to_slots(drafted: list[dict], slots: dict[str, int]) -> dict[str, list[str]]:
    """Each of my players placed in one slot, dedicated first, then flex, then superflex.

    `drafted` is in draft order, so the first back taken holds RB1 and the third falls to the
    flex — which is what a drafter expects to see and what makes the table checkable by hand.
    """
    placed = {slot: [] for slot in [*SLOT_ORDER, BENCH]}
    remaining = list(drafted)

    for slot in FILL_ORDER:
        eligible = SLOT_ELIGIBILITY[slot]
        openings = slots.get(slot, 0)
        still_open = []
        for player in remaining:
            if openings > 0 and player["position"] in eligible:
                placed[slot].append(player["player_name"])
                openings -= 1
            else:
                still_open.append(player)
        remaining = still_open

    placed[BENCH] = [player["player_name"] for player in remaining]
    return placed


def _roster_frame(placed: dict[str, list[str]], slots: dict[str, int]) -> pd.DataFrame:
    """One row per starting slot the league actually uses, plus the bench.

    Slots the league does not start are left out entirely rather than shown as zero, so the
    one-quarterback league's table never mentions a superflex it does not have.
    """
    shown = [slot for slot in SLOT_ORDER if slots.get(slot, 0) > 0 or placed[slot]]
    return pd.DataFrame(
        [
            {
                "slot": slot,
                "starts": slots.get(slot, 0),
                "filled": len(placed[slot]),
                "open": max(slots.get(slot, 0) - len(placed[slot]), 0),
                "players": placed[slot],
            }
            for slot in shown
        ]
    )


def ingest_picks(picks: list[dict], board: pd.DataFrame, league: dict) -> dict:
    """Read a raw Sleeper picks payload against one league's board.

    `league` carries the shape the payload cannot supply: `seat` and `roster_id` for the drafter,
    `team_count` and `rounds` for the snake, and `slots` mapping each starting slot to how many of
    it the league starts.

    Returns a dict of:

    - `taken` — the board's own player IDs for everyone drafted, mine and everyone else's.
    - `roster` — my lineup so far, a frame of one row per starting slot.
    - `unmatched` — every pick whose player the board could not identify, named, in draft order.
    - `next_pick` — the overall number of my next turn, or None once the draft is over.
    - `pick_after_next` — the turn after that, or None when my next turn is my last. This is what
      a player passed on at `next_pick` has to survive to, so it is the pick cost of waiting is
      measured against.
    - `picks_made` — how far the draft has got, so a caller need not re-parse the payload for it.
    """
    ordered = _unique_picks(picks)

    # Sleeper's ID -> the board row it identifies. Built once rather than per pick, and keyed on
    # the string form because that is what a pick carries and what `sleeper_ids` guarantees.
    by_sleeper_id = {
        str(sleeper_id): {"player_id": player_id, "player_name": name, "position": position}
        for sleeper_id, player_id, name, position in zip(
            board["sleeper_id"], board["player_id"], board["player_name"], board["position"]
        )
        if pd.notna(sleeper_id)
    }

    taken = set()
    mine = []
    unmatched = []

    for entry in ordered:
        row = by_sleeper_id.get(str(entry["player_id"]))
        if row is None:
            metadata = entry.get("metadata") or {}
            unmatched.append({
                "sleeper_id": entry["player_id"],
                "player_name": _picked_name(entry),
                "position": metadata.get("position"),
                "pick_no": entry.get("pick_no"),
            })
            continue

        taken.add(row["player_id"])
        if entry.get("roster_id") == league["roster_id"]:
            mine.append(row)

    picks_made = _picks_made(ordered)
    next_pick = next_pick_number(
        picks_made, league["seat"], league["team_count"], league["rounds"]
    )
    return {
        "taken": taken,
        "roster": _roster_frame(_assign_to_slots(mine, league["slots"]), league["slots"]),
        "unmatched": unmatched,
        "next_pick": next_pick,
        # The same walk, started from my next turn rather than from the draft: whatever I pass on
        # there has to last until this one.
        "pick_after_next": (
            None
            if next_pick is None
            else next_pick_number(
                next_pick, league["seat"], league["team_count"], league["rounds"]
            )
        ),
        "picks_made": picks_made,
    }
