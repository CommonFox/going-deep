"""Price each player against what his draft slot historically returned: surplus over ADP.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from
`points_over_replacement`, `adp_consensus`, `inhouse_projections` and `league_settings`, all
already loaded.

`points_over_replacement` answers "how much was he worth". `adp_consensus` answers "what did he
cost". Neither on its own answers the question a draft board actually turns on, which is whether
he was worth *more than he cost* — the 3rd overall pick returning a top-3 season is a fair trade,
not a win, while the same season out of the 9th round is what decides leagues. This table is that
subtraction.

## Value is points over replacement, floored at zero

`points_over_replacement` runs negative — arbitrarily so, for a QB25 measured against a QB12
replacement level. Draft value cannot: nobody is forced to start a player worse than the free agent
pool, so the worst a pick can do is return nothing. `actual_value` / `projected_value` are
therefore `points_over_replacement` floored at 0, and every curve and surplus below is built on
them. The raw signed `actual_por` / `projected_por` are kept alongside for the roster questions
where the size of a shortfall does matter.

Flooring is not cosmetic here, it is what makes the table usable across positions. Fit on signed
PoR, the expected curve for a QB drafted around pick 235 sits near -139, so any projection short of
catastrophe scores a huge "surplus" — an early version of this board ranked Tua Tagovailoa (ADP
235, projected PoR -46) as the single best value in 2026, along with seven other quarterbacks in
the top 25, all of them projected below replacement. They were not beating their price; they were
being less bad than a floor set by how deep their position's replacement level is. Positions with
low floors generated fake surplus purely from having further to fall.

## The ADP curve

`expected_value` is what a pick at a given ADP has historically returned at that position: an
isotonic regression of realised `actual_value` on `consensus_adp`, fit per
(league_key, position). Isotonic rather than a polynomial or a linear fit because the one thing
known a priori about this curve is its shape — later picks return less, monotonically — and
isotonic regression is exactly the estimator that assumes that and nothing else. No knots to place,
no degree to choose, no negative-slope-turning-positive at the tail.

Fit per position, which preserves both comparisons worth making: within a position ("of the RBs
going around pick 30, which ones beat the slot") the curve is the benchmark, and across positions
("should this pick be an RB or a TE at all") the difference in the curves' *level* is itself the
answer — a TE at pick 30 sits on a much lower curve than an RB at pick 30.

Fit walk-forward, like `inhouse_projections`' backtest: the curve for season S is fit only on
seasons before S, so no row's `surplus_value` is measured against a benchmark that had already seen
it. `_MIN_CURVE_SEASONS` / `_MIN_CURVE_ROWS` leave the earliest seasons with a NULL curve rather
than one fit on a handful of rows.

## Two surpluses

- `surplus_value` = `actual_value` - `expected_value`. Backward-looking, the study column: who
  actually beat their price. NULL for a season that hasn't been played.
- `projected_surplus` = `projected_value` - `expected_value`. Forward-looking, the draft-board
  column: who this warehouse thinks *will* beat their price. Available for the live season, and —
  because `inhouse_projections` stores its walk-forward folds alongside the live projection — for
  past seasons too, so the question "did projected surplus predict actual surplus" can be asked of
  the same table rather than taken on faith.

## Surplus is a within-season ranking, not a calibrated point total

A whole season's drafted pool does not average zero surplus, and no choice of training window makes
it. The drafted pool's own mean PoR swings by season for reasons no preseason curve can anticipate
— the league's scoring environment and its injury luck — running about -6 across 2015-2017 and
about +6 across 2024-2025, with 2020 a -10 outlier. Fitting the curve on a rolling recent window
instead of an expanding one moves the pooled offset from +5.5 to +3.9 and leaves the mean
*per-season* offset at ~6 points either way, so the residual is a property of the seasons rather
than of the estimator. The expanding window is kept: it has the most training data, matches
`inhouse_projections`' walk-forward convention, and buys the alternative nothing real.

So the raw `surplus_value` carries a season-level offset and should not be read as "points he beat
his price by", nor compared across seasons as-is. Three columns exist to make the honest uses
direct:
- `surplus_rank` / `projected_surplus_rank` — position within that league-season's own draft pool,
  1 = best. Immune to the offset, and the actual draft-board artifact: at any given pick, who
  returns the most over what he costs.
- `surplus_centered` — `surplus_value` minus that league-season's mean, which removes exactly the
  measured offset and is the column for comparing players *across* seasons.

## Two unit systems, reconciled

`points_over_replacement` is in each league's own scoring; `inhouse_projections` is in nflverse's
canned full-PPR points. `projected_por` needs the two on one scale, so league points are regressed
on PPR points per (league_key, position) — a single through-origin coefficient, since the two
differ only by the rulebook. It fits essentially exactly (R2 0.996-1.000, mean absolute residual
0.2-3.0 points on season totals of 100-350): 1.000 for full-PPR ESPN, 0.81-0.90 for the half-PPR
Sleeper receiving positions, 1.04 for Sleeper QBs, which is what the scoring rules say it should
be. Fit on all seasons rather than walk-forward, deliberately: this coefficient is a property of a
fixed rulebook, not an outcome being predicted, so there is nothing here to leak.

The projected replacement level is then computed by handing those converted projections to
`points_over_replacement`'s own `_replacement_levels` — the same combined-flex-pool VBD rule the
realised side uses, imported rather than restated so the two definitions cannot drift apart.

`projected_points` is the input rather than `projected_points_full`, because the benchmark it is
being compared against is realised production, which already carries every game its players
actually missed. `projected_points_full` deliberately strips the injury discount back out to stay
comparable to the external sites (see `inhouse_projections`), which is the wrong frame here.

## A drafted player who never played is a real zero

A player with a preseason ADP and no `points_over_replacement` row scored nothing that season, and
dropping him would bias the curve upward exactly where it matters — the expected return on a pick
has to include its bust rate. Those seasons are therefore carried at zero league points (so
`actual_por` is a full replacement level below zero) rather than skipped.

Excluded from that treatment is a player with no `weekly_stats` row in his *entire career*, who is
not a bust but an unresolved name: `adp_consensus` sometimes maps one ADP entry onto a gsis_id that
never appears in the box scores (2026 carries both a "Marvin Harrison" and a "Marvin Harrison Jr."
at different ADPs, and only one of them is a football player). Roughly 1-2% of recent drafted
seasons, higher in 2015-2016. Counting those as zeros would invent busts that never happened.
"""

from pathlib import Path

import duckdb
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from src import console
from src.gold.points_over_replacement import _SKILL_POSITIONS, _replacement_levels

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# The ADP curve needs enough history behind it to be a benchmark rather than an echo of one or two
# seasons' noise. Both bars must clear before a season gets a curve at all.
_MIN_CURVE_SEASONS = 3
_MIN_CURVE_ROWS = 60

# Realised season totals per league and position, with a drafted-but-never-played season carried as
# a genuine zero. The career-appearances guard separates that real zero from an ADP row whose
# gsis_id was never a football player — see the module docstring.
_ACTUAL_SQL = f"""
WITH career_appearances AS (
    SELECT player_id, COUNT(*) AS games
    FROM weekly_stats
    WHERE season_type = 'REG' AND fantasy_points_ppr IS NOT NULL
    GROUP BY player_id
),
adp AS (
    SELECT a.gsis_id, a.season, a.position, a.player_name, a.consensus_adp
    FROM adp_consensus a
    JOIN career_appearances c ON c.player_id = a.gsis_id
    WHERE a.consensus_adp IS NOT NULL AND a.format = '1qb'
        AND a.position IN {_SKILL_POSITIONS}
),
leagues AS (SELECT league_key FROM league_settings)
SELECT
    l.league_key,
    adp.season,
    adp.gsis_id AS player_id,
    COALESCE(por.player_name, adp.player_name) AS player_name,
    adp.position,
    adp.consensus_adp,
    -- A drafted player with no points_over_replacement row played no games at all. Zero league
    -- points is the honest total; the replacement level below then makes his PoR properly negative
    -- rather than merely absent.
    COALESCE(por.league_points, 0.0) AS league_points,
    por.player_id IS NULL AS never_played
FROM adp
CROSS JOIN leagues l
LEFT JOIN points_over_replacement por
    ON por.player_id = adp.gsis_id AND por.season = adp.season AND por.league_key = l.league_key
-- Only seasons that have actually been played: the live season's ADP rows have no realised side at
-- all, and carrying them here as zeros would label every 2026 first-rounder a total bust.
WHERE adp.season <= (SELECT MAX(season) FROM weekly_stats)
ORDER BY l.league_key, adp.season, adp.gsis_id
"""

# Replacement levels are recomputed here rather than read off points_over_replacement because the
# zero-point seasons added above change the pool that the flex allocation is drawn from.
_REPLACEMENT_SQL = """
SELECT league_key, season, position, starters_at_position, replacement_level_points
FROM points_over_replacement
GROUP BY ALL
"""

# The scale factor's training data: every player-season where both unit systems are observed.
_SCALE_SQL = """
SELECT p.league_key, p.position, p.league_points, w.ppr_points
FROM points_over_replacement p
JOIN (
    SELECT player_id, season, SUM(fantasy_points_ppr) AS ppr_points
    FROM weekly_stats
    WHERE season_type = 'REG' AND fantasy_points_ppr IS NOT NULL
    GROUP BY player_id, season
) w ON w.player_id = p.player_id AND w.season = p.season
WHERE w.ppr_points > 0
"""

_PROJECTION_SQL = f"""
SELECT
    i.target_season AS season,
    i.player_id,
    i.player_name,
    i.position,
    i.projected_points
FROM inhouse_projections i
WHERE i.projected_points IS NOT NULL AND i.position IN {_SKILL_POSITIONS}
"""

_OUTPUT_COLUMNS = [
    "league_key", "season", "player_id", "player_name", "position", "consensus_adp",
    "expected_value", "actual_value", "surplus_value", "surplus_centered", "surplus_rank",
    "projected_value", "projected_surplus", "projected_surplus_rank",
    "actual_por", "projected_por", "never_played", "curve_train_seasons",
]


def _ppr_to_league_scale(scale_df: pd.DataFrame) -> pd.Series:
    """One through-origin coefficient per (league_key, position) mapping PPR points to league
    points. Through the origin because zero production is zero points under any rulebook, and a
    free intercept would only fit noise at the bottom of the pool."""
    df = scale_df.assign(
        cross=scale_df["league_points"] * scale_df["ppr_points"],
        square=scale_df["ppr_points"] ** 2,
    )
    totals = df.groupby(["league_key", "position"])[["cross", "square"]].sum()
    return totals["cross"] / totals["square"]


def _projected_por(
    projections: pd.DataFrame, leagues: pd.DataFrame, scale: pd.Series
) -> pd.DataFrame:
    """Convert each season's projections into league points and subtract that season's projected
    replacement level, using the same VBD rule the realised side uses."""
    frames = []
    for _, league in leagues.iterrows():
        df = projections.copy()
        df["league_key"] = league["league_key"]
        key = pd.MultiIndex.from_arrays(
            [df["league_key"], df["position"]], names=["league_key", "position"]
        )
        df["league_points"] = df["projected_points"] * scale.reindex(key).to_numpy()
        for _, season_df in df.groupby("season"):
            levels = _replacement_levels(season_df, league)
            merged = season_df.merge(levels, on="position")
            merged["projected_por"] = (
                merged["league_points"] - merged["replacement_level_points"]
            )
            frames.append(merged[["league_key", "season", "player_id", "projected_por"]])
    return pd.concat(frames, ignore_index=True)


def _fit_curve(train: pd.DataFrame) -> IsotonicRegression:
    """Later picks return less: the one thing known about this curve before fitting it, and the
    only assumption isotonic regression makes."""
    return IsotonicRegression(increasing=False, out_of_bounds="clip").fit(
        train["consensus_adp"], train["actual_value"]
    )


def _expected_value(actual: pd.DataFrame) -> pd.DataFrame:
    """Walk-forward isotonic curve of value on ADP, per (league_key, position, season).

    Returns one row per (league_key, season, position, consensus_adp) rather than per player, so
    the curve is a lookup both the realised and the projected side can join against.
    """
    rows = []
    for (league_key, position), group in actual.groupby(["league_key", "position"]):
        for season in sorted(group["season"].unique()):
            train = group[group["season"] < season]
            train_seasons = train["season"].nunique()
            if train_seasons < _MIN_CURVE_SEASONS or len(train) < _MIN_CURVE_ROWS:
                continue
            fold = group[group["season"] == season]
            rows.append(pd.DataFrame({
                "league_key": league_key,
                "position": position,
                "season": season,
                "consensus_adp": fold["consensus_adp"].to_numpy(),
                "expected_value": _fit_curve(train).predict(fold["consensus_adp"]),
                "curve_train_seasons": train_seasons,
            }))
    return pd.concat(rows, ignore_index=True).drop_duplicates(
        subset=["league_key", "position", "season", "consensus_adp"]
    )


def _live_expected_value(
    actual: pd.DataFrame, live_adp: pd.DataFrame, live_season: int
) -> pd.DataFrame:
    """The same curve for the season being drafted, whose realised side doesn't exist yet — fit on
    every played season, which is the walk-forward rule applied to the live fold."""
    rows = []
    for (league_key, position), train in actual.groupby(["league_key", "position"]):
        fold = live_adp[live_adp["position"] == position]
        train_seasons = train["season"].nunique()
        if fold.empty or train_seasons < _MIN_CURVE_SEASONS or len(train) < _MIN_CURVE_ROWS:
            continue
        rows.append(pd.DataFrame({
            "league_key": league_key,
            "position": position,
            "season": live_season,
            "consensus_adp": fold["consensus_adp"].to_numpy(),
            "expected_value": _fit_curve(train).predict(fold["consensus_adp"]),
            "curve_train_seasons": train_seasons,
        }))
    return pd.concat(rows, ignore_index=True).drop_duplicates(
        subset=["league_key", "position", "season", "consensus_adp"]
    )


def _add_within_season_columns(result: pd.DataFrame) -> pd.DataFrame:
    """Rank and centre each surplus inside its own league-season.

    Both exist because the raw surplus carries a season-level offset the curve cannot anticipate
    (see the module docstring) — ranking is immune to it, and centring subtracts it outright.
    Ranked across positions rather than within one, because that is the question a draft pick
    actually poses: at this cost, who returns the most, whatever he plays.
    """
    by_season = result.groupby(["league_key", "season"])
    result["surplus_rank"] = by_season["surplus_value"].rank(ascending=False, method="min")
    result["projected_surplus_rank"] = by_season["projected_surplus"].rank(
        ascending=False, method="min"
    )
    result["surplus_centered"] = result["surplus_value"] - by_season["surplus_value"].transform(
        "mean"
    )
    return result


@console.analysis
def _report(result: pd.DataFrame) -> None:
    sleeper = result[
        (result["league_key"] == "sleeper") & (result["consensus_adp"] <= 180)
    ].copy()
    scored = sleeper[sleeper["surplus_value"].notna()].copy()

    print("\nSurplus over ADP — realised, by draft round (Sleeper, all curve-covered seasons):")
    scored["adp_round"] = (scored["consensus_adp"] / 12).apply(lambda x: int(x) + 1)
    print(f"  {'round':>5} {'n':>5} {'exp val':>8} {'act val':>8} {'surplus':>8} {'hit%':>6}")
    for adp_round, group in scored.groupby("adp_round"):
        print(f"  {adp_round:>5} {len(group):>5} {group['expected_value'].mean():>8.1f} "
              f"{group['actual_value'].mean():>8.1f} {group['surplus_value'].mean():>8.1f} "
              f"{100 * (group['surplus_value'] > 0).mean():>6.1f}")

    # The accept/reject test for the forward-looking half: sorted into deciles by what the model
    # said before the season, did realised surplus actually come out in that order?
    both = scored[scored["projected_surplus"].notna()].copy()
    if len(both) < _MIN_CURVE_ROWS:
        return
    both["decile"] = pd.qcut(both["projected_surplus"], 10, labels=False, duplicates="drop")
    print("\nDoes projected surplus predict realised surplus? Deciles of the preseason call:")
    print(f"  {'decile':>6} {'n':>5} {'proj surplus':>13} {'realised':>9} {'beat price %':>13}")
    for decile, group in both.groupby("decile"):
        print(f"  {decile + 1:>6} {len(group):>5} {group['projected_surplus'].mean():>13.1f} "
              f"{group['surplus_value'].mean():>9.1f} "
              f"{100 * (group['surplus_value'] > 0).mean():>13.1f}")
    # ADP is near-orthogonal to surplus by construction — the curve is a function of it — so this
    # is a check that the curve absorbed what ADP knows, not a benchmark the model is beating.
    print(f"\n  Spearman(projected surplus, realised surplus) = "
          f"{both['projected_surplus'].corr(both['surplus_value'], method='spearman'):.3f}  "
          f"(n={len(both)}; ADP vs realised surplus = "
          f"{both['consensus_adp'].corr(both['surplus_value'], method='spearman'):.3f}, "
          f"~0 by construction)")


def build_draft_value() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    actual = con.execute(_ACTUAL_SQL).df()
    replacement = con.execute(_REPLACEMENT_SQL).df()
    scale_df = con.execute(_SCALE_SQL).df()
    projections = con.execute(_PROJECTION_SQL).df()
    leagues = con.execute("SELECT * FROM league_settings").df()
    live_season = con.execute("SELECT MAX(season) FROM adp_consensus").fetchone()[0]
    live_adp = con.execute(f"""
        SELECT a.gsis_id AS player_id, a.player_name, a.position, a.consensus_adp
        FROM adp_consensus a
        WHERE a.season = {live_season} AND a.consensus_adp IS NOT NULL AND a.format = '1qb'
            AND a.position IN {_SKILL_POSITIONS}
    """).df()
    con.close()

    actual = actual.merge(
        replacement, on=["league_key", "season", "position"], how="left"
    )
    actual["actual_por"] = actual["league_points"] - actual["replacement_level_points"]
    # Floored, because a player below replacement is worth what the waiver wire is worth, not a
    # negative amount — see the module docstring on why the unfloored version breaks the board.
    actual["actual_value"] = actual["actual_por"].clip(lower=0)

    scale = _ppr_to_league_scale(scale_df)
    projected = _projected_por(projections, leagues, scale)
    projected["projected_value"] = projected["projected_por"].clip(lower=0)

    curve = _expected_value(actual)
    live_curve = _live_expected_value(actual, live_adp, live_season)

    played = actual.merge(
        curve, on=["league_key", "position", "season", "consensus_adp"], how="left"
    )

    # The live season has ADP and a projection but no realised side — built separately and
    # concatenated, so one table serves both the study and the draft board.
    live = live_adp.copy()
    live["season"] = live_season
    live = live.merge(leagues[["league_key"]], how="cross").merge(
        live_curve, on=["league_key", "position", "season", "consensus_adp"], how="left"
    )
    for column in ("actual_por", "actual_value", "league_points"):
        live[column] = float("nan")
    live["never_played"] = False

    result = pd.concat([played, live], ignore_index=True)
    result = result.merge(projected, on=["league_key", "season", "player_id"], how="left")
    result["surplus_value"] = result["actual_value"] - result["expected_value"]
    result["projected_surplus"] = result["projected_value"] - result["expected_value"]
    result = _add_within_season_columns(result)[_OUTPUT_COLUMNS]

    _report(result)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE draft_value AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM draft_value").fetchone()
    con.close()

    console.table("draft_value", count)


if __name__ == "__main__":
    build_draft_value()
