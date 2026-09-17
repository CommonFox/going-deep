"""`weekly_player_context` (#124): one row per (league_key, season, week, player), joining the four
weekly tables this epic (#108) built plus `game_environment`, flat and unblended.

Full test list (see the PR body for the same list with rationale):
 1. A `weekly_projections` row whose `scoring` matches a league's own basis produces exactly one
    `weekly_player_context` row for that league
 2. Two leagues with different scoring bases split a player's two `weekly_projections` rows
    (one `ppr`, one `half_ppr`) one-to-one, so the total row count is 2, not 4
 3. `game_environment`'s `implied_team_total`, `gamescript_lean`, `roof` and `opponent` join onto
    the row via the player's own team, resolved through `draft_board`
 4. `defense_vs_position` joins on the opponent read off `game_environment` crossed with the
    player's own position — a QB and a WR facing the same defense the same week get that
    defense's QB-specific and WR-specific figures, never each other's
 5. `player_role_trend` joins on (player_id, season, week) only, with no `league_key` on either
    side, so both leagues' rows for the same player-week carry identical role figures
 6. `weekly_outcome_rates` joins on (league_key, player_id, season, week), so the two leagues can
    carry different figures for the same player-week
 7. A player with no Sleeper weekly projection (`sleeper_points` null) gets a null
    `weekly_position_rank` and `weekly_position_tier`, rather than displacing the ranks of players
    who do have one
 8. `weekly_position_rank` ranks players within (league_key, season, week, position) by
    `sleeper_points` descending, and a tie shares the lower rank number
 9. `weekly_position_tier` buckets `weekly_position_rank` into groups of the league's own
    `team_count` — a 12-team league's 13th-ranked WR lands in tier 2, the 12th in tier 1
10. A player missing from one of the joined tables (no `defense_vs_position` row yet) still ships
    with every other table's columns intact, rather than being dropped by the join
11. The table's total row count matches `weekly_projections`' own row count exactly — the
    correctness check that would catch a fan-out on the team or position join
12. A DST row (out of scope for `defense_vs_position`/`player_role_trend`/`weekly_outcome_rates`,
    all three scoped to skill positions) still gets its own `game_environment` context from its
    own team's game
"""

import duckdb
import pandas as pd
import pytest

from src.gold.weekly_player_context import _build_sql

_WP_DEFAULTS = dict(
    player_id="P1", player_name="Player One", position="WR", season=2026, week=3,
    scoring="half_ppr", sleeper_points=10.0, espn_points=None,
    fantasypros_rank_ecr=None, fantasypros_pos_rank=None, num_sources=1,
    points_gap=None, points_gap_pct=None,
)
_DB_DEFAULTS = dict(league_key="sleeper", player_id="P1", team="BAL", scoring="half_ppr")
_LS_DEFAULTS = dict(league_key="sleeper", team_count=12)
_GE_DEFAULTS = dict(
    season=2026, week=3, team="BAL", opponent="CLE", kickoff=None,
    implied_team_total=None, gamescript_lean=None, roof="outdoors", temp=None, wind=None,
)
_DVP_DEFAULTS = dict(
    league_key="sleeper", season=2026, week=3, defense_team="CLE", position="WR",
    points_allowed_per_game_season_to_date=None, points_allowed_per_game_last3=None,
    points_allowed_per_game_last5=None, games_observed=None,
    league_avg_points_allowed=None, vs_league_avg_ratio=None, vs_league_avg_zscore=None, rank=None,
)
_RT_DEFAULTS = dict(
    player_id="P1", season=2026, week=3, games_observed=None,
    snap_share=None, snap_share_delta=None, target_share=None, target_share_delta=None,
    air_yards_share=None, air_yards_share_delta=None, wopr=None, wopr_delta=None,
    carries_share=None, carries_share_delta=None, depth_rank=None, depth_rank_delta=None,
    is_starter=None, is_starter_delta=None,
)
_OR_DEFAULTS = dict(
    league_key="sleeper", player_id="P1", season=2026, week=3, games_observed=None,
    median=None, floor=None, ceiling=None, ceiling_rate=None, floor_rate=None,
    position_ceiling_threshold=None, position_floor_threshold=None,
)


def _rows(defaults: dict, *overrides: dict) -> list[dict]:
    return [{**defaults, **override} for override in overrides]


def _table(con: duckdb.DuckDBPyConnection, name: str, columns: list[str], rows: list[dict]) -> None:
    data = {column: [row.get(column) for row in rows] for column in columns}
    df = pd.DataFrame(data, columns=columns)
    con.register("_src", df)
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
    con.unregister("_src")


def _con(
    weekly_projections: list[dict] = (),
    draft_board: list[dict] = (),
    league_settings: list[dict] = (),
    game_environment: list[dict] = (),
    defense_vs_position: list[dict] = (),
    player_role_trend: list[dict] = (),
    weekly_outcome_rates: list[dict] = (),
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    _table(con, "weekly_projections", list(_WP_DEFAULTS), list(weekly_projections))
    _table(con, "draft_board", list(_DB_DEFAULTS), list(draft_board))
    _table(con, "league_settings", list(_LS_DEFAULTS), list(league_settings))
    _table(con, "game_environment", list(_GE_DEFAULTS), list(game_environment))
    _table(con, "defense_vs_position", list(_DVP_DEFAULTS), list(defense_vs_position))
    _table(con, "player_role_trend", list(_RT_DEFAULTS), list(player_role_trend))
    _table(con, "weekly_outcome_rates", list(_OR_DEFAULTS), list(weekly_outcome_rates))
    con.execute(_build_sql())
    return con


def _out(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return con.sql("SELECT * FROM weekly_player_context ORDER BY league_key, player_id").df().to_dict(
        "records"
    )


# 1. A weekly_projections row matches the one league whose scoring basis agrees with it.
def test_a_wp_row_produces_exactly_one_row_for_the_matching_league():
    con = _con(
        weekly_projections=_rows(_WP_DEFAULTS, {}),
        draft_board=_rows(_DB_DEFAULTS, {}, {"league_key": "espn", "scoring": "ppr"}),
        league_settings=_rows(_LS_DEFAULTS, {}, {"league_key": "espn", "team_count": 14}),
    )

    out = _out(con)

    assert len(out) == 1
    assert out[0]["league_key"] == "sleeper"


# 2. Two leagues, two scoring bases: the player's two weekly_projections rows split one-to-one.
def test_two_scoring_bases_split_one_to_one_across_leagues():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS, {"scoring": "half_ppr", "sleeper_points": 10.0},
            {"scoring": "ppr", "sleeper_points": 12.0},
        ),
        draft_board=_rows(
            _DB_DEFAULTS, {"scoring": "half_ppr"}, {"league_key": "espn", "scoring": "ppr"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}, {"league_key": "espn", "team_count": 14}),
    )

    out = _out(con)

    assert len(out) == 2
    by_league = {row["league_key"]: row for row in out}
    assert by_league["sleeper"]["sleeper_points"] == 10.0
    assert by_league["espn"]["sleeper_points"] == 12.0


# 3. game_environment's context columns join on the player's own team.
def test_game_environment_joins_on_the_players_own_team():
    con = _con(
        weekly_projections=_rows(_WP_DEFAULTS, {}),
        draft_board=_rows(_DB_DEFAULTS, {}),
        league_settings=_rows(_LS_DEFAULTS, {}),
        game_environment=_rows(
            _GE_DEFAULTS,
            {"team": "BAL", "opponent": "CLE", "implied_team_total": 24.5,
             "gamescript_lean": "favorite", "roof": "dome"},
        ),
    )

    (row,) = _out(con)

    assert row["game_opponent"] == "CLE"
    assert row["game_implied_team_total"] == 24.5
    assert row["game_gamescript_lean"] == "favorite"
    assert row["game_roof"] == "dome"


# 4. defense_vs_position joins on the opponent (from game_environment) crossed with position, so a
#    QB and a WR facing the same defense get their own position's figure, not each other's.
def test_defense_vs_position_joins_on_opponent_and_the_players_own_position():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS,
            {"player_id": "Q1", "position": "QB"},
            {"player_id": "W1", "position": "WR"},
        ),
        draft_board=_rows(
            _DB_DEFAULTS, {"player_id": "Q1", "team": "BAL"}, {"player_id": "W1", "team": "BAL"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}),
        game_environment=_rows(_GE_DEFAULTS, {"team": "BAL", "opponent": "CLE"}),
        defense_vs_position=_rows(
            _DVP_DEFAULTS,
            {"position": "QB", "points_allowed_per_game_season_to_date": 20.0},
            {"position": "WR", "points_allowed_per_game_season_to_date": 15.0},
        ),
    )

    out = {row["player_id"]: row for row in _out(con)}

    assert out["Q1"]["dvp_points_allowed_per_game_season_to_date"] == 20.0
    assert out["W1"]["dvp_points_allowed_per_game_season_to_date"] == 15.0


# 5. player_role_trend joins on player-week alone: both leagues' rows for the same player-week
#    carry identical role figures.
def test_player_role_trend_is_shared_across_leagues():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS, {"scoring": "half_ppr"}, {"scoring": "ppr"},
        ),
        draft_board=_rows(
            _DB_DEFAULTS, {"scoring": "half_ppr"}, {"league_key": "espn", "scoring": "ppr"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}, {"league_key": "espn", "team_count": 14}),
        player_role_trend=_rows(_RT_DEFAULTS, {"snap_share": 0.75}),
    )

    out = _out(con)

    assert len(out) == 2
    assert all(row["role_snap_share"] == 0.75 for row in out)


# 6. weekly_outcome_rates joins per league, so the two leagues can disagree.
def test_weekly_outcome_rates_can_differ_by_league():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS, {"scoring": "half_ppr"}, {"scoring": "ppr"},
        ),
        draft_board=_rows(
            _DB_DEFAULTS, {"scoring": "half_ppr"}, {"league_key": "espn", "scoring": "ppr"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}, {"league_key": "espn", "team_count": 14}),
        weekly_outcome_rates=_rows(
            _OR_DEFAULTS, {"median": 12.0}, {"league_key": "espn", "median": 15.0},
        ),
    )

    out = {row["league_key"]: row for row in _out(con)}

    assert out["sleeper"]["outcome_median"] == 12.0
    assert out["espn"]["outcome_median"] == 15.0


# 7. A missing Sleeper projection gets a null rank/tier without disturbing the real ranks.
def test_a_null_sleeper_points_gets_a_null_rank_and_tier():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS,
            {"player_id": "W1", "sleeper_points": 20.0},
            {"player_id": "W2", "sleeper_points": None},
        ),
        draft_board=_rows(_DB_DEFAULTS, {"player_id": "W1"}, {"player_id": "W2"}),
        league_settings=_rows(_LS_DEFAULTS, {}),
    )

    out = {row["player_id"]: row for row in _out(con)}

    assert out["W1"]["weekly_position_rank"] == 1
    assert pd.isna(out["W2"]["weekly_position_rank"])
    assert pd.isna(out["W2"]["weekly_position_tier"])


# 8. Ranking is within (league_key, season, week, position), by sleeper_points descending, with
#    ties sharing the lower rank number.
def test_weekly_position_rank_orders_by_sleeper_points_with_ties():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS,
            {"player_id": "W1", "sleeper_points": 20.0},
            {"player_id": "W2", "sleeper_points": 15.0},
            {"player_id": "W3", "sleeper_points": 15.0},
        ),
        draft_board=_rows(
            _DB_DEFAULTS, {"player_id": "W1"}, {"player_id": "W2"}, {"player_id": "W3"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}),
    )

    out = {row["player_id"]: row for row in _out(con)}

    assert out["W1"]["weekly_position_rank"] == 1
    assert out["W2"]["weekly_position_rank"] == 2
    assert out["W3"]["weekly_position_rank"] == 2


# 9. Tier buckets the rank into groups of the league's own team_count.
def test_weekly_position_tier_buckets_by_team_count():
    players = [{"player_id": f"W{i}", "sleeper_points": float(20 - i)} for i in range(13)]
    con = _con(
        weekly_projections=_rows(_WP_DEFAULTS, *players),
        draft_board=_rows(_DB_DEFAULTS, *[{"player_id": p["player_id"]} for p in players]),
        league_settings=_rows(_LS_DEFAULTS, {"team_count": 12}),
    )

    out = {row["player_id"]: row for row in _out(con)}

    assert out["W11"]["weekly_position_rank"] == 12
    assert out["W11"]["weekly_position_tier"] == 1
    assert out["W12"]["weekly_position_rank"] == 13
    assert out["W12"]["weekly_position_tier"] == 2


# 10. A gap in one joined table (no defense_vs_position row) doesn't drop the player or poison the
#     other tables' columns on the same row.
def test_a_missing_join_ships_the_row_with_other_columns_intact():
    con = _con(
        weekly_projections=_rows(_WP_DEFAULTS, {}),
        draft_board=_rows(_DB_DEFAULTS, {}),
        league_settings=_rows(_LS_DEFAULTS, {}),
        player_role_trend=_rows(_RT_DEFAULTS, {"snap_share": 0.6}),
        # defense_vs_position left empty: no row anywhere matches.
    )

    (row,) = _out(con)

    assert row["sleeper_points"] == 10.0
    assert row["role_snap_share"] == 0.6
    assert pd.isna(row["dvp_points_allowed_per_game_season_to_date"])


# 11. Total row count matches weekly_projections' own row count exactly.
def test_row_count_reconciles_against_weekly_projections():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS,
            {"player_id": "W1", "scoring": "half_ppr"},
            {"player_id": "W1", "scoring": "ppr"},
            {"player_id": "W2", "scoring": "half_ppr"},
        ),
        draft_board=_rows(
            _DB_DEFAULTS,
            {"player_id": "W1", "scoring": "half_ppr"},
            {"player_id": "W1", "league_key": "espn", "scoring": "ppr"},
            {"player_id": "W2", "scoring": "half_ppr"},
        ),
        league_settings=_rows(_LS_DEFAULTS, {}, {"league_key": "espn", "team_count": 14}),
    )

    (wpc_count,) = con.execute("SELECT COUNT(*) FROM weekly_player_context").fetchone()
    (wp_count,) = con.execute("SELECT COUNT(*) FROM weekly_projections").fetchone()

    assert wpc_count == wp_count == 3


# 12. A DST row still gets its own team's game_environment context, with no coverage from the
#     three tables scoped to skill positions.
def test_a_dst_row_still_gets_game_environment_context():
    con = _con(
        weekly_projections=_rows(
            _WP_DEFAULTS, {"player_id": "BAL", "player_name": "Baltimore Ravens", "position": "DST"},
        ),
        draft_board=_rows(_DB_DEFAULTS, {"player_id": "BAL", "team": "BAL"}),
        league_settings=_rows(_LS_DEFAULTS, {}),
        game_environment=_rows(
            _GE_DEFAULTS, {"team": "BAL", "opponent": "CLE", "implied_team_total": 20.0},
        ),
    )

    (row,) = _out(con)

    assert row["game_implied_team_total"] == 20.0
    assert pd.isna(row["dvp_points_allowed_per_game_season_to_date"])
    assert pd.isna(row["role_snap_share"])
    assert pd.isna(row["outcome_median"])
