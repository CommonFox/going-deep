"""Combine the generalized slot-filler, the resolved roster and `weekly_projections` into one
starting lineup per (league, week) — issue #89, under the lineup-optimizer epic (#86).

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `my_roster` (#88),
`weekly_projections` (#80) and `league_settings`, with `fill_lineup` (#87) doing the actual seating.

## Bridging two identity spaces

`my_roster.platform_player_id` is each platform's own ID (Sleeper's `sleeper_id`, ESPN's own `id`)
— the same shape `sleeper_ids.py`/`espn_ids.py` already resolve `draft_board` against, not the
`gsis_id`-based identity `weekly_projections.player_id` carries. `draft_board` is the one
already-loaded table that holds both sides at once (`player_id`, `sleeper_id`, `espn_id` per row),
so it is read here purely as a crosswalk — the same shortcut `weekly_projections.py` takes for its
own `sleeper_id -> player_id` map, generalized to cover the ESPN roster too. Collapsed with
`ANY_VALUE` for the identical reason that module gives: every league resolves the same player to
the same platform ID, so this is a dedup, not a choice between disagreeing sources.

A roster row whose platform ID matches nothing on `draft_board` — a deep bench player the identity
crosswalk hasn't caught up with — gets a null `player_id` rather than being dropped, so it still
reaches the "missing a projection" report below instead of silently vanishing.

## Missing a week's projection is reported, never zeroed

A rostered player who resolves to a real `player_id` can still have no `weekly_projections` row for
a given week — on a bye, or (every week, for the ESPN league's punter) a position no weekly source
in this warehouse prices at all; see `weekly_projections.py`, which nothing here adds coverage for.
Either way, defaulting him to 0 points would let `fill_lineup` correctly bench him, but for the
*wrong* reason: a real 0 is a should-not-start signal, and a missing signal is not one. So a player
with no row for the week is pulled out of that week's candidate pool entirely, rather than handed to
`fill_lineup` with a fabricated score, and reported by name instead.

## A close sit/start call is a fact about the bench, not just the starter

For every filled slot, the best-scoring benched player still eligible for it — leftover after every
other slot has taken its player, mirroring `fill_lineup`'s own leftover pools: a dedicated slot
compares against its own position only, `FLEX` against leftover RB/WR/TE, `SUPERFLEX` against that
plus leftover QB — is compared to the starter. Within `_CLOSE_CALL_MARGIN_POINTS`, both players are
named on the row rather than the table only ever showing the single best lineup as if the call were
obvious.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.draft_board import _scoring_basis
from src.gold.lineup_fill import Player, fill_lineup
from src.gold.points_over_replacement import _FLEX_POSITIONS, _SKILL_POSITIONS
from src.query import q

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_DEDICATED_POSITIONS = _SKILL_POSITIONS + ("K", "DST", "P")

_PLATFORM_ID_COLUMN = {"sleeper": "sleeper_id", "espn": "espn_id"}

# One score's worth of separation — a field goal, the fantasy-football rule of thumb for "this
# could go either way". Not a value fit to any data; see the module docstring.
_CLOSE_CALL_MARGIN_POINTS = 3.0

_OUTPUT_COLUMNS = [
    "league_key", "season", "week", "slot", "player_id", "player_name", "projected_points",
    "is_close_call", "bench_player_id", "bench_player_name", "bench_projected_points",
]


def resolve_player_ids(roster: pd.DataFrame, identity: pd.DataFrame) -> pd.DataFrame:
    """`roster` (`my_roster`'s shape) with a `player_id` column resolved through `identity`
    (`draft_board`'s `player_id`/`sleeper_id`/`espn_id`, one row per player).

    Joined on whichever platform ID column matches a roster row's `league_key`. A row the identity
    crosswalk doesn't cover gets `player_id` set to `None` rather than being dropped — see the
    module docstring.
    """
    resolved = []
    for league_key, group in roster.groupby("league_key", sort=False):
        id_column = _PLATFORM_ID_COLUMN[league_key]
        lookup = dict(zip(identity[id_column], identity["player_id"]))
        resolved.append(group.assign(player_id=group["platform_player_id"].map(lookup)))
    return pd.concat(resolved, ignore_index=True)


def split_by_projection(
    roster_ids: pd.DataFrame, projections: pd.DataFrame,
) -> tuple[list[Player], pd.DataFrame]:
    """One league's roster split into `fill_lineup`'s candidate pool and the rows with nothing to
    seat them on this week.

    `roster_ids` is one league's roster with a `player_id` column (`resolve_player_ids`'s output,
    `None` where unresolved); `projections` is `weekly_projections` cut to `player_id`/
    `sleeper_points` for that league's scoring basis and one week. A roster row is a candidate only
    if it resolved to a real `player_id` *and* that `player_id` has a projection this week — either
    gap reads identically to a drafter (nothing to start him on), so both land in the same missing
    frame rather than one of them silently becoming a 0.
    """
    points_by_id = dict(zip(projections["player_id"], projections["sleeper_points"]))

    candidates: list[Player] = []
    missing_rows = []
    for row in roster_ids.itertuples():
        points = points_by_id.get(row.player_id) if pd.notna(row.player_id) else None
        if points is None or pd.isna(points):
            missing_rows.append({"player_name": row.player_name, "position": row.position})
        else:
            candidates.append((row.player_id, row.position, float(points)))

    missing = pd.DataFrame(missing_rows, columns=["player_name", "position"])
    return candidates, missing


def _slot_eligible_positions(slot: str) -> tuple[str, ...]:
    """The position pool a bench player must belong to in order to be eligible for `slot` — the
    same leftover pools `fill_lineup` itself draws from, so the comparison here can never surface a
    bench player who was never actually a candidate for the slot."""
    if slot.startswith("SUPERFLEX"):
        return _FLEX_POSITIONS + ("QB",)
    if slot.startswith("FLEX"):
        return _FLEX_POSITIONS
    return (slot.rstrip("0123456789"),)


def flag_close_calls(assignment: dict[str, str | None], players: list[Player]) -> pd.DataFrame:
    """One row per slot in `assignment`: the starter's own points, and — within
    `_CLOSE_CALL_MARGIN_POINTS` — the best bench alternative still eligible for that slot.

    `players` is the same candidate pool `fill_lineup` was called with to produce `assignment`. A
    slot `fill_lineup` left empty, or a starter with no eligible bench player within the margin,
    gets `is_close_call=False` and null bench columns rather than a comparison against nothing.
    """
    points_by_id = {player_id: points for player_id, _, points in players}
    started = {player_id for player_id in assignment.values() if player_id}
    bench = [player for player in players if player[0] not in started]

    rows = []
    for slot, player_id in assignment.items():
        eligible = [player for player in bench if player[1] in _slot_eligible_positions(slot)]
        best_bench = max(eligible, key=lambda player: player[2], default=None)

        starter_points = points_by_id.get(player_id) if player_id else None
        is_close = (
            player_id is not None
            and best_bench is not None
            and starter_points - best_bench[2] <= _CLOSE_CALL_MARGIN_POINTS
        )
        rows.append({
            "slot": slot,
            "player_id": player_id,
            "projected_points": starter_points,
            "is_close_call": is_close,
            "bench_player_id": best_bench[0] if is_close else None,
            "bench_projected_points": best_bench[2] if is_close else None,
        })
    return pd.DataFrame(rows)


def _week_rows(
    league_key: str, season: int, week: int,
    league_roster: pd.DataFrame, week_projections: pd.DataFrame,
    slots: dict[str, int], flex: int, superflex: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One league's one week: the filled lineup with close-call flags and player names, and the
    roster rows that had nothing to seat them on."""
    candidates, missing = split_by_projection(league_roster, week_projections)
    assignment, _ = fill_lineup(candidates, slots, flex, superflex)
    rows = flag_close_calls(assignment, candidates)

    names = dict(zip(league_roster["player_id"], league_roster["player_name"]))
    rows["player_name"] = rows["player_id"].map(names)
    rows["bench_player_name"] = rows["bench_player_id"].map(names)
    rows["league_key"] = league_key
    rows["season"] = season
    rows["week"] = week

    return rows, missing.assign(league_key=league_key, season=season, week=week)


def build_optimal_lineup() -> None:
    league_settings = q("SELECT * FROM league_settings")
    roster = q("SELECT league_key, platform_player_id, player_name, position FROM my_roster")
    identity = q(
        "SELECT player_id, ANY_VALUE(sleeper_id) AS sleeper_id, ANY_VALUE(espn_id) AS espn_id "
        "FROM draft_board GROUP BY player_id"
    )
    projections = q("SELECT player_id, season, week, scoring, sleeper_points FROM weekly_projections")
    roster_ids = resolve_player_ids(roster, identity)

    lineup_frames = []
    missing_frames = []
    for _, league in league_settings.iterrows():
        league_key = league["league_key"]
        scoring = _scoring_basis(league)
        season = int(league["season"])
        slots = {pos: int(league[f"{pos.lower()}_slots"]) for pos in _DEDICATED_POSITIONS}
        flex = int(league["flex_slots"])
        superflex = int(league["superflex_slots"])

        league_roster = roster_ids[roster_ids["league_key"] == league_key]
        league_projections = projections[
            (projections["season"] == season) & (projections["scoring"] == scoring)
        ]

        for week in sorted(league_projections["week"].unique()):
            rows, missing = _week_rows(
                league_key, season, week, league_roster,
                league_projections[league_projections["week"] == week],
                slots, flex, superflex,
            )
            lineup_frames.append(rows)
            if not missing.empty:
                missing_frames.append(missing)

    lineup = pd.concat(lineup_frames, ignore_index=True)[_OUTPUT_COLUMNS]

    if missing_frames:
        missing = pd.concat(missing_frames, ignore_index=True)
        for row in missing.itertuples():
            console.note(
                f"{row.league_key} week {row.week}: no weekly projection for {row.player_name} "
                f"({row.position}) — on bye, or not covered by any weekly source"
            )

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE optimal_lineup AS SELECT * FROM lineup")
    con.close()

    console.table("optimal_lineup", len(lineup))


if __name__ == "__main__":
    build_optimal_lineup()
