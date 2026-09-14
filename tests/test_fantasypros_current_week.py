"""Which week fantasypros.py's weekly rankings fetch reads.

Before the season, `current_week = 1` in `__main__` was fine as a hand-typed constant. Once the
season is live, the weekly rankings need the *real* current week every build, read from
`sleeper_nfl_state` — already loaded by `src.silver.sleeper` earlier in the build — rather than a
constant someone has to remember to bump. `sleeper_nfl_state` not existing yet (a fresh clone, or
`src.silver.sleeper` skipped) has to fail loudly rather than silently fetch week 1, since a wrong
week fetched and archived without anyone noticing is worse than a build stopping.
"""

import duckdb
import pytest

from src.silver.fantasypros import _current_week


def _con_with_nfl_state(week: int) -> duckdb.DuckDBPyConnection:
    """An in-memory stand-in for the warehouse, with sleeper_nfl_state already loaded."""
    con = duckdb.connect()
    con.execute("CREATE TABLE sleeper_nfl_state (week BIGINT)")
    con.execute("INSERT INTO sleeper_nfl_state VALUES (?)", [week])
    return con


# 1. The normal case: sleeper.py has already loaded sleeper_nfl_state this build.
def test_reads_the_week_sleeper_nfl_state_reports():
    con = _con_with_nfl_state(4)
    try:
        assert _current_week(con) == 4
    finally:
        con.close()


# 2. Week 1 is a real value here, not a falsy placeholder to special-case.
def test_week_one_is_read_like_any_other_week():
    con = _con_with_nfl_state(1)
    try:
        assert _current_week(con) == 1
    finally:
        con.close()


# 3. A fresh clone (or a build that skipped src.silver.sleeper) hasn't loaded the table yet —
#    this must name the missing dependency rather than defaulting to week 1.
def test_missing_sleeper_nfl_state_raises_naming_the_dependency():
    con = duckdb.connect()
    try:
        with pytest.raises(RuntimeError, match="sleeper_nfl_state"):
            _current_week(con)
    finally:
        con.close()
