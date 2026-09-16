"""Shared archiving mechanism used by fantasypros.py, espn.py, sleeper.py and nfl_data.py to stop
overwriting weekly feeds on every build (#117).

Two things are tested here in isolation, since both are pure enough to reason about by hand:

- `save_json_snapshot`/`save_parquet_snapshot`: archive-or-skip. A fetch identical to the most
  recently archived snapshot for the same key must not grow the archive; anything else must.
- `refresh_latest_snapshot`: given an accumulated `_snapshots` table (one row per (key,
  captured_at)), materialize "the newest snapshot per key" — the shape every existing consumer
  (`weekly_projections.py`, `waiver_rankings.py`, ...) expects, with no `captured_at` column at all.
"""

import duckdb
import pandas as pd
import pytest

from src.silver.snapshots import (
    captured_at_of,
    existing_snapshots,
    refresh_latest_snapshot,
    save_json_snapshot,
    save_parquet_snapshot,
)


# ---- save_json_snapshot ------------------------------------------------------------------------

# 1. Nothing archived yet: the first save always writes, regardless of content.
def test_first_save_writes_a_new_file(tmp_path):
    path, is_new = save_json_snapshot(tmp_path, "weekly_qb_2026_2", [{"player": "Josh Allen"}])

    assert is_new is True
    assert path.exists()
    assert path.parent == tmp_path
    assert existing_snapshots(tmp_path, "weekly_qb_2026_2", "json") == [path]


# 2. A second save with identical content is a no-op: no new file, the original path comes back.
def test_identical_save_does_not_grow_the_archive(tmp_path):
    payload = [{"player": "Josh Allen", "rank_ecr": 1}]
    first_path, _ = save_json_snapshot(tmp_path, "weekly_qb_2026_2", payload)

    second_path, is_new = save_json_snapshot(tmp_path, "weekly_qb_2026_2", list(payload))

    assert is_new is False
    assert second_path == first_path
    assert existing_snapshots(tmp_path, "weekly_qb_2026_2", "json") == [first_path]


# 3. A second save with different content archives a new, distinct file alongside the first.
def test_changed_save_archives_a_new_file(tmp_path):
    first_path, _ = save_json_snapshot(tmp_path, "weekly_qb_2026_2", [{"rank_ecr": 1}])
    second_path, is_new = save_json_snapshot(tmp_path, "weekly_qb_2026_2", [{"rank_ecr": 2}])

    assert is_new is True
    assert second_path != first_path
    assert set(existing_snapshots(tmp_path, "weekly_qb_2026_2", "json")) == {first_path, second_path}


# 4. Dedup only ever compares against the single most recent snapshot, not the whole history — so
#    a value reverting to something seen two snapshots ago still archives (it's "identical
#    consecutive snapshots" that collapse, per the issue, not identical-ever).
def test_dedup_compares_only_the_most_recent_snapshot(tmp_path, monkeypatch):
    from src.silver import snapshots

    stamps = iter(["20260901T000000000000", "20260902T000000000000", "20260903T000000000000"])
    monkeypatch.setattr(snapshots, "new_captured_at", lambda: next(stamps))

    path_a, _ = save_json_snapshot(tmp_path, "injuries", [{"status": "DNP"}])
    path_b, _ = save_json_snapshot(tmp_path, "injuries", [{"status": "Full"}])
    path_c, is_new = save_json_snapshot(tmp_path, "injuries", [{"status": "DNP"}])

    assert is_new is True
    assert len({path_a, path_b, path_c}) == 3


# 5. Snapshots for different keys (e.g. different positions) never collide with each other.
def test_different_keys_are_independent_archives(tmp_path):
    save_json_snapshot(tmp_path, "weekly_qb_2026_2", [{"rank_ecr": 1}])
    save_json_snapshot(tmp_path, "weekly_rb_2026_2", [{"rank_ecr": 1}])

    assert len(existing_snapshots(tmp_path, "weekly_qb_2026_2", "json")) == 1
    assert len(existing_snapshots(tmp_path, "weekly_rb_2026_2", "json")) == 1


# 6. A stem that is a prefix of another key's stem (e.g. week 2 vs week 20) must not glob-collide.
def test_stem_prefix_does_not_collide_with_a_longer_stem(tmp_path):
    save_json_snapshot(tmp_path, "weekly_qb_2026_2", [{"rank_ecr": 1}])
    save_json_snapshot(tmp_path, "weekly_qb_2026_20", [{"rank_ecr": 99}])

    assert len(existing_snapshots(tmp_path, "weekly_qb_2026_2", "json")) == 1
    assert len(existing_snapshots(tmp_path, "weekly_qb_2026_20", "json")) == 1


# ---- save_parquet_snapshot ----------------------------------------------------------------------

# 7. Same archive-or-skip contract as the JSON case, for the parquet-backed feed (injuries).
def test_parquet_snapshot_skips_identical_and_archives_changed(tmp_path):
    unchanged = pd.DataFrame({"season": [2025], "week": [1], "report_status": ["Out"]})
    changed = pd.DataFrame({"season": [2025], "week": [1], "report_status": ["Questionable"]})

    first_path, first_is_new = save_parquet_snapshot(tmp_path, "injuries", unchanged)
    same_path, same_is_new = save_parquet_snapshot(tmp_path, "injuries", unchanged.copy())
    new_path, new_is_new = save_parquet_snapshot(tmp_path, "injuries", changed)

    assert first_is_new is True
    assert same_is_new is False and same_path == first_path
    assert new_is_new is True and new_path != first_path


# ---- captured_at_of -----------------------------------------------------------------------------

# 8. The capture timestamp round-trips out of the filename it was stamped into.
def test_captured_at_of_reads_back_the_stamped_timestamp(tmp_path):
    path, _ = save_json_snapshot(tmp_path, "projections_2026", [{"week": 1}])
    assert captured_at_of(path) == path.name.removeprefix("projections_2026_").removesuffix(".json")


# 9. A path with no timestamp in its name is a caller bug, not a silently-wrong answer.
def test_captured_at_of_raises_on_an_unstamped_path(tmp_path):
    stray = tmp_path / "projections_2026.json"
    stray.write_text("[]")
    with pytest.raises(ValueError, match="captured_at"):
        captured_at_of(stray)


# ---- refresh_latest_snapshot ----------------------------------------------------------------------

def _snapshots_table(con, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    con.execute("CREATE TABLE foo_snapshots AS SELECT * FROM df")


# 10. Of two snapshots for the same key, only the newer one's rows survive into the materialized
#     table, and the captured_at column itself is gone (consumers shouldn't need to know it exists).
def test_refresh_latest_snapshot_keeps_only_the_newest_per_key():
    con = duckdb.connect()
    _snapshots_table(con, [
        {"season": 2026, "week": 2, "value": "old", "captured_at": "20260901T000000000000"},
        {"season": 2026, "week": 2, "value": "new", "captured_at": "20260903T000000000000"},
    ])

    refresh_latest_snapshot(con, "foo", "foo_snapshots", ["season", "week"])
    result = con.execute("SELECT * FROM foo").df()

    assert list(result["value"]) == ["new"]
    assert "captured_at" not in result.columns
    con.close()


# 11. Each key's own newest snapshot is picked independently of other keys' history.
def test_refresh_latest_snapshot_is_independent_per_key():
    con = duckdb.connect()
    _snapshots_table(con, [
        {"season": 2026, "week": 1, "value": "week1-old", "captured_at": "20260901T000000000000"},
        {"season": 2026, "week": 1, "value": "week1-new", "captured_at": "20260908T000000000000"},
        {"season": 2026, "week": 2, "value": "week2-only", "captured_at": "20260902T000000000000"},
    ])

    refresh_latest_snapshot(con, "foo", "foo_snapshots", ["season", "week"])
    result = con.execute("SELECT week, value FROM foo ORDER BY week").df()

    assert list(result["value"]) == ["week1-new", "week2-only"]
    con.close()


# 12. A tie for the newest captured_at within a key keeps every row from that snapshot (it's
#     "latest snapshot", not "latest row") rather than arbitrarily dropping half of it.
def test_refresh_latest_snapshot_keeps_every_row_of_a_tied_snapshot():
    con = duckdb.connect()
    _snapshots_table(con, [
        {"season": 2026, "week": 2, "player": "a", "captured_at": "20260901T000000000000"},
        {"season": 2026, "week": 2, "player": "b", "captured_at": "20260901T000000000000"},
    ])

    refresh_latest_snapshot(con, "foo", "foo_snapshots", ["season", "week"])
    result = con.execute("SELECT player FROM foo ORDER BY player").df()

    assert list(result["player"]) == ["a", "b"]
    con.close()


# 13. Rebuilding is idempotent: running it twice over the same snapshots table gives the same
#     result, the way every other load_* in this codebase is idempotent.
def test_refresh_latest_snapshot_is_idempotent():
    con = duckdb.connect()
    _snapshots_table(con, [
        {"season": 2026, "week": 2, "value": "only", "captured_at": "20260901T000000000000"},
    ])

    refresh_latest_snapshot(con, "foo", "foo_snapshots", ["season", "week"])
    refresh_latest_snapshot(con, "foo", "foo_snapshots", ["season", "week"])
    result = con.execute("SELECT * FROM foo").df()

    assert list(result["value"]) == ["only"]
    con.close()
