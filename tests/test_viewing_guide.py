"""`viewing_guide`/`viewing_guide_starters` (#168): one row per (league_key, season, week, game_id)
for every game with at least one of my `optimal_lineup` starters in it, plus the per-starter detail
behind it.

Full test list (see the issue body for the same list with rationale):
 1. A starter who resolves through `weekly_player_context` and `game_environment` produces exactly
    one `viewing_guide_starters` row and one `viewing_guide` row for his game
 2. An `optimal_lineup` row with no `player_id` (an unfilled slot) produces no row in either table
 3. Two of my starters on opposite sides of the same physical game collapse into one `viewing_guide`
    row, with `home_team`/`away_team` resolved correctly and `starter_count = 2`
 4. Two starters in two different games produce two separate `viewing_guide` rows
 5. `total_projected_points` sums only non-null `projected_points`; a starter with a null
    `projected_points` (a backfilled slot) still counts toward `starter_count` without zeroing the
    sum
 6. A starter whose `player_id` has no matching `weekly_player_context` row is left out of both
    tables and reported by the missing-starter query
 7. A starter whose team has no matching `game_environment` row (a bye) is left out of both tables
    and reported by the missing-starter query
 8. `broadcast_window` buckets Thursday, Sunday early/late (16:00 boundary), Sunday night/Monday
    (18:00 boundary, matching `src/gameday/storylines.py`'s `NIGHT_GAMETIME_FLOOR`) correctly
 9. A non-standard weekday (a Saturday playoff slate) reports as that raw weekday string instead of
    being forced into one of the five standard buckets
10. A game with none of my starters in it produces no `viewing_guide` row

Every fixture is a hand-built in-memory table, per `conftest.py` — no warehouse file.
"""

import duckdb
import pandas as pd

from src.gold.viewing_guide import _guide_sql, _missing_sql, _starters_sql

_OL_DEFAULTS = dict(
    league_key="sleeper", season=2026, week=3, slot="WR1", player_id="P1",
    player_name="Player One", projected_points=10.0,
)
_WPC_DEFAULTS = dict(league_key="sleeper", player_id="P1", season=2026, week=3, team="BAL")
_GE_DEFAULTS = dict(
    season=2026, week=3, team="BAL", opponent="CLE", game_id="2026_03_BAL_CLE",
    kickoff=pd.Timestamp("2026-09-27 13:00:00"), is_home=True,
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
    optimal_lineup: list[dict] = (),
    weekly_player_context: list[dict] = (),
    game_environment: list[dict] = (),
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    _table(con, "optimal_lineup", list(_OL_DEFAULTS), list(optimal_lineup))
    _table(con, "weekly_player_context", list(_WPC_DEFAULTS), list(weekly_player_context))
    _table(con, "game_environment", list(_GE_DEFAULTS), list(game_environment))
    con.execute(_starters_sql())
    con.execute(_guide_sql())
    return con


def _starters(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return con.sql("SELECT * FROM viewing_guide_starters ORDER BY player_id").df().to_dict("records")


def _guide(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return con.sql("SELECT * FROM viewing_guide ORDER BY game_id").df().to_dict("records")


def _missing(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return con.execute(_missing_sql()).df().to_dict("records")


# 1. A resolving starter lands in both tables, once each.
def test_a_resolving_starter_produces_one_row_in_each_table():
    con = _con(
        optimal_lineup=_rows(_OL_DEFAULTS, {}),
        weekly_player_context=_rows(_WPC_DEFAULTS, {}),
        game_environment=_rows(_GE_DEFAULTS, {}),
    )

    (starter,) = _starters(con)
    (game,) = _guide(con)

    assert starter["player_id"] == "P1"
    assert starter["team"] == "BAL"
    assert starter["game_id"] == "2026_03_BAL_CLE"
    assert game["game_id"] == "2026_03_BAL_CLE"
    assert game["starter_count"] == 1


# 2. An unfilled slot (no player_id) names no one to watch for.
def test_an_unfilled_slot_produces_no_rows():
    con = _con(
        optimal_lineup=_rows(_OL_DEFAULTS, {"player_id": None, "player_name": None}),
        weekly_player_context=_rows(_WPC_DEFAULTS, {}),
        game_environment=_rows(_GE_DEFAULTS, {}),
    )

    assert _starters(con) == []
    assert _guide(con) == []


# 3. Two starters on opposite sides of the same physical game collapse into one viewing_guide row.
def test_starters_on_both_sides_of_a_game_collapse_to_one_row():
    con = _con(
        optimal_lineup=_rows(
            _OL_DEFAULTS,
            {"player_id": "P1", "slot": "WR1"},
            {"player_id": "P2", "player_name": "Player Two", "slot": "TE1"},
        ),
        weekly_player_context=_rows(
            _WPC_DEFAULTS,
            {"player_id": "P1", "team": "BAL"},
            {"player_id": "P2", "team": "CLE"},
        ),
        game_environment=_rows(
            _GE_DEFAULTS,
            {"team": "BAL", "opponent": "CLE", "is_home": True},
            {"team": "CLE", "opponent": "BAL", "is_home": False},
        ),
    )

    out = _guide(con)

    assert len(out) == 1
    assert out[0]["home_team"] == "BAL"
    assert out[0]["away_team"] == "CLE"
    assert out[0]["starter_count"] == 2


# 4. Two starters in two different games produce two separate viewing_guide rows.
def test_starters_in_different_games_produce_two_rows():
    con = _con(
        optimal_lineup=_rows(
            _OL_DEFAULTS,
            {"player_id": "P1", "slot": "WR1"},
            {"player_id": "P2", "player_name": "Player Two", "slot": "WR2"},
        ),
        weekly_player_context=_rows(
            _WPC_DEFAULTS,
            {"player_id": "P1", "team": "BAL"},
            {"player_id": "P2", "team": "SF"},
        ),
        game_environment=_rows(
            _GE_DEFAULTS,
            {"team": "BAL", "opponent": "CLE", "game_id": "2026_03_BAL_CLE"},
            {"team": "SF", "opponent": "NYG", "game_id": "2026_03_NYG_SF", "is_home": False},
        ),
    )

    out = _guide(con)

    assert len(out) == 2
    assert {row["game_id"] for row in out} == {"2026_03_BAL_CLE", "2026_03_NYG_SF"}


# 5. total_projected_points sums only non-null projections; a null one still counts toward
#    starter_count without zeroing the sum.
def test_a_null_projection_counts_toward_starter_count_not_the_sum():
    con = _con(
        optimal_lineup=_rows(
            _OL_DEFAULTS,
            {"player_id": "P1", "slot": "WR1", "projected_points": 10.0},
            {"player_id": "P2", "player_name": "Player Two", "slot": "P1", "projected_points": None},
        ),
        weekly_player_context=_rows(
            _WPC_DEFAULTS, {"player_id": "P1", "team": "BAL"}, {"player_id": "P2", "team": "BAL"},
        ),
        game_environment=_rows(_GE_DEFAULTS, {}),
    )

    (game,) = _guide(con)

    assert game["starter_count"] == 2
    assert game["total_projected_points"] == 10.0


# 6. A starter with no weekly_player_context row (never resolved to a team) is left out of both
#    tables and reported by the missing-starter query.
def test_a_starter_missing_from_weekly_player_context_is_left_out_and_reported():
    con = _con(
        optimal_lineup=_rows(_OL_DEFAULTS, {}),
        weekly_player_context=(),
        game_environment=_rows(_GE_DEFAULTS, {}),
    )

    assert _starters(con) == []
    assert _guide(con) == []
    missing = _missing(con)
    assert len(missing) == 1
    assert missing[0]["player_name"] == "Player One"


# 7. A starter whose team has no matching game_environment row (a bye) is left out and reported.
def test_a_starter_whose_team_has_no_game_is_left_out_and_reported():
    con = _con(
        optimal_lineup=_rows(_OL_DEFAULTS, {}),
        weekly_player_context=_rows(_WPC_DEFAULTS, {"team": "BAL"}),
        # BAL is on a bye this week — the week's other games exist, just none for BAL.
        game_environment=_rows(_GE_DEFAULTS, {"team": "SF", "opponent": "NYG", "is_home": True}),
    )

    assert _starters(con) == []
    assert _guide(con) == []
    missing = _missing(con)
    assert len(missing) == 1
    assert missing[0]["player_name"] == "Player One"


# 8. broadcast_window buckets the five standard windows correctly.
def test_broadcast_window_buckets_the_five_standard_windows():
    con = _con(
        optimal_lineup=_rows(
            _OL_DEFAULTS,
            {"player_id": "P1", "slot": "WR1"},
            {"player_id": "P2", "player_name": "P Two", "slot": "WR2"},
            {"player_id": "P3", "player_name": "P Three", "slot": "WR3"},
            {"player_id": "P4", "player_name": "P Four", "slot": "TE1"},
            {"player_id": "P5", "player_name": "P Five", "slot": "RB1"},
        ),
        weekly_player_context=_rows(
            _WPC_DEFAULTS,
            {"player_id": "P1", "team": "BAL"},
            {"player_id": "P2", "team": "SF"},
            {"player_id": "P3", "team": "GB"},
            {"player_id": "P4", "team": "KC"},
            {"player_id": "P5", "team": "NYJ"},
        ),
        game_environment=_rows(
            _GE_DEFAULTS,
            {"team": "BAL", "game_id": "thu", "kickoff": pd.Timestamp("2026-09-24 20:15:00")},
            {"team": "SF", "game_id": "early", "kickoff": pd.Timestamp("2026-09-27 13:00:00")},
            {"team": "GB", "game_id": "late", "kickoff": pd.Timestamp("2026-09-27 16:05:00")},
            {"team": "KC", "game_id": "night", "kickoff": pd.Timestamp("2026-09-27 20:20:00")},
            {"team": "NYJ", "game_id": "mon", "kickoff": pd.Timestamp("2026-09-28 20:15:00")},
        ),
    )

    out = {row["game_id"]: row["broadcast_window"] for row in _guide(con)}

    assert out["thu"] == "thursday"
    assert out["early"] == "sunday_early"
    assert out["late"] == "sunday_late"
    assert out["night"] == "sunday_night"
    assert out["mon"] == "monday"


# 9. A non-standard weekday reports as itself rather than being forced into one of the five.
def test_a_non_standard_weekday_reports_as_its_own_name():
    con = _con(
        optimal_lineup=_rows(_OL_DEFAULTS, {}),
        weekly_player_context=_rows(_WPC_DEFAULTS, {}),
        game_environment=_rows(_GE_DEFAULTS, {"kickoff": pd.Timestamp("2026-09-26 15:00:00")}),
    )

    (game,) = _guide(con)

    assert game["weekday"] == "Saturday"
    assert game["broadcast_window"] == "Saturday"


# 10. A game with none of my starters in it produces no viewing_guide row.
def test_a_game_with_no_starters_produces_no_row():
    con = _con(
        optimal_lineup=(),
        weekly_player_context=(),
        game_environment=_rows(_GE_DEFAULTS, {}),
    )

    assert _guide(con) == []
