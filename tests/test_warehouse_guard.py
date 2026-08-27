"""The guard that keeps the suite off the warehouse.

DuckDB takes a file lock on `data/warehouse.duckdb`: one writer or many readers, never both. A
test that opens a connection therefore holds a lock for as long as it runs, and a build started in
that window dies with `Could not set lock on file` — an error naming the warehouse and never
mentioning the test suite. `src/query.py` documents the same trap for notebooks.

So tests run on hand-built frames, and `tests/conftest.py` makes reaching for a database file fail
loudly instead of quietly taking that lock. These cases are what that guard has to do.
"""

import duckdb
import pytest

from src.query import WAREHOUSE_PATH


def test_opening_a_database_file_raises():
    with pytest.raises(RuntimeError):
        duckdb.connect("data/some-other.duckdb")


def test_opening_the_warehouse_raises_and_says_why():
    with pytest.raises(RuntimeError) as caught:
        duckdb.connect(str(WAREHOUSE_PATH))
    assert "warehouse" in str(caught.value).lower()


def test_read_only_is_no_exemption():
    # A read-only connection still takes a lock, so it still breaks a concurrent build.
    with pytest.raises(RuntimeError):
        duckdb.connect(str(WAREHOUSE_PATH), read_only=True)


def test_an_in_memory_database_is_still_allowed():
    # Nothing on disk, nothing locked — this stays available for building fixtures.
    con = duckdb.connect()
    try:
        assert con.execute("SELECT 1").fetchone() == (1,)
    finally:
        con.close()
