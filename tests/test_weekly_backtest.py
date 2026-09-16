"""Weekly backtest harness — issue #131, under the "what is actually predictive" epic (#114).

Every backtest in the warehouse before this one is season-scale (`inhouse_backtest`,
`punter_backtest`, `draft_strategy`'s walk-forward). Nothing scores a *weekly* signal — keyed on
(player, season, week) — against what a week actually returned. `score_signal` is that harness:
given a signal, does it improve on the baselines a fantasy manager already has, holding each
baseline fixed?

Full test list (see the PR body for the same list with rationale):
 1. `_walk_forward_baselines` computes `season_to_date_ppg` as the mean of strictly prior weeks
 2. `_walk_forward_baselines` computes `last3_ppg` as the mean of up to the three prior weeks
 3. A player's first season week has no walk-forward baseline and is dropped from scoring
 4. `score_signal` returns one row per position per baseline, plus a pooled "ALL" row
 5. A signal that *is* the actual outcome (the leak) scores a very high incremental correlation
    against every baseline, with a small p-value — the harness's own correctness check
 6. A signal that is pure noise scores an incremental correlation near zero and an insignificant
    p-value — the other half of the correctness check
 7. A signal predictive for one position and pure noise for another scores them differently
 8. Significance clusters by week: `n_weeks` counts distinct weeks contributing to the test, not
    player-week rows, and a single-week sample can't produce a t-test (NaN, not a crash)
 9. Every row of the output carries `n` and `n_weeks`, whatever their values

Every fixture is a small hand-built DataFrame — no warehouse — since these are exactly the kind of
"deliberately-leaked" and "deliberately-null" constructions the ticket calls for as ground truth
independent of the implementation.
"""

import numpy as np
import pandas as pd

from src.gold.weekly_backtest import _walk_forward_baselines, score_signal


def _actuals(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "season", "week", "position", "actual_points"])


# 1. season_to_date_ppg for a given week is the mean of that player's strictly prior weeks in the
#    same season, not an average that includes the week itself.
def test_season_to_date_ppg_excludes_the_current_week():
    actuals = _actuals([
        ("p1", 2024, 1, "RB", 10.0),
        ("p1", 2024, 2, "RB", 20.0),
        ("p1", 2024, 3, "RB", 0.0),
    ])

    out = _walk_forward_baselines(actuals).set_index("week")

    assert out.loc[2, "season_to_date_ppg"] == 10.0
    assert out.loc[3, "season_to_date_ppg"] == 15.0


# 2. last3_ppg for a given week is the mean of up to the three prior weeks, never the current one,
#    and never reaching back further than three.
def test_last3_ppg_uses_only_the_three_prior_weeks():
    actuals = _actuals([
        ("p1", 2024, 1, "RB", 4.0),
        ("p1", 2024, 2, "RB", 8.0),
        ("p1", 2024, 3, "RB", 12.0),
        ("p1", 2024, 4, "RB", 100.0),
        ("p1", 2024, 5, "RB", 0.0),
    ])

    out = _walk_forward_baselines(actuals).set_index("week")

    # Week 4: mean of weeks 1-3, not yet three full weeks of history beyond that window.
    assert out.loc[4, "last3_ppg"] == (4.0 + 8.0 + 12.0) / 3
    # Week 5: mean of weeks 2-4 — the window has slid forward, week 1 has fallen out of it.
    assert out.loc[5, "last3_ppg"] == (8.0 + 12.0 + 100.0) / 3


# 3. A player's first week in a season has no prior week to average, so both baselines are NaN —
#    the walk-forward computation has nothing to walk forward from yet.
def test_first_week_of_a_season_has_no_walk_forward_baseline():
    actuals = _actuals([("p1", 2024, 1, "RB", 10.0)])

    out = _walk_forward_baselines(actuals)

    assert out["season_to_date_ppg"].isna().all()
    assert out["last3_ppg"].isna().all()


# 4. Two seasons for the same player are independent: season_to_date_ppg resets rather than
#    carrying points from the prior season's history into the new one's week 1.
def test_baselines_reset_at_a_new_season():
    actuals = _actuals([
        ("p1", 2023, 1, "RB", 10.0),
        ("p1", 2023, 2, "RB", 10.0),
        ("p1", 2024, 1, "RB", 5.0),
        ("p1", 2024, 2, "RB", 5.0),
    ])

    out = _walk_forward_baselines(actuals).set_index(["season", "week"])

    assert pd.isna(out.loc[(2024, 1), "season_to_date_ppg"])
    assert out.loc[(2024, 2), "season_to_date_ppg"] == 5.0


def _weekly_fixture(
    n_weeks: int, n_players: int, positions: dict[str, str], seed: int, base_spread: float = 6.0,
) -> dict:
    """A multi-week, multi-player fixture big enough to run real correlations and a week-clustered
    t-test on. `positions` maps player_id -> position; every player gets a row in every week.

    `base_spread` controls each player's fixed per-player mean, drawn from
    `uniform(12 - base_spread, 12 + base_spread)`. Left wide (the default) for tests that only need
    plausible-looking weekly data. Narrowed to 0 — every player sharing the same mean — for the
    leak/null correctness checks below: `season_to_date_ppg` converges toward that shared mean
    regardless, so with `base_spread=0` its residual (`actual - baseline`) isolates each week's own
    noise term instead of being dominated by which player it is, which is what a raw-points leak
    needs to show up as *suspiciously* correlated rather than merely somewhat correlated.

    Baselines are deliberately unrelated to the eventual signal/actual construction in each test:
    `sleeper_points` is the same per-player mean plus its own independent noise, so a test grafting
    a specific actual/signal relationship on top isn't fighting a baseline built to match it.
    """
    rng = np.random.default_rng(seed)
    player_ids = list(positions)
    actual_rows, projection_rows = [], []
    base_ppg = {
        player_id: rng.uniform(12.0 - base_spread, 12.0 + base_spread) for player_id in player_ids
    }
    for week in range(1, n_weeks + 1):
        for player_id in player_ids:
            actual = max(0.0, base_ppg[player_id] + rng.normal(0.0, 3.0))
            actual_rows.append((player_id, 2024, week, positions[player_id], actual))
            projection_rows.append(
                (player_id, 2024, week, base_ppg[player_id] + rng.normal(0.0, 3.0))
            )
    return {
        "actuals": _actuals(actual_rows),
        "projection": pd.DataFrame(
            projection_rows, columns=["player_id", "season", "week", "sleeper_points"]
        ),
    }


# 5. The output carries one row per position per baseline, plus a pooled "ALL" row per baseline —
#    three baselines x (two positions + ALL) here.
def test_output_has_one_row_per_position_and_baseline_plus_pooled():
    fixture = _weekly_fixture(
        n_weeks=6, n_players=10, positions={f"p{i}": ("RB" if i % 2 else "WR") for i in range(10)},
        seed=1,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = np.random.default_rng(2).normal(size=len(signal))

    out = score_signal(signal, fixture["actuals"], fixture["projection"])

    assert set(out["baseline"]) == {"season_to_date_ppg", "last3_ppg", "sleeper_points"}
    assert set(out["position"]) == {"RB", "WR", "ALL"}
    assert len(out) == 3 * 3


# 6. The harness's own correctness check: a signal that *is* the actual outcome (the leak) scores a
#    high incremental correlation against every baseline, with a small p-value.
def test_a_leaked_signal_scores_high_and_significant():
    fixture = _weekly_fixture(
        n_weeks=10, n_players=12, positions={f"p{i}": "RB" for i in range(12)}, seed=3,
        base_spread=0.0,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = fixture["actuals"]["actual_points"]

    out = score_signal(signal, fixture["actuals"], fixture["projection"])

    for _, row in out[out["position"] == "ALL"].iterrows():
        # The sleeper_points baseline carries its own independent noise on top of the actual's, so
        # even a full leak's residual correlation sits well under 1.0 against it — the bar has to
        # clear every baseline, so it's set below the softest of the three rather than the best.
        assert row["incremental_rho"] > 0.5
        assert row["p_value"] < 0.01


# 7. The other half of the correctness check: a signal that is pure noise, unrelated to the
#    outcome, scores near zero and doesn't clear significance.
def test_a_random_signal_scores_near_zero_and_insignificant():
    fixture = _weekly_fixture(
        n_weeks=10, n_players=12, positions={f"p{i}": "RB" for i in range(12)}, seed=4,
        base_spread=0.0,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = np.random.default_rng(5).normal(size=len(signal))

    out = score_signal(signal, fixture["actuals"], fixture["projection"])

    for _, row in out[out["position"] == "ALL"].iterrows():
        assert abs(row["incremental_rho"]) < 0.3
        assert row["p_value"] > 0.05


# 8. A signal predictive for one position and pure noise for another scores them differently —
#    pooling into "ALL" alone would hide exactly this.
def test_a_signal_predictive_for_one_position_scores_it_higher():
    positions = {**{f"rb{i}": "RB" for i in range(12)}, **{f"wr{i}": "WR" for i in range(12)}}
    fixture = _weekly_fixture(n_weeks=10, n_players=24, positions=positions, seed=6, base_spread=0.0)
    actuals = fixture["actuals"]
    signal = actuals[["player_id", "season", "week"]].copy()
    rng = np.random.default_rng(7)
    is_rb = (actuals["position"] == "RB").to_numpy()
    # RBs: signal tracks the actual outcome. WRs: signal is unrelated noise.
    signal["signal_value"] = np.where(is_rb, actuals["actual_points"], rng.normal(size=len(signal)))

    out = score_signal(signal, actuals, fixture["projection"])
    rb_row = out[(out["position"] == "RB") & (out["baseline"] == "season_to_date_ppg")].iloc[0]
    wr_row = out[(out["position"] == "WR") & (out["baseline"] == "season_to_date_ppg")].iloc[0]

    assert rb_row["incremental_rho"] > wr_row["incremental_rho"] + 0.4


# 9. Significance clusters by week: n_weeks counts distinct weeks contributing to the t-test, not
#    player-week rows, and a sample that can't produce more than one clustered week returns NaN
#    significance rather than a spuriously confident number.
def test_significance_clusters_by_week_and_degrades_to_nan_with_one_week():
    fixture = _weekly_fixture(
        n_weeks=8, n_players=10, positions={f"p{i}": "RB" for i in range(10)}, seed=8,
        base_spread=0.0,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = fixture["actuals"]["actual_points"]

    out = score_signal(signal, fixture["actuals"], fixture["projection"])
    row = out[(out["position"] == "ALL") & (out["baseline"] == "season_to_date_ppg")].iloc[0]

    # Week 1 has no season_to_date_ppg (nothing to walk forward from yet), so 7 of the 8 weeks are
    # eligible, each with 10 rows — comfortably clear of the minimum for every one to count.
    assert row["n_weeks"] == 7
    assert row["n"] == 10 * 7

    one_week_signal = signal[signal["week"] == 2]
    one_week_actuals = fixture["actuals"][fixture["actuals"]["week"].isin([1, 2])]
    one_week_projection = fixture["projection"][fixture["projection"]["week"] == 2]
    out_one_week = score_signal(one_week_signal, one_week_actuals, one_week_projection)
    row_one_week = out_one_week[
        (out_one_week["position"] == "ALL") & (out_one_week["baseline"] == "season_to_date_ppg")
    ].iloc[0]

    assert row_one_week["n_weeks"] == 1
    assert pd.isna(row_one_week["t_stat"])
    assert pd.isna(row_one_week["p_value"])
    assert pd.isna(row_one_week["ci_low"])
    assert pd.isna(row_one_week["ci_high"])


# 11. The acceptance criterion is "sample sizes *and confidence intervals* on every output": a row
#     with enough clustered weeks to run the significance test also reports a 95% CI on the same
#     clustered effect, bracketing the mean and ordered low <= high.
def test_a_confidence_interval_is_reported_alongside_significance():
    fixture = _weekly_fixture(
        n_weeks=10, n_players=12, positions={f"p{i}": "RB" for i in range(12)}, seed=3,
        base_spread=0.0,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = fixture["actuals"]["actual_points"]

    out = score_signal(signal, fixture["actuals"], fixture["projection"])
    row = out[(out["position"] == "ALL") & (out["baseline"] == "season_to_date_ppg")].iloc[0]

    assert row["ci_low"] < row["ci_high"]
    # A full leak's effect is nowhere near zero, so the interval shouldn't cross it — the same
    # information the small p_value already carries, read as a range instead of a threshold.
    assert row["ci_low"] > 0


# 10. Every row carries its sample sizes, whatever their values — the acceptance criterion that a
#     result is only as good as the n it's read alongside.
def test_every_row_reports_n_and_n_weeks():
    fixture = _weekly_fixture(
        n_weeks=6, n_players=10, positions={f"p{i}": ("RB" if i % 2 else "WR") for i in range(10)},
        seed=9,
    )
    signal = fixture["actuals"][["player_id", "season", "week"]].copy()
    signal["signal_value"] = np.random.default_rng(10).normal(size=len(signal))

    out = score_signal(signal, fixture["actuals"], fixture["projection"])

    assert out["n"].notna().all()
    assert out["n_weeks"].notna().all()
    assert (out["n"] > 0).all()
