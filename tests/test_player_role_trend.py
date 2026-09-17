"""player_role_trend — issue #121, under the weekly player context epic (#108).

Nothing in the warehouse tracks a player's role *direction* week to week — only its season-level
snapshot, inside `player_weighted_baselines`. `player_role_trend` is that table: one row per
(player_id, season, week) for QB/RB/WR/TE, carrying each role component's value that week (the
level) alongside a delta describing whether it's rising or falling (the direction), computed
strictly from games before the row's own week.

Full test list (see the PR body for the same list with rationale):
 1. `_weekly_role` pulls `target_share`/`air_yards_share`/`wopr` directly from `weekly_stats` for a
    player-week
 2. `_weekly_role` computes `carries_share` as a player's carries divided by the sum of every
    skill-position player's carries on the same team in the same week
 3. `_weekly_role` pulls `snap_share` from the pre-joined snap-counts frame for the matching
    (player_id, season, week)
 4. `_weekly_role` pulls `depth_rank`/`is_starter` from `player_depth_chart` for the matching
    (player_id, season, week), left-joined so a missing depth-chart row nulls those two columns
    rather than dropping the row
 4b. `_weekly_role` joins the depth chart on `position` as well, so a player listed at two positions
    the same week (a wildcat QB/TE) doesn't fan out the one row `weekly_stats` has for him
 4c. `_weekly_role` joins the depth chart on `team` as well, so a player traded mid-week, and
    therefore listed on both teams' depth charts that week, doesn't fan out either
 5. `_walk_forward`'s `games_observed` is 0 on a player's first game of a season
 6. `_walk_forward` ships a row for a player's first game of a season with every `_delta` column
    null, rather than dropping the row
 7. `_walk_forward`'s delta on a player's 4th game compares the mean of games 1-3 (last3) against
    the mean of games 1-3 (season-to-date) — the same three games, so the delta is exactly 0
 8. `_walk_forward`'s delta on a player's 5th game compares the last-3 mean (games 2-4) against the
    season-to-date mean (games 1-4), and the two differ when the metric changed over those games
 9. `_walk_forward`'s delta is null while fewer than 3 prior games exist, even though
    `games_observed` (1 or 2) and the season-to-date figure are already computable
10. `_walk_forward` resets at a new season: a player's first game of a new season has
    `games_observed = 0` and a null delta, even after a full season of history the year before
11. `_walk_forward` walks over a gap in a player's games (a bye or inactive week, already absent
    from the input): with games at weeks 1, 2, 4, 5, the week-5 row's `games_observed` counts the 3
    actual prior games, not week-number arithmetic
12. `_walk_forward`'s `depth_rank_delta` uses the same sign convention as every other metric's (last3
    minus season-to-date) rather than inverted — so a player whose recent rank number is lower (a
    better slot) than his season average gets a *negative* `depth_rank_delta`, the opposite reading
    from every other metric's positive-means-improving convention
13. `_walk_forward` treats `is_starter` as a 0/1 rate for its delta — a player who started all of his
    last 3 games but only half of his season average gets a positive `is_starter_delta`
14. `_walk_forward`'s `rolling(3)` window requires every one of its three prior games to carry a
    non-null value for a metric — a single missing week (e.g. one game with no depth-chart match)
    nulls that metric's delta for every one of the three games where the gap still sits inside the
    window, even though `games_observed` and the metric's own season-to-date figure stay unaffected
"""

import numpy as np
import pandas as pd
import pytest

from src.gold.player_role_trend import _ROLE_METRICS, _walk_forward, _weekly_role


def _stats(rows: list[dict]) -> pd.DataFrame:
    """weekly_stats-shaped rows, restricted to the columns `_weekly_role` reads."""
    defaults = {"target_share": 0.0, "air_yards_share": 0.0, "wopr": 0.0, "carries": 0}
    return pd.DataFrame([{**defaults, **row} for row in rows])


def _snaps(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "season", "week", "snap_share"])


def _depth(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["gsis_id", "season", "week", "team", "position", "depth_rank", "is_starter"],
    )


# 1. target_share/air_yards_share/wopr come straight from weekly_stats.
def test_weekly_role_pulls_projection_style_shares_directly():
    stats = _stats([
        {"player_id": "P1", "player_name": "A", "position": "WR", "team": "DAL",
         "season": 2024, "week": 1, "target_share": 0.28, "air_yards_share": 0.35, "wopr": 0.6},
    ])

    out = _weekly_role(stats, _snaps([]), _depth([]))

    row = out.iloc[0]
    assert row["target_share"] == 0.28
    assert row["air_yards_share"] == 0.35
    assert row["wopr"] == 0.6


# 2. carries_share divides one player's carries by the whole team's carries that week.
def test_weekly_role_computes_carries_share_against_the_team_total():
    stats = _stats([
        {"player_id": "P1", "player_name": "A", "position": "RB", "team": "DAL",
         "season": 2024, "week": 1, "carries": 15},
        {"player_id": "P2", "player_name": "B", "position": "RB", "team": "DAL",
         "season": 2024, "week": 1, "carries": 5},
        {"player_id": "P3", "player_name": "C", "position": "WR", "team": "NYG",
         "season": 2024, "week": 1, "carries": 10},
    ])

    out = _weekly_role(stats, _snaps([]), _depth([])).set_index("player_id")

    assert out.loc["P1", "carries_share"] == pytest.approx(15 / 20)
    assert out.loc["P2", "carries_share"] == pytest.approx(5 / 20)
    # NYG's own total is just P3's 10 carries — DAL's carries must not leak into the denominator.
    assert out.loc["P3", "carries_share"] == pytest.approx(1.0)


# 3. snap_share comes from the pre-joined snap-counts frame, matched on (player_id, season, week).
def test_weekly_role_pulls_snap_share_from_the_joined_frame():
    stats = _stats([
        {"player_id": "P1", "player_name": "A", "position": "RB", "team": "DAL",
         "season": 2024, "week": 1},
    ])
    snaps = _snaps([("P1", 2024, 1, 0.72)])

    out = _weekly_role(stats, snaps, _depth([]))

    assert out.iloc[0]["snap_share"] == pytest.approx(0.72)


# 4. depth_rank/is_starter left-join from player_depth_chart; a miss nulls those two columns only.
def test_weekly_role_left_joins_depth_chart_and_nulls_on_a_miss():
    stats = _stats([
        {"player_id": "P1", "player_name": "A", "position": "WR", "team": "DAL",
         "season": 2024, "week": 1, "target_share": 0.2},
        {"player_id": "P2", "player_name": "B", "position": "WR", "team": "DAL",
         "season": 2024, "week": 1, "target_share": 0.1},
    ])
    depth = _depth([("P1", 2024, 1, "DAL", "WR", 1, True)])  # P2 has no depth-chart row.

    out = _weekly_role(stats, _snaps([]), depth).set_index("player_id")

    assert out.loc["P1", "depth_rank"] == 1
    assert out.loc["P1", "is_starter"] == True  # noqa: E712
    assert pd.isna(out.loc["P2", "depth_rank"])
    assert pd.isna(out.loc["P2", "is_starter"])
    # The miss doesn't drop the row or touch its other columns.
    assert out.loc["P2", "target_share"] == pytest.approx(0.1)


# 4b. A player listed at two positions the same week (e.g. a wildcat QB/TE) only matches the
#     depth-chart row for the position weekly_stats has him at that week, not both.
def test_weekly_role_depth_chart_join_does_not_fan_out_a_dual_position_player():
    stats = _stats([
        {"player_id": "P1", "player_name": "Wildcat", "position": "TE", "team": "NO",
         "season": 2025, "week": 15, "target_share": 0.1},
    ])
    depth = _depth([
        ("P1", 2025, 15, "NO", "QB", 3, False),
        ("P1", 2025, 15, "NO", "TE", 2, False),
    ])

    out = _weekly_role(stats, _snaps([]), depth)

    assert len(out) == 1
    assert out.iloc[0]["depth_rank"] == 2


# 4c. A player traded mid-week (and therefore listed on both teams' depth charts) only matches the
#     depth-chart row for the team weekly_stats says actually fielded him, not both.
def test_weekly_role_depth_chart_join_does_not_fan_out_a_midseason_trade():
    stats = _stats([
        {"player_id": "P1", "player_name": "Traded", "position": "WR", "team": "PIT",
         "season": 2024, "week": 10, "target_share": 0.1},
    ])
    depth = _depth([
        ("P1", 2024, 10, "NYJ", "WR", 2, False),  # old team, same week of the trade
        ("P1", 2024, 10, "PIT", "WR", 2, False),  # new team, the one that actually fielded him
    ])

    out = _weekly_role(stats, _snaps([]), depth)

    assert len(out) == 1


def _role_row(**overrides) -> dict:
    """One player_role_trend-shaped row, every metric defaulted to 0.0 unless overridden — mirrors
    `test_defense_vs_position.py`'s `_stat_row` helper."""
    row = {metric: 0.0 for metric in _ROLE_METRICS}
    row.update(overrides)
    return row


def _role(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player_id": r.pop("player_id"), "season": r.pop("season"), "week": r.pop("week"),
         **_role_row(**r)}
        for r in rows
    ])


# 5. First game of a season: no prior game exists, so games_observed is 0.
def test_walk_forward_games_observed_is_zero_on_the_first_game():
    role = _role([{"player_id": "P1", "season": 2024, "week": 1}])

    out = _walk_forward(role)

    assert out.iloc[0]["games_observed"] == 0


# 6. That first-game row ships with every delta null rather than being dropped.
def test_walk_forward_ships_the_first_game_row_with_null_deltas():
    role = _role([{"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.3}])

    out = _walk_forward(role)

    assert len(out) == 1
    for metric in _ROLE_METRICS:
        assert pd.isna(out.iloc[0][f"{metric}_delta"])


# 7. 4th game: last3 (games 1-3) and season-to-date (games 1-3) are the same three games, so the
#    delta is exactly 0.
def test_walk_forward_delta_is_zero_when_last3_and_season_to_date_cover_the_same_games():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.10},
        {"player_id": "P1", "season": 2024, "week": 2, "target_share": 0.20},
        {"player_id": "P1", "season": 2024, "week": 3, "target_share": 0.30},
        {"player_id": "P1", "season": 2024, "week": 4, "target_share": 0.99},
    ])

    out = _walk_forward(role).set_index("week")

    assert out.loc[4, "target_share_delta"] == pytest.approx(0.0)


# 8. 5th game: last3 (games 2-4) differs from season-to-date (games 1-4) when the role changed.
def test_walk_forward_delta_reflects_a_real_change_between_the_two_windows():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.10},
        {"player_id": "P1", "season": 2024, "week": 2, "target_share": 0.20},
        {"player_id": "P1", "season": 2024, "week": 3, "target_share": 0.30},
        {"player_id": "P1", "season": 2024, "week": 4, "target_share": 0.40},
        {"player_id": "P1", "season": 2024, "week": 5, "target_share": 0.99},
    ])

    out = _walk_forward(role).set_index("week")

    last3 = (0.20 + 0.30 + 0.40) / 3
    season_to_date = (0.10 + 0.20 + 0.30 + 0.40) / 4
    assert out.loc[5, "target_share_delta"] == pytest.approx(last3 - season_to_date)


# 9. Delta is null with 1 or 2 prior games, even though games_observed/season-to-date already exist.
def test_walk_forward_delta_is_null_before_three_prior_games_exist():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.10},
        {"player_id": "P1", "season": 2024, "week": 2, "target_share": 0.20},
        {"player_id": "P1", "season": 2024, "week": 3, "target_share": 0.30},
    ])

    out = _walk_forward(role).set_index("week")

    assert out.loc[2, "games_observed"] == 1
    assert pd.isna(out.loc[2, "target_share_delta"])
    assert out.loc[3, "games_observed"] == 2
    assert pd.isna(out.loc[3, "target_share_delta"])


# 10. A new season resets the walk-forward: no history carried over even after a full prior season.
def test_walk_forward_resets_at_a_new_season():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.10},
        {"player_id": "P1", "season": 2024, "week": 2, "target_share": 0.20},
        {"player_id": "P1", "season": 2024, "week": 3, "target_share": 0.30},
        {"player_id": "P1", "season": 2025, "week": 1, "target_share": 0.50},
    ])

    out = _walk_forward(role).set_index(["season", "week"])

    assert out.loc[(2025, 1), "games_observed"] == 0
    assert pd.isna(out.loc[(2025, 1), "target_share_delta"])


# 11. A gap in the input (a bye/inactive week already absent) is walked over positionally, not by
#     week-number arithmetic.
def test_walk_forward_walks_over_a_missing_week():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "target_share": 0.10},
        {"player_id": "P1", "season": 2024, "week": 2, "target_share": 0.20},
        # week 3 absent — a bye or inactive week.
        {"player_id": "P1", "season": 2024, "week": 4, "target_share": 0.30},
        {"player_id": "P1", "season": 2024, "week": 5, "target_share": 0.40},
    ])

    out = _walk_forward(role).set_index("week")

    assert out.loc[5, "games_observed"] == 3
    last3 = (0.10 + 0.20 + 0.30) / 3
    season_to_date = (0.10 + 0.20 + 0.30) / 3
    assert out.loc[5, "target_share_delta"] == pytest.approx(last3 - season_to_date)


# 12. depth_rank_delta uses the same (last3 - season_to_date) arithmetic as every other metric, so a
#     player whose recent rank number is *lower* (better) than his season average gets a *negative*
#     delta — the opposite reading from every other metric's positive-means-improving convention.
def test_walk_forward_depth_rank_delta_is_uninverted_and_therefore_reads_backwards():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "depth_rank": 3.0},
        {"player_id": "P1", "season": 2024, "week": 2, "depth_rank": 3.0},
        {"player_id": "P1", "season": 2024, "week": 3, "depth_rank": 3.0},
        {"player_id": "P1", "season": 2024, "week": 4, "depth_rank": 1.0},
        {"player_id": "P1", "season": 2024, "week": 5, "depth_rank": 1.0},
    ])

    out = _walk_forward(role).set_index("week")

    # Games 2-4 (last3) average a better (lower) rank than games 1-4 (season-to-date) — an
    # improving role — and the delta comes out negative rather than positive.
    last3 = (3.0 + 3.0 + 1.0) / 3
    season_to_date = (3.0 + 3.0 + 3.0 + 1.0) / 4
    assert last3 < season_to_date
    assert out.loc[5, "depth_rank_delta"] == pytest.approx(last3 - season_to_date)
    assert out.loc[5, "depth_rank_delta"] < 0


# 13. is_starter is treated as a 0/1 rate for its delta.
def test_walk_forward_is_starter_delta_reads_as_a_rate():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "is_starter": False},
        {"player_id": "P1", "season": 2024, "week": 2, "is_starter": False},
        {"player_id": "P1", "season": 2024, "week": 3, "is_starter": True},
        {"player_id": "P1", "season": 2024, "week": 4, "is_starter": True},
        {"player_id": "P1", "season": 2024, "week": 5, "is_starter": True},
    ])

    out = _walk_forward(role).set_index("week")

    last3 = (0 + 1 + 1) / 3  # games 2-4
    season_to_date = (0 + 0 + 1 + 1) / 4  # games 1-4
    assert out.loc[5, "is_starter_delta"] == pytest.approx(last3 - season_to_date)
    assert out.loc[5, "is_starter_delta"] > 0


# 14. A single missing value inside the rolling window nulls that metric's delta for every game the
#     gap still sits inside, even though games_observed and the season-to-date figure are unaffected.
def test_walk_forward_a_missing_value_poisons_the_rolling_window_it_sits_inside():
    role = _role([
        {"player_id": "P1", "season": 2024, "week": 1, "depth_rank": 2.0},
        {"player_id": "P1", "season": 2024, "week": 2, "depth_rank": np.nan},  # no depth-chart row
        {"player_id": "P1", "season": 2024, "week": 3, "depth_rank": 2.0},
        {"player_id": "P1", "season": 2024, "week": 4, "depth_rank": 2.0},
        {"player_id": "P1", "season": 2024, "week": 5, "depth_rank": 2.0},
    ])

    out = _walk_forward(role).set_index("week")

    # Week 4's last-3 window (games 1-3) still contains the week-2 gap.
    assert pd.isna(out.loc[4, "depth_rank_delta"])
    # Week 5's last-3 window (games 2-4) also still contains it.
    assert pd.isna(out.loc[5, "depth_rank_delta"])
    # games_observed and the raw level are untouched by the gap.
    assert out.loc[5, "games_observed"] == 4
