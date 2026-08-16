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
`age_at_season`, `draft_round`, `draft_pick`. Age and years-in-league for the season being
predicted are fixed and knowable before it starts, so using them isn't leakage the way a
performance stat would be. Without these the model had no way to represent a player's place on
the experience curve at all: its whole view of a high-scoring second-year player was last year's
rate plus team context, so it could only regress them toward the mean of everyone who scored
similarly, never toward what young ascending players specifically tend to do next.

Label: actual PPG in `target_season`, only for players who played >= the same games-played floor
`player_baselines.py` uses (imported, not redefined) — an actual season cut short by injury is as
untrustworthy a training target as a short season is a baseline input.

Only two cohorts ever get a *stored* prediction, both produced without seeing their own label:
- the earliest labeled `target_season` has no prior labeled season to train on, so no honest
  out-of-sample prediction is possible for it — it's used only as training data, not stored.
- the next labeled `target_season` is held out as a genuine backtest: trained on the earliest
  season only, predicted out-of-sample, with MAE/R2 printed.
- the live (unlabeled) `target_season` — the one that actually feeds `consensus.py` — is predicted
  by a model refit on *all* labeled seasons combined, since there's no reason to hold data back
  from the projection that actually matters once the honest backtest above has already run.

`projected_points` (season-total, matching the units every other source in `consensus.py` uses) is
`predicted_ppg_ppr * expected_games`, where `expected_games` is the same
`weighted_games_per_season` durability signal used as a model input — decoupling "how good per
game" (learned) from "how many games will they play" (a recency-weighted historical rate).
"""

from pathlib import Path

import duckdb
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score

from src.gold.player_baselines import _MIN_GAMES_PLAYED, _SKILL_POSITIONS
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_BASE_FEATURES = [
    "weighted_ppg_ppr", "weighted_games_per_season", "seasons_used", "ol_grade", "skill_grade",
    "seasons_of_experience", "age_at_season", "draft_pick",
]
_FEATURE_COLUMNS = _BASE_FEATURES + [f"position_{p}" for p in _SKILL_POSITIONS]

# Conservative for a dataset this small (a few hundred rows per cohort): shallow trees, a decent
# per-leaf sample floor, and a gentle learning rate all guard against overfitting what little data
# there is.
_MODEL_PARAMS = dict(max_depth=3, min_samples_leaf=15, learning_rate=0.1, random_state=0)

# offensive_line_grades needs target_season - 1 >= 2018 (PFR advanced stats' own floor) before it
# has any non-null values at all. Picking an earlier target_season as the single-season holdout
# training slice would hand it a feature column that's 100% missing rather than just sparse, which
# breaks HistGradientBoostingRegressor's binning outright. The final live-season model further
# down still trains on every labeled season combined (pre-2019 ones included), since per-row (not
# per-column) missingness there is exactly what skill_grade already looks like for every QB row.
_MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT = 2019

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
        SUM(fantasy_points_ppr) / COUNT(*) AS actual_ppg_ppr
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
    HAVING COUNT(*) >= {_MIN_GAMES_PLAYED}
)
SELECT
    b.target_season,
    b.player_id,
    b.player_name,
    b.position,
    b.seasons_used,
    b.weighted_ppg_ppr,
    b.weighted_games_per_season,
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
    o.actual_ppg_ppr
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


def build_inhouse_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")
    df = con.execute(_FEATURE_SQL).df()
    con.close()

    df = _add_position_dummies(df)
    labeled = df[df["actual_ppg_ppr"].notna()].sort_values("target_season")
    labeled_seasons = sorted(labeled["target_season"].unique())
    trainable_seasons = [s for s in labeled_seasons if s >= _MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT]
    live_season = df["target_season"].max()

    output_frames = []

    if len(trainable_seasons) >= 2:
        train_season, holdout_season = trainable_seasons[0], trainable_seasons[1]
        train = labeled[labeled["target_season"] == train_season]
        holdout = labeled[labeled["target_season"] == holdout_season].copy()

        holdout_model = _fit(train)
        holdout["predicted_ppg_ppr"] = holdout_model.predict(holdout[_FEATURE_COLUMNS])
        mae = mean_absolute_error(holdout["actual_ppg_ppr"], holdout["predicted_ppg_ppr"])
        r2 = r2_score(holdout["actual_ppg_ppr"], holdout["predicted_ppg_ppr"])
        print(
            f"Holdout eval: trained on target_season={train_season} (n={len(train)}), "
            f"evaluated out-of-sample on target_season={holdout_season} (n={len(holdout)}) "
            f"-> MAE={mae:.2f} R2={r2:.2f}"
        )

        # Out-of-sample (on the holdout, not train) so a feature the model overfit to doesn't look
        # important just because it memorized train-set noise. Answers "do the team-context signals
        # (ol_grade/skill_grade) actually carry weight" rather than assuming they do because they're
        # in the feature list.
        importances = permutation_importance(
            holdout_model, holdout[_FEATURE_COLUMNS], holdout["actual_ppg_ppr"],
            n_repeats=20, random_state=0,
        )
        ranked_importances = sorted(
            zip(_FEATURE_COLUMNS, importances.importances_mean), key=lambda x: -x[1]
        )
        importance_str = ", ".join(f"{name}={score:.3f}" for name, score in ranked_importances)
        print(f"Permutation feature importance (mean R2 drop when shuffled): {importance_str}")

        output_frames.append(holdout)
    else:
        print("Fewer than 2 trainable labeled seasons available — skipping holdout evaluation.")

    # Restrict live output to players who actually played in target_season - 1: without that,
    # a player with no data more recent than 2+ years back (e.g. a retiree like Tom Brady, whose
    # last game was 2022) still gets extrapolated forward from their old numbers, with nothing in
    # the feature set signaling they're no longer active. Training/holdout are untouched — a
    # missing prior-year team there is just a smaller feature signal, not a "don't show this"
    # judgment call the way it is for the live cohort actually feeding consensus.py.
    live = df[(df["target_season"] == live_season) & df["team"].notna()].copy()
    if not labeled.empty:
        final_model = _fit(labeled)
        live["predicted_ppg_ppr"] = final_model.predict(live[_FEATURE_COLUMNS])
    else:
        live["predicted_ppg_ppr"] = float("nan")
        print("No labeled seasons available yet — live cohort predictions left null.")
    output_frames.append(live)

    result = pd.concat(output_frames, ignore_index=True)
    result["expected_games"] = result["weighted_games_per_season"]
    result["projected_points"] = result["predicted_ppg_ppr"] * result["expected_games"]
    result = result[[
        "player_id", "player_name", "position", "target_season",
        "predicted_ppg_ppr", "expected_games", "projected_points",
    ]]

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE inhouse_projections AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM inhouse_projections").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: inhouse_projections)")


if __name__ == "__main__":
    build_inhouse_projections()
