"""Suite-wide setup: keep tests off the warehouse.

DuckDB puts a file lock on `data/warehouse.duckdb` — one writer or many readers, never both, held
until the connection closes. A test that opens one is therefore not just slow, it is a build
breaker: `scripts/build_warehouse.sh` running in the same window dies with `Could not set lock on
file`, an error that names the warehouse and says nothing about a test suite. `src/query.py`
documents the identical trap for notebook kernels, which is where that connect-per-call design
came from.

Rather than leaving that as a rule to remember, this makes it fail on contact. Tests here run on
small hand-written frames whose expected values can be reasoned about by hand, which is the only
kind of fixture that stays stable across a rebuild anyway.
"""

import duckdb
import pytest

# Captured before anything is patched, so the in-memory path below still has a real connect to
# call. The fixture is function-scoped and undoes itself, so this stays the genuine article.
_REAL_CONNECT = duckdb.connect

# What duckdb.connect() treats as "no file": its own default, and the explicit spellings of it.
_IN_MEMORY = {"", ":memory:"}


@pytest.fixture(autouse=True)
def no_warehouse_access(monkeypatch):
    """Make opening any database file raise, for every test, without opting in."""

    def refuse_to_open_a_file(database: str = ":memory:", *args, **kwargs):
        if str(database) in _IN_MEMORY:
            return _REAL_CONNECT(database, *args, **kwargs)
        raise RuntimeError(
            f"A test tried to open the database file {database!r}. Tests must not touch the "
            "warehouse: DuckDB locks the file, so a connection open during a build makes that "
            "build fail with 'Could not set lock on file' and no mention of the test suite. "
            "Build the frame the code under test needs by hand, or call duckdb.connect() with no "
            "arguments for a throwaway in-memory database."
        )

    monkeypatch.setattr(duckdb, "connect", refuse_to_open_a_file)
