"""Assign named players to a league's starting slots — the generalized form of
`draft_strategy.py`'s `_score_lineup`, extracted under issue #87 so the coming lineup optimizer
(#86) doesn't duplicate it.

`draft_strategy.py` already proves the greedy fill optimal: fill the narrowest eligibility first,
since superflex eligibility is a superset of flex eligibility (RB/WR/TE plus QB), which is itself a
superset of a dedicated slot (one position only). That proof doesn't depend on whether the thing
being filled is a point total or a specific player, so this is the identical algorithm with player
identity carried through instead of discarded.

Any position not in `_FLEX_POSITIONS` — the ESPN league's punter (`P`) included — is dedicated
only: it fills from its own slot and never leaks into FLEX or SUPERFLEX.
"""

from src.gold.points_over_replacement import _FLEX_POSITIONS

Player = tuple[str, str, float]


def _ranked(players: list[Player]) -> list[Player]:
    """Best points first; ties break on `player_id` so the same roster always seats the same
    lineup, regardless of input order."""
    return sorted(players, key=lambda player: (-player[2], player[0]))


def _seat(pool: list[Player], label: str, count: int) -> dict[str, str | None]:
    """`count` slots named `f"{label}1"`, `f"{label}2"`, ... from the front of an already-ranked
    pool, `None` where the pool runs out."""
    return {f"{label}{i + 1}": (pool[i][0] if i < len(pool) else None) for i in range(count)}


def fill_lineup(
    players: list[Player], slots: dict[str, int], flex: int, superflex: int,
) -> tuple[dict[str, str | None], float]:
    """Greedily seat `players` into one league's starting slots.

    `players` is `(player_id, position, points)` per rostered player. `slots` maps every dedicated
    position this league starts (e.g. `QB`/`RB`/`WR`/`TE`, plus `P` for the ESPN league) to how
    many it starts; `flex` and `superflex` are counts for the RB/WR/TE and RB/WR/TE/QB pools.

    Returns `(assignment, total)`: `assignment` maps a slot label (`"RB1"`, `"FLEX2"`,
    `"SUPERFLEX1"`, ...) to the `player_id` seated there, or `None` if too few eligible players
    were left to fill it. `total` is the summed points of every filled slot.
    """
    by_position: dict[str, list[Player]] = {}
    for player in players:
        by_position.setdefault(player[1], []).append(player)

    assignment: dict[str, str | None] = {}
    leftover: dict[str, list[Player]] = {}
    for position, count in slots.items():
        pool = _ranked(by_position.get(position, []))
        assignment.update(_seat(pool, position, count))
        leftover[position] = pool[count:]

    flex_pool = _ranked([player for pos in _FLEX_POSITIONS for player in leftover.get(pos, [])])
    assignment.update(_seat(flex_pool, "FLEX", flex))

    if superflex:
        superflex_pool = _ranked(flex_pool[flex:] + leftover.get("QB", []))
        assignment.update(_seat(superflex_pool, "SUPERFLEX", superflex))

    points_by_id = {player_id: points for player_id, _, points in players}
    total = sum(points_by_id[player_id] for player_id in assignment.values() if player_id)
    return assignment, total
