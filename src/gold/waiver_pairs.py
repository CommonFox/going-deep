"""Build `waiver_pairs`: pair every `drop_candidates` row (#171) with every `waiver_rankings` row
in the same league, ranked by net change to the *starting lineup* — issue #172, the second child of
the waiver epic (#110).

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. `waiver_rankings` prices adds
and `drop_candidates` prices the roster spot an add would cost, but neither says whether an add
actually helps: a WR4 upgrade that never enters the lineup is worth approximately nothing, and
roster-total accounting can't see that (the epic's own motivating example). `lineup_fill.fill_lineup`
already assigns named players to slots and is the identical instrument the lineup optimizer uses, so
this table runs it twice per pair — once on the current roster, once with the free agent swapped in
for the drop candidate — and reports the literal difference as `net_lineup_gain`, never a heuristic
estimate.

## Every pair, not just the ones that help

No materiality threshold: a pair that doesn't help still shows its (zero or negative) number rather
than being filtered out silently, the same "no invented cutoff" rule this warehouse holds elsewhere
(e.g. `weekly_outcome_rates`' floor/ceiling thresholds are derived, never picked). "The pairing names
the drop candidate whose removal yields the highest `net_lineup_gain` when that free agent is added"
falls out of sorting this table by `(league_key, add_player_id, net_lineup_gain)`, not from a second
"best pair" column layered on top.

## Weekly points, not ROS, drive the fill

`drop_candidates.ros_points` is a rest-of-season number; `lineup_fill.fill_lineup` (and
`optimal_lineup.py`'s own use of it) is a one-week instrument. So the swap is computed off each
roster player's `weekly_projections` row for the current week, looked up fresh here rather than
reused from `drop_candidates` (which never carries it), and off `waiver_rankings.weekly_points`
for the free-agent side, which is exactly that same week's number already.

## Superflex/flex eligibility is exactly what `fill_lineup` already resolves

No separate eligibility check is added here (the ticket's own acceptance bar): a pair is simulated
by removing the drop candidate's player from the roster's candidate pool, adding the free agent, and
handing the whole pool to `fill_lineup` again. Whether the free agent actually displaces anyone —
including at a different position, through FLEX/SUPERFLEX — is entirely `fill_lineup`'s own call.

## A drop candidate absent from the candidate pool makes the swap a pure addition

`roster_pool` excludes a roster row with no resolved `player_id` or no `weekly_projections` row this
week (a bye, or an identity crosswalk gap) — the same "nothing to start him on" line
`optimal_lineup.split_by_projection` already draws. Removing a player who was never a candidate is a
no-op on the pool, which is exactly correct: he wasn't contributing to the lineup total to begin
with, so the pair's gain is purely whatever adding the free agent is worth on its own.

## A free agent with no resolved identity or no weekly points can't be simulated

There is then no number to hand `fill_lineup`, so `net_lineup_gain` (and the two totals it's the
difference of) are left null for that pair rather than guessed at — not a reason to drop the row,
since the pair itself (who the free agent is, why the drop candidate is droppable) is still real
information; only the lineup-math columns go null.

## Sleeper's own raw defense label

`drop_candidates.position` (and the `my_roster` row underneath it) carries Sleeper's own raw label
for a team defense, `'DEF'`, unchanged — see that table's own docstring. `waiver_rankings` never
carries a DST/K/P row at all (scoped to QB/RB/WR/TE), so this only matters on the drop side, but it
matters a lot there: `fill_lineup` buckets candidates by position to fill each league's dedicated
slots, which are keyed by the normalized `'DST'` (`league_settings.dst_slots`). Left unnormalized, a
Sleeper defense's own dedicated slot comes back empty every week regardless of his real points —
`roster_pool` normalizes `'DEF'` to `'DST'` for exactly this reason, the identical quirk
`drop_candidates.py` already normalizes for its own replacement-level join, generalized here to the
eligibility bucket instead. The row's own `drop_position` output column is left exactly as
`drop_candidates` reports it, matching that table's own "displayed position stays raw" rule.

## Every joined column rides through unblended

Matching `weekly_player_context`'s rule: no composite score, every column traceable to the table it
came from. `add_`/`drop_` prefix which side of the pair a column belongs to (`add_player_id` from
`waiver_rankings`, `drop_player_id` from `drop_candidates`), the same source-tagging
`weekly_player_context` uses (`game_`, `dvp_`, `role_`, `outcome_`) so two identically-named source
columns never collide into one and a reader can always trace a column back to its table by name
alone. `baseline_lineup_points`/`swapped_lineup_points` are kept alongside `net_lineup_gain` rather
than only the difference, so the acceptance bar ("the literal output of two `fill_lineup()` calls")
is checkable on the table itself, not just trusted.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.draft_board import _scoring_basis
from src.gold.lineup_fill import Player, fill_lineup
from src.gold.points_over_replacement import _SKILL_POSITIONS
from src.query import q

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_DEDICATED_POSITIONS = _SKILL_POSITIONS + ("K", "DST", "P")

_OUTPUT_COLUMNS = [
    "league_key", "season", "week",
    "add_player_id", "add_player_name", "add_position", "add_availability",
    "add_weekly_points", "add_weekly_points_source",
    "add_fantasypros_rank_ecr", "add_fantasypros_pos_rank",
    "add_ros_points", "add_replacement_level_points", "add_starters_at_position",
    "drop_platform_player_id", "drop_player_id", "drop_player_name", "drop_position",
    "drop_ros_points", "drop_replacement_level_points", "drop_points_over_replacement",
    "drop_snap_share_delta", "drop_target_share_delta", "drop_air_yards_share_delta",
    "drop_wopr_delta", "drop_carries_share_delta", "drop_depth_rank_delta",
    "drop_is_starter_delta", "drop_report_status",
    "baseline_lineup_points", "swapped_lineup_points", "net_lineup_gain",
]


def roster_pool(roster: pd.DataFrame, points_by_id: dict) -> list[Player]:
    """One league's roster (`drop_candidates`'s shape: `player_id`/`position` at least) turned into
    `fill_lineup`'s candidate pool for the current week.

    A row is a candidate only if it resolved a real `player_id` *and* that id has an entry in
    `points_by_id` (`weekly_projections.sleeper_points` for the current week) — either gap reads
    identically to a drafter (nothing to start him on), matching
    `optimal_lineup.split_by_projection`'s exact rule. Sleeper's raw `'DEF'` label is normalized to
    `'DST'` — see the module docstring — everything else passes through unchanged.
    """
    pool: list[Player] = []
    for row in roster.itertuples():
        if pd.isna(row.player_id):
            continue
        points = points_by_id.get(row.player_id)
        if points is None or pd.isna(points):
            continue
        position = "DST" if row.position == "DEF" else row.position
        pool.append((row.player_id, position, float(points)))
    return pool


def net_lineup_gain(
    baseline_candidates: list[Player], baseline_total: float,
    slots: dict[str, int], flex: int, superflex: int,
    drop_player_id: str | None, add_player: Player | None,
) -> float | None:
    """The points a starting lineup gains (or loses) from dropping `drop_player_id` for
    `add_player`, run through the identical `fill_lineup` the lineup optimizer itself uses.

    `add_player` is `None` when the free agent has no resolved identity or no weekly points to
    seat him with — see the module docstring. There is then no number to compute, so the gain is
    left `None` rather than guessed at.

    A `drop_player_id` absent from `baseline_candidates` (unresolved identity, or a bye —
    `roster_pool` already excluded him) makes the removal a no-op: the swap becomes a pure
    addition, which is exactly correct since he wasn't contributing to the lineup total to begin
    with.
    """
    if add_player is None:
        return None
    swapped = [player for player in baseline_candidates if player[0] != drop_player_id] + [add_player]
    _, swapped_total = fill_lineup(swapped, slots, flex, superflex)
    return swapped_total - baseline_total


def _league_slots(league: pd.Series) -> tuple[dict[str, int], int, int]:
    slots = {position: int(league[f"{position.lower()}_slots"]) for position in _DEDICATED_POSITIONS}
    return slots, int(league["flex_slots"]), int(league["superflex_slots"])


def _league_pairs(
    league: pd.Series, roster: pd.DataFrame, free_agents: pd.DataFrame,
    weekly_projections: pd.DataFrame,
) -> pd.DataFrame:
    """Every (drop, add) pair for one league's one current week."""
    league_key = league["league_key"]
    season = int(league["season"])
    scoring = _scoring_basis(league)
    slots, flex, superflex = _league_slots(league)
    week = int(free_agents["week"].iloc[0])

    week_points = weekly_projections[
        (weekly_projections["season"] == season)
        & (weekly_projections["week"] == week)
        & (weekly_projections["scoring"] == scoring)
    ]
    points_by_id = dict(zip(week_points["player_id"], week_points["sleeper_points"]))

    baseline_candidates = roster_pool(roster, points_by_id)
    _, baseline_total = fill_lineup(baseline_candidates, slots, flex, superflex)

    rows = []
    for drop in roster.itertuples():
        for add in free_agents.itertuples():
            add_player = None
            if pd.notna(add.player_id) and pd.notna(add.weekly_points):
                add_player = (add.player_id, add.position, float(add.weekly_points))

            gain = net_lineup_gain(
                baseline_candidates, baseline_total, slots, flex, superflex,
                drop.player_id, add_player,
            )
            swapped_total = baseline_total + gain if gain is not None else None

            rows.append({
                "league_key": league_key, "season": season, "week": week,
                "add_player_id": add.player_id, "add_player_name": add.player_name,
                "add_position": add.position, "add_availability": add.availability,
                "add_weekly_points": add.weekly_points,
                "add_weekly_points_source": add.weekly_points_source,
                "add_fantasypros_rank_ecr": add.fantasypros_rank_ecr,
                "add_fantasypros_pos_rank": add.fantasypros_pos_rank,
                "add_ros_points": add.ros_points,
                "add_replacement_level_points": add.replacement_level_points,
                "add_starters_at_position": add.starters_at_position,
                "drop_platform_player_id": drop.platform_player_id,
                "drop_player_id": drop.player_id, "drop_player_name": drop.player_name,
                "drop_position": drop.position,
                "drop_ros_points": drop.ros_points,
                "drop_replacement_level_points": drop.replacement_level_points,
                "drop_points_over_replacement": drop.points_over_replacement,
                "drop_snap_share_delta": drop.snap_share_delta,
                "drop_target_share_delta": drop.target_share_delta,
                "drop_air_yards_share_delta": drop.air_yards_share_delta,
                "drop_wopr_delta": drop.wopr_delta,
                "drop_carries_share_delta": drop.carries_share_delta,
                "drop_depth_rank_delta": drop.depth_rank_delta,
                "drop_is_starter_delta": drop.is_starter_delta,
                "drop_report_status": drop.report_status,
                "baseline_lineup_points": baseline_total,
                "swapped_lineup_points": swapped_total,
                "net_lineup_gain": gain,
            })

    return pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)


def build_waiver_pairs() -> None:
    league_settings = q("SELECT * FROM league_settings")
    drop_candidates = q(
        "SELECT league_key, platform_player_id, player_id, player_name, position, ros_points, "
        "replacement_level_points, points_over_replacement, snap_share_delta, target_share_delta, "
        "air_yards_share_delta, wopr_delta, carries_share_delta, depth_rank_delta, "
        "is_starter_delta, report_status FROM drop_candidates"
    )
    waiver_rankings = q("SELECT * FROM waiver_rankings")
    weekly_projections = q(
        "SELECT player_id, season, week, scoring, sleeper_points FROM weekly_projections"
    )

    pair_frames = []
    for _, league in league_settings.iterrows():
        league_key = league["league_key"]
        roster = drop_candidates[drop_candidates["league_key"] == league_key]
        free_agents = waiver_rankings[waiver_rankings["league_key"] == league_key]
        if free_agents.empty:
            continue
        pair_frames.append(_league_pairs(league, roster, free_agents, weekly_projections))

    pairs = (
        pd.concat(pair_frames, ignore_index=True) if pair_frames
        else pd.DataFrame(columns=_OUTPUT_COLUMNS)
    )
    pairs = pairs.sort_values(
        ["league_key", "add_player_id", "net_lineup_gain"],
        ascending=[True, True, False], na_position="last",
    ).reset_index(drop=True)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE waiver_pairs AS SELECT * FROM pairs")
    con.close()

    console.table("waiver_pairs", len(pairs))


if __name__ == "__main__":
    build_waiver_pairs()
