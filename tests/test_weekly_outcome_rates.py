"""weekly_outcome_rates — issue #122, under the weekly player context epic (#108).

Nothing in the warehouse describes how wide a player's weekly scoring actually is — only a single
point estimate (`weekly_projections`). `weekly_outcome_rates` is that table: one row per
(league_key, player_id, season, week) for QB/RB/WR/TE, carrying a ceiling rate / floor rate (how
often the player's own score cleared or missed a positional threshold derived from that league's own
scoring) and a median/floor/ceiling band read from the player's own game log, all computed strictly
from games before the row's own week.

Full test list (see the PR body for the same list with rationale):
 1. `_score_games` scores one player's raw stat line under the league's own coefficients, keeping
    identity columns (player_id, season, week, position, team) intact
 2. `_position_thresholds`: the first week a (season, position) has any games has a null
    ceiling/floor threshold — no prior evidence exists yet
 3. `_position_thresholds`: a week's threshold is computed only from strictly prior weeks' pooled
    points, never that week's own games
 4. `_position_thresholds` pools every player at the position that week regardless of team, not just
    one team's players
 5. `_position_thresholds` keeps two positions in the same season/week on separate pools
 6. `_position_thresholds` resets at a new season: the new season's first week has a null threshold
    even after a full prior season of data
 7. `_walk_forward_player`'s `games_observed` is 0 on a player's first game of a season
 8. `_walk_forward_player` ships the first-game row with `ceiling_rate`/`floor_rate`/`median`/
    `floor`/`ceiling` all null, rather than dropping the row
 9. `_walk_forward_player`'s `ceiling_rate` on the 2nd game reflects exactly whether the 1st game
    cleared its own week's threshold (1.0 cleared, 0.0 not)
10. `_walk_forward_player`'s `ceiling_rate`/`floor_rate` on a later game average the per-game
    clear/miss indicator across every strictly prior game
11. `_walk_forward_player` skips a prior game whose own threshold was still null (no population yet
    that week) when averaging `ceiling_rate`, rather than counting it as a miss — while
    `games_observed` still counts that game
12. `_walk_forward_player`'s `median`/`floor`/`ceiling` are the 50th/10th/90th percentile of the
    player's own strictly prior points
13. `_walk_forward_player` resets at a new season: a player's first game of a new season has
    `games_observed = 0` and every rate/quantile null, even after a full prior season of history
14. `_walk_forward_player` walks over a gap in a player's own games (a bye/inactive week already
    absent from the input) positionally, not by calendar-week arithmetic
"""

import numpy as np
import pandas as pd
import pytest

from src.gold.weekly_outcome_rates import _position_thresholds, _score_games, _walk_forward_player

# Matches test_defense_vs_position.py's fixture — the scoring coefficients league_points reads.
_HALF_PPR = pd.Series({
    "pass_yd_pts": 0.04, "pass_td_pts": 4.0, "pass_2pt_pts": 2.0, "pass_int_pts": -1.0,
    "rush_yd_pts": 0.1, "rush_td_pts": 6.0, "rush_2pt_pts": 2.0,
    "rec_pts": 0.5, "rec_yd_pts": 0.1, "rec_td_pts": 6.0, "rec_2pt_pts": 2.0,
    "fum_lost_pts": -2.0,
})


def _stat_row(**overrides) -> dict:
    """One weekly_stats-shaped player-game row, every counting stat zeroed unless overridden."""
    row = {
        "passing_yards": 0, "passing_tds": 0, "passing_2pt_conversions": 0, "passing_interceptions": 0,
        "rushing_yards": 0, "rushing_tds": 0, "rushing_2pt_conversions": 0,
        "receptions": 0, "receiving_yards": 0, "receiving_tds": 0, "receiving_2pt_conversions": 0,
        "sack_fumbles_lost": 0, "rushing_fumbles_lost": 0, "receiving_fumbles_lost": 0,
    }
    row.update(overrides)
    return row


def _stats(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player_id": r.pop("player_id"), "player_name": r.pop("player_name"),
         "position": r.pop("position"), "team": r.pop("team"),
         "season": r.pop("season"), "week": r.pop("week"), **_stat_row(**r)}
        for r in rows
    ])


# 1. One player's raw stat line scores correctly under a league's own coefficients, and identity
#    columns survive the trip.
def test_score_games_scores_one_player_under_the_leagues_own_rules():
    stats = _stats([
        {"player_id": "P1", "player_name": "A", "position": "WR", "team": "DAL",
         "season": 2024, "week": 1, "receptions": 6, "receiving_yards": 80, "receiving_tds": 1},
    ])

    out = _score_games(stats, _HALF_PPR)

    expected = 6 * 0.5 + 80 * 0.1 + 1 * 6.0
    row = out.iloc[0]
    assert row["points"] == pytest.approx(expected)
    assert row["player_id"] == "P1"
    assert row["season"] == 2024
    assert row["week"] == 1
    assert row["position"] == "WR"
    assert row["team"] == "DAL"


def _scored(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["season", "position", "week", "points"])


# 2. A position's first week of a season has no prior games, so both thresholds are null.
def test_position_thresholds_null_on_the_first_week():
    scored = _scored([(2024, "WR", 1, 10.0)])

    out = _position_thresholds(scored, ceiling_pct=1.0, floor_pct=0.0)

    row = out.iloc[0]
    assert pd.isna(row["position_ceiling_threshold"])
    assert pd.isna(row["position_floor_threshold"])


# 3. Week 3's threshold is drawn only from weeks 1-2's pool, never week 3's own games — verified
#    with ceiling_pct=1.0/floor_pct=0.0 so the expected value is exactly the pool's max/min.
def test_position_thresholds_excludes_the_current_week():
    scored = _scored([
        (2024, "WR", 1, 10.0),
        (2024, "WR", 2, 20.0),
        (2024, "WR", 3, 999.0),  # must not leak into week 3's own threshold
    ])

    out = _position_thresholds(scored, ceiling_pct=1.0, floor_pct=0.0).set_index("week")

    assert out.loc[3, "position_ceiling_threshold"] == pytest.approx(20.0)
    assert out.loc[3, "position_floor_threshold"] == pytest.approx(10.0)


# 4. The pool spans every player at the position that week, not one team's alone.
def test_position_thresholds_pools_every_player_at_the_position():
    scored = _scored([
        (2024, "WR", 1, 10.0),   # team A, week 1
        (2024, "WR", 1, 30.0),   # team B, week 1
        (2024, "WR", 2, 0.0),
    ])

    out = _position_thresholds(scored, ceiling_pct=1.0, floor_pct=0.0).set_index("week")

    assert out.loc[2, "position_ceiling_threshold"] == pytest.approx(30.0)
    assert out.loc[2, "position_floor_threshold"] == pytest.approx(10.0)


# 5. WR and RB pools in the same season/week never mix.
def test_position_thresholds_keeps_positions_separate():
    scored = _scored([
        (2024, "WR", 1, 10.0),
        (2024, "RB", 1, 100.0),
        (2024, "WR", 2, 0.0),
        (2024, "RB", 2, 0.0),
    ])

    out = _position_thresholds(scored, ceiling_pct=1.0, floor_pct=0.0)

    wr_week2 = out[(out["position"] == "WR") & (out["week"] == 2)].iloc[0]
    rb_week2 = out[(out["position"] == "RB") & (out["week"] == 2)].iloc[0]
    assert wr_week2["position_ceiling_threshold"] == pytest.approx(10.0)
    assert rb_week2["position_ceiling_threshold"] == pytest.approx(100.0)


# 6. A new season resets the pool: 2025 week 1 has no threshold despite a full 2024 of data.
def test_position_thresholds_resets_at_a_new_season():
    scored = _scored([
        (2024, "WR", 1, 10.0),
        (2024, "WR", 2, 20.0),
        (2025, "WR", 1, 5.0),
    ])

    out = _position_thresholds(scored, ceiling_pct=1.0, floor_pct=0.0)

    row = out[(out["season"] == 2025) & (out["week"] == 1)].iloc[0]
    assert pd.isna(row["position_ceiling_threshold"])
    assert pd.isna(row["position_floor_threshold"])


def _walked_input(rows: list[dict]) -> pd.DataFrame:
    """A frame already shaped like `_walk_forward_player`'s input — per-game points with that
    game's own as-of-week thresholds already attached."""
    defaults = {"position_ceiling_threshold": np.nan, "position_floor_threshold": np.nan}
    return pd.DataFrame([{**defaults, **row} for row in rows])


# 7. First game of a season: no prior game exists, so games_observed is 0.
def test_walk_forward_player_games_observed_is_zero_on_the_first_game():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 10.0},
    ])

    out = _walk_forward_player(scored)

    assert out.iloc[0]["games_observed"] == 0


# 8. That first-game row ships with every rate/quantile null rather than being dropped.
def test_walk_forward_player_ships_the_first_game_row_with_nulls():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 10.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},
    ])

    out = _walk_forward_player(scored)

    assert len(out) == 1
    row = out.iloc[0]
    for column in ("ceiling_rate", "floor_rate", "median", "floor", "ceiling"):
        assert pd.isna(row[column])


# 9. 2nd game: ceiling_rate reflects exactly whether the 1st game cleared its own threshold.
def test_walk_forward_player_ceiling_rate_on_second_game_reflects_the_first_game():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 20.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # cleared
        {"player_id": "P1", "season": 2024, "week": 2, "points": 0.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},
    ])

    out = _walk_forward_player(scored).set_index("week")

    assert out.loc[2, "ceiling_rate"] == pytest.approx(1.0)
    assert out.loc[2, "floor_rate"] == pytest.approx(0.0)


# 10. A later game's rate is the mean of the per-game indicator across every strictly prior game.
def test_walk_forward_player_rate_averages_every_prior_game():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 20.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # cleared
        {"player_id": "P1", "season": 2024, "week": 2, "points": 10.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # missed
        {"player_id": "P1", "season": 2024, "week": 3, "points": 16.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # cleared
        {"player_id": "P1", "season": 2024, "week": 4, "points": 12.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # missed
        {"player_id": "P1", "season": 2024, "week": 5, "points": 0.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},
    ])

    out = _walk_forward_player(scored).set_index("week")

    assert out.loc[5, "ceiling_rate"] == pytest.approx(0.5)  # 2 of 4 prior games cleared


# 11. A prior game whose own threshold was null is skipped by the rate average, not counted as a
#     miss, while games_observed still counts it.
def test_walk_forward_player_skips_a_prior_game_with_no_threshold_yet():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 999.0,
         "position_ceiling_threshold": np.nan, "position_floor_threshold": np.nan},  # no pool yet
        {"player_id": "P1", "season": 2024, "week": 2, "points": 20.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},  # cleared
        {"player_id": "P1", "season": 2024, "week": 3, "points": 0.0,
         "position_ceiling_threshold": 15.0, "position_floor_threshold": 5.0},
    ])

    out = _walk_forward_player(scored).set_index("week")

    assert out.loc[3, "games_observed"] == 2
    assert out.loc[3, "ceiling_rate"] == pytest.approx(1.0)  # week 1's null indicator excluded


# 12. median/floor/ceiling are the 50th/10th/90th percentile of the player's own strictly prior
#     points.
def test_walk_forward_player_quantile_band_matches_prior_points():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 10.0},
        {"player_id": "P1", "season": 2024, "week": 2, "points": 20.0},
        {"player_id": "P1", "season": 2024, "week": 3, "points": 30.0},
        {"player_id": "P1", "season": 2024, "week": 4, "points": 40.0},
        {"player_id": "P1", "season": 2024, "week": 5, "points": 0.0},
    ])

    out = _walk_forward_player(scored).set_index("week")

    prior = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert out.loc[5, "median"] == pytest.approx(prior.quantile(0.5))
    assert out.loc[5, "floor"] == pytest.approx(prior.quantile(0.1))
    assert out.loc[5, "ceiling"] == pytest.approx(prior.quantile(0.9))


# 13. A new season resets the walk-forward: no history carried over even after a full prior season.
def test_walk_forward_player_resets_at_a_new_season():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 10.0},
        {"player_id": "P1", "season": 2024, "week": 2, "points": 20.0},
        {"player_id": "P1", "season": 2025, "week": 1, "points": 50.0},
    ])

    out = _walk_forward_player(scored).set_index(["season", "week"])

    assert out.loc[(2025, 1), "games_observed"] == 0
    for column in ("ceiling_rate", "floor_rate", "median", "floor", "ceiling"):
        assert pd.isna(out.loc[(2025, 1), column])


# 14. A gap in the input (a bye/inactive week already absent) is walked over positionally.
def test_walk_forward_player_walks_over_a_missing_week():
    scored = _walked_input([
        {"player_id": "P1", "season": 2024, "week": 1, "points": 10.0},
        {"player_id": "P1", "season": 2024, "week": 2, "points": 20.0},
        # week 3 absent — a bye or inactive week.
        {"player_id": "P1", "season": 2024, "week": 4, "points": 30.0},
        {"player_id": "P1", "season": 2024, "week": 5, "points": 0.0},
    ])

    out = _walk_forward_player(scored).set_index("week")

    assert out.loc[5, "games_observed"] == 3
    prior = pd.Series([10.0, 20.0, 30.0])
    assert out.loc[5, "median"] == pytest.approx(prior.quantile(0.5))
