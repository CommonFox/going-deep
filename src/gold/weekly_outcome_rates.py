"""Build `weekly_outcome_rates`: how wide a player's weekly scoring distribution actually is, not
just its middle — issue #122, under the weekly player context epic (#108).

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `weekly_stats`
(already loaded by nfl_data.py) and `league_settings` (already built by league_settings.py), the
same two inputs `defense_vs_position.py` reads and scored the same way, through `league_scoring`'s
`league_points`. One row per (league_key, player_id, season, week) for QB/RB/WR/TE — the same scope
`defense_vs_position` and `points_over_replacement` settle on for the same reason: `league_settings`
prices no kicking, and DST has no per-player row to describe.

`league_settings` carries one row per league (its *current* season's rules), not one per historical
season, so — exactly as `defense_vs_position` already does — every league rescoring here applies
today's coefficients to every season of `weekly_stats`, rather than each league only rescoring the
seasons it has actually run.

## Two different things, two different columns, per CONTEXT.md's Ceiling rate / Floor entries

- **`ceiling_rate` / `floor_rate`**: how often a player's own score cleared or missed a *positional*
  threshold — a fact about him relative to his position's population that week. Never named "boom
  rate" / "bust rate"; those are spoken for by `boom_bust.py`'s season-scale, draft-price-relative
  buckets, which is the reason #119 carved this pair of terms out separately.
- **`median` / `floor` / `ceiling`**: the player's *own* 50th/10th/90th percentile, read directly
  from his own game log — the weekly-grain sibling of `inhouse_projections`' `ppg_p10`/`ppg_p90`,
  fit there under the pinball loss; empirical here, because a season's worth of weekly rows is too
  thin a sample to fit a model against.

## Thresholds are derived, never hardcoded, and as-of-week on both sides

`position_ceiling_threshold` / `position_floor_threshold` are the `_CEILING_PERCENTILE` /
`_FLOOR_PERCENTILE` points of every QB/RB/WR/TE's own per-game score at that position, in that
league's scoring, pooled across every team — not one fixed point total, which `boom_bust.py`'s
`finish_tier` already makes the same argument against doing with a hardcoded round number. 80/20 is
a round split roughly matching the "top-fifth / bottom-fifth" shape fantasy commentary usually means
by boom/bust, not a value tuned to either league.

The threshold itself is computed as-of-week — `_position_thresholds` pools only games from weeks
strictly before the one it's attached to, walking forward exactly like `defense_vs_position`'s
`points_allowed_per_game_season_to_date` does for its own league-wide comparison. `_walk_forward_
player` then compares each of a player's own games against *that game's own* as-of-week threshold,
producing a per-game clear/miss indicator before ever touching the player's own history; only then
does it walk forward a second time — the same `shift(1)` + `expanding()` step `defense_vs_position.
_walk_forward` and `player_role_trend._walk_forward` already use — to average that indicator over
the player's own strictly prior games. Both walks reset at a season boundary, for the reason
`defense_vs_position` already gives: a discontinuity, not a fact age.

## No cross-season blending, on purpose

The ticket flags that a weekly-grain table with no evidence in September is a real limitation, and
that blending in a prior season is the natural fix, citing `player_baselines.py`'s recency-weighted
multi-year average as the house precedent. This table doesn't do that. `player_role_trend` and
`defense_vs_position` — the two other as-of-week weekly tables in this same epic — face the
identical problem (a season-opening row with `games_observed = 0`) and both made the same call:
ship the sparse row, expose the count, and leave shrinkage or blending to #114 to justify once the
raw signal is shown to matter. Blending here specifically would also have to cross a threshold
population that resets every season, comparing a player's multi-season quantile band against a
single-season population cutoff — a mismatch a future ticket should resolve deliberately, once #114
says the table is worth widening, rather than one this build should absorb silently now.

## First game of a season still ships

A player's first game has no prior game to compare, the same case `defense_vs_position` and
`player_role_trend` both keep rather than drop: `games_observed` is 0 and every rate/quantile is
null, but the row exists — otherwise a season-opening game and a bye week become indistinguishable
to a consumer doing a plain join.

## Left out on purpose

- Projecting a distribution forward. This measures the distribution a player has already produced.
- Using this to pick a lineup or price a drop. That's gated on #114 showing it helps.
- Shrinking or blending the early-season rows toward anything. See above.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src import console
from src.gold.league_scoring import STAT_COLUMNS, league_points

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# The population percentile a game must clear/miss to count as a ceiling/floor game — a round
# top-fifth/bottom-fifth split, not a value tuned to either league. See the module docstring.
_CEILING_PERCENTILE = 0.80
_FLOOR_PERCENTILE = 0.20

_OUTPUT_COLUMNS = [
    "league_key", "player_id", "player_name", "position", "team", "season", "week",
    "games_observed", "median", "floor", "ceiling",
    "ceiling_rate", "floor_rate", "position_ceiling_threshold", "position_floor_threshold",
]


def _score_games(stats: pd.DataFrame, league: pd.Series) -> pd.DataFrame:
    """Each `weekly_stats` row scored under one league's own coefficients.

    Unlike `defense_vs_position._points_allowed_by_game`, no grouping is needed first: `weekly_stats`
    is already one row per player per game, so `league_points` scores each row directly.
    """
    scored = stats[["player_id", "player_name", "position", "team", "season", "week"]].copy()
    scored["points"] = league_points(stats, league)
    return scored


def _position_thresholds(scored: pd.DataFrame, ceiling_pct: float, floor_pct: float) -> pd.DataFrame:
    """One row per (season, position, week) present in `scored`: the `ceiling_pct`/`floor_pct`
    points of every player at that position's games in strictly earlier weeks of the same season.

    Before a position's first week of evidence that season, both thresholds are null — there is no
    pool yet. The pool spans every player at the position regardless of team, and never crosses a
    season boundary, the same reset `defense_vs_position._walk_forward` applies to its own
    per-defense series.
    """
    rows = []
    for (season, position), group in scored.groupby(["season", "position"]):
        weeks = sorted(group["week"].unique())
        pool: list[float] = []
        for week in weeks:
            if pool:
                ceiling = float(np.percentile(pool, ceiling_pct * 100))
                floor = float(np.percentile(pool, floor_pct * 100))
            else:
                ceiling = np.nan
                floor = np.nan
            rows.append({
                "season": season, "position": position, "week": week,
                "position_ceiling_threshold": ceiling, "position_floor_threshold": floor,
            })
            pool.extend(group.loc[group["week"] == week, "points"].tolist())
    return pd.DataFrame(rows)


def _walk_forward_player(scored: pd.DataFrame) -> pd.DataFrame:
    """Attach each player's own as-of-week `games_observed`, ceiling/floor rate, and outcome
    quantile band, computed strictly from that player's own games before the row's own week.

    Each game's own clear/miss against `position_ceiling_threshold`/`position_floor_threshold` is
    computed first, from that game's own (already as-of-that-week) threshold, producing a per-game
    0.0/1.0/null indicator — null wherever the threshold itself was still null (the position's first
    week of evidence that season). The walk-forward step then averages a player's own indicators
    over his strictly prior games via `shift(1)` + `expanding()`, the same technique
    `defense_vs_position._walk_forward` and `player_role_trend._walk_forward` already use;
    `expanding().mean()` skips a null indicator rather than treating it as a miss, so it doesn't
    pull a rate down just because the league-wide pool hadn't formed yet that week.
    """
    out = scored.sort_values(["player_id", "season", "week"]).reset_index(drop=True)

    has_ceiling = out["position_ceiling_threshold"].notna()
    has_floor = out["position_floor_threshold"].notna()
    out["_cleared_ceiling"] = np.where(
        has_ceiling, (out["points"] >= out["position_ceiling_threshold"]).astype(float), np.nan
    )
    out["_under_floor"] = np.where(
        has_floor, (out["points"] <= out["position_floor_threshold"]).astype(float), np.nan
    )

    groups = out.groupby(["player_id", "season"])
    out["games_observed"] = groups["week"].transform(lambda w: w.shift(1).expanding().count())
    out["ceiling_rate"] = groups["_cleared_ceiling"].transform(lambda s: s.shift(1).expanding().mean())
    out["floor_rate"] = groups["_under_floor"].transform(lambda s: s.shift(1).expanding().mean())
    out["median"] = groups["points"].transform(lambda s: s.shift(1).expanding().quantile(0.5))
    out["floor"] = groups["points"].transform(lambda s: s.shift(1).expanding().quantile(0.1))
    out["ceiling"] = groups["points"].transform(lambda s: s.shift(1).expanding().quantile(0.9))

    return out.drop(columns=["_cleared_ceiling", "_under_floor"])


def build_weekly_outcome_rates() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    stats = con.sql(f"""
        SELECT player_id, player_name, position, team, season, week, {", ".join(STAT_COLUMNS)}
        FROM weekly_stats
        WHERE season_type = 'REG' AND position IN {_SKILL_POSITIONS}
    """).df()
    leagues = con.sql("SELECT * FROM league_settings").df()

    frames = []
    for _, league in leagues.iterrows():
        scored = _score_games(stats, league)
        thresholds = _position_thresholds(scored, _CEILING_PERCENTILE, _FLOOR_PERCENTILE)
        merged = scored.merge(thresholds, on=["season", "position", "week"], how="left")
        walked = _walk_forward_player(merged)
        walked["league_key"] = league["league_key"]
        frames.append(walked)

    result = pd.concat(frames, ignore_index=True)[_OUTPUT_COLUMNS]

    con.execute("CREATE OR REPLACE TABLE weekly_outcome_rates AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM weekly_outcome_rates").fetchone()
    con.close()

    console.table("weekly_outcome_rates", count)


if __name__ == "__main__":
    build_weekly_outcome_rates()
