"""Build `player_role_trend`: is a player's role growing or dying, week to week — issue #121, under
the weekly player context epic (#108).

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `weekly_stats`
(target share, air-yards share, WOPR, carries), `snap_counts` (offense share, joined through the
`ids` crosswalk the same way `player_baselines.py` already does), and `player_depth_chart` (depth
rank, starter flag). One row per (player_id, season, week) for QB/RB/WR/TE — role is priced into
every projection through its own per-game term already, so nothing here touches league scoring and
the table isn't keyed by league, unlike `defense_vs_position`.

## Level and direction are two different computations, on purpose

Every metric gets two columns: its own value that week (the level, straight from the source), and a
`_delta` describing where it's heading (the direction) — never blended into one number, the same
argument `weekly_projections` and `CONTEXT.md`'s **Role** entry both make for keeping components
apart. The delta is `_walk_forward`'s comparison of the last three prior games against every prior
game this season, both computed strictly from games before the row's own week — the same
walk-forward technique `defense_vs_position._walk_forward` and
`weekly_backtest._walk_forward_baselines` already use, so a delta can never leak the week it
describes into itself.

## Bye weeks and inactive weeks need no special handling

`weekly_stats` already only carries a row for a game a player actually appeared in — verified
against Justin Jefferson's 2023 hamstring absence, which is simply missing from `weekly_stats` for
the weeks he didn't play, the same as any bye week. `_walk_forward` therefore walks over both a bye
and an inactive week identically: its rolling and expanding windows operate on each player's actual
game sequence (by row position, after sorting on season/week), not on calendar week arithmetic, so
any kind of gap is skipped rather than counted as a zero.

A player's first game of a season is a different case from either: it's a real row for a game that
was actually played, with no prior game to compare it to. That row still ships — `games_observed` is
0 and every delta is null — rather than being dropped, the same reason `defense_vs_position` keeps a
defense/position's own season opener: dropping a zero-evidence row would make it indistinguishable
from a bye to a consumer doing a plain join.

## Depth-chart rank runs backwards from every other metric

Every level column reads "more is more role" except `depth_rank`, where a *lower* number is the
better slot. `depth_rank_delta` is computed with the exact same arithmetic as every other metric's —
last-3 average minus season-to-date average — rather than inverted in code, the same choice
`defense_vs_position` makes for its own `rank` column ("made explicit because either reading is
defensible"). So a *negative* `depth_rank_delta` is the one that means an improving role: the
player's recent rank number has been lower (better) than his season average. A consumer has to know
that rather than infer it from the sign.

`is_starter` is boolean at the source; its delta treats a start as 1 and a non-start as 0, and reads
the same direction as every metric but `depth_rank` — a positive `is_starter_delta` means starting
more recently than over the season as a whole.

## A single missing value poisons the rolling window it sits inside

`rolling(3)` (the default `min_periods`, equal to the window) requires all three prior games to
carry a real value before it returns anything — unlike `expanding()`, which skips an individual NaN
and keeps going. A coverage gap in one week (`player_depth_chart`'s join is real but not universal;
see below) therefore nulls that metric's delta for every one of the three games where the gap still
sits inside the window, not just the one game it belongs to. This is inherited, not chosen —
`defense_vs_position._walk_forward` uses the same plain `.rolling(window).mean()` — and is called out
here because it bites harder on a column with real gaps than it does there.

## Two joins, two different coverage stories

`snap_share` reaches `snap_counts` through the `ids` crosswalk exactly as `player_baselines.py`
already does (`pfr_player_id` -> `gsis_id`), at comparable coverage (99%+) — measured and logged via
`console.note` if a build ever falls short of it, the guard `player_baselines.py`'s own docstring
promises but never automated. `depth_rank`/`is_starter` reach `player_depth_chart` on (season, week,
player_id) with real, expected gaps — the depth-chart module's own docstring describes two
incompatible eras being reconciled, not the near-universal weekly participation record `snap_counts`
is. A miss there is left-joined to null, not filtered out: the row still ships with every other
metric intact.

## Left out on purpose

- Route participation, snap-weighted target rate, or anything needing charting data this warehouse
  doesn't hold — out of scope per the ticket.
- Predicting anything. This table describes what has already happened; whether it predicts next
  week, and how many weeks of movement it takes, was #134's question to answer against this table.
  #134 answers it below; nothing here changed as a result.

## Verdict (#134): no rule is supportable, and the drop logic must not gate on this

Run through `weekly_backtest.score_signal` (#131) against 2015-2025, plus a notebook-local scorer
decoupling the outcome from the baseline for the next-game and rest-of-season checks
(`notebooks/role_trend.ipynb`).

**Level** (this week's own snap/target/air-yards share, WOPR, depth rank, starter flag) clears
significance hugely against both walk-forward baselines — WR/TE `air_yards_share`, `target_share`
and `wopr` incremental rho 0.20-0.26, p < 1e-55. That is not evidence of forecasting value: a level
column is drawn from the *same game* as the points it's being scored against, so it's close to
tautological (targets and air yards are the mechanism receiving points come from, not a leading
indicator of them). Rerun against the player's own **next** game instead of the same one, the effect
either vanishes or flips slightly negative for every metric (`target_share` -0.038, `wopr` -0.041,
`is_starter` -0.082, all p < 1e-15; `carries_share` flat). Level is display context, not signal.

**Direction** (each metric's `_delta`, the real question) is unstable in a specific, diagnosable way
rather than merely noisy: positive against the slow `season_to_date_ppg` baseline at every window
from 1 to 6 games (e.g. `target_share_delta` +0.03 to +0.04, p < 1e-8), but negative against the
fast `last3_ppg` baseline at every one of those same windows (-0.06 to -0.21, p < 1e-18) — the
reversal peaks in magnitude exactly at the shipped 3-game window, because `last3_ppg`'s own baseline
window is also 3 games. `target_share_delta` itself correlates at rho 0.46 with `last3_ppg -
season_to_date_ppg`: a rising role over the last 3 games is largely restating a `last3_ppg` a
manager would already see from the last three box scores, and once that's held fixed the leftover
correlates negatively — mean reversion, not a persisting edge. The same flip replicates on the ESPN
league's own scoring to within 0.01 of Spearman rho, so it isn't a scoring-basis artifact.

Re-run against **rest-of-season** points instead of next week, the flip doesn't shrink — it grows
(`target_share_delta` last3-relative incremental rho -0.21 rest-of-season vs. -0.15 next-week, RB
-0.19). A recent role spike doesn't just fail to persist; the signature is if anything *worse* to
chase over a longer horizon.

**No window (1-6 games) and no metric among the seven clears both baselines with the same sign,** so
no "N straight weeks of decline is worth acting on" rule is supportable — the sign itself depends on
which recent-form baseline the question is asked against. `depth_rank`/`is_starter` add nothing new:
both weak and short on sample (depth-chart coverage caps `depth_rank` at ~4-5k rows, 14-17 weeks).
`sleeper_points` — the literal vendor weekly projection, the one baseline not itself built from recent
role or recent scoring — stays completely untestable: zero player-weeks in this warehouse have both a
`weekly_stats` row and a `weekly_projections` row (#117's 2026 gap). That is the one comparison that
could still show something different; re-run once that archive accumulates.

**What #110 may and may not rely on**: may not gate a drop on any `player_role_trend` delta column,
at any window, as an independent signal beyond a player's own recent scoring — the direction flips
sign depending on which recent-form baseline it's read against, and the flip gets larger, not
smaller, at a rest-of-season horizon. May display level and delta as context (what's actually
happened), the same status `game_environment.py`'s and `defense_vs_position.py`'s verdicts landed
on. May not treat this as settled for a projection-relative bar — that specific comparison remains
unanswered, not negative, pending #117's archive.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Every role component this table tracks, in both its level and its trend form. Kept as one list so
# `_walk_forward` computes the same delta the same way for all seven rather than special-casing any
# of them — see the module docstring for why `depth_rank`'s resulting sign reads backwards from the
# rest, and why `is_starter` is treated as a 0/1 rate rather than inverted or dropped from the loop.
_ROLE_METRICS = [
    "snap_share", "target_share", "air_yards_share", "wopr", "carries_share",
    "depth_rank", "is_starter",
]

# The coverage `player_baselines.py`'s docstring already claims for the identical crosswalk join, at
# season grain rather than week grain. Logged rather than enforced — a real drop is worth a look, not
# a failed build.
_SNAP_SHARE_COVERAGE_FLOOR = 0.99

_OUTPUT_COLUMNS = (
    ["player_id", "player_name", "position", "team", "season", "week", "games_observed"]
    + _ROLE_METRICS
    + [f"{metric}_delta" for metric in _ROLE_METRICS]
)


def _weekly_role(stats: pd.DataFrame, snaps: pd.DataFrame, depth: pd.DataFrame) -> pd.DataFrame:
    """One row per (player_id, season, week) for a skill-position player who actually played that
    week, carrying that week's own role level.

    `stats` is raw `weekly_stats` rows restricted to QB/RB/WR/TE, for every such player on every
    team (not just the ones being reported on) — `carries_share` needs the whole team's carries as
    its denominator, summed across every skill-position player on that team the same week (a QB
    scramble or a WR jet sweep both count). `snaps` is `snap_share` pre-joined per (player_id,
    season, week) through the `ids` crosswalk. `depth` is `player_depth_chart` as-is, joined on its
    full natural key — (player_id, season, week, team, position) — rather than a subset of it. Two
    real cases each fan out the join if either `team` or `position` is dropped from the key: a
    midseason trade leaves a player on *both* teams' depth charts the week of the trade (Mike
    Williams, traded from NYJ to PIT in week 10 of 2024, appears on both that week — `stats` already
    says which team actually fielded him), and a wildcat QB/TE like Taysom Hill is listed at two
    positions the same week. The join is left so a coverage gap nulls `depth_rank`/`is_starter` for
    that one row rather than dropping it.
    """
    team_carries = stats.groupby(["season", "week", "team"])["carries"].transform("sum")

    role = stats[[
        "player_id", "player_name", "position", "team", "season", "week",
        "target_share", "air_yards_share", "wopr",
    ]].copy()
    role["carries_share"] = stats["carries"] / team_carries

    role = role.merge(
        snaps[["player_id", "season", "week", "snap_share"]],
        on=["player_id", "season", "week"], how="left",
    )
    role = role.merge(
        depth[["gsis_id", "season", "week", "team", "position", "depth_rank", "is_starter"]]
        .rename(columns={"gsis_id": "player_id"}),
        on=["player_id", "season", "week", "team", "position"], how="left",
    )
    return role


def _walk_forward(role: pd.DataFrame) -> pd.DataFrame:
    """Attach `games_observed` and one `_delta` column per role metric, computed from each player's
    own strictly-prior games within the same season.

    `games_observed` is the count of the player's own prior games this season, read off `week`
    (never null) rather than any one metric, so a coverage gap in a single metric doesn't also
    shrink the shared evidence count. A delta is the mean of a metric's last three prior games minus
    the mean of every prior game this season; both use `shift(1)` to move the current game out of
    its own window before `expanding`/`rolling` sees it, so nothing here can leak the week it
    describes into its own trend figure. `rolling(3)` requires three full prior games before it
    returns anything, so a delta is null for a player's first two games of a season even though
    `games_observed` is already 1 or 2 by then — the same ramp-up `defense_vs_position`'s `last3`
    window has, and the same window that lets one missing value poison three games' worth of delta
    (see the module docstring).
    """
    out = role.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    groups = out.groupby(["player_id", "season"])["week"]
    out["games_observed"] = groups.transform(lambda w: w.shift(1).expanding().count())

    for metric in _ROLE_METRICS:
        values = pd.to_numeric(out[metric], errors="coerce")
        grouped = values.groupby([out["player_id"], out["season"]])
        season_to_date = grouped.transform(lambda s: s.shift(1).expanding().mean())
        last3 = grouped.transform(lambda s: s.shift(1).rolling(3).mean())
        out[f"{metric}_delta"] = last3 - season_to_date

    return out


def build_player_role_trend() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    stats = con.sql(f"""
        SELECT player_id, player_name, position, team, season, week,
               target_share, air_yards_share, wopr, carries
        FROM weekly_stats
        WHERE season_type = 'REG' AND position IN {_SKILL_POSITIONS}
    """).df()
    snaps = con.sql("""
        -- snap_counts is keyed on PFR's player id, so it reaches gsis_id through the `ids`
        -- crosswalk (99%+ of qualifying player-weeks match, per player_baselines.py's identical
        -- season-grain join). `ids` carries more than one row for some players (and, rarer, maps
        -- one pfr_id to two different gsis_ids) — grouped to one row per (gsis_id, season, week)
        -- before this returns, the same defense player_baselines.py's own snap_share_by_season CTE
        -- applies at season grain, so a duplicate crosswalk row can't fan out the weekly_stats join
        -- below.
        SELECT i.gsis_id AS player_id, sc.season, sc.week, AVG(sc.offense_pct) AS snap_share
        FROM snap_counts sc
        JOIN ids i ON i.pfr_id = sc.pfr_player_id
        WHERE sc.game_type = 'REG' AND i.gsis_id IS NOT NULL
        GROUP BY i.gsis_id, sc.season, sc.week
    """).df()
    depth = con.sql("""
        -- A small number of legacy-era rows (2015-2024) carry a NULL week — a raw depth_charts.week
        -- gap player_depth_chart.py passes through as-is. Unusable for a per-week join and dropped
        -- here rather than upstream, since that table's own contract doesn't promise week is never
        -- null.
        SELECT gsis_id, season, week, team, position, depth_rank, is_starter
        FROM player_depth_chart
        WHERE week IS NOT NULL
    """).df()

    role = _weekly_role(stats, snaps, depth)
    covered = role["snap_share"].notna().mean()
    if covered < _SNAP_SHARE_COVERAGE_FLOOR:
        console.note(
            f"player_role_trend: snap_share join covered only {covered:.1%} of player-weeks, "
            f"below the {_SNAP_SHARE_COVERAGE_FLOOR:.0%} player_baselines.py's equivalent join achieves"
        )

    result = _walk_forward(role)[_OUTPUT_COLUMNS]

    con.execute("CREATE OR REPLACE TABLE player_role_trend AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM player_role_trend").fetchone()
    con.close()

    console.table("player_role_trend", count)


if __name__ == "__main__":
    build_player_role_trend()
