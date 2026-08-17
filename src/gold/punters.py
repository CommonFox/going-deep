"""Score, project, and backtest punters under the ESPN league's punting rules.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from nflverse weekly
stats and the punt play-by-play archive (`weekly_stats`, `pbp_punts`, both loaded by nfl_data.py),
team assignments from `rosters`, and the scoring itself from `league_settings`.

Punter is the one position in this warehouse no external source prices. FantasyPros, CBS, FFToday,
FFC and Sleeper don't rank punters at all; ESPN projects them, but only for its own scoring and
only for the upcoming season, so there is nothing to backtest it against and nothing to blend it
with. That makes this the only model here that has to stand entirely on its own.

## What the scoring rewards, and what that implies

The ESPN league scores a punt at 1 point, +2 inside the 20, a further +3 inside the 10, -1 if it's
returned, -1 for a touchback, +0.5 for a fair catch, and a per-*game* bonus of 3/2/1 for finishing
a game averaging 44.0+ / 42.0-43.9 / 40.0-41.9 gross. Read together, a punter's season is close to

    points ≈ punts × (1 + 2·in20_rate + 3·in10_rate - returned_rate - touchback_rate
                        + 0.5·fair_catch_rate)  +  games × tier_bonus_per_game

so **volume dominates**: across 2015-2025, season points correlate 0.70 with punt attempts and
only 0.07 with gross average. A big leg is worth roughly 18 points of tier bonus a season between
the best and worst starters; punting 20 more times is worth more than that on the flat rate alone.

## Why volume is modelled from the team, not the punter

Punt volume is a property of a bad offense, not of the man kicking. Team punts-per-game is as
stable year over year (r=0.47) as any individual punter's (r=0.48), and the two are near enough
the same number for a punter who stays put. The tell is the 33 punters who changed teams: their
next-season volume tracks their **new** team's prior punt rate (r=0.37) better than their own
prior rate (r=0.27) or their old team's (r=0.30). So volume is carried by the target season's team
and rates are carried by the punter, which is the whole reason this beats "project last year
forward".

## Why shrinkage rather than a learner

Every per-punt rate is weakly self-correlated year to year — inside-20 rate r=0.21, inside-10
r=0.17, touchback rate r=0.06 — against ~250 usable consecutive punter-season pairs in total. A
gradient-booster on 250 rows and six barely-autocorrelated features would fit noise. So each
component is instead an empirical-Bayes estimate: the punter's own history, shrunk toward the
league rate by a constant `k` expressed in punts-of-prior-evidence, with `k` fit on the training
seasons only. A stat that doesn't persist gets shrunk almost to the mean, which is the correct
answer for it, and the model degrades gracefully to "everyone is average" rather than inventing
edges it can't support.

Gross average is the exception worth keeping (r=0.49, easily the most persistent thing a punter
does), and it feeds the tier bonus through a straight line fit on training seasons — season gross
average predicts realized bonus-per-game at r=0.82.

## Games played: not from the punter's history, from whether the job is his

Games played is the largest single term in a punter's season and the least predictable from his own
record — it self-correlates at r=0.09, so carrying last year's games forward is worthless. What
does predict it is whether the job is already his. Split the week-1-rostered punters three ways by
what they were doing the season before:

    incumbent  (same team punted for last year)   n=233   15.3 games   148 points
    new team   (punted elsewhere last year)       n= 65   11.9 games   115 points
    unproven   (didn't punt last year)            n= 68   12.6 games   119 points

That's a 33-point gap between an incumbent and everyone else — wider than the entire spread from
the best starting punter to the twelfth — and it is known at draft time. So expected games comes
from the incumbency group, fit on training seasons, and every other component is a per-game or
per-punt rate multiplied through it.

Even so, games played is what caps this model. Handed the true games count, its season projections
score MAE 20 and rank correlation 0.67; forced to predict games, MAE 38 and rank correlation 0.33.
Almost all the remaining error is not knowing who keeps the job in November.

One caveat on the level: fold bias runs from -14 to +29 points by season, averaging +9, because how
many games a punter lasts and how much a punt is worth both drift year to year (2020 and 2021 were
low, 2022 high). Predicted games track actual to within about one game per season, so this is
league-environment noise rather than a fixable miscalibration — and it's a level shift, which
leaves the ordering, and therefore the draft decision, untouched.

## Tables

- `punter_seasons` — every punter-season 2015-2025, scored week by week under league rules (the
  tier bonus is per game, so it cannot be computed from season totals), with the volume and rate
  components each projection is built out of.
- `punter_projections` — the upcoming season, one row per rostered punter, carrying this model's
  projection *and* ESPN's as separate named columns alongside the blend, so a disagreement between
  the two stays visible instead of being averaged away.
- `punter_backtest` — walk-forward accuracy against three naive baselines.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# The only league here that starts a punter. Sleeper doesn't offer the position, so its
# league_settings row prices every punting stat at zero and would score every punter 0.0.
_LEAGUE_KEY = "espn"

# The season being projected — no stats yet, but rosters are published. Matches nfl_data.py's
# _UPCOMING_SEASON; bump both together.
_UPCOMING_SEASON = 2026

# A punter-season needs this many games before its rates are trusted as history. Punting rates off
# three games are mostly noise, and a fill-in's stat line shouldn't set a career prior.
_MIN_GAMES_FOR_HISTORY = 6

# How far back history reaches, and how much each season counts. Shallower than player_baselines'
# four years: punters change teams and legs age, and the fourth year back added nothing in testing.
_MAX_YEARS_BACK = 3
_RECENCY_WEIGHTS = {1: 1.0, 2: 0.9, 3: 0.8}

# First season to backtest. Needs enough prior seasons to fit shrinkage constants and give punters
# a history at all, which 2015-2018 provides.
_FIRST_BACKTEST_SEASON = 2019

# Per-punt rates the model carries, mapped to the league_settings column that prices each one.
# Every punt also scores the flat `punt_pts`, which needs no rate.
_PUNT_RATES = {
    "in10_rate": "punt_in10_pts",
    "in20_rate": "punt_in20_pts",
    "blocked_rate": "punt_blocked_pts",
    "returned_rate": "punt_returned_pts",
    "touchback_rate": "punt_touchback_pts",
    "fair_catch_rate": "punt_fair_catch_pts",
}

# Candidate shrinkage strengths, in punts of prior evidence. A punter has ~60-90 punts a season, so
# k=200 means "trust three seasons of your own history about as much as the league mean".
_K_GRID = [10, 25, 50, 100, 200, 400, 800, 1600, 3200]

# Same idea for team punt volume, in games of prior evidence.
_TEAM_K_GRID = [2, 4, 8, 16, 32, 64, 128]

# How settled a punter's job is going into the season, which is what expected games is drawn from.
_INCUMBENT, _NEW_TEAM, _UNPROVEN = "incumbent", "new_team", "unproven"


def _scoring(con: duckdb.DuckDBPyConnection) -> pd.Series:
    return con.sql(
        f"SELECT * FROM league_settings WHERE league_key = '{_LEAGUE_KEY}'"
    ).df().iloc[0]


def _weekly_sql(scoring: pd.Series) -> str:
    """Per-week punter lines with league points attached.

    The tier bonus is awarded per game, so points have to be built week by week and summed; scoring
    a season's totals would hand a punter one bonus for the year instead of seventeen.

    Inside-the-10 is the one scoring category no per-player feed carries, so it's counted off the
    punt play-by-play: a punt is inside the 10 when nflverse flags it inside the 20 and the
    receiving team's resulting spot — where the ball landed, plus whatever the returner gained —
    is short of their own 10. Validated against ESPN's own 2025 actuals for all 36 punters it
    published: exact for 28, within one punt for 35, nine punts of total error league-wide.

    pbp carries ~0.5% more punts than weekly_stats does (nullified and aborted plays), so the
    derived inside-10 count is capped at weekly_stats' inside-20 count, which is the authority
    here — it reproduces ESPN's Net Punts and Punts Inside the 20 exactly.
    """
    return f"""
    WITH inside_ten AS (
        SELECT
            punter_player_id AS player_id, season, week,
            COUNT(*) FILTER (
                WHERE punt_inside_twenty = 1
                  AND yardline_100 - kick_distance + COALESCE(return_yards, 0) < 10
            ) AS in10
        FROM pbp_punts
        WHERE season_type = 'REG'
        GROUP BY punter_player_id, season, week
    )
    SELECT
        w.player_id,
        w.player_display_name AS player_name,
        w.season,
        w.week,
        w.team,
        w.pt_att AS punts,
        w.pt_yards AS punt_yards,
        w.pt_inside_20 AS in20,
        LEAST(COALESCE(t.in10, 0), w.pt_inside_20) AS in10,
        w.pt_blocked AS blocked,
        w.pt_returned AS returned,
        w.pt_touchback AS touchbacks,
        w.pt_fair_caught AS fair_catches,
        w.pt_net_yards AS net_yards,
        w.pt_att * {scoring.punt_pts}
            + LEAST(COALESCE(t.in10, 0), w.pt_inside_20) * {scoring.punt_in10_pts}
            + w.pt_inside_20 * {scoring.punt_in20_pts}
            + w.pt_blocked * {scoring.punt_blocked_pts}
            + w.pt_returned * {scoring.punt_returned_pts}
            + w.pt_touchback * {scoring.punt_touchback_pts}
            + w.pt_fair_caught * {scoring.punt_fair_catch_pts}
            + CASE
                WHEN w.pt_yards / w.pt_att >= 44 THEN {scoring.punt_avg44_pts}
                WHEN w.pt_yards / w.pt_att >= 42 THEN {scoring.punt_avg42_pts}
                WHEN w.pt_yards / w.pt_att >= 40 THEN {scoring.punt_avg40_pts}
                ELSE 0.0
              END AS points
    FROM weekly_stats w
    LEFT JOIN inside_ten t
        ON t.player_id = w.player_id AND t.season = w.season AND t.week = w.week
    WHERE w.season_type = 'REG' AND w.pt_att > 0
    """


_SEASON_SQL = """
SELECT
    player_id,
    ANY_VALUE(player_name) AS player_name,
    season,
    -- A punter who was traded mid-season is credited to whoever he punted most for.
    MODE(team) AS team,
    COUNT(*) AS games,
    SUM(punts) AS punts,
    SUM(points) AS points,
    SUM(points) / COUNT(*) AS points_per_game,
    SUM(punts) / COUNT(*) AS punts_per_game,
    SUM(punt_yards) / SUM(punts) AS gross_average,
    SUM(net_yards) / SUM(punts) AS net_average,
    SUM(in10) / SUM(punts) AS in10_rate,
    SUM(in20) / SUM(punts) AS in20_rate,
    SUM(blocked) / SUM(punts) AS blocked_rate,
    SUM(returned) / SUM(punts) AS returned_rate,
    SUM(touchbacks) / SUM(punts) AS touchback_rate,
    SUM(fair_catches) / SUM(punts) AS fair_catch_rate,
    SUM(in10) AS in10, SUM(in20) AS in20, SUM(blocked) AS blocked,
    SUM(returned) AS returned, SUM(touchbacks) AS touchbacks,
    SUM(fair_catches) AS fair_catches, SUM(punt_yards) AS punt_yards
FROM weekly_punts
GROUP BY player_id, season
ORDER BY season, points DESC
"""

# Which team each punter is attached to going into a season. The week-1 roster is what a drafter
# actually knows, and for the upcoming season it's the only thing there is; `rosters` carries the
# preseason snapshot before a down is played. Taking the earliest week present per season handles
# both the played seasons and the upcoming one, whose snapshot isn't labelled week 1.
_ROSTER_TEAM_SQL = """
SELECT player_id, player_name, season, team, years_exp, age
FROM (
    SELECT
        player_id, player_name, season, team, years_exp, age,
        ROW_NUMBER() OVER (PARTITION BY season, player_id ORDER BY week) AS rn
    FROM rosters
    WHERE position = 'P' AND status = 'ACT' AND player_id IS NOT NULL
) WHERE rn = 1
"""


def _recency_weight(gap: pd.Series) -> pd.Series:
    return gap.map(_RECENCY_WEIGHTS).astype(float)


def _incumbency(seasons: pd.DataFrame, roster: pd.DataFrame, target: int) -> pd.DataFrame:
    """Label each punter on `target`'s week-1 roster by whether the job is already his.

    Only the immediately preceding season counts. A punter who held a job two years ago, sat out
    last year and has now signed somewhere is in the same position as a rookie as far as keeping
    the job for seventeen weeks goes, and the data agrees — both land near 12.5 games.
    """
    prior = seasons[seasons["season"] == target - 1].set_index("player_id")
    players = roster[roster["season"] == target].copy()
    prior_team = players["player_id"].map(prior["team"])
    players["incumbency"] = np.where(
        prior_team.isna(), _UNPROVEN,
        np.where(prior_team == players["team"], _INCUMBENT, _NEW_TEAM),
    )
    return players


def _games_by_incumbency(seasons: pd.DataFrame, roster: pd.DataFrame, target: int) -> pd.Series:
    """Mean games played per incumbency group, over every training season.

    Fit on the same universe the projection is asked about — punters who were on a week-1 roster
    and went on to punt at all — rather than on the qualifying-history universe. Fitting it on
    seasons that cleared the six-game history bar instead ran the whole model ~19 points high,
    because it quietly assumed away the fill-ins and midseason benchings that the bar excludes.
    """
    universe = []
    for season in sorted(s for s in seasons["season"].unique() if s < target):
        labelled = _incumbency(seasons, roster, season)
        played = seasons[seasons["season"] == season].set_index("player_id")["games"]
        labelled["games"] = labelled["player_id"].map(played)
        universe.append(labelled.dropna(subset=["games"]))

    if not universe:
        return pd.Series(dtype=float)
    pooled = pd.concat(universe, ignore_index=True)
    means = pooled.groupby("incumbency")["games"].mean()
    return means.reindex([_INCUMBENT, _NEW_TEAM, _UNPROVEN]).fillna(pooled["games"].mean())


def _weighted_history(seasons: pd.DataFrame, target: int, key: str) -> pd.DataFrame:
    """Recency-weighted totals over the seasons preceding `target`, grouped by `key`.

    Totals rather than means: the shrinkage below works in units of evidence (punts, games), so it
    needs a numerator and a denominator, not a ratio that has already forgotten how much was behind
    it. The recency weight scales both, so a three-year-old season contributes 0.8 of a punt.
    """
    gap = target - seasons["season"]
    window = seasons[(gap >= 1) & (gap <= _MAX_YEARS_BACK)].copy()
    window["w"] = _recency_weight(target - window["season"])

    counted = ["punts", "punt_yards", "in10", "in20", "blocked", "returned",
               "touchbacks", "fair_catches", "games"]
    for column in counted:
        window[f"w_{column}"] = window[column] * window["w"]
    grouped = window.groupby(key)
    history = grouped[[f"w_{c}" for c in counted]].sum()
    history["seasons_used"] = grouped.size()
    return history.reset_index()


def _shrink(numerator: pd.Series, denominator: pd.Series, prior: float, k: float) -> pd.Series:
    """Empirical-Bayes rate: observed evidence pulled toward `prior` by `k` units of it."""
    return (numerator + k * prior) / (denominator + k)


def _fit_rate_k(history: pd.DataFrame, actual: pd.DataFrame, numerator: str,
                prior: float) -> float:
    """Pick the shrinkage strength that best predicts next season's rate, on training data only."""
    merged = history.merge(actual, on="player_id")
    if len(merged) < 20:
        return _K_GRID[len(_K_GRID) // 2]
    best_k, best_error = _K_GRID[0], np.inf
    for k in _K_GRID:
        predicted = _shrink(merged[f"w_{numerator}"], merged["w_punts"], prior, k)
        # Weighted by the punts the punter actually took that season: getting a rate right for a
        # 90-punt workhorse matters proportionally more than for someone who punted twice.
        error = np.average((predicted - merged["target_rate"]) ** 2, weights=merged["punts"])
        if error < best_error:
            best_k, best_error = k, error
    return best_k


def _fit_team_k(history: pd.DataFrame, actual: pd.DataFrame, prior: float) -> float:
    merged = history.merge(actual, on="team")
    if len(merged) < 20:
        return _TEAM_K_GRID[len(_TEAM_K_GRID) // 2]
    best_k, best_error = _TEAM_K_GRID[0], np.inf
    for k in _TEAM_K_GRID:
        predicted = _shrink(merged["w_punts"], merged["w_games"], prior, k)
        error = np.mean((predicted - merged["punts_per_game"]) ** 2)
        if error < best_error:
            best_k, best_error = k, error
    return best_k


def _fit(seasons: pd.DataFrame, roster: pd.DataFrame, target: int) -> dict:
    """Fit every constant the projection needs, using only seasons strictly before `target`."""
    train = seasons[seasons["season"] < target]
    qualified = train[train["games"] >= _MIN_GAMES_FOR_HISTORY]

    games_by_incumbency = _games_by_incumbency(seasons, roster, target)
    priors = {
        "punts_per_game": train["punts"].sum() / train["games"].sum(),
        "gross_average": train["punt_yards"].sum() / train["punts"].sum(),
        # The universe-wide mean, used only by the naive prior-PPG baseline; the model itself goes
        # through games_by_incumbency.
        "games": float(games_by_incumbency.mean()),
    }
    for rate, numerator in _RATE_NUMERATORS.items():
        priors[rate] = train[numerator].sum() / train["punts"].sum()

    # Shrinkage constants are fit on the *within-training* transition: history up to season S-1
    # predicting season S, for every S the training window can supply. Nothing from `target` is
    # touched, so the walk-forward stays clean.
    rate_pairs, team_pairs = [], []
    for season in sorted(qualified["season"].unique()):
        if season - _MAX_YEARS_BACK < train["season"].min():
            continue
        history = _weighted_history(qualified, season, "player_id")
        current = qualified[qualified["season"] == season]
        rate_pairs.append((history, current))
        team_history = _weighted_history(train, season, "team")
        team_current = (
            train[train["season"] == season].groupby("team")
            .agg(punts=("punts", "sum"), games=("games", "sum")).reset_index()
        )
        team_current["punts_per_game"] = team_current["punts"] / team_current["games"]
        team_pairs.append((team_history, team_current))

    ks = {}
    for rate, numerator in _RATE_NUMERATORS.items():
        histories = pd.concat([h for h, _ in rate_pairs], ignore_index=True) if rate_pairs else None
        actuals = (
            pd.concat(
                [c.assign(target_rate=c[numerator] / c["punts"])[
                    ["player_id", "target_rate", "punts"]]
                 for _, c in rate_pairs],
                ignore_index=True,
            ) if rate_pairs else None
        )
        ks[rate] = (
            _fit_rate_k(histories, actuals, numerator, priors[rate])
            if rate_pairs else _K_GRID[len(_K_GRID) // 2]
        )
    ks["gross_average"] = (
        _fit_rate_k(
            pd.concat([h for h, _ in rate_pairs], ignore_index=True),
            pd.concat(
                [c.assign(target_rate=c["punt_yards"] / c["punts"])[
                    ["player_id", "target_rate", "punts"]]
                 for _, c in rate_pairs],
                ignore_index=True,
            ),
            "punt_yards",
            priors["gross_average"],
        ) if rate_pairs else _K_GRID[len(_K_GRID) // 2]
    )
    ks["team"] = (
        _fit_team_k(
            pd.concat([h for h, _ in team_pairs], ignore_index=True),
            pd.concat([c for _, c in team_pairs], ignore_index=True),
            priors["punts_per_game"],
        ) if team_pairs else _TEAM_K_GRID[len(_TEAM_K_GRID) // 2]
    )

    # Season gross average -> realized tier bonus per game. Fit rather than simulated: a punter
    # averaging 46 doesn't clear 44.0 in every game, and the straight line absorbs that spread
    # (r=0.82, residual sd 0.19 against a bonus-per-game sd of 0.33).
    bonus = train[train["games"] >= _MIN_GAMES_FOR_HISTORY]
    slope, intercept = np.polyfit(bonus["gross_average"], bonus["tier_bonus_per_game"], 1)

    return {
        "priors": priors,
        "ks": ks,
        "bonus_fit": (slope, intercept),
        "games_by_incumbency": games_by_incumbency,
    }


# Which season-total column backs each rate.
_RATE_NUMERATORS = {
    "in10_rate": "in10",
    "in20_rate": "in20",
    "blocked_rate": "blocked",
    "returned_rate": "returned",
    "touchback_rate": "touchbacks",
    "fair_catch_rate": "fair_catches",
}


def _project(seasons: pd.DataFrame, roster: pd.DataFrame, target: int,
             scoring: pd.Series, fit: dict) -> pd.DataFrame:
    """Project every punter on `target`'s week-1 roster, from history before `target`."""
    priors, ks = fit["priors"], fit["ks"]
    train = seasons[seasons["season"] < target]
    qualified = train[train["games"] >= _MIN_GAMES_FOR_HISTORY]

    players = _incumbency(seasons, roster, target)[
        ["player_id", "player_name", "team", "years_exp", "age", "incumbency"]
    ].copy()

    history = _weighted_history(qualified, target, "player_id")
    out = players.merge(history, on="player_id", how="left").fillna(
        {f"w_{c}": 0.0 for c in ["punts", "punt_yards", "in10", "in20", "blocked",
                                 "returned", "touchbacks", "fair_catches", "games"]}
        | {"seasons_used": 0}
    )

    # Volume from the team the punter is joining, not from the punter.
    team_history = _weighted_history(train, target, "team")
    out = out.merge(team_history, on="team", how="left", suffixes=("", "_team"))
    for column in ["w_punts_team", "w_games_team"]:
        out[column] = out[column].fillna(0.0)
    out["team_punts_per_game"] = _shrink(
        out["w_punts_team"], out["w_games_team"], priors["punts_per_game"], ks["team"]
    )

    for rate, numerator in _RATE_NUMERATORS.items():
        out[rate] = _shrink(out[f"w_{numerator}"], out["w_punts"], priors[rate], ks[rate])
    out["gross_average"] = _shrink(
        out["w_punt_yards"], out["w_punts"], priors["gross_average"], ks["gross_average"]
    )

    out["expected_games"] = out["incumbency"].map(fit["games_by_incumbency"])
    out["expected_punts"] = out["expected_games"] * out["team_punts_per_game"]

    per_punt = float(scoring.punt_pts) + sum(
        out[rate] * float(scoring[column]) for rate, column in _PUNT_RATES.items()
    )
    slope, intercept = fit["bonus_fit"]
    out["tier_bonus_per_game"] = slope * out["gross_average"] + intercept
    out["projected_points"] = (
        out["expected_punts"] * per_punt
        + out["expected_games"] * out["tier_bonus_per_game"]
    )
    out["target_season"] = target
    return out


def _baselines(seasons: pd.DataFrame, projected: pd.DataFrame, target: int,
               fit: dict) -> pd.DataFrame:
    """Three naive predictions to beat, on the same universe the model projected."""
    train = seasons[seasons["season"] < target]
    prior = seasons[seasons["season"] == target - 1].set_index("player_id")

    out = projected[["player_id", "player_name", "team", "projected_points"]].copy()
    league_mean = train[train["games"] >= _MIN_GAMES_FOR_HISTORY]["points"].mean()
    out["baseline_league_mean"] = league_mean
    out["baseline_prior_points"] = (
        out["player_id"].map(prior["points"]).fillna(league_mean)
    )
    out["baseline_prior_ppg"] = (
        out["player_id"].map(prior["points_per_game"]).mul(fit["priors"]["games"])
        .fillna(league_mean)
    )
    return out


_PREDICTIONS = {
    "model": "projected_points",
    "prior_points": "baseline_prior_points",
    "prior_ppg": "baseline_prior_ppg",
    "league_mean": "baseline_league_mean",
}


def _walk_forward(seasons: pd.DataFrame, roster: pd.DataFrame,
                  scoring: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Project each season from the seasons before it, and score against what happened."""
    metrics, folds = [], []
    played = sorted(s for s in seasons["season"].unique() if s >= _FIRST_BACKTEST_SEASON)

    for target in played:
        fit = _fit(seasons, roster, target)
        projected = _project(seasons, roster, target, scoring, fit)
        fold = _baselines(seasons, projected, target, fit)

        actual = seasons[seasons["season"] == target].set_index("player_id")
        fold["actual_points"] = fold["player_id"].map(actual["points"])
        fold["actual_games"] = fold["player_id"].map(actual["games"])
        fold["target_season"] = target
        # Punters who were on a week-1 roster but never punted that season didn't play; there's no
        # outcome to score against, so they leave the fold rather than counting as a zero.
        fold = fold.dropna(subset=["actual_points"])
        folds.append(fold)

        for name, column in _PREDICTIONS.items():
            error = fold[column] - fold["actual_points"]
            # The league-mean baseline is one number repeated, so it has no ordering: its rank
            # correlation is undefined and its "top 5" would just be whichever rows nlargest
            # happened to return first. Left NULL rather than reported as a score it didn't earn.
            ranks = fold[column].nunique() > 1
            metrics.append({
                "target_season": target,
                "prediction": name,
                "punters": len(fold),
                "mae": error.abs().mean(),
                "rmse": float(np.sqrt((error ** 2).mean())),
                "spearman": (
                    fold[column].corr(fold["actual_points"], method="spearman")
                    if ranks else np.nan
                ),
                "top5_hits": _top_n_hits(fold, column, 5) if ranks else np.nan,
                # 10 is the decision boundary that matters: ten teams each start one punter, so
                # the question a draft actually asks is whether this punter is a starter at all.
                "top10_hits": _top_n_hits(fold, column, 10) if ranks else np.nan,
            })

    return pd.DataFrame(metrics), pd.concat(folds, ignore_index=True)


def _top_n_hits(fold: pd.DataFrame, column: str, n: int) -> int:
    """How many of the predicted top N finished in the actual top N."""
    predicted = set(fold.nlargest(n, column)["player_id"])
    actual = set(fold.nlargest(n, "actual_points")["player_id"])
    return len(predicted & actual)


@console.analysis
def _print_stability_report(seasons: pd.DataFrame) -> None:
    """Year-over-year self-correlation of each punting stat — the case for the model's shape."""
    full = seasons[seasons["games"] >= 12]
    nxt = full.copy()
    nxt["season"] -= 1
    pairs = full.merge(nxt, on=["player_id", "season"], suffixes=("", "_next"))

    print("\n  year-over-year stability (full-time punters, "
          f"n={len(pairs)} consecutive pairs)")
    print(f"    {'stat':22}{'r':>8}")
    for column in ["points", "points_per_game", "punts_per_game", "gross_average",
                   "net_average", "in20_rate", "in10_rate", "touchback_rate",
                   "fair_catch_rate", "returned_rate", "games"]:
        r = pairs[column].corr(pairs[f"{column}_next"])
        print(f"    {column:22}{r:8.3f}")


@console.analysis
def _print_backtest_report(metrics: pd.DataFrame) -> None:
    print(f"\n  walk-forward backtest ({metrics['target_season'].min()}"
          f"-{metrics['target_season'].max()}, season points)")
    pooled = (
        metrics.groupby("prediction")
        .agg(mae=("mae", "mean"), rmse=("rmse", "mean"), spearman=("spearman", "mean"),
             top5=("top5_hits", "sum"), top10=("top10_hits", "sum"),
             seasons=("target_season", "size"), punters=("punters", "mean"))
        .reindex(_PREDICTIONS)
    )
    print(f"    {'prediction':14}{'MAE':>8}{'RMSE':>8}{'rank r':>9}"
          f"{'top-5':>10}{'top-10':>11}")
    for name, row in pooled.iterrows():
        folds = int(row.seasons)
        if pd.isna(row.spearman):
            print(f"    {name:14}{row.mae:8.1f}{row.rmse:8.1f}{'--':>9}{'--':>10}{'--':>11}")
            continue
        print(f"    {name:14}{row.mae:8.1f}{row.rmse:8.1f}{row.spearman:9.3f}"
              f"{int(row.top5):>6}/{folds * 5:<3}{int(row.top10):>7}/{folds * 10:<3}")
    print(f"    ({pooled['punters'].iloc[0]:.0f} punters per season)")

    print(f"\n    {'season':8}" + "".join(f"{n:>14}" for n in _PREDICTIONS))
    for season, group in metrics.groupby("target_season"):
        by_name = group.set_index("prediction")["mae"]
        print(f"    {season:<8}" + "".join(f"{by_name[n]:>14.1f}" for n in _PREDICTIONS))
    print("    (MAE by season, lower is better)")


@console.analysis
def _print_projection_report(projections: pd.DataFrame, target: int) -> None:
    print(f"\n  {target} punter projections — top 15")
    columns = ["player_name", "team", "projected_points", "projected_points_espn",
               "expected_punts", "gross_average", "in20_rate"]
    top = projections.nlargest(15, "projected_points")[columns]
    print(f"    {'punter':20}{'tm':>4}{'model':>8}{'espn':>8}{'punts':>8}"
          f"{'gross':>8}{'in20':>8}")
    for _, row in top.iterrows():
        espn = f"{row.projected_points_espn:8.1f}" if pd.notna(
            row.projected_points_espn) else f"{'--':>8}"
        print(f"    {row.player_name[:20]:20}{str(row.team):>4}"
              f"{row.projected_points:8.1f}{espn}{row.expected_punts:8.1f}"
              f"{row.gross_average:8.1f}{row.in20_rate:8.3f}")


def build_punters() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    scoring = _scoring(con)

    con.execute(
        f"CREATE OR REPLACE TABLE punter_seasons AS "
        f"WITH weekly_punts AS ({_weekly_sql(scoring)}) {_SEASON_SQL}"
    )
    (season_count,) = con.execute("SELECT COUNT(*) FROM punter_seasons").fetchone()
    console.table("punter_seasons", season_count)

    seasons = con.sql("SELECT * FROM punter_seasons").df()
    # Realized bonus-per-game has to come off the weekly rows, since it's a per-game award; it's
    # what the gross-average -> bonus line is fit against.
    weekly = con.sql(f"SELECT * FROM ({_weekly_sql(scoring)})").df()
    weekly["tier_bonus"] = np.select(
        [weekly["punt_yards"] / weekly["punts"] >= 44,
         weekly["punt_yards"] / weekly["punts"] >= 42,
         weekly["punt_yards"] / weekly["punts"] >= 40],
        [float(scoring.punt_avg44_pts), float(scoring.punt_avg42_pts),
         float(scoring.punt_avg40_pts)],
        default=0.0,
    )
    bonus_by_season = (
        weekly.groupby(["player_id", "season"])["tier_bonus"].mean()
        .rename("tier_bonus_per_game").reset_index()
    )
    seasons = seasons.merge(bonus_by_season, on=["player_id", "season"], how="left")

    roster = con.sql(_ROSTER_TEAM_SQL).df()

    _print_stability_report(seasons)

    metrics, folds = _walk_forward(seasons, roster, scoring)
    con.execute("CREATE OR REPLACE TABLE punter_backtest AS SELECT * FROM metrics")
    console.table("punter_backtest", len(metrics))
    _print_backtest_report(metrics)

    # The live projection, fit on every played season.
    fit = _fit(seasons, roster, _UPCOMING_SEASON)
    projections = _project(seasons, roster, _UPCOMING_SEASON, scoring, fit)

    # ESPN is the only outside opinion that exists for this position; kept as its own named column
    # next to ours rather than folded in, so the two can be compared before they're trusted.
    # Joined through the `ids` crosswalk rather than on name — ESPN writes "JK Scott" where nflverse
    # writes "J.K. Scott", and a name join silently drops that kind of row. ESPN publishes 0.0 for
    # punters it isn't projecting at all (four of them this year); that's an absence, not a
    # forecast of zero, so it's nulled rather than blended in as a real number.
    espn = con.sql(f"""
        SELECT i.gsis_id AS player_id,
               NULLIF(e.projected_points, 0.0) AS projected_points_espn
        FROM espn_projections e
        JOIN ids i ON i.espn_id = e.espn_id
        WHERE e.position = 'P' AND e.season = {_UPCOMING_SEASON} AND i.gsis_id IS NOT NULL
    """).df()
    projections = projections.merge(espn, on="player_id", how="left")
    projections["projected_points_blended"] = projections[
        ["projected_points", "projected_points_espn"]
    ].mean(axis=1)
    projections = projections.sort_values("projected_points", ascending=False)

    output = projections[[
        "target_season", "player_id", "player_name", "team", "years_exp", "age", "incumbency",
        "projected_points", "projected_points_espn", "projected_points_blended",
        "expected_games", "expected_punts", "team_punts_per_game", "gross_average",
        "tier_bonus_per_game", "in10_rate", "in20_rate", "blocked_rate", "returned_rate",
        "touchback_rate", "fair_catch_rate", "seasons_used",
    ]]
    con.execute("CREATE OR REPLACE TABLE punter_projections AS SELECT * FROM output")
    console.table("punter_projections", len(output))
    _print_projection_report(projections, _UPCOMING_SEASON)

    con.close()


if __name__ == "__main__":
    build_punters()
