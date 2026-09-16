"""Tolerating pre-#117 injuries raw files during the migration to snapshot archiving.

Before #117, `fetch_injuries` overwrote `injuries_<season-range>.parquet` on every build (e.g.
`injuries_2015_2025.parquet`), and that season range grows every year. `load_injuries` now globs a
constant `injuries` stem instead (so the archive doesn't fragment at each year boundary), which
means those old, differently-shaped filenames get matched by the same glob but carry no
`captured_at` — they have to be skipped rather than crashing the load.
"""

from pathlib import Path

from src.silver.nfl_data import _captured_snapshots


# 1. A properly stamped snapshot is kept, paired with its captured_at.
def test_keeps_a_wellformed_snapshot():
    path = Path("injuries_20260901T120000000000.parquet")
    assert _captured_snapshots([path]) == [(path, "20260901T120000000000")]


# 2. A pre-#117 file (no captured_at suffix at all) is skipped rather than raising.
def test_skips_a_pre_migration_file():
    legacy = Path("injuries_2015_2025.parquet")
    assert _captured_snapshots([legacy]) == []


# 3. A mix of both: only the well-formed ones survive, in their original relative order.
def test_keeps_wellformed_and_drops_legacy_from_a_mixed_list():
    legacy = Path("injuries_2015_2025.parquet")
    stamped_a = Path("injuries_20260901T120000000000.parquet")
    stamped_b = Path("injuries_20260908T120000000000.parquet")

    result = _captured_snapshots([legacy, stamped_a, stamped_b])

    assert result == [
        (stamped_a, "20260901T120000000000"),
        (stamped_b, "20260908T120000000000"),
    ]
