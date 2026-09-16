"""Which seasons `src/gold/seasons.py` considers complete (#116).

`src/silver/nfl_data.py` now fetches record-of-play feeds (weekly_stats, snap_counts, ...) through
the season in progress, not just finished ones, so a season appearing in one of those tables no
longer implies it's over. `completed_seasons`/`max_completed_season` are the shared predicate every
gold model should use instead of trusting fetch scope — derived from `schedules`, where a season is
complete once every regular-season game has a `result`.
"""

import duckdb

from src.gold.seasons import completed_seasons, max_completed_season


def _con_with_schedules(rows: list[tuple[int, str, object]]) -> duckdb.DuckDBPyConnection:
    """An in-memory stand-in for the warehouse, with `schedules` loaded from (season, game_type,
    result) rows. `result` is `None` for a game not yet played.
    """
    con = duckdb.connect()
    con.execute("CREATE TABLE schedules (season BIGINT, game_type VARCHAR, result DOUBLE)")
    con.executemany("INSERT INTO schedules VALUES (?, ?, ?)", rows)
    return con


# 1. Every regular-season game has a result: the season counts as complete.
def test_season_with_every_reg_game_played_is_complete():
    con = _con_with_schedules([(2024, "REG", 3.0), (2024, "REG", -7.0)])
    try:
        assert completed_seasons(con) == [2024]
    finally:
        con.close()


# 2. One regular-season game still has no result: the season is in progress, not complete.
def test_season_with_an_unplayed_reg_game_is_not_complete():
    con = _con_with_schedules([(2026, "REG", 3.0), (2026, "REG", None)])
    try:
        assert completed_seasons(con) == []
    finally:
        con.close()


# 3. A mix of finished and in-progress seasons: only the finished ones come back, ascending.
def test_mixed_seasons_returns_only_the_complete_ones_in_order():
    con = _con_with_schedules([
        (2025, "REG", 10.0),
        (2024, "REG", 3.0),
        (2026, "REG", None),
    ])
    try:
        assert completed_seasons(con) == [2024, 2025]
    finally:
        con.close()


# 4. Regular season done, postseason not yet played: still complete. A player-season's stats are
#    finished at the end of REG; a pending Super Bowl result isn't a reason to call weekly_stats
#    unfinished for that season.
def test_completed_reg_season_counts_even_with_unplayed_postseason():
    con = _con_with_schedules([
        (2025, "REG", 3.0),
        (2025, "WC", None),
        (2025, "DIV", None),
        (2025, "CON", None),
        (2025, "SB", None),
    ])
    try:
        assert completed_seasons(con) == [2025]
    finally:
        con.close()


# 5. max_completed_season is the highest complete season, ignoring an in-progress one after it.
def test_max_completed_season_ignores_the_in_progress_season():
    con = _con_with_schedules([
        (2024, "REG", 3.0),
        (2025, "REG", 10.0),
        (2026, "REG", None),
    ])
    try:
        assert max_completed_season(con) == 2025
    finally:
        con.close()


# 6. No season has finished yet (an empty or brand-new schedules table): max is None rather than
#    raising, so a caller can still run — there's simply no history to bound against.
def test_max_completed_season_is_none_when_nothing_is_complete():
    con = _con_with_schedules([(2026, "REG", None)])
    try:
        assert max_completed_season(con) is None
    finally:
        con.close()
