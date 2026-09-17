"""Defense vs position — issue #120, under the weekly player context epic (#108).

Nothing in the warehouse aggregates `weekly_stats.opponent_team` into "how has this defense
actually performed against this position, as of week N". `defense_vs_position` is that table: one
row per (league_key, season, week, defense_team, position), scored under each league's own
coefficients, with a season-to-date figure and separate recency-window figures kept apart, computed
strictly from games before the row's own week.

Full test list (see the PR body for the same list with rationale):
 1. `_points_allowed_by_game` sums one position's league points against the correct defense for one
    week, from that league's own scoring
 2. `_points_allowed_by_game` sums multiple players at the same position against the same defense in
    the same week into one row, not one row per player
 3. `_points_allowed_by_game` keeps two positions facing the same defense in the same week as
    separate rows rather than combining them
 4. `_walk_forward`'s season-to-date figure for a defense/position's second game is exactly its
    first game's total — one game of evidence, not yet an average of anything else
 5. `_walk_forward`'s season-to-date figure is the mean of every strictly prior game, never
    including the game the row itself describes
 6. `_walk_forward`'s `games_observed` is 0 for a defense/position's first game of a season — no
    prior game exists to report a fact from yet
 7. `_walk_forward` resets at a new season: a defense/position's first game of a new season carries
    no history over from the previous season, even after a full season of it
 8. `_walk_forward`'s recency windows (`last3`, `last5`) average only the games actually inside the
    window, sliding forward as more games accrue and never reaching past the window's size
 9. `_relative_to_league` computes each defense's ratio and z-score against the mean of every other
    defense's season-to-date figure for the same (league_key, season, week, position)
10. `_relative_to_league` ranks a defense that has allowed more points to a position, among the same
    (league_key, season, week, position) group, ahead of one that has allowed fewer — rank 1 is the
    softest defense against that position, the most exploitable matchup, not the stingiest
11. Two leagues scored from the same raw weekly stats produce different WR points-allowed figures
    when their `rec_pts` differ, using the shared `league_points` machinery rather than a second
    scoring path — the acceptance criterion that the two current leagues must actually disagree
"""

import numpy as np
import pandas as pd
import pytest

from src.gold.defense_vs_position import _points_allowed_by_game, _relative_to_league, _walk_forward

# A league_settings row carries far more columns than league_points reads; tests only need the
# scoring coefficients league_points actually multiplies against, matching league_scoring.py's
# STAT_COEFFICIENTS keys plus the shared fumble-lost rate.
_HALF_PPR = pd.Series({
    "pass_yd_pts": 0.04, "pass_td_pts": 4.0, "pass_2pt_pts": 2.0, "pass_int_pts": -1.0,
    "rush_yd_pts": 0.1, "rush_td_pts": 6.0, "rush_2pt_pts": 2.0,
    "rec_pts": 0.5, "rec_yd_pts": 0.1, "rec_td_pts": 6.0, "rec_2pt_pts": 2.0,
    "fum_lost_pts": -2.0,
})
_FULL_PPR = _HALF_PPR.copy()
_FULL_PPR["rec_pts"] = 1.0


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
        {"season": r.pop("season"), "week": r.pop("week"), "opponent_team": r.pop("opponent_team"),
         "position": r.pop("position"), **_stat_row(**r)}
        for r in rows
    ])


# 1. One WR facing DAL in one week scores DAL exactly what half-PPR says his own line is worth.
def test_points_allowed_by_game_scores_one_player_under_the_leagues_own_rules():
    stats = _stats([
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "WR",
         "receptions": 6, "receiving_yards": 80, "receiving_tds": 1},
    ])

    out = _points_allowed_by_game(stats, _HALF_PPR)

    expected = 6 * 0.5 + 80 * 0.1 + 1 * 6.0
    row = out[(out["defense_team"] == "DAL") & (out["position"] == "WR")].iloc[0]
    assert row["points_allowed"] == pytest.approx(expected)


# 2. Two RBs who both faced DAL in week 1 combine into DAL's one RB row for that week, not two.
def test_points_allowed_by_game_sums_multiple_players_at_a_position():
    stats = _stats([
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "RB",
         "rushing_yards": 100, "rushing_tds": 1},
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "RB",
         "rushing_yards": 20, "rushing_tds": 0},
    ])

    out = _points_allowed_by_game(stats, _HALF_PPR)

    rb_rows = out[(out["defense_team"] == "DAL") & (out["position"] == "RB")]
    assert len(rb_rows) == 1
    expected = (100 * 0.1 + 1 * 6.0) + (20 * 0.1)
    assert rb_rows.iloc[0]["points_allowed"] == pytest.approx(expected)


# 3. A QB and a WR facing DAL in the same week stay on separate rows.
def test_points_allowed_by_game_keeps_positions_separate():
    stats = _stats([
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "QB",
         "passing_yards": 300, "passing_tds": 2},
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "WR",
         "receptions": 4, "receiving_yards": 50},
    ])

    out = _points_allowed_by_game(stats, _HALF_PPR)

    assert set(out[out["defense_team"] == "DAL"]["position"]) == {"QB", "WR"}
    assert len(out[out["defense_team"] == "DAL"]) == 2


def _by_game(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["season", "week", "defense_team", "position", "points_allowed"]
    )


# 4. Second game: season-to-date equals the first game's total exactly, since it's the only prior
#    game there is.
def test_walk_forward_season_to_date_on_second_game_is_the_first_games_total():
    by_game = _by_game([
        (2024, 1, "DAL", "WR", 20.0),
        (2024, 2, "DAL", "WR", 0.0),
    ])

    out = _walk_forward(by_game).set_index("week")

    assert out.loc[2, "points_allowed_per_game_season_to_date"] == 20.0


# 5. Third game: season-to-date is the mean of the two strictly prior games, not all three.
def test_walk_forward_season_to_date_excludes_the_current_game():
    by_game = _by_game([
        (2024, 1, "DAL", "WR", 10.0),
        (2024, 2, "DAL", "WR", 20.0),
        (2024, 3, "DAL", "WR", 999.0),
    ])

    out = _walk_forward(by_game).set_index("week")

    assert out.loc[3, "points_allowed_per_game_season_to_date"] == 15.0


# 6. First game of the season: no prior game exists, so games_observed is 0.
def test_walk_forward_games_observed_is_zero_on_the_first_game():
    by_game = _by_game([(2024, 1, "DAL", "WR", 10.0)])

    out = _walk_forward(by_game)

    assert out.iloc[0]["games_observed"] == 0


# 7. A new season resets the walk-forward: DAL's first WR game of 2025 has no season-to-date figure
#    even though 2024 gave it a full season of history.
def test_walk_forward_resets_at_a_new_season():
    by_game = _by_game([
        (2024, 1, "DAL", "WR", 10.0),
        (2024, 2, "DAL", "WR", 10.0),
        (2025, 1, "DAL", "WR", 5.0),
        (2025, 2, "DAL", "WR", 5.0),
    ])

    out = _walk_forward(by_game).set_index(["season", "week"])

    assert out.loc[(2025, 1), "games_observed"] == 0
    assert pd.isna(out.loc[(2025, 1), "points_allowed_per_game_season_to_date"])
    assert out.loc[(2025, 2), "points_allowed_per_game_season_to_date"] == 5.0


# 8. last3 uses only the three prior games and slides forward as the season goes on, mirroring
#    weekly_backtest's last3_ppg behaviour for a player baseline.
def test_walk_forward_last3_window_slides():
    by_game = _by_game([
        (2024, 1, "DAL", "WR", 4.0),
        (2024, 2, "DAL", "WR", 8.0),
        (2024, 3, "DAL", "WR", 12.0),
        (2024, 4, "DAL", "WR", 100.0),
        (2024, 5, "DAL", "WR", 0.0),
    ])

    out = _walk_forward(by_game).set_index("week")

    assert out.loc[4, "points_allowed_per_game_last3"] == pytest.approx((4.0 + 8.0 + 12.0) / 3)
    assert out.loc[5, "points_allowed_per_game_last3"] == pytest.approx((8.0 + 12.0 + 100.0) / 3)


def _walked(rows: list[tuple]) -> pd.DataFrame:
    """A frame already shaped like `_walk_forward`'s output, for `_relative_to_league` tests —
    one row per (league_key, season, week, defense_team, position) with its season-to-date figure
    already attached."""
    return pd.DataFrame(
        rows,
        columns=["league_key", "season", "week", "defense_team", "position",
                 "points_allowed_per_game_season_to_date"],
    )


# 9. Ratio and z-score are computed against the mean of the other defenses in the same
#    (league_key, season, week, position) group.
def test_relative_to_league_ratio_and_zscore_use_the_groups_mean():
    walked = _walked([
        ("sleeper", 2024, 3, "DAL", "WR", 30.0),
        ("sleeper", 2024, 3, "NYG", "WR", 10.0),
        ("sleeper", 2024, 3, "PHI", "WR", 20.0),
    ])

    out = _relative_to_league(walked).set_index("defense_team")

    league_avg = (30.0 + 10.0 + 20.0) / 3
    assert out.loc["DAL", "league_avg_points_allowed"] == pytest.approx(league_avg)
    assert out.loc["DAL", "vs_league_avg_ratio"] == pytest.approx(30.0 / league_avg)
    league_std = pd.Series([30.0, 10.0, 20.0]).std()
    assert out.loc["DAL", "vs_league_avg_zscore"] == pytest.approx((30.0 - league_avg) / league_std)


# 10. Rank 1 is the defense that has allowed the *most* points to the position — the softest matchup
#     — not the stingiest.
def test_relative_to_league_rank_favors_the_defense_that_allows_the_most():
    walked = _walked([
        ("sleeper", 2024, 3, "DAL", "WR", 30.0),
        ("sleeper", 2024, 3, "NYG", "WR", 10.0),
        ("sleeper", 2024, 3, "PHI", "WR", 20.0),
    ])

    out = _relative_to_league(walked).set_index("defense_team")

    assert out.loc["DAL", "rank"] == 1
    assert out.loc["PHI", "rank"] == 2
    assert out.loc["NYG", "rank"] == 3


# 11. The same raw weekly stats, scored by two leagues whose rec_pts differ, produce different WR
#     points-allowed totals — the acceptance criterion that the two current leagues actually
#     disagree, using the one shared scoring path rather than two.
def test_two_leagues_with_different_rec_pts_score_wr_differently():
    stats = _stats([
        {"season": 2024, "week": 1, "opponent_team": "DAL", "position": "WR",
         "receptions": 8, "receiving_yards": 60},
    ])

    half_ppr = _points_allowed_by_game(stats, _HALF_PPR)
    full_ppr = _points_allowed_by_game(stats, _FULL_PPR)

    half_points = half_ppr[half_ppr["defense_team"] == "DAL"].iloc[0]["points_allowed"]
    full_points = full_ppr[full_ppr["defense_team"] == "DAL"].iloc[0]["points_allowed"]
    assert full_points - half_points == pytest.approx(8 * (1.0 - 0.5))
