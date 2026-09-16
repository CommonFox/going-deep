"""Which season fantasypros.py stamps onto each archived weekly-rankings snapshot (#117).

Weekly rankings are now archived rather than overwritten (`load_weekly_rankings` reads every
snapshot ever captured for a position, not just the newest), so each snapshot needs a `season`
column alongside `week` to key on — the raw payload itself carries neither, so both are read from
`sleeper_nfl_state`, exactly like `_current_week` already does for `week`.
"""

import duckdb
import pytest

from src.silver.fantasypros import _current_season


def _con_with_nfl_state(season: int) -> duckdb.DuckDBPyConnection:
    """An in-memory stand-in for the warehouse, with sleeper_nfl_state already loaded."""
    con = duckdb.connect()
    con.execute("CREATE TABLE sleeper_nfl_state (season BIGINT)")
    con.execute("INSERT INTO sleeper_nfl_state VALUES (?)", [season])
    return con


# 1. The normal case: sleeper.py has already loaded sleeper_nfl_state this build.
def test_reads_the_season_sleeper_nfl_state_reports():
    con = _con_with_nfl_state(2026)
    try:
        assert _current_season(con) == 2026
    finally:
        con.close()


# 2. A fresh clone (or a build that skipped src.silver.sleeper) hasn't loaded the table yet — this
#    must name the missing dependency rather than defaulting to some hand-typed season.
def test_missing_sleeper_nfl_state_raises_naming_the_dependency():
    con = duckdb.connect()
    try:
        with pytest.raises(RuntimeError, match="sleeper_nfl_state"):
            _current_season(con)
    finally:
        con.close()
