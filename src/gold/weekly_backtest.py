"""Score a weekly signal against what a week actually returned — issue #131, under the "what is
actually predictive" epic (#114).

Every backtest already in this warehouse is season-scale: `inhouse_backtest` walks forward one fold
per season, `punter_backtest` the same, `draft_strategy`'s field simulation clusters its
significance test by season. None of them can answer "does knowing X on Saturday improve Sunday's
estimate" — that needs a signal keyed on (player, season, week), scored week by week. Without one
harness, each of #132/#133/#134 would build its own evaluation loop and the results would not be
comparable, which is most of the value of measuring this at all.

`score_signal` is the one entry point. It asks the same question for every candidate signal,
holding each baseline fixed: given what a manager already has — season-to-date PPG, last-3 PPG, the
week's own vendor projection — does the signal explain any of what that baseline misses? That's
"incremental" framing rather than raw correlation, because a signal can track weekly scoring
strongly and add nothing once the projection has already priced it in — the same trap
`player_archetypes.py` found most of its archetype effect was ("a restatement of ADP").

Two of the three baselines are walk-forward computations this module owns rather than trusts: a
season-to-date average that included the week being predicted would leak the label into its own
baseline, silently, in every one of the four measurement tickets that would otherwise each
re-derive it slightly differently. `_walk_forward_baselines` is the single place that computation
happens. The third baseline, the vendor's own weekly projection, needs no such treatment — it's
published before kickoff by construction of its source, not derived from this warehouse's own
history.

Every correlation reported — not just its significance — is clustered by week rather than pooled
across the whole sample: weather and game environment correlate outcomes within a week the same way
one season's injuries correlate every RB-heavy draft in `draft_strategy.py`, so a correlation pooled
across weeks can pick up cross-week structure (a season-long scoring drift, bye-week timing) that has
nothing to do with the within-week relationship being asked about. `_score_group` computes one
correlation per eligible week and reports the mean, which is also what the significance test and
confidence interval run on — the headline number and the test attached to it are the same quantity,
not two different ones that happen to sit in the same row. A week with too few player-weeks to
compute a meaningful within-week correlation is excluded rather than propagating a noisy value into
the average — the reason `n` (player-weeks in the sample) and `n_weeks` (weeks that actually
contributed) are both reported on every row, alongside every other number, rather than trusted to be
adequate.
"""

import pandas as pd
from scipy import stats

# Below this many player-weeks, a within-week correlation is a handful of points read as a trend —
# a fifth to a quarter of a typical single-position week in real data, chosen so the clustering step
# excludes a week rather than let one blow out the average with a spurious +-1.0.
_MIN_WEEK_ROWS = 5

_BASELINE_COLUMNS = ["season_to_date_ppg", "last3_ppg", "sleeper_points"]


def _walk_forward_baselines(actuals: pd.DataFrame) -> pd.DataFrame:
    """Attach `season_to_date_ppg` and `last3_ppg` to `actuals`, each computed from strictly prior
    weeks in the same player-season only. Assumes one row per (player_id, season, week) with no
    gaps in the weeks a player actually has a row for — the walk forward is "the prior row in this
    player-season", not "week number minus one".
    """
    actuals = actuals.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    points_by_season = actuals.groupby(["player_id", "season"])["actual_points"]
    actuals["season_to_date_ppg"] = points_by_season.transform(
        lambda points: points.shift(1).expanding().mean()
    )
    actuals["last3_ppg"] = points_by_season.transform(
        lambda points: points.shift(1).rolling(3).mean()
    )
    return actuals


def _rho(a: pd.Series, b: pd.Series) -> float:
    """Spearman correlation, or NaN rather than a runtime warning when either side has no spread
    to rank — the same silent-NaN convention `inhouse_projections.py`'s metrics already use for a
    fold too small or too uniform to correlate."""
    if len(a) < 2 or a.nunique() < 2 or b.nunique() < 2:
        return float("nan")
    return stats.spearmanr(a, b).statistic


def _score_group(group: pd.DataFrame, baseline_col: str) -> dict:
    """Score one (position, baseline) slice as a per-week average, not one correlation pooled across
    every row. Pooling across weeks is not the same computation as averaging one number per week —
    unlike a mean, a correlation pooled across the clustering variable can pick up cross-week
    structure (a season-long scoring drift, bye-week timing) that clustering exists specifically to
    keep out, so `baseline_rho` (the baseline's own accuracy), `signal_rho` (the signal's raw
    accuracy) and `incremental_rho` (the headline: the signal against what the baseline's error
    leaves unexplained) are each the mean of one number per eligible week, the same population the
    significance test below runs on.
    """
    weekly = []
    for _, week_group in group.groupby(["season", "week"]):
        if len(week_group) < _MIN_WEEK_ROWS:
            continue
        week_residual = week_group["actual_points"] - week_group[baseline_col]
        weekly.append({
            "baseline_rho": _rho(week_group[baseline_col], week_group["actual_points"]),
            "signal_rho": _rho(week_group["signal_value"], week_group["actual_points"]),
            "incremental_rho": _rho(week_group["signal_value"], week_residual),
        })
    weekly = pd.DataFrame(weekly, columns=["baseline_rho", "signal_rho", "incremental_rho"])

    incremental = weekly["incremental_rho"].dropna()
    if len(incremental) > 1:
        t_stat, p_value = stats.ttest_1samp(incremental, 0.0)
        ci_low, ci_high = stats.t.interval(
            0.95, df=len(incremental) - 1, loc=incremental.mean(), scale=stats.sem(incremental),
        )
    else:
        t_stat = p_value = ci_low = ci_high = float("nan")

    return {
        "n": len(group),
        "n_weeks": len(incremental),
        "baseline_rho": weekly["baseline_rho"].mean(),
        "signal_rho": weekly["signal_rho"].mean(),
        "incremental_rho": incremental.mean() if len(incremental) else float("nan"),
        "t_stat": t_stat,
        "p_value": p_value,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def score_signal(
    signal: pd.DataFrame, actuals: pd.DataFrame, projection: pd.DataFrame
) -> pd.DataFrame:
    """Score `signal` — one row per (player_id, season, week) with a `signal_value` — against every
    baseline, per position and pooled.

    `actuals` is the labelled history: one row per (player_id, season, week, position,
    actual_points). `projection` is the week's own vendor projection: one row per (player_id,
    season, week, sleeper_points), taken as given rather than walk-forward computed. A row missing
    the signal or the outcome is dropped everywhere; a row missing one particular baseline (e.g.
    `last3_ppg` before a player's third prior week) is dropped only from that baseline's own
    population, so a stricter baseline's ramp-up doesn't also shrink a looser one's.

    Returns one row per (position, baseline), plus a pooled "ALL" row per baseline — pooling alone
    would hide a signal that works for one position and not another, the way `inhouse_backtest`
    already reports "ALL" alongside every position for the same reason.
    """
    joined = (
        signal.merge(_walk_forward_baselines(actuals), on=["player_id", "season", "week"])
        .merge(projection, on=["player_id", "season", "week"])
        .dropna(subset=["signal_value", "actual_points"])
    )

    rows = []
    for baseline_col in _BASELINE_COLUMNS:
        # Dropped per baseline rather than once up front: last3_ppg isn't defined until a player's
        # third prior week, which would otherwise cut season_to_date_ppg's first eligible week out
        # of every baseline's population just because a stricter one wasn't ready yet.
        scored = joined.dropna(subset=[baseline_col])
        for position, group in scored.groupby("position"):
            rows.append({
                "position": position, "baseline": baseline_col, **_score_group(group, baseline_col)
            })
        rows.append({
            "position": "ALL", "baseline": baseline_col, **_score_group(scored, baseline_col)
        })
    return pd.DataFrame(rows)
