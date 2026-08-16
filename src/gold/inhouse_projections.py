"""Build an in-house PPG projection: a shift-based model trained on this warehouse's own data.

Unlike the other `src/gold` modules, this one isn't pure SQL — it's Python/pandas/scikit-learn
over the warehouse (still no fetch step, no network). It's the "home-grown predictive model" idea
from project notes: prior-year weighted baseline + team context -> next-year PPG, meant to become
a fifth voice in `consensus.py`'s aggregation alongside the external sites.

Features, all as of `target_season - 1` (the most recent season actually played — team context for
the season being predicted doesn't exist yet, so this stays a strictly prior-year-only setup):
- `player_weighted_baselines.weighted_ppg_ppr` / `weighted_games_per_season` / `seasons_used` —
  the player's own recency-weighted history and durability.
- `offensive_line_grades.ol_grade` and `skill_position_grades.grade` (own position) for the
  player's most-played team that season — a LEFT JOIN, since QB never matches
  `skill_position_grades` (it only grades WR/TE/RB); HistGradientBoostingRegressor takes the
  resulting NaN natively, no imputation needed.

Plus a career block that is instead as of `target_season` itself — `seasons_of_experience`,
`age_at_season`, `draft_pick`. Age and years-in-league for the season being
predicted are fixed and knowable before it starts, so using them isn't leakage the way a
performance stat would be. Without these the model had no way to represent a player's place on
the experience curve at all: its whole view of a high-scoring second-year player was last year's
rate plus team context, so it could only regress them toward the mean of everyone who scored
similarly, never toward what young ascending players specifically tend to do next.

Label: actual PPG in `target_season`, only for players who played >= the same games-played floor
`player_baselines.py` uses (imported, not redefined) — an actual season cut short by injury is as
untrustworthy a training target as a short season is a baseline input.

Every stored prediction is produced without the model having seen its own label:
- each labeled `target_season` from the first fold onward is predicted by a model trained only on
  the labeled seasons *before* it — a walk-forward backtest, one fold per season (see
  `_walk_forward`). The per-fold, per-position error is written to `inhouse_backtest`.
- the live (unlabeled) `target_season` — the one that actually feeds `consensus.py` — is predicted
  by a model refit on *all* labeled seasons combined, since there's no reason to hold data back
  from the projection that actually matters once the honest backtest above has already run.

Two season-total columns, because the external projection sites and this model don't answer the
same question:
- `projected_points_full` = `predicted_ppg_ppr * season_games` — points if the player is available
  all season. This is what every external source in `consensus.py` publishes (verified: CBS's own
  `fppg` implies exactly 17 games for all 888 of its players, and the others match to within a
  percent), so it's the column `consensus.py` blends. Anything else silently discounts the in-house
  arm against four health-neutral ones and drags the consensus median down.
- `projected_points` = `predicted_ppg_ppr * expected_games` — the same projection scaled by how
  many games the player is actually expected to play. Strictly more informative than the full
  season number for ranking real rosters; just not comparable to what the sites publish.

`predicted_ppg_ppr` and `expected_games` are predicted separately, by models with different feature
sets, because "how good per game" and "how many games will they play" are different questions —
prior-year scoring rate says nothing about availability, and the role signals that do carry it (see
`_GAMES_FEATURE_COLUMNS`) say little about scoring. Expect the games half to stay weak whatever is
thrown at it: most of what decides availability is injury luck, which nothing in this warehouse
observes.
"""

from pathlib import Path

import duckdb
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score

from src.gold.player_baselines import (
    _COMPONENT_COLUMNS,
    _MIN_GAMES_PLAYED,
    _SKILL_POSITIONS,
)
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# _COMPONENT_COLUMNS is imported rather than restated so that a column added to
# player_baselines.py reaches the model automatically instead of being built and then silently
# ignored. It's the volume/role/efficiency block: how the player's prior scoring was actually
# earned, which is the part that carries across a change of team or role, where a bare
# points-per-game average carries nothing.
_BASE_FEATURES = [
    "weighted_ppg_ppr", "weighted_games_per_season", "seasons_used", "ol_grade", "skill_grade",
    "seasons_of_experience", "age_at_season", "draft_pick",
] + _COMPONENT_COLUMNS
_FEATURE_COLUMNS = _BASE_FEATURES + [f"position_{p}" for p in _SKILL_POSITIONS]

# Availability is its own question, so it gets its own model rather than sharing the PPG feature
# set. Prior-year scoring rate says nothing about whether a player will be on the field, and the
# role block (how many weeks he appeared on a depth chart, how often as a starter, and whether he
# finished there) is the part that distinguishes "wasn't the starter yet" from "kept getting hurt"
# — two stories a games-played average alone tells identically.
_GAMES_FEATURE_COLUMNS = [
    "weighted_games_per_season", "seasons_used",
    "seasons_of_experience", "age_at_season", "draft_pick",
    "weeks_on_depth_chart", "starter_share", "end_starter",
] + [f"position_{p}" for p in _SKILL_POSITIONS]

# Conservative for a dataset this small (a few hundred rows per cohort): shallow trees, a decent
# per-leaf sample floor, and a gentle learning rate all guard against overfitting what little data
# there is.
_MODEL_PARAMS = dict(max_depth=3, min_samples_leaf=15, learning_rate=0.1, random_state=0)

# offensive_line_grades needs target_season - 1 >= 2018 (PFR advanced stats' own floor) before it
# has any non-null values at all. A walk-forward fold whose entire training slice sits below that
# would be handed a feature column that's 100% missing rather than just sparse, which breaks
# HistGradientBoostingRegressor's binning outright — so folds start at the first labeled season
# *after* this one, which is the first whose training slice is guaranteed to contain a season with
# real team-context values. Per-row (rather than per-column) missingness inside a fold is fine:
# it's exactly what skill_grade already looks like for every QB row.
_MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT = 2019

# A fold/position slice below this many rows gets no metrics row: R2 and Spearman over a handful of
# players are noise reported to three decimals, which is worse than reporting nothing.
_MIN_METRIC_ROWS = 20

# Season length for the `projected_points_full` multiplier, read from the schedule rather than
# hardcoded to 17 — the warehouse still holds 16-game seasons (<= 2020), and the number moving
# again is a live possibility. The live target_season has no schedule published yet, so it falls
# back to the most recent season that does.
_SEASON_GAMES_SQL = """
SELECT season, CAST(ROUND(COUNT(*) * 2.0 / COUNT(DISTINCT home_team)) AS BIGINT) AS games
FROM schedules
WHERE game_type = 'REG'
GROUP BY season
"""

_COMPONENT_SELECTS = ",\n".join(f"    b.{column}" for column in _COMPONENT_COLUMNS)

# player_team resolves each player's most-played team in a season (mirrors the
# team_by_player_season pattern in skill_position_grades.py, over weekly_stats instead of
# ngs_data), so team context can be joined in as of target_season - 1.
_FEATURE_SQL = f"""
WITH team_by_player_season AS (
    SELECT
        season, player_id, normalize_team(team) AS team,
        -- COUNT(*) alone isn't a total order: a player traded at the midpoint has an equal game
        -- count on both teams, and DuckDB then resolves the tie differently from run to run,
        -- silently reassigning their team (and with it the ol_grade/skill_grade joined below).
        -- MAX(week) breaks ties toward the team they finished the season on, which is also the
        -- better signal for the following season this row is used to predict; team is a final
        -- alphabetical backstop so the ordering is total.
        ROW_NUMBER() OVER (
            PARTITION BY season, player_id
            ORDER BY COUNT(*) DESC, MAX(week) DESC, normalize_team(team)
        ) AS rn
    FROM weekly_stats
    WHERE season_type = 'REG' AND team IS NOT NULL
    GROUP BY season, player_id, normalize_team(team)
),
player_team AS (
    SELECT season, player_id, team FROM team_by_player_season WHERE rn = 1
),
actual_outcomes AS (
    SELECT
        player_id,
        season AS target_season,
        SUM(fantasy_points_ppr) / COUNT(*) AS actual_ppg_ppr,
        COUNT(*) AS actual_games
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
    HAVING COUNT(*) >= {_MIN_GAMES_PLAYED}
),
-- Role as of target_season - 1, which is what separates "wasn't the starter yet" from "kept
-- getting hurt" — two very different stories that a games-played average alone tells identically.
-- end_starter deliberately reads the *last* week the player appears rather than the season as a
-- whole: a rookie who takes the job over in week 4 finishes as the starter, and that's the better
-- statement about next season than a share diluted by the weeks he spent behind someone else.
prior_role AS (
    SELECT
        season,
        gsis_id,
        COUNT(*) AS weeks_on_depth_chart,
        AVG(CASE WHEN is_starter THEN 1.0 ELSE 0.0 END) AS starter_share,
        CAST(ARG_MAX(is_starter, week) AS BIGINT) AS end_starter
    FROM player_depth_chart
    GROUP BY season, gsis_id
)
SELECT
    b.target_season,
    b.player_id,
    b.player_name,
    b.position,
    b.seasons_used,
    b.weighted_ppg_ppr,
    b.weighted_games_per_season,
{_COMPONENT_SELECTS},
    pt.team,
    ol.ol_grade,
    sk.grade AS skill_grade,
    -- Unlike every feature above, these are as of target_season itself rather than
    -- target_season - 1. That isn't leakage: how old a player will be and how many years they'll
    -- have been in the league are both fixed facts about the season being predicted, known before
    -- a snap of it is played, which is exactly not true of performance or team-context stats.
    -- 0 = rookie year, 1 = entering year two, and so on.
    b.target_season - pl.rookie_season AS seasons_of_experience,
    -- birth_date arrives as an ISO string, and TRY_CAST leaves an unparseable one as NULL rather
    -- than failing the whole build over one bad row.
    b.target_season - YEAR(TRY_CAST(pl.birth_date AS DATE)) AS age_at_season,
    -- A static career fact, so no as-of question arises. NULL means undrafted, which is signal
    -- rather than absence — HistGradientBoostingRegressor consumes the NaN natively, the same way
    -- it already does for skill_grade on every QB row. Coverage is 56-81% across rookie eras with
    -- no gradient, so a null doesn't stand in for "old season". draft_round is deliberately left
    -- out: pick number already encodes it, and carrying both scored marginally worse in the
    -- walk-forward backtest while adding no permutation importance.
    pl.draft_pick,
    pr.weeks_on_depth_chart,
    pr.starter_share,
    pr.end_starter,
    -- Benchmark only, deliberately absent from _FEATURE_COLUMNS: what the preseason draft market
    -- thought of this player, so the walk-forward can answer "does the model rank better than ADP
    -- already does" instead of only "better than doing nothing". It must never become a feature —
    -- breakout_candidates.py's entire premise is that this model is ADP-blind, and a model that
    -- had seen ADP could not meaningfully disagree with it.
    adp.consensus_adp,
    o.actual_ppg_ppr,
    o.actual_games
FROM player_weighted_baselines b
LEFT JOIN player_team pt ON pt.player_id = b.player_id AND pt.season = b.target_season - 1
LEFT JOIN offensive_line_grades ol ON ol.team = pt.team AND ol.season = b.target_season - 1
LEFT JOIN skill_position_grades sk
    ON sk.team = pt.team AND sk.season = b.target_season - 1 AND sk.position = b.position
-- players.years_of_experience is deliberately unused: it's one static value per player that
-- doesn't equal seasons-since-rookie (Brady reads 23 against a 2000 rookie season and a final
-- 2022 season), so applying it to a historical row would leak how the career eventually turned
-- out. rookie_season and birth_date are immutable, so deriving from them is safe at any season.
LEFT JOIN players pl ON pl.gsis_id = b.player_id
LEFT JOIN prior_role pr ON pr.gsis_id = b.player_id AND pr.season = b.target_season - 1
-- As of target_season itself, unlike every other prior-year join here: preseason ADP is published
-- before the season starts, so lining it up with the season it was drafted for is what makes it a
-- fair benchmark rather than a lagged one.
LEFT JOIN adp_consensus adp ON adp.gsis_id = b.player_id AND adp.season = b.target_season
LEFT JOIN actual_outcomes o ON o.player_id = b.player_id AND o.target_season = b.target_season
-- Not cosmetic: DuckDB's parallel joins return these rows in a different order from run to run,
-- and HistGradientBoostingRegressor sums gradients per histogram bin in row order. Float addition
-- isn't associative, so a reordered frame shifts split gains just enough to flip tree splits,
-- which cascades — back-to-back rebuilds on identical data moved projections by up to 20 points.
-- (target_season, player_id) is unique here, so this is a total order and makes rebuilds
-- reproducible.
ORDER BY b.target_season, b.player_id
"""


def _add_position_dummies(df: pd.DataFrame) -> pd.DataFrame:
    dummies = pd.get_dummies(df["position"])
    for position in _SKILL_POSITIONS:
        if position not in dummies.columns:
            dummies[position] = 0
    dummies = dummies[list(_SKILL_POSITIONS)].add_prefix("position_").astype(int)
    return pd.concat([df, dummies], axis=1)


def _fit(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(**_MODEL_PARAMS)
    model.fit(train[_FEATURE_COLUMNS], train["actual_ppg_ppr"])
    return model


def _fit_games(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(**_MODEL_PARAMS)
    model.fit(train[_GAMES_FEATURE_COLUMNS], train["actual_games"])
    return model


def _metrics(fold: pd.DataFrame, target_season: int, position: str) -> dict:
    """Score one fold (or one position within it) against every benchmark worth beating.

    Three comparisons, because "the model has some skill" and "the model is worth running" are
    different claims:
    - `naive_*` — carrying last season's recency-weighted rate forward unchanged, i.e. what
      `player_weighted_baselines` alone would say. Losing to this means the model is subtracting.
    - `adp_spearman` — the preseason draft market's own ordering. This is the parity benchmark:
      ADP is thousands of drafters pricing in offseason information (job changes, scheme, camp
      reports) that nothing in this warehouse observes.
    - `model_spearman_vs_adp` — the model re-scored on *only* the ADP-covered players, so the two
      Spearmans above sit on the same population. Scored over the full pool the model would get
      credit for correctly ranking undrafted players ADP never had an opinion on.
    """
    actual, predicted = fold["actual_ppg_ppr"], fold["predicted_ppg_ppr"]
    drafted = fold[fold["consensus_adp"].notna()]
    has_adp = len(drafted) >= _MIN_METRIC_ROWS
    return {
        "target_season": target_season,
        "position": position,
        "n": len(fold),
        "mae": mean_absolute_error(actual, predicted),
        "r2": r2_score(actual, predicted),
        "spearman": spearmanr(predicted, actual).statistic,
        "naive_mae": mean_absolute_error(actual, fold["weighted_ppg_ppr"]),
        "naive_spearman": spearmanr(fold["weighted_ppg_ppr"], actual).statistic,
        # Negated so a better draft pick sorts the same direction as more points, making this
        # Spearman directly comparable to the two above rather than its mirror image.
        "adp_spearman": (
            spearmanr(-drafted["consensus_adp"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "model_spearman_vs_adp": (
            spearmanr(drafted["predicted_ppg_ppr"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "adp_n": len(drafted),
        "games_mae": mean_absolute_error(fold["actual_games"], fold["expected_games"]),
        "games_naive_mae": mean_absolute_error(
            fold["actual_games"], fold["weighted_games_per_season"]
        ),
    }


def _walk_forward(
    labeled: pd.DataFrame, fold_seasons: list[int]
) -> tuple[list[pd.DataFrame], pd.DataFrame]:
    """Predict each labeled season from a model trained only on the seasons before it.

    Replaces what used to be a single train-2019/holdout-2020 split (n=331). One split can't
    distinguish a real improvement from fold noise, which makes it useless as the accept/reject
    instrument for any change to the feature set — hence one fold per labeled season, each training
    on everything that came before it, exactly mirroring how the live model is fit.
    """
    predictions, metrics = [], []
    for season in fold_seasons:
        train = labeled[labeled["target_season"] < season]
        fold = labeled[labeled["target_season"] == season].copy()
        fold["predicted_ppg_ppr"] = _fit(train).predict(fold[_FEATURE_COLUMNS])
        fold["expected_games"] = _fit_games(train).predict(fold[_GAMES_FEATURE_COLUMNS])
        predictions.append(fold)

        metrics.append(_metrics(fold, season, "ALL"))
        for position, group in fold.groupby("position"):
            if len(group) >= _MIN_METRIC_ROWS:
                metrics.append(_metrics(group, season, position))
    return predictions, pd.DataFrame(metrics)


def _print_backtest_report(metrics: pd.DataFrame, folds: list[pd.DataFrame]) -> None:
    overall = metrics[metrics["position"] == "ALL"]
    print("\nWalk-forward backtest — each season predicted by a model trained only on prior ones:")
    print(f"  {'season':>6} {'n':>5} {'MAE':>6} {'R2':>6} {'rho':>6} | "
          f"{'naive MAE':>9} {'naive rho':>9} | {'ADP rho':>7} {'model rho':>9} {'(n)':>6}")
    for row in overall.itertuples():
        print(f"  {row.target_season:>6} {row.n:>5} {row.mae:>6.2f} {row.r2:>6.3f} "
              f"{row.spearman:>6.3f} | {row.naive_mae:>9.2f} {row.naive_spearman:>9.3f} | "
              f"{row.adp_spearman:>7.3f} {row.model_spearman_vs_adp:>9.3f} {row.adp_n:>6}")

    # Pooled over every fold rather than averaged over per-fold numbers, so a season with more
    # qualifying players counts for more and small positions (TE, QB) don't get a whole fold's
    # worth of weight from 25 rows.
    pooled = pd.concat(folds, ignore_index=True)
    print("\n  Pooled across all folds, by position:")
    for position in ("QB", "RB", "WR", "TE"):
        group = pooled[pooled["position"] == position]
        if len(group) < _MIN_METRIC_ROWS:
            continue
        row = _metrics(group, 0, position)
        print(f"  {position:>6} {row['n']:>5} {row['mae']:>6.2f} {row['r2']:>6.3f} "
              f"{row['spearman']:>6.3f} | {row['naive_mae']:>9.2f} {row['naive_spearman']:>9.3f} | "
              f"{row['adp_spearman']:>7.3f} {row['model_spearman_vs_adp']:>9.3f} "
              f"{row['adp_n']:>6}")

    games = _metrics(pooled, 0, "ALL")
    print(f"\n  Games played, pooled: MAE={games['games_mae']:.2f} vs "
          f"{games['games_naive_mae']:.2f} for the weighted average it replaces")


def build_inhouse_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")
    df = con.execute(_FEATURE_SQL).df()
    season_games = dict(con.execute(_SEASON_GAMES_SQL).fetchall())
    con.close()

    df = _add_position_dummies(df)
    # The live target_season's schedule isn't published yet (and nflverse won't carry it until
    # well after these projections are wanted), so it inherits the most recent season that is —
    # the same assumption every external site is making when it projects 17 games.
    latest_scheduled = season_games[max(season_games)]
    for season in df["target_season"].unique():
        season_games.setdefault(int(season), latest_scheduled)

    labeled = df[df["actual_ppg_ppr"].notna()].sort_values("target_season")
    labeled_seasons = sorted(labeled["target_season"].unique())
    fold_seasons = [s for s in labeled_seasons if s > _MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT]
    live_season = df["target_season"].max()

    output_frames = []
    metrics = pd.DataFrame()

    if fold_seasons:
        output_frames, metrics = _walk_forward(labeled, fold_seasons)
        _print_backtest_report(metrics, output_frames)

        # Out-of-sample (on the fold, not its training slice) so a feature the model overfit to
        # doesn't look important just because it memorized training noise. Answers "do the
        # team-context signals (ol_grade/skill_grade) actually carry weight" rather than assuming
        # they do because they're in the feature list. Run on the last fold: it has the most
        # training data behind it, so it's the fold closest to the live model actually shipped.
        last_fold = output_frames[-1]
        last_model = _fit(labeled[labeled["target_season"] < fold_seasons[-1]])
        importances = permutation_importance(
            last_model, last_fold[_FEATURE_COLUMNS], last_fold["actual_ppg_ppr"],
            n_repeats=20, random_state=0,
        )
        ranked_importances = sorted(
            zip(_FEATURE_COLUMNS, importances.importances_mean), key=lambda x: -x[1]
        )
        importance_str = ", ".join(f"{name}={score:.3f}" for name, score in ranked_importances)
        print(f"\nPermutation feature importance on the {fold_seasons[-1]} fold "
              f"(mean R2 drop when shuffled): {importance_str}")
    else:
        print("No labeled season has a trainable prior slice yet — skipping the backtest.")

    # Restrict live output to players who actually played in target_season - 1: without that,
    # a player with no data more recent than 2+ years back (e.g. a retiree like Tom Brady, whose
    # last game was 2022) still gets extrapolated forward from their old numbers, with nothing in
    # the feature set signaling they're no longer active. Backtest folds are untouched — a missing
    # prior-year team there is just a smaller feature signal, not a "don't show this" judgment call
    # the way it is for the live cohort actually feeding consensus.py.
    live = df[(df["target_season"] == live_season) & df["team"].notna()].copy()
    if not labeled.empty:
        final_model = _fit(labeled)
        live["predicted_ppg_ppr"] = final_model.predict(live[_FEATURE_COLUMNS])
        live["expected_games"] = _fit_games(labeled).predict(live[_GAMES_FEATURE_COLUMNS])
    else:
        live["predicted_ppg_ppr"] = float("nan")
        live["expected_games"] = float("nan")
        print("No labeled seasons available yet — live cohort predictions left null.")
    output_frames.append(live)

    result = pd.concat(output_frames, ignore_index=True)
    result["season_games"] = result["target_season"].map(season_games)
    result["projected_points"] = result["predicted_ppg_ppr"] * result["expected_games"]
    result["projected_points_full"] = result["predicted_ppg_ppr"] * result["season_games"]
    result = result[[
        "player_id", "player_name", "position", "target_season",
        "predicted_ppg_ppr", "expected_games", "season_games",
        "projected_points", "projected_points_full",
    ]]

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE inhouse_projections AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM inhouse_projections").fetchone()
    con.execute("CREATE OR REPLACE TABLE inhouse_backtest AS SELECT * FROM metrics")
    (metric_count,) = con.execute("SELECT COUNT(*) FROM inhouse_backtest").fetchone()
    con.close()

    print(f"\nBuilt {count} rows into {WAREHOUSE_PATH} (table: inhouse_projections)")
    print(f"Built {metric_count} rows into {WAREHOUSE_PATH} (table: inhouse_backtest)")


if __name__ == "__main__":
    build_inhouse_projections()
