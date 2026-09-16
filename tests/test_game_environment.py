"""`game_environment` (#118): implied totals, gamescript lean, and kickoff conditions derived from
`schedules` alone.

The one real risk here is `spread_line`'s sign convention: nflverse states it home-relative but not
which sign favours the home team, and getting it backwards produces numbers that look entirely
reasonable and are exactly wrong. Checked against `home_moneyline` (2020-2025,
`corr(spread_line, home_moneyline) = -0.95`) and by hand against three 2024 games, one with the away
team favoured — see the module docstring. Positive `spread_line` means the home team is favoured.

Every fixture is a hand-built in-memory `schedules` stand-in, per `conftest.py` — no warehouse file.
"""

import duckdb
import pandas as pd

from src.gold.game_environment import _build_sql


def _con_with_schedules(*rows: dict) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE schedules (
            game_id VARCHAR, season BIGINT, week BIGINT, gameday VARCHAR, gametime VARCHAR,
            home_team VARCHAR, away_team VARCHAR, spread_line DOUBLE, total_line DOUBLE,
            roof VARCHAR, temp DOUBLE, wind DOUBLE, div_game BIGINT, home_rest BIGINT,
            away_rest BIGINT
        )
    """)
    defaults = dict(
        game_id="2024_01_AAA_BBB", season=2024, week=1, gameday="2024-09-08", gametime="13:00",
        home_team="AAA", away_team="BBB", spread_line=None, total_line=None,
        roof="outdoors", temp=None, wind=None, div_game=0, home_rest=7, away_rest=7,
    )
    for row in rows:
        merged = {**defaults, **row}
        con.execute(
            "INSERT INTO schedules VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(merged.values()),
        )
    con.execute(_build_sql())
    return con


def _rows(con, **where) -> list[dict]:
    clause = " AND ".join(f"{k} = '{v}'" if isinstance(v, str) else f"{k} = {v}"
                           for k, v in where.items())
    sql = "SELECT * FROM game_environment"
    if clause:
        sql += f" WHERE {clause}"
    return con.sql(sql + " ORDER BY team").df().to_dict("records")


def _team(con, team: str) -> dict:
    (row,) = _rows(con, team=team)
    return row


# 1. Home team favoured (positive spread_line): its implied_margin is the raw line, and it is
#    flagged the favorite.
def test_home_favorite_gets_positive_implied_margin():
    con = _con_with_schedules({"home_team": "BAL", "away_team": "CLE", "spread_line": 19.5,
                                "total_line": 42.5})
    home = _team(con, "BAL")
    away = _team(con, "CLE")
    assert home["implied_margin"] == 19.5
    assert home["is_favorite"] is True
    assert away["implied_margin"] == -19.5
    assert away["is_favorite"] is False


# 2. Away team favoured (negative spread_line, matching the hand-verified NYG/BAL 2024 game): the
#    sign flips onto the away row, not the home row.
def test_away_favorite_gets_positive_implied_margin():
    con = _con_with_schedules({"home_team": "NYG", "away_team": "BAL", "spread_line": -16.5,
                                "total_line": 43.5})
    home = _team(con, "NYG")
    away = _team(con, "BAL")
    assert home["implied_margin"] == -16.5
    assert home["is_favorite"] is False
    assert away["implied_margin"] == 16.5
    assert away["is_favorite"] is True


# 3. Implied team totals split the game total in proportion to the margin, and the two sides sum
#    back to total_line exactly.
def test_implied_team_totals_split_the_total_line_and_sum_back_to_it():
    con = _con_with_schedules({"home_team": "BAL", "away_team": "CLE", "spread_line": 19.5,
                                "total_line": 42.5})
    home = _team(con, "BAL")
    away = _team(con, "CLE")
    assert home["implied_team_total"] == 31.0
    assert away["implied_team_total"] == 11.5
    assert home["implied_team_total"] + away["implied_team_total"] == 42.5


# 4. A pick'em (spread_line exactly 0) favours neither side.
def test_pick_em_spread_is_not_a_favorite_for_either_side():
    con = _con_with_schedules({"spread_line": 0.0, "total_line": 44.0})
    home = _team(con, "AAA")
    away = _team(con, "BBB")
    assert home["is_favorite"] is False
    assert away["is_favorite"] is False
    assert home["implied_margin"] == 0.0


# 5. A season/week with no published line yet (checked live: 2026 week 4 onward) leaves every
#    derived column null, distinguishable from a priced pick'em.
def test_no_published_line_leaves_derived_columns_null_not_zero():
    con = _con_with_schedules({"spread_line": None, "total_line": None})
    home = _team(con, "AAA")
    assert pd.isna(home["implied_margin"])
    assert pd.isna(home["implied_team_total"])
    assert pd.isna(home["is_favorite"])
    assert pd.isna(home["gamescript_lean"])


# 6. gamescript_lean names where the implied margin sits, symmetric around zero.
def test_gamescript_lean_buckets_the_implied_margin():
    con = _con_with_schedules(
        {"game_id": "g1", "home_team": "A1", "away_team": "B1", "spread_line": 14.0,
         "total_line": 44.0},
        {"game_id": "g2", "home_team": "A2", "away_team": "B2", "spread_line": 5.0,
         "total_line": 44.0},
        {"game_id": "g3", "home_team": "A3", "away_team": "B3", "spread_line": 0.0,
         "total_line": 44.0},
        {"game_id": "g4", "home_team": "A4", "away_team": "B4", "spread_line": -5.0,
         "total_line": 44.0},
        {"game_id": "g5", "home_team": "A5", "away_team": "B5", "spread_line": -14.0,
         "total_line": 44.0},
    )
    leans = {r["team"]: r["gamescript_lean"] for r in _rows(con)}
    assert leans["A1"] == "big_favorite"
    assert leans["A2"] == "favorite"
    assert leans["A3"] == "pick_em"
    assert leans["A4"] == "underdog"
    assert leans["A5"] == "big_underdog"


# 7. A dome game's null temp/wind pass through as null — a dome has no wind reading, not a zero
#    one — matching how punt_environment.py and the roadmap already treat this.
def test_dome_game_keeps_null_temp_and_wind():
    con = _con_with_schedules({"roof": "dome", "temp": None, "wind": None})
    home = _team(con, "AAA")
    assert home["roof"] == "dome"
    assert pd.isna(home["temp"])
    assert pd.isna(home["wind"])


# 8. One scheduled game produces exactly two rows, one per team, with is_home, opponent and
#    rest_days correctly assigned to each side.
def test_one_row_per_team_with_correct_home_away_and_rest_days():
    con = _con_with_schedules({"home_team": "BAL", "away_team": "CLE", "home_rest": 10,
                                "away_rest": 6})
    rows = _rows(con)
    assert len(rows) == 2
    (home,) = [r for r in rows if r["team"] == "BAL"]
    (away,) = [r for r in rows if r["team"] == "CLE"]
    assert home["is_home"] is True and home["opponent"] == "CLE" and home["rest_days"] == 10
    assert away["is_home"] is False and away["opponent"] == "BAL" and away["rest_days"] == 6


# 9. kickoff combines the date and time columns into one timestamp.
def test_kickoff_combines_date_and_time():
    con = _con_with_schedules({"gameday": "2024-09-05", "gametime": "20:20"})
    home = _team(con, "AAA")
    assert str(home["kickoff"]) == "2024-09-05 20:20:00"
